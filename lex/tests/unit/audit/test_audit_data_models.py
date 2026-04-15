"""
Unit tests for ``lex.audit_logging.utils.DataModels``.

**What this tests (customer-visible behaviour)**

``CacheCleanupResult`` and the exception hierarchy
(``CalculationLogError``, ``CacheOperationError``,
``ContextResolutionError``) are the structured types the audit logging
system uses for error handling and cleanup reporting.

**Why it matters**

If ``CacheCleanupResult`` doesn't coerce ``None`` into empty lists,
downstream code accessing ``.cleaned_keys`` or ``.errors`` will crash
with ``TypeError``.  If exceptions don't carry their metadata, error
diagnostics become useless.

**Methodology**

Pure dataclass / exception construction — no DB.

Run::

    python manage.py test lex.tests.test_audit_data_models
"""

from django.test import SimpleTestCase

from lex.audit_logging.utils.DataModels import (
    CacheCleanupResult,
    CalculationLogError,
    CacheOperationError,
    ContextResolutionError,
)


class TestCacheCleanupResult(SimpleTestCase):
    """Prove ``CacheCleanupResult`` dataclass behaves correctly."""

    def test_basic_construction(self):
        result = CacheCleanupResult(
            success=True, cleaned_keys=["key1"], errors=[]
        )
        self.assertTrue(result.success)
        self.assertEqual(result.cleaned_keys, ["key1"])
        self.assertEqual(result.errors, [])

    def test_none_cleaned_keys_becomes_empty_list(self):
        result = CacheCleanupResult(success=True, cleaned_keys=None, errors=[])
        self.assertEqual(result.cleaned_keys, [])

    def test_none_errors_becomes_empty_list(self):
        result = CacheCleanupResult(
            success=False, cleaned_keys=[], errors=None
        )
        self.assertEqual(result.errors, [])

    def test_both_none_become_empty_lists(self):
        result = CacheCleanupResult(success=True, cleaned_keys=None, errors=None)
        self.assertEqual(result.cleaned_keys, [])
        self.assertEqual(result.errors, [])

    def test_failure_with_errors(self):
        result = CacheCleanupResult(
            success=False,
            cleaned_keys=["k1"],
            errors=["Failed to delete k2"],
        )
        self.assertFalse(result.success)
        self.assertEqual(len(result.errors), 1)


class TestCalculationLogError(SimpleTestCase):
    """Prove ``CalculationLogError`` base exception stores metadata."""

    def test_message(self):
        exc = CalculationLogError("something went wrong")
        self.assertEqual(str(exc), "something went wrong")

    def test_calculation_id(self):
        exc = CalculationLogError("err", calculation_id="calc_123")
        self.assertEqual(exc.calculation_id, "calc_123")

    def test_default_calculation_id_is_none(self):
        exc = CalculationLogError("err")
        self.assertIsNone(exc.calculation_id)

    def test_is_exception(self):
        self.assertTrue(issubclass(CalculationLogError, Exception))


class TestCacheOperationError(SimpleTestCase):
    """Prove ``CacheOperationError`` extends base with cache_key."""

    def test_message_and_cache_key(self):
        exc = CacheOperationError(
            "Redis timeout", calculation_id="c1", cache_key="my_key"
        )
        self.assertEqual(str(exc), "Redis timeout")
        self.assertEqual(exc.cache_key, "my_key")
        self.assertEqual(exc.calculation_id, "c1")

    def test_default_cache_key_is_none(self):
        exc = CacheOperationError("err")
        self.assertIsNone(exc.cache_key)

    def test_is_calculation_log_error(self):
        self.assertTrue(issubclass(CacheOperationError, CalculationLogError))


class TestContextResolutionError(SimpleTestCase):
    """Prove ``ContextResolutionError`` extends base with stack info."""

    def test_message_and_stack_length(self):
        exc = ContextResolutionError(
            "missing context", calculation_id="c2", stack_length=3
        )
        self.assertEqual(str(exc), "missing context")
        self.assertEqual(exc.stack_length, 3)
        self.assertEqual(exc.calculation_id, "c2")

    def test_default_stack_length_is_none(self):
        exc = ContextResolutionError("err")
        self.assertIsNone(exc.stack_length)

    def test_is_calculation_log_error(self):
        self.assertTrue(
            issubclass(ContextResolutionError, CalculationLogError)
        )
