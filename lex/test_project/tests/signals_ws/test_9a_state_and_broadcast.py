"""
Cluster 9a/9b: calculation state-store and broadcast contract.

The plan lists scenarios 9.1 / 9.2 / 9.3 / 9.6 for the
``update_calculation_status`` signal. The canonical unit tests already
cover this framework surface; this test-project file preserves the
scenario-numbered customer-journey contract alongside the rest of the
cluster suite.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from django.test import SimpleTestCase

from lex.api.utils import operation_context
from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.core.signals.CalculationSignals import update_calculation_status


class DummySignalCalc(CalculationModel):
    """Unmanaged calculation model sufficient for signal-level tests."""

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):  # pragma: no cover - not used by these tests
        return None


class TestCluster09a_StateAndBroadcast(SimpleTestCase):
    """9.1 / 9.2 / 9.3 / 9.6 — signal state and websocket payloads."""

    def setUp(self):
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

        self._set_context("calc-9")
        self.addCleanup(self._set_context, "")

    @staticmethod
    def _set_context(calculation_id: str) -> None:
        operation_context.set({
            "operation_id": "op-9",
            "request_obj": {},
            "calculation_id": calculation_id,
            "audit_log_temp": None,
        })

    @staticmethod
    def _instance(pk: int, state: str = CalculationModel.IN_PROGRESS):
        instance = DummySignalCalc(id=pk)
        instance.is_calculated = state
        return instance

    # -- 9.1 -----------------------------------------------------------
    def test_9_1_mark_in_progress_registers_state_store(self) -> None:
        """Scenario 9.1: IN_PROGRESS registers the record in state store."""
        instance = self._instance(91)

        update_calculation_status(instance)

        record_id = f"{instance._meta.model_name}_{instance.id}"
        entry = ActiveCalculationStateStore.get_entry(record_id)
        self.assertEqual(entry["calculation_id"], "calc-9")
        self.assertEqual(entry["record_pk"], str(instance.id))

    # -- 9.2 -----------------------------------------------------------
    def test_9_2_completion_cleans_up_state_store(self) -> None:
        """Scenario 9.2: SUCCESS clears the state-store entry."""
        instance = self._instance(92)
        record_id = f"{instance._meta.model_name}_{instance.id}"
        update_calculation_status(instance)
        self.assertTrue(ActiveCalculationStateStore.get_entry(record_id))

        instance.is_calculated = CalculationModel.SUCCESS
        update_calculation_status(instance)

        self.assertEqual(ActiveCalculationStateStore.get_entry(record_id), {})

    # -- 9.3 -----------------------------------------------------------
    def test_9_3_websocket_notification_sent_on_state_change(self) -> None:
        """Scenario 9.3: IN_PROGRESS sends the websocket update payload."""
        instance = self._instance(93)

        update_calculation_status(instance)

        self.assertEqual(len(self.broadcasts), 1)
        group, message = self.broadcasts[0]
        self.assertEqual(group, "update_calculation_status")
        self.assertEqual(message["type"], "calculation_in_progress")
        self.assertEqual(message["payload"]["calculation_id"], "calc-9")
        self.assertEqual(
            message["payload"]["record_id"],
            f"{instance._meta.model_name}_{instance.id}",
        )

    # -- 9.6 -----------------------------------------------------------
    def test_9_6_update_status_includes_error_details_on_failure(self) -> None:
        """Scenario 9.6: ERROR broadcast includes message and traceback."""
        instance = self._instance(96)
        update_calculation_status(instance)

        instance.is_calculated = CalculationModel.ERROR
        update_calculation_status(
            instance,
            exception_details="boom",
            stack_trace="traceback-here",
        )

        message = self.broadcasts[-1][1]
        self.assertEqual(message["type"], "calculation_error")
        self.assertEqual(message["payload"]["message"], "boom")
        self.assertEqual(message["payload"]["traceback"], "traceback-here")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

