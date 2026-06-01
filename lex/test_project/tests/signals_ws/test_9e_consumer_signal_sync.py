"""Cluster 9e: status consumer/store reconciliation and ABORTED signal contract.

Intent: real-time calculation status updates must stay correct across reconnects
and cancellation. A reconnecting browser must receive the active-calculation
snapshot, and terminal updates (SUCCESS/ERROR/ABORTED) must clear stale
in-memory entries so the UI does not show ghost spinners.

Cluster 9e — scenarios 9.29–9.33. Type: U.
Covers: lex/api/consumers/UpdateCalculationStatusConsumer.py,
lex/core/signals/CalculationSignals.py.
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9e_consumer_signal_sync.py -v
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase
from lex.api.consumers.UpdateCalculationStatusConsumer import (
    UpdateCalculationStatusConsumer,
)
from lex.api.utils import operation_context
from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.core.signals.CalculationSignals import update_calculation_status

import pytest

pytestmark = pytest.mark.signals_ws


class DummySignalCalc9e(CalculationModel):
    """Unmanaged calculation model for signal-level tests."""

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):  # pragma: no cover - not used in tests
        return None


class TestCluster09e_StatusConsumer(SimpleTestCase):
    """Cluster 9e consumer scenarios: reconnect snapshot + store sync."""

    def setUp(self):
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)
        UpdateCalculationStatusConsumer.active_consumers.clear()
        self.addCleanup(UpdateCalculationStatusConsumer.active_consumers.clear)

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    @staticmethod
    def _build_consumer() -> UpdateCalculationStatusConsumer:
        consumer = UpdateCalculationStatusConsumer()
        consumer.channel_name = "channel-9e"
        consumer.channel_layer = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.send = AsyncMock()
        return consumer

    # -- 9.29 ----------------------------------------------------------
    def test_9_29_connect_emits_reconciliation_snapshot(self) -> None:
        """Scenario 9.29: connect sends the current active-calculation snapshot."""
        consumer = self._build_consumer()
        snapshot = [{"record_id": "sigcalc_1", "record": "SigCalc(1)"}]

        with patch.object(ActiveCalculationStateStore, "snapshot", return_value=snapshot):
            self._run(consumer.connect())

        consumer.channel_layer.group_add.assert_awaited_once_with(
            "update_calculation_status",
            "channel-9e",
        )
        consumer.accept.assert_awaited_once()
        self.assertIn(
            consumer,
            UpdateCalculationStatusConsumer.active_consumers,
            "Connected consumer must be tracked for disconnect_all().",
        )

        payload = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(
            payload["type"],
            "calculation_reconciliation",
            "Reconnect payload type must be calculation_reconciliation.",
        )
        self.assertEqual(
            payload["payload"]["active_calculations"],
            snapshot,
            "Reconnect payload must include the state-store snapshot.",
        )

    # -- 9.30 ----------------------------------------------------------
    def test_9_30_in_progress_event_updates_store_and_forwards_payload(self) -> None:
        """Scenario 9.30: in-progress event seeds the ASGI store and emits websocket payload."""
        consumer = self._build_consumer()
        event = {
            "payload": {
                "record_id": "sigcalc_30",
                "calculation_id": "calc-9-30",
                "record": "SigCalc(30)",
            },
        }

        self._run(consumer.calculation_in_progress(event))

        entry = ActiveCalculationStateStore.get_entry("sigcalc_30")
        self.assertEqual(
            entry.get("calculation_id"),
            "calc-9-30",
            "in-progress event must update ActiveCalculationStateStore.",
        )

        payload = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(payload["type"], "calculation_in_progress")
        self.assertEqual(payload["payload"]["record_id"], "sigcalc_30")

    # -- 9.31 ----------------------------------------------------------
    def test_9_31_terminal_events_clear_store_entry(self) -> None:
        """Scenario 9.31: SUCCESS/ERROR/ABORTED events clear in-memory active state."""
        consumer = self._build_consumer()

        terminal_events = {
            "calculation_success": "calculation_success",
            "calculation_error": "calculation_error",
            "calculation_aborted": "calculation_aborted",
        }

        for method_name, message_type in terminal_events.items():
            with self.subTest(method=method_name):
                record_id = f"sigcalc_{method_name}"
                ActiveCalculationStateStore.mark_in_progress(
                    record_id=record_id,
                    calculation_id=f"calc-{method_name}",
                    record=f"SigCalc({method_name})",
                )

                method = getattr(consumer, method_name)
                self._run(method({"payload": {"record_id": record_id}}))

                self.assertEqual(
                    ActiveCalculationStateStore.get_entry(record_id),
                    {},
                    f"{method_name} must clear state-store entry for {record_id}.",
                )

                payload = json.loads(consumer.send.call_args.kwargs["text_data"])
                self.assertEqual(
                    payload["type"],
                    message_type,
                    f"{method_name} must forward websocket type {message_type}.",
                )


class TestCluster09e_CalculationSignals(SimpleTestCase):
    """Cluster 9e signal scenarios: ABORTED broadcast + calculation_id resolution."""

    def setUp(self):
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)
        self.broadcasts: list[tuple[str, dict]] = []

        self._broadcast_patcher = patch(
            "lex.core.signals.CalculationSignals.sync_channel_group_send",
            side_effect=lambda group, message: self.broadcasts.append((group, message)),
        )
        self._broadcast_patcher.start()
        self.addCleanup(self._broadcast_patcher.stop)

        operation_context.set(
            {
                "operation_id": "op-9e",
                "request_obj": {},
                "calculation_id": "",
                "audit_log_temp": None,
            }
        )
        self.addCleanup(
            operation_context.set,
            {
                "operation_id": "",
                "request_obj": {},
                "calculation_id": "",
                "audit_log_temp": None,
            },
        )

    @staticmethod
    def _instance(pk: int, state: str = CalculationModel.IN_PROGRESS):
        instance = DummySignalCalc9e(id=pk)
        instance.is_calculated = state
        return instance

    # -- 9.32 ----------------------------------------------------------
    def test_9_32_aborted_state_clears_store_and_broadcasts(self) -> None:
        """Scenario 9.32: ABORTED transitions emit calculation_aborted and clear state."""
        instance = self._instance(32, CalculationModel.IN_PROGRESS)
        record_id = f"{instance._meta.model_name}_{instance.id}"

        update_calculation_status(instance)
        self.assertTrue(
            ActiveCalculationStateStore.get_entry(record_id),
            "IN_PROGRESS must create a store entry before terminal transition.",
        )

        instance.is_calculated = CalculationModel.ABORTED
        update_calculation_status(instance)

        self.assertEqual(
            self.broadcasts[-1][1]["type"],
            "calculation_aborted",
            "ABORTED transition must emit calculation_aborted.",
        )
        self.assertEqual(
            ActiveCalculationStateStore.get_entry(record_id),
            {},
            "ABORTED transition must clear state-store entry.",
        )

    # -- 9.33 ----------------------------------------------------------
    def test_9_33_signal_resolves_calculation_id_from_state_store(self) -> None:
        """Scenario 9.33: when context is empty, broadcast uses state-store calculation_id."""
        instance = self._instance(33, CalculationModel.IN_PROGRESS)
        record_id = f"{instance._meta.model_name}_{instance.id}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="calc-from-store-9-33",
            record=str(instance),
        )

        update_calculation_status(instance)

        payload = self.broadcasts[-1][1]["payload"]
        self.assertEqual(
            payload.get("calculation_id"),
            "calc-from-store-9-33",
            "Broadcast must resolve calculation_id from state store before instance fallback.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
