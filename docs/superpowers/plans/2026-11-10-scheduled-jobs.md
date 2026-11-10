# Scheduled jobs — implementation plan

**Status:** implemented on `feat/scheduled-jobs` (this document was written first and kept in
step with the code).
**Date:** 2026-11-10
**Scope:** phase 1 of the "cron for lex instances" architecture: lex-app only, no
infrastructure change, no frontend change.

---

## 1. Goal

Let an instance run work on a schedule — a cron expression or a one-off date — without a
new pod, without Celery beat, and with the same behaviour on all 53 instances, whether or not
they run Celery workers.

"Work" is an allow-list, never arbitrary code from the UI:

1. **a calculation** on one record of a `CalculationModel`, or on every record of it;
2. **a function the developer registered** with `@lex_job("name")`, called with JSON kwargs.

Developers can also ship schedules with their code: `@lex_job("name", schedule="0 7 * * 1-5")`
creates and maintains the schedule row at startup.

## 2. Decisions (taken, with the alternative that was rejected)

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| D1 | Schedules are rows of a framework model, `ScheduledJob`, in the instance's own database | One home for the state; ships with the image, so it survives the per-instance version skew; no infra contract | `django_celery_beat.PeriodicTask` — beat is being retired (LTM `feat/recovery-supervisor-on-demand`), runs on 6/53 instances, and the old beat pod would fire the same rows on some instances only |
| D2 | The clock is a daemon thread in the **served backend process**, started from `lex_app/asgi.py` | Every instance has exactly one backend, always on by default (`Recreate`, 1 replica). Only the process that serves the ASGI app imports `asgi.py`, so the uvicorn reloader parent, workers, management commands and tests never start a clock | A scheduler pod (always-on cost, the thing the team is removing); pg_cron (cannot run Python; the app role has no cron access, LEX-136); one Kubernetes CronJob per schedule (needs the instance controller for every user schedule) |
| D3 | `ScheduledJob` **is a `CalculationModel`** | Firing a job = starting its calculation exactly as the ▶ Calculate button does. Free: "Run now" button, status of the last run, live log drawer, audit entry, cancel, the abort-on-restart sweep, Celery dispatch on MQ/Worker instances | A plain `LexModel` with its own status/log plumbing and a new frontend action contract (none exists) |
| D4 | Exactly-once firing by **claiming the row**: `SELECT … FOR UPDATE SKIP LOCKED`, and moving `next_run_at` forward in the same transaction | Safe with any number of clocks (uvicorn `--workers`, a future second replica, the `run_scheduler` command next to a backend) | Trusting "there is only one backend" |
| D5 | The tick is a **pure function** `run_due_jobs(now)`; the thread is a thin loop around it | Testable without threads; the same function backs `lex run_scheduler --once`, and can be hosted elsewhere later without changing the data model | Logic inside the thread |
| D6 | Enabled **by default** (`LEX_SCHEDULER_ENABLED=true`); disabling logs a WARNING, not a DEBUG | The recovery incident (§0 of `2026-08-04-global-scheduler-infra.md`): absence of configuration must not mean absence of function | Opt-in flag |
| D7 | A schedule **runs as the user who last saved its definition**; that save is refused if the user may not calculate the target | Keycloak permissions only exist inside a request, so they cannot be re-checked at fire time; checking at save time keeps "a schedule cannot do what its author couldn't" | Re-checking at fire time (impossible without a token); running as a superuser (privilege escalation) |
| D8 | Own 5-field cron engine with `zoneinfo`, no new dependency | `croniter` is not installed, its maintenance status is uncertain, and the DST rules need to be ours and tested anyway | Adding `croniter` |
| D9 | `ScheduledJob` is excluded from bitemporal history tracking | Every run changes the row (status, `next_run_at`); history would grow by several rows per run forever. Definition changes are still in `AuditLog` (every REST write is audited), runs are in `AuditLog` + `CalculationLog` | History-tracked job rows |
| D10 | Scale-to-zero (`autoscaling=true`) and paused backends fire on wake, within the grace window | Phase 1 needs no infra; a KEDA wake trigger is a phase-2 follow-up | A KEDA trigger now |

## 3. Architecture

```
 UI (generic)                       served backend process (uvicorn)
 ScheduledJob list/edit/▶  ──REST─▶ ScheduledJob rows (instance Postgres)
                                    │
                                    │ lex-scheduler thread, every LEX_SCHEDULER_TICK_SECONDS
                                    ▼
                          run_due_jobs(now)
                            1. pick enabled rows with next_run_at <= now
                            2. per row, in one transaction:
                               SELECT … FOR UPDATE SKIP LOCKED
                               decide FIRE | SKIP (still running) | MISSED (too late)
                               next_run_at ← next slot after now   (coalesces missed slots)
                            3. FIRE → start the job's calculation like a Calculate click
                                    │
                    ┌───────────────┴────────────────┐
          CELERY_ACTIVE=false                CELERY_ACTIVE=true
          calculation thread pool            Redis queue → KEDA workers
          in the backend                     (the backend thread waits, as for a click)
                                    │
                         ScheduledJob.calculate()
                           calculation target → target.is_calculated = IN_PROGRESS; target.save()
                                                (the documented parent → child pattern)
                           function target    → registry[name](**kwargs)
```

## 4. Components

| File | What it holds |
|---|---|
| `lex/scheduling/__init__.py` | Public import surface: `lex_job` |
| `lex/scheduling/apps.py` | `SchedulingConfig(LexAppConfig)` — label `scheduling`; model discovery/registration comes from the base class |
| `lex/scheduling/cron.py` | `CronSchedule.parse(expr)`, `.next_after(instant, tz)`, `describe(expr)`; `CronError` |
| `lex/scheduling/registry.py` | `lex_job` decorator, `RegisteredJob`, `get_job`, `registered_jobs`, `sync_code_jobs()` |
| `lex/scheduling/models.py` | `ScheduledJob(CalculationModel)` — definition, validation, `next_run_at` maintenance, `calculate()` |
| `lex/scheduling/dispatch.py` | `run_due_jobs(now)`, `fire(job)`, `TickReport`, `ScheduledRunRequest` |
| `lex/scheduling/loop.py` | `SchedulerLoop` thread, `start_scheduler_for_served_backend()` |
| `lex/scheduling/management/commands/run_scheduler.py` | `lex run_scheduler [--once] [--interval N]` |
| `lex/scheduling/migrations/0001_initial.py` | The `ScheduledJob` table |
| `lex/core/calculation_start.py` | **Moved, not new:** the two halves of "start a calculation like a click" (`mark_calculation_started`, `calculate_in_background`), previously private methods of `OneModelEntry`. The view now delegates to them, so the button and the scheduler share one start path |
| `lex/core/models/CalculationModel.py` | New class switch `reset_calculation_on_edit` (default `True`): `False` keeps the status through an edit in the app. `ScheduledJob` sets it, because its status is the last run's outcome |
| `lex/audit_logging/utils/calculation_audit.py` | The terminal audit keeps the `trigger` key the run's opener recorded (it used to replace the whole payload with the record's snapshot) |
| `lex/lex_app/settings.py` | `INSTALLED_APPS += lex.scheduling`; `LEX_SCHEDULER_ENABLED`, `LEX_SCHEDULER_TICK_SECONDS`, `LEX_SCHEDULER_MISFIRE_GRACE_SECONDS` |
| `lex/lex_app/asgi.py` | Starts the clock in the serving process |
| `lex/utilities/config/generic_app_config.py` | `scheduledjob` in the untracked-history list; `ScheduledJob` in the predefined sidebar structure (next to AuditLog / CalculationLog) |

## 5. The `ScheduledJob` model

| Field | Type | Notes |
|---|---|---|
| `name` | `CharField(200, unique)` | Code-declared jobs use the `@lex_job` name |
| `enabled` | `BooleanField(True)` | Disabling clears `next_run_at`; enabling recomputes it from now (missed slots are not replayed) |
| `cron` | `CharField(100, blank)` | 5-field cron in `timezone`; blank = one-off |
| `run_at` | `DateTimeField(null)` | One-off instant; exactly one of `cron` / `run_at` |
| `timezone` | `CharField(64)` | Default `settings.TIME_ZONE` (`LEX_TIME_ZONE`, Europe/Berlin) |
| `target_type` | `CharField` choices `calculation` / `function` | |
| `target_model` | `CharField(200, blank)` | `app_label.ModelName`; a bare `ModelName` is accepted when unique and normalised to the label |
| `target_record_id` | `CharField(64, blank)` | Blank = every record of the model |
| `function_name` | `CharField(200, blank)` | A `@lex_job` name |
| `kwargs` | `JSONField(dict)` | Passed to the function |
| `managed_by_code` | `BooleanField(False, editable=False)` | Rows created by `@lex_job(schedule=…)` |
| `run_as` | `FK(User, null, SET_NULL, editable=False)` | Set from the request user on every definition save |
| `next_run_at` | `DateTimeField(null, editable=False, db_index)` | The clock's work list |
| `last_run_at` | `DateTimeField(null, editable=False)` | When the last run was started |
| `status_message` | `TextField(blank, editable=False)` | Why the last slot did not run (skipped / missed), cleared when a run starts |
| `calculation_error_message` | `TextField(blank, editable=False)` | The last run's error (the framework fills it); cleared when a run starts |
| `is_calculated` | inherited | Status of the last run (the ▶ column) |

`is_atomic = False`: the job's run must not wrap its targets in one transaction — with
"every record", one failing record must not roll back the others' results.

**Who writes which field.** Three writers touch a row, and `save()` keeps them apart so a stale
copy never undoes another's write:
- a **definition save** (a person or the code sync changing a definition field) writes the
  definition, and `next_run_at` / `run_as` / `status_message` only when it derived them itself (an
  old copy must not write back a slot the clock already ran) — never the run's status: it never
  starts a run (a copy loaded mid-run reads IN_PROGRESS, which `CalculationModel.save` would take
  as "run again") and never resets the status (`reset_calculation_on_edit = False`), so a run in
  progress stays visible to the overlap check;
- a **status save** (the calculation machinery, including the copy a run saves when it ends)
  writes everything except the definition, `next_run_at`, `run_as` and the clock's fields;
- the **clock** writes `next_run_at`, `last_run_at` and `status_message` with `QuerySet.update`.

**Validation** runs in `save()` whenever the row is new or a definition field changed (never on
the status-only saves of the calculation machinery). It raises DRF's `ValidationError` with
per-field messages, which the REST layer already answers as HTTP 400. A switched-off schedule is
not validated — it never runs, and switching off a job whose function or model has gone must
work; everything below is checked, with the permissions of whoever does it, when it is switched
on again:

- cron parses and fires at least once in the next 10 years; 6-field (seconds) cron is refused
  with a clear message;
- exactly one of `cron` / `run_at`; a changed `run_at` must lie in the future;
- `timezone` is an IANA zone;
- calculation target: the model resolves, is a concrete `CalculationModel`, is not
  `ScheduledJob`; a given record exists;
- function target: the name is registered;
- inside a request: the caller may calculate the target (`write_denial` / `edit_denial`, the
  same checks the Calculate button passes), and a `managed_by_code` row's schedule/target cannot
  be changed through the API (change the decorator).

## 6. Semantics

**Cron.** Minute, hour, day-of-month, month, day-of-week; `*`, lists, ranges, steps, month and
weekday names, `7` = Sunday, macros `@yearly @annually @monthly @weekly @daily @midnight
@hourly`. Day-of-month and day-of-week combine with OR when both are restricted, AND otherwise
(Vixie cron), a field counting as unrestricted when it starts with `*`.

**Time zones and DST — "at most once per wall-clock time".** The expression is read in the
job's zone; instants are stored in UTC.
- A wall time skipped by spring-forward fires once, at the instant the clocks jump (02:30 in
  Berlin on the last Sunday of March fires at 03:00 CEST).
- A wall time repeated by fall-back fires only on its first occurrence.

**Missed slots.** A row is due when `next_run_at <= now`. On claim, its next slot is computed
from *now*, so any number of missed slots collapses into one decision:
- late by at most `LEX_SCHEDULER_MISFIRE_GRACE_SECONDS` (default 3600) → it runs once;
- later than that → it does not run; `status_message` says which slot was missed and why.

**Overlap.** A job whose previous run is still `IN_PROGRESS` skips the slot
(`status_message` says so) and moves to its next slot — Kubernetes' `concurrencyPolicy: Forbid`.

**One-off.** Fires once at `run_at` (subject to the same grace rule); `next_run_at` then stays
empty until `run_at` is changed.

**Running a job.** The tick starts the job's calculation through the shared start path:
a fresh `calculation_id` of the click's shape, an `OperationContext` whose request carries the
`run_as` user, a pending `AuditLog` entry (`author` = that user, or `Scheduler` when there is
none; payload `trigger: {by: "schedule", job, schedule}`, which the terminal audit keeps),
IN_PROGRESS + broadcast, and
`calculate_hook` on the calculation thread pool. From there it is an ordinary calculation:
Celery when it is on, the backend otherwise; concurrency is bounded by the existing
`LEX_CALCULATION_THREADS` pool.

**Inside the run.**
- calculation target, one record: `IN_PROGRESS` + `save()` — the documented parent → child
  pattern; a failure propagates, so the job ends in ERROR with the target's error;
- calculation target, every record: each record in turn; failures are collected, the rest still
  run, and the job ends in ERROR listing the failed records; closed records are skipped by the
  framework and say so in the log;
- function target: `func(**kwargs)`; an exception ends the job in ERROR.

**Code-declared jobs** (`@lex_job(..., schedule=...)`) are synced by the clock thread on start and
by `run_scheduler`: created if missing (`managed_by_code=True`, no `run_as`), schedule/kwargs
updated when the code changed (the user's `enabled` choice is kept), disabled with a message when
the decorator was removed. A user-created row that already uses the name is left alone, with a
warning in the log.

**Visibility.** Every row shows `next_run_at`, `last_run_at`, the last run's status and the reason
a slot did not run. A `next_run_at` in the past on an enabled row means no clock is running.

## 7. Implementation order

1. `cron.py` (pure), `registry.py`.
2. `models.py`, `apps.py`, `INSTALLED_APPS`, `makemigrations scheduling`.
3. `lex/core/calculation_start.py`; `One.py` delegates (behaviour unchanged).
4. `dispatch.py` (claim / decide / fire), `loop.py`, `run_scheduler` command, settings, `asgi.py`.
5. `generic_app_config.py`: untracked list + sidebar structure.
6. Cluster tests (§8), and the whole cluster suite against an untouched `lex-app-v2` baseline:
   the moved start path, the audit change and the new app touch every cluster.
7. Test-plan sync: allocation/batches/cluster shards, session fragment, dashboard.

## 8. Test plan — surfaces → clusters

| Batch | Cluster | Type | Surfaces |
|---|---|---|---|
| **8f** (8.167–8.182) | 08 celery_async | U + E | cron parsing/validation; next slot incl. DST gap and fold; tick fires due jobs only; disabled rows ignored; exactly-once claim across two ticks; overlap skip; missed slots coalesce into one run within grace; beyond grace → missed; one-off fires once; with Celery on, the job's run is dispatched to a worker instead of running in the backend |
| **7x** (7.269–7.278) | 07 calculations | E | a fired job and its target settle in SUCCESS; a failing target leaves the job in ERROR; every-record mode runs all and reports failures without rolling back the successes; function target runs with kwargs; an unregistered function / deleted record ends in ERROR with the reason |
| **10ac** (10.175–10.183) | 10 api_layer | E | create through REST computes `next_run_at` and sets `run_as`; field-level 400s (cron, timezone, target, past `run_at`, both/neither schedule); editing the schedule recomputes `next_run_at`; disabling clears it; ▶ (PATCH `calculate=true`) runs the job now; a code-managed row refuses schedule edits; the model is in the model structure |
| **6v** (6.173–6.175) | 06 audit_logging | E | a scheduled run opens and finalises an `AuditLog` entry authored by the run-as user, payload marks the schedule; a failed run finalises as failure |
| **4z** (4.159–4.162) | 04 permissions | E | saving a schedule for a target the caller may not calculate answers 400 and creates nothing; code without a request may |
| **1bk** (1.619–1.629) | 01 init | U + E | the clock starts only in the served backend and only when enabled; disabled logs a WARNING; `run_scheduler --once` syncs code jobs and fires due ones; code-job sync creates/updates/keeps `enabled`/disables removed; a duplicate `@lex_job` name is refused |

## 9. Rollout

No infrastructure change. The migration runs with `lex init` on the next instance start. On an
instance nobody has created a schedule on, the clock runs one indexed query per tick
(`enabled AND next_run_at <= now`, on an indexed column) and does nothing else.

## 10. Out of scope (follow-ups)

- **Wake a scaled-to-zero backend** when a job is due: a KEDA PostgreSQL trigger on the backend
  `ScaledObject` calling a `lex_due_schedule_count()` SQL function (LTM `scaled_objects.yaml`).
  Only needed once an `autoscaling=true` instance needs on-time runs.
- **Programmatic one-off API** `ScheduledJob.ensure(target, run_at, dedupe_tag, on_conflict)` from
  the withdrawn draft `docs/_drafts/withdrawn/scheduled-calculations-NOT-IMPLEMENTED.md`.
- Retries after a failed run, failure notifications (mail/webhook).
- The published page in `lex-app-docs` (the docs here are a read-only mirror); §5–§6 are its
  content.
