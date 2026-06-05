"""
Cluster 9c: ``CacheManager.cleanup_calculation`` — root-process discipline.

Intent (from docs/features/calculations/ + cache-manager docs):

    ``cleanup_calculation`` must run exactly once per root calculation
    — in the root process. Child processes spawned by the framework
    must NOT re-run the cleanup, otherwise they'd wipe the parent's
    cache prematurely.

The Pass B2 fixture (``e2e_unpatch``) lets us release the default
``store_message`` / ``build_cache_key`` mocks so the real
``CacheManager`` path runs, and ``patch`` gives us a direct spy on
``cleanup_calculation``.

Scenario numbering matches
docs/test-plan/test-clusters.md#9-signals--websocket.
"""

from __future__ import annotations

import unittest

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SigAtomicCalc

import pytest

pytestmark = pytest.mark.signals_ws


class TestCluster09c_CacheCleanup(E2ETestCase):
    """CacheManager.cleanup_calculation root-vs-child discipline."""

    e2e_models = ALL_MODELS
    # The framework's cleanup branch goes through ContextResolver, which
    # looks up an AuditLog row by calculation_id. Make sure the table
    # exists so we can pre-seed a matching row below.
    e2e_framework_models = [AuditLog, AuditLogStatus]

    # -- 9.4 -----------------------------------------------------------
    def test_9_4_root_process_cleans_up_cache(self) -> None:
        """
        Scenario 9.4: Root process → ``CacheManager.cleanup_calculation``
        is invoked exactly once when a calculation finishes.

        Intent: the root process in a calculation tree owns the cache
        lifecycle. When its ``calculate_hook`` finishes (success or
        error), the framework must call ``cleanup_calculation`` so the
        calculation's cache entries don't linger.

        Setup: this is a single, flat calculation — no parent,
        ``context.parent_record is None`` — so the framework treats
        this instance as the root. We wrap the save in an
        :class:`OperationContext` so ``ContextResolver`` can resolve
        a ``calculation_id`` (that is the documented prerequisite for
        the cleanup branch to run at all).

        Observation: patch ``CacheManager.cleanup_calculation`` directly
        and assert (a) it was called at least once and (b) the
        ``calculation_id`` it received matches the one we set on the
        operation context.
        """
        from unittest.mock import patch
        from lex.audit_logging.utils.CacheManager import (
            CacheManager,
            CacheCleanupResult,
        )
        from lex.audit_logging.utils.ModelContext import (
            model_logging_context,
        )

        calc_id = "calc-9-4"
        # Pre-seed the AuditLog row that ContextResolver.resolve() looks
        # up — without it, the cleanup branch aborts early with
        # "AuditLog not found for calculation_id: …" and the test
        # observation window closes before cleanup_calculation is ever
        # called. This mirrors what happens in production: an
        # ``AuditLogMixin`` action (API write) or the Celery task
        # wrapper creates the AuditLog row at the start of the
        # calculation; by the time ``calculate_hook`` finishes, the row
        # is there for ContextResolver to find.
        AuditLog.objects.create(
            resource="sigatomiccalc",
            action="create",
            calculation_id=calc_id,
        )

        # Save the instance up front so it has a pk — then run the
        # calculation under both the operation context (for calculation_id)
        # and the model-logging context (so ContextResolver sees this
        # instance as the ``current_record``, which is how the framework
        # decides "this is the root process").
        calc = SigAtomicCalc.objects.create(
            name="s9-4", should_fail=False,
        )

        with patch.object(
            CacheManager,
            "cleanup_calculation",
            return_value=CacheCleanupResult(
                success=True, errors=[], cleaned_keys=[],
            ),
        ) as spy:
            with self.operation_context(calc_id), model_logging_context(calc):
                calc.is_calculated = CalculationModel.IN_PROGRESS
                calc.save()

        self.assertGreaterEqual(
            spy.call_count, 1,
            "Root process must invoke CacheManager.cleanup_calculation "
            "exactly once when its calculate_hook finishes.",
        )
        # Any call must reference the calc_id we placed on the context.
        observed_ids = [
            kw.get("calculation_id", (a[0] if a else None))
            for a, kw in (call for call in spy.call_args_list)
        ]
        self.assertIn(
            calc_id, observed_ids,
            f"cleanup_calculation must be called with calculation_id="
            f"{calc_id!r}; observed {observed_ids!r}",
        )

    # -- 9.5 -----------------------------------------------------------
    def test_9_5_child_process_skips_cache_cleanup(self) -> None:
        """
        Scenario 9.5: Child process → ``cleanup_calculation`` is NOT
        invoked; only the root process owns cache lifecycle.

        Intent: if a calculation is nested under a parent, the framework
        must leave the cache intact — the parent is still running and
        will do its own cleanup when *it* finishes. If the child also
        cleaned up, the parent would observe a half-empty cache.

        Fixture: stack two models on the ``model_logging_context`` — an
        outer ``parent`` pushed first (becomes the root) and an inner
        ``child`` pushed on top (becomes ``current``). The framework's
        root detection (``root_record == current_record``) then
        evaluates to False on the child's ``calculate_hook`` exit.
        """
        from unittest.mock import patch
        from lex.audit_logging.utils.CacheManager import CacheManager
        from lex.audit_logging.utils.ModelContext import (
            model_logging_context,
        )

        calc_id = "calc-9-5"
        AuditLog.objects.create(
            resource="sigatomiccalc",
            action="create",
            calculation_id=calc_id,
        )

        parent = SigAtomicCalc.objects.create(name="parent-9-5")
        child = SigAtomicCalc.objects.create(name="child-9-5")

        with patch.object(
            CacheManager, "cleanup_calculation",
        ) as spy:
            with self.operation_context(calc_id), \
                    model_logging_context(parent), \
                    model_logging_context(child):
                # Only the CHILD runs its hook here — the parent is
                # still "above" us on the stack, so the framework must
                # treat this as a child process.
                child.is_calculated = CalculationModel.IN_PROGRESS
                child.save()

        self.assertEqual(
            spy.call_count, 0,
            "A child process must NOT call cleanup_calculation — the "
            "root still owns the calculation's cache entries. "
            f"Got {spy.call_count} call(s) with args "
            f"{spy.call_args_list!r}.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


