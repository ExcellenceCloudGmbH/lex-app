---
date: 2026-07-14
clusters: [8ae]
tests_added: "2 (8.145–8.146) + fixtures NonIdempotentCalc/NonIdempotentChild in celery_async/models.py (no framework source change — reproduction only)"
suite_tally: "8ae 1 pass / 1 xfail-strict (BUG-026); full celery_async without the broker flag: unchanged (8ae skips cleanly)"
---

**Batch 8ae landed — real-worker reproduction of the stale-queue double
execution (BUG-026).** Customer log (2026-07-14, `PlanningRun` "SD 2026" with
worker2/3/4/7 booting): the same update ran TWICE concurrently under two
distinct `planningrun_2_update_<uuid>` calculation_ids; each run bulk-created
its 12 `Sponsor` children and the second died with `MultipleObjectsReturned:
get() returned more than one Sponsor -- it returned 2!`. Traced the mechanism:
a `calc_and_save` message dispatched while no worker was consuming parks on
the Redis broker; the server restart runs the startup sweep
(`_handle_calculation_model_reset`), which flips the row
IN_PROGRESS→ABORTED — **reopening One.py's duplicate-calculation guard** — so
the user's re-click dispatches a second task with a fresh calculation_id. The
sweep neither purges the broker message nor sets the cluster cancel marker
for the abandoned calculation, so the 8ad/`calc_and_save` task-start checks
find nothing and BOTH messages execute at worker boot. Reproduced end-to-end
with a real Redis broker + a real in-process worker (`start_worker`, the 8k
opt-in pattern — the showcase CI Redis service runs it on every release):
the repro shows exactly 2 executions with 2 distinct calculation_ids.
Harness subtlety worth keeping: dispatched instances must carry
`_calculation_hook_in_progress=True` (as `calculate_hook` pickles them) or
`model.save()` in the worker re-enters the hook and double-executes within
one task, masking the real bug. Fix direction (not in this change): the sweep
should `mark_cancelled(calculation_id)` for every row it aborts — the
existing task-start checks then land the stale message CANCELLED, and 8.146
flips green. See [batch 8ae](../../clusters/08-celery_async/batches.md) and
BUG-026 in [known-bugs.md](../../known-bugs.md).
