"""
Cluster 9a: ``ActiveCalculationStateStore`` — in-flight calculation tracking.

Intent (from docs/features/calculations/ + signals docs):

    When a calculation enters IN_PROGRESS, the framework must register
    it in :class:`ActiveCalculationStateStore` so the UI / API can ask
    "is calculation X still running?". On SUCCESS or ERROR the record
    must be cleaned up.

These tests **un-patch** the stock ``mark_in_progress`` mock installed
by :class:`E2ETestCase` so we can observe the real call.

Scenario numbering matches
docs/test-plan/test-clusters.md#9-signals--websocket.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestCluster09a_StateStore(unittest.TestCase):
    """ActiveCalculationStateStore registration + cleanup."""

    @unittest.skip(
        "Scenario 9.1: mark_in_progress registration. Requires un-patching "
        "the E2ETestCase default mock AND running with a real cache "
        "backend (or a mock store fixture) since the state store writes "
        "through CacheManager. Covered at unit level; re-add here once "
        "the signals fixture lands."
    )
    def test_9_1_mark_in_progress_registers_record(self) -> None:
        """Scenario 9.1: Entering IN_PROGRESS calls mark_in_progress."""

    @unittest.skip("Scenario 9.2: cleanup on completion (see 9.1)")
    def test_9_2_completion_cleans_up_state_store(self) -> None:
        """Scenario 9.2: SUCCESS/ERROR removes record from state store."""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

