from pathlib import Path

import environ
from celery.schedules import crontab


BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()

AUTH_USER_MODEL = "account.Account"
AUTHENTICATION_BACKENDS = [
    "account.backends.EmailOrUsernameModelBackend",
]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
        ),
    },
}

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "crispy_forms",
    "crispy_bootstrap5",
    "django_better_admin_arrayfield",
    "channels",
]

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

EXTERNAL_APPS = ["banking", "account", "dashboard", "creature", "trading"]
INSTALLED_APPS += EXTERNAL_APPS

MIDDLEWARE = [
    "brokerdex.middleware.RequestIdMiddleware",
    "django_ratelimit.middleware.RatelimitMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

RATELIMIT_VIEW = "brokerdex.views.ratelimited_view"
RATELIMIT_USE_CACHE = "default"


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "brokerdex.observability.RequestIdFilter",
        },
    },
    "formatters": {
        "readable": {
            "format": (
                "%(asctime)s %(levelname)s %(name)s "
                "[request_id=%(request_id)s] %(message)s"
            ),
        },
        "json": {
            "()": "brokerdex.observability.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "readable",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

ROOT_URLCONF = "brokerdex.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "brokerdex.wsgi.application"
ASGI_APPLICATION = "brokerdex.asgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/account/login/"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_IGNORE_RESULT = True
CELERY_RESULT_EXPIRES = 3600
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "socket_timeout": 5.0,
    "socket_connect_timeout": 5.0,
    "retry_on_timeout": True,
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "brokerdex-default-cache",
    }
}

BATTLE_TURN_INTERVAL = 180
DEPOSIT_WEBHOOK_SECRET = ""

CELERY_BEAT_SCHEDULE = {
    "generar-batallas-cada-5-minutos": {
        "task": "creature.tasks.matchmake_random_battle",
        "schedule": crontab(minute="*/5"),
    },
    "recuperar-operaciones-creature-cada-minuto": {
        "task": "creature.tasks.recover_creature_operations",
        "schedule": crontab(minute="*/1"),
    },
    "recuperar-retiros-pendientes-cada-minuto": {
        "task": "banking.tasks.recover_pending_withdrawals",
        "schedule": crontab(minute="*/1"),
    },
    "actualizar-precios-cada-minuto": {
        "task": "trading.tasks.update_creature_prices",
        "schedule": crontab(minute="*/1"),
    },
    "verificar-ordenes-limite-cada-30s": {
        "task": "trading.tasks.check_pending_limit_orders",
        "schedule": 30.0,
    },
    "snapshot-precios-horario": {
        "task": "trading.tasks.record_hourly_price_snapshots",
        "schedule": crontab(minute=0),
    },
    "snapshot-precios-diario": {
        "task": "trading.tasks.record_daily_price_snapshots",
        "schedule": crontab(hour=0, minute=0),
    },
    "snapshot-precios-semanal": {
        "task": "trading.tasks.record_weekly_price_snapshots",
        "schedule": crontab(hour=0, minute=0, day_of_week=1),
    },
    "calcular-indices-diario": {
        "task": "trading.tasks.calculate_market_indices",
        "schedule": crontab(hour=0, minute=30),
    },
}
