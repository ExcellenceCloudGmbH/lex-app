---
date: 2026-10-22
clusters: [6, 7]
tests_added: "25 new scenarios (6.168, 7.238-7.261)"
suite_tally: "test_7v_calculation_finished_events.py + test_6u_fleet_metrics_audit_contract.py: 27 pass / 0 fail, 4 subtests pass — confirmed deterministic across repeated full-file runs and every test run alone"
---

# Batches 6u, 7v — the calculation_finished fleet-metrics event

Branch `feat/fleet-metrics-calculation-events`, continuing from the event emitter and heartbeat work
on `feat/fleet-metrics-emitters`. Scope: lex-app sends one `calculation_finished` event per root
calculation run, through that emitter. `lex/lex_app/fleet_metrics/emitter.py` and
`lex/core/services/usage_config.py` are read-only dependencies here, not touched.

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
after its `transaction.atomic()` block, wrapped in a bare `try/except` plus
`emitter.guard("calculation_audit.emit")` so an import failure there can't propagate out of the
finaliser after the audit write has already committed, and the status-row re-fetch is skipped
entirely when usage metrics are off. Every terminal path that already converges there — sync
success/error/cancel, a Celery worker's success/failure callback, and the startup-recovery abort
sweep — converges on the event too, with no new root-run detection logic of its own.
`celery_tasks.py`'s `_update_model_status` calls `emitter.flush(5)` at the end so a worker's event
leaves before `SIGTERM`.
[6u](../../clusters/06-audit_logging/batches.md) is the scenario-6.168 audit-contract test: the
out-of-repo fleet-metrics backfill reads `audit_logging_auditlog` /
`audit_logging_auditlogstatus` directly by column name and type, so this batch pins that contract at
the `Model._meta` level (no DB access needed) rather than leaving it to be discovered by a silent
backfill failure in a different repo.

**One real framework bug found and fixed (BUG-034):** the startup-recovery abort sweep's call to
`ensure_terminal_calculation_audit(instance, audit_status="aborted", ...)` (`model_registration.py`)
passed no `context_data`, and `ActiveCalculationStateStore` is documented as empty at startup by
design, so `_resolve_calculation_id` always missed and no audit row (or event) was ever written for
that path. Confirmed by running the scenario in complete isolation before the fix (zero `AuditLog`
rows afterward). Fixed by looking up the interrupted run's own pending `AuditLog` — the one written
when the run started, by `(content_type, object_id, status='pending')` — and handing it through as a
template, so that real row is finalised instead of a fresh one being silently skipped. 7.255 seeds
that pending row the way a real interrupted run leaves it and asserts the event carries its
`audit_log_id` with a real, non-null duration; 7.259 covers the narrower residual case (no pending row
at all) where there is still nothing to key an event on. See `known-bugs.md`.

**A test-isolation hazard found in passing, fixed in the test harness only (not production code):**
`_update_operation_context_audit_log` (pre-existing, inside `ensure_terminal_calculation_audit`) calls
`operation_context.set(...)` with no saved reset token, so a direct call to the function from a test
leaks a stale `audit_log_temp` forward onto whatever runs next on the same OS thread — first
discovered because it made the startup-abort scenario pass for the wrong reason (reusing an unrelated
prior test's `AuditLog` row) depending on run order within the file. `TestCluster07v_EndToEnd.setUp`
resets both `operation_context` and `ActiveCalculationStateStore` (a second process-global with the
same cross-test leakage risk) to a clean default on every test, and pins `usage_metrics_enabled` to
`True` at both call sites so a developer's local `.env` can't turn these tests red. Re-confirmed
deterministic: the full file three times in a row, and each scenario run alone.

Two more scenarios pin properties the hook relies on but nothing had exercised directly: 7.260 (a
raising emit hook leaves `ensure_terminal_calculation_audit`'s own result and committed row intact)
and 7.261 (a nested, non-root child run never reaches the hook at all).
