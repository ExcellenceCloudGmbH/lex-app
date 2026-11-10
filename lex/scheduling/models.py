"""``ScheduledJob`` — a schedule, stored in the instance's own database.

A scheduled job is a calculation. Firing it starts its calculation exactly as the
Calculate button does (see :mod:`lex.scheduling.dispatch`), so a job's last run
shows in the ``is_calculated`` column, its ▶ button is "run now", its run streams
into the log drawer and lands in the audit log, and it runs on a Celery worker
when the instance has them. What the run *does* is :meth:`ScheduledJob.calculate`:
calculate a target record (or every record of a model), or call a function
registered with :func:`lex.scheduling.lex_job`.

Design: docs/superpowers/plans/2026-11-10-scheduled-jobs.md
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Optional

from django.apps import apps
from django.conf import settings
from django.db import models
from django.utils import timezone as django_timezone
from rest_framework.exceptions import ValidationError

from lex.core.models.CalculationModel import CalculationModel
from lex.scheduling.cron import CronError, CronSchedule, describe, resolve_timezone

logger = logging.getLogger(__name__)


class ScheduledJobError(Exception):
    """A scheduled run could not do its work; the message says why, for the person reading it."""


def _default_timezone() -> str:
    return settings.TIME_ZONE


class ScheduledJob(CalculationModel):
    """Run a calculation or a registered function on a cron schedule, or once at a set time."""

    TARGET_CALCULATION = "calculation"
    TARGET_FUNCTION = "function"
    TARGET_TYPES = [
        (TARGET_CALCULATION, "Calculation"),
        (TARGET_FUNCTION, "Registered function"),
    ]

    REMOVED_FROM_CODE_MESSAGE = (
        "Switched off: its @lex_job declaration was removed from the code."
    )

    #: What a person defines. A save that changes none of these never writes them
    #: (or ``next_run_at`` / ``run_as``), so a stale copy of the row — the one a
    #: calculation saves when its run ends — cannot undo an edit made meanwhile.
    DEFINITION_FIELDS = (
        "name",
        "enabled",
        "cron",
        "run_at",
        "timezone",
        "target_type",
        "target_model",
        "target_record_id",
        "function_name",
        "kwargs",
    )
    #: Fields whose change moves the next run.
    SCHEDULE_FIELDS = ("enabled", "cron", "run_at", "timezone")
    #: Fields only the clock writes, with ``QuerySet.update``.
    CLOCK_FIELDS = ("last_run_at", "status_message")
    #: Fields only a run writes: an edit of the schedule never does.
    RUN_FIELDS = ("is_calculated", "calculation_error_message")
    #: Derived by a definition save, written only by the save that derived them.
    SCHEDULE_OWNED_FIELDS = ("next_run_at", "run_as")

    # A run must not wrap its targets in one transaction: with "every record",
    # one failing record must not roll back the records that succeeded.
    is_atomic = False

    # The status is the last run's outcome, not a result of the fields: editing
    # the schedule keeps it — and keeps a run in progress visible to the clock.
    reset_calculation_on_edit = False

    lex_initial_state_extra_fields = DEFINITION_FIELDS + ("status_message",)

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="A name for this schedule. Schedules declared in code use their @lex_job name.",
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Switched off, the schedule keeps its settings but never runs.",
    )
    cron = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Cron expression",
        help_text=(
            "When to run, as minute hour day-of-month month day-of-week in the schedule's "
            "time zone, e.g. '0 6 * * 1-5' for 06:00 on weekdays. Leave empty to run once at 'Run at'."
        ),
    )
    run_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Run once at this time instead of on a cron schedule.",
    )
    timezone = models.CharField(
        max_length=64,
        default=_default_timezone,
        verbose_name="Time zone",
        help_text="The time zone the cron expression is read in, e.g. 'Europe/Berlin'.",
    )
    target_type = models.CharField(
        max_length=20,
        choices=TARGET_TYPES,
        default=TARGET_CALCULATION,
        verbose_name="What to run",
    )
    target_model = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name="Calculation model",
        help_text="The model to calculate, as 'app_label.ModelName' (or just 'ModelName').",
    )
    target_record_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="Record id",
        help_text="The record to calculate. Leave empty to calculate every record of the model.",
    )
    function_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name="Function",
        help_text="The name of a function registered with @lex_job.",
    )
    kwargs = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Arguments",
        help_text="Keyword arguments passed to the function, as a JSON object.",
    )
    managed_by_code = models.BooleanField(
        default=False,
        editable=False,
        help_text="Declared in code with @lex_job(schedule=...); change it there.",
    )
    run_as = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Runs act as the user who last saved the schedule.",
    )
    next_run_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True, editable=False)
    status_message = models.TextField(
        blank=True,
        default="",
        editable=False,
        help_text="Why the last slot did not run, when it did not.",
    )
    calculation_error_message = models.TextField(blank=True, default="", editable=False)

    class Meta:
        app_label = "scheduling"
        ordering = ["name"]
        verbose_name = "Scheduled job"
        verbose_name_plural = "Scheduled jobs"

    def __str__(self) -> str:
        return self.name

    # ── schedule ──────────────────────────────────────────────────────

    @property
    def schedule_description(self) -> str:
        """The schedule in words, for logs and messages."""
        if self.cron:
            return f"{describe(self.cron)} ({self.timezone})"
        if self.run_at:
            return f"once at {self.run_at.isoformat()}"
        return "not scheduled"

    def next_slot_after(self, after: datetime) -> Optional[datetime]:
        """The first slot strictly after ``after``, or ``None`` when there is none (or it is off)."""
        if not self.enabled:
            return None
        if self.cron:
            return CronSchedule.parse(self.cron).next_after(after, resolve_timezone(self.timezone))
        if self.run_at and self.run_at > after:
            return self.run_at
        return None

    # ── target ────────────────────────────────────────────────────────

    def resolve_target_model(self):
        """The calculation model this job targets, or :class:`ScheduledJobError` saying why not."""
        return _resolve_calculation_model(self.target_model)

    # ── save ──────────────────────────────────────────────────────────

    def _definition_changed(self) -> bool:
        return self._state.adding or any(self.has_changed(name) for name in self.DEFINITION_FIELDS)

    def save(self, *args, **kwargs):
        if self._state.adding:
            self._prepare_definition_save(adding=True)
            return super().save(*args, **kwargs)

        update_fields = kwargs.get("update_fields")
        if kwargs.get("skip_hooks"):
            defines = False
        elif update_fields is not None:
            defines = bool(set(update_fields) & set(self.DEFINITION_FIELDS))
        else:
            defines = self._definition_changed()

        if defines:
            # Only what this save decided: a copy loaded before the clock moved the
            # slot must not write the old slot back (it would run a second time).
            decided = self._prepare_definition_save(adding=False)
            if update_fields is None:
                base = set(
                    self._concrete_field_names(
                        exclude=self.SCHEDULE_OWNED_FIELDS + self.CLOCK_FIELDS + self.RUN_FIELDS
                    )
                )
            else:
                base = set(update_fields)
            kwargs["update_fields"] = sorted((base | decided) - set(self.RUN_FIELDS))
            return self._save_definition(*args, **kwargs)
        elif update_fields is None:
            # A save that defines nothing — a status write, or the copy a run saves
            # when it ends — must not write the schedule back from a stale copy.
            kwargs["update_fields"] = self._concrete_field_names(
                exclude=self.DEFINITION_FIELDS
                + self.SCHEDULE_OWNED_FIELDS
                + ("managed_by_code",)
                + self.CLOCK_FIELDS
            )
        return super().save(*args, **kwargs)

    def _save_definition(self, *args, **kwargs):
        """Save an edit of the schedule without touching its run.

        The status is the last run's outcome, and an edit does not change it: the
        generic "an edit resets the status" would hide a run in progress from the
        overlap check, and saving a copy loaded mid-run (status IN_PROGRESS) would
        otherwise count as asking for a new run.
        """
        deferred = getattr(self, "_defer_calculate_hook", False)
        self._defer_calculate_hook = True
        try:
            result = super().save(*args, **kwargs)
        finally:
            self._defer_calculate_hook = deferred
        stored = (
            type(self)._default_manager.filter(pk=self.pk)
            .values_list(*self.RUN_FIELDS)
            .first()
        )
        if stored is not None:
            for name, value in zip(self.RUN_FIELDS, stored):
                setattr(self, name, value)
        return result

    def _concrete_field_names(self, exclude: Iterable[str]) -> list:
        excluded = set(exclude)
        return [
            field.attname
            for field in self._meta.concrete_fields
            if not field.primary_key and field.name not in excluded
        ]

    def _prepare_definition_save(self, *, adding: bool) -> set:
        """Normalise, validate and derive; return the derived fields this save sets."""
        request = _request_from_context()
        if request is not None and not adding and self.managed_by_code:
            self._refuse_code_managed_edit()
        self._normalise()
        self.validate_definition(request=request)
        decided = set()
        user = _real_user(request)
        if user is not None:
            self.run_as = user
            decided.add("run_as")
        if not adding and self.has_changed("status_message"):
            decided.add("status_message")  # this save explains something (the code sync)
        if adding or any(self.has_changed(name) for name in self.SCHEDULE_FIELDS):
            self.next_run_at = self.next_slot_after(django_timezone.now())
            decided.add("next_run_at")
            # A new schedule makes the old "why it did not run" stale — unless this
            # very save is the one explaining something.
            if not adding and "status_message" not in decided:
                self.status_message = ""
                decided.add("status_message")
        return decided

    def _refuse_code_managed_edit(self) -> None:
        changed = [
            name
            for name in self.DEFINITION_FIELDS
            if name != "enabled" and self.has_changed(name)
        ]
        if changed:
            raise ValidationError(
                {
                    name: [
                        f"This schedule is declared in code with @lex_job({self.initial_value('name')!r}); "
                        "change it there. Only 'enabled' can be changed here."
                    ]
                    for name in changed
                }
            )

    def _normalise(self) -> None:
        self.name = (self.name or "").strip()
        self.cron = " ".join((self.cron or "").split())
        self.timezone = (self.timezone or "").strip() or settings.TIME_ZONE
        self.target_model = (self.target_model or "").strip()
        self.target_record_id = str(self.target_record_id or "").strip()
        self.function_name = (self.function_name or "").strip()
        if self.kwargs is None:
            self.kwargs = {}
        if self.target_type == self.TARGET_CALCULATION and self.target_model:
            try:
                self.target_model = self.resolve_target_model()._meta.label
            except ScheduledJobError:
                pass  # reported by validate_definition

    def validate_definition(self, request=None) -> None:
        """Refuse a schedule that could never run correctly, field by field.

        Raises DRF's ``ValidationError``, which the REST API answers as a 400
        naming the fields. ``request`` adds the checks only a person's save
        needs: that this person may calculate the target.

        A switched-off schedule is not checked: it never runs, and it must stay
        possible to switch off a job whose function or model has gone. Everything
        is checked — with the permissions of whoever does it — when it is
        switched on again.
        """
        if not self.enabled:
            return
        errors = {}

        if self.cron and self.run_at:
            errors["cron"] = ["Give either a cron expression or 'Run at', not both."]
        elif not self.cron and not self.run_at:
            errors["cron"] = ["Give a cron expression, or a 'Run at' time for a single run."]
        elif self.cron:
            try:
                CronSchedule.parse(self.cron)
            except CronError as exc:
                errors["cron"] = [str(exc)]
        elif self.run_at is not None:
            if django_timezone.is_naive(self.run_at):
                self.run_at = django_timezone.make_aware(self.run_at)
            run_at_changed = self._state.adding or self.has_changed("run_at")
            if run_at_changed and self.run_at <= django_timezone.now():
                errors["run_at"] = ["'Run at' must lie in the future."]

        try:
            resolve_timezone(self.timezone)
        except CronError as exc:
            errors["timezone"] = [str(exc)]

        if not isinstance(self.kwargs, dict):
            errors["kwargs"] = ["Arguments must be a JSON object, e.g. {\"days\": 30}."]

        if self.target_type == self.TARGET_CALCULATION:
            errors.update(self._calculation_target_errors(request))
        elif self.target_type == self.TARGET_FUNCTION:
            from lex.scheduling.registry import get_job, registered_jobs

            if not self.function_name:
                errors["function_name"] = ["Name the @lex_job function to run."]
            elif get_job(self.function_name) is None:
                known = ", ".join(job.name for job in registered_jobs()) or "none are registered"
                errors["function_name"] = [
                    f"No function is registered as {self.function_name!r} (registered: {known})."
                ]
        else:
            errors["target_type"] = [f"{self.target_type!r} is not something a schedule can run."]

        if errors:
            raise ValidationError(errors)

    def _calculation_target_errors(self, request) -> dict:
        if not self.target_model:
            return {"target_model": ["Name the calculation model to run, as 'app_label.ModelName'."]}
        try:
            model = self.resolve_target_model()
        except ScheduledJobError as exc:
            return {"target_model": [str(exc)]}

        target = None
        if self.target_record_id:
            try:
                target = model._default_manager.filter(pk=self.target_record_id).first()
            except Exception:  # noqa: BLE001 - "abc" for an integer key, a malformed UUID, …
                target = None
            if target is None:
                return {
                    "target_record_id": [
                        f"There is no {model._meta.verbose_name} with id {self.target_record_id!r}."
                    ]
                }

        if request is not None:
            denial = _calculate_denial(request, model, target)
            if denial:
                return {"target_model": [denial]}
        return {}

    # ── run ───────────────────────────────────────────────────────────

    def calculate(self):
        """One run of the schedule: do what it targets."""
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_heading(f"Scheduled job: {self.name}", level=2).add_text(
            f"Schedule: {self.schedule_description}"
        ).log()

        if self.target_type == self.TARGET_FUNCTION:
            self._run_function(LexLogger)
        elif self.target_type == self.TARGET_CALCULATION:
            self._run_calculations(LexLogger)
        else:
            raise ScheduledJobError(f"{self.target_type!r} is not something a schedule can run.")

    def _run_function(self, LexLogger) -> None:
        from lex.scheduling.registry import get_job

        job = get_job(self.function_name)
        if job is None:
            raise ScheduledJobError(
                f"No function is registered as {self.function_name!r} in this process; "
                "it may have been renamed or removed from the code."
            )
        kwargs = dict(self.kwargs or {})
        LexLogger().add_text(f"Calling {job.name}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})").log()
        job.func(**kwargs)

    def _run_calculations(self, LexLogger) -> None:
        model = self.resolve_target_model()
        manager = model._default_manager

        if self.target_record_id:
            record = manager.filter(pk=self.target_record_id).first()
            if record is None:
                raise ScheduledJobError(
                    f"{model._meta.verbose_name.capitalize()} {self.target_record_id} no longer exists."
                )
            LexLogger().add_text(f"Calculating {model.__name__} {record.pk}: {record}").log()
            _calculate_record(record)
            return

        pks = list(manager.order_by("pk").values_list("pk", flat=True))
        LexLogger().add_text(f"Calculating all {len(pks)} records of {model.__name__}.").log()
        failures = []
        for pk in pks:
            record = manager.filter(pk=pk).first()
            if record is None:
                continue  # deleted while the run went through the others
            try:
                _calculate_record(record)
            except Exception as exc:  # noqa: BLE001 - collected and reported below
                failures.append((pk, exc))
        if failures:
            listed = "; ".join(f"{pk}: {exc}" for pk, exc in failures[:10])
            more = f" (and {len(failures) - 10} more)" if len(failures) > 10 else ""
            raise ScheduledJobError(
                f"{len(failures)} of {len(pks)} {model.__name__} records failed — {listed}{more}"
            )


def _calculate_record(record) -> None:
    """Start ``record``'s calculation inside the running job: the parent → child pattern."""
    record.is_calculated = CalculationModel.IN_PROGRESS
    record.save()


def _resolve_calculation_model(label: str):
    text = (label or "").strip()
    if not text:
        raise ScheduledJobError("No calculation model is set.")
    model = None
    if "." in text:
        try:
            model = apps.get_model(text)
        except (LookupError, ValueError):
            model = None
    else:
        matches = [m for m in apps.get_models() if m.__name__.lower() == text.lower()]
        if len(matches) > 1:
            labels = ", ".join(sorted(m._meta.label for m in matches))
            raise ScheduledJobError(f"{text!r} is ambiguous; use one of: {labels}.")
        model = matches[0] if matches else None
    if model is None:
        raise ScheduledJobError(f"There is no model {text!r}.")
    if not issubclass(model, CalculationModel) or model._meta.abstract:
        raise ScheduledJobError(f"{model._meta.label} is not a calculation model.")
    if issubclass(model, ScheduledJob):
        raise ScheduledJobError("A schedule cannot run another schedule.")
    if getattr(model, "instance_type", None) is not None:
        raise ScheduledJobError(f"{model._meta.label} is a history table, not a calculation model.")
    return model


def _request_from_context():
    """The request of the person saving, when a save happens inside one."""
    try:
        from lex.api.utils import operation_context

        request = operation_context.get().get("request_obj")
    except Exception:  # noqa: BLE001
        return None
    if not request or isinstance(request, dict) or not hasattr(request, "user"):
        return None
    if getattr(request, "lex_scheduled_run", False):
        return None
    return request


def _real_user(request):
    """The saving person as a user row, when there is one to point ``run_as`` at."""
    if request is None:
        return None
    user = getattr(request, "user", None)
    user_model = apps.get_model(settings.AUTH_USER_MODEL)
    if isinstance(user, user_model) and getattr(user, "is_authenticated", False) and user.pk:
        return user
    return None


def _calculate_denial(request, model, target) -> Optional[str]:
    """Why the person behind ``request`` may not calculate ``target`` (or ``model``), or ``None``.

    The same checks the Calculate button passes: a write the model allows, on a
    record the person may edit.
    """
    from lex.api.security import edit_denial, write_denial

    instance = target if target is not None else model()
    denial = write_denial(request, model) or edit_denial(request, instance, {})
    if denial is None:
        return None
    detail = getattr(denial, "data", {}) or {}
    reason = detail.get("detail") or detail.get("error") or "not allowed"
    return f"You may not calculate {model._meta.verbose_name}: {reason}"
