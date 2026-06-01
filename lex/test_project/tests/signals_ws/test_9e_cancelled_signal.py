"""Cluster 9e — cancelled calculation status broadcast contract.

Intent: when a calculation is cancelled, connected clients must receive the
same real-time status update guarantees as success/error states and the
active-calculation store entry must be cleared.
Cluster 9e — scenarios 9.29–9.29. Type: U.
Covers: lex/core/signals/CalculationSignals.py.
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9e_cancelled_signal.py -v
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.core.signals.CalculationSignals import update_calculation_status

import pytest

pytestmark = pytest.mark.signals_ws


class DummySignalCalc(CalculationModel):
    """Unmanaged calculation model for signal-level tests."""

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):  # pragma: no cover - not invoked by this test
        return None


class TestCluster09e_CancelledSignal(TestCase):
    """Cluster 9e: cancelled-state status signal behaviour."""

    def setUp(self) -> None:
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)

    def test_9_29_cancelled_status_clears_store_and_broadcasts(self):
        """
        Scenario 9.29: cancelled status emits cancellation event and clears state.
        Given: a tracked in-progress calculation entry exists for a record.
        When: ``update_calculation_status`` is called for the CANCELLED state.
        Then: store entry is removed and a ``calculation_cancelled`` payload is sent.
        """
        instance = DummySignalCalc(id=29)
        instance.is_calculated = "CANCELLED"

        record_id = f"{instance._meta.model_name}_{instance.id}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="calc-29",
            record="Dummy #29",
            model_label=instance._meta.label_lower,
            record_pk=instance.pk,
        )

        with patch.object(CalculationModel, "CANCELLED", "CANCELLED", create=True), patch(
            "lex.core.signals.CalculationSignals.sync_channel_group_send"
        ) as send_spy:
            update_calculation_status(instance)

        self.assertFalse(
            ActiveCalculationStateStore.get_entry(record_id),
            "Cancelled calculation must be removed from active-state store.",
        )
        send_spy.assert_called_once()
        group_name, message = send_spy.call_args.args
        self.assertEqual(
            group_name,
            "update_calculation_status",
            "Signal must broadcast on the update_calculation_status group.",
        )
        self.assertEqual(
            message["type"],
            "calculation_cancelled",
            "Cancelled status must broadcast calculation_cancelled message type.",
        )
        self.assertEqual(
            message["payload"]["record_id"],
            record_id,
            "Broadcast payload must identify the cancelled record.",
        )
