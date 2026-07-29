"""
Tests for ``CacheManager`` I/O operations — store, get, cleanup, availability.

**What is tested:**

    * ``store_message()`` — appends to cache, handles backend unavailable, errors
    * ``get_message()`` — retrieves from cache, handles backend unavailable
    * ``cleanup_calculation()`` — specific keys, pattern matching, backend unavailable
    * ``cleanup_specific_key()`` — single key deletion with error handling
    * ``is_cache_available()`` — probes cache connectivity
    * ``_find_calculation_keys()`` — iter_keys, keys fallback, no-pattern fallback

**Why this matters:**

    Calculation log messages are stored in cache during processing and displayed
    to users in the monitoring drawer.  If ``store_message`` silently drops
    messages, users see blank logs for failed calculations.  If ``cleanup``
    fails to gracefully degrade, cache fills up and Celery workers slow down.

**How to run:**

    .. code-block:: bash

        lex test lex.tests.test_cache_manager_io --verbosity=2 --noinput
"""

from unittest.mock import patch, MagicMock

from django.core.cache.backends.base import InvalidCacheBackendError
from django.test import SimpleTestCase
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.DataModels import CacheCleanupResult


def _mock_cache():
    """Create a dict-backed fake cache for testing."""
    store = {}

    class FakeCache:
        def get(self, key, default=None):
            return store.get(key, default)

        def set(self, key, value, timeout=None):
            store[key] = value

        def delete(self, key):
            store.pop(key, None)

    return FakeCache(), store


# ════════════════════════════════════════════════════════════════════════
#  store_message
# ════════════════════════════════════════════════════════════════════════

class TestStoreMessage(SimpleTestCase):
    """Verify message storage, appending, and graceful degradation."""

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_stores_first_message(self, mock_caches):
        cache, store = _mock_cache()
        mock_caches.__getitem__ = MagicMock(return_value=cache)

        result = CacheManager.store_message("key1", "hello")
        self.assertTrue(result)
        self.assertEqual(store["key1"], "hello")

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_appends_to_existing_message(self, mock_caches):
        """Second message is appended with newline separator."""
        cache, store = _mock_cache()
        store["key1"] = "line1"
        mock_caches.__getitem__ = MagicMock(return_value=cache)

        CacheManager.store_message("key1", "line2")
        self.assertEqual(store["key1"], "line1\nline2")

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_backend_unavailable_returns_false(self, mock_caches):
        """InvalidCacheBackendError → returns False, does not raise."""
        mock_caches.__getitem__ = MagicMock(side_effect=InvalidCacheBackendError("no redis"))

        result = CacheManager.store_message("key1", "msg")
        self.assertFalse(result)

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_unexpected_error_returns_false(self, mock_caches):
        """Generic exception → returns False (graceful degradation)."""
        bad_cache = MagicMock()
        bad_cache.get.side_effect = RuntimeError("boom")
        mock_caches.__getitem__ = MagicMock(return_value=bad_cache)

        result = CacheManager.store_message("key1", "msg")
        self.assertFalse(result)


# ════════════════════════════════════════════════════════════════════════
#  get_message
# ════════════════════════════════════════════════════════════════════════

class TestGetMessage(SimpleTestCase):
    """Verify message retrieval and graceful degradation."""

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_retrieves_stored_message(self, mock_caches):
        cache, store = _mock_cache()
        store["key1"] = "cached content"
        mock_caches.__getitem__ = MagicMock(return_value=cache)

        result = CacheManager.get_message("key1")
        self.assertEqual(result, "cached content")

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_missing_key_returns_none(self, mock_caches):
        cache, _ = _mock_cache()
        mock_caches.__getitem__ = MagicMock(return_value=cache)

        result = CacheManager.get_message("nonexistent")
        self.assertIsNone(result)

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_backend_unavailable_returns_none(self, mock_caches):
        mock_caches.__getitem__ = MagicMock(side_effect=InvalidCacheBackendError("no redis"))

        result = CacheManager.get_message("key1")
        self.assertIsNone(result)


# ════════════════════════════════════════════════════════════════════════
#  cleanup_calculation
# ════════════════════════════════════════════════════════════════════════

class TestCleanupCalculation(SimpleTestCase):
    """Verify cache cleanup with specific keys, patterns, and error handling."""

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_specific_keys_deleted(self, mock_caches):
        """When specific_keys provided, deletes exactly those keys."""
        cache, store = _mock_cache()
        store["model_42_calc1"] = "data"
        store["model_43_calc1"] = "data"
        mock_caches.__getitem__ = MagicMock(return_value=cache)

        result = CacheManager.cleanup_calculation(
            calculation_id="calc1",
            specific_keys=["model_42_calc1", "model_43_calc1"],
        )

        self.assertIsInstance(result, CacheCleanupResult)
        self.assertTrue(result.success)
        self.assertEqual(len(result.cleaned_keys), 2)
        self.assertNotIn("model_42_calc1", store)
        self.assertNotIn("model_43_calc1", store)

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_no_args_returns_empty_success(self, mock_caches):
        """Neither calculation_id nor specific_keys → empty success."""
        cache, _ = _mock_cache()
        mock_caches.__getitem__ = MagicMock(return_value=cache)

        result = CacheManager.cleanup_calculation()
        self.assertTrue(result.success)
        self.assertEqual(result.cleaned_keys, [])

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_backend_unavailable_returns_failure(self, mock_caches):
        mock_caches.__getitem__ = MagicMock(side_effect=InvalidCacheBackendError("no redis"))

        result = CacheManager.cleanup_calculation(calculation_id="calc1")
        self.assertFalse(result.success)
        self.assertTrue(any("not available" in e for e in result.errors))

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_delete_error_captured_in_result(self, mock_caches):
        """If deleting a specific key fails, error is captured but others continue."""
        bad_cache = MagicMock()
        bad_cache.delete.side_effect = [None, RuntimeError("disk full")]
        mock_caches.__getitem__ = MagicMock(return_value=bad_cache)

        result = CacheManager.cleanup_calculation(
            calculation_id="calc1",
            specific_keys=["key1", "key2"],
        )

        self.assertFalse(result.success)
        self.assertEqual(len(result.cleaned_keys), 1)
        self.assertEqual(len(result.errors), 1)


# ════════════════════════════════════════════════════════════════════════
#  cleanup_specific_key
# ════════════════════════════════════════════════════════════════════════

class TestCleanupSpecificKey(SimpleTestCase):
    """Verify single-key cleanup."""

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_deletes_key_successfully(self, mock_caches):
        cache, store = _mock_cache()
        store["my_key"] = "data"
        mock_caches.__getitem__ = MagicMock(return_value=cache)

        result = CacheManager.cleanup_specific_key("my_key")
        self.assertTrue(result)
        self.assertNotIn("my_key", store)

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_backend_unavailable_returns_false(self, mock_caches):
        mock_caches.__getitem__ = MagicMock(side_effect=InvalidCacheBackendError("no redis"))

        result = CacheManager.cleanup_specific_key("key1")
        self.assertFalse(result)


# ════════════════════════════════════════════════════════════════════════
#  is_cache_available
# ════════════════════════════════════════════════════════════════════════

class TestIsCacheAvailable(SimpleTestCase):
    """Verify cache availability probe."""

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_available_when_set_delete_succeeds(self, mock_caches):
        cache, _ = _mock_cache()
        mock_caches.__getitem__ = MagicMock(return_value=cache)

        self.assertTrue(CacheManager.is_cache_available())

    @patch("lex.audit_logging.utils.CacheManager.caches")
    def test_unavailable_on_exception(self, mock_caches):
        mock_caches.__getitem__ = MagicMock(side_effect=Exception("connection refused"))

        self.assertFalse(CacheManager.is_cache_available())


# ════════════════════════════════════════════════════════════════════════
#  _find_calculation_keys
# ════════════════════════════════════════════════════════════════════════

class TestFindCalculationKeys(SimpleTestCase):
    """Verify key discovery with iter_keys, keys, and fallback."""

    def test_iter_keys_used_when_available(self):
        """Cache with iter_keys → returns matching keys."""
        cache = MagicMock()
        cache.iter_keys.return_value = ["model_1_calc1", "model_2_calc1"]

        result = CacheManager._find_calculation_keys(cache, "calc1")
        cache.iter_keys.assert_called_once_with("*_calc1")
        self.assertEqual(result, ["model_1_calc1", "model_2_calc1"])

    def test_keys_fallback_when_no_iter_keys(self):
        """Cache without iter_keys but with keys → uses keys method."""
        cache = MagicMock(spec=["keys"])
        cache.keys.return_value = ["key_calc1"]

        result = CacheManager._find_calculation_keys(cache, "calc1")
        cache.keys.assert_called_once_with("*_calc1")
        self.assertEqual(result, ["key_calc1"])

    def test_no_pattern_methods_returns_empty(self):
        """Cache with neither iter_keys nor keys → empty list."""
        cache = MagicMock(spec=[])

        result = CacheManager._find_calculation_keys(cache, "calc1")
        self.assertEqual(result, [])

    def test_exception_returns_empty(self):
        """iter_keys raises → graceful degradation, empty list."""
        cache = MagicMock()
        cache.iter_keys.side_effect = ConnectionError("redis down")

        result = CacheManager._find_calculation_keys(cache, "calc1")
        self.assertEqual(result, [])
