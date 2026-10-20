from django.core.cache import cache
from django.utils import timezone


STATE_KEY = 'brokerdex:simulation:active'
STATS_KEY = 'brokerdex:simulation:stats'


def is_simulation_active() -> bool:
    """Return whether the automated simulation bots are active."""
    val = cache.get(STATE_KEY)
    if val is None:
        return True
    return bool(val)


def set_simulation_active(active: bool) -> None:
    """Set the active/paused state of the automated simulation bots."""
    cache.set(STATE_KEY, active, timeout=None)


def get_simulation_stats() -> dict:
    """Return metrics for the current simulation session."""
    stats = cache.get(STATS_KEY)
    if not stats:
        stats = {
            'total_events': 0,
            'strategies': {
                'MOMENTUM': 0,
                'VALUE': 0,
                'MARKET_MAKER': 0,
                'CASUAL': 0,
            },
            'last_event_at': None,
        }
    return stats


def reset_simulation_stats() -> None:
    """Reset simulation stats counters."""
    cache.set(
        STATS_KEY,
        {
            'total_events': 0,
            'strategies': {
                'MOMENTUM': 0,
                'VALUE': 0,
                'MARKET_MAKER': 0,
                'CASUAL': 0,
            },
            'last_event_at': None,
        },
        timeout=None,
    )


def record_simulation_event(strategy: str, action: str, creature_name: str, amount, details: str = '') -> dict:
    """Record an executed bot event and update in-memory / cache statistics."""
    stats = get_simulation_stats()
    stats['total_events'] += 1
    if strategy in stats['strategies']:
        stats['strategies'][strategy] += 1
    stats['last_event_at'] = timezone.now().isoformat()
    cache.set(STATS_KEY, stats, timeout=None)

    return {
        'timestamp': timezone.now(),
        'strategy': strategy,
        'action': action,
        'creature': creature_name,
        'amount': str(amount),
        'details': details,
    }
