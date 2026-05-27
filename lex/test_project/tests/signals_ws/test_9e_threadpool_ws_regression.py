"""
Cluster 9e: regression — WebSocket broadcast carries ``calculation_id``
through the synchronous ``_run_in_calculation_executor`` path.

Background
----------
When ``_run_in_calculation_executor`` was first implemented it submitted
the calculation to ``_calculation_executor`` (a ``ThreadPoolExecutor``).
Worker threads do **not** inherit the caller's ``ContextVar`` state
(they get fresh default values unless ``copy_context()`` is used).
As a result:

* ``_resolve_calculation_id`` in ``CalculationSignals`` read an empty
  ``operation_context`` and returned ``None``.
* The SUCCESS / ERROR WebSocket broadcast was emitted without a
  ``calculation_id`` — the frontend spinner never resolved, and the
  calculation-log frame showed no log entries.

The fix: ``_run_in_calculation_executor`` now delegates to
``execute_calculation_sync()`` inline on the **same** thread, so the
caller's ``ContextVar`` is directly accessible and every broadcast
carries the correct ``calculation_id``.

Scenarios
---------
9.29  SUCCESS broadcast from the synchronous calculation path carries
      the ``calculation_id`` that was set in ``operation_context``.
9.30  ERROR broadcast from the synchronous calculation path carries the
      ``calculation_id`` that was set in ``operation_context``.
9.31  ``_run_in_calculation_executor`` runs ``execute_calculation_sync``
      on the same thread — confirmed by ``ContextVar`` visibility.
"""

from __future__ import annotations

import unittest
from contextvars import ContextVar
from unittest.mock import patch

from lex.core.models.CalculationModel import CalculationModel
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SigAtomicCalc

import pytest

pytestmark = pytest.mark.signals_ws


class TestCluster09e_ThreadpoolWsRegression(E2ETestCase):
    """
    9.29 / 9.30 / 9.31 — synchronous calculation path preserves
    ``operation_context`` so WebSocket broadcasts carry ``calculation_id``.
    """

    e2e_models = ALL_MODELS

    # ── helpers ────────────────────────────────────────────────────────

    def _capture_ws_broadcasts(self):
        """
        Return (broadcasts_list, context_manager).

        Patches ``sync_channel_group_send`` in ``CalculationSignals`` so
        every broadcast call is appended to *broadcasts_list* instead of
        attempting a real channel-layer send (which would fail silently
        in tests with no channel layer configured).
        """
        broadcasts: list[tuple[str, dict]] = []
        patcher = patch(
            "lex.core.signals.CalculationSignals.sync_channel_group_send",
            side_effect=lambda group, msg: broadcasts.append((group, msg)),
        )
        return broadcasts, patcher

    # -- 9.29 -------------------------------------------------------

    def test_9_29_success_broadcast_carries_calculation_id(self) -> None:
        """
        Scenario 9.29: SUCCESS broadcast carries the ``calculation_id``
        that was set in ``operation_context`` when the calculation ran.

        Given: ``operation_context`` carries ``calculation_id = "ws-reg-29"``
        When:  a calculation is triggered via ``save()`` (non-Celery,
               synchronous path through ``_run_in_calculation_executor``)
               and the ``calculate()`` method returns normally
        Then:  the ``calculation_success`` WebSocket broadcast payload
               includes ``calculation_id = "ws-reg-29"``

        Regression guard: if ``_run_in_calculation_executor`` were
        reverted to submit the work to a ``ThreadPoolExecutor`` without
        ``copy_context()``, the worker thread would see an empty
        ``operation_context``, ``_resolve_calculation_id`` would return
        ``None``, and this assertion would fail.
        """
        calc_id = "ws-reg-29"
        calc = SigAtomicCalc.objects.create(name="s9-29", should_fail=False)

        broadcasts, patcher = self._capture_ws_broadcasts()
        with patcher:
            with self.operation_context(calc_id):
                calc.is_calculated = CalculationModel.IN_PROGRESS
                calc.save()

        success_msgs = [
            msg
            for _, msg in broadcasts
            if msg.get("type") == "calculation_success"
        ]
        self.assertTrue(
            success_msgs,
            "A calculation_success WebSocket broadcast must be emitted "
            "after a successful synchronous calculation.",
        )
        observed_id = success_msgs[-1]["payload"].get("calculation_id")
        self.assertEqual(
            observed_id,
            calc_id,
            f"The SUCCESS broadcast payload must carry calculation_id="
            f"{calc_id!r} from the caller's operation_context; "
            f"got {observed_id!r}.  "
            f"If this is None, the synchronous executor is running on a "
            f"separate thread without ContextVar propagation.",
        )

    # -- 9.30 -------------------------------------------------------

    def test_9_30_error_broadcast_carries_calculation_id(self) -> None:
        """
        Scenario 9.30: ERROR broadcast carries the ``calculation_id``
        that was set in ``operation_context`` when the calculation ran.

        Given: ``operation_context`` carries ``calculation_id = "ws-reg-30"``
        When:  a calculation is triggered via ``save()`` (non-Celery,
               synchronous path) and ``calculate()`` raises an exception
        Then:  the ``calculation_error`` WebSocket broadcast payload
               includes ``calculation_id = "ws-reg-30"``

        Regression guard: same as 9.29 — a thread-pool implementation
        without ``copy_context()`` loses the ``calculation_id``.
        """
        calc_id = "ws-reg-30"
        calc = SigAtomicCalc.objects.create(name="s9-30", should_fail=True)

        broadcasts, patcher = self._capture_ws_broadcasts()
        with patcher:
            with self.operation_context(calc_id):
                calc.is_calculated = CalculationModel.IN_PROGRESS
                # Intentional failure — calculate() raises RuntimeError.
                # The framework must catch it, persist ERROR, and emit
                # the error broadcast.
                try:
                    calc.save()
                except Exception:
                    pass  # Expected — we only care about the broadcast.

        error_msgs = [
            msg
            for _, msg in broadcasts
            if msg.get("type") == "calculation_error"
        ]
        self.assertTrue(
            error_msgs,
            "A calculation_error WebSocket broadcast must be emitted "
            "after a failed synchronous calculation.",
        )
        observed_id = error_msgs[-1]["payload"].get("calculation_id")
        self.assertEqual(
            observed_id,
            calc_id,
            f"The ERROR broadcast payload must carry calculation_id="
            f"{calc_id!r} from the caller's operation_context; "
            f"got {observed_id!r}.  "
            f"If this is None, the synchronous executor is running on a "
            f"separate thread without ContextVar propagation.",
        )

    # -- 9.31 -------------------------------------------------------

    def test_9_31_run_in_calculation_executor_preserves_contextvar(
        self,
    ) -> None:
        """
        Scenario 9.31: ``_run_in_calculation_executor`` is transparent to
        ``ContextVar`` — the value set by the caller is visible inside
        ``execute_calculation_sync``.

        This test uses a dedicated ``ContextVar`` (not ``operation_context``)
        so the assertion is independent of any framework wiring.  If
        ``_run_in_calculation_executor`` were reimplemented to use a bare
        ``ThreadPoolExecutor.submit()`` (without ``copy_context()``), the
        worker thread would see the default value and the assertion
        would fail.

        Given: a sentinel ``ContextVar`` is set to ``"caller"`` on the
               current thread
        When:  ``_run_in_calculation_executor`` is called
        Then:  ``execute_calculation_sync`` observes the value ``"caller"``
               (not the default ``"default"``)
        """
        _sentinel: ContextVar[str] = ContextVar("_sentinel_9_31", default="default")
        _sentinel.set("caller")

        observed_values: list[str] = []

        calc = SigAtomicCalc.objects.create(name="s9-31", should_fail=False)

        original_execute = calc.execute_calculation_sync

        def _spy_execute():
            observed_values.append(_sentinel.get())
            original_execute()

        broadcasts, patcher = self._capture_ws_broadcasts()
        with patcher, patch.object(calc, "execute_calculation_sync", _spy_execute):
            calc._run_in_calculation_executor()

        self.assertEqual(
            len(observed_values), 1,
            "_run_in_calculation_executor must invoke execute_calculation_sync exactly once.",
        )
        self.assertEqual(
            observed_values[0],
            "caller",
            "execute_calculation_sync must observe the caller's ContextVar "
            "value ('caller').  Got 'default', which means the executor "
            "ran on a different thread without ContextVar propagation.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
