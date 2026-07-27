---
title: "Worker Recovery"
---

Calculations that get dispatched to Celery normally run to completion on whichever worker picked them up. But every now and then a worker process disappears mid-task — the pod gets evicted, a node reboots, an OOM killer fires. Without recovery, the parent calculation would block forever waiting for a result that's never coming.

The framework now ships a small recovery system that handles this transparently. You don't write any extra code: if a worker dies while running one of your `@lex_shared_task` methods, the task is automatically re-published to the queue and a fresh worker picks it up.

## What you get out of the box

- **Bounded retries.** If a worker dies, the task is requeued. After a configurable cap (default 4 retries) the recovery system gives up and marks the task as failed in the result backend, which causes the parent calculation's `WaitForTasks` block to raise an exception — just like a normal task failure would.
- **Same result identity.** The retried task keeps the same task ID it had originally, so any code already waiting on it (`result.get()`, `WaitForTasks`) continues to work without rewiring.
- **Normal failure paths still work.** Ordinary exceptions inside your task body still go through `on_failure` as before. The recovery system only steps in when the worker itself dies.

## When does it kick in?

Each worker writes a heartbeat for every task it's actively running. A separate supervisor (scheduled by Celery beat) wakes up every few seconds and looks for tasks whose heartbeats have gone silent. If the worker that owned the task is also gone, the supervisor re-publishes the task and bumps an internal attempt counter.

If a task's worker comes back to life right after the supervisor noticed silence, the supervisor will see the worker is alive and back off — so a slow heartbeat doesn't cause a spurious requeue.

## Setting the retry cap

The default cap is fine for most tasks. If you have a particularly expensive calculation where you'd rather fail fast than churn through retries, you can lower the cap per-task:

```python
from lex.lex_app.celery_tasks import lex_shared_task

@lex_shared_task(lex_max_retries=1)
def expensive_child_calculation(self):
    ...
```

Or raise it for a task that's so critical you want every chance to recover:

```python
@lex_shared_task(lex_max_retries=10)
def critical_export(self):
    ...
```

`lex_max_retries` is independent of Celery's built-in `max_retries` (which is for in-task `self.retry()` calls). You can set both if you want different behavior for the two failure modes.

## What does a failed recovery look like from your code?

When the cap is exceeded, the parent calculation's `WaitForTasks.wait_for_completion()` raises a `MaxRequeueExceeded` exception. The exception message includes the task ID, the last known worker hostname, and the attempt count, so you can spot recovery failures distinctly from normal task failures in logs.

If you're catching exceptions inside `WaitForTasks` to handle partial failures, just include `MaxRequeueExceeded` (or its parent `WorkerLost`) in your `except` clause:

```python
from lex.lex_app.celery_recovery.exceptions import WorkerLost

try:
    with WaitForTasks() as wait:
        child_a.delay()
        child_b.delay()
        # ...
except WorkerLost as e:
    # A child task's worker died and never recovered within the retry budget.
    logger.error("child %s could not be recovered: %s", e.task_id, e)
    raise
```

You don't have to handle it specially — if you don't, it propagates and the parent calculation transitions to `ERROR` like any other unhandled task exception.

## Turning it off

If you ever need to run with recovery completely disabled (for example to reproduce an old behavior locally), set:

```bash
LEX_TASK_RECOVERY_ENABLED=false
```

The worker still runs, your tasks still execute — they just won't be requeued if the worker dies.

## What about synchronous mode?

Recovery is a no-op when `CELERY_ACTIVE=false`. Synchronous calculations run in the request thread and either succeed or raise an exception inline; there's no worker to die.

---

Need to dig into the moving parts (Redis keys, sweep schedule, tuning knobs)? See the operator notes in [Celery Worker Recovery — Operator Guide](../../ci-cd/celery-worker-recovery.md).
