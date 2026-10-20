import functools
import logging
import uuid

from django.core.cache import cache

logger = logging.getLogger(__name__)

LUA_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _release_redis_lock(cache_backend, key, token):
    """
    Attempt atomic lock release using Lua script if Redis backend is active.
    Returns True if atomic release was attempted, False if backend is not Redis.
    """
    raw_cache = getattr(cache_backend, "_cache", None)
    if raw_cache is not None and hasattr(raw_cache, "get_client"):
        try:
            redis_key = cache_backend.make_and_validate_key(key)
            client = raw_cache.get_client(redis_key, write=True)
            serializer = getattr(raw_cache, "_serializer", None)
            raw_token = serializer.dumps(token) if serializer is not None else token
            client.eval(LUA_RELEASE_LOCK_SCRIPT, 1, redis_key, raw_token)
            return True
        except Exception:
            logger.warning(
                "Failed to execute atomic Redis lock release for %s",
                key,
                exc_info=True,
            )
            return False
    return False


class TaskLock:
    """
    Context manager providing a non-blocking distributed lock via Django cache.
    Uses atomic cache.add (SET NX EX) with a unique token to prevent releasing
    locks owned by other processes if the local timeout expires.
    """

    def __init__(self, key, timeout=60):
        self.key = key
        self.timeout = timeout
        self.token = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self):
        self.acquired = bool(
            cache.add(self.key, self.token, timeout=self.timeout)
        )
        return self.acquired

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            if not _release_redis_lock(cache, self.key, self.token):
                if cache.get(self.key) == self.token:
                    cache.delete(self.key)


def prevent_task_overlap(timeout=60, lock_key=None):
    """
    Decorator for Celery tasks preventing concurrent overlapping runs.
    If the lock is currently held by another worker or execution,
    the task skips execution and returns a descriptive status message.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = lock_key or f"celery:lock:{func.__module__}.{func.__name__}"
            with TaskLock(key, timeout=timeout) as acquired:
                if not acquired:
                    logger.warning(
                        "Task %s skipped: previous run still in progress.",
                        func.__name__,
                    )
                    return f"Skipped: {func.__name__} is already running."
                return func(*args, **kwargs)

        return wrapper

    return decorator
