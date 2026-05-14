"""
Cluster 8a: Sync fallback when Celery is inactive or unavailable.

Intent (from docs/features/calculations/ + Celery integration docs):

    When ``CELERY_ACTIVE`` is not set to ``"true"``, or when
    ``calculate()`` has no ``.delay`` attribute (no task decorator),
    ``should_use_celery()`` must return False and the framework must
    execute the calculation synchronously. This is the default
    zero-config experience.

Scenario numbering matches
docs/test-plan/test-clusters.md#8-celery--async.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from lex.core.models.CalculationModel import CalculationModel
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CelerySyncCalc


class TestCluster08a_SyncFallback(E2ETestCase):
    """Synchronous fallback when Celery is off."""

    e2e_models = ALL_MODELS

    # -- 8.1 -----------------------------------------------------------
    def test_8_1_should_use_celery_false_when_celery_active_false(self) -> None:
        """
        Scenario 8.1: ``CELERY_ACTIVE=False`` → should_use_celery()=False.

        E2ETestCase sets ``CELERY_ACTIVE=False`` in env.
        """
        with patch.dict(os.environ, {"CELERY_ACTIVE": "False"}, clear=False):
            calc = CelerySyncCalc(name="s8-1")
            self.assertFalse(
                calc.should_use_celery(),
                "should_use_celery must return False when CELERY_ACTIVE is not 'true'",
            )

    # -- 8.2 -----------------------------------------------------------
    def test_8_2_should_use_celery_false_without_delay_attr(self) -> None:
        """
        Scenario 8.2: No ``.delay`` attribute on calculate → False.

        Even if CELERY_ACTIVE were 'true', an undecorated calculate
        method cannot be dispatched — the framework must fall back.
        """
        with patch.dict(os.environ, {"CELERY_ACTIVE": "true"}, clear=False):
            calc = CelerySyncCalc(name="s8-2")
            # lex_func() returns the plain calculate method — no .delay.
            self.assertFalse(
                hasattr(calc.lex_func(), "delay"),
                "Plain calculate has no .delay — precondition of 8.2",
            )
            self.assertFalse(
                calc.should_use_celery(),
                "should_use_celery must be False when .delay is missing",
            )

    # -- 8.3 -----------------------------------------------------------
    def test_8_3_sync_execution_success(self) -> None:
        """Scenario 8.3: Sync execution → SUCCESS + history trail."""
        calc = CelerySyncCalc(name="s8-3", should_fail=False)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        fresh = CelerySyncCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.SUCCESS,
            f"Sync calc must end SUCCESS; got {fresh.is_calculated!r}",
        )

    # -- 8.4 -----------------------------------------------------------
    def test_8_4_sync_execution_failure(self) -> None:
        """Scenario 8.4: Sync execution failure → ERROR with error_message."""
        calc = CelerySyncCalc(name="s8-4", should_fail=True)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            pass

        fresh = CelerySyncCalc.objects.get(pk=calc.pk)
        self.assertEqual(fresh.is_calculated, CalculationModel.ERROR)
        self.assertTrue(
            fresh.calculation_error_message,
            "calculation_error_message must be populated on failure",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

