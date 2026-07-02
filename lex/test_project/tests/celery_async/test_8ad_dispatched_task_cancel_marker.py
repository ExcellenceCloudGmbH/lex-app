"""Dispatched @lex_shared_task runs self-abort on the cluster cancel marker.

Intent: nested calculations DISPATCH to other Celery workers by default (7q) —
they must parallelise, never collapse inline. That makes abort-safety the cancel
marker's job (Report 1 — abort→resume): ``cancel()`` persists a Redis
"cancelled" marker for the calculation_id (``mark_cancelled``) which — unlike
the in-memory revoke — survives a server+worker restart. When the broker then
redelivers a still-unacked child task, the task must check that marker at start
and self-abort with ``CalculationCancelled`` so it lands CANCELLED instead of
silently resuming. ``calc_and_save`` already had this net; these scenarios pin
the same net in the generic ``lex_shared_task`` wrapper, which covers every
*decorated* calculate method dispatched directly via ``func.delay`` (e.g. a
project's ``CalculateNAV.calculate``) — previously the uncovered hole. The check
fires ONLY for dispatched executions (a ``context`` kwarg with a
calculation_id); direct synchronous calls carry no context and must never touch
the cancel index (no Redis dependency in sync mode). A regression here either
resurrects abort→resume (check lost) or breaks sync/local runs (check firing
without a dispatched context).

Cluster 8ad — scenarios 8.142–8.144. Type: U.
Covers: lex/lex_app/celery_tasks.py (lex_shared_task wrapper cancel-marker check).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8ad_dispatched_task_cancel_marker.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationCancelled
from lex.lex_app import celery_tasks
from lex.lex_app.celery_tasks import lex_shared_task

pytestmark = pytest.mark.celery_async

# Module-level probe task: records executions so each scenario can assert
# whether the wrapped body ran. Decorated at import time exactly like a real
# project task; calling it with ``_celery_is_active`` patched False executes
# the task body (the wrapper) in-process — the same code path a worker runs
# for a dispatched message.
_executions: list = []


@lex_shared_task
def _probe_task(*args, **kwargs):
    _executions.append((args, kwargs))
    return "ran"


class TestCluster08ad_DispatchedTaskCancelMarker(SimpleTestCase):
    """Cluster 8ad: the ``lex_shared_task`` wrapper self-aborts a dispatched
    execution whose calculation carries the cluster cancel marker, and leaves
    non-dispatched (synchronous) executions untouched."""

    def setUp(self) -> None:
        super().setUp()
        _executions.clear()
        # Force the descriptor's synchronous branch so calling the task
        # executes the wrapper in-process (as a worker would for a
        # redelivered message) instead of attempting a real .delay().
        self._active_patch = patch.object(
            celery_tasks, "_celery_is_active", return_value=False
        )
        self._active_patch.start()
        self.addCleanup(self._active_patch.stop)

    # -- 8.142 ---------------------------------------------------------
    def test_8_142_dispatched_run_with_cancel_marker_self_aborts(self) -> None:
        """
        Scenario 8.142: dispatched execution, cancel marker SET.
        Given: the task runs with a dispatched ``context`` carrying a
               calculation_id whose cluster cancel marker is set (the
               broker-redelivery-after-restart case).
        When:  the task body starts.
        Then:  it raises CalculationCancelled BEFORE running the wrapped
               function — the redelivered child lands CANCELLED instead of
               silently resuming the aborted calculation.
        """
        with patch(
            "lex.core.cancellation.cluster_cancel_index.is_cancelled",
            return_value=True,
        ) as is_cancelled_mock:
            with self.assertRaises(
                CalculationCancelled,
                msg="A dispatched run whose calc is marked cancelled must "
                    "self-abort with CalculationCancelled",
            ):
                _probe_task(context={"calculation_id": "calc-8-142"})

        is_cancelled_mock.assert_called_once_with(
            "calc-8-142"
        )  # marker consulted for THIS calculation
        self.assertEqual(
            _executions,
            [],
            "The wrapped function must NOT run when the cancel marker is set "
            "— running it is exactly the Report 1 abort→resume bug",
        )

    # -- 8.143 ---------------------------------------------------------
    def test_8_143_dispatched_run_without_marker_executes(self) -> None:
        """
        Scenario 8.143: dispatched execution, cancel marker NOT set.
        Given: the task runs with a dispatched ``context`` but the calculation
               was never cancelled.
        When:  the task body starts.
        Then:  the marker is consulted and, being clear, the wrapped function
               runs normally — the net must not block healthy dispatched work.
        """
        with patch(
            "lex.core.cancellation.cluster_cancel_index.is_cancelled",
            return_value=False,
        ) as is_cancelled_mock:
            result, _ = _probe_task(context={"calculation_id": "calc-8-143"})

        is_cancelled_mock.assert_called_once_with(
            "calc-8-143"
        )  # marker consulted before running
        self.assertEqual(
            result, "ran", "Wrapped function must execute when no marker is set"
        )
        self.assertEqual(
            len(_executions),
            1,
            "Wrapped function must run exactly once for a healthy dispatch",
        )

    # -- 8.144 ---------------------------------------------------------
    def test_8_144_synchronous_run_never_touches_cancel_index(self) -> None:
        """
        Scenario 8.144: direct synchronous call — no dispatched context.
        Given: the task is invoked without a ``context`` kwarg (the in-process
               synchronous mode used locally and in tests).
        When:  the task body runs.
        Then:  the cancel index is never consulted (sync mode must have zero
               Redis dependency — without a broker there is nothing to
               redeliver) and the wrapped function runs normally.
        """
        with patch(
            "lex.core.cancellation.cluster_cancel_index.is_cancelled"
        ) as is_cancelled_mock:
            result, _ = _probe_task()

        is_cancelled_mock.assert_not_called()  # sync mode: no cancel-index I/O
        self.assertEqual(
            result, "ran", "Synchronous execution must be completely unaffected"
        )
        self.assertEqual(
            len(_executions),
            1,
            "Wrapped function must run exactly once in synchronous mode",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
