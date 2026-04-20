"""
Cluster 8b: Dispatch — context extraction + nested-calc handling.

Intent (from docs/features/calculations/ + Celery integration docs):

    * 8.5: ``dispatch_calculation_task`` serializes the active
      ``operation_context`` without choking on unpicklable objects
      (request, transaction connections, …).
    * 8.6: Nested calculation inside a Celery worker runs **synchronously**
      to avoid recursive dispatch. ``is_celery_worker_process()``
      detects the worker.

Scenario numbering matches
docs/test-plan/test-clusters.md#8-celery--async.
"""

from __future__ import annotations

import unittest


class TestCluster08b_Dispatch(unittest.TestCase):
    """Dispatch details — skipped pending Celery broker fixture."""

    @unittest.skip(
        "Scenario 8.5: dispatch_calculation_task context extraction. "
        "Requires a Celery broker or a mock to intercept the dispatched "
        "task payload. Covered by lex.tests.unit.infra.* at the unit "
        "level — re-enable here once a broker-less dispatch fixture "
        "exists."
    )
    def test_8_5_dispatch_extracts_operation_context(self) -> None:
        """Scenario 8.5: Dispatched task receives a serializable context."""

    @unittest.skip(
        "Scenario 8.6: Nested calc inside a Celery worker. Requires "
        "patching ``is_celery_worker_process`` and a real dispatch path "
        "with an active outer task — covered by unit tests. Re-add "
        "once Celery fixture lands."
    )
    def test_8_6_nested_calc_in_worker_runs_sync(self) -> None:
        """Scenario 8.6: Nested calc inside worker runs synchronously."""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

