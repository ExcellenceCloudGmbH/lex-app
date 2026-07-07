"""Cluster 1v: the USE_TZ ↔ TIME_ZONE coupling that keeps django_celery_beat correct.

Intent
------
The framework schedules two kinds of beat work through ``django_celery_beat``'s
``DatabaseScheduler``:

* the recovery sweep — an ``IntervalSchedule`` (``PeriodicTask`` re-run every N s), and
* future history edits — a one-off ``ClockedSchedule`` fired at ``History.valid_from``
  (see ``lex/core/services/bitemporal_signals.py`` → ``_schedule_future_activation``).

Both go through ``celery.utils.time.maybe_make_aware``, which interprets a *naive*
datetime as **UTC** (``django_celery_beat.schedulers.ModelEntry.is_due`` and
``django_celery_beat.clockedschedule.clocked.__init__``). When ``USE_TZ`` is False the
framework stores naive datetimes (``PeriodicTask.last_run_at``, ``History.valid_from``).
If ``TIME_ZONE`` were a non-UTC zone those naive values would be local wall-clock while
beat reads them as UTC, so ``is_due()`` is wrong by the UTC offset — the interval sweep
never becomes due, and clocked activations fire hours late. ``settings.py`` therefore
pins ``TIME_ZONE = "Europe/Berlin" if USE_TZ else "UTC"``. A regression that decouples
the two silently breaks every beat-driven feature on a ``USE_TZ=False`` deployment
(GCP/default), which is exactly the production class of bug this batch guards.

Cluster 1v — scenarios 1.179–1.183. Type: U.
Covers: lex/lex_app/settings.py (USE_TZ↔TIME_ZONE coupling); the
django_celery_beat is_due path it must keep correct.
Run: python -m lex pytest lex/test_project/tests/init/test_1v_scheduler_tz_invariant.py -v
"""

from __future__ import annotations

import datetime
from datetime import timedelta

from celery import schedules
from celery.utils.time import maybe_make_aware
from django.conf import settings
from django.test import SimpleTestCase
from django.utils import timezone

import pytest

pytestmark = pytest.mark.init


class TestCluster01v_TimezoneInvariant(SimpleTestCase):
    """The settings-level coupling that makes naive storage UTC-correct for beat."""

    def test_1_179_use_tz_false_forces_utc_timezone(self):
        """1.179: ``USE_TZ`` False ⟹ ``TIME_ZONE == "UTC"``.

        Given: the framework stores naive datetimes whenever USE_TZ is False.
        When:  settings are imported.
        Then:  TIME_ZONE must be UTC so django_celery_beat's naive-as-UTC
               assumption is correct. With USE_TZ True the display zone is free
               (Europe/Berlin) because datetimes are tz-aware.
        """
        if settings.USE_TZ:
            self.assertNotEqual(
                settings.TIME_ZONE, "",
                "a display TIME_ZONE must still be configured under USE_TZ=True",
            )
        else:
            self.assertEqual(
                settings.TIME_ZONE, "UTC",
                "USE_TZ=False stores naive datetimes; TIME_ZONE must be UTC or "
                "django_celery_beat reads PeriodicTask.last_run_at / clocked_time "
                "off by the UTC offset (recovery sweep never due; clocked late).",
            )

    def test_1_180_django_now_frame_matches_beat_assumption(self):
        """1.180: the naive frame Django writes is exactly the one beat reads.

        Given: beat reads naive datetimes as UTC.
        When:  Django produces a 'now' via timezone.now() (USE_TZ=False path).
        Then:  that naive value equals real UTC (within a few seconds), so a
               stored timestamp round-trips through maybe_make_aware unchanged.
        """
        now = timezone.now()
        if settings.USE_TZ:
            self.assertIsNotNone(
                now.tzinfo, "USE_TZ=True must yield tz-aware datetimes",
            )
            return
        # USE_TZ=False → naive; the frame must be UTC for beat to read it right.
        self.assertIsNone(now.tzinfo, "USE_TZ=False must yield naive datetimes")
        real_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        skew = abs((now - real_utc).total_seconds())
        self.assertLess(
            skew, 120,
            "timezone.now() under USE_TZ=False must be UTC wall-clock (got a "
            f"{skew:.0f}s skew — TIME_ZONE is not UTC, so beat would misread it)",
        )

    def test_1_181_interval_sweep_becomes_due_in_current_frame(self):
        """1.181: the recovery IntervalSchedule fires in the live timezone frame.

        Given: a PeriodicTask seeded with last_run_at = now (just ran) and a
               10s interval — the recovery-sweep shape.
        When:  is_due() is evaluated exactly as ModelEntry does
               (maybe_make_aware(last_run_at).astimezone(app.timezone)).
        Then:  it reports 'not due yet, ~within the interval' — NOT ~hours away.
               The bug produced next≈6969s; a correct frame yields next≤interval.
        """
        from lex.lex_app.celery import app

        sched = schedules.schedule(run_every=timedelta(seconds=10), app=app)
        tz = app.timezone

        # Just ran → not due, next ≈ interval.
        last_run_at = timezone.now()
        last_in_tz = maybe_make_aware(last_run_at).astimezone(tz)
        state = sched.is_due(last_in_tz)
        self.assertFalse(state.is_due, "a task that just ran must not be due")
        self.assertLessEqual(
            state.next, 60,
            f"next-check must be within the interval, got {state.next:.0f}s — a "
            "large value means the stored frame is misread by the UTC offset",
        )

        # Ran 30s ago with a 10s interval → due now.
        stale = timezone.now() - timedelta(seconds=30)
        stale_in_tz = maybe_make_aware(stale).astimezone(tz)
        self.assertTrue(
            sched.is_due(stale_in_tz).is_due,
            "an interval task overdue by 3× the period must be due — if this is "
            "False the recovery sweep is stuck (the original production symptom)",
        )

    def test_1_182_clocked_future_edit_fires_at_intended_instant(self):
        """1.182: the future-edit ClockedSchedule fires at valid_from, not offset.

        Given: a clocked schedule built from valid_from in the live frame.
        When:  remaining_estimate / is_due are evaluated.
        Then:  a 30s-future target is ~30s away (not hours), and a past target
               is due. This is the exact path future history edits take under
               Celery (bitemporal_signals._schedule_future_activation).
        """
        from django_celery_beat.clockedschedule import clocked
        from lex.lex_app.celery import app

        future_at = timezone.now() + timedelta(seconds=30)
        c = clocked(clocked_time=future_at, app=app)
        remaining = c.remaining_estimate(None).total_seconds()
        self.assertGreater(remaining, 0, "a future activation must not be due yet")
        self.assertLessEqual(
            remaining, 120,
            f"remaining must be ~30s, got {remaining:.0f}s — a large value means "
            "valid_from is misread by the UTC offset and the edit fires hours late",
        )
        self.assertFalse(c.is_due(None).is_due)

        past_at = timezone.now() - timedelta(seconds=30)
        c_past = clocked(clocked_time=past_at, app=app)
        self.assertTrue(
            c_past.is_due(None).is_due,
            "a clocked target in the past must be due immediately",
        )

    def test_1_183_non_utc_naive_storage_is_misread_by_the_offset(self):
        """1.183: documents WHY the coupling is mandatory (regression rationale).

        Given: the SAME real instant stored as naive-UTC vs naive-Berlin.
        When:  both are read by beat (maybe_make_aware).
        Then:  the Berlin-stored value is misread by exactly the UTC offset,
               while the UTC-stored value is exact. This is the failure mode the
               settings coupling prevents — kept as living documentation.
        """
        from zoneinfo import ZoneInfo

        real = datetime.datetime.now(datetime.timezone.utc)
        naive_utc = real.replace(tzinfo=None)
        naive_berlin = real.astimezone(ZoneInfo("Europe/Berlin")).replace(tzinfo=None)

        read_utc = maybe_make_aware(naive_utc)
        read_berlin = maybe_make_aware(naive_berlin)

        self.assertEqual(
            read_utc, real,
            "naive-UTC storage round-trips exactly through beat's reader",
        )
        offset = abs((read_berlin - read_utc).total_seconds())
        self.assertGreaterEqual(
            offset, 3600,
            "naive-Berlin storage is misread by ≥1h (CET) / 2h (CEST) — the exact "
            "skew the TIME_ZONE=UTC coupling eliminates under USE_TZ=False",
        )
