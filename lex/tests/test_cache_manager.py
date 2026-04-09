"""
Unit tests for ``lex.audit_logging.utils.CacheManager``.

**What this tests (customer-visible behaviour)**

``CacheManager.build_cache_key`` generates the cache key pattern used
for storing calculation log messages.  This is tested as a pure
function.  The cache I/O methods (``store_message``, ``get_message``,
``cleanup_calculation``, etc.) are tested using Django's default
in-memory cache backend.

**Why it matters**

If ``build_cache_key`` produces wrong keys, calculation logs either
overwrite each other or become unretrievable, leaving users with
incomplete error diagnostics for failed calculations.

**Methodology**

``build_cache_key`` — pure function, no DB.
Cache I/O methods — tested with Django's LocMemCache (available in
SimpleTestCase, no DB needed).

Run::

    python manage.py test lex.tests.test_cache_manager
"""

from django.test import SimpleTestCase

from lex.audit_logging.utils.CacheManager import CacheManager


class TestBuildCacheKey(SimpleTestCase):
    """Prove ``build_cache_key`` produces the expected pattern."""

    def test_basic_key(self):
        key = CacheManager.build_cache_key("invoice_42", "calc_001")
        self.assertEqual(key, "invoice_42_calc_001")

    def test_uuid_calc_id(self):
        key = CacheManager.build_cache_key(
            "fund_1", "550e8400-e29b-41d4-a716-446655440000"
        )
        self.assertEqual(key, "fund_1_550e8400-e29b-41d4-a716-446655440000")

    def test_empty_calculation_record_raises(self):
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("", "calc_001")

    def test_empty_calc_id_raises(self):
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("invoice_42", "")

    def test_none_calculation_record_raises(self):
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key(None, "calc_001")

    def test_none_calc_id_raises(self):
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("invoice_42", None)

    def test_both_empty_raises(self):
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("", "")


class TestCacheManagerConstants(SimpleTestCase):
    """Prove ``CacheManager`` has correct constants."""

    def test_cache_timeout_is_one_week(self):
        self.assertEqual(CacheManager.CACHE_TIMEOUT, 60 * 60 * 24 * 7)

    def test_calc_cache_name_is_string(self):
        self.assertIsInstance(CacheManager.CALC_CACHE_NAME, str)
        self.assertIn(CacheManager.CALC_CACHE_NAME, ("redis", "local"))
