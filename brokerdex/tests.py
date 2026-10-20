import json
import os
from pathlib import Path
import subprocess
import sys
from importlib.util import find_spec

from django.test import SimpleTestCase


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SettingsModulesTests(SimpleTestCase):
    CONFIGURATION_VARIABLES = (
        "DATABASE_URL",
        "DEPOSIT_WEBHOOK_SECRET",
        "DJANGO_ALLOWED_HOSTS",
        "DJANGO_CSRF_COOKIE_SECURE",
        "DJANGO_DEBUG",
        "DJANGO_READ_DOT_ENV_FILE",
        "DJANGO_SECRET_KEY",
        "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
        "DJANGO_SECURE_HSTS_PRELOAD",
        "DJANGO_SECURE_HSTS_SECONDS",
        "DJANGO_SECURE_SSL_REDIRECT",
        "DJANGO_SESSION_COOKIE_SECURE",
        "DJANGO_TRUST_X_FORWARDED_FOR",
        "REDIS_CACHE_URL",
        "REDIS_URL",
        "TEST_DATABASE_URL",
    )
    PRODUCTION_ENVIRONMENT = {
        "DATABASE_URL": (
            "postgresql://test_user:test_password@localhost:5432/test_db"
        ),
        "DJANGO_ALLOWED_HOSTS": "brokerdex.example.com",
        "DJANGO_SECRET_KEY": (
            "configuration-test-only-key-with-more-than-fifty-characters"
        ),
        "DEPOSIT_WEBHOOK_SECRET": "configuration-test-webhook-secret",
        "REDIS_URL": "redis://localhost:6379/15",
    }

    def _environment(self, **overrides):
        environment = os.environ.copy()
        for variable in self.CONFIGURATION_VARIABLES:
            environment.pop(variable, None)
        environment["DJANGO_READ_DOT_ENV_FILE"] = "False"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment.update(overrides)
        return environment

    def _load_settings(self, module, expression, **environment):
        script = (
            "import importlib, json\n"
            f"settings = importlib.import_module({module!r})\n"
            f"print(json.dumps({expression}))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=False,
            env=self._environment(**environment),
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def _media_route_is_enabled(self, module, **environment):
        script = (
            "import json, os\n"
            f"os.environ['DJANGO_SETTINGS_MODULE'] = {module!r}\n"
            "import django\n"
            "django.setup()\n"
            "from django.urls import Resolver404, resolve\n"
            "try:\n"
            "    match = resolve('/media/example.png')\n"
            "except Resolver404:\n"
            "    enabled = False\n"
            "else:\n"
            "    enabled = match.func.__module__ == 'django.views.static'\n"
            "print(json.dumps(enabled))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=False,
            env=self._environment(**environment),
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_default_settings_package_uses_local_configuration(self):
        environment = self._load_settings(
            "brokerdex.settings",
            "settings.SETTINGS_ENVIRONMENT",
        )

        self.assertEqual(environment, "local")

    def test_local_settings_keep_development_defaults(self):
        configuration = self._load_settings(
            "brokerdex.settings.local",
            "{"
            "'debug': settings.DEBUG, "
            "'database_engine': settings.DATABASES['default']['ENGINE'], "
            "'database_name': settings.DATABASES['default']['NAME'], "
            "'redis': settings.CELERY_BROKER_URL"
            "}",
        )

        self.assertEqual(
            configuration,
            {
                "debug": True,
                "database_engine": "django.db.backends.postgresql",
                "database_name": "brokerdex_db",
                "redis": "redis://localhost:6379/0",
            },
        )

    def test_local_settings_parse_environment_overrides(self):
        configuration = self._load_settings(
            "brokerdex.settings.local",
            "{"
            "'debug': settings.DEBUG, "
            "'database_name': settings.DATABASES['default']['NAME'], "
            "'hosts': settings.ALLOWED_HOSTS, "
            "'redis': settings.CELERY_RESULT_BACKEND"
            "}",
            DATABASE_URL=(
                "postgresql://external_user:test_password@localhost/"
                "external_db"
            ),
            DJANGO_ALLOWED_HOSTS="local.example.com,127.0.0.1",
            DJANGO_DEBUG="False",
            DJANGO_SECRET_KEY="local-configuration-test-key",
            REDIS_URL="redis://localhost:6379/14",
        )

        self.assertEqual(
            configuration,
            {
                "debug": False,
                "database_name": "external_db",
                "hosts": ["local.example.com", "127.0.0.1"],
                "redis": "redis://localhost:6379/14",
            },
        )

    def test_local_settings_redis_isolation_and_timeouts(self):
        configuration = self._load_settings(
            "brokerdex.settings.local",
            "{"
            "'broker_url': settings.CELERY_BROKER_URL, "
            "'cache_url': settings.REDIS_CACHE_URL, "
            "'cache_location': settings.CACHES['default']['LOCATION'], "
            "'cache_prefix': settings.CACHES['default']['KEY_PREFIX'], "
            "'cache_options': settings.CACHES['default'].get('OPTIONS', {})"
            "}",
        )

        self.assertEqual(
            configuration,
            {
                "broker_url": "redis://localhost:6379/0",
                "cache_url": "redis://localhost:6379/1",
                "cache_location": "redis://localhost:6379/1",
                "cache_prefix": "brokerdex_cache",
                "cache_options": {
                    "socket_timeout": 2.0,
                    "socket_connect_timeout": 2.0,
                    "retry_on_timeout": True,
                },
            },
        )

    def test_production_settings_redis_isolation_and_timeouts(self):
        config_default = self._load_settings(
            "brokerdex.settings.production",
            "{"
            "'broker_url': settings.CELERY_BROKER_URL, "
            "'cache_url': settings.REDIS_CACHE_URL, "
            "'cache_location': settings.CACHES['default']['LOCATION'], "
            "'cache_prefix': settings.CACHES['default']['KEY_PREFIX'], "
            "'cache_options': settings.CACHES['default'].get('OPTIONS', {})"
            "}",
            **{**self.PRODUCTION_ENVIRONMENT, "REDIS_URL": "redis://prod-redis:6379/0"},
        )
        self.assertEqual(
            config_default,
            {
                "broker_url": "redis://prod-redis:6379/0",
                "cache_url": "redis://prod-redis:6379/1",
                "cache_location": "redis://prod-redis:6379/1",
                "cache_prefix": "brokerdex_cache",
                "cache_options": {
                    "socket_timeout": 2.0,
                    "socket_connect_timeout": 2.0,
                    "retry_on_timeout": True,
                },
            },
        )

        config_explicit = self._load_settings(
            "brokerdex.settings.production",
            "{"
            "'broker_url': settings.CELERY_BROKER_URL, "
            "'cache_url': settings.REDIS_CACHE_URL, "
            "'cache_location': settings.CACHES['default']['LOCATION']"
            "}",
            **{
                **self.PRODUCTION_ENVIRONMENT,
                "REDIS_CACHE_URL": "redis://cache-redis:6379/2",
            },
        )
        self.assertEqual(
            config_explicit,
            {
                "broker_url": "redis://localhost:6379/15",
                "cache_url": "redis://cache-redis:6379/2",
                "cache_location": "redis://cache-redis:6379/2",
            },
        )

    def test_base_settings_celery_transport_and_results(self):
        configuration = self._load_settings(
            "brokerdex.settings.base",
            "{"
            "'ignore_result': settings.CELERY_TASK_IGNORE_RESULT, "
            "'result_expires': settings.CELERY_RESULT_EXPIRES, "
            "'transport_options': settings.CELERY_BROKER_TRANSPORT_OPTIONS"
            "}",
        )

        self.assertEqual(
            configuration,
            {
                "ignore_result": True,
                "result_expires": 3600,
                "transport_options": {
                    "socket_timeout": 5.0,
                    "socket_connect_timeout": 5.0,
                    "retry_on_timeout": True,
                },
            },
        )

    def test_base_settings_ratelimit_defaults(self):
        configuration = self._load_settings(
            "brokerdex.settings.base",
            "{"
            "'ratelimit_view': settings.RATELIMIT_VIEW, "
            "'ratelimit_use_cache': settings.RATELIMIT_USE_CACHE, "
            "'ratelimit_ip_meta_key': settings.RATELIMIT_IP_META_KEY"
            "}",
        )

        self.assertEqual(
            configuration,
            {
                "ratelimit_view": "brokerdex.views.ratelimited_view",
                "ratelimit_use_cache": "default",
                "ratelimit_ip_meta_key": "REMOTE_ADDR",
            },
        )

    def test_production_settings_trusted_proxy_ratelimit(self):
        default_meta = self._load_settings(
            "brokerdex.settings.production",
            "settings.RATELIMIT_IP_META_KEY",
            **self.PRODUCTION_ENVIRONMENT,
        )
        self.assertEqual(default_meta, "REMOTE_ADDR")

        forwarded_meta = self._load_settings(
            "brokerdex.settings.production",
            "settings.RATELIMIT_IP_META_KEY",
            **{
                **self.PRODUCTION_ENVIRONMENT,
                "DJANGO_TRUST_X_FORWARDED_FOR": "True",
            },
        )
        self.assertEqual(forwarded_meta, "HTTP_X_FORWARDED_FOR")

    def test_base_uses_django_52_storage_configuration(self):
        configuration = self._load_settings(
            "brokerdex.settings.base",
            "{"
            "'static_root': str(settings.STATIC_ROOT), "
            "'default_storage': settings.STORAGES['default']['BACKEND'], "
            "'static_storage': settings.STORAGES['staticfiles']['BACKEND']"
            "}",
        )

        self.assertEqual(
            configuration,
            {
                "static_root": str(PROJECT_ROOT / "staticfiles"),
                "default_storage": (
                    "django.core.files.storage.FileSystemStorage"
                ),
                "static_storage": (
                    "django.contrib.staticfiles.storage.StaticFilesStorage"
                ),
            },
        )

    def test_local_does_not_enable_whitenoise(self):
        configuration = self._load_settings(
            "brokerdex.settings.local",
            "{"
            "'middleware': settings.MIDDLEWARE, "
            "'static_storage': "
            "settings.STORAGES['staticfiles']['BACKEND']"
            "}",
        )

        self.assertNotIn(
            "whitenoise.middleware.WhiteNoiseMiddleware",
            configuration["middleware"],
        )
        self.assertEqual(
            configuration["static_storage"],
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )

    def test_test_settings_use_postgresql(self):
        configuration = self._load_settings(
            "brokerdex.settings.test",
            "{"
            "'debug': settings.DEBUG, "
            "'database_engine': settings.DATABASES['default']['ENGINE'], "
            "'database_name': settings.DATABASES['default']['NAME']"
            "}",
            TEST_DATABASE_URL=(
                "postgresql://test_user:test_password@localhost/"
                "brokerdex_settings_test"
            ),
        )

        self.assertEqual(
            configuration,
            {
                "debug": False,
                "database_engine": "django.db.backends.postgresql",
                "database_name": "brokerdex_settings_test",
            },
        )

    def test_production_requires_critical_environment_variables(self):
        script = "import brokerdex.settings.production"

        for missing_variable in self.PRODUCTION_ENVIRONMENT:
            with self.subTest(missing_variable=missing_variable):
                environment = self.PRODUCTION_ENVIRONMENT.copy()
                environment.pop(missing_variable)
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    capture_output=True,
                    check=False,
                    env=self._environment(**environment),
                    text=True,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(missing_variable, result.stderr)

    def test_production_enables_deployment_security(self):
        configuration = self._load_settings(
            "brokerdex.settings.production",
            "{"
            "'debug': settings.DEBUG, "
            "'hosts': settings.ALLOWED_HOSTS, "
            "'csrf_cookie': settings.CSRF_COOKIE_SECURE, "
            "'session_cookie': settings.SESSION_COOKIE_SECURE, "
            "'ssl_redirect': settings.SECURE_SSL_REDIRECT, "
            "'hsts_seconds': settings.SECURE_HSTS_SECONDS, "
            "'hsts_subdomains': settings.SECURE_HSTS_INCLUDE_SUBDOMAINS, "
            "'hsts_preload': settings.SECURE_HSTS_PRELOAD"
            "}",
            **self.PRODUCTION_ENVIRONMENT,
        )

        self.assertEqual(
            configuration,
            {
                "debug": False,
                "hosts": ["brokerdex.example.com"],
                "csrf_cookie": True,
                "session_cookie": True,
                "ssl_redirect": True,
                "hsts_seconds": 31536000,
                "hsts_subdomains": True,
                "hsts_preload": True,
            },
        )

    def test_production_serves_static_files_with_whitenoise(self):
        configuration = self._load_settings(
            "brokerdex.settings.production",
            "{"
            "'middleware': settings.MIDDLEWARE, "
            "'static_storage': "
            "settings.STORAGES['staticfiles']['BACKEND']"
            "}",
            **self.PRODUCTION_ENVIRONMENT,
        )

        security_index = configuration["middleware"].index(
            "django.middleware.security.SecurityMiddleware"
        )
        self.assertEqual(
            configuration["middleware"][security_index + 1],
            "whitenoise.middleware.WhiteNoiseMiddleware",
        )
        self.assertEqual(
            configuration["static_storage"],
            (
                "whitenoise.storage."
                "CompressedManifestStaticFilesStorage"
            ),
        )

    def test_media_helper_is_only_enabled_in_debug(self):
        self.assertTrue(
            self._media_route_is_enabled("brokerdex.settings.local")
        )
        self.assertFalse(
            self._media_route_is_enabled(
                "brokerdex.settings.local",
                DJANGO_DEBUG="False",
            )
        )
        self.assertFalse(
            self._media_route_is_enabled(
                "brokerdex.settings.production",
                **self.PRODUCTION_ENVIRONMENT,
            )
        )

    def test_production_deployment_checks_are_clean(self):
        script = (
            "import json, os\n"
            "os.environ['DJANGO_SETTINGS_MODULE'] = "
            "'brokerdex.settings.production'\n"
            "import django\n"
            "django.setup()\n"
            "from django.core.checks import run_checks\n"
            "issues = run_checks(include_deployment_checks=True)\n"
            "print(json.dumps([f'{issue.id}: {issue.msg}' "
            "for issue in issues]))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=False,
            env=self._environment(**self.PRODUCTION_ENVIRONMENT),
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])


class RuntimeConfigurationTests(SimpleTestCase):
    def _lines(self, filename):
        return (PROJECT_ROOT / filename).read_text().splitlines()

    def test_production_procfile_uses_uv_managed_processes(self):
        processes = self._lines("Procfile")

        self.assertEqual(
            processes,
            [
                (
                    "web: uv run daphne -b 0.0.0.0 -p ${PORT:-8000} "
                    "brokerdex.asgi:application"
                ),
                (
                    "worker: DJANGO_SETTINGS_MODULE="
                    "brokerdex.settings.production uv run celery "
                    "-A brokerdex worker --loglevel=info"
                ),
                (
                    "beat: DJANGO_SETTINGS_MODULE="
                    "brokerdex.settings.production uv run celery "
                    "-A brokerdex beat --loglevel=info"
                ),
            ],
        )
        self.assertEqual(
            sum(line.startswith("beat:") for line in processes),
            1,
        )

    def test_development_procfile_uses_uv_managed_processes(self):
        self.assertEqual(
            self._lines("Procfile.dev"),
            [
                "web: uv run python manage.py runserver",
                (
                    "worker: uv run celery -A brokerdex worker "
                    "--loglevel=info"
                ),
                (
                    "beat: uv run celery -A brokerdex beat "
                    "--loglevel=info"
                ),
            ],
        )

    def test_run_script_checks_dependencies_and_delegates_to_honcho(self):
        script = (PROJECT_ROOT / "run.sh").read_text()

        self.assertIn("uv run python manage.py check", script)
        self.assertIn("connection.ensure_connection()", script)
        self.assertIn("Redis.from_url", script)
        self.assertIn("pgrep", script)
        self.assertIn(
            "exec uv run honcho start -f Procfile.dev",
            script,
        )
        self.assertNotIn("docker run", script)
        self.assertNotIn("manage.py runserver", script)

    def test_production_runtime_dependencies_are_installed(self):
        self.assertIsNotNone(find_spec("daphne"))
        self.assertIsNotNone(find_spec("gunicorn"))
        self.assertIsNotNone(find_spec("whitenoise"))
