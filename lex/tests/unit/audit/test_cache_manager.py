"""
Unit tests for ``CacheManager`` — the cache layer for calculation log messages.

**What this tests (customer-visible behaviour)**

``CacheManager`` stores log messages in Django's cache framework so they
can be streamed to the frontend in real time.  If ``store_message``
silently fails, the user loses live calculation logs.  If
``cleanup_calculation`` fails, stale cache entries accumulate until
the TTL expires (1 week).

**Methodology**

Uses Django's ``LocMemCache`` (the ``local`` backend selected when
neither ``DEPLOYMENT_ENVIRONMENT`` nor ``CELERY_ACTIVE`` is set).
The ``CACHES`` setting in ``django_test_settings.py`` defines
``'local'`` as ``LocMemCache``, which is process-local — no Redis
needed.

Run::

    lex test lex.audit_logging.tests.test_cache_manager --verbosity=2 --noinput --keepdb
"""

from unittest.mock import patch, MagicMock

from django.core.cache import caches
from django.test import SimpleTestCase, override_settings
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.DataModels import CacheCleanupResult

TEST_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "local": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "test-cache-manager"},
}


@override_settings(CACHES=TEST_CACHES)
class TestBuildCacheKey(SimpleTestCase):
    """Prove ``build_cache_key`` produces deterministic keys."""

    def test_standard_key(self):
        """Key is ``{record}_{calc_id}``."""
        key = CacheManager.build_cache_key("nav_42", "calc-abc")
        self.assertEqual(key, "nav_42_calc-abc")

    def test_raises_on_empty_record(self):
        """Empty ``calculation_record`` raises ``ValueError``."""
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("", "calc-abc")

    def test_raises_on_empty_calc_id(self):
        """Empty ``calc_id`` raises ``ValueError``."""
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("nav_42", "")


@override_settings(CACHES=TEST_CACHES)
class TestStoreMessage(SimpleTestCase):
    """Prove ``store_message`` appends messages under the cache key."""

    def setUp(self):
        # Force CacheManager to use the test-local cache
        self._orig_cache_name = CacheManager.CALC_CACHE_NAME
        CacheManager.CALC_CACHE_NAME = "local"
        self.addCleanup(setattr, CacheManager, "CALC_CACHE_NAME", self._orig_cache_name)
        # Clear the test cache
        caches["local"].clear()
        self.addCleanup(caches["local"].clear)

    def test_stores_single_message(self):
        """First message is stored verbatim."""
        result = CacheManager.store_message("key1", "hello")
        self.assertTrue(result)
        self.assertEqual(CacheManager.get_message("key1"), "hello")

    def test_appends_to_existing(self):
        """Subsequent messages are newline-separated."""
        CacheManager.store_message("key2", "line1")
        CacheManager.store_message("key2", "line2")
        cached = CacheManager.get_message("key2")
        self.assertIn("line1", cached)
        self.assertIn("line2", cached)
        self.assertIn("\n", cached)

    def test_returns_false_on_invalid_backend(self):
        """Returns False when the cache backend is unavailable."""
        CacheManager.CALC_CACHE_NAME = "nonexistent_backend"
        result = CacheManager.store_message("key3", "msg")
        self.assertFalse(result)

    def test_returns_false_on_exception(self):
        """Returns False on unexpected cache errors."""
        with patch.object(caches["local"], "get", side_effect=RuntimeError("boom")):
            result = CacheManager.store_message("key4", "msg")
            self.assertFalse(result)


@override_settings(CACHES=TEST_CACHES)
class TestGetMessage(SimpleTestCase):
    """Prove ``get_message`` retrieves cached log content."""

    def setUp(self):
        self._orig_cache_name = CacheManager.CALC_CACHE_NAME
        CacheManager.CALC_CACHE_NAME = "local"
        self.addCleanup(setattr, CacheManager, "CALC_CACHE_NAME", self._orig_cache_name)
        caches["local"].clear()
        self.addCleanup(caches["local"].clear)

    def test_returns_cached_value(self):
        """Returns the stored message."""
        caches["local"].set("gm_key", "cached content")
        self.assertEqual(CacheManager.get_message("gm_key"), "cached content")

    def test_returns_none_for_missing_key(self):
        """Returns None when key does not exist."""
        self.assertIsNone(CacheManager.get_message("no_such_key"))

    def test_returns_none_on_invalid_backend(self):
        """Returns None when cache backend is unavailable."""
        CacheManager.CALC_CACHE_NAME = "nonexistent_backend"
        self.assertIsNone(CacheManager.get_message("any_key"))

    def test_returns_none_on_exception(self):
        """Returns None on unexpected cache errors."""
        with patch.object(caches["local"], "get", side_effect=RuntimeError("broken")):
            self.assertIsNone(CacheManager.get_message("err_key"))


@override_settings(CACHES=TEST_CACHES)
class TestCleanupCalculation(SimpleTestCase):
    """Prove ``cleanup_calculation`` removes cache entries by key."""

    def setUp(self):
        self._orig_cache_name = CacheManager.CALC_CACHE_NAME
        CacheManager.CALC_CACHE_NAME = "local"
        self.addCleanup(setattr, CacheManager, "CALC_CACHE_NAME", self._orig_cache_name)
        caches["local"].clear()
        self.addCleanup(caches["local"].clear)

    def test_cleans_specific_keys(self):
        """specific_keys are deleted and reported in cleaned_keys."""
        caches["local"].set("nav_1_calc-x", "log data")
        caches["local"].set("nav_2_calc-x", "more data")

        result = CacheManager.cleanup_calculation(
            calculation_id="calc-x",
            specific_keys=["nav_1_calc-x", "nav_2_calc-x"],
        )
        self.assertIsInstance(result, CacheCleanupResult)
        self.assertTrue(result.success)
        self.assertEqual(len(result.cleaned_keys), 2)
        self.assertIsNone(caches["local"].get("nav_1_calc-x"))
        self.assertIsNone(caches["local"].get("nav_2_calc-x"))

    def test_no_args_returns_empty_success(self):
        """No calculation_id and no specific_keys → empty success result."""
        result = CacheManager.cleanup_calculation()
        self.assertTrue(result.success)
        self.assertEqual(result.cleaned_keys, [])

    def test_invalid_backend_returns_failure(self):
        """Unavailable cache backend → failure result."""
        CacheManager.CALC_CACHE_NAME = "nonexistent_backend"
        result = CacheManager.cleanup_calculation(calculation_id="calc-y")
        self.assertFalse(result.success)
        self.assertTrue(len(result.errors) > 0)

    def test_delete_error_captured(self):
        """If a single key deletion fails, it's recorded in errors."""
        caches["local"].set("fail_key", "data")
        with patch.object(caches["local"], "delete", side_effect=RuntimeError("oops")):
            result = CacheManager.cleanup_calculation(
                calculation_id="calc-z",
                specific_keys=["fail_key"],
            )
        self.assertFalse(result.success)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("oops", result.errors[0])

    def test_pattern_matching_with_iter_keys(self):
        """When cache has ``iter_keys``, uses pattern matching to find keys."""
        mock_cache = MagicMock()
        mock_cache.iter_keys.return_value = ["nav_1_calc-p", "nav_2_calc-p"]
        with patch("lex.audit_logging.utils.CacheManager.caches", {"local": mock_cache}):
            CacheManager.CALC_CACHE_NAME = "local"
            result = CacheManager.cleanup_calculation(calculation_id="calc-p")
        self.assertEqual(len(result.cleaned_keys), 2)
        mock_cache.iter_keys.assert_called_once_with("*_calc-p")

    def test_pattern_matching_fallback_to_keys(self):
        """Falls back to ``keys()`` when ``iter_keys`` is unavailable."""
        mock_cache = MagicMock(spec=["keys", "delete", "set", "get"])
        del mock_cache.iter_keys
        mock_cache.keys.return_value = ["nav_3_calc-q"]
        with patch("lex.audit_logging.utils.CacheManager.caches", {"local": mock_cache}):
            CacheManager.CALC_CACHE_NAME = "local"
            result = CacheManager.cleanup_calculation(calculation_id="calc-q")
        self.assertEqual(len(result.cleaned_keys), 1)

    def test_no_pattern_support_returns_empty(self):
        """When neither ``iter_keys`` nor ``keys`` exists, returns []."""
        mock_cache = MagicMock(spec=["delete", "set", "get"])
        with patch("lex.audit_logging.utils.CacheManager.caches", {"local": mock_cache}):
            CacheManager.CALC_CACHE_NAME = "local"
            result = CacheManager.cleanup_calculation(calculation_id="calc-r")
        self.assertTrue(result.success)
        self.assertEqual(result.cleaned_keys, [])


@override_settings(CACHES=TEST_CACHES)
class TestCleanupSpecificKey(SimpleTestCase):
    """Prove ``cleanup_specific_key`` deletes exactly one key."""

    def setUp(self):
        self._orig_cache_name = CacheManager.CALC_CACHE_NAME
        CacheManager.CALC_CACHE_NAME = "local"
        self.addCleanup(setattr, CacheManager, "CALC_CACHE_NAME", self._orig_cache_name)
        caches["local"].clear()
        self.addCleanup(caches["local"].clear)

    def test_deletes_existing_key(self):
        caches["local"].set("del_me", "data")
        result = CacheManager.cleanup_specific_key("del_me")
        self.assertTrue(result)
        self.assertIsNone(caches["local"].get("del_me"))

    def test_nonexistent_key_returns_true(self):
        """Deleting a missing key is idempotent — returns True."""
        result = CacheManager.cleanup_specific_key("no_such_key")
        self.assertTrue(result)

    def test_invalid_backend_returns_false(self):
        CacheManager.CALC_CACHE_NAME = "nonexistent_backend"
        result = CacheManager.cleanup_specific_key("any_key")
        self.assertFalse(result)

    def test_exception_returns_false(self):
        with patch.object(caches["local"], "delete", side_effect=RuntimeError):
            result = CacheManager.cleanup_specific_key("err_key")
            self.assertFalse(result)


@override_settings(CACHES=TEST_CACHES)
class TestIsCacheAvailable(SimpleTestCase):
    """Prove ``is_cache_available`` detects cache health."""

    def setUp(self):
        self._orig_cache_name = CacheManager.CALC_CACHE_NAME
        CacheManager.CALC_CACHE_NAME = "local"
        self.addCleanup(setattr, CacheManager, "CALC_CACHE_NAME", self._orig_cache_name)

    def test_returns_true_when_available(self):
        self.assertTrue(CacheManager.is_cache_available())

    def test_returns_false_on_exception(self):
        with patch.object(caches["local"], "set", side_effect=RuntimeError):
            self.assertFalse(CacheManager.is_cache_available())

    def test_returns_false_for_invalid_backend(self):
        CacheManager.CALC_CACHE_NAME = "nonexistent_backend"
        self.assertFalse(CacheManager.is_cache_available())


# ── Tests merged from lex/tests/test_cache_manager.py ─────────────────


@override_settings(CACHES=TEST_CACHES)
class TestBuildCacheKeyExtended(SimpleTestCase):
    """Additional ``build_cache_key`` edge-case tests."""

    def test_uuid_calc_id(self):
        key = CacheManager.build_cache_key(
            "fund_1", "550e8400-e29b-41d4-a716-446655440000"
        )
        self.assertEqual(key, "fund_1_550e8400-e29b-41d4-a716-446655440000")

    def test_none_calculation_record_raises(self):
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key(None, "calc_001")

    def test_none_calc_id_raises(self):
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("invoice_42", None)

    def test_both_empty_raises(self):
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("", "")


@override_settings(CACHES=TEST_CACHES)
class TestCacheManagerConstants(SimpleTestCase):
    """Prove ``CacheManager`` has correct constants."""

    def test_cache_timeout_is_one_week(self):
        self.assertEqual(CacheManager.CACHE_TIMEOUT, 60 * 60 * 24 * 7)

    def test_calc_cache_name_is_string(self):
        self.assertIsInstance(CacheManager.CALC_CACHE_NAME, str)
        self.assertIn(CacheManager.CALC_CACHE_NAME, ("redis", "local"))
