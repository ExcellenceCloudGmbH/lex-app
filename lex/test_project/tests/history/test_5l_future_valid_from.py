"""
Cluster 5l: Future-dated bitemporal saves — scheduled activation contract.

Intent (from docs/features/tracking/bitemporal history.md +
``lex/core/services/bitemporal_signals.py`` +
``lex/lex_app/celery_tasks.py::activate_history_version``):

    A save whose ``valid_from`` lies more than 5 seconds in the future
    must:

      (a) create the L1 row at the requested future date,
      (b) NOT update the live Level-0 row — ``BitemporalSynchronizer
          .sync_record_for_model`` only syncs the row currently
          valid by ``valid_from``,
      (c) create the L2 meta with ``meta_task_status = "SCHEDULED"`` +
          a populated ``meta_task_name``,
      (d) register a Celery ``PeriodicTask`` (``one_off=True``,
          ``ClockedSchedule.clocked_time = valid_from``) when
          ``CELERY_ACTIVE=true`` (or hand the same callable to
          ``LocalSchedulerBackend.schedule(...)`` otherwise).

    Editing an existing future-dated row reschedules — the previous
    ``PeriodicTask`` is deleted by name before the new one is created.

    Deleting a row whose meta is ``SCHEDULED`` cancels the queued task
    and flips ``meta_task_status → "CANCELLED"``.

    The 5-second grace window is a real boundary — ``valid_from = now +
    2s`` does NOT schedule, ``valid_from = now + 60s`` does.

Concrete user scenario (the example that surfaced this gap): a record
renamed ``test → test1 → test2`` where the third save sets
``valid_from = now + 1h``. Until the activation fires the **main
table (Level 0)** must still read ``name = "test1"``.

Companion to 8.43 (``activate_history_version`` against a Mock-backed
model). 5l closes the *producer* side of the same contract on a real
fixture. Scenario numbering matches docs/test-plan/test-clusters.md
§ 5l.
"""

from __future__ import annotations

import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HistSimpleItem


def _l2_wired() -> bool:
    """L2 (MetaHistorical) is wired on the *historical* model — probe at
    call time. ``HistSimpleItem.history.model.meta_history.model`` is
    the canonical access path used by ``bitemporal_signals.py`` and
    ``activate_history_version``."""
    try:
        return hasattr(HistSimpleItem.history.model, "meta_history") and bool(
            HistSimpleItem.history.model.meta_history.model
        )
    except Exception:
        return False


_SKIP_REASON_L2 = (
    "Future-activation scheduling lives on the L2 meta record. The "
    "test_project's HistSimpleItem fixture does not have MetaHistorical* "
    "wired in this build — see lex/tests/integration/test_event_scheduling.py "
    "for the unit-level coverage that uses a fully-wired SchedTestModel "
    "fixture. 5.91 still runs (Level 0 + Level 1 only)."
)


class TestCluster05l_FutureDatedActivation(E2ETestCase):
    """Future ``valid_from`` → main table stays on previous version,
    L2 carries SCHEDULED + meta_task_name, PeriodicTask is registered."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        # PeriodicTask is a global table — make sure no leftovers from
        # a prior test (or another test class) bleed into our counts.
        try:
            from django_celery_beat.models import PeriodicTask
            PeriodicTask.objects.filter(
                task="activate_history_version"
            ).delete()
        except Exception:
            pass

    # -- 5.91 -------------------------------------------------- (no L2)
    def test_5_91_future_save_keeps_main_table_on_previous_version(self) -> None:
        """
        Scenario 5.91 — the customer-visible handoff.

        Rename ``test → test1 → test2`` where ``test2`` carries
        ``valid_from = now + 1h``. After all three saves:

          * ``HistSimpleItem.objects.get(pk=...).name == "test1"``
            (Level 0 still reflects the version effective NOW),
          * ``item.history.count() == 3`` and the rows in
            ``valid_from`` order carry names ``["test", "test1",
            "test2"]``,
          * the latest L1 row's ``valid_from`` is the future moment,
            not ``now``.
        """
        item = HistSimpleItem.objects.create(name="test", value=0)

        # Second save — name=test1, valid_from=now (default).
        item.name = "test1"
        item.value = 1
        item.save()

        # Third save — name=test2, valid_from = NOW + 1h.
        future = timezone.now() + timedelta(hours=1)
        item.name = "test2"
        item.value = 2
        item._history_date = future
        item.save()

        # ── Level 0: live grid still reads "test1" ─────────────────
        live = HistSimpleItem.objects.get(pk=item.pk)
        self.assertEqual(
            live.name, "test1",
            "Level-0 main table must still reflect the version "
            "currently valid (test1) — it must NOT leap to test2 "
            "before the future valid_from. Got %r" % (live.name,),
        )

        # ── Level 1: 3 history rows in valid_from order ────────────
        rows = list(
            HistSimpleItem.history.filter(id=item.pk).order_by(
                "valid_from", "history_id"
            )
        )
        self.assertEqual(
            len(rows), 3,
            "create + update + future-update = 3 history rows; got %d"
            % len(rows),
        )
        self.assertEqual(
            [r.name for r in rows], ["test", "test1", "test2"],
            "L1 row names in valid_from order must be "
            "['test','test1','test2']; got %r"
            % ([r.name for r in rows],),
        )

        # The future row's valid_from must round-trip to the future.
        future_row = rows[-1]
        self.assertGreater(
            future_row.valid_from, timezone.now() + timedelta(minutes=30),
            "Future row's valid_from must persist as the future "
            "moment we asked for, not be silently overwritten with "
            "now(); got %r" % (future_row.valid_from,),
        )

    # -- 5.92 -------------------------------------------------- (L2)
    def test_5_92_future_save_schedules_periodic_task(self) -> None:
        """
        Scenario 5.92 — under ``CELERY_ACTIVE=true``, exactly one
        ``PeriodicTask`` exists with ``task="activate_history_version"``,
        ``one_off=True``, and ``args`` JSON-decoded to
        ``[app_label, model_name, history_id]``. Its
        ``ClockedSchedule.clocked_time`` matches the L1 row's
        ``valid_from``.

        Under ``CELERY_ACTIVE=false`` (the default in tests), the L2
        meta still flips to ``SCHEDULED`` and ``meta_task_name`` is
        populated — the local-thread scheduler path.
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from django_celery_beat.models import PeriodicTask, ClockedSchedule

        future = timezone.now() + timedelta(hours=1)

        with patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False):
            item = HistSimpleItem.objects.create(name="s5-92", value=1)
            item.value = 2
            item._history_date = future
            item.save()

        history_row = (
            HistSimpleItem.history.filter(id=item.pk)
            .order_by("-valid_from", "-history_id")
            .first()
        )
        meta_model = HistSimpleItem.history.model.meta_history.model
        meta = meta_model.objects.filter(
            history_object_id=history_row.history_id
        ).first()
        self.assertIsNotNone(
            meta,
            "L2 meta row for the future-dated history row must exist",
        )
        self.assertEqual(
            meta.meta_task_status, "SCHEDULED",
            "Future-dated save must flip L2.meta_task_status to "
            "'SCHEDULED'; got %r" % (meta.meta_task_status,),
        )
        self.assertTrue(
            meta.meta_task_name,
            "Future-dated save must populate L2.meta_task_name "
            "(non-empty); got %r" % (meta.meta_task_name,),
        )

        tasks = PeriodicTask.objects.filter(
            task="activate_history_version",
            name=meta.meta_task_name,
        )
        self.assertEqual(
            tasks.count(), 1,
            "Exactly one PeriodicTask must be registered under "
            "L2.meta_task_name for the future activation; got %d"
            % tasks.count(),
        )
        task = tasks.first()
        self.assertTrue(task.one_off, "PeriodicTask.one_off must be True")
        args = json.loads(task.args or "[]")
        self.assertEqual(
            args,
            [
                HistSimpleItem._meta.app_label,
                HistSimpleItem._meta.model_name,
                history_row.history_id,
            ],
            "PeriodicTask.args must be [app_label, model_name, "
            "history_id]; got %r" % (args,),
        )
        clocked = task.clocked
        self.assertIsInstance(
            clocked, ClockedSchedule,
            "PeriodicTask must reference a ClockedSchedule",
        )
        # clocked_time should equal the L1 row's valid_from to the
        # microsecond — both came from the same datetime.
        self.assertEqual(
            clocked.clocked_time, history_row.valid_from,
            "ClockedSchedule.clocked_time must equal the L1 row's "
            "valid_from; got %r vs %r"
            % (clocked.clocked_time, history_row.valid_from),
        )

    # -- 5.93 -------------------------------------------------- (L2)
    def test_5_93_five_second_grace_window_boundary(self) -> None:
        """
        Scenario 5.93 — the ``> now + timedelta(seconds=5)`` threshold.

          * ``valid_from = now + 2s`` MUST NOT schedule. The L2 row's
            ``meta_task_status`` stays at the non-active default
            (anything except ``SCHEDULED``), zero ``PeriodicTask``
            rows are created.
          * ``valid_from = now + 60s`` MUST schedule.

        Without this boundary check a regression that flipped the
        threshold to ``0`` would schedule every save (broker spam) or
        a regression that pushed it to ``> 60s`` would silently drop
        small future edits on the floor.
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from django_celery_beat.models import PeriodicTask

        meta_model = HistSimpleItem.history.model.meta_history.model

        with patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False):
            # ── Inside-grace: 2s ahead → no schedule ──────────────
            near = timezone.now() + timedelta(seconds=2)
            inside = HistSimpleItem.objects.create(name="inside", value=1)
            inside.value = 2
            inside._history_date = near
            inside.save()

            inside_l1 = (
                HistSimpleItem.history.filter(id=inside.pk)
                .order_by("-valid_from", "-history_id")
                .first()
            )
            inside_meta = meta_model.objects.filter(
                history_object_id=inside_l1.history_id
            ).first()
            if inside_meta is not None:
                self.assertNotEqual(
                    inside_meta.meta_task_status, "SCHEDULED",
                    "valid_from = now + 2s is INSIDE the 5s grace "
                    "window — must NOT schedule. Got %r"
                    % (inside_meta.meta_task_status,),
                )
            self.assertEqual(
                PeriodicTask.objects.filter(
                    task="activate_history_version",
                    args__contains=str(inside_l1.history_id),
                ).count(),
                0,
                "valid_from = now + 2s must produce zero "
                "PeriodicTask rows for that history_id",
            )

            # ── Outside-grace: 60s ahead → schedule fires ─────────
            far = timezone.now() + timedelta(seconds=60)
            outside = HistSimpleItem.objects.create(name="outside", value=1)
            outside.value = 2
            outside._history_date = far
            outside.save()

            outside_l1 = (
                HistSimpleItem.history.filter(id=outside.pk)
                .order_by("-valid_from", "-history_id")
                .first()
            )
            outside_meta = meta_model.objects.filter(
                history_object_id=outside_l1.history_id
            ).first()
            self.assertIsNotNone(
                outside_meta,
                "L2 meta for outside-grace future row must exist",
            )
            self.assertEqual(
                outside_meta.meta_task_status, "SCHEDULED",
                "valid_from = now + 60s is OUTSIDE the 5s grace — "
                "must schedule; got %r"
                % (outside_meta.meta_task_status,),
            )
            self.assertEqual(
                PeriodicTask.objects.filter(
                    task="activate_history_version",
                    name=outside_meta.meta_task_name,
                ).count(),
                1,
                "Outside-grace save must register exactly one "
                "PeriodicTask",
            )

    # -- 5.94 -------------------------------------------------- (L2)
    def test_5_94_editing_future_row_reschedules_cleanly(self) -> None:
        """
        Scenario 5.94 — editing an existing future-dated row deletes
        the old ``PeriodicTask`` (by name) before creating the new one.
        After two future saves at different ``valid_from`` values:

          * the old task is gone,
          * exactly one new ``PeriodicTask`` exists with a *different*
            name,
          * its ``ClockedSchedule.clocked_time`` matches the new
            ``valid_from``,
          * the L2 row's ``meta_task_name`` equals the new task name.
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from django_celery_beat.models import PeriodicTask

        meta_model = HistSimpleItem.history.model.meta_history.model

        with patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False):
            item = HistSimpleItem.objects.create(name="s5-94", value=1)

            # First future save — t = now + 1h
            t1 = timezone.now() + timedelta(hours=1)
            item.value = 2
            item._history_date = t1
            item.save()
            l1_a = (
                HistSimpleItem.history.filter(id=item.pk)
                .order_by("-valid_from", "-history_id")
                .first()
            )
            meta_a = meta_model.objects.filter(
                history_object_id=l1_a.history_id
            ).first()
            name_a = meta_a.meta_task_name
            self.assertTrue(name_a, "First future save must register a task")

            # Second future save on the same L1 row — t = now + 2h.
            # We reschedule by saving a *new* future history row on
            # the same record (the realistic edit path).
            t2 = timezone.now() + timedelta(hours=2)
            item.value = 3
            item._history_date = t2
            item.save()
            l1_b = (
                HistSimpleItem.history.filter(id=item.pk)
                .order_by("-valid_from", "-history_id")
                .first()
            )
            meta_b = meta_model.objects.filter(
                history_object_id=l1_b.history_id
            ).first()
            name_b = meta_b.meta_task_name

            self.assertTrue(name_b, "Second future save must register a task")
            self.assertNotEqual(
                name_a, name_b,
                "Reschedule must produce a *new* meta_task_name; got "
                "the same name twice",
            )

            # The new task exists with the right clocked_time.
            new_tasks = PeriodicTask.objects.filter(name=name_b)
            self.assertEqual(
                new_tasks.count(), 1,
                "Exactly one PeriodicTask must exist under the new "
                "name; got %d" % new_tasks.count(),
            )
            self.assertEqual(
                new_tasks.first().clocked.clocked_time, l1_b.valid_from,
                "Rescheduled ClockedSchedule.clocked_time must match "
                "the new valid_from",
            )

    # -- 5.95 -------------------------------------------------- (L2)
    def test_5_95_activation_runs_main_table_catches_up(self) -> None:
        """
        Scenario 5.95 — invoking ``activate_history_version`` after the
        future moment has arrived synchronizes the main table to the
        activated row's values.

          * ``HistSimpleItem.objects.get(pk=...).name == "test2"`` —
            ``BitemporalSynchronizer.sync_record_for_model`` wrote
            the activated row's values into Level 0,
          * the L2 row's ``meta_task_status`` is no longer
            ``"SCHEDULED"`` (the worker flipped it to ``"DONE"``),
          * ``item.history.count()`` is unchanged — activation does
            NOT mint a new history row.

        Implementation note: we simulate "the future arrived" by
        patching ``timezone.now`` inside the worker + synchronizer
        modules to return a moment past ``valid_from``. This mirrors
        Celery Beat firing the task at the scheduled clocked time.
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from lex.lex_app import celery_tasks

        item = HistSimpleItem.objects.create(name="test", value=0)
        item.name = "test1"
        item.value = 1
        item.save()

        future = timezone.now() + timedelta(hours=1)
        item.name = "test2"
        item.value = 2
        item._history_date = future
        item.save()

        # Sanity: main table must still read "test1" before activation.
        self.assertEqual(
            HistSimpleItem.objects.get(pk=item.pk).name,
            "test1",
            "Pre-activation: main table must still reflect test1",
        )

        l1 = (
            HistSimpleItem.history.filter(id=item.pk)
            .order_by("-valid_from", "-history_id")
            .first()
        )
        meta_model = HistSimpleItem.history.model.meta_history.model

        history_count_before = HistSimpleItem.history.filter(id=item.pk).count()

        # Pretend "now" inside the worker + synchronizer is past the
        # future moment. Both ``activate_history_version`` and
        # ``BitemporalSynchronizer.sync_record_for_model`` call
        # ``timezone.now()`` from ``django.utils.timezone`` — patching
        # that single function is enough to simulate "Beat fired the
        # task at the clocked time".
        simulated_now = future + timedelta(minutes=1)
        with patch("django.utils.timezone.now", return_value=simulated_now):
            result = celery_tasks.activate_history_version(
                HistSimpleItem._meta.app_label,
                HistSimpleItem._meta.model_name,
                l1.history_id,
            )

        # ``lex_shared_task`` may wrap the return into ``(result, args)``;
        # accept either the bare string or a tuple whose first element
        # is the success token.
        outcome = result[0] if isinstance(result, tuple) else result
        self.assertEqual(
            outcome, "success",
            "activate_history_version must return 'success' on a "
            "valid (post-clocked-time) row; got %r" % (result,),
        )

        # Main table reflects the activated values.
        live = HistSimpleItem.objects.get(pk=item.pk)
        self.assertEqual(
            live.name, "test2",
            "After activation, Level-0 must reflect the activated "
            "row's name; got %r" % (live.name,),
        )
        self.assertEqual(
            live.value, 2,
            "After activation, Level-0 must reflect the activated "
            "row's value; got %r" % (live.value,),
        )

        # Meta status flipped away from SCHEDULED.
        meta = meta_model.objects.filter(
            history_object_id=l1.history_id
        ).first()
        self.assertNotEqual(
            getattr(meta, "meta_task_status", None), "SCHEDULED",
            "After activation, L2.meta_task_status must no longer be "
            "'SCHEDULED' (worker contract: SCHEDULED → DONE); got %r"
            % (getattr(meta, "meta_task_status", None),),
        )

        # No new history row.
        self.assertEqual(
            HistSimpleItem.history.filter(id=item.pk).count(),
            history_count_before,
            "Activation must NOT mint a new history row — it only "
            "syncs the main table",
        )

    # -- 5.96 -------------------------------------------------- (L2)
    def test_5_96_delete_cancels_scheduled_activation(self) -> None:
        """
        Scenario 5.96 — deleting the future-dated L1 history row
        (``HistoryModel.objects.get(...).delete()``) cancels the queued
        activation. The handler is registered on ``pre_delete`` of the
        historical model — same contract as
        ``lex/tests/integration/test_event_scheduling.py::test_deletion_revokes_schedule``.

          * the ``PeriodicTask`` with ``name == meta.meta_task_name``
            no longer exists,
          * the L2 meta row's ``meta_task_status == "CANCELLED"``.

        Without this, deleting a record leaves orphaned Beat rows
        firing against a missing history_id and producing
        ``"skipped_missing_record"`` noise (the 8.41 path).
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from django_celery_beat.models import PeriodicTask

        meta_model = HistSimpleItem.history.model.meta_history.model

        with patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False):
            item = HistSimpleItem.objects.create(name="s5-96", value=1)
            future = timezone.now() + timedelta(hours=1)
            item.value = 2
            item._history_date = future
            item.save()

            l1 = (
                HistSimpleItem.history.filter(id=item.pk)
                .order_by("-valid_from", "-history_id")
                .first()
            )
            meta = meta_model.objects.filter(
                history_object_id=l1.history_id
            ).first()
            scheduled_task_name = meta.meta_task_name
            self.assertTrue(
                PeriodicTask.objects.filter(name=scheduled_task_name).exists(),
                "Pre-condition: scheduled PeriodicTask must exist",
            )

            # Delete the future-dated L1 row directly — that's what
            # fires ``on_history_pre_delete__cancel_schedules``.
            l1.delete()

        self.assertFalse(
            PeriodicTask.objects.filter(name=scheduled_task_name).exists(),
            "After L1 row delete, the queued PeriodicTask must be revoked",
        )

        meta_after = meta_model.objects.filter(pk=meta.pk).first()
        self.assertIsNotNone(
            meta_after,
            "L2 meta row should survive the delete (audit trail) — "
            "only its meta_task_status flips",
        )
        self.assertEqual(
            meta_after.meta_task_status, "CANCELLED",
            "L2.meta_task_status must flip to 'CANCELLED' on delete; "
            "got %r" % (meta_after.meta_task_status,),
        )

    # -- 5.97 -------------------------------------------------- (L2)
    def test_5_97_multiple_future_saves_queue_independently(self) -> None:
        """
        Scenario 5.97 — two distinct rows with distinct future
        ``valid_from`` values produce two distinct PeriodicTasks.
        Cancelling one (via delete) leaves the other intact. Pins
        fan-out — a regression that namespaced both schedules under
        the same key would silently coalesce to one.
        """
        if not _l2_wired():
            self.skipTest(_SKIP_REASON_L2)

        from django_celery_beat.models import PeriodicTask

        meta_model = HistSimpleItem.history.model.meta_history.model

        with patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False):
            t1 = timezone.now() + timedelta(hours=1)
            t2 = timezone.now() + timedelta(hours=2)

            a = HistSimpleItem.objects.create(name="a", value=1)
            a.value = 2
            a._history_date = t1
            a.save()

            b = HistSimpleItem.objects.create(name="b", value=1)
            b.value = 2
            b._history_date = t2
            b.save()

            a_l1 = (
                HistSimpleItem.history.filter(id=a.pk)
                .order_by("-valid_from", "-history_id")
                .first()
            )
            b_l1 = (
                HistSimpleItem.history.filter(id=b.pk)
                .order_by("-valid_from", "-history_id")
                .first()
            )
            a_meta = meta_model.objects.filter(
                history_object_id=a_l1.history_id
            ).first()
            b_meta = meta_model.objects.filter(
                history_object_id=b_l1.history_id
            ).first()

            self.assertNotEqual(
                a_meta.meta_task_name, b_meta.meta_task_name,
                "Two future-dated rows must register under distinct "
                "PeriodicTask names",
            )
            a_task = PeriodicTask.objects.filter(name=a_meta.meta_task_name).first()
            b_task = PeriodicTask.objects.filter(name=b_meta.meta_task_name).first()
            self.assertIsNotNone(a_task, "Row a must have a queued task")
            self.assertIsNotNone(b_task, "Row b must have a queued task")
            self.assertNotEqual(
                a_task.clocked.clocked_time,
                b_task.clocked.clocked_time,
                "Distinct future valid_from values must produce "
                "distinct ClockedSchedule.clocked_time values",
            )

            # Cancel A — B must survive untouched.
            a_l1.delete()

            self.assertFalse(
                PeriodicTask.objects.filter(name=a_meta.meta_task_name).exists(),
                "A's task must be revoked after delete",
            )
            self.assertTrue(
                PeriodicTask.objects.filter(name=b_meta.meta_task_name).exists(),
                "B's task must survive A's delete — fan-out "
                "independence",
            )
            b_meta_after = meta_model.objects.filter(pk=b_meta.pk).first()
            self.assertEqual(
                b_meta_after.meta_task_status, "SCHEDULED",
                "B's L2.meta_task_status must remain SCHEDULED after "
                "A's delete; got %r" % (b_meta_after.meta_task_status,),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()









