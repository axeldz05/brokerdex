from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from brokerdex.locks import TaskLock, prevent_task_overlap


class TaskLockUnitTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_task_lock_acquires_and_releases(self):
        lock_key = "test:lock:basic"
        self.assertIsNone(cache.get(lock_key))

        with TaskLock(lock_key, timeout=30) as acquired:
            self.assertTrue(acquired)
            self.assertIsNotNone(cache.get(lock_key))

        self.assertIsNone(cache.get(lock_key))

    def test_task_lock_rejects_concurrent_acquisition(self):
        lock_key = "test:lock:concurrent"

        with TaskLock(lock_key, timeout=30) as first_acquired:
            self.assertTrue(first_acquired)

            with TaskLock(lock_key, timeout=30) as second_acquired:
                self.assertFalse(second_acquired)

        # After the first lock exits, the key is freed
        self.assertIsNone(cache.get(lock_key))

    def test_task_lock_does_not_release_foreign_token(self):
        lock_key = "test:lock:token_expiry"

        lock = TaskLock(lock_key, timeout=30)
        lock.__enter__()
        self.assertTrue(lock.acquired)

        # Simulate lock expired and another worker grabbed it with a different token
        cache.set(lock_key, "different-token-from-another-worker", timeout=30)

        lock.__exit__(None, None, None)

        # Key should NOT have been deleted by the first lock
        self.assertEqual(
            cache.get(lock_key),
            "different-token-from-another-worker",
        )

    def test_task_lock_uses_lua_script_when_redis_available(self):
        from brokerdex.locks import LUA_RELEASE_LOCK_SCRIPT, _release_redis_lock

        mock_cache = MagicMock()
        mock_raw_cache = MagicMock()
        mock_client = MagicMock()
        mock_serializer = MagicMock()

        mock_cache._cache = mock_raw_cache
        mock_cache.make_and_validate_key.return_value = "brokerdex:1:test_key"
        mock_raw_cache.get_client.return_value = mock_client
        mock_raw_cache._serializer = mock_serializer
        mock_serializer.dumps.return_value = b"serialized:my-token"

        result = _release_redis_lock(mock_cache, "test_key", "my-token")

        self.assertTrue(result)
        mock_raw_cache.get_client.assert_called_once_with(
            "brokerdex:1:test_key",
            write=True,
        )
        mock_client.eval.assert_called_once_with(
            LUA_RELEASE_LOCK_SCRIPT,
            1,
            "brokerdex:1:test_key",
            b"serialized:my-token",
        )

    def test_task_lock_redis_fallback_on_exception(self):
        from brokerdex.locks import _release_redis_lock

        mock_cache = MagicMock()
        mock_raw_cache = MagicMock()
        mock_cache._cache = mock_raw_cache
        mock_cache.make_and_validate_key.return_value = "brokerdex:1:test_key"
        mock_raw_cache.get_client.side_effect = RuntimeError("Redis error")

        result = _release_redis_lock(mock_cache, "test_key", "my-token")
        self.assertFalse(result)

    def test_release_redis_lock_returns_false_for_non_redis_cache(self):
        from brokerdex.locks import _release_redis_lock

        # LocMemCache is active in tests, has no get_client on _cache
        self.assertFalse(_release_redis_lock(cache, "test_key", "token"))

    def test_task_lock_exit_invokes_release_redis_lock(self):
        with patch("brokerdex.locks._release_redis_lock") as mock_release:
            mock_release.return_value = True
            with TaskLock("test:lock:invoke", timeout=30) as acquired:
                self.assertTrue(acquired)

            mock_release.assert_called_once()


class PreventTaskOverlapDecoratorTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_decorator_runs_function_and_releases_lock(self):
        executed = []

        @prevent_task_overlap(timeout=30)
        def sample_task():
            executed.append(True)
            return "done"

        result = sample_task()
        self.assertEqual(result, "done")
        self.assertEqual(executed, [True])

        # Lock is released after completion
        self.assertIsNone(
            cache.get("celery:lock:brokerdex.tests_locks.sample_task")
        )

    def test_decorator_skips_when_lock_is_active(self):
        call_count = 0

        @prevent_task_overlap(timeout=30)
        def heavy_task():
            nonlocal call_count
            call_count += 1
            return "finished"

        # Simulate task currently running by manually acquiring the lock
        lock_key = "celery:lock:brokerdex.tests_locks.heavy_task"
        cache.set(lock_key, "active-run", timeout=30)

        with self.assertLogs("brokerdex.locks", level="WARNING") as logs:
            result = heavy_task()

        self.assertEqual(result, "Skipped: heavy_task is already running.")
        self.assertEqual(call_count, 0)
        self.assertTrue(
            any("heavy_task skipped" in message for message in logs.output)
        )

    def test_decorator_releases_lock_on_exception(self):
        @prevent_task_overlap(timeout=30)
        def failing_task():
            raise RuntimeError("Task crashed")

        with self.assertRaises(RuntimeError):
            failing_task()

        self.assertIsNone(
            cache.get("celery:lock:brokerdex.tests_locks.failing_task")
        )

    def test_decorator_custom_lock_key(self):
        @prevent_task_overlap(timeout=30, lock_key="custom:my_task_lock")
        def custom_task():
            return "ok"

        custom_task()
        # Verify custom key was used and released
        self.assertIsNone(cache.get("custom:my_task_lock"))
