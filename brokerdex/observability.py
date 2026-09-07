import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

from django.conf import settings
from django.db import DatabaseError, connection
from redis import Redis
from redis.exceptions import RedisError


REDIS_TIMEOUT_SECONDS = 0.5
_request_id = ContextVar("request_id", default="-")


def set_request_id(request_id):
    return _request_id.set(request_id)


def reset_request_id(token):
    _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        request = getattr(record, "request", None)
        record.request_id = getattr(
            request,
            "request_id",
            _request_id.get(),
        )
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": (
                datetime.fromtimestamp(record.created, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def database_is_ready():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return False
    return True


def redis_is_ready():
    client = None
    try:
        client = Redis.from_url(
            settings.CELERY_BROKER_URL,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
        )
        return bool(client.ping())
    except (RedisError, OSError, ValueError):
        return False
    finally:
        if client is not None:
            try:
                client.close()
            except (RedisError, OSError):
                pass


def readiness_status():
    checks = {
        "database": "ok" if database_is_ready() else "error",
        "redis": "ok" if redis_is_ready() else "error",
    }
    status = (
        "ok"
        if all(check == "ok" for check in checks.values())
        else "unavailable"
    )
    return status, checks
