import functools
import logging
import uuid

from django.core.cache import cache

logger = logging.getLogger(__name__)


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
