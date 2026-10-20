#!/usr/bin/env bash

set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# By default, run the entire stack isolated in Docker Compose
if [[ "${1:-}" != "--local" ]] && command -v docker >/dev/null 2>&1; then
    echo "Starting Brokerdex in Docker Compose (fully isolated)..."
    exec docker compose up "$@"
fi

if [[ "${1:-}" == "--local" ]]; then
    shift
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to start Brokerdex." >&2
    exit 1
fi

# Ensure PostgreSQL is running
ensure_postgres() {
    local max_attempts=15
    local attempt=0

    if command -v pg_isready >/dev/null 2>&1; then
        if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
            return 0
        fi
    fi

    echo "PostgreSQL is not responding. Attempting to start PostgreSQL service..."

    # 1. Try Docker if available
    if command -v docker >/dev/null 2>&1; then
        local container_name
        container_name="$(docker ps -a --filter "name=brokerdex-.*pg" --format '{{.Names}}' | head -n 1)"
        if [[ -z "$container_name" ]]; then
            container_name="$(docker ps -a --filter "name=postgres" --format '{{.Names}}' | head -n 1)"
        fi

        if [[ -n "$container_name" ]]; then
            echo "Starting existing PostgreSQL container: $container_name..."
            docker start "$container_name" >/dev/null 2>&1 || true
        else
            echo "Creating and starting brokerdex-test-pg container..."
            docker create --name brokerdex-test-pg \
                -p 5432:5432 \
                -e POSTGRES_USER=brokerdex_user \
                -e POSTGRES_DB=brokerdex_db \
                -e POSTGRES_HOST_AUTH_METHOD=trust \
                postgres:18-alpine >/dev/null 2>&1 || true
            docker start brokerdex-test-pg >/dev/null 2>&1 || true
        fi
    fi

    # 2. Try system service if docker was not used or still waiting
    if command -v pg_isready >/dev/null 2>&1 && ! pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
        systemctl --user start postgresql 2>/dev/null || true
    fi

    # 3. Wait for PostgreSQL to be ready
    while [[ $attempt -lt $max_attempts ]]; do
        if command -v pg_isready >/dev/null 2>&1; then
            if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
                echo "PostgreSQL is ready."
                return 0
            fi
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
}

# Ensure Redis is running
ensure_redis() {
    local max_attempts=10
    local attempt=0

    if command -v redis-cli >/dev/null 2>&1; then
        if redis-cli ping >/dev/null 2>&1; then
            return 0
        fi
    fi

    echo "Redis is not responding. Attempting to start Redis..."

    # 1. Try native redis-server in background
    if command -v redis-server >/dev/null 2>&1; then
        redis-server --daemonize yes >/dev/null 2>&1 || true
    elif command -v docker >/dev/null 2>&1; then
        local container_name
        container_name="$(docker ps -a --filter "name=brokerdex-.*redis" --format '{{.Names}}' | head -n 1)"
        if [[ -z "$container_name" ]]; then
            container_name="$(docker ps -a --filter "name=redis" --format '{{.Names}}' | head -n 1)"
        fi

        if [[ -n "$container_name" ]]; then
            docker start "$container_name" >/dev/null 2>&1 || true
        else
            docker create --name brokerdex-redis -p 6379:6379 redis:alpine >/dev/null 2>&1 || true
            docker start brokerdex-redis >/dev/null 2>&1 || true
        fi
    fi

    # 2. Wait for Redis to be ready
    while [[ $attempt -lt $max_attempts ]]; do
        if command -v redis-cli >/dev/null 2>&1; then
            if redis-cli ping >/dev/null 2>&1; then
                echo "Redis is ready."
                return 0
            fi
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
}

# Handle Celery processes and prevent duplicates
ensure_celery() {
    if pgrep -f "[c]elery .*[-]A brokerdex" >/dev/null 2>&1; then
        if pgrep -f "honcho start -f Procfile.dev" >/dev/null 2>&1; then
            echo "A Brokerdex development session is already running." >&2
            exit 1
        fi
        echo "Cleaning up orphaned Brokerdex Celery processes..."
        pkill -f "[c]elery .*[-]A brokerdex" >/dev/null 2>&1 || true
        sleep 1
    fi
}

ensure_postgres
ensure_redis
ensure_celery

# Verify PostgreSQL connection, create database if missing, and verify Redis
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
import psycopg
from psycopg import sql

db_settings = settings.DATABASES["default"]
db_name = db_settings.get("NAME", "brokerdex_db")
db_user = db_settings.get("USER", "brokerdex_user")
db_host = db_settings.get("HOST", "localhost") or "localhost"
db_port = db_settings.get("PORT", "5432") or "5432"
db_password = db_settings.get("PASSWORD", "")

try:
    connection.ensure_connection()
except Exception as exc:
    exc_str = str(exc)
    if "does not exist" in exc_str and db_name in exc_str:
        print(f"Database '{db_name}' does not exist. Creating...", file=sys.stderr)
        try:
            maint_conn = psycopg.connect(
                dbname="postgres",
                user=db_user,
                password=db_password,
                host=db_host,
                port=db_port,
                autocommit=True,
            )
            with maint_conn.cursor() as cur:
                cur.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
                )
            maint_conn.close()
            print(f"Database '{db_name}' created successfully.", file=sys.stderr)
            connection.close()
            connection.ensure_connection()
        except Exception as create_exc:
            print(f"Failed to create database '{db_name}': {create_exc}", file=sys.stderr)
            raise SystemExit(1)
    else:
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

# Run Django system checks
uv run python manage.py check

# Apply pending migrations to ensure database schema is ready
echo "Applying database migrations..."
uv run python manage.py migrate --no-input

# Seed basic Pokémon catalog if database is empty
uv run python - <<'PY'
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "brokerdex.settings.local")
django.setup()

from creature.models import Creature

if not Creature.objects.exists():
    from django.core.management import call_command
    print("Database has no creatures. Seeding initial Pokémon catalog...")
    call_command("seed_all")
PY

echo "All services and dependencies are ready. Starting Brokerdex..."
exec uv run honcho start -f Procfile.dev "$@"
