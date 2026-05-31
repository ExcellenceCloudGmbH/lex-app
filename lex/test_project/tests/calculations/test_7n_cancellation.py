"""
Cluster 7n: Manual cancellation of a calculation.

Intent (from docs/reference/CalculationModel Internals.md,
docs/features/processing/calculations.md, and
docs/lex_topics/04-calculationmodel-lifecycle.md):

    The documented state machine has an ``ABORTED`` terminal state with the
    transitions ``IN_PROGRESS → ABORTED`` ("manually cancelled") and the
    retry path ``ABORTED → IN_PROGRESS``. A user clicking "cancel" must
    reach ABORTED through a deliberate, supported API — not by mutating
    ``is_calculated`` by hand. The framework must:

    * transition only IN_PROGRESS records (terminal states are no-ops);
    * persist ABORTED without re-triggering ``calculate_hook``;
    * clear the record from ``ActiveCalculationStateStore`` so the
      reconciliation snapshot returned to a (re)connecting WebSocket
      client no longer reports the calc as active;
    * broadcast ``calculation_aborted`` via ``update_calculation_status``;
    * preserve the retry path — saving an ABORTED record with
      ``is_calculated=IN_PROGRESS`` must restart the calculation.

    Why a regression matters: without a real ``cancel()`` API, a frontend
    cancel button has no choice but to overwrite ``is_calculated``
    directly, which (a) skips the broadcast path so other tabs and the
    spinner state desync, and (b) leaves a stale
    ``ActiveCalculationStateStore`` entry that re-spawns the spinner on
    every page refresh.

Cluster 7n — scenarios 7.166–7.172. Type: I.
Covers: lex/core/models/CalculationModel.py (the new ``cancel()`` method).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7n_cancellation.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# Coverage-pairing: explicit import of the changed module so the
# .github/scripts/copilot_coverage_detect.py heuristic accepts this test
# as paired with the cancel() addition in lex/core/models/CalculationModel.py.
from lex.core.models import CalculationModel as _calculation_model_module  # noqa: F401

from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import (
    ActiveCalculationStateStore,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AtomicCalc, FailingCalc

pytestmark = pytest.mark.calculations


class TestCluster07n_Cancellation(E2ETestCase):
    """Cluster 7n: ``CalculationModel.cancel()`` — manual abort API."""

    e2e_models = ALL_MODELS
    # Cancel + the active-state-store assertion in 7.170 need the *real*
    # ActiveCalculationStateStore, not the default E2ETestCase Mock.
    e2e_unpatch = ["mark_in_progress"]

    def setUp(self) -> None:
        super().setUp()
        # Each test owns a fresh store — the store is a process-global
        # in-memory dict (see ActiveCalculationStateStore docstring), so
        # prior tests must not leak entries into ours.
        ActiveCalculationStateStore.clear_all()

    # -- 7.166 ---------------------------------------------------------
    def test_7_166_cancel_in_progress_transitions_to_aborted(self) -> None:
        """
        Scenario 7.166: cancel() on an IN_PROGRESS record persists ABORTED.

        Given: an AtomicCalc record sitting at IN_PROGRESS (no calc kicked
               off — we set the field directly with skip_hooks).
        When:  cancel() is called.
        Then:  the in-memory instance reports ABORTED, the persisted row
               reports ABORTED, and cancel() returned True.
        """
        calc = AtomicCalc(name="c7-166")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)

        result = calc.cancel()

        self.assertTrue(
            result,
            "cancel() on IN_PROGRESS must return True to signal it took effect",
        )
        self.assertEqual(
            calc.is_calculated,
            CalculationModel.ABORTED,
            f"In-memory instance must be ABORTED; got {calc.is_calculated!r}",
        )
        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.ABORTED,
            f"Persisted row must be ABORTED; got {fresh.is_calculated!r}",
        )

    # -- 7.167 ---------------------------------------------------------
    def test_7_167_cancel_on_success_is_noop(self) -> None:
        """
        Scenario 7.167: cancel() on a SUCCESS record is a no-op.

        Given: a record that completed successfully.
        When:  cancel() is called.
        Then:  returns False and the state stays SUCCESS — terminal
               states cannot be "un-terminated" by a cancel.
        """
        calc = AtomicCalc(name="c7-167", should_fail=False)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()  # runs calculate_hook → SUCCESS

        self.assertEqual(
            calc.is_calculated,
            CalculationModel.SUCCESS,
            "Precondition: setup must leave the record at SUCCESS",
        )

        result = calc.cancel()

        self.assertFalse(
            result,
            "cancel() on a terminal state must return False (no-op)",
        )
        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.SUCCESS,
            "SUCCESS must NOT be overwritten by a stray cancel() call",
        )

    # -- 7.168 ---------------------------------------------------------
    def test_7_168_cancel_on_not_calculated_is_noop(self) -> None:
        """
        Scenario 7.168: cancel() on a NOT_CALCULATED record is a no-op.

        A record that never started is not "running" — cancelling it is
        meaningless and must not silently move it to ABORTED.
        """
        calc = AtomicCalc(name="c7-168")
        calc.save(skip_hooks=True)

        self.assertEqual(
            calc.is_calculated,
            CalculationModel.NOT_CALCULATED,
            "Precondition: a freshly-saved AtomicCalc is NOT_CALCULATED",
        )

        result = calc.cancel()

        self.assertFalse(
            result,
            "cancel() on NOT_CALCULATED must return False",
        )
        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.NOT_CALCULATED,
            "NOT_CALCULATED must not be promoted to ABORTED by cancel()",
        )

    # -- 7.169 ---------------------------------------------------------
    def test_7_169_cancel_on_error_is_noop(self) -> None:
        """
        Scenario 7.169: cancel() on an ERROR record is a no-op.

        A failed calc is already in a terminal state — cancelling it
        would erase the error signal from the UI.
        """
        calc = FailingCalc(name="c7-169")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            # FailingCalc.calculate raises by design; the framework
            # converts that to a persisted ERROR row.
            pass

        fresh = FailingCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.ERROR,
            "Precondition: FailingCalc must have landed at ERROR",
        )

        result = fresh.cancel()

        self.assertFalse(
            result,
            "cancel() on ERROR must return False (no-op)",
        )
        self.assertEqual(
            FailingCalc.objects.get(pk=calc.pk).is_calculated,
            CalculationModel.ERROR,
            "ERROR must remain ERROR — cancel() does not erase failures",
        )

    # -- 7.170 ---------------------------------------------------------
    def test_7_170_cancel_clears_active_state_store_and_broadcasts(self) -> None:
        """
        Scenario 7.170: cancel() prunes the ActiveCalculationStateStore
        entry and emits ``calculation_aborted`` to subscribers.

        Without this, a (re)connecting WebSocket client would still see
        the cancelled record in its reconciliation snapshot and re-show
        the spinner on every page refresh.
        """
        calc = AtomicCalc(name="c7-170")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)

        record_id = f"{calc._meta.model_name}_{calc.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="calc-7-170",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        self.assertIn(
            record_id,
            ActiveCalculationStateStore._state_map,
            "Precondition: store must contain the IN_PROGRESS entry",
        )

        with patch(
            "lex.core.signals.CalculationSignals.sync_channel_group_send"
        ) as mock_send:
            result = calc.cancel()

        self.assertTrue(
            result,
            "cancel() on IN_PROGRESS must return True",
        )
        self.assertNotIn(
            record_id,
            ActiveCalculationStateStore._state_map,
            "ActiveCalculationStateStore must no longer carry the cancelled record",
        )
        self.assertTrue(
            mock_send.called,
            "cancel() must invoke sync_channel_group_send to broadcast the abort",
        )
        group, message = mock_send.call_args.args
        self.assertEqual(
            group,
            "update_calculation_status",
            "Broadcast must target the update_calculation_status channel group",
        )
        self.assertEqual(
            message.get("type"),
            "calculation_aborted",
            f"Broadcast type must be 'calculation_aborted'; got {message.get('type')!r}",
        )
        self.assertEqual(
            message.get("payload", {}).get("record_id"),
            record_id,
            "Broadcast payload must identify the cancelled record",
        )

    # -- 7.171 ---------------------------------------------------------
    def test_7_171_cancel_reason_is_stored_on_error_message_field(self) -> None:
        """
        Scenario 7.171: cancel(reason=...) stores the reason on the
        record's ``calculation_error_message`` field (when present),
        prefixed with ``"Cancelled: "``.

        This is what the UI surfaces next to the ABORTED badge so the
        user can see *why* the calc was cancelled.
        """
        calc = AtomicCalc(name="c7-171")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)

        result = calc.cancel(reason="superseded by newer upload")

        self.assertTrue(result, "cancel() with a reason must still return True")
        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.ABORTED,
            "Reason must not affect the state transition",
        )
        self.assertEqual(
            fresh.calculation_error_message,
            "Cancelled: superseded by newer upload",
            "Reason must land on calculation_error_message with the "
            "'Cancelled: ' prefix so the UI can render it consistently",
        )

    # -- 7.172 ---------------------------------------------------------
    def test_7_172_retry_after_cancel_runs_calculate_again(self) -> None:
        """
        Scenario 7.172: An ABORTED record can be retried.

        The documented state machine has ``ABORTED → IN_PROGRESS : Retry``.
        After cancel(), saving the record with ``is_calculated=IN_PROGRESS``
        must restart the calculation and reach SUCCESS (assuming
        ``calculate()`` itself succeeds).
        """
        calc = AtomicCalc(name="c7-172", should_fail=False)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)

        self.assertTrue(calc.cancel(), "Setup: cancel() must take effect")
        self.assertEqual(
            AtomicCalc.objects.get(pk=calc.pk).is_calculated,
            CalculationModel.ABORTED,
            "Setup: record must be ABORTED before retry",
        )

        # Retry: setting IN_PROGRESS and saving must re-trigger calculate_hook.
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.SUCCESS,
            "Retry path ABORTED → IN_PROGRESS → SUCCESS must work; "
            f"got {fresh.is_calculated!r}",
        )



