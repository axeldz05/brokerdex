from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import Sum, Q, Count, Avg, Max, Min

from .models import Order, Trade, Portfolio, PriceHistory, MarketIndex, Notification
from creature.models import Creature, Battle


class PricingEngine:
    """
    Dynamic Pricing Engine.
    Formula: price = base_price × (1 + Δ_battles + Δ_market) × rarity_multiplier

    - Δ_battles: based on recent win/loss ratio
    - Δ_market: buy/sell volume ratio from recent trades
    - rarity_multiplier: legendary/mythical premium
    """

    BATTLE_WEIGHT = Decimal('0.10')    # max ±10% from battles
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
    def calculate_battle_delta(creature):
        """
        Compute Δ_battles from the last 20 battles.
        Positive if winning more, negative if losing more.
        """
        recent_battles = Battle.objects.filter(
            status='finished',
            participants__creature=creature
        ).distinct().order_by('-updated_at')[:20]

        if not recent_battles.exists():
            return Decimal('0')

        wins = 0
        total = 0
        for battle in recent_battles:
            total += 1
            if battle.winner and battle.winner.creature_id == creature.pk:
                wins += 1

        if total == 0:
            return Decimal('0')

        win_rate = Decimal(str(wins)) / Decimal(str(total))
        # Center around 0.5: win_rate=1.0 → +BATTLE_WEIGHT, win_rate=0.0 → -BATTLE_WEIGHT
        delta = (win_rate - Decimal('0.5')) * 2 * PricingEngine.BATTLE_WEIGHT
        return delta

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

        buy_volume = recent_trades.aggregate(
            total=Sum('quantity')
        )['total'] or Decimal('0')

        # For sell volume, count trades where creature was sold (seller != None)
        sell_volume = recent_trades.filter(
            seller__isnull=False
        ).aggregate(
            total=Sum('quantity')
        )['total'] or Decimal('0')

        total_volume = buy_volume + sell_volume
        if total_volume == 0:
            return Decimal('0')

        # Ratio centered at 0: all buys → +MARKET_WEIGHT, all sells → -MARKET_WEIGHT
        buy_ratio = buy_volume / total_volume
        delta = (buy_ratio - Decimal('0.5')) * 2 * PricingEngine.MARKET_WEIGHT
        return delta

    @classmethod
    def calculate_price(cls, creature):
        """
        Calculate the new dynamic price for a creature.
        """
        base_price = creature.current_price
        battle_delta = cls.calculate_battle_delta(creature)
        market_delta = cls.calculate_market_delta(creature)
        rarity = cls.get_rarity_multiplier(creature)

        multiplier = (1 + battle_delta + market_delta) * rarity
        new_price = base_price * multiplier

        # Floor at $0.01, cap at Decimal(12,2) max to avoid DB overflow
        MAX_PRICE = Decimal('9999999999.99')
        return max(min(new_price, MAX_PRICE), Decimal('0.01')).quantize(Decimal('0.01'))

    @classmethod
    def update_creature_price(cls, creature):
        """Recalculate and persist the new price for a creature."""
        old_price = creature.current_price
        creature.previous_close = old_price
        new_price = cls.calculate_price(creature)
        creature.current_price = new_price
        creature.save(update_fields=['current_price', 'previous_close'])
        VolatilityService.check_volatility(creature)
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

    @classmethod
    def record_price_snapshot(cls, creature, interval):
        """
        Record an OHLCV snapshot for a given interval.
        Uses the current price as OHLC (simplified; for accurate OHLC
        we'd need to track intra-period high/low from trades).
        """
        now = timezone.now()
        price = creature.current_price

        # Get volume from trades in this interval
        if interval == PriceHistory.Interval.ONE_HOUR:
            period_start = now - timezone.timedelta(hours=1)
        elif interval == PriceHistory.Interval.ONE_DAY:
            period_start = now - timezone.timedelta(days=1)
        else:
            period_start = now - timezone.timedelta(weeks=1)

        period_trades = Trade.objects.filter(
            creature=creature,
            executed_at__gte=period_start,
            executed_at__lte=now
        )
        volume = period_trades.aggregate(
            total=Sum('quantity')
        )['total'] or Decimal('0')

        # Get actual high/low from trade prices in this period
        price_agg = period_trades.aggregate(
            high=Max('price_per_unit'),
            low=Min('price_per_unit')
        )

        # Use open from previous snapshot or current price
        previous_snapshot = PriceHistory.objects.filter(
            creature=creature,
            interval=interval
        ).order_by('-timestamp').first()

        open_price = previous_snapshot.close_price if previous_snapshot else price
        high_price = max(price, price_agg['high'] or price)
        low_price = min(price, price_agg['low'] or price)

        snapshot, created = PriceHistory.objects.update_or_create(
            creature=creature,
            interval=interval,
            timestamp=now.replace(minute=0, second=0, microsecond=0),
            defaults={
                'open_price': open_price,
                'high_price': high_price,
                'low_price': low_price,
                'close_price': price,
                'volume': volume,
            }
        )
        return snapshot


class TradingEngine:
    """
    Core trading engine for executing market and limit orders.
    Handles order creation, matching, portfolio updates, and balance changes.
    """

    COMMISSION_RATE = Trade.COMMISSION_RATE  # 1.5%

    @classmethod
    def _check_trading_halted(cls, creature):
        """Raise ValidationError if trading is halted for this creature."""
        if creature.is_trading_halted:
            raise ValidationError(
                f"Trading for {creature.name} is temporarily halted due "
                f"to extreme volatility. Please try again later."
            )

    @classmethod
    def execute_market_buy(cls, account, creature, quantity):
        """
        Execute an immediate market buy order.
        Deducts balance, creates Portfolio entry, records Trade.
        """
        cls._check_trading_halted(creature)
        quantity = Decimal(str(quantity))
        price = creature.current_price
        subtotal = quantity * price
        commission = (subtotal * cls.COMMISSION_RATE).quantize(Decimal('0.01'))
        total_cost = subtotal + commission

        with transaction.atomic():
            # Re-fetch account with row lock
            from account.models import Account
            locked_account = Account.objects.select_for_update().get(pk=account.pk)

            if locked_account.balance_cents < int(total_cost * 100):
                raise ValidationError(
                    f"Insufficient funds. Need ${total_cost}, "
                    f"have ${locked_account.balance_dollars}"
                )

            # Create the order record
            order = Order.objects.create(
                account=locked_account,
                creature=creature,
                order_type=Order.OrderType.BUY,
                execution_type=Order.ExecutionType.MARKET,
                quantity=quantity,
                filled_quantity=quantity,
                status=Order.Status.FILLED,
            )

            # Create the trade record
            trade = Trade.objects.create(
                buyer=locked_account,
                seller=None,  # Market Maker
                creature=creature,
                buy_order=order,
                quantity=quantity,
                price_per_unit=price,
                commission=commission,
            )

            # Deduct balance
            locked_account.balance_cents -= int(total_cost * 100)
            locked_account.save(update_fields=['balance_cents'])

            # Update or create portfolio entry
            portfolio, created = Portfolio.objects.select_for_update().get_or_create(
                owner=locked_account,
                creature=creature,
                defaults={
                    'quantity': quantity,
                    'average_cost': price,
                }
            )
            if not created:
                # Weighted average cost
                total_qty = portfolio.quantity + quantity
                if total_qty > 0:
                    portfolio.average_cost = (
                        (portfolio.average_cost * portfolio.quantity + price * quantity)
                        / total_qty
                    ).quantize(Decimal('0.01'))
                portfolio.quantity = total_qty
                portfolio.save(update_fields=['quantity', 'average_cost'])

            return trade

    @classmethod
    def execute_market_sell(cls, account, creature, quantity):
        """
        Execute an immediate market sell order.
        Credits balance, reduces Portfolio, records Trade.
        """
        cls._check_trading_halted(creature)
        quantity = Decimal(str(quantity))
        price = creature.current_price
        subtotal = quantity * price
        commission = (subtotal * cls.COMMISSION_RATE).quantize(Decimal('0.01'))
        net_proceeds = subtotal - commission

        with transaction.atomic():
            from account.models import Account
            locked_account = Account.objects.select_for_update().get(pk=account.pk)

            # Check portfolio
            try:
                portfolio = Portfolio.objects.select_for_update().get(
                    owner=locked_account,
                    creature=creature
                )
            except Portfolio.DoesNotExist:
                raise ValidationError(
                    f"You don't own any {creature.name} to sell."
                )

            if portfolio.quantity < quantity:
                raise ValidationError(
                    f"Insufficient holdings. Own {portfolio.quantity}, "
                    f"trying to sell {quantity}."
                )

            # Create order
            order = Order.objects.create(
                account=locked_account,
                creature=creature,
                order_type=Order.OrderType.SELL,
                execution_type=Order.ExecutionType.MARKET,
                quantity=quantity,
                filled_quantity=quantity,
                status=Order.Status.FILLED,
            )

            # Create trade
            trade = Trade.objects.create(
                buyer=None,  # Market Maker buys
                seller=locked_account,
                creature=creature,
                sell_order=order,
                quantity=quantity,
                price_per_unit=price,
                commission=commission,
            )

            # Credit balance
            locked_account.balance_cents += int(net_proceeds * 100)
            locked_account.save(update_fields=['balance_cents'])

            # Update portfolio
            portfolio.quantity -= quantity
            if portfolio.quantity <= 0:
                portfolio.delete()
            else:
                portfolio.save(update_fields=['quantity'])

            return trade

    @classmethod
    def place_limit_order(cls, account, creature, order_type, quantity, limit_price):
        """
        Place a limit order that will be executed when the price crosses the threshold.
        For BUY limits: executes when price <= limit_price.
        For SELL limits: executes when price >= limit_price.
        """
        quantity = Decimal(str(quantity))
        limit_price = Decimal(str(limit_price))

        with transaction.atomic():
            from account.models import Account
            locked_account = Account.objects.select_for_update().get(pk=account.pk)

            if order_type == Order.OrderType.BUY:
                # Reserve funds for buy limit order
                max_cost = quantity * limit_price
                commission = (max_cost * cls.COMMISSION_RATE).quantize(Decimal('0.01'))
                total_reserved = max_cost + commission

                if locked_account.balance_cents < int(total_reserved * 100):
                    raise ValidationError(
                        f"Insufficient funds to reserve for limit order. "
                        f"Need ${total_reserved}."
                    )
                locked_account.balance_cents -= int(total_reserved * 100)
                locked_account.save(update_fields=['balance_cents'])

            elif order_type == Order.OrderType.SELL:
                # Verify holdings
                try:
                    portfolio = Portfolio.objects.select_for_update().get(
                        owner=locked_account,
                        creature=creature
                    )
                except Portfolio.DoesNotExist:
                    raise ValidationError(
                        f"You don't own any {creature.name} to sell."
                    )
                if portfolio.quantity < quantity:
                    raise ValidationError(
                        f"Insufficient holdings for limit sell."
                    )

            order = Order.objects.create(
                account=locked_account,
                creature=creature,
                order_type=order_type,
                execution_type=Order.ExecutionType.LIMIT,
                quantity=quantity,
                limit_price=limit_price,
                status=Order.Status.OPEN,
            )
            return order

    @classmethod
    def check_limit_orders(cls, creature):
        """
        Check and execute pending limit orders for a creature
        when the price crosses their threshold.
        """
        with transaction.atomic():
            current_price = creature.current_price
            executed_count = 0

            # BUY limits: execute when current_price <= limit_price
            buy_orders = Order.objects.filter(
                creature=creature,
                order_type=Order.OrderType.BUY,
                execution_type=Order.ExecutionType.LIMIT,
                status__in=[Order.Status.OPEN, Order.Status.PARTIALLY_FILLED],
                limit_price__gte=current_price,
            ).select_for_update()

            for order in buy_orders:
                try:
                    cls._execute_limit_buy(order, current_price)
                    executed_count += 1
                except (ValidationError, Exception):
                    continue

            # SELL limits: execute when current_price >= limit_price
            sell_orders = Order.objects.filter(
                creature=creature,
                order_type=Order.OrderType.SELL,
                execution_type=Order.ExecutionType.LIMIT,
                status__in=[Order.Status.OPEN, Order.Status.PARTIALLY_FILLED],
                limit_price__lte=current_price,
            ).select_for_update()

            for order in sell_orders:
                try:
                    cls._execute_limit_sell(order, current_price)
                    executed_count += 1
                except (ValidationError, Exception):
                    continue

            return executed_count

    @classmethod
    def _execute_limit_buy(cls, order, execution_price):
        """Execute a pending buy limit order at the given price."""
        remaining = order.remaining_quantity

        with transaction.atomic():
            from account.models import Account
            account = Account.objects.select_for_update().get(pk=order.account_id)

            subtotal = remaining * execution_price
            commission = (subtotal * cls.COMMISSION_RATE).quantize(Decimal('0.01'))

            # Refund the difference if execution price < limit price
            reserved_cost = remaining * order.limit_price
            reserved_commission = (reserved_cost * cls.COMMISSION_RATE).quantize(Decimal('0.01'))
            total_reserved = reserved_cost + reserved_commission
            actual_cost = subtotal + commission
            refund = int((total_reserved - actual_cost) * 100)

            if refund > 0:
                account.balance_cents += refund
                account.save(update_fields=['balance_cents'])

            # Create trade
            Trade.objects.create(
                buyer=account,
                seller=None,
                creature=order.creature,
                buy_order=order,
                quantity=remaining,
                price_per_unit=execution_price,
                commission=commission,
            )

            # Update portfolio
            portfolio, created = Portfolio.objects.select_for_update().get_or_create(
                owner=account,
                creature=order.creature,
                defaults={
                    'quantity': remaining,
                    'average_cost': execution_price,
                }
            )
            if not created:
                total_qty = portfolio.quantity + remaining
                portfolio.average_cost = (
                    (portfolio.average_cost * portfolio.quantity + execution_price * remaining)
                    / total_qty
                ).quantize(Decimal('0.01'))
                portfolio.quantity = total_qty
                portfolio.save(update_fields=['quantity', 'average_cost'])

            # Update order status
            order.filled_quantity = order.quantity
            order.status = Order.Status.FILLED
            order.save(update_fields=['filled_quantity', 'status'])

    @classmethod
    def _execute_limit_sell(cls, order, execution_price):
        """Execute a pending sell limit order at the given price."""
        remaining = order.remaining_quantity

        with transaction.atomic():
            from account.models import Account
            account = Account.objects.select_for_update().get(pk=order.account_id)

            subtotal = remaining * execution_price
            commission = (subtotal * cls.COMMISSION_RATE).quantize(Decimal('0.01'))
            net_proceeds = subtotal - commission

            # Check portfolio still has holdings
            try:
                portfolio = Portfolio.objects.select_for_update().get(
                    owner=account,
                    creature=order.creature
                )
            except Portfolio.DoesNotExist:
                order.status = Order.Status.CANCELLED
                order.save(update_fields=['status'])
                return

            if portfolio.quantity < remaining:
                order.status = Order.Status.CANCELLED
                order.save(update_fields=['status'])
                return

            # Create trade
            Trade.objects.create(
                buyer=None,
                seller=account,
                creature=order.creature,
                sell_order=order,
                quantity=remaining,
                price_per_unit=execution_price,
                commission=commission,
            )

            # Credit balance
            account.balance_cents += int(net_proceeds * 100)
            account.save(update_fields=['balance_cents'])

            # Update portfolio
            portfolio.quantity -= remaining
            if portfolio.quantity <= 0:
                portfolio.delete()
            else:
                portfolio.save(update_fields=['quantity'])

            # Update order
            order.filled_quantity = order.quantity
            order.status = Order.Status.FILLED
            order.save(update_fields=['filled_quantity', 'status'])

    @classmethod
    def cancel_order(cls, order):
        """Cancel an open limit order and refund reserved funds if applicable."""
        if order.status not in [Order.Status.OPEN, Order.Status.PARTIALLY_FILLED]:
            raise ValidationError("Only open or partially filled orders can be cancelled.")

        with transaction.atomic():
            from account.models import Account

            if order.order_type == Order.OrderType.BUY:
                # Refund reserved funds
                remaining = order.remaining_quantity
                reserved_cost = remaining * order.limit_price
                reserved_commission = (reserved_cost * cls.COMMISSION_RATE).quantize(Decimal('0.01'))
                refund = int((reserved_cost + reserved_commission) * 100)

                account = Account.objects.select_for_update().get(pk=order.account_id)
                account.balance_cents += refund
                account.save(update_fields=['balance_cents'])

            order.status = Order.Status.CANCELLED
            order.save(update_fields=['status'])
            return order


class MarketIndicesService:
    """
    Calculates and records type-based market indices.
    Each index is a volume-weighted average price of all creatures
    of that type currently in the market.
    """

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

        return indices

    @classmethod
    def calculate_type_index(cls, creature_type):
        """
        Calculate a single type index: volume-weighted average price.
        index_value = Σ(price_i * volume_i) / Σ(volume_i)
        If no trades, use simple average of current prices.
        """
        from creature.models import Creature

        creatures = Creature.objects.filter(
            type=creature_type
        ).exclude(current_price=0)

        if not creatures.exists():
            return None

        count = creatures.count()
        total_volume = Trade.objects.filter(
            creature__type=creature_type,
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')

        weighted_sum = Decimal('0')
        for c in creatures:
            weighted_sum += c.current_price

        avg_price = (weighted_sum / Decimal(str(count))).quantize(Decimal('0.01'))

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

        if abs(change_pct) >= cls.VOLATILITY_THRESHOLD:
            if not creature.is_trading_halted:
                cls._trigger_circuit_breaker(creature, change_pct)
            cls._notify_holders(creature, change_pct)

    @classmethod
    def _trigger_circuit_breaker(cls, creature, change_pct):
        """Freeze trading for the creature."""
        from django.utils import timezone
        creature.circuit_breaker_expires_at = (
            timezone.now() + timezone.timedelta(minutes=cls.CIRCUIT_BREAKER_DURATION)
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
                title=f"{emoji} {creature.name} {direction} {abs(change_pct)}%",
                message=(
                    f"{creature.name} price moved {abs(change_pct)}% in minutes. "
                    f"Trading has been halted for {cls.CIRCUIT_BREAKER_DURATION} minutes. "
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
