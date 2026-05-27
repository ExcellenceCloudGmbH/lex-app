"""
Cluster 9e: background-thread calculation logs still reach WebSocket listeners.

Regression for issue #518: when async calculations moved onto the dedicated
thread pool, the request thread unwound its ``model_logging_context`` before the
worker emitted log lines. ``copy_context()`` preserves ``operation_context`` but
captures the same mutable ``ModelContext`` object, so without re-installing a
fresh per-thread model context the calculation-log websocket fan-out silently
stops.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from lex.api.utils import operation_context
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.ModelContext import (
    ModelContext,
    _model_context,
    model_logging_context,
)
from lex.core.models.CalculationModel import CalculationModel

import pytest

pytestmark = pytest.mark.signals_ws


class DummyThreadedSignalCalc(CalculationModel):
    """Minimal unmanaged calculation model for threaded log-delivery tests."""

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):  # pragma: no cover - not used directly here
        return None


class TestCluster09e_ThreadedLogDelivery(SimpleTestCase):
    """9.29 — async calculation-log fan-out survives request-context unwind."""

    def setUp(self) -> None:
        super().setUp()
        self.instance = DummyThreadedSignalCalc(id=929)
        self._original_operation_context = dict(operation_context.get() or {})
        self._original_model_stack = list(
            _model_context.get()["model_context"]._stack
        )

    def tearDown(self) -> None:
        operation_context.set(dict(self._original_operation_context))
        _model_context.set({"model_context": ModelContext(self._original_model_stack)})
        super().tearDown()

    def test_9_29_background_thread_reinstalls_model_context_for_log_websocket(self) -> None:
        """
        Scenario 9.29: the async calculation thread still emits the live
        ``calculation_log_real_time`` payload for the current record even after
        the request thread has unwound its model context.
        """
        calculation_id = "calc-9-29"
        record_id = f"{self.instance._meta.model_name}_{self.instance.pk}"
        operation_context.set(
            {
                "operation_id": "op-9-29",
                "request_obj": {},
                "calculation_id": calculation_id,
                "audit_log_temp": None,
            }
        )

        with model_logging_context(self.instance):
            ctx = copy_context()

        self.assertIsNone(
            ctx.run(lambda: _model_context.get()["model_context"].current),
            "copy_context() alone must not be enough here: it captures the same "
            "mutable ModelContext object that the request thread already popped. "
            "This is the exact regression shape that previously dropped threaded "
            "calculation-log websocket updates.",
        )

        audit_log = SimpleNamespace(
            calculation_id=calculation_id,
            pk=1,
            _state=SimpleNamespace(db="default"),
        )
        websocket_events: list[tuple[str, dict]] = []
        message = "Start: threaded log survives websocket fan-out"

        def _run_in_worker() -> None:
            def _invoke_with_fresh_model_context() -> None:
                _model_context.set({"model_context": ModelContext([self.instance])})
                CalculationLog.log(message)

            ctx.run(_invoke_with_fresh_model_context)

        with patch(
            "lex.audit_logging.models.AuditLog.AuditLog.objects.get",
            return_value=audit_log,
        ), patch(
            "lex.audit_logging.utils.ContextResolver._safe_get_content_type",
            return_value=object(),
        ), patch.object(
            CalculationLog,
            "_persist_message",
        ), patch(
            "lex.audit_logging.models.CalculationLog.CacheManager.store_message"
        ) as store_message_mock, patch(
            "lex.audit_logging.handlers.WebSocketHandler.sync_channel_group_send",
            side_effect=lambda group, payload: websocket_events.append((group, payload)),
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_run_in_worker).result(timeout=2)

        store_message_mock.assert_called_once_with(
            f"{record_id}_{calculation_id}",
            message,
        )
        self.assertEqual(
            len(websocket_events),
            1,
            "A threaded calculation log for a single record must fan out to that "
            "record's WebSocket group exactly once.",
        )
        group, payload = websocket_events[0]
        self.assertEqual(
            group,
            record_id,
            "Calculation-log websocket delivery must target the current record's "
            "group so the live log tab keeps updating after the request returns 202.",
        )
        self.assertEqual(payload["type"], "calculation_log_real_time")
        self.assertEqual(
            payload["payload"].strip(),
            message,
            "WebSocket payload must still carry the threaded log line itself; "
            "dropping it recreates the 'spinner moves but logs stay blank' bug.",
        )
