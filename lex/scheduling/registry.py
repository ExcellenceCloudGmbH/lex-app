"""The functions a schedule may run, and the schedules developers ship in code.

A schedule never runs arbitrary code typed into the UI. It runs either a
calculation, or a function a developer registered here::

    from lex.scheduling import lex_job

    @lex_job("send_daily_digest")
    def send_daily_digest(recipients=()):
        ...

A user can then create a schedule for ``send_daily_digest`` in the app. A
developer can also ship the schedule itself with the code::

    @lex_job("purge_old_exports", schedule="0 3 * * *")
    def purge_old_exports(days=30):
        ...

At startup the scheduler creates a ``ScheduledJob`` row for every job declared
with a ``schedule`` and keeps it in step with the code (see :func:`sync_code_jobs`).
Registration happens when the module is imported, which the framework does for
every module of a project at startup, so a decorated function anywhere in the
project is found.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

__all__ = [
    "RegisteredJob",
    "get_job",
    "lex_job",
    "registered_jobs",
    "sync_code_jobs",
]

_registry: Dict[str, "RegisteredJob"] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class RegisteredJob:
    """A function registered with :func:`lex_job`."""

    name: str
    func: Callable[..., Any]
    schedule: Optional[str] = None
    timezone: Optional[str] = None
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{getattr(self.func, '__module__', '?')}.{getattr(self.func, '__qualname__', '?')}"


def _same_function(left: Callable, right: Callable) -> bool:
    """Whether two callables are the same function, also across a module imported twice.

    Discovery may import a project module under a second name; the decorator then
    runs again for what is, in source, the same function.
    """
    if left is right:
        return True
    return getattr(left, "__qualname__", None) == getattr(right, "__qualname__", None) and (
        getattr(getattr(left, "__code__", None), "co_filename", None)
        == getattr(getattr(right, "__code__", None), "co_filename", None)
    )


def lex_job(
    name: Optional[str] = None,
    *,
    schedule: Optional[str] = None,
    timezone: Optional[str] = None,
    kwargs: Optional[Mapping[str, Any]] = None,
    description: str = "",
):
    """Register a function so schedules can run it.

    Args:
        name: The name schedules refer to. Defaults to the function's name.
        schedule: Optional cron expression. When given, the framework creates and
            maintains a schedule for this job; users can still switch it off.
        timezone: Time zone of ``schedule``. Defaults to the instance's zone.
        kwargs: Keyword arguments the code-declared schedule passes to the function.
        description: Shown in logs.

    Raises:
        ImproperlyConfigured: when another function already uses ``name``, or when
            ``schedule`` / ``timezone`` cannot be used.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        job_name = (name or func.__name__).strip()
        if not job_name:
            raise ImproperlyConfigured("@lex_job needs a non-empty name.")
        if schedule is not None or timezone is not None:
            from lex.scheduling.cron import CronError, CronSchedule, resolve_timezone

            try:
                if schedule is not None:
                    CronSchedule.parse(schedule)
                if timezone is not None:
                    resolve_timezone(timezone)
            except CronError as exc:
                raise ImproperlyConfigured(f"@lex_job({job_name!r}): {exc}") from exc
        job = RegisteredJob(
            name=job_name,
            func=func,
            schedule=schedule,
            timezone=timezone,
            kwargs=dict(kwargs or {}),
            description=description,
        )
        with _lock:
            existing = _registry.get(job_name)
            if existing is not None and not _same_function(existing.func, func):
                raise ImproperlyConfigured(
                    f"@lex_job name {job_name!r} is already used by {existing.qualified_name}; "
                    f"{job.qualified_name} needs a different name."
                )
            _registry[job_name] = job
        return func

    return decorator


def get_job(name: str) -> Optional[RegisteredJob]:
    """The job registered under ``name``, or ``None``."""
    return _registry.get(name)


def registered_jobs() -> List[RegisteredJob]:
    """Every registered job, sorted by name."""
    return sorted(_registry.values(), key=lambda job: job.name)


def unregister(name: str) -> None:
    """Remove a job. For tests; registration is otherwise permanent for the process."""
    with _lock:
        _registry.pop(name, None)


@dataclass
class SyncReport:
    """What :func:`sync_code_jobs` changed."""

    created: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    disabled: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)


def sync_code_jobs() -> SyncReport:
    """Make the schedules declared with ``@lex_job(schedule=...)`` exist and match the code.

    * missing → created, enabled, with no run-as user;
    * schedule, time zone or kwargs changed in code → updated; the ``enabled``
      switch a user set is kept;
    * declared before but no longer in code → disabled, with a message saying so;
    * the name is already used by a schedule a user created → left alone, logged.
    """
    from django.conf import settings
    from django.db import transaction

    from lex.scheduling.models import ScheduledJob

    report = SyncReport()
    declared = {job.name: job for job in registered_jobs() if job.schedule}

    for job in declared.values():
        desired = {
            "cron": job.schedule,
            "timezone": job.timezone or settings.TIME_ZONE,
            "kwargs": dict(job.kwargs),
        }
        with transaction.atomic():
            row = ScheduledJob.objects.select_for_update().filter(name=job.name).first()
            if row is None:
                ScheduledJob.objects.create(
                    name=job.name,
                    target_type=ScheduledJob.TARGET_FUNCTION,
                    function_name=job.name,
                    managed_by_code=True,
                    **desired,
                )
                report.created.append(job.name)
                continue
            if not row.managed_by_code:
                logger.warning(
                    "@lex_job(%r) declares a schedule, but a schedule with that name was created "
                    "in the app; the app's schedule is kept and the declared one is not applied.",
                    job.name,
                )
                report.conflicts.append(job.name)
                continue
            changed = {key: value for key, value in desired.items() if getattr(row, key) != value}
            if row.target_type != ScheduledJob.TARGET_FUNCTION or row.function_name != job.name:
                changed.update(target_type=ScheduledJob.TARGET_FUNCTION, function_name=job.name)
            if row.run_at is not None:
                changed["run_at"] = None
            if row.status_message == ScheduledJob.REMOVED_FROM_CODE_MESSAGE:
                # Declared again after it was removed: it is the code's job once more.
                changed.update(enabled=True, status_message="")
            if changed:
                for key, value in changed.items():
                    setattr(row, key, value)
                row.save()
                report.updated.append(job.name)

    orphans = ScheduledJob.objects.filter(managed_by_code=True, enabled=True).exclude(
        name__in=list(declared)
    )
    for row in orphans:
        row.enabled = False
        row.status_message = ScheduledJob.REMOVED_FROM_CODE_MESSAGE
        row.save()
        report.disabled.append(row.name)

    if report.created or report.updated or report.disabled:
        logger.info(
            "Code-declared schedules synced: created=%s updated=%s disabled=%s",
            report.created,
            report.updated,
            report.disabled,
        )
    return report
