web: uv run daphne -b 0.0.0.0 -p ${PORT:-8000} brokerdex.asgi:application
worker: DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run celery -A brokerdex worker --loglevel=info
beat: DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run celery -A brokerdex beat --loglevel=info