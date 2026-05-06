"""
Cluster 6f: Audit-log resilience — deadlock retries + ContentType cache healing.

Intent (from docs/features/tracking/audit logs.md "Resilience"):

    * Deadlocks and serialization conflicts (PG SQLSTATE ``40P01`` /
      ``40001``) are retried with exponential backoff up to
      ``MAX_UPDATE_RETRIES``.
    * ``safe_get_content_type`` heals stale ContentType cache —
      Django's per-process cache can go stale post-migration; the
      framework auto-corrects without surfacing the error.

Both contracts are explicitly enumerated in docs but unpinned by tests.
Scenario numbering matches docs/test-plan/test-clusters.md § 6f.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from django.contrib.contenttypes.models import ContentType
from django.db.utils import OperationalError
from django.test import SimpleTestCase, TestCase

from lex.audit_logging.mixins.AuditLogMixin import (
    BASE_RETRY_DELAY_SECONDS,
    MAX_UPDATE_RETRIES,
    RETRYABLE_SQLSTATE_CODES,
    _execute_with_retry,
    _is_retryable_db_error,
)
from lex.audit_logging.utils.content_types import safe_get_content_type


def _make_pg_operational_error(pgcode):
    """Construct an OperationalError with a populated ``pgcode``."""
    err = OperationalError("simulated transient DB error")
    err.pgcode = pgcode
    return err


class TestCluster06f_RetryAndCacheHealing(SimpleTestCase):
    """Retry behaviour + ``safe_get_content_type`` defensive contract."""

    # -- 6.61 ----------------------------------------------------------
    def test_6_61_retryable_pgcode_retries_then_succeeds(self) -> None:
        """
        Scenario 6.61: A retryable ``OperationalError`` (pgcode 40P01)
        triggers retries with exponential backoff up to
        ``MAX_UPDATE_RETRIES``; final attempt returns the value.
        """
        # Pin contract on the constants so a regression that drops a
        # code is caught at this level.
        self.assertEqual(
            RETRYABLE_SQLSTATE_CODES, {"40P01", "40001"},
            "Documented retryable PG SQLSTATE codes must be 40P01 + "
            "40001; got %r" % (RETRYABLE_SQLSTATE_CODES,),
        )
        self.assertEqual(MAX_UPDATE_RETRIES, 3, "Default 3 attempts")

        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise _make_pg_operational_error("40P01")
            return "ok"

        sleep_calls = []
        with patch(
            "lex.audit_logging.mixins.AuditLogMixin.time.sleep",
            side_effect=lambda s: sleep_calls.append(s),
        ):
            result = _execute_with_retry(flaky, operation_name="test")

        self.assertEqual(result, "ok")
        self.assertEqual(
            attempts["n"], 3,
            "Expected 3 attempts (2 fails + 1 success); got %d" % attempts["n"],
        )
        # Exponential backoff: BASE * 2^0 then BASE * 2^1
        self.assertEqual(
            sleep_calls,
            [BASE_RETRY_DELAY_SECONDS, BASE_RETRY_DELAY_SECONDS * 2],
            "Backoff must double each attempt; got %r" % (sleep_calls,),
        )

    # -- 6.62 ----------------------------------------------------------
    def test_6_62_retryable_error_exhausted_reraises(self) -> None:
        """
        Scenario 6.62: When all retries fail, the original exception
        is re-raised so the caller (and ``CallbackTask.on_failure``)
        sees the real error type and pgcode.
        """
        attempts = {"n": 0}

        def always_fails():
            attempts["n"] += 1
            raise _make_pg_operational_error("40P01")

        with patch(
            "lex.audit_logging.mixins.AuditLogMixin.time.sleep",
            return_value=None,
        ):
            with self.assertRaises(OperationalError) as cm:
                _execute_with_retry(always_fails, operation_name="test")

        self.assertEqual(
            getattr(cm.exception, "pgcode", None), "40P01",
            "The re-raised exception must preserve pgcode for the "
            "caller's diagnostics",
        )
        self.assertEqual(
            attempts["n"], MAX_UPDATE_RETRIES,
            "Expected exactly MAX_UPDATE_RETRIES attempts; got %d" % attempts["n"],
        )

    def test_6_62b_non_retryable_error_propagates_immediately(self) -> None:
        """
        Sub-pin: a non-retryable error must propagate on the first
        attempt — ``_is_retryable_db_error`` is the gate.
        """
        attempts = {"n": 0}

        def boom():
            attempts["n"] += 1
            raise ValueError("not a DB transient")

        with self.assertRaises(ValueError):
            _execute_with_retry(boom, operation_name="test")
        self.assertEqual(
            attempts["n"], 1,
            "Non-retryable errors must NOT be retried; got %d attempts"
            % attempts["n"],
        )
        # And the gate function itself
        self.assertFalse(
            _is_retryable_db_error(ValueError("plain")),
            "_is_retryable_db_error must return False for non-DB exceptions",
        )
        self.assertTrue(
            _is_retryable_db_error(_make_pg_operational_error("40P01")),
            "_is_retryable_db_error must return True for pgcode 40P01",
        )

    # -- 6.63 ----------------------------------------------------------
    def test_6_63_safe_get_content_type_input_validation(self) -> None:
        """
        Scenario 6.63 (input-validation half): ``safe_get_content_type(None)``
        is the defensive contract — bad input must raise rather than
        silently corrupt the audit row. The recovery half (covered in
        :class:`TestCluster06f_CacheHealing`) requires DB access.
        """
        with self.assertRaises(ValueError):
            safe_get_content_type(None)


class TestCluster06f_CacheHealing(TestCase):
    """``safe_get_content_type`` recovery from stale cache (DB-backed)."""

    def test_6_63b_safe_get_content_type_recovers_from_stale_cache(self) -> None:
        """
        Scenario 6.63 (recovery half): when ``get_for_model`` raises
        ``DoesNotExist`` once (stale cache), the helper clears the
        cache and retries — the second call returns a valid
        ``ContentType``. The exception MUST NOT propagate, otherwise
        every audit-row write would crash post-migration.
        """
        from django.contrib.contenttypes.models import ContentType as CT

        real_ct = CT.objects.get_for_model(CT)
        call_state = {"n": 0}

        def flaky_get_for_model(model):
            call_state["n"] += 1
            if call_state["n"] == 1:
                raise CT.DoesNotExist("stale cache — first call fails")
            return real_ct

        with patch.object(
            CT.objects, "get_for_model", side_effect=flaky_get_for_model,
        ):
            try:
                result = safe_get_content_type(CT)
            except CT.DoesNotExist as exc:
                self.fail(
                    "safe_get_content_type must NEVER let "
                    "ContentType.DoesNotExist escape; got %r" % (exc,)
                )

        self.assertGreaterEqual(
            call_state["n"], 2,
            "Helper must retry after the first DoesNotExist; saw %d "
            "call(s)" % call_state["n"],
        )
        self.assertEqual(
            getattr(result, "id", None), real_ct.id,
            "Recovery must return a valid ContentType after cache "
            "invalidation; got %r" % (result,),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()






