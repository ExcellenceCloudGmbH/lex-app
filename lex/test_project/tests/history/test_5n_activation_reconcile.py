"""
Cluster 5n: Bitemporal activation catch-up — the timer is not the truth.

Intent (``lex/core/services/activation_reconcile.py``):

    A future-dated save schedules a timer for its own activation. Both timer
    backends are volatile: the in-process ``LocalSchedulerBackend`` queue (used
    whenever ``CELERY_ACTIVE`` is false, i.e. most instances) is held in a daemon
    thread and lost on every restart, with nothing to rehydrate it. Before this
    batch, that meant a deploy or an OOM silently dropped every pending
    activation — the meta row stayed ``SCHEDULED`` forever, the main table never
    caught up, and nothing was logged.

    The durable record is the meta row, not the timer: ``meta_task_status =
    "SCHEDULED"`` plus the history row's ``valid_from`` states exactly what
    should already have happened. ``reconcile_pending_activations`` reads that
    and finishes the job.

    The contract this batch pins:

      (a) an overdue SCHEDULED row is activated — the main table catches up
          even though no timer ever fired (5.104);
      (b) a row whose moment has not arrived is left strictly alone (5.105);
      (c) the pass is idempotent — a second run activates nothing and does not
          disturb the result (5.106);
      (d) activations older than ``LEX_ACTIVATION_RECONCILE_MAX_AGE_DAYS`` are
          reported, never silently replayed (5.107);
      (e) a record that fails to activate is retried only up to
          ``LEX_ACTIVATION_RECONCILE_MAX_ATTEMPTS``, then given up, so a
          permanently-broken row cannot burn a pass forever (5.108);
      (f) terminal meta states (DONE / CANCELLED) are never re-activated
          (5.109);
      (g) the background loop closes stale database connections around every
          pass, so a Postgres restart does not silently end catch-up for the
          life of the pod (5.110).

Companion to 5l, which covers the *producer* side of the same contract (a
future save schedules correctly). 5n covers what happens when that schedule is
lost — which, on the majority of the fleet, is every restart.

Run: python -m lex pytest lex/test_project/tests/history/test_5n_activation_reconcile.py -v
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HistSimpleItem

pytestmark = pytest.mark.history


def _l2_wired() -> bool:
    """L2 (MetaHistorical) is wired on the *historical* model — probe at call
    time, mirroring 5l."""
    try:
        return hasattr(HistSimpleItem.history.model, "meta_history") and bool(
            HistSimpleItem.history.model.meta_history.model
        )
    except Exception:
        return False


_SKIP_REASON_L2 = (
    "Activation catch-up reads the L2 meta record (meta_task_status). The "
    "test_project's HistSimpleItem fixture does not have MetaHistorical* wired "
    "in this build; see 5l for the same limitation."
)


class TestCluster05n_ActivationReconcile(E2ETestCase):
    """Overdue SCHEDULED rows activate without any timer having fired."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        # The attempt counter is process-global; a leaked entry from another
        # test would silently suppress an activation here.
        from lex.core.services import activation_reconcile

        activation_reconcile._failures.clear()
        try:
            from django_celery_beat.models import PeriodicTask

            PeriodicTask.objects.filter(task="activate_history_version").delete()
        except Exception:
            pass

    # ---------------------------------------------------------------- helpers
    def _future_item(self, hours: int = 1):
        """An item whose latest version is dated ``hours`` into the future.

        Returns ``(item, future_moment)``. The main table deliberately still
        reads the *previous* version at this point — that is the state a lost
        timer leaves behind.
        """
        item = HistSimpleItem.objects.create(name="before", value=0)

        # Backdate the initial version well into the past. Without this the base
        # row sits at "now", so once _moment_arrives moves the scheduled row back
        # by 30s the scheduled row becomes the OLDER of the two — the
        # synchronizer then correctly keeps "before", and the test would be
        # asserting the wrong thing for the wrong reason.
        base = (
            HistSimpleItem.history.filter(id=item.pk)
            .order_by("valid_from", "history_id")
            .first()
        )
        type(base).objects.filter(pk=base.pk).update(
            valid_from=timezone.now() - timedelta(days=400)
        )

        future = timezone.now() + timedelta(hours=hours)
        item.name = "after"
        item.value = 1
        item._history_date = future
        item.save()
        return item, future

    def _moment_arrives(self, item, seconds_ago: int = 30):
        """Move the scheduled row's ``valid_from`` into the past.

        This is how the test makes the moment arrive. Passing a synthetic
        ``now`` to the reconcile pass does not work and must not be used:
        ``activate_history_version`` re-checks ``valid_from`` against the real
        clock and declines anything not yet due, so a faked ``now`` selects the
        row and then fails to activate it. Moving the row is what a real hour
        passing looks like to every layer at once.
        """
        hist = (
            HistSimpleItem.history.filter(id=item.pk)
            .order_by("-valid_from", "-history_id")
            .first()
        )
        past = timezone.now() - timedelta(seconds=seconds_ago)
        type(hist).objects.filter(pk=hist.pk).update(valid_from=past)
        return past

    def _meta_rows(self, item):
        meta_model = HistSimpleItem.history.model.meta_history.model
        history_ids = list(
            HistSimpleItem.history.filter(id=item.pk).values_list("pk", flat=True)
        )
        return meta_model.objects.filter(history_object_id__in=history_ids)

    # -- 5.104 ---------------------------------------------------------------
    def test_5_104_overdue_activation_catches_up_without_a_timer(self) -> None:
        """
        Scenario 5.104 — the core contract, and the bug this batch exists for.
        Given: a future-dated save whose timer was lost (never fired)
        When:  the reconcile pass runs after the moment has passed
        Then:  the main table catches up and the meta row leaves SCHEDULED —
               proving the activation does not depend on the timer surviving
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from lex.core.services.activation_reconcile import (
            reconcile_pending_activations,
        )

        item, future = self._future_item(hours=1)

        self.assertEqual(
            HistSimpleItem.objects.get(pk=item.pk).name,
            "before",
            "Precondition: the main table must still hold the pre-activation value.",
        )
        self.assertTrue(
            self._meta_rows(item).filter(meta_task_status="SCHEDULED").exists(),
            "Precondition: a SCHEDULED meta row must exist to be caught up.",
        )

        # The moment arrives. No timer fires — only the reconcile pass runs.
        self._moment_arrives(item)
        stats = reconcile_pending_activations()

        self.assertEqual(
            stats["activated"], 1,
            f"Exactly one overdue activation was expected; got {stats}",
        )
        self.assertEqual(
            HistSimpleItem.objects.get(pk=item.pk).name,
            "after",
            "The main table must catch up to the activated version.",
        )
        self.assertFalse(
            self._meta_rows(item).filter(meta_task_status="SCHEDULED").exists(),
            "The meta row must leave SCHEDULED once activated.",
        )

    # -- 5.105 ---------------------------------------------------------------
    def test_5_105_not_yet_due_is_left_alone(self) -> None:
        """
        Scenario 5.105 — catch-up must never activate early.
        Given: a future-dated save whose moment has NOT arrived
        When:  the reconcile pass runs
        Then:  nothing is activated and the main table is untouched — otherwise
               the pass would defeat the whole point of scheduling
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from lex.core.services.activation_reconcile import (
            reconcile_pending_activations,
        )

        item, _future = self._future_item(hours=1)

        stats = reconcile_pending_activations()

        self.assertEqual(stats["activated"], 0, f"Nothing was due; got {stats}")
        self.assertEqual(
            HistSimpleItem.objects.get(pk=item.pk).name,
            "before",
            "A not-yet-due record must not be activated early.",
        )
        self.assertTrue(
            self._meta_rows(item).filter(meta_task_status="SCHEDULED").exists(),
            "The row must remain SCHEDULED for its real moment.",
        )

    # -- 5.106 ---------------------------------------------------------------
    def test_5_106_second_pass_is_a_no_op(self) -> None:
        """
        Scenario 5.106 — the pass is idempotent.
        Given: an activation already caught up by a previous pass
        When:  the pass runs again
        Then:  it activates nothing further and the value is unchanged. The loop
               runs every 60s forever, so a non-idempotent pass would re-apply
               the same activation continuously.
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from lex.core.services.activation_reconcile import (
            reconcile_pending_activations,
        )

        item, _future = self._future_item(hours=1)
        self._moment_arrives(item)

        first = reconcile_pending_activations()
        second = reconcile_pending_activations()

        self.assertEqual(first["activated"], 1)
        self.assertEqual(
            second["activated"], 0,
            f"The second pass must find nothing left to do; got {second}",
        )
        self.assertEqual(
            HistSimpleItem.objects.get(pk=item.pk).name, "after",
            "The value must be unchanged by the redundant pass.",
        )

    # -- 5.107 ---------------------------------------------------------------
    @override_settings(LEX_ACTIVATION_RECONCILE_MAX_AGE_DAYS=1)
    def test_5_107_ancient_activations_are_not_replayed(self) -> None:
        """
        Scenario 5.107 — a dormant instance must not replay history on wake.
        Given: an activation whose moment passed long before the age window
        When:  the reconcile pass runs
        Then:  it is NOT activated. An instance offline for months should not
               come back and silently apply a backlog of backdated changes; that
               is a decision for a human, not a background loop.
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from lex.core.services.activation_reconcile import (
            reconcile_pending_activations,
        )

        item, _future = self._future_item(hours=1)
        # Far outside the 1-day window configured above.
        self._moment_arrives(item, seconds_ago=45 * 24 * 3600)

        stats = reconcile_pending_activations()

        self.assertEqual(
            stats["activated"], 0,
            f"An activation older than the window must not be replayed; got {stats}",
        )
        self.assertEqual(
            HistSimpleItem.objects.get(pk=item.pk).name, "before",
            "The main table must be left for a human to resolve.",
        )

    # -- 5.108 ---------------------------------------------------------------
    @override_settings(LEX_ACTIVATION_RECONCILE_MAX_ATTEMPTS=2)
    def test_5_108_failing_record_is_given_up_not_retried_forever(self) -> None:
        """
        Scenario 5.108 — a broken record must not burn every pass forever.
        Given: an overdue activation that raises every time it is attempted
        When:  the pass runs repeatedly
        Then:  it is attempted up to the cap and then skipped. Without the cap a
               single poison record would be retried every 60s for the life of
               the process, filling logs and delaying healthy work.
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from lex.core.services.activation_reconcile import (
            reconcile_pending_activations,
        )

        item, _future = self._future_item(hours=1)
        self._moment_arrives(item)

        # reconcile_pending_activations imports the task inside the function, so
        # patch it at its source module.
        with patch(
            "lex.lex_app.celery_tasks.activate_history_version",
            side_effect=RuntimeError("activation exploded"),
        ):
            first = reconcile_pending_activations()
            second = reconcile_pending_activations()
            third = reconcile_pending_activations()

        self.assertEqual(first["failed"], 1, f"First attempt should fail; got {first}")
        self.assertEqual(second["failed"], 1, f"Second attempt should fail; got {second}")
        self.assertEqual(
            third["gave_up"], 1,
            f"After {2} attempts the record must be given up, not retried; got {third}",
        )
        self.assertEqual(
            third["failed"], 0,
            "A given-up record must not be attempted again in the same process.",
        )

    # -- 5.109 ---------------------------------------------------------------
    def test_5_109_terminal_meta_states_are_never_reactivated(self) -> None:
        """
        Scenario 5.109 — DONE and CANCELLED are terminal.
        Given: an overdue history row whose meta rows are all terminal
        When:  the reconcile pass runs
        Then:  nothing is activated. A cancelled schedule must stay cancelled —
               resurrecting it would re-apply a change a user explicitly
               withdrew.
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from lex.core.services.activation_reconcile import (
            reconcile_pending_activations,
        )

        item, _future = self._future_item(hours=1)
        self._moment_arrives(item)
        self._meta_rows(item).filter(meta_task_status="SCHEDULED").update(
            meta_task_status="CANCELLED"
        )

        stats = reconcile_pending_activations()

        self.assertEqual(
            stats["activated"], 0,
            f"A cancelled activation must never be resurrected; got {stats}",
        )
        self.assertEqual(
            HistSimpleItem.objects.get(pk=item.pk).name, "before",
            "The withdrawn change must not be applied.",
        )

    # -- 5.110 ---------------------------------------------------------------
    def test_5_110_loop_closes_stale_connections_around_each_pass(self) -> None:
        """
        Scenario 5.110 — the loop must survive a database connection dying.
        Given: the background loop running a pass
        When:  the pass completes (or fails)
        Then:  close_old_connections is called before AND after.

        Django opens a connection per thread and normally closes it on the
        request_finished signal. A background thread has no request cycle, so
        without this the connection is held for the life of the pod; once
        Postgres drops it, restarts or fails over, every later pass raises
        OperationalError and the loop logs an error every interval forever
        without ever reconnecting — a silent stop dressed up as noise.
        """
        from lex.core.services import activation_reconcile

        calls = []

        def _stop_after_one_pass(*_a, **_kw):
            activation_reconcile.stop_background_reconcile()
            return {}

        with patch("django.db.close_old_connections", side_effect=lambda: calls.append("closed")), \
             patch.object(
                 activation_reconcile,
                 "reconcile_pending_activations",
                 side_effect=_stop_after_one_pass,
             ):
            activation_reconcile._stop.clear()
            activation_reconcile._loop()

        self.assertGreaterEqual(
            len(calls), 2,
            "close_old_connections must run before and after each pass; "
            f"saw {len(calls)} call(s). Without both, a dropped connection ends "
            "catch-up for the life of the pod.",
        )
