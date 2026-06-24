from celery import shared_task
from decimal import Decimal

from creature.models import Creature
from .models import PriceHistory
from .services import PricingEngine, TradingEngine


@shared_task
def update_creature_prices():
    """
    Periodic task: recalculates dynamic prices for all creatures.
    Runs every minute via Celery Beat.
    """
    updated = PricingEngine.update_all_prices()
    return f"Updated prices for {updated} creatures."


@shared_task
def check_pending_limit_orders():
    """
    Periodic task: checks all creatures for limit orders that should execute.
    Runs every 30 seconds via Celery Beat.
    """
    creatures = Creature.objects.all()
    total_executed = 0
    for creature in creatures:
        executed = TradingEngine.check_limit_orders(creature)
        total_executed += executed
    return f"Executed {total_executed} limit orders."


@shared_task
def record_hourly_price_snapshots():
    """
    Periodic task: records an hourly OHLCV snapshot for all creatures.
    Runs every hour via Celery Beat.
    """
    creatures = Creature.objects.all()
    count = 0
    for creature in creatures:
        PricingEngine.record_price_snapshot(creature, PriceHistory.Interval.ONE_HOUR)
        count += 1
    return f"Recorded hourly snapshots for {count} creatures."


@shared_task
def record_daily_price_snapshots():
    """
    Periodic task: records a daily OHLCV snapshot for all creatures.
    Runs once a day via Celery Beat.
    """
    creatures = Creature.objects.all()
    count = 0
    for creature in creatures:
        PricingEngine.record_price_snapshot(creature, PriceHistory.Interval.ONE_DAY)
        count += 1
    return f"Recorded daily snapshots for {count} creatures."


@shared_task
def update_price_after_battle(creature_id, won):
    """
    One-shot task: called after a battle finishes to immediately
    update the price of a participating creature.
    """
    try:
        creature = Creature.objects.get(pk=creature_id)
    except Creature.DoesNotExist:
        return f"Creature {creature_id} not found."

    new_price = PricingEngine.update_creature_price(creature)
    result = "won" if won else "lost"
    return f"{creature.name} {result} — new price: ${new_price}"


@shared_task
def calculate_market_indices():
    """
    Periodic task: calculate and record market indices for all types.
    Should run daily via Celery Beat.
    """
    from .services import MarketIndicesService
    indices = MarketIndicesService.calculate_all_indices()
    return f"Recorded {len(indices)} market indices."
