"""
Cluster 8b: Celery dispatch context extraction and worker recursion guard.

These two baseline scenarios were listed in the test plan as 8.5 / 8.6
but coverage had moved into later 8g/8h tests without preserving the
scenario ids. This file keeps the plan-to-test contract explicit while
remaining broker-free.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from lex.api.utils import operation_context
from lex.lex_app.celery_tasks import is_celery_worker_process, lex_shared_task
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CeleryCalc, CelerySyncCalc

import pytest

pytestmark = pytest.mark.celery_async


class TestCluster08b_DispatchContext(E2ETestCase):
    """8.5 / 8.6 — dispatch context and worker-recursion behavior."""

    e2e_models = ALL_MODELS

    # -- 8.5 -----------------------------------------------------------
    def test_8_5_dispatch_calculation_task_extracts_context(self) -> None:
        """Scenario 8.5: dispatch serializes request context safely."""
        calc = CeleryCalc.objects.create(name="ctx-8-5")
        fake_task = MagicMock()
        fake_result = MagicMock(name="async-result")
        fake_task.delay.return_value = fake_result

        request_obj = MagicMock()
        request_obj.user.email = "user@example.test"
        request_obj.user.username = "fallback"
        request_obj.user.is_authenticated = True
        request_obj.headers = {"X-Test": "yes"}
        request_obj.method = "PATCH"
        request_obj.path = "/api/test"

        token = operation_context.set({
            "operation_id": "op-8-5",
            "request_obj": request_obj,
            "calculation_id": "calc-8-5",
            "audit_log_temp": None,
        })
        self.addCleanup(operation_context.reset, token)

        with patch.object(calc, "lex_func", return_value=fake_task), patch(
            "lex.lex_app.celery_tasks.register_task_with_context",
            side_effect=lambda result: result,
        ):
            result = calc.dispatch_calculation_task()

        self.assertIs(result, fake_result)
        fake_task.delay.assert_called_once()
        kwargs = fake_task.delay.call_args.kwargs
        dispatched_context = kwargs["context"]
        self.assertEqual(dispatched_context["calculation_id"], "calc-8-5")
        self.assertEqual(dispatched_context["operation_id"], "op-8-5")
        self.assertIsInstance(
            dispatched_context["request_obj"], dict,
            "dispatch_calculation_task must replace the live request object "
            "with a serializable dict before sending work to Celery.",
        )
        self.assertIn("model_context", kwargs)

    # -- 8.6 -----------------------------------------------------------
    def test_8_6_nested_calculation_inside_worker_runs_sync(self) -> None:
        """Scenario 8.6: inside a Celery task body, worker detection is true."""
        observations: list[bool] = []

        @lex_shared_task()
        def observe_worker_flag(_instance):
            observations.append(is_celery_worker_process())
            return "observed"

        calc = CelerySyncCalc(name="worker-8-6")
        result = observe_worker_flag.task(calc)

        self.assertEqual(result[0], "observed")
        self.assertEqual(
            observations, [True],
            "Nested calculations running inside a Celery task body must see "
            "is_celery_worker_process()=True so recursive dispatch can fall "
            "back to synchronous execution.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

