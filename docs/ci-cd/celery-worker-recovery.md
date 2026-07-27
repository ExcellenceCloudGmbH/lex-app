---
title: "Celery Worker Recovery — Operator Guide"
---

This page is the operator's view of the worker-recovery subsystem (`lex/lex_app/celery_recovery/`). If you're a calculation author looking for the customer-facing story, read [Worker Recovery](../features/processing/worker%20recovery.md) instead.

The deep-dive design plan lives at [`docs/celery-worker-recovery/plan.md`](../celery-worker-recovery/plan.md). This doc covers the parts you need at runtime: knobs, Redis keys, log lines, and troubleshooting.

## What runs where

```
┌────────────────────────────────────────────────────────────┐
│ Each Celery worker                                         │
│                                                            │
│  task_prerun  ─► write lex:task:<id>  (envelope)           │
│  HeartbeatThread (daemon) ─►                               │
│       lex:wrk:<host>          every N s, TTL = N * M       │
│       lex:task:<id>/last_hb_iso  every N s                 │
│  task_postrun ─► delete lex:task:<id>                      │
│  worker_shutting_down ─► delete lex:wrk:<host>             │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ Supervisor (Celery beat task `sweep_dead_workers`)         │
│                                                            │
│  every LEX_TASK_SUPERVISOR_SCAN_INTERVAL seconds:          │
│    SCAN lex:task:*                                         │
│    if last_hb_iso older than N * M:                        │
│       if lex:wrk:<host> still alive → skip                 │
│       acquire lex:task:<id>:lock (SET NX EX)               │
│       if attempt+1 ≤ max_retries:                          │
│           app.send_task(name, args, kwargs, task_id=<same>,│
│                         headers={"lex_attempt": attempt+1})│
│       else:                                                │
│           app.backend.mark_as_failure(task_id,             │
│               MaxRequeueExceeded(...))                     │
└────────────────────────────────────────────────────────────┘
```

The supervisor task is registered automatically in `settings.py` under the beat schedule key `lex-celery-recovery-sweep` whenever `LEX_TASK_RECOVERY_ENABLED=true` (the default). It runs as a normal Celery task, so you can also kick it on-demand:

```bash
celery -A lex_app call lex.lex_app.celery_recovery.supervisor.sweep_dead_workers
```

The return value is a summary dict you can read from the result backend.

## Environment knobs

All of these have sensible defaults baked in. You only need to set them when you're tuning.

| Variable | Default | What it does |
|---|---|---|
| `LEX_TASK_RECOVERY_ENABLED` | `true` | Master switch. Set to `false` to disable both the heartbeat and the supervisor. |
| `LEX_TASK_HEARTBEAT_INTERVAL` | `5` | How often each worker writes its heartbeat (seconds). |
| `LEX_TASK_HB_TTL_MULTIPLIER` | `3` | The worker liveness key gets a TTL of `interval * multiplier`. So with defaults, the key expires after 15 s of no heartbeat. |
| `LEX_TASK_SUPERVISOR_SCAN_INTERVAL` | `10` | Beat schedule for the sweep — how often the supervisor wakes up (seconds). |
| `LEX_TASK_MAX_RETRIES` | `4` | Default per-task requeue cap. Overridable per task via `@lex_shared_task(lex_max_retries=N)`. |
| `LEX_TASK_LOCK_TTL` | `30` | TTL on the per-task supervisor lease — long enough for one sweep, short enough that a dead supervisor doesn't block forever. |

Rule of thumb: keep `LEX_TASK_SUPERVISOR_SCAN_INTERVAL` larger than `LEX_TASK_HEARTBEAT_INTERVAL` so the supervisor isn't scanning faster than workers can refresh.

## Redis key inventory

All recovery keys are namespaced under `lex:` to keep them separate from Celery's transport keys.

| Key | Owner | What it holds |
|---|---|---|
| `lex:wrk:<hostname>` | worker (HeartbeatThread) | A small marker proving the worker is alive. TTL = heartbeat-interval × multiplier. |
| `lex:task:<task_id>` | worker (task_prerun) | Hash with `task_name`, `queue`, `attempt`, `max_retries`, `delivery_tag`, `args_b64`, `kwargs_b64`, `hostname`, `last_hb_iso`. Deleted by `task_postrun` on normal completion. |
| `lex:task:<task_id>:lock` | supervisor | Short-lived lease so only one supervisor replica acts on a given task per sweep. TTL = `LEX_TASK_LOCK_TTL`. |

These do **not** touch Celery's `unacked` / `unacked_index` transport keys. With `visibility_timeout=float("inf")` those entries stay around per dead worker — see the open item in the plan for the cleanup approach.

## Reading the logs

Every supervisor action emits a single structured line starting with `lex_recovery`. Grep these in production to answer "what did recovery do":

```
lex_recovery action=requeue reason=stale_heartbeat task_id=... attempt=N new_attempt=N+1
              max_retries=... previous_hostname=... last_hb_age_s=...

lex_recovery action=skip reason=worker_alive_but_task_stale task_id=...
              hostname=... last_hb_age_s=...

lex_recovery action=mark_as_failure reason=max_retries_exceeded task_id=...
              attempt=... max_retries=... previous_hostname=... last_hb_age_s=...

lex_recovery action=skip_failure_already_terminal task_id=... attempt=... max_retries=...

lex_recovery sweep summary={'scanned': N, 'stale': N, 'requeued': N, 'failed': N, ...}
```

Useful queries:

- `lex_recovery action=requeue` — count of recoveries by hour. A spike usually means a node restarted.
- `lex_recovery action=mark_as_failure` — exhausted-retry events. Each one corresponds to a parent calculation that transitioned to ERROR.
- `last_hb_age_s` on a requeue/mark log — how silent the worker was before we acted. If this is consistently close to your stale threshold, your heartbeat cadence is fine. If it's enormous, the supervisor isn't running often enough.

## Troubleshooting

**Tasks never get requeued even though pods are clearly dying.**
First check that the supervisor is actually scheduled: `celery -A lex_app inspect scheduled` should list `lex-celery-recovery-sweep`. If beat isn't running at all, the sweep never fires. Also verify `LEX_TASK_RECOVERY_ENABLED` isn't accidentally set to `false`.

**The supervisor runs but a stale task is never picked up.**
Inspect the Redis hash for the task: `HGETALL lex:task:<task_id>`. If `last_hb_iso` is still recent, the heartbeat thread is alive — the worker is probably busy on something else, not dead. If the hash is missing entirely, the envelope expired (the supervisor can't recover what it can't see).

**A task keeps getting requeued forever.**
Check `HGET lex:task:<task_id> attempt`. It should be incrementing each sweep. If it's not, the `lex_attempt` header isn't being honored on the next worker — verify the recovery package is loaded on **every** worker, not just the dispatching one. The `enable()` call in `lex/lex_app/celery.py` handles this for normal deployments.

**`MaxRequeueExceeded` never raises from the parent.**
The supervisor's `mark_as_failure` writes to `app.backend`, which is the Postgres result backend by default. If the parent uses a different backend (test runs, different config) it won't see the failure. Confirm with `celery -A lex_app result <task_id>` that the backend has the FAILURE row.

**Two supervisor replicas double-requeue the same task.**
They shouldn't — each scan acquires `lex:task:<task_id>:lock` first via `SET NX EX`. If you see double action, look for an unbounded `LEX_TASK_LOCK_TTL` or a stuck supervisor that's been holding the lock past its TTL (and a second one is starting fresh).

**Recovery turned on but worker keys never appear.**
Each worker calls `celery_recovery.enable()` in `lex/lex_app/celery.py` right after `autodiscover_tasks`. If the call is failing (Redis unreachable at boot, for example), look for a `Failed to enable lex celery recovery system; continuing without it` log line — the try/except keeps worker startup unblocked.

**Beat is running but `sweep_dead_workers` never fires (`last_run_at` stays `NULL`, `total_run_count=0`).**
Verify the `PeriodicTask` row is healthy first:

```bash
PYTHONPATH=... DJANGO_SETTINGS_MODULE=lex_app.settings python -c "
import django; django.setup()
from django_celery_beat.models import PeriodicTask
pt = PeriodicTask.objects.get(name='lex-celery-recovery-sweep')
print(pt.enabled, pt.interval, pt.last_run_at, pt.total_run_count)
"
```

If the row looks healthy (`enabled=True`, `interval=every N seconds`, `start_time=None`) but `last_run_at` stays `None` after a full minute of beat running, the supervisor itself is **not** the bug — beat's `DatabaseScheduler` is wedged. Confirm by invoking the sweep manually:

```bash
celery -A lex_app call lex.lex_app.celery_recovery.supervisor.sweep_dead_workers
```

If the survivor worker logs `Task ... received` + `Task ... succeeded`, the supervisor is fine. The fix is to swap beat off DatabaseScheduler for the duration of the test:

```bash
export CELERY_BEAT_SCHEDULER=celery.beat:PersistentScheduler
lex celery beat -l info
# Banner should now read: scheduler -> celery.beat.PersistentScheduler
# And every N seconds: "Scheduler: Sending due task lex-celery-recovery-sweep"
```

The default `PersistentScheduler` reads `CELERY_BEAT_SCHEDULE` straight out of `settings.py` and persists `last_run_at` to a small on-disk file — much harder to break than DatabaseScheduler, which depends on writable DB transactions and matching timezones between Django and Postgres. When you're done: `rm -f celerybeat-schedule* celerybeat.pid`.

The framework setting `CELERY_BEAT_SCHEDULER` in `lex/lex_app/settings.py` defaults to `DatabaseScheduler` but is `os.getenv` overridable specifically for this case.

## Disabling recovery for a single run

Set `LEX_TASK_RECOVERY_ENABLED=false` in the environment of both the worker and the beat process. The heartbeat thread and the supervisor will both no-op; everything else (task dispatch, normal failures) behaves as before.

## Manual local chaos test

When the unit suite (cluster 8r) and the live-broker smoke (cluster 8s) aren't enough — for example you want to convince yourself with your own eyes that a SIGKILL'd worker actually gets requeued on a different worker — this is the recipe.

**Critical gotcha first.** You need **two** workers, not one. Beat *schedules* `sweep_dead_workers`, but beat does not *execute* tasks; a worker has to consume the sweep job from the broker queue. If you SIGKILL the only worker, the sweep task itself sits in the queue with no consumer, beat's `last_run_at` stays `NULL`, and nothing recovers anything. Run two workers — one is the victim, one is the survivor.

**Recipe.** Tune the knobs short so the cycle is observable in seconds rather than minutes:

```bash
# In every terminal below, source the project env first.
cd /home/syscall/LUND_IT/ArmiraCashflowDB
source .venv/bin/activate
set -a && source .env && set +a

# Knobs for fast feedback. Stale = interval × multiplier = 4s.
# Cap at 2 retries so MaxRequeueExceeded fires in ~12s of unattended killing.
export CELERY_ACTIVE=true
export LEX_TASK_RECOVERY_ENABLED=true
export LEX_TASK_HEARTBEAT_INTERVAL=2
export LEX_TASK_HB_TTL_MULTIPLIER=2
export LEX_TASK_SUPERVISOR_SCAN_INTERVAL=3
export LEX_TASK_MAX_RETRIES=2

# Register the built-in slow task. Off by default; this opts in.
export LEX_RECOVERY_SMOKE_TASK=true
```

**Terminal 1 — beat (scheduler only, no execution):**

```bash
lex celery beat --scheduler django_celery_beat.schedulers:DatabaseScheduler -l info
```

You should see `lex-celery-recovery-sweep` in the schedule on startup.

**Terminal 2 — victim worker (will be killed):**

```bash
lex celery worker -n victim@%h --concurrency=1 -l info
```

**Terminal 3 — survivor worker (will run sweeps and pick up the requeue):**

```bash
lex celery worker -n survivor@%h --concurrency=1 -l info
```

**Terminal 4 — fire a long-running task and watch it route to the victim:**

```bash
celery -A lex_app call \
  lex.lex_app.celery_recovery.tasks.recovery_smoke_slow \
  --args='[120]'
```

Once you see `recovery_smoke_slow starting` in **victim**'s log, kill it:

```bash
pkill -9 -f 'victim@'
```

**What to expect, in order:**

1. **0–4 s after kill** — survivor's beat-driven sweep logs `action=skip reason=worker_alive_but_task_stale` while the victim's `lex:wrk:victim@...` key is still within its TTL.
2. **4–8 s after kill** — `lex_recovery action=requeue reason=stale_heartbeat task_id=<id> attempt=0 new_attempt=1 last_hb_age_s=...` — the supervisor re-publishes the **same `task_id`** to the queue.
3. **A moment later** — survivor's worker log shows `Task lex.lex_app.celery_recovery.tasks.recovery_smoke_slow[<same id>] received` and the body resumes.
4. **If you don't restart anything and just keep killing whichever worker picks it up** — after `LEX_TASK_MAX_RETRIES + 1` cycles, you'll see `lex_recovery action=mark_as_failure reason=max_retries_exceeded`. From a fifth terminal: `celery -A lex_app result <task_id>` shows `FAILURE` and the exception is `MaxRequeueExceeded`.

**Inspect Redis at any point:**

```bash
redis-cli -n 1 KEYS 'lex:*'                         # all recovery keys
redis-cli -n 1 HGETALL 'lex:task:<paste-task-id>'   # full envelope
redis-cli -n 1 TTL 'lex:wrk:victim@<host>'          # worker key liveness
```

The hash TTL is `interval × multiplier × 4` (32 s with the knobs above). If you wait longer than that to inspect a dead-worker envelope, the hash will have expired and the supervisor can't recover what it can't see — that's a feature, not a bug, but it means **inspect quickly**.

**Cleanup when done:**

```bash
unset LEX_RECOVERY_SMOKE_TASK   # so you don't ship the smoke task by accident
pkill -f 'celery'
redis-cli -n 1 --scan --pattern 'lex:*' | xargs -r redis-cli -n 1 DEL
```

> ⚠️ `LEX_RECOVERY_SMOKE_TASK` registers a sleep task on the worker. Never set it in a customer environment — it exists purely so this recipe doesn't depend on every project providing its own long-running task. The framework's `tasks.py` keeps it env-gated for that reason.

## Related docs

- [Worker Recovery (feature doc)](../features/processing/worker%20recovery.md) — the user-facing story.
- [`docs/celery-worker-recovery/plan.md`](../celery-worker-recovery/plan.md) — the full design plan and progress log.
- [Celery & Async Calculations](../features/processing/celery%20and%20async%20calculations.md) — broader Celery setup this layers on top of.
