from .base import *  # noqa: F403
from .base import BASE_DIR, env


if env.bool("DJANGO_READ_DOT_ENV_FILE", default=True):
    env.read_env(BASE_DIR / ".env")

SETTINGS_ENVIRONMENT = "local"
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="django-insecure-local-development-only-change-me",
)
DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "[::1]"],
)

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=(
            "postgresql://brokerdex_user@localhost:5432/brokerdex_db"
        ),
    ),
}

CELERY_BROKER_URL = env(
    "REDIS_URL",
    default="redis://localhost:6379/0",
)
CELERY_RESULT_BACKEND = CELERY_BROKER_URL

_default_cache_url = (
    CELERY_BROKER_URL[:-1] + "1"
    if CELERY_BROKER_URL.endswith("/0")
    else "redis://localhost:6379/1"
)
REDIS_CACHE_URL = env("REDIS_CACHE_URL", default=_default_cache_url)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "KEY_PREFIX": "brokerdex_cache",
        "TIMEOUT": 300,
        "OPTIONS": {
            "socket_timeout": 2.0,
            "socket_connect_timeout": 2.0,
            "retry_on_timeout": True,
        },
    }
}

DEPOSIT_WEBHOOK_SECRET = env(
    "DEPOSIT_WEBHOOK_SECRET",
    default="",
)

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [CELERY_BROKER_URL],
        },
    },
}


