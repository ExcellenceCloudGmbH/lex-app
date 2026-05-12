"""
Tests for ``update_calculation_status`` — the authoritative signal that
broadcasts calculation state transitions and keeps
``ActiveCalculationStateStore`` in sync.

Why this matters
----------------
Every IN_PROGRESS / SUCCESS / ERROR / ABORTED transition on a
``CalculationModel`` instance flows through this function. The frontend
relies on the broadcast to update its spinners across tabs, and the
``ActiveCalculationStateStore`` is the authoritative source for
post-reconnect WebSocket reconciliation. A bug here means either a
stuck spinner in the UI or a duplicated calculation in the reconciliation
payload — both customer-visible.

What we assert
--------------
1. IN_PROGRESS registers the record in the store with the current
   ``operation_context`` calculation_id and emits a
   ``calculation_in_progress`` channel message.
2. Every IN_PROGRESS broadcast fires — the design explicitly never
   suppresses duplicates (the frontend handles them idempotently, and
   suppression previously caused multi-tab drift; see the docstring on
   ``update_calculation_status`` in ``CalculationSignals.py``).
3. SUCCESS / ERROR clear the store entry and broadcast the terminal
   message.
4. When ``operation_context`` has no calculation_id, the store's
   previously-written entry is preserved.

Why we patch ``sync_channel_group_send`` and not the channel layer
------------------------------------------------------------------
``update_calculation_status`` calls ``sync_channel_group_send`` which
owns the channel-layer boundary (ASGI vs Celery-worker dispatch). That
helper is the true boundary — we patch it to capture broadcast calls
and assert on the payload. We do NOT mock ``ActiveCalculationStateStore``:
it is part of the behavior we are asserting.
"""

from unittest.mock import patch

from django.test import SimpleTestCase
from lex.api.utils import operation_context
from lex.core.models.CalculationModel import CalculationModel, CalculationModelException
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.core.signals.CalculationSignals import (
    update_calculation_status,
    _resolve_calculation_id,
)


class DummyCalculationModel(CalculationModel):
    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):
        return None


class CalculationStatusSignalTests(SimpleTestCase):
    """
    Exercise ``update_calculation_status`` against the real
    ``ActiveCalculationStateStore`` (in-memory, so safe in
    ``SimpleTestCase``) with the channel-layer send boundary mocked.
    """

    def setUp(self):
        super().setUp()
        # Ensure a clean store — this is a process-global class attribute.
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)

        self._broadcasts = []

        def _capture(group, message):
            self._broadcasts.append((group, message))

        patcher = patch(
            "lex.core.signals.CalculationSignals.sync_channel_group_send",
            side_effect=_capture,
        )
        self._send_mock = patcher.start()
        self.addCleanup(patcher.stop)

        self._set_context("")
        self.addCleanup(self._set_context, "")

    @staticmethod
    def _set_context(calculation_id):
        operation_context.set(
            {
                "operation_id": "test-op",
                "request_obj": {},
                "calculation_id": calculation_id,
                "audit_log_temp": None,
            }
        )

    @staticmethod
    def _build_instance(pk, status=CalculationModel.IN_PROGRESS):
        instance = DummyCalculationModel(id=pk)
        instance.is_calculated = status
        return instance

    # ------------------------------------------------------------------
    # IN_PROGRESS
    # ------------------------------------------------------------------

    def test_in_progress_registers_store_entry_and_broadcasts(self):
        self._set_context("calc-1")
        instance = self._build_instance(1)

        update_calculation_status(instance)

        self.assertEqual(len(self._broadcasts), 1)
        group, message = self._broadcasts[0]
        self.assertEqual(group, "update_calculation_status")
        self.assertEqual(message["type"], "calculation_in_progress")
        self.assertEqual(message["payload"]["calculation_id"], "calc-1")
        self.assertEqual(
            message["payload"]["record_id"],
            f"{instance._meta.model_name}_{instance.id}",
        )

        entry = ActiveCalculationStateStore.get_entry(
            f"{instance._meta.model_name}_{instance.id}"
        )
        self.assertEqual(entry["calculation_id"], "calc-1")
        self.assertEqual(entry["record_pk"], str(instance.id))

    def test_in_progress_always_rebroadcasts(self):
        """
        Regression: the old implementation suppressed duplicate
        IN_PROGRESS broadcasts when the ``calculation_id`` was the
        same. The current design (see ``update_calculation_status``
        docstring) explicitly NEVER suppresses — the frontend is
        idempotent and multi-tab clients need every event. This test
        pins the new contract so a future "optimization" can't silently
        regress it.
        """
        self._set_context("calc-dup")
        instance = self._build_instance(2)

        update_calculation_status(instance)
        update_calculation_status(instance)

        self.assertEqual(len(self._broadcasts), 2)
        self.assertEqual(
            self._broadcasts[0][1]["payload"]["calculation_id"], "calc-dup"
        )
        self.assertEqual(
            self._broadcasts[1][1]["payload"]["calculation_id"], "calc-dup"
        )

    def test_in_progress_without_context_preserves_stored_calculation_id(self):
        """
        Once a record is registered in the store with a calculation_id,
        a subsequent IN_PROGRESS broadcast from a context that lost the
        id must not overwrite the store entry back to empty — the
        store's existing id is the fallback in ``_resolve_calculation_id``.
        """
        instance = self._build_instance(3)
        record_id = f"{instance._meta.model_name}_{instance.id}"

        self._set_context("calc-3")
        update_calculation_status(instance)

        self._set_context("")
        update_calculation_status(instance)

        # Both broadcasts fire (design: never suppress), and both carry
        # the original calculation_id via the store fallback.
        self.assertEqual(len(self._broadcasts), 2)
        self.assertEqual(
            self._broadcasts[0][1]["payload"]["calculation_id"], "calc-3"
        )
        self.assertEqual(
            self._broadcasts[1][1]["payload"]["calculation_id"], "calc-3"
        )
        self.assertEqual(
            ActiveCalculationStateStore.get_entry(record_id).get("calculation_id"),
            "calc-3",
        )

    # ------------------------------------------------------------------
    # Terminal states
    # ------------------------------------------------------------------

    def test_success_clears_store_entry_and_broadcasts(self):
        self._set_context("calc-4")
        instance = self._build_instance(4)
        record_id = f"{instance._meta.model_name}_{instance.id}"

        # Prime the store with an IN_PROGRESS entry.
        update_calculation_status(instance)
        self.assertIn("calculation_id", ActiveCalculationStateStore.get_entry(record_id))

        # Now mark SUCCESS.
        instance.is_calculated = CalculationModel.SUCCESS
        update_calculation_status(instance)

        self.assertEqual(len(self._broadcasts), 2)
        self.assertEqual(self._broadcasts[-1][1]["type"], "calculation_success")
        self.assertEqual(ActiveCalculationStateStore.get_entry(record_id), {})

    def test_error_clears_store_and_propagates_exception_details(self):
        self._set_context("calc-5")
        instance = self._build_instance(5)
        record_id = f"{instance._meta.model_name}_{instance.id}"

        update_calculation_status(instance)

        instance.is_calculated = CalculationModel.ERROR
        update_calculation_status(
            instance,
            exception_details="boom",
            stack_trace="traceback-here",
        )

        self.assertEqual(len(self._broadcasts), 2)
        error_msg = self._broadcasts[-1][1]
        self.assertEqual(error_msg["type"], "calculation_error")
        self.assertEqual(error_msg["payload"]["message"], "boom")
        self.assertEqual(error_msg["payload"]["traceback"], "traceback-here")
        self.assertEqual(ActiveCalculationStateStore.get_entry(record_id), {})

    def test_non_calculationmodel_instance_is_noop(self):
        """
        Defensive guard: if something other than a CalculationModel
        subclass is handed to the signal, return early without
        broadcasting or touching the store.
        """

        class NotACalculationModel:
            is_calculated = CalculationModel.IN_PROGRESS
            _meta = None
            id = 7

        update_calculation_status(NotACalculationModel())
        self.assertEqual(self._broadcasts, [])

    def test_aborted_clears_store_and_broadcasts(self):
        """ABORTED → clears the entry and sends ``calculation_aborted``."""
        self._set_context("calc-abort")
        instance = self._build_instance(10)
        record_id = f"{instance._meta.model_name}_{instance.id}"

        update_calculation_status(instance)  # IN_PROGRESS

        instance.is_calculated = CalculationModel.ABORTED
        update_calculation_status(instance)

        self.assertEqual(len(self._broadcasts), 2)
        self.assertEqual(self._broadcasts[-1][1]["type"], "calculation_aborted")
        self.assertEqual(ActiveCalculationStateStore.get_entry(record_id), {})

    def test_unknown_status_no_broadcast(self):
        """An unrecognised ``is_calculated`` value does not broadcast."""
        instance = self._build_instance(11)
        instance.is_calculated = "TOTALLY_UNKNOWN"

        update_calculation_status(instance)
        self.assertEqual(self._broadcasts, [])

    def test_calculation_id_from_instance_attribute(self):
        """Falls back to ``instance.calculation_id`` when context + store are empty."""
        self._set_context("")  # no context calc id
        instance = self._build_instance(12)
        instance.calculation_id = "attr-fallback-id"

        update_calculation_status(instance)

        payload = self._broadcasts[0][1]["payload"]
        self.assertEqual(payload["calculation_id"], "attr-fallback-id")

    def test_no_calculation_id_omits_key_from_payload(self):
        """When no calculation_id is resolved, payload has no ``calculation_id`` key."""
        self._set_context("")
        instance = self._build_instance(13)

        update_calculation_status(instance)

        payload = self._broadcasts[0][1]["payload"]
        self.assertNotIn("calculation_id", payload)


class CalculationExceptionUtilityTests(SimpleTestCase):
    def test_calculation_model_exception_string_uses_actual_detail(self):
        exception = CalculationModelException(
            exception_details=["SharePoint Server cannot handle requests at the moment."]
        )

        self.assertEqual(
            str(exception),
            "SharePoint Server cannot handle requests at the moment.",
        )

    def test_build_exception_chain_recovers_nested_calculation_exception(self):
        instance = DummyCalculationModel(id=41)
        child_instance = DummyCalculationModel(id=410)
        nested_exception = CalculationModelException(
            calc_obj=[child_instance],
            exception_details=["inner-failure"],
            stack_trace=["inner-trace"],
        )

        try:
            try:
                raise nested_exception
            except CalculationModelException:
                raise RuntimeError("outer-wrapper")
        except RuntimeError as wrapped_exception:
            calc_obj, exception_details, stack_trace = (
                DummyCalculationModel.build_exception_chain(
                    wrapped_exception,
                    current_obj=instance,
                )
            )

        self.assertEqual(calc_obj, [child_instance, instance])
        self.assertEqual(exception_details, ["inner-failure"])
        self.assertEqual(stack_trace, ["inner-trace"])


class TestResolveCalculationId(SimpleTestCase):
    """Prove ``_resolve_calculation_id`` follows the priority chain correctly."""

    def setUp(self):
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)

    def test_priority_1_operation_context(self):
        """operation_context['calculation_id'] has highest priority."""
        instance = DummyCalculationModel(id=20)
        operation_context.set(
            {"operation_id": "op", "request_obj": {}, "calculation_id": "ctx-20", "audit_log_temp": None}
        )
        self.addCleanup(operation_context.set, {"operation_id": "", "request_obj": {}, "calculation_id": "", "audit_log_temp": None})

        result = _resolve_calculation_id(instance, "dummycalculationmodel_20")
        self.assertEqual(result, "ctx-20")

    def test_priority_2_state_store(self):
        """Falls back to ActiveCalculationStateStore when context is empty."""
        operation_context.set(
            {"operation_id": "op", "request_obj": {}, "calculation_id": "", "audit_log_temp": None}
        )
        self.addCleanup(operation_context.set, {"operation_id": "", "request_obj": {}, "calculation_id": "", "audit_log_temp": None})

        ActiveCalculationStateStore.mark_in_progress(
            record_id="dummycalculationmodel_21",
            calculation_id="store-21",
            record="Dummy(21)",
        )
        instance = DummyCalculationModel(id=21)
        result = _resolve_calculation_id(instance, "dummycalculationmodel_21")
        self.assertEqual(result, "store-21")

    def test_priority_3_instance_attribute(self):
        """Falls back to instance.calculation_id when store is empty."""
        operation_context.set(
            {"operation_id": "op", "request_obj": {}, "calculation_id": "", "audit_log_temp": None}
        )
        self.addCleanup(operation_context.set, {"operation_id": "", "request_obj": {}, "calculation_id": "", "audit_log_temp": None})

        instance = DummyCalculationModel(id=22)
        instance.calculation_id = "attr-22"
        result = _resolve_calculation_id(instance, "dummycalculationmodel_22")
        self.assertEqual(result, "attr-22")

    def test_returns_none_when_nothing_available(self):
        """Returns None when all three sources are absent."""
        operation_context.set(
            {"operation_id": "op", "request_obj": {}, "calculation_id": "", "audit_log_temp": None}
        )
        self.addCleanup(operation_context.set, {"operation_id": "", "request_obj": {}, "calculation_id": "", "audit_log_temp": None})

        instance = DummyCalculationModel(id=23)
        result = _resolve_calculation_id(instance, "dummycalculationmodel_23")
        self.assertIsNone(result)

    def test_empty_string_instance_attr_skipped(self):
        """An empty-string calculation_id on instance is treated as absent."""
        operation_context.set(
            {"operation_id": "op", "request_obj": {}, "calculation_id": "", "audit_log_temp": None}
        )
        self.addCleanup(operation_context.set, {"operation_id": "", "request_obj": {}, "calculation_id": "", "audit_log_temp": None})

        instance = DummyCalculationModel(id=24)
        instance.calculation_id = ""
        result = _resolve_calculation_id(instance, "dummycalculationmodel_24")
        self.assertIsNone(result)

    def test_non_string_instance_attr_skipped(self):
        """A non-string calculation_id on instance is skipped."""
        operation_context.set(
            {"operation_id": "op", "request_obj": {}, "calculation_id": "", "audit_log_temp": None}
        )
        self.addCleanup(operation_context.set, {"operation_id": "", "request_obj": {}, "calculation_id": "", "audit_log_temp": None})

        instance = DummyCalculationModel(id=25)
        instance.calculation_id = 99999
        result = _resolve_calculation_id(instance, "dummycalculationmodel_25")
        self.assertIsNone(result)

