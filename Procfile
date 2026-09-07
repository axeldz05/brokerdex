web: uv run gunicorn brokerdex.wsgi:application --bind 0.0.0.0:${PORT:-8000}
worker: DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run celery -A brokerdex worker --loglevel=info
beat: DJANGO_SETTINGS_MODULE=brokerdex.settings.production uv run celery -A brokerdex beat --loglevel=info