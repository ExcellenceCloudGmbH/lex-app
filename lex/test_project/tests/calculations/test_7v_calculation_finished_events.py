"""Cluster 7v — the ``calculation_finished`` fleet-metrics event.

Intent: every root calculation's terminal state (SUCCESS, ERROR, CANCELLED,
ABORTED) must reach the Instance Controller as exactly one ``calculation_
finished`` event, however it got there — a synchronous save, a Celery
worker's success/failure callback, a user cancel, or the startup recovery
sweep aborting a stuck row. Missing one of those paths would hide that
failure mode from fleet-wide monitoring.

Two layers are tested:

* **Logic** (``TestCluster07v_EventLogic``, Type U) — ``emit_calculation_
  finished``'s own rules in isolation: outcome mapping, the usage-metrics
  gate, duration derivation, pass-through fields, the web/worker process
  tag, and the in-process LRU de-dupe. Driven with lightweight stand-ins
  (``SimpleNamespace``), no DB needed.
* **Integration** (``TestCluster07v_EndToEnd``, Type I) — the real hook
  inside ``ensure_terminal_calculation_audit`` fires for each terminal call
  site, with ``emitter.emit`` mocked so no network call is made and the
  captured events can be asserted on directly.

Cluster 7v — scenarios 7.238–7.261. Type: U/I.
Covers: lex/lex_app/fleet_metrics/calculations.py,
        lex/audit_logging/utils/calculation_audit.py (the emit hook),
        lex/lex_app/celery_tasks.py (the flush(5) call),
        lex/process_admin/utils/model_registration.py (the startup-abort lookup).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7v_calculation_finished_events.py -v
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import SimpleTestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.core.models.CalculationModel import CalculationModel
from lex.api.utils.Context import operation_context
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.lex_app.celery_tasks import CallbackTask
from lex.lex_app.fleet_metrics import calculations as fleet_calculations
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AuditColumnsCalc, AuditColumnsCancelCalc, AuditColumnsFailingCalc

pytestmark = pytest.mark.calculations


def _audit_log(pk=1, calculation_id="order_12_update_6f1c", resource="order"):
    """A minimal stand-in for an ``AuditLog`` row — only attribute access,
    never an isinstance check, happens inside ``calculations.py``."""
    return SimpleNamespace(pk=pk, calculation_id=calculation_id, resource=resource)


def _status_row(created_at=None, updated_at=None, freshly_created=False):
    row = SimpleNamespace(created_at=created_at, updated_at=updated_at)
    row._freshly_created = freshly_created
    return row


#: Mirrors lex.api.utils.Context._DEFAULT_CONTEXT -- not imported directly
#: since the leading underscore marks it as that module's own internal detail.
_DEFAULT_OPERATION_CONTEXT = {
    "operation_id": "",
    "request_obj": "",
    "calculation_id": "",
    "audit_log_temp": None,
}


class TestCluster07v_EventLogic(SimpleTestCase):
    """Cluster 7v: ``emit_calculation_finished``'s own rules, in isolation."""

    def setUp(self):
        super().setUp()
        fleet_calculations._reset_for_tests()
        self._emit_patch = mock.patch("lex.lex_app.fleet_metrics.emitter.emit")
        self.emit_mock = self._emit_patch.start()
        self.addCleanup(self._emit_patch.stop)
        # usage_metrics_enabled is imported by name into calculations.py, so
        # the re-exported symbol must be patched there, not at its source
        # (same rule test_8g documents for ensure_terminal_calculation_audit).
        self._usage_patch = mock.patch(
            "lex.lex_app.fleet_metrics.calculations.usage_metrics_enabled",
            return_value=True,
        )
        self.usage_mock = self._usage_patch.start()
        self.addCleanup(self._usage_patch.stop)
        self.addCleanup(fleet_calculations._reset_for_tests)

    # -- 7.238 — outcome mapping -----------------------------------------
    def test_7_238_audit_status_maps_to_outcome(self):
        """
        Scenario 7.238: each finaliser audit_status maps to its event outcome.
        Given: the four audit_status strings ensure_terminal_calculation_audit writes
        When: emit_calculation_finished is called with each
        Then: the emitted event's "outcome" is SUCCESS/ERROR/CANCELLED/ABORTED respectively
        """
        expected = {
            "success": "SUCCESS",
            "failure": "ERROR",
            "cancelled": "CANCELLED",
            "aborted": "ABORTED",
        }
        for audit_status, outcome in expected.items():
            with self.subTest(audit_status=audit_status):
                self.emit_mock.reset_mock()
                fleet_calculations._reset_for_tests()
                fleet_calculations.emit_calculation_finished(
                    _audit_log(pk=f"pk-{audit_status}"), _status_row(), audit_status,
                )
                self.emit_mock.assert_called_once()
                (event,), _ = self.emit_mock.call_args
                self.assertEqual(
                    event["outcome"], outcome,
                    f"audit_status={audit_status!r} must map to outcome={outcome!r}; "
                    f"got {event['outcome']!r}.",
                )

    # -- 7.239 — unmapped status -------------------------------------------
    def test_7_239_unmapped_audit_status_emits_nothing(self):
        """
        Scenario 7.239: an unrecognised audit_status is silently dropped.
        Given: an audit_status outside the four mapped values
        When: emit_calculation_finished is called with it
        Then: no event is emitted and nothing raises
        """
        fleet_calculations.emit_calculation_finished(_audit_log(), _status_row(), "pending")
        self.emit_mock.assert_not_called()

    # -- 7.240 — usage-metrics gate ------------------------------------------
    def test_7_240_usage_metrics_off_suppresses_every_outcome(self):
        """
        Scenario 7.240: the usage-metrics switch gates calculation events too.
        Given: usage_metrics_enabled() is False
        When: emit_calculation_finished is called with a normally-mapped audit_status
        Then: no event is emitted — a disabled instance still sends heartbeats,
              just no calculation events
        """
        self.usage_mock.return_value = False
        fleet_calculations.emit_calculation_finished(_audit_log(), _status_row(), "success")
        self.emit_mock.assert_not_called()

    # -- 7.241 -- defensive: no pk yet ----------------------------------------
    def test_7_241_audit_log_without_a_pk_is_a_no_op(self):
        """
        Scenario 7.241: an unsaved AuditLog (no pk) never reaches the emitter.
        Given: an audit_log stand-in whose pk is None
        When: emit_calculation_finished is called
        Then: nothing is emitted — there is no audit_log_id to key the event on
        """
        fleet_calculations.emit_calculation_finished(
            _audit_log(pk=None), _status_row(), "success",
        )
        self.emit_mock.assert_not_called()

    # -- 7.242 — real duration -------------------------------------------
    def test_7_242_duration_is_updated_at_minus_created_at(self):
        """
        Scenario 7.242: duration_seconds is the status row's real span.
        Given: a status_row with distinct created_at/updated_at, not freshly created
        When: emit_calculation_finished runs
        Then: duration_seconds == (updated_at - created_at).total_seconds()
        """
        created = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
        updated = created + timedelta(seconds=252, microseconds=400_000)
        fleet_calculations.emit_calculation_finished(
            _audit_log(), _status_row(created, updated, freshly_created=False), "success",
        )
        (event,), _ = self.emit_mock.call_args
        self.assertAlmostEqual(
            event["duration_seconds"], 252.4, places=3,
            msg=f"duration_seconds must be updated_at - created_at; got {event['duration_seconds']!r}.",
        )

    # -- 7.243 — null duration when freshly created -----------------------
    def test_7_243_duration_is_null_when_finaliser_created_the_row(self):
        """
        Scenario 7.243: duration is null when there was no earlier row to time from.
        Given: a status_row ensure_terminal_calculation_audit marks _freshly_created
        When: emit_calculation_finished runs
        Then: duration_seconds is None, even though created_at/updated_at are set
        """
        created = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
        fleet_calculations.emit_calculation_finished(
            _audit_log(), _status_row(created, created, freshly_created=True), "success",
        )
        (event,), _ = self.emit_mock.call_args
        self.assertIsNone(
            event["duration_seconds"],
            "duration_seconds must be null when the finaliser created the "
            "audit row itself — the start time is unknown, not zero.",
        )

    # -- 7.244 — pass-through fields ---------------------------------------
    def test_7_244_placeholder_calculation_id_and_resource_pass_through(self):
        """
        Scenario 7.244: calculation_id and resource are forwarded unchanged.
        Given: an audit_log whose calculation_id is an opaque placeholder string
        When: emit_calculation_finished runs
        Then: the event's calculation_id / resource are byte-identical, never
              reformatted, validated or re-cased
        """
        fleet_calculations.emit_calculation_finished(
            _audit_log(pk=184233, calculation_id="placeholder-not-a-real-id///", resource="Order"),
            _status_row(),
            "success",
        )
        (event,), _ = self.emit_mock.call_args
        self.assertEqual(event["calculation_id"], "placeholder-not-a-real-id///")
        self.assertEqual(event["resource"], "Order")
        self.assertEqual(event["audit_log_id"], 184233)

    # -- 7.245 — process tag -------------------------------------------------
    def test_7_245_process_follows_running_in_uvicorn(self):
        """
        Scenario 7.245: process is "web" only inside the uvicorn web process.
        Given: running_in_uvicorn() answering True, then False
        When: emit_calculation_finished runs each time
        Then: the event's "process" is "web", then "worker"
        """
        with mock.patch("lex.lex_app.apps.running_in_uvicorn", return_value=True):
            fleet_calculations.emit_calculation_finished(
                _audit_log(pk="web-case"), _status_row(), "success",
            )
        (event,), _ = self.emit_mock.call_args
        self.assertEqual(event["process"], "web")

        fleet_calculations._reset_for_tests()
        self.emit_mock.reset_mock()
        with mock.patch("lex.lex_app.apps.running_in_uvicorn", return_value=False):
            fleet_calculations.emit_calculation_finished(
                _audit_log(pk="worker-case"), _status_row(), "success",
            )
        (event,), _ = self.emit_mock.call_args
        self.assertEqual(event["process"], "worker")

    # -- 7.246 / 7.247 — in-process LRU de-dupe -----------------------------
    def test_7_246_same_audit_log_and_outcome_emits_only_once(self):
        """
        Scenario 7.246: a repeat (audit_log.pk, outcome) call is de-duped.
        Given: the finaliser runs twice for the same root run in one process
               (e.g. a sync cancel reaching both CalculationModel and
               LexModel._finalize_pending_terminal_audit)
        When: emit_calculation_finished is called twice with the same pk/outcome
        Then: the emitter only ever sees one event
        """
        audit_log = _audit_log(pk=42)
        fleet_calculations.emit_calculation_finished(audit_log, _status_row(), "cancelled")
        fleet_calculations.emit_calculation_finished(audit_log, _status_row(), "cancelled")
        self.emit_mock.assert_called_once()

    def test_7_247_same_pk_different_outcome_is_not_deduped(self):
        """
        Scenario 7.247: the de-dupe key is (pk, outcome), not pk alone.
        Given: the same audit_log.pk with two different outcomes
        When: emit_calculation_finished is called once per outcome
        Then: both events are emitted — a later distinct outcome for the same
              run must never be swallowed by an earlier one's de-dupe entry
        """
        audit_log = _audit_log(pk=43)
        fleet_calculations.emit_calculation_finished(audit_log, _status_row(), "success")
        fleet_calculations.emit_calculation_finished(audit_log, _status_row(), "failure")
        self.assertEqual(self.emit_mock.call_count, 2)

    # -- 7.248 — bounded LRU -------------------------------------------------
    def test_7_248_dedupe_cache_is_bounded_and_evicts_oldest(self):
        """
        Scenario 7.248: the de-dupe cache never grows past 10,000 entries.
        Given: 10,001 distinct (pk, outcome) pairs emitted in order
        When: the first pair is seen again
        Then: it is treated as new (its entry was evicted) — proving the
              cache is bounded rather than growing forever
        """
        first = _audit_log(pk="evict-0")
        fleet_calculations.emit_calculation_finished(first, _status_row(), "success")
        for i in range(1, 10_001):
            fleet_calculations.emit_calculation_finished(
                _audit_log(pk=f"evict-{i}"), _status_row(), "success",
            )
        self.assertEqual(
            len(fleet_calculations._dedupe_seen), fleet_calculations._DEDUPE_MAX_SIZE,
            "the de-dupe cache must stay bounded at _DEDUPE_MAX_SIZE entries.",
        )
        self.emit_mock.reset_mock()
        fleet_calculations.emit_calculation_finished(first, _status_row(), "success")
        self.emit_mock.assert_called_once()  # evicted → treated as new, re-emitted

    # -- 7.249 — event shape --------------------------------------------
    def test_7_249_event_shape_and_aware_utc_timestamp(self):
        """
        Scenario 7.249: the event's static shape.
        Given: any emitted calculation_finished event
        When: its fields are inspected
        Then: type == "calculation_finished" and occurred_at parses as an
              aware, UTC-offset-zero ISO-8601 timestamp
        """
        fleet_calculations.emit_calculation_finished(_audit_log(), _status_row(), "success")
        (event,), _ = self.emit_mock.call_args
        self.assertEqual(event["type"], "calculation_finished")
        parsed = datetime.fromisoformat(event["occurred_at"])
        self.assertIsNotNone(parsed.tzinfo, "occurred_at must be timezone-aware.")
        self.assertEqual(
            parsed.utcoffset(), timedelta(0),
            "occurred_at must be an aware UTC timestamp.",
        )


class TestCluster07v_EndToEnd(E2ETestCase):
    """Cluster 7v: the real hook fires exactly once per terminal call site."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    e2e_unpatch = {"ensure_terminal_calculation_audit"}

    def setUp(self):
        super().setUp()
        fleet_calculations._reset_for_tests()
        self._emit_patch = mock.patch("lex.lex_app.fleet_metrics.emitter.emit")
        self.emit_mock = self._emit_patch.start()
        self.addCleanup(self._emit_patch.stop)
        self.addCleanup(fleet_calculations._reset_for_tests)
        # Pin the usage-metrics switch on for both call sites that read it
        # (each imported the name directly, so the source module can't be
        # patched from here) -- a developer's local .env with
        # LEX_USAGE_METRICS=false must not turn these tests red.
        self._usage_patches = [
            mock.patch(
                "lex.lex_app.fleet_metrics.calculations.usage_metrics_enabled",
                return_value=True,
            ),
            mock.patch(
                "lex.audit_logging.utils.calculation_audit.usage_metrics_enabled",
                return_value=True,
            ),
        ]
        for p in self._usage_patches:
            p.start()
            self.addCleanup(p.stop)
        AuditLog.objects.all().delete()
        # ActiveCalculationStateStore is a process-global, not reset by
        # TransactionTestCase's table flush+PK reset between tests. Left
        # alone, an earlier test's tracked record_id ("<model>_<pk>") can
        # collide with this test's freshly reset auto-increment pk and
        # leak a resolvable calculation_id into a scenario that must not
        # have one (7.255). Clear it so every test starts from empty,
        # matching the store's own real-world "empty on server start"
        # contract (ActiveCalculationStateStore's docstring).
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)
        # ensure_terminal_calculation_audit's _update_operation_context_audit_log
        # helper does an unscoped operation_context.set(...) with no token/reset
        # (pre-existing behaviour, calculation_audit.py) -- a direct call to the
        # function (7.256/7.257) leaks a stale audit_log_temp into whichever test
        # runs next on this thread. Reset to the module's own documented default
        # so each test here starts from the same clean slate a fresh process would.
        operation_context.set(dict(_DEFAULT_OPERATION_CONTEXT))
        self.addCleanup(operation_context.set, dict(_DEFAULT_OPERATION_CONTEXT))

    def _run_calc(self, model_cls, pk):
        """Trigger the calculation the way project code does, then settle.

        Wrapped in ``operation_context`` so ``_resolve_calculation_id``
        (calculation_audit.py) has a calculation_id to resolve — exactly
        what the REST API's ``OperationContext(request, calculationId)``
        wrapper supplies in production (``views/model_entries/One.py``).
        Without it the finaliser's root-run guard legitimately has nothing
        to key an AuditLog on and returns early, same as it would for any
        other caller that skipped that context.
        """
        obj = model_cls.objects.get(pk=pk)
        obj.is_calculated = CalculationModel.IN_PROGRESS
        with self.operation_context(f"calc_{pk}"):
            try:
                obj.save()
            except Exception:
                pass  # ERROR / CANCELLED surface to the caller; state still persists
        time.sleep(0.5)

    def _captured_events(self):
        return [call.args[0] for call in self.emit_mock.call_args_list]

    # -- 7.250 — sync success ------------------------------------------
    def test_7_250_sync_success_emits_one_success_event(self):
        """
        Scenario 7.250: a successful sync calculation emits one SUCCESS event.
        """
        calc = AuditColumnsCalc.objects.create(name="v250")
        self._run_calc(AuditColumnsCalc, calc.pk)

        events = self._captured_events()
        self.assertEqual(len(events), 1, f"expected exactly one event, got {len(events)}.")
        self.assertEqual(events[0]["outcome"], "SUCCESS")
        self.assertEqual(events[0]["resource"], "auditcolumnscalc")

    # -- 7.251 — sync error ---------------------------------------------
    def test_7_251_sync_error_emits_one_error_event(self):
        """
        Scenario 7.251: a failing sync calculation emits one ERROR event.
        """
        calc = AuditColumnsFailingCalc.objects.create(name="v251")
        self._run_calc(AuditColumnsFailingCalc, calc.pk)

        events = self._captured_events()
        self.assertEqual(len(events), 1, f"expected exactly one event, got {len(events)}.")
        self.assertEqual(events[0]["outcome"], "ERROR")

    # -- 7.252 — sync cancel ----------------------------------------------
    def test_7_252_sync_cancel_emits_one_cancelled_event(self):
        """
        Scenario 7.252: a cooperatively cancelled calculation emits one
        CANCELLED event — not two, even though the cancel path can reach
        ensure_terminal_calculation_audit from more than one caller in the
        same process (the in-process de-dupe collapses them).
        """
        calc = AuditColumnsCancelCalc.objects.create(name="v252")
        self._run_calc(AuditColumnsCancelCalc, calc.pk)

        events = self._captured_events()
        self.assertEqual(len(events), 1, f"expected exactly one event, got {len(events)}.")
        self.assertEqual(events[0]["outcome"], "CANCELLED")

    # -- 7.253 — Celery success --------------------------------------------
    def test_7_253_celery_success_callback_emits_one_success_event(self):
        """
        Scenario 7.253: CallbackTask.on_success emits one SUCCESS event.
        Driven directly against a real persisted row — no broker needed
        (mirrors test_8g's pattern for CallbackTask).
        """
        calc = AuditColumnsCalc.objects.create(name="v253", is_calculated=CalculationModel.NOT_CALCULATED)
        AuditColumnsCalc.objects.filter(pk=calc.pk).update(is_calculated=CalculationModel.IN_PROGRESS)

        task = CallbackTask()
        task.name = "fleet_metrics_test_task"
        task.on_success(
            retval=None, task_id="task-7-253", args=(calc,),
            kwargs={"context": {"calculation_id": f"calc_{calc.pk}"}, "model_context": None},
        )

        events = self._captured_events()
        self.assertEqual(len(events), 1, f"expected exactly one event, got {len(events)}.")
        self.assertEqual(events[0]["outcome"], "SUCCESS")

    # -- 7.254 — Celery failure --------------------------------------------
    def test_7_254_celery_failure_callback_emits_one_error_event(self):
        """
        Scenario 7.254: CallbackTask.on_failure emits one ERROR event.
        """
        calc = AuditColumnsCalc.objects.create(name="v254", is_calculated=CalculationModel.NOT_CALCULATED)
        AuditColumnsCalc.objects.filter(pk=calc.pk).update(is_calculated=CalculationModel.IN_PROGRESS)

        task = CallbackTask()
        task.name = "fleet_metrics_test_task"
        task.on_failure(
            exc=RuntimeError("boom"), task_id="task-7-254", args=(calc,),
            kwargs={"context": {"calculation_id": f"calc_{calc.pk}"}, "model_context": None}, einfo=None,
        )

        events = self._captured_events()
        self.assertEqual(len(events), 1, f"expected exactly one event, got {len(events)}.")
        self.assertEqual(events[0]["outcome"], "ERROR")

    # -- 7.255 — startup abort finalises the interrupted run's own row -----
    def test_7_255_startup_abort_sweep_finalises_the_pending_audit_row(self):
        """
        Scenario 7.255: the startup recovery sweep emits one ABORTED event
        for a stuck IN_PROGRESS row, using the pending AuditLog the
        interrupted run left behind when it started.
        """
        from lex.audit_logging.utils.content_types import safe_get_content_type

        calc = AuditColumnsCalc.objects.create(name="v255")
        pending_log = AuditLog.objects.create(
            author="system", resource="auditcolumnscalc", action="update",
            calculation_id=f"calc_{calc.pk}",
            content_type=safe_get_content_type(AuditColumnsCalc), object_id=calc.pk,
        )
        pending_status = AuditLogStatus.objects.create(audit_log=pending_log, status="pending")
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        AuditLogStatus.objects.filter(pk=pending_status.pk).update(created_at=start)

        AuditColumnsCalc.objects.filter(pk=calc.pk).update(is_calculated=CalculationModel.IN_PROGRESS)

        with mock.patch.dict(os.environ, {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(
                AuditColumnsCalc, tracked_record_ids=set()
            )

        events = self._captured_events()
        self.assertEqual(len(events), 1, f"expected exactly one event, got {len(events)}.")
        self.assertEqual(events[0]["outcome"], "ABORTED")
        self.assertEqual(
            events[0]["audit_log_id"], pending_log.pk,
            "the event must carry the interrupted run's own pending AuditLog, "
            "not a fresh, unrelated one.",
        )
        self.assertIsNotNone(
            events[0]["duration_seconds"],
            "the pending row's real created_at gives a measurable duration, not null.",
        )
        self.assertGreater(events[0]["duration_seconds"], 4.0)

        final_status = AuditLogStatus.objects.get(pk=pending_status.pk)
        self.assertEqual(
            final_status.status, "aborted",
            "the sweep must finalise the interrupted run's own status row to "
            "'aborted', not leave it 'pending' forever.",
        )

    # -- 7.259 — startup abort, nothing to link to --------------------------
    def test_7_259_startup_abort_without_a_pending_row_emits_nothing(self):
        """
        Scenario 7.259: a stuck row with no prior audit trail at all still
        produces no event — there is nothing to key it on.
        """
        calc = AuditColumnsCalc.objects.create(name="v259")
        AuditColumnsCalc.objects.filter(pk=calc.pk).update(is_calculated=CalculationModel.IN_PROGRESS)

        with mock.patch.dict(os.environ, {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(
                AuditColumnsCalc, tracked_record_ids=set()
            )

        self.assertEqual(
            self._captured_events(), [],
            "with no pending AuditLog to find and no calculation_id to "
            "resolve, the sweep has nothing to key an event on.",
        )

    # -- 7.260 — a raising hook must not break the finaliser ----------------
    def test_7_260_a_raising_emit_hook_leaves_the_finaliser_intact(self):
        """
        Scenario 7.260: if the emit hook itself raises, ensure_terminal_
        calculation_audit still returns its AuditLog, row committed.
        """
        from lex.audit_logging.utils.calculation_audit import ensure_terminal_calculation_audit

        calc = AuditColumnsCalc.objects.create(name="v260")
        self.emit_mock.side_effect = RuntimeError("boom")

        result = ensure_terminal_calculation_audit(
            calc, audit_status="success", context_data={"calculation_id": f"calc_{calc.pk}"},
        )

        self.assertIsNotNone(
            result,
            "a raising emit hook must not stop the finaliser from returning its AuditLog.",
        )
        self.assertTrue(
            AuditLog.objects.filter(pk=result.pk).exists(),
            "the AuditLog row must still be committed even though the emit hook raised.",
        )

    # -- 7.261 — a nested (non-root) run sends nothing -----------------------
    def test_7_261_a_nested_child_run_sends_no_event(self):
        """
        Scenario 7.261: a child calculation nested under a root run is not
        itself a root run, so it never reaches the emit hook.
        """
        from lex.audit_logging.utils.ModelContext import ModelContext
        from lex.audit_logging.utils.calculation_audit import ensure_terminal_calculation_audit

        root = AuditColumnsCalc(pk=101, name="root")
        child = AuditColumnsCalc(pk=102, name="child")
        nested_context = ModelContext([root, child])

        result = ensure_terminal_calculation_audit(
            child, audit_status="success", model_context=nested_context,
        )

        self.assertIsNone(
            result, "a non-root instance must return None before any audit write or emit.",
        )
        self.emit_mock.assert_not_called()

    # -- 7.256 — real null duration --------------------------------------
    def test_7_256_duration_is_null_when_no_prior_audit_row_existed(self):
        """
        Scenario 7.256: end-to-end, a first-ever terminal audit call (no
        'pending' row from calc start) reports a null duration.
        """
        from lex.audit_logging.utils.calculation_audit import ensure_terminal_calculation_audit

        calc = AuditColumnsCalc.objects.create(name="v256")

        ensure_terminal_calculation_audit(
            calc, audit_status="success", context_data={"calculation_id": f"calc_{calc.pk}"},
        )

        events = self._captured_events()
        self.assertEqual(len(events), 1, f"expected exactly one event, got {len(events)}.")
        self.assertIsNone(
            events[0]["duration_seconds"],
            "no prior pending AuditLogStatus existed, so the start time is "
            "unknown and duration_seconds must be null, not ~0.",
        )

    # -- 7.257 — real nonzero duration ------------------------------------
    def test_7_257_duration_is_real_span_when_a_pending_row_pre_existed(self):
        """
        Scenario 7.257: end-to-end, a pre-existing 'pending' status row (as
        the calc-start path writes) gives a real, measured duration.
        """
        from lex.audit_logging.utils.calculation_audit import ensure_terminal_calculation_audit

        calc = AuditColumnsCalc.objects.create(name="v257")
        audit_log = AuditLog.objects.create(
            author="system", resource="auditcolumnscalc", action="update",
            calculation_id=f"calc_{calc.pk}", object_id=calc.pk,
        )
        pending = AuditLogStatus.objects.create(audit_log=audit_log, status="pending")
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        AuditLogStatus.objects.filter(pk=pending.pk).update(created_at=start)

        ensure_terminal_calculation_audit(
            calc, audit_status="success", context_data={"calculation_id": f"calc_{calc.pk}"},
        )

        events = self._captured_events()
        self.assertEqual(len(events), 1, f"expected exactly one event, got {len(events)}.")
        final_row = AuditLogStatus.objects.get(pk=pending.pk)
        expected = (final_row.updated_at - final_row.created_at).total_seconds()
        self.assertAlmostEqual(
            events[0]["duration_seconds"], expected, places=3,
            msg="duration_seconds must equal the real pending-row span, not null.",
        )
        self.assertGreater(
            events[0]["duration_seconds"], 4.0,
            "the pre-seeded 5s-old pending row must produce a real, "
            "measurable duration — not a near-zero one.",
        )

    # -- 7.258 — Celery worker flush ----------------------------------------
    def test_7_258_update_model_status_flushes_the_emitter(self):
        """
        Scenario 7.258: _update_model_status drains the emitter before
        returning, so a worker's event leaves before SIGTERM.
        """
        calc = AuditColumnsCalc.objects.create(name="v258", is_calculated=CalculationModel.NOT_CALCULATED)
        AuditColumnsCalc.objects.filter(pk=calc.pk).update(is_calculated=CalculationModel.IN_PROGRESS)

        with mock.patch("lex.lex_app.fleet_metrics.emitter.flush") as flush_mock:
            task = CallbackTask()
            task.name = "fleet_metrics_test_task"
            task._update_model_status(
                calc, CalculationModel.SUCCESS, task_id="task-7-258",
                context_data={"calculation_id": f"calc_{calc.pk}"},
            )

        flush_mock.assert_called_once_with(5)
