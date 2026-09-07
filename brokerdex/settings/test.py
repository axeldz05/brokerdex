from .base import *  # noqa: F403
from .base import env


SETTINGS_ENVIRONMENT = "test"
SECRET_KEY = "configuration-test-only-key-not-for-deployment"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

_default_database_url = env(
    "DATABASE_URL",
    default="postgresql://brokerdex_user@localhost:5432/brokerdex_db",
)
DATABASES = {
    "default": env.db(
        "TEST_DATABASE_URL",
        default=_default_database_url,
    ),
}

CELERY_BROKER_URL = env(
    "REDIS_URL",
    default="redis://localhost:6379/15",
)
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
