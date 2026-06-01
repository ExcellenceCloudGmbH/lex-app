"""
Cluster 7n: Cooperative cancellation of a running synchronous calculation.

Intent
------
Customers must be able to cancel a long-running calculation from the UI.
The contract (from ``docs/features/processing/calculations.md`` and the
state-machine diagram listing ``ABORTED`` as a first-class terminal state):

* A user-triggered cancel transitions the record from ``IN_PROGRESS`` to
  ``ABORTED`` — **not** ``ERROR``. ``ERROR`` means the calculation
  itself failed; ``ABORTED`` means the user no longer wants the result.
* A calculation that polls :meth:`CalculationModel.check_cancelled` at
  safe interruption points stops promptly when cancel is requested —
  raising :class:`CalculationCancelled` inside ``calculate()``.
* A calculation that does **not** poll cannot be hard-stopped on the
  sync route (Python provides no safe thread-kill).  The framework
  honours the user's intent anyway via a state-guard at the SUCCESS
  write: if cancel was requested while ``calculate()`` was running, the
  row settles as ``ABORTED`` even though the body ran to completion.
* The transition emits the existing ``calculation_aborted`` broadcast,
  so every connected WebSocket client clears the spinner immediately.
* The terminal audit row records ``status="aborted"``, distinguishing
  user cancellations from clean ``SUCCESS`` in the compliance log.

Scenario range — **7.166 – 7.176** (continues from 7k's 7.142;
7.155 – 7.165 reserved for the planned 7m batch).

Cancellation is the only public surface in this batch; the Celery
hard-cancel route is deferred to a separate follow-up sub-cluster.

Run
---
``python -m lex pytest lex/test_project/tests/calculations/test_7n_cancellation.py -v``
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import models
from django.test import SimpleTestCase

from lex.core.exceptions import CalculationCancelled
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult
from lex.core.signals.ActiveCalculationStateStore import (
    ActiveCalculationStateStore,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase

pytestmark = pytest.mark.calculations


# ---------------------------------------------------------------------
# Fixture models
# ---------------------------------------------------------------------


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("7n")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("7n")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class PollingCancelCalc(CalculationModel):
    """``calculate()`` polls ``self.check_cancelled()`` in a loop.

    Used by scenarios 7.166–7.169 to drive the cooperative-cancel path:
    the body iterates, polls the cancel flag, and raises
    ``CalculationCancelled`` the moment a cancel was requested.

    ``polled_iterations`` records how far the loop progressed so tests
    can assert that the cancel was observed *before* the body would
    have completed naturally.
    """

    name = models.CharField(max_length=200)
    polled_iterations = models.IntegerField(default=0)
    target_iterations = models.IntegerField(default=5)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        for i in range(self.target_iterations):
            # Real customer code would do unit-of-work here; we just
            # record progress so the test can assert the loop short-
            # circuited on cancel rather than running to completion.
            self.polled_iterations = i + 1
            self.check_cancelled()


@_permissive
class NonPollingCancelCalc(CalculationModel):
    """``calculate()`` runs to completion without polling.

    Used by scenario 7.171 to drive the state-guard branch: if the user
    cancels mid-flight but the body never polls, the framework must
    still settle ``ABORTED`` (not ``SUCCESS``) at the terminal write so
    the UI reflects the user's intent.
    """

    name = models.CharField(max_length=200)
    completed = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        # No check_cancelled() call — simulates a customer who hasn't
        # opted into cooperative cancellation yet.
        self.completed = True


ALL_7N_MODELS = [PollingCancelCalc, NonPollingCancelCalc]


# ---------------------------------------------------------------------
# 7.166 — CalculationCancelled exception identity
# ---------------------------------------------------------------------


class TestCluster07n_CalculationCancelledException(SimpleTestCase):
    """``CalculationCancelled`` is a distinct exception type the framework
    can branch on — not a generic ``Exception``."""

    def test_7_166_default_message_set(self) -> None:
        """Scenario 7.166: ``CalculationCancelled()`` carries a customer-visible default.

        Given: no message argument.
        When: the exception is instantiated.
        Then: ``str(exc)`` is non-empty and mentions cancellation.
        """
        exc = CalculationCancelled()
        self.assertTrue(
            str(exc), "CalculationCancelled must have a non-empty default message"
        )
        self.assertIn(
            "cancel",
            str(exc).lower(),
            f"Default message must reference cancellation; got {str(exc)!r}",
        )

    def test_7_166b_is_dedicated_subclass(self) -> None:
        """Scenario 7.166b: the framework can branch on the exception type.

        Given: a ``CalculationCancelled`` and an unrelated ``RuntimeError``.
        When: ``isinstance`` is used to discriminate.
        Then: the cancel exception is identified uniquely and is NOT a
        generic runtime error (the framework would otherwise route it
        into the ERROR path).
        """
        cancel = CalculationCancelled("user clicked cancel")
        self.assertIsInstance(cancel, CalculationCancelled)
        self.assertNotIsInstance(
            cancel,
            RuntimeError,
            "CalculationCancelled must not subclass RuntimeError — the "
            "framework distinguishes user cancellation from real failures.",
        )


# ---------------------------------------------------------------------
# 7.167 — ActiveCalculationStateStore cancel surface (the data layer)
# ---------------------------------------------------------------------


class TestCluster07n_StateStoreCancelSurface(SimpleTestCase):
    """``ActiveCalculationStateStore`` owns the cancel flag and actor.

    The store is the framework's transient registry of active
    calculations; the cancel flag lives there because the running
    calculation polls it at safe interruption points, and the request
    side (REST endpoint, management command) needs a thread-safe place
    to set it without touching the DB.
    """

    def setUp(self) -> None:
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)

    def test_7_167_request_cancel_returns_false_when_no_active_entry(self) -> None:
        """Scenario 7.167: cancelling a non-active record returns ``False``.

        Given: an empty state store (no record registered as IN_PROGRESS).
        When: ``request_cancel`` is called for an unknown record_id.
        Then: it returns ``False`` (caller treats as "nothing to cancel"),
        and no entry is silently created (the store stays empty).
        """
        ok = ActiveCalculationStateStore.request_cancel("unknown_42")
        self.assertFalse(
            ok,
            "request_cancel must return False when there is no active entry "
            "to flag — a True return would imply a cancel was registered.",
        )
        self.assertEqual(
            ActiveCalculationStateStore.get_entry("unknown_42"),
            {},
            "request_cancel must NOT silently create an entry for a record "
            "that isn't currently calculating.",
        )

    def test_7_167b_request_cancel_flags_active_entry(self) -> None:
        """Scenario 7.167b: cancel flag round-trips with the requesting actor.

        Given: a record marked IN_PROGRESS in the store.
        When: ``request_cancel`` is called with ``requested_by="alice"``.
        Then: ``is_cancel_requested`` returns ``True`` and
        ``get_cancel_requested_by`` returns ``"alice"``.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="pollingcancelcalc_1",
            calculation_id="calc-1",
            record="PollingCancelCalc(1)",
            model_label="lex_app.pollingcancelcalc",
            record_pk=1,
        )
        ok = ActiveCalculationStateStore.request_cancel(
            "pollingcancelcalc_1", requested_by="alice"
        )
        self.assertTrue(ok, "request_cancel must return True for an active entry")
        self.assertTrue(
            ActiveCalculationStateStore.is_cancel_requested("pollingcancelcalc_1"),
            "After request_cancel the flag must be observable via is_cancel_requested",
        )
        self.assertEqual(
            ActiveCalculationStateStore.get_cancel_requested_by("pollingcancelcalc_1"),
            "alice",
            "The actor that requested the cancel must round-trip through the store",
        )

    def test_7_167c_clear_drops_cancel_flag(self) -> None:
        """Scenario 7.167c: terminal-state ``clear()`` drops the cancel flag.

        Given: a flagged entry in the store.
        When: ``clear()`` is called (the framework calls this when the
        calculation reaches a terminal state).
        Then: ``is_cancel_requested`` returns ``False`` — a retry of
        the same record starts from a clean slate, not with a stale
        cancel flag from the previous run.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="pollingcancelcalc_2",
            calculation_id="calc-2",
            record="PollingCancelCalc(2)",
            model_label="lex_app.pollingcancelcalc",
            record_pk=2,
        )
        ActiveCalculationStateStore.request_cancel("pollingcancelcalc_2")
        ActiveCalculationStateStore.clear("pollingcancelcalc_2")
        self.assertFalse(
            ActiveCalculationStateStore.is_cancel_requested("pollingcancelcalc_2"),
            "clear() must drop the cancel flag along with the rest of the "
            "entry — otherwise a re-triggered calc would be cancelled "
            "before it even started.",
        )


# ---------------------------------------------------------------------
# 7.168 – 7.176 — End-to-end cancellation through the calculation lifecycle
# ---------------------------------------------------------------------


class TestCluster07n_CancellationLifecycle(E2ETestCase):
    """Drive cancellation through the public ``save()`` entry point.

    These tests exercise the integration path a real customer reaches:
    a model is saved with ``is_calculated=IN_PROGRESS`` (which kicks
    off the calculation), and either a polling ``calculate()`` body
    observes the cancel via ``check_cancelled`` or the state-guard at
    the SUCCESS write flips the terminal state for a non-polling body.

    The default ``mark_in_progress`` / ``ensure_terminal_calculation_audit``
    mocks are opted out so the real state store and real audit-log
    machinery participate — that's the entire point of these scenarios.
    """

    e2e_models = ALL_7N_MODELS
    e2e_unpatch = {"mark_in_progress", "ensure_terminal_calculation_audit"}

    def setUp(self) -> None:
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)
        self.broadcasts: list[tuple[str, dict]] = []
        patcher = patch(
            "lex.core.signals.CalculationSignals.sync_channel_group_send",
            side_effect=lambda group, message: self.broadcasts.append((group, message)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- 7.168 ---------------------------------------------------------
    def test_7_168_check_cancelled_is_noop_when_no_cancel_requested(self) -> None:
        """Scenario 7.168: ``check_cancelled`` is silent on the happy path.

        Given: a record actively calculating with no cancel pending.
        When: ``calculate()`` calls ``self.check_cancelled()`` on every
        iteration.
        Then: the calculation runs to completion (all iterations executed,
        terminal state SUCCESS) — ``check_cancelled`` does NOT raise
        spuriously.
        """
        calc = PollingCancelCalc(name="7n-168", target_iterations=4)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        fresh = PollingCancelCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.SUCCESS,
            "Without a cancel request, a polling calculate() must reach SUCCESS",
        )
        self.assertEqual(
            fresh.polled_iterations,
            4,
            "All target_iterations must execute when no cancel is pending; "
            "check_cancelled must not raise spuriously.",
        )

    # -- 7.169 ---------------------------------------------------------
    def test_7_169_polling_calculate_observes_cancel_and_aborts(self) -> None:
        """Scenario 7.169: pre-flagged cancel stops a polling calculate().

        Given: a polling ``calculate()`` and a cancel flag set on the
        record's state-store entry **before** ``save()`` runs the
        calculation.
        When: the record is saved with ``IN_PROGRESS``.
        Then: ``check_cancelled`` raises ``CalculationCancelled`` on the
        first poll; the framework settles the row as ``ABORTED`` (NOT
        ``ERROR``); the loop short-circuited (``polled_iterations < 5``).
        """
        calc = PollingCancelCalc(name="7n-169", target_iterations=5)
        calc.save(skip_hooks=True)  # commit the row first so pk exists

        # Pre-register the entry in the store and flag it for cancel,
        # mimicking what the REST endpoint would do for a record that
        # is about to start calculating.
        record_id = f"{calc._meta.model_name}_{calc.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        ActiveCalculationStateStore.request_cancel(record_id, requested_by="alice")

        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()  # runs calculate() which immediately observes the cancel

        fresh = PollingCancelCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.ABORTED,
            f"Cooperative cancel must settle the row as ABORTED, not "
            f"{fresh.is_calculated!r} — user cancellation is not an error.",
        )
        self.assertLess(
            fresh.polled_iterations,
            5,
            "calculate() loop must short-circuit on cancel; ran "
            f"{fresh.polled_iterations} of 5 iterations.",
        )

    # -- 7.170 ---------------------------------------------------------
    def test_7_170_aborted_terminal_state_broadcast(self) -> None:
        """Scenario 7.170: ABORTED transition emits ``calculation_aborted``.

        Given: a polling calculate() that gets cancelled (as in 7.169).
        When: the framework settles the row as ABORTED.
        Then: a ``calculation_aborted`` message is sent to the
        ``update_calculation_status`` channel group — that's how every
        connected UI clears the spinner immediately.
        """
        calc = PollingCancelCalc(name="7n-170", target_iterations=3)
        calc.save(skip_hooks=True)
        record_id = f"{calc._meta.model_name}_{calc.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        ActiveCalculationStateStore.request_cancel(record_id)

        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        aborted_messages = [
            msg for (_, msg) in self.broadcasts
            if msg.get("type") == "calculation_aborted"
            and msg.get("payload", {}).get("record_id") == record_id
        ]
        self.assertTrue(
            aborted_messages,
            f"Expected a calculation_aborted broadcast for {record_id!r}; "
            f"saw types {[m.get('type') for (_, m) in self.broadcasts]!r}",
        )

    # -- 7.171 ---------------------------------------------------------
    def test_7_171_state_guard_aborts_non_polling_calculation(self) -> None:
        """Scenario 7.171: state-guard flips SUCCESS → ABORTED on cancel.

        Given: a ``calculate()`` that does NOT poll
        ``check_cancelled`` (a customer who hasn't opted into
        cooperative cancellation yet) — and a cancel flagged on the
        store while the body is running.
        When: the body returns normally (would otherwise become SUCCESS).
        Then: the terminal state is ``ABORTED``, honouring the user's
        intent.  The body's work product (``completed=True``) is still
        recorded — the thread couldn't be hard-stopped — but the
        terminal state and the UI broadcast both reflect the cancel.
        """
        calc = NonPollingCancelCalc(name="7n-171")
        calc.save(skip_hooks=True)
        record_id = f"{calc._meta.model_name}_{calc.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        # User cancels before the calculation even starts; the body
        # runs to completion (no polling), but the state-guard at the
        # terminal write must override SUCCESS with ABORTED.
        ActiveCalculationStateStore.request_cancel(record_id, requested_by="bob")

        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        fresh = NonPollingCancelCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.ABORTED,
            "State-guard must flip SUCCESS → ABORTED when cancel was "
            "requested mid-flight, even if the body never polled.",
        )
        self.assertTrue(
            fresh.completed,
            "The body's work product survives (Python can't kill the thread); "
            "only the terminal state and broadcast reflect the user's cancel.",
        )

    # -- 7.172 ---------------------------------------------------------
    def test_7_172_no_cancel_pending_settles_success(self) -> None:
        """Scenario 7.172: state-guard is dormant when no cancel is pending.

        Given: a non-polling ``calculate()`` and an active state-store
        entry with NO cancel flag set.
        When: the body completes normally.
        Then: the terminal state is SUCCESS — the state-guard must not
        spuriously abort calculations that nobody cancelled.
        """
        calc = NonPollingCancelCalc(name="7n-172")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        fresh = NonPollingCancelCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.SUCCESS,
            "Without a cancel request, the state-guard must leave SUCCESS untouched.",
        )

    # -- 7.173 ---------------------------------------------------------
    def test_7_173_request_cancel_classmethod_routes_through_store(self) -> None:
        """Scenario 7.173: ``Model.request_cancel(instance)`` is the public surface.

        Given: an active calculation registered in the state store.
        When: ``PollingCancelCalc.request_cancel(instance, requested_by=…)``
        is called.
        Then: the cancel flag is set on the store entry — the framework
        exposes a single classmethod entry point instead of forcing every
        caller (REST view, management command, custom workflow) to know
        about ``ActiveCalculationStateStore``.
        """
        calc = PollingCancelCalc(name="7n-173")
        calc.save(skip_hooks=True)
        record_id = f"{calc._meta.model_name}_{calc.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )

        ok = PollingCancelCalc.request_cancel(calc, requested_by="carol")

        self.assertTrue(
            ok,
            "request_cancel must return True when there is an active entry to flag",
        )
        self.assertTrue(
            ActiveCalculationStateStore.is_cancel_requested(record_id),
            "Model.request_cancel must route through the store so check_cancelled "
            "polls observe the flag.",
        )
        self.assertEqual(
            ActiveCalculationStateStore.get_cancel_requested_by(record_id),
            "carol",
        )

    # -- 7.174 ---------------------------------------------------------
    def test_7_174_request_cancel_returns_false_when_not_running(self) -> None:
        """Scenario 7.174: cancelling an idle record is a benign no-op.

        Given: a saved record that is NOT currently calculating
        (no entry in the state store).
        When: ``Model.request_cancel(instance)`` is called.
        Then: it returns ``False`` and the record's persisted state is
        unchanged — a double-click on the Cancel button after the calc
        already finished must not raise.
        """
        calc = NonPollingCancelCalc(name="7n-174")
        calc.is_calculated = CalculationModel.NOT_CALCULATED
        calc.save(skip_hooks=True)

        ok = NonPollingCancelCalc.request_cancel(calc)

        self.assertFalse(
            ok,
            "request_cancel must return False when there is nothing to cancel",
        )
        fresh = NonPollingCancelCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.NOT_CALCULATED,
            "request_cancel must NOT touch the DB row's is_calculated field — "
            "the terminal transition belongs to the running calculation.",
        )

    # -- 7.175 ---------------------------------------------------------
    def test_7_175_check_cancelled_safe_on_unsaved_instance(self) -> None:
        """Scenario 7.175: ``check_cancelled`` on an unsaved instance is a no-op.

        Given: a model instance that has not been saved (``pk is None``).
        When: ``check_cancelled()`` is called.
        Then: it returns silently — there is no record_id to look up,
        so there cannot be a pending cancel.  The framework must not
        raise just because the instance hasn't been persisted yet.
        """
        calc = PollingCancelCalc(name="7n-175")
        # No save() — pk stays None.
        try:
            calc.check_cancelled()
        except Exception as exc:
            self.fail(
                f"check_cancelled() on unsaved instance must be a no-op; raised {exc!r}"
            )

    # -- 7.176 ---------------------------------------------------------
    def test_7_176_aborted_calculation_logs_aborted_audit_status(self) -> None:
        """Scenario 7.176: ABORTED transitions write ``status="aborted"`` audit.

        Given: a cancellation that settles a record as ABORTED (any
        route — cooperative ``check_cancelled`` raise or state-guard
        flip at SUCCESS).
        When: ``ensure_terminal_calculation_audit`` is invoked by the
        terminal-state finalizer.
        Then: it is called with ``audit_status="aborted"`` — the
        compliance log distinguishes user cancellation from
        ``success`` (clean) and ``failure`` (error).
        """
        captured = []

        def _spy(instance, *, audit_status, **kwargs):
            captured.append(audit_status)
            return None

        with patch(
            "lex.core.models.CalculationModel.ensure_terminal_calculation_audit",
            side_effect=_spy,
            create=True,
        ):
            # Patch the import-site as well — calculate_hook does a
            # local `from … import ensure_terminal_calculation_audit`.
            with patch(
                "lex.audit_logging.utils.calculation_audit"
                ".ensure_terminal_calculation_audit",
                side_effect=_spy,
            ):
                calc = NonPollingCancelCalc(name="7n-176")
                calc.save(skip_hooks=True)
                record_id = f"{calc._meta.model_name}_{calc.pk}"
                ActiveCalculationStateStore.mark_in_progress(
                    record_id=record_id,
                    calculation_id="",
                    record=str(calc),
                    model_label=calc._meta.label_lower,
                    record_pk=calc.pk,
                )
                ActiveCalculationStateStore.request_cancel(record_id)

                calc.is_calculated = CalculationModel.IN_PROGRESS
                calc.save()

        self.assertIn(
            "aborted",
            captured,
            "Aborted terminal state must invoke the audit hook with "
            f'audit_status="aborted"; observed statuses: {captured!r}',
        )

