"""Cluster 7o: Scheduled calculations — fire a calculation later, with dedupe.

Intent
------
``ScheduledCalculation`` is the framework's answer to "someone is still
typing, but I want one consolidated run later". The whole user-facing
contract lives in ``docs/features/processing/scheduled-calculations.md``;
the short version of what this cluster pins:

* ``ScheduledCalculation.ensure(target=…, run_at=…, dedupe_tag=…)`` is
  the **only** entry-point a customer reaches. First call records a
  ``PENDING`` row + a one-shot ``django-celery-beat`` ``PeriodicTask``;
  subsequent calls with the same ``(target, dedupe_tag)`` are governed
  by ``on_conflict``: ``"dedupe"`` (default — no-op), ``"replace"``
  (revoke old, schedule new), ``"debounce"`` (replace only if the new
  ``run_at`` is strictly later than the existing one).
* Validation is loud: missing/unsaved target, empty ``dedupe_tag``,
  unknown ``on_conflict``, naive datetime, bogus ``run_at`` type all
  raise — silently dropping a schedule would leave a customer believing
  a calculation is queued for tonight when it is not. Likewise the
  whole subsystem hard-requires ``CELERY_ACTIVE=true`` and raises
  ``ScheduledCalculationUnavailable`` otherwise.
* ``ScheduledCalculation.cancel(schedule)`` flips ``PENDING → CANCELLED``
  and deletes the queued ``PeriodicTask``. Idempotent on terminal states
  (``FIRED`` / ``CANCELLED`` / ``MISSED``) and safe on ``None``.
* The Celery worker entry-point ``run_scheduled_calculation`` is what
  beat fires at ``run_at``. Outcomes:
    - happy path → schedule ``→ FIRED``, ``fired_at`` populated, the
      target's normal calculation pipeline takes over
      (``target.save()`` with ``is_calculated=IN_PROGRESS``);
    - schedule already terminal → no-op, returns ``"already_<status>"``;
    - schedule row deleted between ``ensure`` and beat firing →
      ``"schedule_not_found"``;
    - target row deleted in the meantime → schedule ``→ MISSED`` with
      ``error_message="target_deleted_before_fire"`` (recorded
      explicitly so an operator can see *why* a "scheduled" report
      never produced a number — vs the row simply vanishing).
    - target is not a ``CalculationModel`` → schedule ``→ MISSED`` with
      ``error_message="target_is_not_a_calculation_model"``.

A regression that lost any of these would surface as silent data loss
in the worst possible places: the report Klaus and Miriam expected
"by 18:00 tonight" simply does not run, and there is no audit row to
explain why.

Cluster 7o — scenarios 7.176–7.192. Type: I (TestCase — exercises the
two ``ScheduledCalculation`` public classmethods + the
``run_scheduled_calculation`` Celery worker against real
``ScheduledCalculation``, ``PeriodicTask`` and ``CalculationModel``
rows; the only mock is ``CalculationModel.should_use_celery → False``
for the worker-fires-pipeline scenario, because the test environment
has no broker and we want the dispatch to land synchronously inline).
Covers: ``lex/lex_app/scheduling.py`` (``ScheduledCalculation`` model,
``ensure``, ``cancel``, ``_coerce_run_at``, ``_celery_active``,
``_register_periodic_task``, ``_delete_periodic_task``,
``ScheduledCalculationUnavailable``); ``lex/lex_app/celery_tasks.py``
(``run_scheduled_calculation``).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7o_scheduled_calculations.py -v
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone as _stdlib_timezone
from unittest.mock import patch

import pytest
from django.utils import timezone

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.scheduling import (
    ScheduledCalculation,
    ScheduledCalculationUnavailable,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AtomicCalc

pytestmark = pytest.mark.calculations


def _aware_now():
    """Return a timezone-aware ``datetime`` independent of ``USE_TZ``.

    Some downstream projects run with ``USE_TZ=False`` (the test host
    here is one — see ``lex/lex_app/settings.py``: ``USE_TZ`` flips off
    for ``DATABASE_DEPLOYMENT_TARGET in ("default", "GCP")``). In that
    setup ``django.utils.timezone.now()`` returns a NAIVE datetime,
    which ``ScheduledCalculation._coerce_run_at`` rightly rejects.
    Cluster 7o asserts the documented contract on aware datetimes, so
    we mint our own UTC-aware "now" rather than depending on the host
    project's ``USE_TZ`` flag.
    """
    return datetime.now(_stdlib_timezone.utc)


def _to_utc_ts(dt):
    """Convert a (possibly naive) datetime to a POSIX timestamp.

    USE_TZ-agnostic helper for comparing the *same moment in time*
    across the boundary between aware-input (what the customer hands
    ``ensure()``) and naive-storage (what Postgres returns when the
    project runs ``USE_TZ=False``). Naive values are interpreted in
    the project's ``TIME_ZONE`` — exactly what Django does internally
    when round-tripping a ``DateTimeField`` in a ``USE_TZ=False``
    project, so this is the comparison that matches the framework's
    own conversion rules.
    """
    if dt.tzinfo is None:
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.timestamp()


def _unwrap_task(result):
    """Strip ``lex_shared_task``'s ``(inner_result, args)`` envelope.

    ``run_scheduled_calculation`` is decorated with ``@lex_shared_task``,
    whose wrapper returns ``(inner, args)`` when the body completes
    (this is the shape ``CallbackTask._extract_model_instances`` relies
    on — see test 8.15 in the cluster plan). Tests that invoke the
    task directly (no Celery in the test environment) get the tuple
    back, so we peel it.
    """
    if isinstance(result, tuple) and len(result) == 2:
        return result[0]
    return result


def _purge_periodic_tasks():
    """Wipe any leftover beat rows from a prior test/process."""
    try:
        from django_celery_beat.models import PeriodicTask, ClockedSchedule

        PeriodicTask.objects.filter(task="lex_scheduled_calculation").delete()
        # ClockedSchedules are shared by clocked_time; only delete orphans.
        ClockedSchedule.objects.filter(periodictask__isnull=True).delete()
    except Exception:  # pragma: no cover — defensive
        pass


class _SchedulingTestBase(E2ETestCase):
    """Shared setup: registers calc fixtures, forces ``CELERY_ACTIVE=true``,
    cleans schedule + beat tables between tests.

    ``ScheduledCalculation`` itself is a real ``lex_app`` model with a
    standard migration — its table exists in the test DB; we just need
    to keep it empty between tests so dedupe assertions are clean.
    """

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()

        # E2ETestCase forces CELERY_ACTIVE=False by default. Scheduling
        # *requires* CELERY_ACTIVE=true (see _celery_active()), so we
        # override here. We never actually open a broker connection —
        # _register_periodic_task / _delete_periodic_task only touch
        # the django-celery-beat ORM tables.
        self._celery_env_patch = patch.dict(
            os.environ, {"CELERY_ACTIVE": "true"}, clear=False,
        )
        self._celery_env_patch.start()

        ScheduledCalculation.objects.all().delete()
        _purge_periodic_tasks()

    def tearDown(self):
        ScheduledCalculation.objects.all().delete()
        _purge_periodic_tasks()
        self._celery_env_patch.stop()
        super().tearDown()

    @staticmethod
    def _periodic_task_count():
        from django_celery_beat.models import PeriodicTask

        return PeriodicTask.objects.filter(task="lex_scheduled_calculation").count()

    @staticmethod
    def _get_periodic_task(name):
        from django_celery_beat.models import PeriodicTask

        return PeriodicTask.objects.filter(name=name).first()

    def assertSameMoment(self, a, b, msg=None, delta_seconds: float = 1.0):
        """USE_TZ-agnostic equality of two datetimes.

        Compares the absolute moment in time, tolerating the
        aware-vs-naive split that ``USE_TZ=False`` introduces between
        what the customer passes to ``ensure()`` (always aware per the
        documented contract) and what Django round-trips back from
        ``TIMESTAMP WITHOUT TIME ZONE`` columns (always naive in the
        project's ``TIME_ZONE``).
        """
        ts_a, ts_b = _to_utc_ts(a), _to_utc_ts(b)
        self.assertAlmostEqual(
            ts_a, ts_b, delta=delta_seconds,
            msg=(msg or f"datetimes differ as moments: {a!r} vs {b!r}"),
        )


class TestCluster07o_Ensure(_SchedulingTestBase):
    """7o — ``ScheduledCalculation.ensure()`` public classmethod."""

    # ------------------------------------------------------------------
    # 7.176 — first ensure() creates PENDING row + PeriodicTask
    # ------------------------------------------------------------------
    def test_07_176_ensure_first_time_creates_pending_and_periodic_task(self):
        """
        Scenario 7.176: ensure() with no existing schedule.
        Given: A saved AtomicCalc target and no existing schedule.
        When:  ensure(target, run_at=+1h, dedupe_tag="daily-report") is called.
        Then:  Returns a PENDING ScheduledCalculation row whose run_at
               matches; exactly one ``lex_scheduled_calculation`` PeriodicTask
               exists, named to the value stored on the row, marked one_off,
               with a ClockedSchedule at run_at.
        """
        target = AtomicCalc.objects.create(name="report-A")
        run_at = _aware_now() + timedelta(hours=1)

        schedule = ScheduledCalculation.ensure(
            target=target,
            run_at=run_at,
            dedupe_tag="daily-report",
        )

        self.assertIsNotNone(schedule.pk, "ensure() must persist the schedule row")
        self.assertEqual(
            schedule.status,
            ScheduledCalculation.PENDING,
            "First ensure() must produce a PENDING row, got %r" % (schedule.status,),
        )
        self.assertSameMoment(
            schedule.run_at, run_at,
            "run_at must round-trip through ensure() unchanged (same moment)",
        )
        self.assertEqual(
            schedule.dedupe_tag, "daily-report",
            "dedupe_tag must be persisted as supplied",
        )
        self.assertEqual(schedule.target_object_id, str(target.pk))
        self.assertTrue(
            schedule.periodic_task_name,
            "ensure() must populate periodic_task_name so cancel/replace can revoke",
        )

        from django_celery_beat.models import PeriodicTask

        beat_row = self._get_periodic_task(schedule.periodic_task_name)
        self.assertIsNotNone(
            beat_row,
            "A django-celery-beat PeriodicTask must exist with the name "
            "stored on the schedule row",
        )
        self.assertTrue(
            beat_row.one_off,
            "Scheduled calcs must register a one-shot PeriodicTask "
            "(one_off=True) — recurring schedules are not in this feature's scope",
        )
        self.assertEqual(beat_row.task, "lex_scheduled_calculation")
        self.assertSameMoment(
            beat_row.clocked.clocked_time,
            run_at,
            "ClockedSchedule.clocked_time must match the requested run_at",
        )
        self.assertEqual(
            self._periodic_task_count(), 1,
            "Exactly one PeriodicTask should be registered for one ensure()",
        )

    # ------------------------------------------------------------------
    # 7.177 — default on_conflict=dedupe is a no-op on duplicate
    # ------------------------------------------------------------------
    def test_07_177_ensure_default_dedupe_returns_existing_no_new_task(self):
        """
        Scenario 7.177: second ensure() with same (target, tag) — default dedupe.
        Given: An existing PENDING schedule for (target, "daily-report").
        When:  ensure(...) is called again with a *different* run_at and
               on_conflict left at the default ("dedupe").
        Then:  Same row pk is returned; run_at is *unchanged*; only the
               original PeriodicTask exists (no second task registered);
               periodic_task_name is unchanged.
        """
        target = AtomicCalc.objects.create(name="report-A")
        first_run_at = _aware_now() + timedelta(hours=1)
        first = ScheduledCalculation.ensure(
            target=target, run_at=first_run_at, dedupe_tag="daily-report",
        )
        original_task_name = first.periodic_task_name

        second_run_at = _aware_now() + timedelta(hours=3)
        second = ScheduledCalculation.ensure(
            target=target, run_at=second_run_at, dedupe_tag="daily-report",
        )

        self.assertEqual(
            second.pk, first.pk,
            "dedupe must return the existing row, not a new one",
        )
        self.assertSameMoment(
            second.run_at, first_run_at,
            "dedupe must NOT update run_at — the deadline does not move "
            "when more inputs arrive (the whole point of the contract)",
        )
        self.assertEqual(
            second.periodic_task_name, original_task_name,
            "dedupe must leave the original PeriodicTask name in place",
        )
        self.assertEqual(
            self._periodic_task_count(), 1,
            "dedupe must NOT register a second PeriodicTask",
        )
        self.assertEqual(
            ScheduledCalculation.objects.filter(status=ScheduledCalculation.PENDING).count(),
            1,
            "Exactly one PENDING row should exist for (target, tag)",
        )

    # ------------------------------------------------------------------
    # 7.178 — on_conflict=replace revokes old, registers new at new run_at
    # ------------------------------------------------------------------
    def test_07_178_ensure_replace_revokes_old_and_uses_new_run_at(self):
        """
        Scenario 7.178: ensure() with on_conflict="replace".
        Given: An existing PENDING schedule for (target, tag).
        When:  ensure(... on_conflict="replace") is called with a new run_at.
        Then:  Same row pk; run_at updated to the new value; the OLD
               PeriodicTask is gone; a NEW PeriodicTask exists at the new
               clocked_time. Exactly one PeriodicTask remains.
        """
        target = AtomicCalc.objects.create(name="report-B")
        first_run_at = _aware_now() + timedelta(hours=1)
        first = ScheduledCalculation.ensure(
            target=target, run_at=first_run_at, dedupe_tag="rpt",
        )
        original_task_name = first.periodic_task_name

        new_run_at = _aware_now() + timedelta(hours=4)
        replaced = ScheduledCalculation.ensure(
            target=target, run_at=new_run_at, dedupe_tag="rpt",
            on_conflict="replace",
        )

        self.assertEqual(replaced.pk, first.pk, "replace updates in place")
        self.assertSameMoment(
            replaced.run_at, new_run_at,
            "replace must adopt the new run_at (same moment)",
        )
        self.assertNotEqual(
            replaced.periodic_task_name, original_task_name,
            "replace must register a fresh PeriodicTask name",
        )
        self.assertIsNone(
            self._get_periodic_task(original_task_name),
            "Old PeriodicTask must be deleted by replace",
        )
        new_beat = self._get_periodic_task(replaced.periodic_task_name)
        self.assertIsNotNone(
            new_beat, "New PeriodicTask must exist after replace",
        )
        self.assertSameMoment(new_beat.clocked.clocked_time, new_run_at)
        self.assertEqual(self._periodic_task_count(), 1)

    # ------------------------------------------------------------------
    # 7.179 — debounce: replaces only when new run_at strictly later
    # ------------------------------------------------------------------
    def test_07_179_ensure_debounce_replaces_only_when_strictly_later(self):
        """
        Scenario 7.179: ensure() with on_conflict="debounce".
        Given: An existing PENDING schedule at T+1h.
        When:  (a) ensure(... debounce) at T+30m (earlier) — must be a no-op;
               (b) ensure(... debounce) at T+1h (equal) — must be a no-op;
               (c) ensure(... debounce) at T+2h (later) — must replace.
        Then:  Cases (a) and (b) leave run_at and PeriodicTask name unchanged;
               case (c) updates run_at and rotates the PeriodicTask.
        """
        target = AtomicCalc.objects.create(name="report-C")
        base = _aware_now() + timedelta(hours=1)
        first = ScheduledCalculation.ensure(
            target=target, run_at=base, dedupe_tag="rpt",
        )
        original_task_name = first.periodic_task_name

        # Earlier — no-op.
        earlier = ScheduledCalculation.ensure(
            target=target,
            run_at=base - timedelta(minutes=30),
            dedupe_tag="rpt",
            on_conflict="debounce",
        )
        self.assertSameMoment(earlier.run_at, base, "debounce-earlier is a no-op on run_at")
        self.assertEqual(
            earlier.periodic_task_name, original_task_name,
            "debounce-earlier must not rotate the PeriodicTask",
        )

        # Exactly equal — also no-op (debounce uses strict ``>``).
        equal = ScheduledCalculation.ensure(
            target=target, run_at=base, dedupe_tag="rpt", on_conflict="debounce",
        )
        self.assertSameMoment(equal.run_at, base, "debounce-equal is a no-op on run_at")
        self.assertEqual(equal.periodic_task_name, original_task_name)

        # Later — replaces.
        later_dt = base + timedelta(hours=1)
        later = ScheduledCalculation.ensure(
            target=target, run_at=later_dt, dedupe_tag="rpt",
            on_conflict="debounce",
        )
        self.assertSameMoment(
            later.run_at, later_dt,
            "debounce-later must adopt the new (later) run_at",
        )
        self.assertNotEqual(
            later.periodic_task_name, original_task_name,
            "debounce-later must rotate the PeriodicTask",
        )
        self.assertIsNone(self._get_periodic_task(original_task_name))
        self.assertEqual(self._periodic_task_count(), 1)

    # ------------------------------------------------------------------
    # 7.180 — ensure() rejects None / unsaved target
    # ------------------------------------------------------------------
    def test_07_180_ensure_rejects_unsaved_or_none_target(self):
        """
        Scenario 7.180: ensure() with no usable target.
        Given: A None target, OR an instance that has never been saved.
        When:  ensure(...) is called.
        Then:  Both raise ValueError before creating any schedule row or
               PeriodicTask — a schedule referencing an unsaved target
               could never be resolved by the worker.
        """
        with self.assertRaises(ValueError):
            ScheduledCalculation.ensure(
                target=None,
                run_at=_aware_now() + timedelta(hours=1),
                dedupe_tag="x",
            )

        unsaved = AtomicCalc(name="never-saved")
        self.assertIsNone(unsaved.pk, "precondition: target.pk is None")
        with self.assertRaises(ValueError):
            ScheduledCalculation.ensure(
                target=unsaved,
                run_at=_aware_now() + timedelta(hours=1),
                dedupe_tag="x",
            )

        self.assertEqual(
            ScheduledCalculation.objects.count(), 0,
            "No ScheduledCalculation row should be created for invalid input",
        )
        self.assertEqual(self._periodic_task_count(), 0)

    # ------------------------------------------------------------------
    # 7.181 — ensure() rejects empty dedupe_tag
    # ------------------------------------------------------------------
    def test_07_181_ensure_rejects_empty_dedupe_tag(self):
        """
        Scenario 7.181: ensure() with empty dedupe_tag.
        Given: A valid target and run_at.
        When:  ensure(target, run_at, dedupe_tag="") is called.
        Then:  Raises ValueError — the dedupe contract is explicit by
               design; an empty tag would let every caller silently
               share one anonymous schedule slot.
        """
        target = AtomicCalc.objects.create(name="t")
        with self.assertRaises(ValueError):
            ScheduledCalculation.ensure(
                target=target,
                run_at=_aware_now() + timedelta(hours=1),
                dedupe_tag="",
            )
        self.assertEqual(ScheduledCalculation.objects.count(), 0)

    # ------------------------------------------------------------------
    # 7.182 — ensure() rejects unknown on_conflict
    # ------------------------------------------------------------------
    def test_07_182_ensure_rejects_unknown_on_conflict(self):
        """
        Scenario 7.182: ensure() with bogus on_conflict.
        Given: A valid target/run_at/tag.
        When:  ensure(... on_conflict="merge") is called.
        Then:  Raises ValueError listing the supported strategies — a
               typo here must fail loudly, not silently fall back to
               dedupe.
        """
        target = AtomicCalc.objects.create(name="t")
        with self.assertRaises(ValueError):
            ScheduledCalculation.ensure(
                target=target,
                run_at=_aware_now() + timedelta(hours=1),
                dedupe_tag="x",
                on_conflict="merge",
            )

    # ------------------------------------------------------------------
    # 7.183 — run_at coercion: timedelta / aware dt / naive dt / bogus type
    # ------------------------------------------------------------------
    def test_07_183_run_at_coercion_rules(self):
        """
        Scenario 7.183: ``run_at`` accepts ``timedelta`` and aware ``datetime``,
        rejects naive ``datetime`` and bogus types.
        Given: Various ``run_at`` shapes against the same target.
        When:  ensure() is called.
        Then:  - timedelta → schedule.run_at == now+delta (within tolerance);
               - aware datetime → schedule.run_at == that datetime;
               - naive datetime → ValueError ("Naive datetimes are rejected");
               - non-datetime/timedelta value → TypeError.
        """
        target = AtomicCalc.objects.create(name="t")

        before_ts = _to_utc_ts(_aware_now())
        s = ScheduledCalculation.ensure(
            target=target, run_at=timedelta(minutes=30), dedupe_tag="td",
        )
        delta_seconds = _to_utc_ts(s.run_at) - before_ts
        self.assertGreaterEqual(
            delta_seconds, 30 * 60 - 5,
            "timedelta(minutes=30) must coerce to roughly now+30m",
        )
        self.assertLessEqual(delta_seconds, 30 * 60 + 5)

        aware = _aware_now() + timedelta(hours=2)
        s2 = ScheduledCalculation.ensure(
            target=target, run_at=aware, dedupe_tag="td2",
        )
        self.assertSameMoment(s2.run_at, aware)

        naive = datetime(2099, 1, 1, 12, 0, 0)  # no tzinfo
        with self.assertRaises(ValueError):
            ScheduledCalculation.ensure(
                target=target, run_at=naive, dedupe_tag="td3",
            )

        with self.assertRaises(TypeError):
            ScheduledCalculation.ensure(
                target=target, run_at="tomorrow", dedupe_tag="td4",
            )

    # ------------------------------------------------------------------
    # 7.184 — CELERY_ACTIVE=false → ScheduledCalculationUnavailable
    # ------------------------------------------------------------------
    def test_07_184_ensure_requires_celery_active(self):
        """
        Scenario 7.184: ensure() without ``CELERY_ACTIVE``.
        Given: ``CELERY_ACTIVE`` env var unset / set to "false".
        When:  ensure(...) is called.
        Then:  Raises ScheduledCalculationUnavailable. No schedule row
               and no PeriodicTask must be created — silently dropping
               a future report would be the worst possible failure mode.
        """
        target = AtomicCalc.objects.create(name="t")

        # Override the test class's CELERY_ACTIVE=true patch for this scenario.
        with patch.dict(os.environ, {"CELERY_ACTIVE": "false"}, clear=False):
            with self.assertRaises(ScheduledCalculationUnavailable):
                ScheduledCalculation.ensure(
                    target=target,
                    run_at=_aware_now() + timedelta(hours=1),
                    dedupe_tag="x",
                )

        self.assertEqual(ScheduledCalculation.objects.count(), 0)
        self.assertEqual(self._periodic_task_count(), 0)


class TestCluster07o_Cancel(_SchedulingTestBase):
    """7o — ``ScheduledCalculation.cancel()`` public classmethod."""

    # ------------------------------------------------------------------
    # 7.185 — cancel(PENDING) flips CANCELLED + deletes PeriodicTask
    # ------------------------------------------------------------------
    def test_07_185_cancel_pending_persists_cancelled_and_deletes_task(self):
        """
        Scenario 7.185: cancel() on a PENDING schedule.
        Given: A PENDING schedule + its PeriodicTask.
        When:  ScheduledCalculation.cancel(schedule) is called.
        Then:  Returns ``cancelled=True`` with status=CANCELLED;
               schedule.status persists CANCELLED;
               the PeriodicTask is deleted.
        """
        target = AtomicCalc.objects.create(name="t")
        schedule = ScheduledCalculation.ensure(
            target=target,
            run_at=_aware_now() + timedelta(hours=1),
            dedupe_tag="x",
        )
        task_name = schedule.periodic_task_name
        self.assertIsNotNone(self._get_periodic_task(task_name), "precondition")

        report = ScheduledCalculation.cancel(schedule)

        self.assertTrue(
            report["cancelled"], "cancel() must report cancelled=True for PENDING",
        )
        self.assertEqual(report["status"], ScheduledCalculation.CANCELLED)
        schedule.refresh_from_db()
        self.assertEqual(
            schedule.status, ScheduledCalculation.CANCELLED,
            "PENDING → CANCELLED must persist",
        )
        self.assertIsNone(
            self._get_periodic_task(task_name),
            "cancel() must delete the queued PeriodicTask",
        )

    # ------------------------------------------------------------------
    # 7.186 — cancel() on terminal state is a no-op
    # ------------------------------------------------------------------
    def test_07_186_cancel_on_terminal_state_is_noop(self):
        """
        Scenario 7.186: cancel() on a non-PENDING schedule is idempotent.
        Given: A schedule already at FIRED (or CANCELLED).
        When:  cancel(schedule) is called.
        Then:  Returns ``cancelled=False, reason="not_pending"``;
               status is unchanged.
        """
        target = AtomicCalc.objects.create(name="t")
        schedule = ScheduledCalculation.ensure(
            target=target,
            run_at=_aware_now() + timedelta(hours=1),
            dedupe_tag="x",
        )
        # Force terminal state without going through the worker path.
        schedule.status = ScheduledCalculation.FIRED
        schedule.save(update_fields=["status"])

        report = ScheduledCalculation.cancel(schedule)

        self.assertFalse(report["cancelled"])
        self.assertEqual(report["reason"], "not_pending")
        self.assertEqual(report["status"], ScheduledCalculation.FIRED)
        schedule.refresh_from_db()
        self.assertEqual(
            schedule.status, ScheduledCalculation.FIRED,
            "Terminal state must not be overwritten by cancel()",
        )

    # ------------------------------------------------------------------
    # 7.187 — cancel(None) / cancel(unsaved) is safe
    # ------------------------------------------------------------------
    def test_07_187_cancel_on_none_or_unsaved_is_safe_noop(self):
        """
        Scenario 7.187: cancel() called with no row.
        Given: ``None``, or a not-yet-saved ``ScheduledCalculation`` instance.
        When:  cancel(schedule) is called.
        Then:  Returns ``cancelled=False`` with a ``missing_schedule``
               reason — never raises.
        """
        report_none = ScheduledCalculation.cancel(None)
        self.assertFalse(report_none["cancelled"])
        self.assertEqual(report_none["reason"], "missing_schedule")

        unsaved = ScheduledCalculation()
        self.assertIsNone(unsaved.pk)
        report_unsaved = ScheduledCalculation.cancel(unsaved)
        self.assertFalse(report_unsaved["cancelled"])
        self.assertEqual(report_unsaved["reason"], "missing_schedule")


class TestCluster07o_WorkerEntryPoint(_SchedulingTestBase):
    """7o — ``run_scheduled_calculation`` Celery worker entry point."""

    # ------------------------------------------------------------------
    # 7.188 — happy path: schedule → FIRED, target gets IN_PROGRESS save
    # ------------------------------------------------------------------
    def test_07_188_run_fires_pipeline_and_marks_schedule_fired(self):
        """
        Scenario 7.188: beat fires the schedule at run_at.
        Given: A PENDING schedule pointing at a saved AtomicCalc.
        When:  ``_unwrap_task(run_scheduled_calculation(schedule.pk))`` runs (as beat would).
        Then:  schedule.status == FIRED, schedule.fired_at populated;
               target's calculation pipeline ran end-to-end (target
               ends at SUCCESS via the normal calculation state machine
               — proving the scheduling layer correctly *handed off* to
               the existing pipeline rather than re-implementing it).
        """
        from lex.lex_app.celery_tasks import run_scheduled_calculation

        target = AtomicCalc.objects.create(name="report-fire")
        schedule = ScheduledCalculation.ensure(
            target=target,
            run_at=_aware_now() + timedelta(hours=1),
            dedupe_tag="rpt",
        )

        # The test environment has no broker, so force the calculation
        # pipeline onto the synchronous fallback once it's triggered.
        with patch.object(CalculationModel, "should_use_celery", return_value=False):
            result = _unwrap_task(run_scheduled_calculation(schedule.pk))

        self.assertEqual(result, "fired")
        schedule.refresh_from_db()
        self.assertEqual(
            schedule.status, ScheduledCalculation.FIRED,
            "Worker must persist FIRED on the schedule row",
        )
        self.assertIsNotNone(
            schedule.fired_at,
            "fired_at must be populated when the worker actually fires",
        )
        target.refresh_from_db()
        self.assertEqual(
            target.is_calculated, CalculationModel.SUCCESS,
            "Worker handing off to target.save(IN_PROGRESS) must drive "
            "the normal calc pipeline through to SUCCESS — got %r"
            % (target.is_calculated,),
        )

    # ------------------------------------------------------------------
    # 7.189 — target deleted between ensure and fire → schedule MISSED
    # ------------------------------------------------------------------
    def test_07_189_run_marks_missed_when_target_deleted(self):
        """
        Scenario 7.189: target row is deleted before beat fires.
        Given: A PENDING schedule whose target row has since been deleted.
        When:  _unwrap_task(run_scheduled_calculation(schedule.pk)) runs.
        Then:  schedule.status == MISSED with error_message
               'target_deleted_before_fire' so an operator can see *why*
               the scheduled report never produced a number.
        """
        from lex.lex_app.celery_tasks import run_scheduled_calculation

        target = AtomicCalc.objects.create(name="will-be-deleted")
        schedule = ScheduledCalculation.ensure(
            target=target,
            run_at=_aware_now() + timedelta(hours=1),
            dedupe_tag="rpt",
        )
        target_pk = target.pk
        target.delete()

        result = _unwrap_task(run_scheduled_calculation(schedule.pk))

        self.assertEqual(result, "missed_target_deleted")
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, ScheduledCalculation.MISSED)
        self.assertEqual(schedule.error_message, "target_deleted_before_fire")
        self.assertIsNotNone(
            schedule.fired_at,
            "fired_at must be set even on MISSED so the audit trail "
            "records when beat actually attempted dispatch",
        )
        self.assertEqual(
            AtomicCalc.objects.filter(pk=target_pk).count(), 0,
            "precondition: target really was deleted",
        )

    # ------------------------------------------------------------------
    # 7.190 — schedule row missing → returns schedule_not_found
    # ------------------------------------------------------------------
    def test_07_190_run_returns_schedule_not_found_for_missing_pk(self):
        """
        Scenario 7.190: schedule row deleted before beat fires.
        Given: A schedule pk that no longer exists in the DB.
        When:  run_scheduled_calculation(missing_pk) runs.
        Then:  Returns 'schedule_not_found' (no exception). beat keeps
               working; nothing else gets corrupted.
        """
        from lex.lex_app.celery_tasks import run_scheduled_calculation

        target = AtomicCalc.objects.create(name="t")
        schedule = ScheduledCalculation.ensure(
            target=target,
            run_at=_aware_now() + timedelta(hours=1),
            dedupe_tag="rpt",
        )
        pk = schedule.pk
        schedule.delete()

        self.assertEqual(
            _unwrap_task(run_scheduled_calculation(pk)),
            "schedule_not_found",
        )

    # ------------------------------------------------------------------
    # 7.191 — schedule already terminal → no-op
    # ------------------------------------------------------------------
    def test_07_191_run_is_noop_when_schedule_already_terminal(self):
        """
        Scenario 7.191: race / late delivery — schedule already terminal.
        Given: A schedule already flipped to FIRED (or CANCELLED).
        When:  _unwrap_task(run_scheduled_calculation(schedule.pk)) is invoked again.
        Then:  Returns 'already_<status>' and does NOT touch target;
               schedule.status is unchanged.
        """
        from lex.lex_app.celery_tasks import run_scheduled_calculation

        target = AtomicCalc.objects.create(name="report-twice")
        schedule = ScheduledCalculation.ensure(
            target=target,
            run_at=_aware_now() + timedelta(hours=1),
            dedupe_tag="rpt",
        )
        schedule.status = ScheduledCalculation.CANCELLED
        schedule.save(update_fields=["status"])

        # Spy on target.save — it must NOT be touched by a second run.
        with patch.object(
            AtomicCalc, "save", autospec=True, side_effect=AtomicCalc.save,
        ) as save_spy:
            result = _unwrap_task(run_scheduled_calculation(schedule.pk))

        self.assertEqual(result, "already_cancelled")
        save_spy.assert_not_called()
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, ScheduledCalculation.CANCELLED)
        target.refresh_from_db()
        self.assertEqual(
            target.is_calculated, CalculationModel.NOT_CALCULATED,
            "Target must not be transitioned by a no-op rerun",
        )

    # ------------------------------------------------------------------
    # 7.192 — target is not a CalculationModel → MISSED defensively
    # ------------------------------------------------------------------
    def test_07_192_run_marks_missed_when_target_is_not_calculation_model(self):
        """
        Scenario 7.192: defensive — target points at a non-CalculationModel.
        Given: A schedule whose stored content_type now resolves to a
               model that is *not* a CalculationModel (older row written
               before the type guard, or someone mutated the target_ct
               directly).
        When:  _unwrap_task(run_scheduled_calculation(schedule.pk)) runs.
        Then:  Returns 'missed_not_calc_model'; schedule.status = MISSED
               with error_message='target_is_not_a_calculation_model'.
               beat does not crash.
        """
        from django.contrib.auth.models import User
        from django.contrib.contenttypes.models import ContentType
        from lex.lex_app.celery_tasks import run_scheduled_calculation

        # Build a schedule pointing at a real saved User (not a CalculationModel).
        # We do this by creating a schedule normally, then rewriting its
        # target_content_type to User — bypassing ensure()'s validation
        # which is intentional: the worker must defend itself even if the
        # row was written by an older framework version.
        calc_target = AtomicCalc.objects.create(name="placeholder")
        schedule = ScheduledCalculation.ensure(
            target=calc_target,
            run_at=_aware_now() + timedelta(hours=1),
            dedupe_tag="rpt",
        )
        user = User.objects.create_user(username="not-a-calc", password="pw")
        schedule.target_content_type = ContentType.objects.get_for_model(User)
        schedule.target_object_id = str(user.pk)
        schedule.save(update_fields=["target_content_type", "target_object_id"])

        result = _unwrap_task(run_scheduled_calculation(schedule.pk))

        self.assertEqual(result, "missed_not_calc_model")
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, ScheduledCalculation.MISSED)
        self.assertEqual(
            schedule.error_message, "target_is_not_a_calculation_model",
        )










