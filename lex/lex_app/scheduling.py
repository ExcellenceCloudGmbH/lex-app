"""Scheduled calculations — fire a CalculationModel calculation later, with dedupe.

See :doc:`docs/features/processing/scheduled-calculations.md` for the
intent and worked examples. The short version:

* :meth:`ScheduledCalculation.ensure` is the one public entry point a
  customer reaches. It records a row in this table and registers a
  one-shot ``django-celery-beat`` ``PeriodicTask`` (via a
  ``ClockedSchedule``) that fires at ``run_at`` and dispatches the
  framework Celery task :func:`run_scheduled_calculation`.
* When the worker picks the task up it loads the target
  ``CalculationModel`` row and saves it with
  ``is_calculated=IN_PROGRESS`` — at which point the **normal**
  calculation pipeline (audit log, history, websocket, cancel button)
  takes over. Scheduled calculations deliberately do not introduce a
  parallel calculation pipeline; they only postpone the trigger.
* Dedupe is keyed on ``(target_content_type, target_object_id,
  dedupe_tag, status="PENDING")``. The ``on_conflict`` argument controls
  what happens when a duplicate is found.

The mechanism for storing one-shot future tasks (``ClockedSchedule`` +
``PeriodicTask(one_off=True)``) is the same one
``lex/core/services/bitemporal_signals.py::_schedule_future_activation``
already uses for bitemporal future-dated activations. We reuse it here
rather than inventing a parallel scheduler so the broker-restart-safety
behaviour (beat re-enqueues from the DB) is identical for both.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone as _stdlib_timezone
from typing import Optional

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.utils import timezone

from lex.core.models.LexModel import LexModel

logger = logging.getLogger(__name__)

# Mirror the lex.lex_app.celery_tasks dual-name alias so the model is
# only ever registered once with Django's app registry, regardless of
# which path the framework was imported from (``lex_app`` vs
# ``lex.lex_app``). Without this a second import path triggers
# ``Conflicting 'scheduledcalculation' models in application 'lex_app'``.
_current_module = sys.modules[__name__]
if __name__ == "lex.lex_app.scheduling":
    sys.modules.setdefault("lex_app.scheduling", _current_module)
elif __name__ == "lex_app.scheduling":
    sys.modules.setdefault("lex.lex_app.scheduling", _current_module)


class ScheduledCalculationUnavailable(RuntimeError):
    """Raised when the scheduling subsystem cannot accept a new schedule.

    Today this means ``CELERY_ACTIVE`` is not set: a one-shot future
    calculation needs a broker + worker + beat. Failing fast with this
    exception is deliberate — silently dropping the schedule would
    leave the customer believing a calculation is queued for tonight
    when it is not.
    """


class _OnConflict:
    DEDUPE = "dedupe"
    REPLACE = "replace"
    DEBOUNCE = "debounce"

    ALL = frozenset({DEDUPE, REPLACE, DEBOUNCE})


class ScheduledCalculation(LexModel):
    """One-shot future trigger for a :class:`CalculationModel` calculation.

    See the module docstring and ``docs/features/processing/scheduled-calculations.md``
    for behaviour. The fields are intentionally short — most of the
    public contract lives on :meth:`ensure` and :meth:`cancel`.
    """

    PENDING = "PENDING"
    FIRED = "FIRED"
    CANCELLED = "CANCELLED"
    MISSED = "MISSED"
    STATUSES = [
        (PENDING, "PENDING"),
        (FIRED, "FIRED"),
        (CANCELLED, "CANCELLED"),
        (MISSED, "MISSED"),
    ]

    # GenericFK target. ``object_id`` is a CharField so we can address
    # any pk shape (UUID, str, int) without a per-model column.
    target_content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="+",
    )
    target_object_id = models.CharField(max_length=255)
    target = GenericForeignKey("target_content_type", "target_object_id")

    dedupe_tag = models.CharField(max_length=255)
    run_at = models.DateTimeField()
    status = models.CharField(
        max_length=20, choices=STATUSES, default=PENDING, db_index=True,
    )

    # Bookkeeping for revoke / lifecycle visibility.
    periodic_task_name = models.CharField(max_length=255, blank=True, default="")
    fired_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        # The lex_app app already exists; this model lives there so it
        # is discovered alongside the rest of the framework's concrete
        # models (AuditLog, etc.).
        app_label = "lex_app"
        indexes = [
            models.Index(
                fields=[
                    "target_content_type",
                    "target_object_id",
                    "dedupe_tag",
                    "status",
                ],
                name="lex_sched_dedupe_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"ScheduledCalculation({self.target_content_type_id}/"
            f"{self.target_object_id}, tag={self.dedupe_tag!r}, "
            f"status={self.status}, run_at={self.run_at.isoformat()})"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def ensure(
        cls,
        *,
        target,
        run_at,
        dedupe_tag: str,
        on_conflict: str = _OnConflict.DEDUPE,
    ) -> "ScheduledCalculation":
        """Schedule ``target.calculate()`` to fire at ``run_at`` once.

        See ``docs/features/processing/scheduled-calculations.md`` for
        the conflict-resolution semantics (dedupe / replace / debounce).
        """
        if target is None or getattr(target, "pk", None) is None:
            raise ValueError(
                "ScheduledCalculation.ensure requires a saved target instance "
                "(target.pk must not be None).",
            )
        if not dedupe_tag:
            raise ValueError(
                "ScheduledCalculation.ensure requires a non-empty dedupe_tag — "
                "the dedupe contract is explicit by design.",
            )
        if on_conflict not in _OnConflict.ALL:
            raise ValueError(
                f"on_conflict must be one of {sorted(_OnConflict.ALL)}, "
                f"got {on_conflict!r}",
            )

        run_at_dt = cls._coerce_run_at(run_at)
        if not _celery_active():
            raise ScheduledCalculationUnavailable(
                "Scheduled calculations require CELERY_ACTIVE=true. "
                "Without a Celery worker + beat, a one-shot future task "
                "cannot be dispatched.",
            )

        ct = ContentType.objects.get_for_model(target.__class__)

        with transaction.atomic():
            existing = (
                cls.objects.select_for_update()
                .filter(
                    target_content_type=ct,
                    target_object_id=str(target.pk),
                    dedupe_tag=dedupe_tag,
                    status=cls.PENDING,
                )
                .first()
            )

            if existing is not None:
                if on_conflict == _OnConflict.DEDUPE:
                    return existing
                if on_conflict == _OnConflict.DEBOUNCE and run_at_dt <= existing.run_at:
                    return existing
                # REPLACE, or DEBOUNCE with a strictly later run_at:
                # revoke the old beat task and re-register at the new
                # time. The row itself is updated in place so a single
                # PENDING row per (target, tag) is the invariant.
                _delete_periodic_task(existing.periodic_task_name)
                existing.run_at = run_at_dt
                existing.periodic_task_name = _register_periodic_task(
                    target=target,
                    run_at=run_at_dt,
                    schedule_pk=existing.pk,
                )
                existing.save(update_fields=["run_at", "periodic_task_name"])
                return existing

            schedule = cls.objects.create(
                target_content_type=ct,
                target_object_id=str(target.pk),
                dedupe_tag=dedupe_tag,
                run_at=run_at_dt,
                status=cls.PENDING,
            )
            schedule.periodic_task_name = _register_periodic_task(
                target=target,
                run_at=run_at_dt,
                schedule_pk=schedule.pk,
            )
            schedule.save(update_fields=["periodic_task_name"])
            return schedule

    @classmethod
    def cancel(cls, schedule: "ScheduledCalculation") -> dict:
        """Cancel a ``PENDING`` schedule before it fires.

        Returns ``{"cancelled": bool, "status": <new status>, "reason": str | None}``.
        Idempotent on terminal states (already-fired schedules are not
        re-cancelled — the in-flight calculation has its own
        :meth:`CalculationModel.cancel` path).
        """
        if schedule is None or schedule.pk is None:
            return {"cancelled": False, "status": None, "reason": "missing_schedule"}

        if schedule.status != cls.PENDING:
            return {
                "cancelled": False,
                "status": schedule.status,
                "reason": "not_pending",
            }

        _delete_periodic_task(schedule.periodic_task_name)
        schedule.status = cls.CANCELLED
        schedule.save(update_fields=["status"])
        return {"cancelled": True, "status": cls.CANCELLED, "reason": None}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_run_at(run_at) -> datetime:
        """Accept a ``timedelta`` (relative to now) or aware ``datetime``.

        Naive ``datetime`` *inputs* are rejected — silently picking a
        timezone is a recipe for "the report ran 2 hours late" bug
        reports. The customer-facing contract is "aware in, exact moment
        out".

        After validation we normalise the result to match the host
        project's ``USE_TZ`` setting:

        * ``USE_TZ=True``  → keep the aware datetime as supplied;
        * ``USE_TZ=False`` → convert to naive in the project's
          ``TIME_ZONE``. This mirrors what Django itself does when an
          aware datetime is saved to a ``DateTimeField`` in a
          ``USE_TZ=False`` project (Django warns and converts), and —
          crucially — it keeps the comparison ``run_at_dt <=
          existing.run_at`` below working when ``existing.run_at`` came
          back from the DB as naive. Without this normalisation,
          on_conflict="debounce" raises ``TypeError: can't compare
          offset-naive and offset-aware datetimes`` for every project
          running ``USE_TZ=False``.
        """
        if isinstance(run_at, timedelta):
            result = timezone.now() + run_at
        elif isinstance(run_at, datetime):
            if run_at.tzinfo is None or run_at.utcoffset() is None:
                raise ValueError(
                    "run_at must be a timezone-aware datetime or a "
                    "timedelta. Naive datetimes are rejected on purpose."
                )
            result = run_at
        else:
            raise TypeError(
                f"run_at must be a datetime or timedelta, got {type(run_at).__name__}",
            )

        from django.conf import settings as _settings

        if not getattr(_settings, "USE_TZ", True) and result.tzinfo is not None:
            # Convert aware → naive in the project's TIME_ZONE. The
            # absolute moment in time is preserved; only the wall-clock
            # representation changes — same behaviour Django applies
            # when persisting an aware datetime to a TIMESTAMP WITHOUT
            # TIME ZONE column.
            result = timezone.make_naive(
                result, timezone.get_current_timezone(),
            )
        return result


# ----------------------------------------------------------------------
# Helpers — kept module-private; not part of the public surface.
# ----------------------------------------------------------------------


def _celery_active() -> bool:
    return os.getenv("CELERY_ACTIVE", "false").lower() == "true"


def _register_periodic_task(*, target, run_at: datetime, schedule_pk) -> str:
    """Create the one-shot ``django_celery_beat.PeriodicTask`` row.

    Returns the unique task name we stored on the ``ScheduledCalculation``
    row so a later cancel can find it.
    """
    from django_celery_beat.models import ClockedSchedule, PeriodicTask

    clocked, _ = ClockedSchedule.objects.get_or_create(clocked_time=run_at)
    task_name = (
        f"lex_scheduled_calc_{target._meta.app_label}_"
        f"{target._meta.model_name}_{target.pk}_"
        f"{int(run_at.timestamp())}_{uuid.uuid4()}"
    )
    PeriodicTask.objects.create(
        clocked=clocked,
        name=task_name,
        task="lex_scheduled_calculation",
        args=json.dumps([schedule_pk]),
        one_off=True,
    )
    logger.info(
        "Registered scheduled calculation task %s for %s @ %s",
        task_name,
        target,
        run_at.isoformat(),
    )
    return task_name


def _delete_periodic_task(task_name: str) -> None:
    if not task_name:
        return
    try:
        from django_celery_beat.models import PeriodicTask

        PeriodicTask.objects.filter(name=task_name).delete()
    except Exception:  # pragma: no cover — defensive
        logger.warning(
            "Failed to delete PeriodicTask %s during scheduled-calc cancel/replace",
            task_name,
            exc_info=True,
        )


