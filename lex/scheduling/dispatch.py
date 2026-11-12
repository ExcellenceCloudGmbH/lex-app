"""The scheduler's tick: find the jobs that are due, claim each one, start its run.

:func:`run_due_jobs` is a plain function of "now". The clock thread
(:mod:`lex.scheduling.loop`) calls it every few seconds; ``lex run_scheduler``
calls it once; tests call it with a chosen instant.

Claiming is what makes firing exactly-once. For each due row, one transaction
locks it with ``SELECT … FOR UPDATE SKIP LOCKED``, decides what this slot does,
and moves ``next_run_at`` to the first slot after *now* — so any number of
clocks can run side by side, and any number of missed slots collapse into one
decision:

* **fire** — the slot is at most ``LEX_SCHEDULER_MISFIRE_GRACE_SECONDS`` late and
  the previous run has finished;
* **skip** — the previous run is still in progress (``status_message`` says so);
* **missed** — the slot is later than the grace window (``status_message`` says so).

Firing starts the job's calculation the way the Calculate button does
(:mod:`lex.core.calculation_start`): a fresh run id, a pending audit entry, the
IN_PROGRESS announcement, and ``calculate_hook`` on the calculation pool —
which runs it in the backend, or on a Celery worker when the instance has them.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from lex.core.models.CalculationModel import CalculationModel
from lex.scheduling.models import ScheduledJob

logger = logging.getLogger(__name__)

__all__ = ["FIRE", "MISSED", "SKIPPED", "TickReport", "fire", "run_due_jobs", "scheduled_run_request"]

FIRE = "fire"
SKIPPED = "skipped"
MISSED = "missed"

#: The audit author of a run whose schedule has no run-as user (code-declared jobs).
SCHEDULER_ACTOR = "Scheduler"

#: How many due rows one tick looks at. The rest wait for the next tick.
_BATCH = 100


@dataclass
class TickReport:
    """What one tick did, per job name."""

    fired: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    missed: List[str] = field(default_factory=list)
    failed_to_start: List[str] = field(default_factory=list)
    futures: List[Future] = field(default_factory=list, repr=False)

    @property
    def is_empty(self) -> bool:
        return not (self.fired or self.skipped or self.missed or self.failed_to_start)


def misfire_grace() -> timedelta:
    """How late a slot may be and still run."""
    return timedelta(seconds=int(getattr(settings, "LEX_SCHEDULER_MISFIRE_GRACE_SECONDS", 3600)))


def run_due_jobs(now: Optional[datetime] = None) -> TickReport:
    """Claim and start every job due at ``now`` (default: the current time)."""
    now = now or timezone.now()
    report = TickReport()
    due_ids = list(
        ScheduledJob.objects.filter(enabled=True, next_run_at__lte=now)
        .order_by("next_run_at", "pk")
        .values_list("pk", flat=True)[:_BATCH]
    )
    for job_id in due_ids:
        try:
            decision = _claim(job_id, now)
        except Exception:  # noqa: BLE001 - one bad row must not stop the others
            logger.exception("Scheduler could not claim scheduled job %s", job_id)
            continue
        if decision is None:
            continue  # another clock took it, or it changed since the query
        action, job = decision
        if action == SKIPPED:
            report.skipped.append(job.name)
        elif action == MISSED:
            report.missed.append(job.name)
        else:
            try:
                report.futures.append(fire(job))
                report.fired.append(job.name)
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler could not start scheduled job %r", job.name)
                report.failed_to_start.append(job.name)
                ScheduledJob.objects.filter(pk=job.pk).update(
                    status_message=f"Could not start at {_local(now, job)}; see the backend log."
                )
    if not report.is_empty:
        logger.info(
            "Scheduler tick: fired=%s skipped=%s missed=%s failed_to_start=%s",
            report.fired,
            report.skipped,
            report.missed,
            report.failed_to_start,
        )
    return report


def _claim(job_id: int, now: datetime):
    """Lock one due row, decide what its slot does, and move it to its next slot."""
    with transaction.atomic():
        job = (
            ScheduledJob.objects.select_for_update(skip_locked=True)
            .filter(pk=job_id, enabled=True, next_run_at__lte=now)
            .first()
        )
        if job is None:
            return None
        slot = job.next_run_at
        try:
            following = job.next_slot_after(now)
        except Exception as exc:  # noqa: BLE001 - e.g. a time zone removed from tzdata
            ScheduledJob.objects.filter(pk=job.pk).update(
                next_run_at=None,
                status_message=f"Stopped: its schedule can no longer be read ({exc}).",
            )
            logger.error("Scheduled job %r has an unreadable schedule: %s", job.name, exc)
            return None

        if now - slot > misfire_grace():
            action = MISSED
            message = (
                f"Missed the run due at {_local(slot, job)}: the scheduler was not running then, "
                f"and the run was more than {int(misfire_grace().total_seconds() // 60)} minutes late."
            )
            updates = {"next_run_at": following, "status_message": message}
        elif job.is_calculated == CalculationModel.IN_PROGRESS:
            action = SKIPPED
            message = (
                f"Skipped the run due at {_local(slot, job)}: the previous run was still in progress."
            )
            updates = {"next_run_at": following, "status_message": message}
        else:
            action = FIRE
            updates = {"next_run_at": following, "last_run_at": now, "status_message": ""}

        ScheduledJob.objects.filter(pk=job.pk).update(**updates)
        for name, value in updates.items():
            setattr(job, name, value)
    if action != FIRE:
        logger.warning("Scheduled job %r: %s", job.name, updates["status_message"])
    return action, job


def _local(moment: datetime, job: ScheduledJob) -> str:
    from lex.scheduling.cron import CronError, resolve_timezone

    try:
        zone = resolve_timezone(job.timezone)
    except CronError:
        return moment.isoformat()
    return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


def scheduled_run_request(job: ScheduledJob) -> HttpRequest:
    """The request a scheduled run acts under: the job's run-as user, or the scheduler.

    A real ``HttpRequest`` so every reader of ``request.user`` / ``request.META``
    along the calculation path works as it does for a click. It carries no
    Keycloak permissions — a scheduled run's rights were checked when the
    schedule was saved.
    """
    from lex.api.utils.api_key_requests import TechnicalAPIKeyUser

    request = HttpRequest()
    request.method = "POST"
    request.user = job.run_as if job.run_as_id else TechnicalAPIKeyUser(SCHEDULER_ACTOR)
    request.user_permissions = ()
    request.lex_scheduled_run = True
    request.lex_scheduled_job = job.name
    return request


def fire(job: ScheduledJob) -> Future:
    """Start ``job``'s run now, the way a Calculate click starts one; return the pool's future."""
    from lex.api.utils.Context import OperationContext
    from lex.audit_logging.utils.ModelContext import model_logging_context
    from lex.core.calculation_start import (
        calculate_in_background,
        calculation_record_id,
        mark_calculation_started,
        new_calculation_id,
    )

    job = ScheduledJob.objects.select_related("run_as").get(pk=job.pk)
    job.calculation_error_message = ""
    calculation_record = calculation_record_id(job)
    calculation_id = new_calculation_id(job)
    request = scheduled_run_request(job)

    with OperationContext(request, calculation_id):
        _open_run_audit(job, calculation_id, request)
        with model_logging_context(job):
            mark_calculation_started(job, calculation_record, calculation_id)
            return calculate_in_background(job, calculation_record)


def _open_run_audit(job: ScheduledJob, calculation_id: str, request) -> None:
    """Open the pending 'update' audit entry the run reports on, as a click's run does.

    ``ensure_terminal_calculation_audit`` finds it by ``calculation_id`` (or via the
    operation context) and finalises it as success or failure.
    """
    from lex.api.utils import operation_context
    from lex.audit_logging.models.AuditLog import AuditLog
    from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
    from lex.audit_logging.utils.content_types import safe_get_content_type

    audit_log = AuditLog.objects.create(
        author=str(request.user),
        resource=job._meta.model_name,
        action="update",
        payload={
            "id": job.pk,
            "name": job.name,
            # Kept through the run's terminal audit (calculation_audit._OPENER_PAYLOAD_KEYS).
            "trigger": {"by": "schedule", "job": job.name, "schedule": job.schedule_description},
        },
        calculation_id=calculation_id,
        content_type=safe_get_content_type(ScheduledJob),
        object_id=job.pk,
    )
    AuditLogStatus.objects.create(audit_log=audit_log, status="pending")
    context = dict(operation_context.get() or {})
    context["audit_log_temp"] = audit_log
    operation_context.set(context)
