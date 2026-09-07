#!/usr/bin/env bash

set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to start Brokerdex." >&2
    exit 1
fi

if pgrep -f "[c]elery .*[-]A brokerdex .*beat" >/dev/null; then
    echo "A Brokerdex Celery beat process is already running." >&2
    exit 1
fi

uv run python manage.py check
uv run python - <<'PY'
import os
import sys

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "brokerdex.settings.local",
)

import django

django.setup()

from django.conf import settings
from django.db import connection
from redis import Redis

try:
    connection.ensure_connection()
except Exception as exc:
    print(f"PostgreSQL is unavailable: {exc}", file=sys.stderr)
    raise SystemExit(1)
finally:
    connection.close()

redis_client = Redis.from_url(settings.CELERY_BROKER_URL)
try:
    redis_client.ping()
except Exception as exc:
    print(f"Redis is unavailable: {exc}", file=sys.stderr)
    raise SystemExit(1)
finally:
    redis_client.close()
PY

exec uv run honcho start -f Procfile.dev "$@"
