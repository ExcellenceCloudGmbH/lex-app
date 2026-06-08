"""Worker-recovery terminal-outcome guard — never resurrect a finished task.

Intent
------
The heartbeat recovery supervisor requeues tasks whose *worker* died. But an
expired heartbeat only proves the worker is gone — it does **not** prove the
*task* was unfinished. A worker can persist a calculation's terminal state
(e.g. ``ERROR``) and then be hard-killed (SIGKILL / OOM / pod eviction) *before*
``task_postrun`` deregistered it. The stale registry entry then looks like a
dead in-progress task, the supervisor requeues the same ``task_id``, the re-run
succeeds, and a row that the user already saw as ``ERROR`` silently flips to
``SUCCESS`` — the ERROR→SUCCESS resurrection bug.

The terminal-outcome guard closes that window: before requeueing a dead task,
the supervisor consults two authoritative "did this actually finish?" signals —
the result backend (a *ready* ``AsyncResult``; with ``task_reject_on_worker_lost``
a merely-lost worker never writes one, so a ready result proves the body ran)
and the calculation rows themselves (every row out of ``IN_PROGRESS``). If either
says "done", the supervisor deregisters instead of requeueing. A regression here
re-opens the resurrection bug and corrupts the compliance trail.

Cluster 8w — scenarios 8.90–8.102. Type: U (helper logic + scan orchestration,
mocked registry/backend) + I (real ``CalculationModel`` rows for the row-state
signal).
Covers: lex/lex_app/celery_recovery/supervisor.py.
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8w_recovery_terminal_guard.py -v
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery_recovery import supervisor
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc

pytestmark = pytest.mark.celery_async


class TestCluster08w_ResultBackendSettled(SimpleTestCase):
    """Cluster 8w: the result-backend half of the terminal guard."""

    def test_08_90_ready_result_means_settled(self):
        """
        Scenario 8.90: a ready AsyncResult proves the task body concluded.
        Given: the result backend reports ``AsyncResult(task_id).ready()`` True
               (a terminal SUCCESS/FAILURE/REVOKED result exists).
        When:  _result_already_settled is consulted for that task.
        Then:  it returns True — the task finished, so recovery must not requeue.
        """
        app = mock.MagicMock()
        app.AsyncResult.return_value.ready.return_value = True
        self.assertTrue(
            supervisor._result_already_settled(app, "task-1"),
            msg="a ready terminal result must count as settled",
        )
        app.AsyncResult.assert_called_once_with("task-1")

    def test_08_91_unready_result_is_not_settled(self):
        """
        Scenario 8.91: a pending/absent result is not a completion signal.
        Given: ``AsyncResult(task_id).ready()`` is False (no terminal result —
               the worker-lost case never writes one under reject_on_worker_lost).
        When:  _result_already_settled is consulted.
        Then:  it returns False so recovery falls back to the requeue path.
        """
        app = mock.MagicMock()
        app.AsyncResult.return_value.ready.return_value = False
        self.assertFalse(
            supervisor._result_already_settled(app, "task-1"),
            msg="an unready result must not block a legitimate requeue",
        )

    def test_08_92_backend_error_degrades_to_not_settled(self):
        """
        Scenario 8.92: a backend lookup error must never strand recovery.
        Given: ``AsyncResult`` raises (Redis down / backend unreachable).
        When:  _result_already_settled is consulted.
        Then:  it swallows the error and returns False — a broken backend
               degrades to requeueing, never to wrongly abandoning a live task.
        """
        app = mock.MagicMock()
        app.AsyncResult.side_effect = RuntimeError("backend down")
        self.assertFalse(
            supervisor._result_already_settled(app, "task-1"),
            msg="a backend error must degrade to 'not settled' (requeue)",
        )


class TestCluster08w_AlreadyFinishedCombination(SimpleTestCase):
    """Cluster 8w: _already_finished ORs the two authoritative signals."""

    def test_08_98_backend_alone_settles_without_rows(self):
        """
        Scenario 8.98: the result backend alone is sufficient evidence.
        Given: a ready result but a payload with no extractable rows.
        When:  _already_finished is consulted.
        Then:  it returns True — either signal alone proves completion.
        """
        app = mock.MagicMock()
        app.AsyncResult.return_value.ready.return_value = True
        self.assertTrue(
            supervisor._already_finished(app, "task-1", {"args": ()}),
            msg="a ready backend result alone must mark the task finished",
        )

    def test_08_99_neither_signal_means_unfinished(self):
        """
        Scenario 8.99: with no completion signal the task is treated as live.
        Given: an unready result and a payload with no terminal rows.
        When:  _already_finished is consulted.
        Then:  it returns False so the dead task is requeued as normal.
        """
        app = mock.MagicMock()
        app.AsyncResult.return_value.ready.return_value = False
        self.assertFalse(
            supervisor._already_finished(app, "task-1", {"args": ()}),
            msg="no completion signal must leave the task eligible for requeue",
        )


class TestCluster08w_ScanSkipsFinished(SimpleTestCase):
    """Cluster 8w: scan_and_recover honours the guard at the public entry point.

    Drives the real ``scan_and_recover`` loop with the Redis registry boundary
    mocked, so the genuine guard decides the branch. ``_requeue`` / ``_give_up``
    are spied to prove *which* path a dead task takes.
    """

    def _registry_patches(self, *, payload, retries):
        """Patch the registry boundary for a single tracked, dead task."""
        full_payload = dict(payload)
        full_payload.setdefault("name", "calc_and_save")
        full_payload["retries"] = retries
        return [
            mock.patch.object(supervisor.registry, "list_tracked", return_value=["task-1"]),
            mock.patch.object(supervisor.registry, "is_alive", return_value=False),
            mock.patch.object(supervisor.registry, "get_payload", return_value=full_payload),
            mock.patch.object(supervisor.registry, "try_acquire_recovery_lock", return_value=True),
            mock.patch.object(supervisor.registry, "deregister"),
            # No cancellation marker for these scenarios.
            mock.patch.object(supervisor, "_is_cancelled", return_value=False),
        ]

    def test_08_100_finished_task_is_deregistered_not_requeued(self):
        """
        Scenario 8.100: a dead-but-finished task is dropped, never requeued.
        Given: a tracked task with a dead heartbeat whose result backend reports
               a ready terminal result (worker died after persisting, before
               postrun deregistered).
        When:  scan_and_recover runs one pass.
        Then:  the guard fires — registry.deregister is called, neither _requeue
               nor _give_up runs, and stats record already_finished=1, requeued=0.
        """
        app = mock.MagicMock()
        app.AsyncResult.return_value.ready.return_value = True  # finished

        patches = self._registry_patches(payload={"args": ()}, retries=0)
        with patches[0] as _lt, patches[1], patches[2], patches[3], \
                patches[4] as dereg, patches[5], \
                mock.patch.object(supervisor, "_requeue") as requeue, \
                mock.patch.object(supervisor, "_give_up") as give_up:
            stats = supervisor.scan_and_recover(app)

        dereg.assert_called_once_with("task-1")
        requeue.assert_not_called()
        give_up.assert_not_called()
        self.assertEqual(stats["already_finished"], 1)
        self.assertEqual(stats["requeued"], 0)
        self.assertEqual(stats["gave_up"], 0)

    def test_08_101_unfinished_task_under_budget_is_requeued(self):
        """
        Scenario 8.101: the guard never false-positives a genuinely live task.
        Given: a dead task with no completion signal (unready result, no terminal
               rows) and retries below the budget.
        When:  scan_and_recover runs one pass.
        Then:  _requeue is called, the guard does not deregister it, and stats
               record requeued=1, already_finished=0 — legitimate recovery still
               happens.
        """
        app = mock.MagicMock()
        app.AsyncResult.return_value.ready.return_value = False  # not finished

        patches = self._registry_patches(payload={"args": ()}, retries=0)
        with patches[0], patches[1], patches[2], patches[3], \
                patches[4] as dereg, patches[5], \
                mock.patch.object(supervisor, "_requeue") as requeue, \
                mock.patch.object(supervisor, "_give_up") as give_up:
            stats = supervisor.scan_and_recover(app)

        requeue.assert_called_once()
        give_up.assert_not_called()
        dereg.assert_not_called()
        self.assertEqual(stats["requeued"], 1)
        self.assertEqual(stats["already_finished"], 0)

    def test_08_102_finished_beats_budget_exhaustion(self):
        """
        Scenario 8.102: the guard runs before the budget branch.
        Given: a dead task whose retries already exhausted the budget BUT which
               actually finished (ready terminal result).
        When:  scan_and_recover runs one pass.
        Then:  the terminal guard wins — it deregisters and counts
               already_finished; _give_up is NOT called, so no spurious FAILURE
               result or ABORTED row clobbers the real terminal outcome.
        """
        app = mock.MagicMock()
        app.AsyncResult.return_value.ready.return_value = True  # finished

        # retries == max budget → without the guard this would hit _give_up.
        budget = supervisor._max_retries()
        patches = self._registry_patches(payload={"args": ()}, retries=budget)
        with patches[0], patches[1], patches[2], patches[3], \
                patches[4] as dereg, patches[5], \
                mock.patch.object(supervisor, "_requeue") as requeue, \
                mock.patch.object(supervisor, "_give_up") as give_up:
            stats = supervisor.scan_and_recover(app)

        give_up.assert_not_called()
        requeue.assert_not_called()
        dereg.assert_called_once_with("task-1")
        self.assertEqual(stats["already_finished"], 1)
        self.assertEqual(stats["gave_up"], 0)


class TestCluster08w_RowsSettled(E2ETestCase):
    """Cluster 8w: the calculation-row half of the terminal guard (real rows).

    The row-state signal is the one that catches the reported ERROR→SUCCESS
    bug, so it is exercised against real persisted ``CalculationModel`` rows.
    """

    e2e_models = [CelerySyncCalc]

    def _payload_for(self, *instances):
        """A re-dispatch payload whose args carry the given calc instances."""
        return {"args": (list(instances),), "name": "calc_and_save"}

    def _make(self, name, state):
        row = CelerySyncCalc.objects.create(name=name)
        row.is_calculated = state
        row.save(skip_hooks=True)
        return row

    def test_08_93_all_terminal_rows_are_settled(self):
        """
        Scenario 8.93: a persisted-ERROR row whose worker died is 'settled'.
        Given: the task's only row is already ERROR in the database (the worker
               persisted it, then was hard-killed before postrun).
        When:  _rows_already_settled inspects the payload.
        Then:  it returns True — requeuing would re-run concluded work and
               resurrect the row as SUCCESS. This is the exact bug the guard fixes.
        """
        row = self._make("err", CalculationModel.ERROR)
        self.assertTrue(
            supervisor._rows_already_settled(self._payload_for(row)),
            msg="a row already in ERROR must count as settled (no requeue)",
        )

    def test_08_94_in_progress_row_is_not_settled(self):
        """
        Scenario 8.94: a row still IN_PROGRESS is genuinely unfinished.
        Given: the task's row is IN_PROGRESS (the worker died mid-run, before
               persisting any terminal state).
        When:  _rows_already_settled inspects the payload.
        Then:  it returns False so the supervisor requeues — the legitimate
               recovery path is preserved.
        """
        row = self._make("running", CalculationModel.IN_PROGRESS)
        self.assertFalse(
            supervisor._rows_already_settled(self._payload_for(row)),
            msg="an IN_PROGRESS row must remain eligible for requeue",
        )

    def test_08_95_not_calculated_row_is_not_settled(self):
        """
        Scenario 8.95: NOT_CALCULATED means the work never concluded.
        Given: the task's row is still NOT_CALCULATED.
        When:  _rows_already_settled inspects the payload.
        Then:  it returns False — NOT_CALCULATED is not a terminal outcome, so
               the task is still eligible for recovery.
        """
        row = self._make("fresh", CalculationModel.NOT_CALCULATED)
        self.assertFalse(
            supervisor._rows_already_settled(self._payload_for(row)),
            msg="NOT_CALCULATED must not be treated as a finished outcome",
        )

    def test_08_96_mixed_rows_are_not_settled(self):
        """
        Scenario 8.96: a partially-finished task must still be requeued.
        Given: a task carrying two rows — one SUCCESS, one still IN_PROGRESS
               (the worker died after finishing the first but not the second).
        When:  _rows_already_settled inspects the payload.
        Then:  it returns False — *every* row must be terminal before the task
               counts as finished, so partial work is not abandoned.
        """
        done = self._make("done", CalculationModel.SUCCESS)
        running = self._make("still-running", CalculationModel.IN_PROGRESS)
        self.assertFalse(
            supervisor._rows_already_settled(self._payload_for(done, running)),
            msg="a single unfinished row must keep the whole task requeueable",
        )

    def test_08_97_no_rows_is_not_settled(self):
        """
        Scenario 8.97: with no extractable rows the guard stays conservative.
        Given: a payload whose args carry no CalculationModel instances.
        When:  _rows_already_settled inspects the payload.
        Then:  it returns False — absent row evidence, fall back to requeue
               rather than silently dropping a task.
        """
        self.assertFalse(
            supervisor._rows_already_settled({"args": ()}),
            msg="missing row evidence must degrade to 'not settled' (requeue)",
        )

    def test_08_93b_every_terminal_state_counts_as_settled(self):
        """
        Scenario 8.93 (extension): SUCCESS/ERROR/ABORTED/CANCELLED all settle.
        Given: four separate single-row tasks, one in each terminal state.
        When:  _rows_already_settled inspects each.
        Then:  every one returns True — all four documented terminal states stop
               a dead task from being resurrected, not just ERROR.
        """
        for state in (
            CalculationModel.SUCCESS,
            CalculationModel.ERROR,
            CalculationModel.ABORTED,
            CalculationModel.CANCELLED,
        ):
            row = self._make(f"terminal-{state}", state)
            self.assertTrue(
                supervisor._rows_already_settled(self._payload_for(row)),
                msg=f"terminal state {state} must count as settled",
            )
