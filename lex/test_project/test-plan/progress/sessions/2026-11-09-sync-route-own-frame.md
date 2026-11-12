---
date: 2026-11-09
clusters: [15]
tests_added: "7 new scenarios (15.65-15.71, batch 15o); 15f's three @expectedFailure markers dropped (15.19-15.21 now pass)"
suite_tally: "calculation_logging 73 pass / 0 fail / 0 xfail (was 63 / 0 / 3); calculations 218 pass; audit_logging 153 pass / 1 xfail; celery_async 149 pass / 7 skip; signals_ws 49 pass; crud_api 66 pass; api_layer 110 pass; lex/tests/unit/calculation green (lex/core/tests/test_calculation_history_transitions.py: the same 3 failures as the BUG-052 head)"
---

# Batch 15o — a CalculationModel run synchronously gets a frame of its own

Follow-up to the fix for BUG-052 (`2026-11-06-fix-calc-expansion-child-logs.md`), which left 15f
xfail. Recorded as [BUG-055](../../known-bugs.md); the batch is
[15o](../../clusters/15-calculation_logging/batches.md).

**The bug.** On the synchronous route (`calculate_hook` → `_run_in_calculation_executor` →
`execute_calculation_sync`) nothing put the model being calculated on the model context stack. The
API pushes the record a Calculate click runs, and the docs tell callers to wrap a nested save in
`model_logging_context` themselves. A `CalculationModel` saved inside another calculation without
that wrap filed its lines under its caller; 15f had pinned this as xfail since 2026-05-12. Probing the
scope of the fix turned up two more consequences of the same missing frame. `execute_calculation_sync`'s
terminal bookkeeping and `calculate_hook`'s failure handling decide from the stack whether a run is
the root, and for a model the root saved they found the root on top:

- the nested model purged the run's live log when it finished (cluster 9c says only the root may);
- when it failed, a run that did not handle the failure kept no log at all (15j), and a run that did
  got the root's live log written out as a second, detached root node.

The test cache cannot purge by pattern, so the first two show only with Redis. 15.65 and 15.66 answer
`cleanup_calculation`'s key lookup with the run's keys to purge as Redis does.

**The fix (scope chosen by the developer).** `calculate_hook` opens
`CalculationModel.child_calculation_context` in its synchronous branch and closes it in its `finally`,
so the frame covers the run, its terminal bookkeeping and its failure handling. The helper skips a
model already on top: the API's record, `_save_root`'s root, a caller's own wrap. The narrower option
the developer was offered, a frame around `calculate()` only (15f's old docstring), fixes 15f but
leaves both bookkeeping defects. A root saved with nothing on the stack now gets a node keyed to its
record, and keeps its log when it fails (15.68, 15.69); before, its lines went to a node with no
record.

**The executor thread.** `_run_in_calculation_executor` runs inline; it no longer hops to the thread
pool, so it has no `copy_context()` of its own. The one copied context on this route is `One.py`'s
`_calculate_in_background`, which installs a fresh stack holding the record and calls `calculate_hook`
inside `ctx.run`; the frame is opened there and skips the record already on top. 15.70 drives that
route through PATCH `calculate=true`.

**Routes.** The generic Celery `calc_and_save` task already pushes the model it runs (fix for
BUG-052), so the synchronous and Celery routes now give the same tree (15.71, which passes with or
without this fix). A scratch probe found one route that still does not: a nested `CalculationModel`
whose `calculate()` is a `@lex_shared_task` is dispatched with the caller on top of the shipped
stack, and the decorated task wrapper does not push the model, so on the Celery route its lines still
land in the caller's node. On the synchronous route it now gets its own node. Left as a follow-up.

**Checks.** Each scenario was checked to fail with its part of the fix undone (see the 15o batch).
15f and 15o passed three runs in a row. Every cluster in the tally ran from its own `lex*` copy; the
counts for calculations, audit_logging, celery_async, signals_ws and api_layer are the BUG-052 head's.
`lex/core/tests/test_calculation_history_transitions.py` fails the same 3 tests with and without the
fix: its model's table is missing. `test_plan_aggregates.py validate` reports the same 79 problems as
the BUG-052 head, all duplicate scenario ids in cluster 1; none in cluster 15.

**Follow-up for `lex-app-docs`** (mirrored read-only here): the Nested Calculations sections of
`calculations/logging.md` and `reference/CalculationModel Internals.md` tell callers to wrap a nested
save in `model_logging_context`. That is no longer needed on the synchronous route or with the
generic Celery task; the wrap stays harmless, because a model already on top is not pushed again.
