import json
import logging
import os
from unittest.mock import patch
from uuid import UUID, uuid4

from django.conf import settings
from django.db import OperationalError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from redis.exceptions import ConnectionError as RedisConnectionError

from brokerdex.middleware import RequestIdMiddleware
from brokerdex.observability import JsonFormatter, RequestIdFilter


class HealthEndpointTests(SimpleTestCase):
    def test_liveness_does_not_require_external_dependencies(self):
        response = self.client.get("/health/live/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_liveness_response_includes_request_id(self):
        response = self.client.get("/health/live/")

        request_id = response["X-Request-ID"]
        self.assertEqual(str(UUID(request_id)), request_id)


class ReadinessEndpointTests(TestCase):
    @patch("brokerdex.observability.Redis.from_url")
    def test_readiness_checks_database_and_redis(self, from_url):
        redis_client = from_url.return_value
        redis_client.ping.return_value = True

        response = self.client.get("/health/ready/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "checks": {"database": "ok", "redis": "ok"},
            },
        )
        from_url.assert_called_once_with(
            settings.CELERY_BROKER_URL,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        redis_client.ping.assert_called_once_with()
        redis_client.close.assert_called_once_with()

    @patch("brokerdex.observability.Redis.from_url")
    def test_readiness_returns_503_when_redis_is_unavailable(
        self,
        from_url,
    ):
        redis_client = from_url.return_value
        redis_client.ping.side_effect = RedisConnectionError("offline")
        redis_client.close.side_effect = RedisConnectionError("offline")

        response = self.client.get("/health/ready/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "status": "unavailable",
                "checks": {"database": "ok", "redis": "error"},
            },
        )
        redis_client.close.assert_called_once_with()

    @patch("brokerdex.observability.connection.cursor")
    @patch("brokerdex.observability.Redis.from_url")
    def test_readiness_returns_503_when_database_is_unavailable(
        self,
        from_url,
        cursor,
    ):
        cursor.side_effect = OperationalError("offline")

        response = self.client.get("/health/ready/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "status": "unavailable",
                "checks": {"database": "error", "redis": "ok"},
            },
        )
        from_url.return_value.ping.assert_called_once_with()


class RequestIdMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_preserves_valid_request_id(self):
        request_id = str(uuid4())
        observed_request_ids = []
        middleware = RequestIdMiddleware(
            lambda request: (
                observed_request_ids.append(request.request_id)
                or HttpResponse()
            ),
        )

        response = middleware(
            self.factory.get("/", HTTP_X_REQUEST_ID=request_id),
        )

        self.assertEqual(observed_request_ids, [request_id])
        self.assertEqual(response["X-Request-ID"], request_id)

    def test_replaces_invalid_request_id_with_uuid(self):
        middleware = RequestIdMiddleware(lambda request: HttpResponse())

        response = middleware(
            self.factory.get("/", HTTP_X_REQUEST_ID="not-a-uuid"),
        )

        generated_request_id = response["X-Request-ID"]
        self.assertNotEqual(generated_request_id, "not-a-uuid")
        self.assertEqual(str(UUID(generated_request_id)), generated_request_id)

    def test_generates_request_id_when_header_is_missing(self):
        middleware = RequestIdMiddleware(lambda request: HttpResponse())

        response = middleware(self.factory.get("/"))

        generated_request_id = response["X-Request-ID"]
        self.assertEqual(str(UUID(generated_request_id)), generated_request_id)


class LoggingTests(SimpleTestCase):
    def test_json_formatter_emits_structured_record(self):
        record = logging.makeLogRecord(
            {
                "name": "brokerdex.health",
                "levelname": "INFO",
                "levelno": logging.INFO,
                "msg": "service ready",
                "request_id": str(uuid4()),
            },
        )

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "brokerdex.health")
        self.assertEqual(payload["message"], "service ready")
        self.assertEqual(payload["request_id"], record.request_id)
        self.assertIn("timestamp", payload)

    def test_request_id_filter_uses_request_after_context_is_reset(self):
        request_id = str(uuid4())
        request = self.client.request().wsgi_request
        request.request_id = request_id
        record = logging.makeLogRecord({"request": request})

        RequestIdFilter().filter(record)

        self.assertEqual(record.request_id, request_id)

    def test_base_logging_is_readable(self):
        from brokerdex.settings.base import LOGGING

        self.assertEqual(
            LOGGING["handlers"]["console"]["formatter"],
            "readable",
        )

    def test_production_logging_is_structured(self):
        environment = {
            "DATABASE_URL": (
                "postgresql://test_user:test_password@localhost/test_db"
            ),
            "DJANGO_ALLOWED_HOSTS": "brokerdex.example.com",
            "DJANGO_SECRET_KEY": "test-production-secret-key",
            "DEPOSIT_WEBHOOK_SECRET": "test-production-webhook-secret",
            "REDIS_URL": "redis://localhost:6379/15",
        }

        with patch.dict(os.environ, environment):
            from brokerdex.settings.production import LOGGING

        self.assertEqual(
            LOGGING["handlers"]["console"]["formatter"],
            "json",
        )
