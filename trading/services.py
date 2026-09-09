from datetime import timezone as datetime_timezone
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Min, Sum
from django.utils import timezone

from account.money import (
    MONEY_QUANTUM as MONEY_CENT,
    dollars_to_cents,
    quantize_money,
)
from creature.models import Creature

from .models import (
    MarketIndex,
    Notification,
    Order,
    Portfolio,
    PriceHistory,
    Trade,
)


class PricingEngine:
    """
    Dynamic Pricing Engine.
    Formula:
    price = reference_price × (1 + Δ_market) × rarity_multiplier

    - battle outcomes persist an ELO-derived shock in reference_price
    - Δ_market: buy/sell volume ratio from recent trades
    - rarity_multiplier: legendary/mythical premium
    """

    MARKET_WEIGHT = Decimal('0.05')    # max ±5% from market pressure
    RARITY_LEGENDARY = Decimal('1.5')
    RARITY_MYTHICAL = Decimal('2.0')
    RARITY_NORMAL = Decimal('1.0')

    @staticmethod
    def get_rarity_multiplier(creature):
        if creature.is_mythical:
            return PricingEngine.RARITY_MYTHICAL
        if creature.is_legendary:
            return PricingEngine.RARITY_LEGENDARY
        return PricingEngine.RARITY_NORMAL

    @staticmethod
    def calculate_market_delta(creature):
        """
        Compute Δ_market from buy vs sell volume in the last 24 hours.
        More buys → positive pressure → price up.
        More sells → negative pressure → price down.
        """
        since = timezone.now() - timezone.timedelta(hours=24)
        recent_trades = Trade.objects.filter(
            creature=creature,
            executed_at__gte=since
        )

        buy_volume = recent_trades.filter(
            buyer__isnull=False
        ).aggregate(
            total=Sum('quantity')
        )['total'] or Decimal('0')

        sell_volume = recent_trades.filter(
            seller__isnull=False
        ).aggregate(
            total=Sum('quantity')
        )['total'] or Decimal('0')

        total_volume = buy_volume + sell_volume
        if total_volume == 0:
            return Decimal('0')

        # Ratio centered at 0: all buys → +MARKET_WEIGHT, all sells →
        # -MARKET_WEIGHT
        buy_ratio = buy_volume / total_volume
        delta = (buy_ratio - Decimal('0.5')) * 2 * PricingEngine.MARKET_WEIGHT
        return delta

    @classmethod
    def calculate_price(cls, creature):
        """
        Calculate the new dynamic price for a creature.
        """
        base_price = (
            creature.pricing_reference_price
            or creature.current_price
        )
        market_delta = cls.calculate_market_delta(creature)
        rarity = cls.get_rarity_multiplier(creature)

        multiplier = (1 + market_delta) * rarity
        new_price = base_price * multiplier

        # Floor at $0.01, cap at Decimal(12,2) max to avoid DB overflow
        MAX_PRICE = Decimal('9999999999.99')
        return quantize_money(
            max(min(new_price, MAX_PRICE), MONEY_CENT)
        )

    @classmethod
    def update_creature_price(cls, creature):
        """Recalculate and persist the new price for a creature."""
        with transaction.atomic():
            locked_creature = Creature.objects.select_for_update().get(
                pk=creature.pk
            )
            old_price = locked_creature.current_price
            locked_creature.previous_close = old_price
            new_price = cls.calculate_price(locked_creature)
            locked_creature.current_price = new_price
            locked_creature.save(
                update_fields=['current_price', 'previous_close']
            )
            VolatilityService.check_volatility(locked_creature)

        creature.previous_close = old_price
        creature.current_price = new_price
        creature.pricing_reference_price = (
            locked_creature.pricing_reference_price
        )
        creature.circuit_breaker_expires_at = (
            locked_creature.circuit_breaker_expires_at
        )
        return new_price

    @classmethod
    def update_all_prices(cls):
        """Recalculate prices for all creatures in the market."""
        creatures = Creature.objects.all()
        updated = 0
        for creature in creatures:
            cls.update_creature_price(creature)
            updated += 1
        return updated

    @staticmethod
    def _completed_snapshot_period(now, interval):
        """Return the last completed UTC bucket as a half-open interval."""
        now_utc = now.astimezone(datetime_timezone.utc)
        if interval == PriceHistory.Interval.ONE_HOUR:
            period_end = now_utc.replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            period_length = timezone.timedelta(hours=1)
        elif interval == PriceHistory.Interval.ONE_DAY:
            period_end = now_utc.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            period_length = timezone.timedelta(days=1)
        elif interval == PriceHistory.Interval.ONE_WEEK:
            day_start = now_utc.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            period_end = day_start - timezone.timedelta(
                days=day_start.weekday()
            )
            period_length = timezone.timedelta(weeks=1)
        else:
            raise ValueError(f"Unsupported price history interval: {interval}")

        return period_end - period_length, period_end

    @classmethod
    def record_price_snapshot(cls, creature, interval):
        """Record the last completed UTC interval as an OHLCV candle."""
        period_start, period_end = cls._completed_snapshot_period(
            timezone.now(),
            interval,
        )

        period_trades = Trade.objects.filter(
            creature=creature,
            executed_at__gte=period_start,
            executed_at__lt=period_end,
        )
        aggregates = period_trades.aggregate(
            volume=Sum('quantity'),
            high=Max('price_per_unit'),
            low=Min('price_per_unit'),
        )

        if aggregates['high'] is None:
            previous_snapshot = PriceHistory.objects.filter(
                creature=creature,
                interval=interval,
                timestamp__lt=period_start,
            ).order_by('-timestamp').first()
            carry_price = (
                previous_snapshot.close_price
                if previous_snapshot
                else creature.current_price
            )
            open_price = high_price = low_price = close_price = carry_price
        else:
            ordered_prices = period_trades.order_by('executed_at', 'pk')
            open_price = ordered_prices.values_list(
                'price_per_unit',
                flat=True,
            ).first()
            close_price = ordered_prices.reverse().values_list(
                'price_per_unit',
                flat=True,
            ).first()
            high_price = aggregates['high']
            low_price = aggregates['low']

        snapshot, _ = PriceHistory.objects.update_or_create(
            creature=creature,
            interval=interval,
            timestamp=period_start,
            defaults={
                'open_price': open_price,
                'high_price': high_price,
                'low_price': low_price,
                'close_price': close_price,
                'volume': aggregates['volume'] or Decimal('0'),
            }
        )
        cache.delete(f"price_history:{creature.pk}:{interval}")
        return snapshot


class TradingEngine:
    """
    Price-time order book with the Market Maker as last-resort liquidity.

    Limit-to-limit fills execute at the resting order's price. The resting
    order is the one with the earlier ``created_at`` (UUID breaks exact ties).
    """

    COMMISSION_RATE = Trade.COMMISSION_RATE  # 1.5%
    # A fill rounds commission independently; 3% covers the worst cent split.
    LIMIT_BUY_RESERVATION_RATE = COMMISSION_RATE * 2
    MONEY_QUANTUM = MONEY_CENT
    MIN_NOTIONAL = Decimal('0.01')
    ACTIVE_STATUSES = [Order.Status.OPEN, Order.Status.PARTIALLY_FILLED]

    @classmethod
    def _check_trading_halted(cls, creature):
        """Raise ValidationError if trading is halted for this creature."""
        if creature.is_trading_halted:
            raise ValidationError(
                f"Trading for {creature.name} is temporarily halted due "
                f"to extreme volatility. Please try again later."
            )

    @classmethod
    def _lock_tradable_creature(cls, creature):
        locked_creature = Creature.objects.select_for_update().get(
            pk=creature.pk
        )
        cls._check_trading_halted(locked_creature)
        return locked_creature

    @classmethod
    def _quantize_money(cls, amount):
        return quantize_money(amount)

    @classmethod
    def _money_to_cents(cls, amount):
        return dollars_to_cents(amount)

    @classmethod
    def _order_amounts(cls, quantity, unit_price):
        try:
            quantity = Decimal(str(quantity))
            unit_price = Decimal(str(unit_price))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("Quantity and price must be valid numbers.")

        if quantity <= 0:
            raise ValidationError("Quantity must be higher than zero.")
        if unit_price < cls.MONEY_QUANTUM:
            raise ValidationError("Price must be at least $0.01.")

        raw_notional = quantity * unit_price
        if raw_notional < cls.MIN_NOTIONAL:
            raise ValidationError("Order notional must be at least $0.01.")

        unit_price = cls._quantize_money(unit_price)
        subtotal = cls._quantize_money(quantity * unit_price)
        commission = cls._quantize_money(
            subtotal * cls.COMMISSION_RATE
        )
        return quantity, unit_price, subtotal, commission

    @classmethod
    def _realized_cost_basis(cls, portfolio, quantity):
        return cls._quantize_money(portfolio.average_cost * quantity)

    @classmethod
    def _reserved_sell_quantity(cls, account, creature):
        return Order.objects.filter(
            account=account,
            creature=creature,
            order_type=Order.OrderType.SELL,
            execution_type=Order.ExecutionType.LIMIT,
            status__in=cls.ACTIVE_STATUSES,
        ).aggregate(total=Sum('reserved_quantity'))['total'] or Decimal('0')

    @classmethod
    def _lock_active_limit_order(cls, order):
        locked_order = Order.objects.select_for_update().select_related(
            'creature'
        ).get(pk=order.pk)
        if (
            locked_order.execution_type != Order.ExecutionType.LIMIT
            or locked_order.status not in cls.ACTIVE_STATUSES
        ):
            raise ValidationError(
                "Only open or partially filled limit orders can be processed."
            )
        return locked_order

    @classmethod
    def _lock_accounts(cls, account_ids):
        from account.models import Account

        accounts = Account.objects.select_for_update().filter(
            pk__in=account_ids
        ).order_by('pk')
        return {account.pk: account for account in accounts}

    @classmethod
    def _lock_portfolios(cls, account_ids, creature):
        portfolios = Portfolio.objects.select_for_update().filter(
            owner_id__in=account_ids,
            creature=creature,
        ).order_by('pk')
        return {portfolio.owner_id: portfolio for portfolio in portfolios}

    @classmethod
    def _limit_orders_for_update(cls, creature, order_type=None, **filters):
        queryset = Order.objects.select_for_update().filter(
            creature=creature,
            execution_type=Order.ExecutionType.LIMIT,
            status__in=cls.ACTIVE_STATUSES,
            **filters,
        )
        if order_type is not None:
            queryset = queryset.filter(order_type=order_type)
        return list(queryset.order_by('pk'))

    @staticmethod
    def _buy_priority(order):
        return (-order.limit_price, order.created_at, str(order.pk))

    @staticmethod
    def _sell_priority(order):
        return (order.limit_price, order.created_at, str(order.pk))

    @staticmethod
    def _resting_price(buy_order, sell_order):
        buy_time = (buy_order.created_at, str(buy_order.pk))
        sell_time = (sell_order.created_at, str(sell_order.pk))
        if buy_time < sell_time:
            return buy_order.limit_price
        return sell_order.limit_price

    @classmethod
    def _planned_cost_cents(cls, fills):
        total = 0
        for _, fill_quantity, price in fills:
            _, _, subtotal, commission = cls._order_amounts(
                fill_quantity,
                price,
            )
            total += cls._money_to_cents(subtotal + commission)
        return total

    @classmethod
    def _reserved_buy_tranche(cls, order, quantity):
        if quantity >= order.remaining_quantity:
            return order.reserved_cents
        _, _, subtotal, commission = cls._order_amounts(
            quantity,
            order.limit_price,
        )
        return min(
            order.reserved_cents,
            cls._money_to_cents(subtotal + commission),
        )

    @classmethod
    def _mark_filled(cls, order, quantity):
        order.filled_quantity += quantity
        if order.is_fully_filled:
            order.filled_quantity = order.quantity
            order.status = Order.Status.FILLED
        else:
            order.status = Order.Status.PARTIALLY_FILLED

    @classmethod
    def _credit_buyer_portfolio(
        cls,
        portfolios,
        buyer,
        creature,
        quantity,
        price,
    ):
        portfolio = portfolios.get(buyer.pk)
        if portfolio is None:
            portfolio = Portfolio.objects.create(
                owner=buyer,
                creature=creature,
                quantity=quantity,
                average_cost=price,
            )
            portfolios[buyer.pk] = portfolio
            return

        total_quantity = portfolio.quantity + quantity
        portfolio.average_cost = cls._quantize_money(
            (
                portfolio.average_cost * portfolio.quantity
                + price * quantity
            )
            / total_quantity
        )
        portfolio.quantity = total_quantity
        portfolio.save(update_fields=['quantity', 'average_cost'])

    @classmethod
    def _debit_seller_portfolio(
        cls,
        portfolios,
        seller,
        creature,
        quantity,
    ):
        portfolio = portfolios.get(seller.pk)
        if portfolio is None or portfolio.quantity < quantity:
            raise ValidationError(
                f"Insufficient {creature.name} holdings for execution."
            )
        realized_cost_basis = cls._realized_cost_basis(portfolio, quantity)
        portfolio.quantity -= quantity
        if portfolio.quantity == 0:
            portfolio.delete()
            portfolios.pop(seller.pk)
        else:
            portfolio.save(update_fields=['quantity'])
        return realized_cost_basis

    @classmethod
    def _execute_fill(
        cls,
        buy_order,
        sell_order,
        quantity,
        price,
        accounts,
        portfolios,
    ):
        buyer = accounts.get(buy_order.account_id) if buy_order else None
        seller = accounts.get(sell_order.account_id) if sell_order else None
        if buyer is not None and seller is not None and buyer.pk == seller.pk:
            raise ValidationError("Self-trades are not allowed.")

        quantity, price, subtotal, commission = cls._order_amounts(
            quantity,
            price,
        )
        actual_cost_cents = cls._money_to_cents(subtotal + commission)

        if buyer is not None:
            if buy_order.execution_type == Order.ExecutionType.LIMIT:
                reserved_tranche = cls._reserved_buy_tranche(
                    buy_order,
                    quantity,
                )
                if reserved_tranche < actual_cost_cents:
                    raise ValidationError(
                        "BUY order reservation is insufficient."
                    )
                buyer.balance_cents += reserved_tranche - actual_cost_cents
                buy_order.reserved_cents -= reserved_tranche
            else:
                buyer.balance_cents -= actual_cost_cents
            buyer.save(update_fields=['balance_cents'])
            cls._credit_buyer_portfolio(
                portfolios,
                buyer,
                buy_order.creature,
                quantity,
                price,
            )

        realized_cost_basis = None
        if seller is not None:
            realized_cost_basis = cls._debit_seller_portfolio(
                portfolios,
                seller,
                sell_order.creature,
                quantity,
            )
            seller.balance_cents += cls._money_to_cents(subtotal - commission)
            seller.save(update_fields=['balance_cents'])
            if sell_order.execution_type == Order.ExecutionType.LIMIT:
                if sell_order.reserved_quantity < quantity:
                    raise ValidationError(
                        "SELL order reservation is insufficient."
                    )
                sell_order.reserved_quantity -= quantity

        if buy_order is not None:
            cls._mark_filled(buy_order, quantity)
            if buy_order.is_fully_filled:
                buy_order.reserved_cents = 0
            buy_order.save(update_fields=[
                'filled_quantity',
                'reserved_cents',
                'status',
            ])
        if sell_order is not None:
            cls._mark_filled(sell_order, quantity)
            if sell_order.is_fully_filled:
                sell_order.reserved_quantity = Decimal('0')
            sell_order.save(update_fields=[
                'filled_quantity',
                'reserved_quantity',
                'status',
            ])

        return Trade.objects.create(
            buyer=buyer,
            seller=seller,
            creature=(buy_order or sell_order).creature,
            buy_order=buy_order,
            sell_order=sell_order,
            quantity=quantity,
            price_per_unit=price,
            commission=commission,
            realized_cost_basis=realized_cost_basis,
        )

    @classmethod
    def _market_buy_plan(cls, sell_orders, quantity, current_price):
        remaining = quantity
        fills = []
        for sell_order in sorted(sell_orders, key=cls._sell_priority):
            if remaining <= 0:
                break
            fill_quantity = min(remaining, sell_order.remaining_quantity)
            fills.append((sell_order, fill_quantity, sell_order.limit_price))
            remaining -= fill_quantity
        if remaining > 0:
            fills.append((None, remaining, current_price))
        return fills

    @classmethod
    def _market_sell_plan(cls, buy_orders, quantity, current_price):
        remaining = quantity
        fills = []
        for buy_order in sorted(buy_orders, key=cls._buy_priority):
            if remaining <= 0:
                break
            fill_quantity = min(remaining, buy_order.remaining_quantity)
            fills.append((buy_order, fill_quantity, buy_order.limit_price))
            remaining -= fill_quantity
        if remaining > 0:
            fills.append((None, remaining, current_price))
        return fills

    @classmethod
    def execute_market_buy(cls, account, creature, quantity):
        """Fill eligible asks first and send only the remainder to the MM."""
        with transaction.atomic():
            creature = cls._lock_tradable_creature(creature)
            quantity, current_price, _, _ = cls._order_amounts(
                quantity,
                creature.current_price,
            )
            sell_orders = cls._limit_orders_for_update(
                creature,
                Order.OrderType.SELL,
                limit_price__lte=current_price,
            )
            sell_orders = [
                order for order in sell_orders
                if order.account_id != account.pk
            ]
            account_ids = {account.pk}
            account_ids.update(order.account_id for order in sell_orders)
            accounts = cls._lock_accounts(account_ids)
            portfolios = cls._lock_portfolios(account_ids, creature)
            fills = cls._market_buy_plan(
                sell_orders,
                quantity,
                current_price,
            )
            total_cost_cents = cls._planned_cost_cents(fills)
            buyer = accounts[account.pk]
            if buyer.balance_cents < total_cost_cents:
                total_cost = Decimal(total_cost_cents) / Decimal('100')
                raise ValidationError(
                    f"Insufficient funds. Need ${total_cost}, "
                    f"have ${buyer.balance_dollars}"
                )

            market_order = Order.objects.create(
                account=buyer,
                creature=creature,
                order_type=Order.OrderType.BUY,
                execution_type=Order.ExecutionType.MARKET,
                quantity=quantity,
                status=Order.Status.OPEN,
            )
            trade = None
            for sell_order, fill_quantity, price in fills:
                trade = cls._execute_fill(
                    market_order,
                    sell_order,
                    fill_quantity,
                    price,
                    accounts,
                    portfolios,
                )
            return trade

    @classmethod
    def execute_market_sell(cls, account, creature, quantity):
        """Fill eligible bids first and send only the remainder to the MM."""
        with transaction.atomic():
            creature = cls._lock_tradable_creature(creature)
            quantity, current_price, _, _ = cls._order_amounts(
                quantity,
                creature.current_price,
            )
            buy_orders = cls._limit_orders_for_update(
                creature,
                Order.OrderType.BUY,
                limit_price__gte=current_price,
            )
            buy_orders = [
                order for order in buy_orders
                if order.account_id != account.pk
            ]
            account_ids = {account.pk}
            account_ids.update(order.account_id for order in buy_orders)
            accounts = cls._lock_accounts(account_ids)
            portfolios = cls._lock_portfolios(account_ids, creature)
            seller = accounts[account.pk]
            portfolio = portfolios.get(seller.pk)
            if portfolio is None:
                raise ValidationError(
                    f"You don't own any {creature.name} to sell."
                )
            reserved_quantity = cls._reserved_sell_quantity(seller, creature)
            available_quantity = portfolio.quantity - reserved_quantity
            if available_quantity < quantity:
                raise ValidationError(
                    f"Insufficient available holdings. "
                    f"Own {portfolio.quantity}, reserved {reserved_quantity}, "
                    f"trying to sell {quantity}."
                )

            market_order = Order.objects.create(
                account=seller,
                creature=creature,
                order_type=Order.OrderType.SELL,
                execution_type=Order.ExecutionType.MARKET,
                quantity=quantity,
                status=Order.Status.OPEN,
            )
            fills = cls._market_sell_plan(
                buy_orders,
                quantity,
                current_price,
            )
            trade = None
            for buy_order, fill_quantity, price in fills:
                trade = cls._execute_fill(
                    buy_order,
                    market_order,
                    fill_quantity,
                    price,
                    accounts,
                    portfolios,
                )
            return trade

    @classmethod
    def place_limit_order(cls, account, creature,
                          order_type, quantity, limit_price):
        """
        Place a limit order that executes when the price crosses the threshold.
        For BUY limits: executes when price <= limit_price.
        For SELL limits: executes when price >= limit_price.
        """
        quantity, limit_price, max_cost, _ = cls._order_amounts(
            quantity,
            limit_price,
        )

        with transaction.atomic():
            from account.models import Account
            creature = cls._lock_tradable_creature(creature)
            locked_account = Account.objects.select_for_update().get(
                pk=account.pk
            )

            if order_type == Order.OrderType.BUY:
                reservation_commission = cls._quantize_money(
                    max_cost * cls.LIMIT_BUY_RESERVATION_RATE
                )
                total_reserved = max_cost + reservation_commission
                total_reserved_cents = cls._money_to_cents(total_reserved)

                if locked_account.balance_cents < total_reserved_cents:
                    raise ValidationError(
                        f"Insufficient funds to reserve for limit order. "
                        f"Need ${total_reserved}."
                    )
                locked_account.balance_cents -= total_reserved_cents
                locked_account.save(update_fields=['balance_cents'])
                reserved_cents = total_reserved_cents
                reserved_quantity = Decimal('0')

            elif order_type == Order.OrderType.SELL:
                try:
                    portfolio = Portfolio.objects.select_for_update().get(
                        owner=locked_account,
                        creature=creature
                    )
                except Portfolio.DoesNotExist:
                    raise ValidationError(
                        f"You don't own any {creature.name} to sell."
                    )
                reserved_quantity = cls._reserved_sell_quantity(
                    locked_account,
                    creature,
                )
                available_quantity = portfolio.quantity - reserved_quantity
                if available_quantity < quantity:
                    raise ValidationError(
                        "Insufficient available holdings for limit sell."
                    )
                reserved_cents = 0
                reserved_quantity = quantity
            else:
                raise ValidationError("Unsupported order type.")

            order = Order.objects.create(
                account=locked_account,
                creature=creature,
                order_type=order_type,
                execution_type=Order.ExecutionType.LIMIT,
                quantity=quantity,
                limit_price=limit_price,
                reserved_cents=reserved_cents,
                reserved_quantity=reserved_quantity,
                status=Order.Status.OPEN,
            )
            return order

    @classmethod
    def check_limit_orders(cls, creature):
        """
        Match the P2P book, then execute MM-eligible remainders.

        The creature, active orders, accounts, and portfolios are locked in
        that stable order so concurrent workers observe each fill once.
        """
        with transaction.atomic():
            try:
                creature = cls._lock_tradable_creature(creature)
            except ValidationError:
                return 0
            current_price = cls._quantize_money(creature.current_price)
            orders = cls._limit_orders_for_update(creature)
            if not orders:
                return 0
            account_ids = {order.account_id for order in orders}
            accounts = cls._lock_accounts(account_ids)
            portfolios = cls._lock_portfolios(account_ids, creature)
            buy_orders = sorted(
                (
                    order for order in orders
                    if order.order_type == Order.OrderType.BUY
                ),
                key=cls._buy_priority,
            )
            sell_orders = sorted(
                (
                    order for order in orders
                    if order.order_type == Order.OrderType.SELL
                ),
                key=cls._sell_priority,
            )
            executed_count = cls._match_p2p_orders(
                buy_orders,
                sell_orders,
                accounts,
                portfolios,
            )
            executed_count += cls._fill_limits_with_market_maker(
                buy_orders,
                sell_orders,
                current_price,
                accounts,
                portfolios,
            )
            return executed_count

    @classmethod
    def _next_cross(cls, buy_orders, sell_orders):
        for buy_order in buy_orders:
            if buy_order.status not in cls.ACTIVE_STATUSES:
                continue
            for sell_order in sell_orders:
                if sell_order.status not in cls.ACTIVE_STATUSES:
                    continue
                if buy_order.limit_price < sell_order.limit_price:
                    break
                if buy_order.account_id != sell_order.account_id:
                    return buy_order, sell_order
        return None

    @classmethod
    def _match_p2p_orders(
        cls,
        buy_orders,
        sell_orders,
        accounts,
        portfolios,
    ):
        count = 0
        while True:
            cross = cls._next_cross(buy_orders, sell_orders)
            if cross is None:
                return count
            buy_order, sell_order = cross
            quantity = min(
                buy_order.remaining_quantity,
                sell_order.remaining_quantity,
            )
            cls._execute_fill(
                buy_order,
                sell_order,
                quantity,
                cls._resting_price(buy_order, sell_order),
                accounts,
                portfolios,
            )
            count += 1

    @classmethod
    def _fill_limits_with_market_maker(
        cls,
        buy_orders,
        sell_orders,
        current_price,
        accounts,
        portfolios,
    ):
        count = 0
        for buy_order in buy_orders:
            if (
                buy_order.status in cls.ACTIVE_STATUSES
                and buy_order.limit_price >= current_price
            ):
                cls._execute_fill(
                    buy_order,
                    None,
                    buy_order.remaining_quantity,
                    current_price,
                    accounts,
                    portfolios,
                )
                count += 1
        for sell_order in sell_orders:
            if (
                sell_order.status in cls.ACTIVE_STATUSES
                and sell_order.limit_price <= current_price
            ):
                cls._execute_fill(
                    None,
                    sell_order,
                    sell_order.remaining_quantity,
                    current_price,
                    accounts,
                    portfolios,
                )
                count += 1
        return count

    @classmethod
    def _execute_limit_buy(cls, order, execution_price):
        """Compatibility entry point for one locked BUY-to-MM fill."""
        return cls._execute_limit_mm_fill(
            order,
            execution_price,
            Order.OrderType.BUY,
        )

    @classmethod
    def _execute_limit_sell(cls, order, execution_price):
        """Compatibility entry point for one locked SELL-to-MM fill."""
        return cls._execute_limit_mm_fill(
            order,
            execution_price,
            Order.OrderType.SELL,
        )

    @classmethod
    def _execute_limit_mm_fill(cls, order, execution_price, order_type):
        """Execute one active limit remainder against Market Maker liquidity."""
        with transaction.atomic():
            order = cls._lock_active_limit_order(order)
            if order.order_type != order_type:
                raise ValidationError("Limit order side does not match fill.")
            cls._check_trading_halted(order.creature)
            accounts = cls._lock_accounts({order.account_id})
            portfolios = cls._lock_portfolios(
                {order.account_id},
                order.creature,
            )
            buy_order = (
                order if order_type == Order.OrderType.BUY else None
            )
            sell_order = (
                order if order_type == Order.OrderType.SELL else None
            )
            cls._execute_fill(
                buy_order,
                sell_order,
                order.remaining_quantity,
                execution_price,
                accounts,
                portfolios,
            )
            return True

    @classmethod
    def cancel_order(cls, order):
        """Cancel an active limit order and release its exact remainder."""
        with transaction.atomic():
            order = cls._lock_active_limit_order(order)

            if order.order_type == Order.OrderType.BUY:
                account = cls._lock_accounts({order.account_id})[
                    order.account_id
                ]
                account.balance_cents += order.reserved_cents
                account.save(update_fields=['balance_cents'])
                order.reserved_cents = 0
            else:
                order.reserved_quantity = Decimal('0')

            order.status = Order.Status.CANCELLED
            order.save(update_fields=[
                'reserved_cents',
                'reserved_quantity',
                'status',
            ])
            return order


class MarketIndicesService:
    """
    Calculates and records type-based market indices.
    Each index is a volume-weighted average price of all creatures
    of that type currently in the market.
    """

    CACHE_KEY = "market_indices:latest"
    CACHE_TIMEOUT = 300

    @classmethod
    def invalidate_cache(cls):
        cache.delete(cls.CACHE_KEY)

    @classmethod
    def get_latest_indices(cls):
        cached = cache.get(cls.CACHE_KEY)
        if cached is not None:
            return cached

        latest_indices = []
        for type_choice in MarketIndex._meta.get_field('creature_type').choices:
            type_value = type_choice[0]
            latest = MarketIndex.objects.filter(
                creature_type=type_value
            ).order_by('-timestamp').first()
            if latest:
                latest_indices.append(latest)

        cache.set(cls.CACHE_KEY, latest_indices, timeout=cls.CACHE_TIMEOUT)
        return latest_indices

    @classmethod
    def calculate_all_indices(cls):
        """
        Calculate and record MarketIndex for every creature type
        that has active creatures.
        """
        from creature.models import PrimaryType

        indices = []
        for type_choice in PrimaryType.choices:
            type_value = type_choice[0]
            index = cls.calculate_type_index(type_value)
            if index:
                indices.append(index)

        cls.invalidate_cache()
        return indices

    @classmethod
    def calculate_type_index(cls, creature_type):
        """
        Calculate a single type index: volume-weighted average price.
        index_value = Σ(price_i * volume_i) / Σ(volume_i)
        If no trades, use simple average of current prices.
        """
        from creature.models import Creature

        creatures = list(Creature.objects.filter(
            type=creature_type
        ).exclude(current_price=0).annotate(
            trade_volume=Sum('trades__quantity')
        ))

        if not creatures:
            return None

        count = len(creatures)
        total_volume = sum(
            (creature.trade_volume or Decimal('0') for creature in creatures),
            Decimal('0'),
        )
        if total_volume:
            weighted_sum = sum(
                (
                    creature.current_price
                    * (creature.trade_volume or Decimal('0'))
                    for creature in creatures
                ),
                Decimal('0'),
            )
            avg_price = (weighted_sum / total_volume).quantize(
                Decimal('0.01')
            )
        else:
            # With no observed volume, every creature receives equal weight.
            price_sum = sum(
                (creature.current_price for creature in creatures),
                Decimal('0'),
            )
            avg_price = (
                price_sum / Decimal(count)
            ).quantize(Decimal('0.01'))

        last_index = MarketIndex.objects.filter(
            creature_type=creature_type
        ).order_by('-timestamp').first()
        previous_value = last_index.value if last_index else Decimal('0')

        index = MarketIndex.objects.create(
            creature_type=creature_type,
            value=avg_price,
            previous_value=previous_value,
            total_volume=total_volume,
            creature_count=count,
        )
        return index


class VolatilityService:
    """
    Monitors price changes and triggers circuit breakers + notifications
    when a creature's price moves more than 10% in a short period.
    """

    VOLATILITY_THRESHOLD = Decimal('10.0')
    CIRCUIT_BREAKER_DURATION = 15  # minutes

    @classmethod
    def check_volatility(cls, creature):
        """
        Check if the creature's price change exceeds the volatility threshold.
        If so, trigger a circuit breaker and notify all portfolio holders.
        """
        if creature.previous_close == 0:
            return

        change_pct = (
            (creature.current_price - creature.previous_close)
            / creature.previous_close * 100
        ).quantize(Decimal('0.01'))

        if abs(change_pct) < cls.VOLATILITY_THRESHOLD:
            return
        if creature.is_trading_halted:
            return

        cls._trigger_circuit_breaker(creature, change_pct)
        cls._notify_holders(creature, change_pct)

    @classmethod
    def _trigger_circuit_breaker(cls, creature, change_pct):
        """Freeze trading for the creature."""
        from django.utils import timezone
        creature.circuit_breaker_expires_at = (
            timezone.now()
            + timezone.timedelta(minutes=cls.CIRCUIT_BREAKER_DURATION)
        )
        creature.save(update_fields=['circuit_breaker_expires_at'])

    @classmethod
    def _notify_holders(cls, creature, change_pct):
        """Notify all users who hold this creature in their portfolio."""
        holders = Portfolio.objects.filter(
            creature=creature,
            quantity__gt=0,
        ).select_related('owner')

        direction = "surged" if change_pct > 0 else "plummeted"
        emoji = "📈" if change_pct > 0 else "📉"

        notifications = []
        for entry in holders:
            notifications.append(Notification(
                user=entry.owner,
                notification_type=Notification.Type.VOLATILITY_ALERT,
                title=f"{emoji} {
                    creature.name} {direction} {
                    abs(change_pct)}%",
                message=(
                    f"{
                        creature.name} price moved {
                        abs(change_pct)}% in minutes. "
                    f"Trading has been halted for {
                        cls.CIRCUIT_BREAKER_DURATION} minutes. "
                    f"Current price: ${creature.current_price}."
                ),
                related_creature=creature,
            ))

        Notification.objects.bulk_create(notifications)

    @classmethod
    def check_all_creatures(cls):
        """Check volatility for all creatures."""
        from creature.models import Creature
        creatures = Creature.objects.all()
        alerts = 0
        for creature in creatures:
            cls.check_volatility(creature)
            alerts += 1
        return alerts
