import random
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from account.money import quantize_money
from creature.models import Creature, EggTemplate, Incubation, Battle
from trading.models import Portfolio, Order
from trading.services import TradingEngine
from .state import record_simulation_event


class BaseStrategy:
    name = "BASE"

    def evaluate_and_execute(self, user, creatures) -> dict | None:
        raise NotImplementedError


class MomentumStrategy(BaseStrategy):
    """
    Follows market momentum and recent battle results:
    - Buys creatures with winning momentum.
    - Sells creatures with negative momentum.
    """
    name = "MOMENTUM"

    def evaluate_and_execute(self, user, creatures) -> dict | None:
        if not creatures:
            return None

        # Look for a creature that won recent battles
        recent_win_creatures = []
        recent_finished_battles = Battle.objects.filter(status='finished').order_by('-updated_at')[:10]
        for battle in recent_finished_battles:
            if battle.winner and battle.winner.creature in creatures:
                recent_win_creatures.append(battle.winner.creature)

        target_creature = random.choice(recent_win_creatures) if recent_win_creatures else random.choice(creatures)

        # Check if user has portfolio of a losing creature to sell
        user_portfolios = list(Portfolio.objects.filter(owner=user, quantity__gt=Decimal('0.05')))
        if user_portfolios and random.random() < 0.35:
            port = random.choice(user_portfolios)
            sell_qty = quantize_money(min(port.quantity * Decimal('0.5'), Decimal('2.0')))
            if sell_qty > Decimal('0'):
                try:
                    TradingEngine.execute_market_sell(user, port.creature, sell_qty)
                    return record_simulation_event(
                        self.name,
                        'SELL',
                        port.creature.name,
                        sell_qty,
                        f"Trend liquidation: sold {sell_qty} units"
                    )
                except Exception:
                    pass

        # Otherwise execute MARKET BUY on target creature
        buy_qty = quantize_money(Decimal(str(random.choice([0.5, 1.0, 1.5, 2.0]))))
        cost = buy_qty * target_creature.current_price
        cost_cents = int(cost * 100)

        if user.balance_cents >= cost_cents:
            try:
                TradingEngine.execute_market_buy(user, target_creature, buy_qty)
                return record_simulation_event(
                    self.name,
                    'BUY',
                    target_creature.name,
                    buy_qty,
                    f"Riding upward momentum after recent performance"
                )
            except Exception:
                pass

        return None


class ValueStrategy(BaseStrategy):
    """
    Mean-reversion / Fundamental value hunting:
    - Buys when current_price < pricing_reference_price (discount >= 8%).
    - Sells when current_price > pricing_reference_price (premium >= 15%).
    """
    name = "VALUE"

    def evaluate_and_execute(self, user, creatures) -> dict | None:
        if not creatures:
            return None

        # Find undervalued candidates
        undervalued = [
            c for c in creatures
            if c.pricing_reference_price and c.current_price <= c.pricing_reference_price * Decimal('0.92')
        ]
        # Find overvalued candidates
        overvalued = [
            c for c in creatures
            if c.pricing_reference_price and c.current_price >= c.pricing_reference_price * Decimal('1.15')
        ]

        # Prioritize taking profit on overvalued holdings if owned
        if overvalued:
            for c in overvalued:
                portfolio = Portfolio.objects.filter(owner=user, creature=c, quantity__gt=Decimal('0')).first()
                if portfolio:
                    sell_qty = quantize_money(portfolio.quantity)
                    try:
                        TradingEngine.execute_market_sell(user, c, sell_qty)
                        return record_simulation_event(
                            self.name,
                            'SELL',
                            c.name,
                            sell_qty,
                            f"Overvalued ({c.current_price} vs ref {c.pricing_reference_price}), taking profits"
                        )
                    except Exception:
                        pass

        # Buy undervalued
        if undervalued:
            target = random.choice(undervalued)
            qty = quantize_money(Decimal(str(random.choice([1.0, 2.0, 3.0]))))
            cost_cents = int(qty * target.current_price * 100)
            if user.balance_cents >= cost_cents:
                try:
                    TradingEngine.execute_market_buy(user, target, qty)
                    return record_simulation_event(
                        self.name,
                        'BUY',
                        target.name,
                        qty,
                        f"Bargain hunting: spot {target.current_price} vs ref {target.pricing_reference_price}"
                    )
                except Exception:
                    pass

        return None


class MarketMakerStrategy(BaseStrategy):
    """
    Provides liquidity by placing resting LIMIT orders on both sides of the spread.
    """
    name = "MARKET_MAKER"

    def evaluate_and_execute(self, user, creatures) -> dict | None:
        if not creatures:
            return None

        creature = random.choice(creatures)
        price = creature.current_price

        # Choose action: limit buy or limit sell
        portfolio = Portfolio.objects.filter(owner=user, creature=creature, quantity__gte=Decimal('1.0')).first()
        should_sell = bool(portfolio and random.random() < 0.5)

        if should_sell:
            # Place limit sell slightly above spot (+1% to +3%)
            spread = Decimal(str(random.uniform(1.01, 1.03)))
            limit_price = quantize_money(price * spread)
            qty = quantize_money(Decimal('1.0'))
            try:
                TradingEngine.place_limit_order(
                    user, creature, Order.OrderType.SELL, qty, limit_price
                )
                return record_simulation_event(
                    self.name,
                    'LIMIT_SELL',
                    creature.name,
                    qty,
                    f"Providing ask liquidity at ${limit_price} (spread +{round((spread - 1) * 100, 1)}%)"
                )
            except Exception:
                pass
        else:
            # Place limit buy slightly below spot (-1% to -3%)
            spread = Decimal(str(random.uniform(0.97, 0.99)))
            limit_price = quantize_money(price * spread)
            qty = quantize_money(Decimal('1.0'))
            cost_cents = int(limit_price * qty * 100)
            if user.balance_cents >= cost_cents:
                try:
                    TradingEngine.place_limit_order(
                        user, creature, Order.OrderType.BUY, qty, limit_price
                    )
                    return record_simulation_event(
                        self.name,
                        'LIMIT_BUY',
                        creature.name,
                        qty,
                        f"Providing bid liquidity at ${limit_price} (spread -{round((1 - spread) * 100, 1)}%)"
                    )
                except Exception:
                    pass

        return None


class CasualStrategy(BaseStrategy):
    """
    Casual noise trader:
    - Places occasional small market orders.
    - Incubates eggs if funds allow.
    """
    name = "CASUAL"

    def evaluate_and_execute(self, user, creatures) -> dict | None:
        if not creatures:
            return None

        # 30% chance to incubate an egg if available and affordable
        if random.random() < 0.30:
            eggs = list(EggTemplate.objects.filter(is_active=True))
            if eggs:
                egg = random.choice(eggs)
                cost_cents = int(egg.price * 100)
                if user.balance_cents >= cost_cents:
                    try:
                        with transaction.atomic():
                            locked_user = type(user).objects.select_for_update().get(pk=user.pk)
                            if locked_user.balance_cents >= cost_cents:
                                locked_user.balance_cents -= cost_cents
                                locked_user.save(update_fields=['balance_cents'])
                                Incubation.objects.create(
                                    user=locked_user,
                                    egg_template=egg,
                                    hatches_at=timezone.now() + egg.hatch_duration,
                                )
                                return record_simulation_event(
                                    self.name,
                                    'INCUBATE',
                                    egg.name,
                                    1,
                                    f"Started incubating {egg.name} for ${egg.price}"
                                )
                    except Exception:
                        pass

        # Otherwise small market buy
        creature = random.choice(creatures)
        qty = quantize_money(Decimal(str(random.choice([0.1, 0.25, 0.5, 1.0]))))
        cost_cents = int(qty * creature.current_price * 100)

        if user.balance_cents >= cost_cents:
            try:
                TradingEngine.execute_market_buy(user, creature, qty)
                return record_simulation_event(
                    self.name,
                    'BUY',
                    creature.name,
                    qty,
                    f"Casual purchase of {creature.name} for ${quantize_money(qty * creature.current_price)}"
                )
            except Exception:
                pass

        return None
