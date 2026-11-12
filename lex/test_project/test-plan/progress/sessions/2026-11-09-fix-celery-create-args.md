---
date: 2026-11-09
clusters: [8]
tests_added: "7 new scenarios (8.157-8.163); BUG-054 resolved"
suite_tally: "celery_async + calculations: 374 pass / 7 skip / 0 fail / 31 subtests in 4 min 55 s (celery_async 156 pass / 7 skip, the opt-in Redis broker and recovery-beat tests; calculations 218 pass). 8e on v2.4.2 source: 3 fail (8.157, 8.159, 8.163) / 4 pass"
---

# Batch 8e: `create(*args)` reach `calculate()` on the Celery route too (BUG-054)

See [8e](../../clusters/08-celery_async/batches.md). `create(*args)` passes its positional
arguments to every generated row's `calculate()`. The sync routes did, and the dispatcher sent them
in each group message, but the Celery task `calc_and_save` called `calculate()` without them. A
`calculate()` with defaults silently computed different rows in production than locally. One with a
required argument failed in every task, and the dispatcher re-ran the whole batch synchronously.

The task now names the `group_index` slot of its message and passes the rest on. The message keeps
the shape it always had. Moving the index to a keyword would make an upgraded worker read a message
queued before the upgrade with the old index as `calculate()`'s first argument; 8.159 fails on that
design. The batch covers every route `create()` can take, plus `dispatch_calculation_task`, the task's
other sender. Three scenarios fail on v2.4.2.

Under plain eager mode a task sees the dispatcher's own `WaitForTasks` scope, which a worker process
never has, and a decorated batch's results are then lost to a pickled copy. 8e runs each eager task
with empty dispatch scopes instead.
