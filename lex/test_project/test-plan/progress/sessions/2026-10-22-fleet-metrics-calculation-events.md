---
date: 2026-10-22
clusters: [6, 7]
tests_added: "22 new scenarios (6.168, 7.238-7.258)"
suite_tally: "test_7v_calculation_finished_events.py + test_6u_fleet_metrics_audit_contract.py: 23 pass / 1 expectedFailure (BUG-034), 4 subtests pass — confirmed deterministic across repeated full-file runs and every test run alone"
---

# Batches 6u, 7v — fleet-metrics push build, Task 12 (PR F part 2)

Branch `feat/fleet-metrics-calculation-events`, from Task 11's commit on `feat/fleet-metrics-emitters`
(controller ruling: Task 11's own branch landed over budget at 1,683 changed lines, so Task 12
continues on its own branch/worktree instead of stacking further onto that PR). Scope: lex-app sends
one `calculation_finished` event per root calculation run, through the emitter Task 11 built.
`lex/lex_app/fleet_metrics/emitter.py` and `lex/core/services/usage_config.py` are read-only
dependencies here, not touched.

[7v](../../clusters/07-calculations/batches.md) covers the new
`lex/lex_app/fleet_metrics/calculations.py`: `emit_calculation_finished(audit_log, status_row,
audit_status)` maps the finaliser's `success`/`failure`/`cancelled`/`aborted` to
`SUCCESS`/`ERROR`/`CANCELLED`/`ABORTED`, derives `duration_seconds` from the status row's
`updated_at - created_at` (or `None` when `ensure_terminal_calculation_audit` had to create that row
itself — a transient `_freshly_created` marker it sets on the instance, never persisted — meaning no
earlier 'pending' row existed to time the run from), tags `process` via the same `running_in_uvicorn()`
heartbeat.py already uses, and de-dupes in-process on `(audit_log.pk, outcome)` with a 10,000-entry
LRU (the finaliser can run twice in one process on some paths). The call sits inside
`ensure_terminal_calculation_audit` itself (`lex/audit_logging/utils/calculation_audit.py`), right
after its `transaction.atomic()` block and wrapped in `emitter.guard("calculation_audit.emit")`, so
every terminal path that already converges there — sync success/error/cancel, a Celery worker's
success/failure callback, and the startup-recovery abort sweep — converges on the event too, with no
new root-run detection logic of its own. `celery_tasks.py`'s `_update_model_status` calls
`emitter.flush(5)` at the end so a worker's event leaves before `SIGTERM`.
[6u](../../clusters/06-audit_logging/batches.md) is the scenario-6.168 audit-contract test: the
out-of-repo fleet-metrics backfill reads `audit_logging_auditlog` /
`audit_logging_auditlogstatus` directly by column name and type, so this batch pins that contract at
the `Model._meta` level (no DB access needed) rather than leaving it to be discovered by a silent
backfill failure in a different repo.

**One real framework bug found, recorded, not fixed (BUG-034, open):** the startup-recovery abort
sweep's call to `ensure_terminal_calculation_audit(instance, audit_status="aborted", ...)`
(`model_registration.py`) passes no `context_data`, and `ActiveCalculationStateStore` is documented as
empty at startup by design — so `_resolve_calculation_id` always misses, the function's own
early-return guard fires before any `AuditLog`/`AuditLogStatus` row is ever written, and the new emit
hook (which sits after that point) is never reached. Confirmed by running the scenario in complete
isolation (zero `AuditLog` rows afterward) — pre-existing and independent of this change; every other
call site this task hooks already passes a resolvable `calculation_id`. Fixing the startup-sweep's
resolution is a separate change (e.g. a synthesized id, or looking an existing `AuditLog` up by
`(resource, object_id)` instead of only by `calculation_id`/`template_pk`), out of scope for a task
whose job is emitting onto what the finaliser already succeeds at. The test (7.255) is marked
`@unittest.expectedFailure` and pins the *correct* desired behaviour.

**A test-isolation hazard found in passing, fixed in the test harness only (not production code):**
`_update_operation_context_audit_log` (pre-existing, inside `ensure_terminal_calculation_audit`) calls
`operation_context.set(...)` with no saved reset token, so a direct call to the function from a test
(7.256/7.257) leaks a stale `audit_log_temp` forward onto whatever runs next on the same OS thread —
first discovered because it made 7.255 pass for the wrong reason (reusing an unrelated prior test's
`AuditLog` row) depending on run order within the file. `TestCluster07v_EndToEnd.setUp` now resets
both `operation_context` and `ActiveCalculationStateStore` (a second process-global with the same
cross-test leakage risk) to a clean default on every test. Re-confirmed deterministic: the full file
three times in a row, and 7.255 alone, all give the same `expectedFailure`.
