# Session — Bitemporal activation catch-up (batch 5n)

**Date:** 2026-08-04
**Batch:** 5n (scenarios 5.104–5.109), 6 pass

## Why

A future-dated history row schedules a timer to activate itself. Both timer
backends are volatile, and the in-process one — used whenever `CELERY_ACTIVE` is
false, i.e. 47 of 53 instances — is held in a daemon thread with nothing to
rehydrate it. Every backend restart silently dropped that instance's pending
activations: the meta row stayed `SCHEDULED` forever, the main table never
caught up, no error was raised.

The timer was being treated as the system of record. It is not — the meta row
is. `meta_task_status = SCHEDULED` plus the history row's `valid_from` states
exactly what should already have happened, and both live in the instance's own
database.

## What landed

`lex/core/services/activation_reconcile.py` — a pass that finds overdue
`SCHEDULED` rows and activates them, run at backend startup and every 60s
thereafter (`running_in_uvicorn()`-gated, so management commands and workers do
not duplicate it). Four `LEX_ACTIVATION_RECONCILE_*` settings.

This inverts the failure model: a timer that never fires now costs *latency*
rather than losing the activation. That is what makes retiring per-instance beat
safe, and what would make any future cluster-level scheduler an optimisation
allowed to fail rather than a component 53 instances depend on.

## Two defects the tests caught while being written

Both would have shipped as silent no-ops:

1. **`activate_history_version` reports failure by return value, not by
   raising** — `"failed_too_early"`, `"skipped_missing_record"`,
   `"failed_model_lookup"`, `"success"`. The first implementation treated a
   clean return as success, so it counted activations that never happened and
   dropped them from the retry set. 5.104 failed and exposed it.
2. **`@lex_shared_task` wraps that return as `(result, args)`**, so the status
   string needs unwrapping. `_outcome_of` unwraps defensively rather than
   indexing `[0]`, so a future change to the decorator degrades to "not
   success" — a retry — instead of a spurious success.

A third was a test bug, not a source bug: faking `now` selects a row that
`activate_history_version` then declines, because it re-checks `valid_from`
against the real clock. The fixture moves `valid_from` into the past instead,
which is what an hour actually passing looks like to every layer at once.

## Companion

Terraform `feat/activation-reconcile-retire-beat` sets the env unconditionally
(not in the MQ/Worker branch — the non-Celery instances are the ones that need
it) and records that beat is no longer required for bitemporal.
