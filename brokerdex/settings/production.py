from copy import deepcopy

from .base import *  # noqa: F403
from .base import (
    LOGGING as BASE_LOGGING,
    MIDDLEWARE as BASE_MIDDLEWARE,
    STORAGES as BASE_STORAGES,
    env,
)


SETTINGS_ENVIRONMENT = "production"
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

DATABASES = {
    "default": env.db("DATABASE_URL"),
}

CELERY_BROKER_URL = env("REDIS_URL")
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

DEPOSIT_WEBHOOK_SECRET = env("DEPOSIT_WEBHOOK_SECRET")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [CELERY_BROKER_URL],
        },
    },
}

LOGGING = deepcopy(BASE_LOGGING)
LOGGING["handlers"]["console"]["formatter"] = "json"

MIDDLEWARE = list(BASE_MIDDLEWARE)
_security_middleware_index = MIDDLEWARE.index(
    "django.middleware.security.SecurityMiddleware"
)
MIDDLEWARE.insert(
    _security_middleware_index + 1,
    "whitenoise.middleware.WhiteNoiseMiddleware",
)

STORAGES = deepcopy(BASE_STORAGES)
STORAGES["staticfiles"]["BACKEND"] = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
)

CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=[],
)
CSRF_COOKIE_SECURE = env.bool(
    "DJANGO_CSRF_COOKIE_SECURE",
    default=True,
)
SESSION_COOKIE_SECURE = env.bool(
    "DJANGO_SESSION_COOKIE_SECURE",
    default=True,
)
SECURE_SSL_REDIRECT = env.bool(
    "DJANGO_SECURE_SSL_REDIRECT",
    default=True,
)
SECURE_HSTS_SECONDS = env.int(
    "DJANGO_SECURE_HSTS_SECONDS",
    default=31536000,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
    default=True,
)
SECURE_HSTS_PRELOAD = env.bool(
    "DJANGO_SECURE_HSTS_PRELOAD",
    default=True,
)
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if env.bool(
        "DJANGO_TRUST_X_FORWARDED_PROTO",
        default=False,
    )
    else None
)
