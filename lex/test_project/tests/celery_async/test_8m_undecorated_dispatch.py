"""
Cluster 8m: Undecorated calculations are still dispatched to Celery.

Intent (from docs/features/calculations/ + Celery integration docs):

    Every root calculation must run as a Celery task when
    ``CELERY_ACTIVE=true`` and the broker is reachable — **regardless of
    whether the user's ``calculate()`` is decorated with
    ``@lex_shared_task``**. Previously the framework silently fell back
    to inline execution for undecorated methods, which meant the same
    code path behaved differently depending on whether the developer
    happened to add the decorator. That divergence is removed.

    For undecorated methods, :meth:`CalculationModel.dispatch_calculation_task`
    wraps the instance in the generic ``calc_and_save`` task that
    already exists in ``lex/lex_app/celery_tasks.py``; the worker then
    invokes ``model.lex_func()()``.

Why this matters: pressing "Calculate" in the UI must produce the same
async semantics for every CalculationModel — otherwise users see hangs
on the request thread for undecorated calcs while decorated ones return
HTTP 202 immediately, which masks slow calculations as backend bugs.

Run:
    python -m lex pytest lex/test_project/tests/celery_async/test_8m_undecorated_dispatch.py -v

Scenarios 8.49 – 8.50 (continues cluster 8 from current max 8.48).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pytest

from lex.api.utils import operation_context
from lex.audit_logging.utils.ModelContext import _model_context
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CelerySyncCalc

pytestmark = pytest.mark.celery_async


class TestCluster08m_UndecoratedDispatch(E2ETestCase):
    """Undecorated CalculationModels still dispatch to Celery."""

    e2e_models = ALL_MODELS

    # -- 8.49 ----------------------------------------------------------
    def test_8_49_undecorated_dispatch_uses_calc_and_save(self) -> None:
        """
        Scenario 8.49: dispatch_calculation_task on an undecorated calc.

        Given: a CalculationModel whose ``calculate()`` has no ``.delay``
               attribute (no ``@lex_shared_task`` decoration).
        When:  ``dispatch_calculation_task()`` is invoked with a populated
               operation_context and model_context.
        Then:  the generic ``calc_and_save`` task receives the instance in
               its first positional argument, plus the extracted context
               and model_context kwargs — i.e. the framework dispatches
               through the generic-task path instead of refusing.
        """
        calc = CelerySyncCalc(name="s8-49")

        # Precondition: this calculate is genuinely undecorated.
        self.assertFalse(
            hasattr(calc.lex_func(), "delay"),
            "Precondition: CelerySyncCalc.calculate has no .delay",
        )

        fake_async_result = MagicMock(name="AsyncResult", id="task-8-49")
        fake_task = MagicMock(name="calc_and_save_task")
        fake_task.delay = MagicMock(return_value=fake_async_result)

        op_ctx_token = operation_context.set({
            "calculation_id": "calc-8-49",
            "request_obj": None,
            "user_context": None,
        })
        model_ctx_token = _model_context.set({"model_context": {}})
        try:
            with patch(
                "lex.lex_app.celery_tasks.calc_and_save",
                fake_task,
            ), patch(
                "lex.lex_app.celery_tasks.register_task_with_context",
                side_effect=lambda r: r,
            ):
                result = calc.dispatch_calculation_task()
        finally:
            operation_context.reset(op_ctx_token)
            _model_context.reset(model_ctx_token)

        self.assertIs(
            result, fake_async_result,
            "dispatch_calculation_task must return the AsyncResult of the "
            "generic calc_and_save task when calculate is undecorated",
        )
        self.assertEqual(
            fake_task.delay.call_count, 1,
            "calc_and_save.delay must be invoked exactly once for the "
            "undecorated dispatch path",
        )
        args, kwargs = fake_task.delay.call_args
        self.assertEqual(
            list(args[0]), [calc],
            "calc_and_save must receive the model instance in a 1-element "
            "list as its first positional argument",
        )
        self.assertIn(
            "context", kwargs,
            "calc_and_save must be called with the extracted operation context",
        )
        self.assertEqual(
            kwargs["context"].get("calculation_id"), "calc-8-49",
            "Dispatched context must preserve the calculation_id from "
            "operation_context",
        )
        self.assertIn(
            "model_context", kwargs,
            "calc_and_save must be called with model_context",
        )

    # -- 8.50 ----------------------------------------------------------
    def test_8_50_decorated_dispatch_skips_generic_task(self) -> None:
        """
        Scenario 8.50: Decorated calculate keeps the fast path.

        Given: a CalculationModel whose ``lex_func()`` exposes ``.delay``
               (decorated with ``@lex_shared_task``).
        When:  ``dispatch_calculation_task()`` is invoked.
        Then:  the user's task ``.delay`` is invoked directly and the
               generic ``calc_and_save`` task is NOT used — the existing
               fast path for decorated methods is preserved.
        """
        calc = CelerySyncCalc(name="s8-50")

        fake_async_result = MagicMock(name="AsyncResult", id="task-8-50")
        decorated_func = MagicMock(name="decorated_lex_func")
        decorated_func.delay = MagicMock(return_value=fake_async_result)

        generic_task = MagicMock(name="calc_and_save_task")
        generic_task.delay = MagicMock()

        op_ctx_token = operation_context.set({
            "calculation_id": "calc-8-50",
            "request_obj": None,
            "user_context": None,
        })
        model_ctx_token = _model_context.set({"model_context": {}})
        try:
            with patch.object(
                type(calc), "lex_func", return_value=decorated_func,
            ), patch(
                "lex.lex_app.celery_tasks.calc_and_save",
                generic_task,
            ), patch(
                "lex.lex_app.celery_tasks.register_task_with_context",
                side_effect=lambda r: r,
            ):
                result = calc.dispatch_calculation_task()
        finally:
            operation_context.reset(op_ctx_token)
            _model_context.reset(model_ctx_token)

        self.assertIs(
            result, fake_async_result,
            "dispatch_calculation_task must return the decorated task's "
            "AsyncResult on the fast path",
        )
        self.assertEqual(
            decorated_func.delay.call_count, 1,
            "Decorated lex_func.delay must be invoked exactly once",
        )
        self.assertEqual(
            generic_task.delay.call_count, 0,
            "calc_and_save must NOT be used when the user's method is "
            "already a Celery task — the fast path must be preserved",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


