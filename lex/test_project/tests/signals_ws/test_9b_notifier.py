"""
Cluster 9b: ``WebSocketNotifier`` + ``update_calculation_status``.

Intent (from docs/features/calculations/ + signals docs):

    Each state transition broadcasts a WebSocket message with
    ``(model, pk, state)``. On failure the payload includes the
    exception message and a stack trace so the UI can surface it.

Scenario numbering matches
docs/test-plan/test-clusters.md#9-signals--websocket.
"""

from __future__ import annotations

import unittest


class TestCluster09b_Notifier(unittest.TestCase):
    """WebSocket notifier is invoked with correct payload."""

    @unittest.skip(
        "Scenario 9.3: WebSocket notification on state change. "
        "Requires un-patching E2ETestCase's send_calculation_update "
        "mock and replacing it with a spy that records calls — doable "
        "but needs a shared fixture to stop the default patch only "
        "for cluster 9. Re-add once that fixture exists."
    )
    def test_9_3_websocket_notification_on_state_change(self) -> None:
        """Scenario 9.3: State change → send_calculation_update(model, state)."""

    @unittest.skip(
        "Scenario 9.6: update_calculation_status error payload. "
        "Same spy-pattern dependency as 9.3."
    )
    def test_9_6_update_calculation_status_on_failure(self) -> None:
        """Scenario 9.6: Failure → error details + stack trace in payload."""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

