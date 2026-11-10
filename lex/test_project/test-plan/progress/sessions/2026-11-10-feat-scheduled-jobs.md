---
date: 2026-11-10
clusters: [1, 4, 6, 7, 8, 10]
tests_added: "53 new scenarios across six batches: 8f (8.167-8.182), 7x (7.269-7.278), 10ac (10.175-10.183), 6v (6.173-6.175), 4z (4.159-4.162), 1bk (1.619-1.629)"
suite_tally: "new batches 53 pass / 0 fail. Full non-stress suite vs an untouched lex-app-v2 checkout: identical outcomes — through init/test_1ap the same 7 pre-existing failures on both (first test of 10a and of 15a-15e, gate_selftest); init/test_1aq_reflex_dashboards busy-loops on both and was excluded; the rest: baseline 656 pass / 15 skip / 1 xfail, branch 671 pass / 15 skip / 1 xfail, 0 fail"
---

# Scheduled jobs — batches 8f, 7x, 10ac, 6v, 4z, 1bk

Branch `feat/scheduled-jobs`; design and implementation plan:
[`docs/superpowers/plans/2026-11-10-scheduled-jobs.md`](../../../../../docs/superpowers/plans/2026-11-10-scheduled-jobs.md).
A new framework app, `lex.scheduling`, stores schedules as `ScheduledJob` rows (a `CalculationModel`)
and fires them from a clock thread in the served backend — no pod, no Celery beat, the same on
instances with and without workers.

- [8f](../../clusters/08-celery_async/batches.md) — the cron engine and the tick (claim, overlap, missed slots, one-off, Celery route).
- [7x](../../clusters/07-calculations/batches.md) — what a run does, through the calculation state machine.
- [10ac](../../clusters/10-api_layer/batches.md) — the REST/UI surface, and `CalculationModel.reset_calculation_on_edit`.
- [6v](../../clusters/06-audit_logging/batches.md) — the run's audit entry; the opener's `trigger` now survives finalisation.
- [4z](../../clusters/04-permissions/batches.md) — a schedule can do no more than the person who saved it.
- [1bk](../../clusters/01-init/batches.md) — bootstrap: the clock, `@lex_job` declarations, `lex run_scheduler`.

Writing the tests surfaced four defects in the first draft, all fixed before landing: switching off a
schedule whose function was gone raised (1.625), saving a schedule loaded mid-run started a second run
(7.278), and an edit through the app reset a running job's status and so blinded the overlap check
(10.182), and an edit from a copy loaded before a run wrote the run's slot back (8.182). The Calculate button's start path moved, unchanged, into `lex/core/calculation_start.py`
so the button, `calculate_on_create` and the scheduler share it.

Letters and ranges were taken past what in-flight branches (feat/ext-*, feat/auth-long-sessions)
already hold, so neither side renumbers on merge.
