"""Cluster 7n — startup reset marks stuck calculations as CANCELLED.

Intent: on process startup, any calculation left IN_PROGRESS from a crashed
runtime must be brought to a terminal cancelled state and emit a matching
terminal audit entry.
Cluster 7n — scenarios 7.166–7.167. Type: U.
Covers: lex/process_admin/utils/model_registration.py.
Run: python -m lex pytest lex/test_project/tests/calculations/test_7n_startup_reset_cancelled.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from lex.core.models.CalculationModel import CalculationModel
from lex.process_admin.utils.model_registration import ModelRegistration

import pytest

pytestmark = pytest.mark.calculations


class TestCluster07n_StartupResetCancelled(TestCase):
    """Cluster 7n: startup reset cancellation contract."""

    def _fake_model(self, stuck_instances):
        return SimpleNamespace(
            __name__="StartupResetCalc",
            objects=SimpleNamespace(filter=Mock(return_value=stuck_instances)),
        )

    def test_7_166_startup_reset_marks_stuck_rows_cancelled_and_audits(self):
        """
        Scenario 7.166: startup reset transitions IN_PROGRESS rows to CANCELLED.
        Given: startup mode is active and a model has stuck IN_PROGRESS rows.
        When: ``_handle_calculation_model_reset`` runs.
        Then: each row is saved as CANCELLED and terminal audit is written as cancelled.
        """
        instance = SimpleNamespace(pk=166, is_calculated=CalculationModel.IN_PROGRESS)
        instance.save = Mock()
        model = self._fake_model([instance])

        with patch.dict(
            "os.environ", {"CALLED_FROM_START_COMMAND": "1"}, clear=False
        ), patch.object(
            # Compatibility bridge: this branch still keeps ABORTED as the model
            # constant, so we inject CANCELLED to assert the forward contract.
            CalculationModel, "CANCELLED", "CANCELLED", create=True
        ), patch(
            "lex.audit_logging.utils.calculation_audit.ensure_terminal_calculation_audit"
        ) as audit_spy:
            ModelRegistration._handle_calculation_model_reset(model)

        model.objects.filter.assert_called_once_with(
            is_calculated=CalculationModel.IN_PROGRESS
        )
        self.assertEqual(
            instance.is_calculated,
            "CANCELLED",
            "Startup reset must put stuck calculations into the CANCELLED terminal state.",
        )
        self.assertEqual(
            instance._history_change_reason,
            "Startup reset: calculation was still IN_PROGRESS",
            "History reason must explain startup-driven cancellation.",
        )
        instance.save.assert_called_once_with(skip_hooks=True)
        audit_spy.assert_called_once_with(
            instance,
            audit_status="cancelled",
            error_message="Calculation cancelled during startup reset",
        )

    def test_7_167_reset_is_noop_without_start_command_flag(self):
        """
        Scenario 7.167: startup reset is gated by CALLED_FROM_START_COMMAND.
        Given: startup mode env flag is not set.
        When: ``_handle_calculation_model_reset`` is called.
        Then: no model query or reset work is executed.
        """
        model = self._fake_model([])

        with patch.dict("os.environ", {}, clear=True):
            ModelRegistration._handle_calculation_model_reset(model)

        model.objects.filter.assert_not_called()
