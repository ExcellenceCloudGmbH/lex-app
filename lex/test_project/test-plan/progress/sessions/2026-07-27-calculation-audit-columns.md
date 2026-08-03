---
date: 2026-07-27
clusters: [7, 8]
tests_added: 26
suite_tally: "calculations+celery_async: 351 pass / 7 skip / 0 xfail; audit+history+validation: 224 pass / 2 xfail"
---

# Calculation audit columns: `edited_at` / `edited_by` must survive a calculation

Customer ticket (2026-07-27, instance 1430, lex-app `2.0.0rc199`): triggering a
calculation updated `edited_at`, an audit-relevant field. The expectation — a
calculation is system-triggered work, not a user edit — is the framework's
documented intent, and the report is real. A second report (no Celery: run a
calculation, restart the server → record `ABORTED` with `edited_at` changed)
turned out to be the **same defect**.

**Root cause (one bug, two saves that escaped the guard):**
1. The HTTP `calculate=true` trigger (`One.update` → `perform_update`) saves the
   row carrying `_defer_calculate_hook`; that save skipped *history* but not
   `edited_at`/`edited_by`, so it stamped them. A completing run reverted the
   value, hiding it — but an interrupted run (server restart) never reached the
   revert, leaving the stamp on the `ABORTED` row.
2. On Celery, a `@lex_shared_task`-decorated `calculate()` ran in a worker whose
   wrapper entered `CeleryCalculationContext` but not
   `calculation_execution_context()`, so `self.save()` inside the calc body
   stamped — surviving even a clean SUCCESS.

**Not a regression** — the guard, `One.perform_update`, and the wrapper are
byte-identical between `v2.0.0rc199` and HEAD.

**Fix (guard-level):** `_should_skip_edited_fields_update()` now also suppresses
when `_defer_calculate_hook` is set, and the `lex_shared_task` worker wrapper
enters `calculation_execution_context()`. Correctness comes from the guard for
every calculation-owned save; the brittle completion-time revert is no longer
load-bearing. Tracked and resolved as **BUG-028**.

- [Batch 7s](../../clusters/07-calculations/batches.md) — 17 pass. Celery-OFF
  paths plus the **real HTTP `calculate=true` endpoint**: 7.219 is the reported
  interrupted→restart→ABORTED case; 7.220 proves the stamp is absent before any
  revert; negative controls (7.213–7.215, 7.221) keep genuine user edits stamping.
- [Batch 8c](../../clusters/08-celery_async/batches.md) — 9 pass. Both Celery
  dispatch paths; the four former xfails are now live regression gates.

**Why earlier coverage missed it:** the contract had no tests, and a programmatic
trigger (`obj.is_calculated = IN_PROGRESS; obj.save()`) bypasses `One.update`, so
it never exercises the HTTP trigger save the UI actually uses.
