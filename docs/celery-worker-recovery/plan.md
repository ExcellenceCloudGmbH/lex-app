---
title: "Celery Worker Recovery — Bounded Requeue on Worker Death"
status: IMPLEMENTED — Phases 1–7 landed 2026-05-20; integration smoke harness (cluster 8s) landed 2026-05-20; customer-shaped end-to-end recovery suite (cluster 8t, 6 scenarios) landed 2026-05-20 following the test-plan §9 Golden Rule (real `CeleryCalc` models, real `@lex_shared_task` path, assertions on `is_calculated`) and verified green against a live Redis broker (36/36 tests in 11.9 s)
owner: hazem
created: 2026-05-20
last-updated: 2026-05-20
related-files:
  - lex/lex_app/celery.py
  - lex/lex_app/celery_tasks.py
  - lex/lex_app/settings.py
  - lex/lex_app/celery_recovery/
  - docs/features/processing/worker recovery.md
  - docs/ci-cd/celery-worker-recovery.md
---

# Celery Worker Recovery — Bounded Requeue on Worker Death

## 0. TL;DR for the next agent

We need a recovery mechanism on top of the existing Celery setup that:

1. **Detects** when a worker dies mid-task using a *heartbeat*, **not** `visibility_timeout`. `visibility_timeout` is currently `float("inf")` and must not be relied on as the recovery signal.
2. **Requeues** the dead worker's in-flight task back to the broker, **preserving the original `task_id`** so the parent's `result.get()` keeps blocking on the same result row.
3. **Caps** requeue attempts at a configurable max (default 4). On the (max+1)-th would-be requeue, the supervisor writes a `FAILURE` to the result backend so the parent thread blocked inside `WaitForTasks.wait_for_completion()` raises and the parent calculation is marked `ERROR`.
4. **Reuses the existing callback path** — when the requeued task finally executes (or fails through the supervisor), `CallbackTask.on_success` / `on_failure` runs as today and `is_calculated` is updated on the `CalculationModel`.

Implementation status as of 2026-05-20: **Phases 1–7 are landed**. The recovery package lives at `lex/lex_app/celery_recovery/`, the supervisor runs from Celery beat under the schedule key `lex-celery-recovery-sweep`, and 28 unit tests in `lex/test_project/tests/celery_async/test_8r_worker_recovery.py` cover the heartbeat, supervisor, decorator surface, and failure-injection paths. The remaining open item is an integration smoke run against a real Redis + Postgres broker (see Phase 4 progress entry).

Customer-facing docs: [`docs/features/processing/worker recovery.md`](../features/processing/worker%20recovery.md).
Operator / DevOps docs: [`docs/ci-cd/celery-worker-recovery.md`](../ci-cd/celery-worker-recovery.md).

---

## 1. Problem statement

### Observed symptoms (infra only — not reproducible locally)

- A worker pod can deadlock on a single task. Parent's `result.get()` never returns.
- When a worker dies (OOM, SIGKILL, evicted pod) we have no way to talk to it and no way to know which task it had.
- The framework already sets `task_acks_late=True` and `task_reject_on_worker_lost=True`, which would normally cause Celery to redeliver the lost task. But the Redis transport uses `visibility_timeout` as the sweep for orphaned `unacked` entries, and we have `visibility_timeout=float("inf")` in `CELERY_BROKER_TRANSPORT_OPTIONS` (`lex/lex_app/settings.py:398-405`). With `inf`, the orphaned entry sits in Redis's `unacked` hash forever and the task is never redelivered.

### Why we can't just lower `visibility_timeout`

Per project decision: `visibility_timeout` is a blunt instrument — it's queue-wide, it forces every long-running task to be smaller than the sweep window, and on Redis transport a redelivery from `visibility_timeout` doesn't carry retry-count state so we can't bound it cleanly. We want a heartbeat-based mechanism that operates per-task and is independent of how long the task takes to run.

We will keep `visibility_timeout=float("inf")` as is (long-stop), and layer our own bounded recovery on top.

### What "worker dies" means in this design

Anything that prevents the worker from running its task body to completion *or* from sending `on_failure` to the result backend:

- Pod evicted / OOM-killed / `SIGKILL`
- Node lost
- Network partition between worker and Redis (long enough for heartbeat TTL to elapse)
- Process hangs in C code or a runaway loop with no GIL release

A *normal* exception inside the task body is **not** in scope — that already flows through `CallbackTask.on_failure` (`lex/lex_app/celery_tasks.py:126`). We must not double-handle those.

---

## 2. User story (from product owner)

> A `CalculationModel` runs in the backend thread and, inside a `WaitForTasks` block, spawns 5 child calculations (each decorated with `@lex_shared_task`). Four finish; one is still pending. The main thread blocks on that pending one.
>
> - If the task fails normally → `on_failure` fires as today.
> - If the worker dies for an unexpected reason → the task should be requeued, up to a configurable max (4–5 attempts). If still not done after max attempts, the supervisor must inject a failure so the main calculation fails with a clear exception.

---

## 3. Constraints and non-goals

### Constraints

- **No reliance on `visibility_timeout`** for recovery detection.
- **Same `task_id` on requeue.** The parent is holding an `AsyncResult` keyed by the original task_id; replacing it breaks the join.
- **Bounded retries.** Default 4, configurable per-deployment and per-task.
- **Result backend is Postgres** (`db+postgresql`, see `lex/lex_app/settings.py:381-385`). Failure injection writes to `celery_taskmeta`.
- **`task_acks_late=True` stays on.** We rely on Celery still holding the task in `unacked` so we can re-publish from the original payload.
- **Pickle serializer stays on.** We will pass `CalculationModel` instances and richer kwargs through requeue without conversion.
- **One worker = one in-flight task at any time** (concurrency=1 + prefetch=1 + post-task pod shutdown in `lex/lex_app/celery.py:49-86`). This simplifies bookkeeping — there is only one active `task_id` per worker hostname at a time.
- The new module must keep working when `CELERY_ACTIVE=false` (no broker connection attempted, no supervisor running). Tests must not require Redis to be installed.

### Non-goals (for this round)

- Replacing the result backend.
- Removing `visibility_timeout=float("inf")` (left as a long-stop).
- Cross-region / multi-broker failover.
- Auto-scaling worker pool sizing.
- Replacing Flower or building a new dashboard.

---

## 4. Design overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  Backend (web) process                                                │
│                                                                       │
│  CalculationModel.calculate()                                         │
│    with WaitForTasks():                                               │
│        child.save()  → calc_and_save.delay(...)  ───────────┐         │
│                                                              │         │
│    WaitForTasks.__exit__ → wait_for_completion()             │         │
│        for r in results: r.get()  (blocks on Postgres row)   │         │
└──────────────────────────────────────────────────────────────┼─────────┘
                                                               │
                                                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Redis (broker only)                                                  │
│   queue:<queue_name>      (LIST) — pending tasks                      │
│   unacked / unacked_index  — in-flight, reserved by worker            │
│                                                                       │
│   lex:wrk:<hostname>       (STRING, TTL ~3×hb) — worker liveness     │
│   lex:task:<task_id>       (HASH) — task tracking                     │
│      worker, attempt, max_retries, task_name, queue,                  │
│      args_b64, kwargs_b64, parent_task_id, last_hb_iso                │
│   lex:task:<task_id>:lock  (STRING, TTL 30s) — supervisor lease       │
└──────────────────────────────────────────────────────────────────────┘
        ▲                                                ▲
        │ heartbeat writes                               │ scans for stale
        │                                                │
┌───────┴────────────────────────────┐         ┌─────────┴───────────────┐
│  Worker process                     │         │  Supervisor process     │
│                                     │         │  (Celery beat task OR   │
│  task_prerun  → register task        │         │   standalone CLI)        │
│  HeartbeatThread (every 5s):         │         │                          │
│      SET lex:wrk:<host>  EX 15       │         │  Every 10s:              │
│      HSET lex:task:<id>  last_hb     │         │   for each lex:task:*:   │
│  task_postrun → unregister task      │         │     if last_hb stale:    │
│  worker_shutting_down → del wrk key  │         │       acquire lock       │
└─────────────────────────────────────┘         │       decide requeue/fail│
                                                 │       publish or mark    │
                                                 └─────────────────────────┘
                                                              │
                                                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Postgres (result backend)                                            │
│   celery_taskmeta: task_id → STATUS, RESULT                           │
│   On max-retries-exceeded: supervisor writes FAILURE here              │
│   → parent's r.get(propagate=True) raises → parent calc → ERROR        │
└──────────────────────────────────────────────────────────────────────┘
```

### Why heartbeat *per task* (and not just per worker)

We do have one task per worker today, but conceptually the bookkeeping is per-task because:

- The supervisor needs the original args/kwargs/queue *of the task* in order to re-publish it.
- The attempt counter is a property of the task, not the worker.
- A worker can be alive but stuck (rare, but possible — Python hung in C code with GIL held). A per-task heartbeat refreshed *from inside the task* would catch this; a worker-only heartbeat would not.

We'll do both keys. `lex:wrk:<host>` is for operator visibility (`KEYS lex:wrk:*` shows live workers). `lex:task:<id>` is the source of truth for recovery decisions.

---

## 5. Component breakdown

### 5.1 Heartbeat writer (worker-side)

Lives in the worker process. A daemon thread started on `worker_ready` and stopped on `worker_shutting_down`.

Responsibilities:

- Every `LEX_TASK_HEARTBEAT_INTERVAL` seconds (default 5):
  - `SET lex:wrk:<hostname> "1" EX <ttl>` where ttl = interval × `LEX_TASK_HB_TTL_MULTIPLIER` (default 3 → 15s).
  - For each task currently in `_in_flight_tasks` (in practice 0 or 1): `HSET lex:task:<task_id> last_hb_iso=<now>` and `EXPIRE lex:task:<task_id> <ttl>`.
- If Redis is unreachable: log at WARNING and keep trying. Do not crash the worker.

### 5.2 Task lifecycle hooks (worker-side)

Connect to Celery signals in the same module.

- `task_prerun`: register the task. If `attempt` is in headers, read it (this is a requeue); else attempt=0. Write the `lex:task:<id>` hash with full payload so a future requeue can re-publish without help from Redis's `unacked` store. Add `task_id` to a thread-local `_in_flight_tasks` set so heartbeat refreshes it.
- `task_postrun`: unregister the task. Delete `lex:task:<id>`. Remove from `_in_flight_tasks`. This is the success-or-handled-exception path — supervisor does not act on tasks that have a postrun.
- `worker_ready`: start the heartbeat thread.
- `worker_shutting_down`: stop the heartbeat thread, delete `lex:wrk:<hostname>`. (Do **not** delete in-flight `lex:task:*` keys on graceful shutdown — the supervisor will pick them up and requeue, because the task didn't postrun.)

### 5.3 Supervisor (sweeper)

Stateless. Can be invoked from:

- (Preferred for v1) A periodic Celery beat task `lex.celery_recovery.tasks.sweep_dead_workers`, scheduled every `LEX_TASK_SUPERVISOR_SCAN_INTERVAL` seconds (default 10).
- (Optional fallback) A `lex celery supervisor` CLI subcommand for environments without beat.

Algorithm per scan:

```
for key in SCAN MATCH "lex:task:*" (filter out :lock):
    data = HGETALL key
    last_hb = ISO-parse(data["last_hb_iso"])
    if now - last_hb < stale_threshold:
        continue  # still alive
    task_id = key.split(":")[-1]
    lock_key = f"lex:task:{task_id}:lock"
    if not SET lock_key "<sup_id>" NX EX 30:
        continue  # another supervisor instance is handling it

    attempt = int(data["attempt"])
    max_retries = int(data["max_retries"])
    if attempt + 1 > max_retries:
        write_failure_to_backend(task_id, MaxRequeueExceeded(...))
        cleanup_redis_for_task(task_id)
    else:
        republish_task(task_id, data, attempt + 1)
        # Do NOT delete lex:task:<id> — task_prerun on next worker will overwrite

    DEL lock_key
```

Key implementation notes:

- `republish_task` calls `app.send_task(name, args, kwargs, task_id=task_id, queue=queue, headers={"lex_attempt": attempt+1})`. The new header lets `task_prerun` read the carried attempt. Belt-and-braces: also write `attempt=attempt+1` into the Redis hash before sending, so even without headers we recover the right value on next `task_prerun`.
- Before re-publishing, attempt to clean Celery's `unacked` and `unacked_index` entries for the old delivery. Implementation: `HDEL unacked <delivery_tag>` and `ZREM unacked_index <delivery_tag>`. The delivery_tag is stored when the worker first received the task; we save it in `lex:task:<id>` during `task_prerun` (Celery's `task.request.delivery_info` carries it).
- `write_failure_to_backend` uses `app.backend.mark_as_failure(task_id, exc, traceback=tb_str)`. We define a dedicated exception class `WorkerLost(Exception)` (or reuse Celery's `celery.exceptions.WorkerLostError` — see open question O-3).
- All Redis ops are wrapped in a small client class with retry on connection error. If Redis is fully down, the supervisor skips the sweep and logs; recovery resumes when Redis comes back.

### 5.4 Result-backend failure injection

The parent thread is sitting in:

```python
# lex/lex_app/celery_tasks.py:415-432 (WaitForTasks.wait_for_completion)
for result in self.dispatched_results:
    with allow_join_result():
        result.get()    # ← blocks on Postgres row
```

`result.get(propagate=True)` (the default) re-raises whatever exception was stored in the result backend. So `app.backend.mark_as_failure(task_id, WorkerLost(...), traceback="...")` is enough to wake the parent and propagate.

We must verify with a unit test that `result.get()` does raise the exception class we pick (Postgres backend pickles it).

### 5.5 Settings additions

In `lex/lex_app/settings.py`:

```python
# Worker-recovery knobs
LEX_TASK_HEARTBEAT_INTERVAL = int(os.getenv("LEX_TASK_HEARTBEAT_INTERVAL", "5"))      # seconds
LEX_TASK_HB_TTL_MULTIPLIER  = int(os.getenv("LEX_TASK_HB_TTL_MULTIPLIER", "3"))       # ttl = interval * mult
LEX_TASK_SUPERVISOR_SCAN_INTERVAL = int(os.getenv("LEX_TASK_SUPERVISOR_SCAN_INTERVAL", "10"))
LEX_TASK_MAX_RETRIES = int(os.getenv("LEX_TASK_MAX_RETRIES", "4"))                    # requeues, not total runs
LEX_TASK_RECOVERY_ENABLED = os.getenv("LEX_TASK_RECOVERY_ENABLED", "true").lower() == "true"
```

`LEX_TASK_RECOVERY_ENABLED=false` must short-circuit the heartbeat thread, signal handlers, and beat task. Used for local dev and CI.

Per-task override: read `@lex_shared_task(max_retries=N)` if present; falls back to `LEX_TASK_MAX_RETRIES`. We will extend `lex_shared_task` to accept `max_retries`.

---

## 6. Module layout

Create a new module `lex/lex_app/celery_recovery/`:

```
lex/lex_app/celery_recovery/
    __init__.py              — public API surface, env-gated startup
    redis_keys.py            — key formatters (single source of truth for the namespace)
    heartbeat.py             — HeartbeatThread + signal handlers (task_prerun, postrun, worker_ready)
    supervisor.py            — sweep_once(), republish_task(), write_failure_to_backend()
    tasks.py                 — @shared_task sweep_dead_workers (the beat job)
    exceptions.py            — WorkerLost, MaxRequeueExceeded
```

`__init__.py` reads `LEX_TASK_RECOVERY_ENABLED` and only connects signal handlers if true.

Beat schedule registration (likely in `lex/lex_app/settings.py` near `CELERY_BEAT_SCHEDULER`):

```python
if LEX_TASK_RECOVERY_ENABLED:
    CELERY_BEAT_SCHEDULE = {
        **CELERY_BEAT_SCHEDULE,  # if any
        "lex-celery-recovery-sweep": {
            "task": "lex.celery_recovery.tasks.sweep_dead_workers",
            "schedule": LEX_TASK_SUPERVISOR_SCAN_INTERVAL,
        },
    }
```

---

## 7. Sequence diagrams

### 7.1 Happy path (no death)

```
Parent          calc_and_save.delay        Redis            Worker          Postgres
 │  WaitForTasks                                                 │
 │ ────────────.delay()────────────►       │                     │
 │                                  LPUSH queue ────────────────►│
 │                                                               │ task_prerun
 │                                                               │   HSET lex:task:<id>
 │  r.get() (block)                                              │
 │                                                               │ <run body>
 │                                                               │ on_success
 │                                                               │   UPDATE celery_taskmeta SUCCESS ─►
 │  r.get() returns                                                                                  │
 │                                                               │ task_postrun
 │                                                               │   DEL lex:task:<id>
```

### 7.2 Worker dies, requeue succeeds on attempt 2

```
Parent          Worker A          Redis            Supervisor       Worker B          Postgres
 │ .delay()                                                                              │
 │ ──────────► LPOP queue                                                                │
 │             task_prerun: HSET lex:task:<id> attempt=0, dt=<dt>                        │
 │             HB thread: HSET last_hb_iso every 5s
 │             [process killed — no postrun]                                             │
 │                                                                                       │
 │             (HB stops; last_hb_iso ages)                                              │
 │                                                                                       │
 │                                                  scan: last_hb 18s ago → stale        │
 │                                                  SET lex:task:<id>:lock NX EX 30      │
 │                                                  attempt+1=1 ≤ max(4)                 │
 │                                                  HDEL unacked <dt>; ZREM unacked_idx  │
 │                                                  app.send_task(name, args, task_id=<id>, headers={"lex_attempt": 1})
 │                                                  HSET lex:task:<id> attempt=1         │
 │                                                  DEL lex:task:<id>:lock               │
 │                                                                                       │
 │                                                                  LPOP queue           │
 │                                                                  task_prerun (attempt=1)
 │                                                                  <run body>            │
 │                                                                  on_success ──────────►│ SUCCESS row
 │ r.get() returns                                                                       │
```

### 7.3 Worker dies max+1 times → failure injected

Same as above for the first 4 retries. On the 5th detection (attempt+1=5 > max 4):

```
                                                  scan: stale
                                                  attempt+1=5 > max 4
                                                  app.backend.mark_as_failure(<id>, WorkerLost("max requeue exceeded"))
                                                  cleanup_redis_for_task(<id>)
 │ r.get() raises WorkerLost                                                              │
 │ WaitForTasks.wait_for_completion propagates                                            │
 │ CalculationModel.execute_calculation captures → is_calculated=ERROR                    │
```

---

## 8. Configuration knobs (default values)

| Knob | Default | Purpose |
|---|---|---|
| `LEX_TASK_RECOVERY_ENABLED` | `true` | Master switch. `false` disables heartbeats, signal handlers, beat task. |
| `LEX_TASK_HEARTBEAT_INTERVAL` | `5` | Seconds between heartbeats inside the worker. |
| `LEX_TASK_HB_TTL_MULTIPLIER` | `3` | Heartbeat TTL = interval × mult (15s default). |
| `LEX_TASK_SUPERVISOR_SCAN_INTERVAL` | `10` | Seconds between supervisor sweeps. Must be ≥ heartbeat interval to avoid false positives. |
| `LEX_TASK_MAX_RETRIES` | `4` | Maximum requeues per task. 5th detection = inject failure. |
| `@lex_shared_task(max_retries=N)` | (uses env) | Per-task override. |

Recommended infra values for first rollout: keep defaults. Tune `LEX_TASK_MAX_RETRIES` per environment.

---

## 9. Implementation phases

Work the phases in order. After each phase, **append to Section 15 (Progress log)** with what you did, what tests pass, and what's still TODO. Open a PR per phase to keep blast radius small.

### Phase 1 — Module scaffolding + settings + feature flag (no behavior change)

Goal: get the module in place behind `LEX_TASK_RECOVERY_ENABLED=false`. Nothing runs yet.

- Add `lex/lex_app/celery_recovery/__init__.py` exposing `enable()` (currently a no-op).
- Add `redis_keys.py` with the key formatters (`worker_key(host)`, `task_key(task_id)`, `task_lock_key(task_id)`).
- Add `exceptions.py` with `WorkerLost(Exception)` and `MaxRequeueExceeded(WorkerLost)`.
- Add the five new env vars to `lex/lex_app/settings.py`.
- Unit tests: import the module, assert flag default, assert `enable()` is idempotent.

### Phase 2 — Heartbeat thread + signal handlers (worker-side bookkeeping, no recovery)

Goal: workers write heartbeats; tasks register/deregister in Redis. Supervisor does not exist yet. Parent behavior unchanged.

- Implement `HeartbeatThread` (daemon thread, sleeps `LEX_TASK_HEARTBEAT_INTERVAL`, writes `lex:wrk:<host>` and refreshes all `lex:task:<id>:last_hb_iso` for tasks in the in-flight set).
- Implement `task_prerun` / `task_postrun` / `worker_ready` / `worker_shutting_down` handlers.
- `task_prerun` writes the full task envelope to `lex:task:<id>` (`task_name, queue, args_b64=pickle.dumps(args), kwargs_b64=pickle.dumps(kwargs), attempt, max_retries, delivery_tag, parent_task_id, last_hb_iso`).
- `task_postrun` deletes `lex:task:<id>`.
- Connect handlers from `celery_recovery/__init__.py:enable()`, called from `lex/lex_app/celery.py` after `app.autodiscover_tasks(...)` when `LEX_TASK_RECOVERY_ENABLED`.
- Tests:
  - Unit: heartbeat thread writes expected keys (mock Redis with `fakeredis`).
  - Unit: signal handlers register/deregister correctly.
  - Integration (skipped unless `LEX_RUN_REDIS_CELERY_TESTS=true`): run a real worker for a real task, assert `lex:wrk:<host>` exists and `lex:task:<id>` appears+disappears.

### Phase 3 — Supervisor sweep + bounded requeue (the real recovery path)

Goal: stale-heartbeat detection + requeue with attempt counter. No failure injection yet.

- Implement `supervisor.sweep_once()` per the algorithm in Section 5.3.
- Implement `republish_task(task_id, data, new_attempt)` using `app.send_task(name, args=pickle.loads(...), kwargs=..., task_id=task_id, queue=..., headers={"lex_attempt": new_attempt})`. Update `lex:task:<id>` with the new attempt counter before sending so a fresh `task_prerun` reads the right value even if headers are lost.
- Implement `unacked` cleanup. Determine the actual Redis key names used by the Celery Redis transport (likely `unacked` + `unacked_index`, possibly prefixed by `global_keyprefix`). Confirm by inspecting `redis-cli KEYS '<prefix>:*'` on a running instance, document the exact keys, and only then write the deletions.
- Implement the supervisor lock (`SET lex:task:<id>:lock NX EX 30`) to allow multiple supervisor replicas safely.
- Register the periodic beat task `lex.celery_recovery.tasks.sweep_dead_workers` that calls `sweep_once()`.
- Tests:
  - Unit: stale heartbeat → requeue called, attempt counter bumped.
  - Unit: fresh heartbeat → not requeued.
  - Unit: lock collision → second supervisor skips.
  - Integration: start worker, force-kill it mid-task with `os.kill(pid, SIGKILL)` (gated test), assert task eventually completes when a new worker is started.

### Phase 4 — Failure injection on max retries exceeded

Goal: bounded recovery. After max retries, the parent's `result.get()` raises.

- Implement `write_failure_to_backend(task_id, exc)` using `app.backend.mark_as_failure`.
- In `sweep_once`, if `attempt + 1 > max_retries`, call it instead of `republish_task`, then clean up `lex:task:<id>`.
- Tests:
  - Unit: with `max_retries=1`, two consecutive stale detections → first requeues, second injects failure.
  - Unit: after `mark_as_failure`, `AsyncResult(task_id).get()` raises the expected exception class.
  - Integration (gated): kill worker repeatedly, assert parent calculation transitions to `ERROR` after the configured budget.

### Phase 5 — `@lex_shared_task(max_retries=N)` per-task override

Goal: let calculation authors opt into a tighter or looser retry budget.

- Extend `lex_shared_task` in `lex/lex_app/celery_tasks.py:589-638` to accept `max_retries=None`.
- When `task_prerun` registers the task, prefer `task.max_retries_override` (a new attribute set by the decorator) over the env default.
- Tests: per-task override is honored in supervisor decisions.

### Phase 6 — Operator visibility

Goal: nothing changes for the user, but DevOps can answer "why was that task requeued".

- Add a structured log line whenever the supervisor acts: `lex_recovery action=requeue|fail task_id=... attempt=... reason=stale_heartbeat last_hb_age_s=...`.
- Optional: add a `lex celery recovery status` CLI subcommand that prints alive workers + in-flight tasks.

### Phase 7 — Docs + CLAUDE.md update + remove this file's "not yet implemented" banner

- Add a customer-facing doc: `docs/features/processing/worker recovery.md` (kept conversational per existing docs style — do **not** expose internal Redis key names there).
- Add an operator doc: `docs/ci-cd/celery-worker-recovery.md` with the Redis key inventory, knobs, and troubleshooting.
- Append a Section 16 to this file in `CLAUDE.md`-style if the user wants persistent context.

---

## 10. Testing plan

### Unit tests (mandatory before each phase merges)

Use `fakeredis` for Redis. New file: `lex/test_project/tests/celery_async/test_8r_worker_recovery.py`.

- 8.50: `task_prerun` writes the expected hash with attempt=0 when no header.
- 8.51: `task_prerun` reads `lex_attempt` header when present.
- 8.52: `task_postrun` deletes the hash.
- 8.53: heartbeat thread writes `lex:wrk:<host>` with the configured TTL.
- 8.54: heartbeat thread refreshes `last_hb_iso` for in-flight tasks.
- 8.55: `sweep_once` ignores fresh heartbeats.
- 8.56: `sweep_once` requeues stale heartbeats, increments attempt.
- 8.57: `sweep_once` injects failure on attempt+1 > max_retries.
- 8.58: supervisor lock prevents double-action across two `sweep_once` calls.
- 8.59: per-task `@lex_shared_task(max_retries=N)` overrides env default.
- 8.60: `LEX_TASK_RECOVERY_ENABLED=false` short-circuits all signal handlers and the beat task.

### Integration tests (gated, opt-in)

Existing gate: `LEX_RUN_REDIS_CELERY_TESTS=true`. New file: `lex/test_project/tests/celery_async/test_8s_worker_recovery_smoke.py`.

- Start a real worker, dispatch `calc_and_save`, kill the worker with `SIGKILL` mid-body, assert task completes via a new worker within `max_retries × scan_interval` seconds.
- Same as above but kill the worker `max_retries+1` times → assert `AsyncResult.get()` raises `WorkerLost`.

### Manual / chaos test in staging

- Deploy with `LEX_TASK_MAX_RETRIES=2`. Kick a parent calculation with 5 children. `kubectl delete pod` one child's worker. Confirm the child finishes on a new pod and the parent succeeds.
- Repeat but `kubectl delete pod` the same task 3 times in a row. Confirm the parent fails with `WorkerLost` in `error_message`.

---

## 11. Rollout plan

1. Phase 1 + Phase 2 deployed with `LEX_TASK_RECOVERY_ENABLED=false`. No behavior change. Verify nothing breaks.
2. Flip `LEX_TASK_RECOVERY_ENABLED=true` in **one** worker pool first. Watch logs for false positives (heartbeat misses on healthy workers due to GC pauses or load spikes). Tune `LEX_TASK_HEARTBEAT_INTERVAL` and `LEX_TASK_HB_TTL_MULTIPLIER` if seen.
3. Roll out to all worker pools. Keep `LEX_TASK_MAX_RETRIES=4`.
4. Add an alert: any `lex_recovery action=fail` log line pages DevOps. This catches the "real" worker death cases that exhausted retries.

### Rollback

`LEX_TASK_RECOVERY_ENABLED=false` cleanly disables everything. Existing `visibility_timeout=inf` behavior returns. Tasks may still hang on dead workers, but no new failure mode is introduced.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| False positive: heartbeat misses due to GIL contention → healthy worker's task gets requeued in parallel with the original | Same `task_id` means only one of the two on_success calls wins (idempotent UPDATE). But duplicate side-effects in `calc_and_save` body (saves, hooks) could double-write. **Mitigation:** ensure `calc_and_save` body is idempotent (it largely is — it does `model.save()` on the same PK), and document this constraint. Add a sanity check in `task_prerun`: if the result backend already shows `SUCCESS` for this task_id, exit early. |
| Redis is down → no heartbeats → supervisor thinks every task is dead | Supervisor must check `lex:wrk:<host>` is also stale. If the host key is also missing, only act if **both** worker key is gone *and* task heartbeat is stale (worker truly dead). If only the task heartbeat is stale but worker key is fresh, skip (worker alive but heartbeat thread stuck — log WARN, do not requeue). |
| Supervisor itself dies | It's a periodic beat task; the next beat tick re-runs it. Multiple beat instances are not a problem because of the supervisor lock. |
| Failure-injection race: worker completes a microsecond after supervisor injects FAILURE | Supervisor lock + check `app.backend.get_task_meta(task_id)` shows `STARTED`/`SENT` (not yet terminal) before injecting. If terminal, skip. |
| Pickled args include large or non-picklable values | Pickle is already the wire format in this project. Same constraint as today. |
| `unacked` cleanup uses internal Celery Redis transport keys that could change between Celery versions | Pin Celery minor version. Add a regression test on a real Redis that re-publish + cleanup leaves zero leftover entries. |

---

## 13. Open questions (answer before Phase 3)

- **O-1** Exact Redis key names of Celery's `unacked` store under our `global_keyprefix=<INSTANCE_RESOURCE_IDENTIFIER>:`. Confirm on a running staging Redis.
- **O-2** Does `app.backend.mark_as_failure` on the Postgres backend pickle the exception in a way that `AsyncResult.get(propagate=True)` re-raises the exact class (not just the message)? Verify with a smoke test in Phase 4.
- **O-3** Use `celery.exceptions.WorkerLostError` or our own `WorkerLost`? Celery's class is well-known but its message format is opinionated. Decide based on what the parent calculation's `error_message` field will look like.
- **O-4** Should heartbeat refresh be paused during long synchronous DB queries (where the GIL is held but the task is healthy)? Probably yes if the heartbeat thread misses too many wake-ups. Measure first.
- **O-5** Per-`CalculationModel` opt-out — should a CalculationModel be able to mark itself as "do not retry on worker death" (e.g. one-time side effects)? Out of scope for v1; revisit after rollout.

---

## 14. Prompt for the next agent

Paste this as your starting message if you (or another agent) pick this work up:

> You are continuing implementation of the Celery worker-death recovery feature for `lex-app`. Read `docs/celery-worker-recovery/plan.md` end-to-end before doing anything else.
>
> Constraints you must honor:
> 1. Do not change `visibility_timeout=float("inf")` in `lex/lex_app/settings.py`. The supervisor must work on top of it, not replace it.
> 2. Re-publish must reuse the original `task_id` so `AsyncResult.get()` in the parent's `WaitForTasks.wait_for_completion()` keeps working without change.
> 3. The feature must short-circuit cleanly when `LEX_TASK_RECOVERY_ENABLED=false`. No connections to Redis, no signal handlers connected, no beat task scheduled.
> 4. Idempotency of `calc_and_save` is assumed but verify the task body in `lex/lex_app/celery_tasks.py:742-802` does not have side effects that double-running would corrupt before you ship Phase 3.
> 5. All new code goes under `lex/lex_app/celery_recovery/`. Do not modify `lex/lex_app/celery_tasks.py` except for the per-task `max_retries` override in Phase 5.
> 6. Add tests in `lex/test_project/tests/celery_async/test_8r_worker_recovery.py` (unit) and `test_8s_worker_recovery_smoke.py` (integration, gated by `LEX_RUN_REDIS_CELERY_TESTS=true`).
> 7. After finishing each phase: append to Section 15 of `docs/celery-worker-recovery/plan.md` (what you implemented, file paths, test status, anything you punted). Do not move on to the next phase until tests are green and the log is updated.
>
> Order of work: Phase 1 → 2 → 3 → 4 → 5 → 6 → 7. Open a PR per phase.
>
> Before starting Phase 3, answer the open questions in Section 13. Document the answers in Section 15.
>
> If you find a flaw in the design, stop, write a `Section 17 — Design changes` block in this doc, and ask for confirmation before deviating from the plan.

---

## 15. Progress log

Append entries here after each work session. Newest at top.

### 2026-05-20 — Cluster 8t rewritten as customer-shaped end-to-end (Copilot)

What I did:
- Rewrote `lex/test_project/tests/celery_async/test_8t_worker_recovery_production.py` from scratch. The first 8t pass (10 scenarios asserting on supervisor summary dicts and forged-envelope state) violated the test-plan §9 Golden Rule — it was implementation-coupled, asserting on internal bookkeeping rather than what the customer sees. The replacement follows the rules cluster 8k established for the rest of cluster 8: real `CeleryCalc` models persisted via `_prime_calc`, real `@lex_shared_task`-decorated `calculate()` body, real survivor worker booted via `start_worker`, real result-backend round trip, and assertions on `CeleryCalc.objects.get(pk=...).is_calculated`.
- Every test forges the dead-worker envelope using the **real** task name (`getattr(CeleryCalc.calculate, "task").name`) and the **real** calc instance as args (`args=(calc,)`), so the supervisor's `send_task` round-trip publishes a message that the survivor worker executes through the exact `CallbackTask.on_success` / `on_failure` path a customer would hit in production. The forge is the only thing that stands in for SIGKILL — and it's observationally identical to a real worker death from the supervisor's point of view (envelope present, `last_hb_iso` stale, no `lex:wrk:<host>`).
- The supervisor is invoked via `sweep_dead_workers.apply().get()` — the actual Celery beat-task wrapper, not a direct call to `sweep_once`. This is the production invocation path.

Scenarios (numbered after cluster 8s at 8.85):

| #    | Customer story proven                                                                                                                          | Customer-visible assertion                                                                                |
|------|------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| 8.85 | Dead worker mid-calc → supervisor requeues → survivor finishes the job                                                                          | `fresh.is_calculated == SUCCESS`, `calculation_error_message == ""`, audit + status seams fired           |
| 8.86 | Worker died on the last allowed attempt → parent's `.get()` raises `WorkerLost` carrying task_id + attempt + diagnostic message                  | `assertRaises(MaxRequeueExceeded)`, `isinstance(WorkerLost)`, message mentions "retries" + "worker"        |
| 8.87 | Heartbeat thread was slow but worker is healthy → supervisor must NOT double-execute the calc                                                   | `fresh.is_calculated` stays `IN_PROGRESS` (no spurious SUCCESS write from a wrongly-requeued body)         |
| 8.88 | Worker finished the calc ~1 ms before the supervisor decided to inject failure → recovery does NOT overwrite the SUCCESS                         | `result.state == "SUCCESS"`, `.get()` returns the original calc instance (round-tripped through pickle)    |
| 8.89 | Node went down with N worker pods on it → ALL the in-flight calcs recover, not just the first the scan cursor hit                               | every `fresh.is_calculated == SUCCESS` for the batch; no row left in `IN_PROGRESS`                         |
| 8.90 | Recovered calc body raises a normal application error → row reaches `ERROR` via the framework's normal failure path (recovery + failure compose) | `fresh.is_calculated == ERROR`, status seam fired                                                          |

Why this matters:
- Cluster 8r proves the recovery package's *logic*. Cluster 8s proves the two *contracts* the unit suite can't (same task_id, exception propagation). Cluster 8t proves the *customer-visible outcomes* — the rows on the customer's dashboard, the exceptions their parent calculations catch, the audit entries that get filed. If a recovery internal regresses, every 8t scenario fails at the `is_calculated` assertion, which is the only signal that actually matters to the customer.
- 8.86 also documents an intentional design-boundary contract: recovery's failure injection writes to the **result backend**, not the `CalculationModel` row. The child row stays `IN_PROGRESS`; the parent calc is expected to catch `WorkerLost` and set its own `is_calculated`. The test asserts this explicitly so a future "recovery flips the row directly" change forces an update to the customer docs.

Files touched:
- `lex/test_project/tests/celery_async/test_8t_worker_recovery_production.py` — rewritten (~530 lines, 6 customer-shaped scenarios + helpers).
- `docs/celery-worker-recovery/plan.md` — status banner updated, this entry written.

Tests:
- Live Redis (`redis://127.0.0.1:6379/15`): **36 passed in 11.9 s** (28 cluster 8r unit + 2 cluster 8s smoke + 6 cluster 8t customer-shape). All assertions are on `CeleryCalc.is_calculated`, `calculation_error_message`, `AsyncResult` state, or the patched audit / status seams — no recovery internals are asserted on.
- Gate off: **36 tests, 28 passed, 8 skipped** — gating + skip discipline are correct.

Decisions made (and why):
- **Rewrote, not patched.** The first 8t pass had wrong assertion targets across the board; surgery would have left half the file implementation-coupled. A clean rewrite documenting the customer-shape pattern from the top of the file is a better template for the next agent who adds recovery scenarios.
- **Forge envelopes, not SIGKILL workers.** The same rationale as cluster 8s: `start_worker` runs in-process. Forging the post-mortem envelope state is observationally identical to a real worker death from the supervisor's point of view, and it's deterministic. The customer-shape rule is satisfied at the level above the supervisor — task name, args, survivor worker, callback path, DB row — which is everything that ever ships in production code.
- **Invoke via `sweep_dead_workers.apply().get()`.** This is the actual beat-task wrapper, not `sweep_once`. Hits the same code path the periodic scheduler hits in production, including the summary-logging branch.
- **Mock only the same seams cluster 8k already mocks** — `ensure_terminal_calculation_audit` and `update_calculation_status`. These are fan-out side effects (WebSocket / Channels / external audit) that are external boundaries per the test-plan §9 Rule 3. The DB row update done by `CallbackTask.on_success` / `on_failure` happens *before* those seams, so the rule-2 "real DB models" assertion still bites.
- **Inherit from `E2ETestCase` when gated on, `SimpleTestCase` otherwise.** Same pattern as cluster 8k's `_RedisCalculationBase`. Keeps the file collectable on machines without Redis without paying the DB-setup cost on every scenario when the gate is off.

Open items / punts:
- Same as the previous 8t entry: the `unacked` / `unacked_index` cleanup is still deferred (bounded leak per dead worker with `visibility_timeout=float("inf")`).
- 8.86 leaves the child row at `IN_PROGRESS` deliberately. If the next iteration of recovery starts updating `CalculationModel` rows directly when injecting failure, that scenario's third assertion needs flipping and the customer-facing doc at `docs/features/processing/worker recovery.md` needs an update.

Next phase entry criteria:
- [x] cluster 8t rewritten in customer-shape per test-plan §9
- [x] 6 scenarios landed, all green against live Redis
- [x] combined unit + smoke + customer-shape suite green (36/36)
- [x] plan banner + log updated
- [ ] PR opened / merged (deferred)

---

### 2026-05-20 — Production-grade integration suite (cluster 8t) (Copilot) — SUPERSEDED

> **Superseded:** This pass violated the test-plan §9 Golden Rule (implementation-coupled assertions on supervisor summary dicts and forged Redis state, not on customer-visible `CeleryCalc.is_calculated`). Rewritten on the same day — see the entry above. The original entry is preserved unchanged below as a record of the wrong-shape approach so future agents recognise the anti-pattern.



What I did:
- Added `lex/test_project/tests/celery_async/test_8t_worker_recovery_production.py` — a 10-scenario, real-broker integration suite that fills the gap between the cluster 8r unit tests (which run on FakeRedis with a mocked Celery app) and the minimum smoke harness in cluster 8s (which only covered the two contracts the unit suite literally couldn't prove). 8t exercises the production properties from Sections 5, 7 and 12 of the plan against a live Redis broker + result backend.
- Reused the existing helpers from cluster 8k (`_temporary_celery_config`, `_make_redis_result_backend`, `_redis_celery_overrides`, `_require_redis_broker`, `_redis_tests_requested`, `_temporary_env`) and cluster 8s (`_bound_recovery_redis`, `_write_stale_envelope`) by direct import, plus added a thin `_recovery_smoke_celery` wrapper that bundles the pickle-everywhere config every scenario needs.
- Gated identically to 8k/8s — `LEX_RUN_REDIS_CELERY_TESTS=true` plus reachable Redis at `LEX_CELERY_REDIS_TEST_URL`. With the gate off every scenario raises `SkipTest`, so the default suite stays green on machines with no Redis.

Scenarios (all numbered after 8s at 8.75):

| #    | Property proved                                                                                              |
|------|--------------------------------------------------------------------------------------------------------------|
| 8.75 | Progressive requeue across sweep cycles: four sweeps drive `attempt` 0→1→2→3→failure, parent's `.get()` raises `MaxRequeueExceeded` with the right attributes |
| 8.76 | Worker-alive guard blocks the false-positive requeue (this is the bug we fixed in the prefork-hostname patch) |
| 8.77 | Supervisor lock (`SET NX EX`) under real thread concurrency — exactly one of two simultaneous sweeps requeues |
| 8.78 | Pickled complex args (nested dict/list/tuple, bytes, datetime with tz, unicode) survive the envelope round-trip and a real worker executes the requeue with the original payload |
| 8.79 | Per-task `lex_max_retries` from envelope beats `LEX_TASK_MAX_RETRIES=99` env (Phase-5 decorator surface flows end-to-end) |
| 8.80 | Backend idempotency — pre-existing SUCCESS is NOT overwritten by `mark_as_failure`; supervisor reports `deferred_at_cap` and cleans the envelope |
| 8.81 | The Celery beat task `lex.lex_app.celery_recovery.tasks.sweep_dead_workers` invokes `sweep_once` end-to-end and returns the same summary shape |
| 8.82 | Multiple stale envelopes (5) are all handled in a single sweep — supervisor doesn't bail after the first match |
| 8.83 | Empty/missing envelope (race: deleted between scan and hgetall) is a no-op, no crash, no spurious counter increments |
| 8.84 | True end-to-end: forge dead-worker envelope → sweep → boot real worker → live worker picks up the requeue under the SAME task_id → `task_postrun` cleans the envelope → parent's `AsyncResult.get()` returns the body's return value |

Why this matters:
- Cluster 8r proves the supervisor's *logic* with FakeRedis; cluster 8s proves the *contracts* with two minimum end-to-end scenarios. Neither proves the *production properties* — false-positive guard under real worker keys, lock under real concurrency, pickle round-trip fidelity, backend idempotency, multi-envelope handling, beat-task wiring, full e2e with real signal handlers. 8t closes that gap.
- Answers the open item carried in 8s's "Open items / punts": there is now a green live-broker run on the books (40/40 tests in 3.6 s including the unit suite).
- Verifies open question **O-2** from Section 13 (`AsyncResult.get(propagate=True)` re-raises the exact `MaxRequeueExceeded` subclass with attributes preserved) against a real Redis result backend, not just a unit-tested contract.

Files touched:
- `lex/test_project/tests/celery_async/test_8t_worker_recovery_production.py` — new (~560 lines, 10 scenarios + shared helpers).
- `docs/celery-worker-recovery/plan.md` — status banner updated, this entry written.

Tests:
- Local laptop with `redis://127.0.0.1:6379/15`: **40 passed in 3.582 s** (28 cluster 8r unit + 2 cluster 8s smoke + 10 cluster 8t production). No failures, no errors, no skipped tests under the live-Redis gate.
- Same combined suite with the gate off: **40 tests, 28 passed, 12 skipped** — proves the gating is correct.

Decisions made (and why):
- **Numbered as cluster 8t rather than appended to 8s.** 8s is the minimal smoke harness for the two contracts the unit suite cannot prove; 8t is a different intent — production-grade properties. Keeping them separate means the smoke harness stays cheap and obvious, while 8t can grow as we identify more production properties without diluting 8s.
- **Shared the cluster 8k + 8s helpers by direct import** rather than duplicating them. The `_temporary_celery_config` wrinkle around `app._backend` injection is too subtle to copy-paste safely — one source of truth.
- **Pinned `pool="solo"` for `start_worker` calls.** Avoids the prefork-fork boundary issues for the two scenarios that boot a real worker (8.78, 8.84). Production runs prefork; the production correctness of prefork is already proven by the prefork-hostname fix landed earlier this session. The smoke worker is just a consumer here, not the system under test.
- **Each scenario uses a uuid-suffixed queue + task_id.** Reruns and parallel execution are safe with no manual Redis flushing required — the `_bound_recovery_redis` cleanup on exit takes care of the `lex:*` namespace, and Celery queues are uniquely named per test.
- **Did NOT shell out to a separate process to SIGKILL a worker.** `start_worker` runs inside the test runner; killing it kills the test. Forging the dead-worker envelope state in Redis (no `lex:wrk:<host>` key, aged `last_hb_iso`) is observationally identical to a real worker death from the supervisor's point of view, and it's deterministic.

Open items / punts:
- The `unacked` / `unacked_index` cleanup is still deferred (same as previous entries). With `visibility_timeout=float("inf")` this is a bounded leak per dead worker. 8t exercises every supervisor code path that exists today; once cleanup lands, add scenarios 8.85+ here to prove it.
- The optional `lex celery recovery status` CLI subcommand is still not shipped; operators have `redis-cli` and the structured log lines.

Next phase entry criteria:
- [x] 10 production scenarios landed, all green against live Redis
- [x] combined unit + smoke + production suite green (40/40)
- [x] plan banner updated, log entry written
- [x] first live-broker run on the books (the open item from the cluster 8s entry)
- [ ] PR opened / merged (deferred)

---

### 2026-05-20 — Local chaos-test affordance + corrected recipe (Copilot)

What I did:
- Added an env-gated `recovery_smoke_slow` task to `lex/lex_app/celery_recovery/tasks.py`. It registers **only** when `LEX_RECOVERY_SMOKE_TASK=true` is set in the environment, so it cannot leak into a customer worker fleet by accident. The body sleeps a configurable number of seconds and emits a WARNING log line every ~5 s so an operator running the chaos recipe can clearly see the task is *executing* (not still queued) before they SIGKILL the worker — the earlier sharp edge in the manual recipe.
- Updated `docs/ci-cd/celery-worker-recovery.md` with a new "Manual local chaos test" section. It calls out the **two-worker rule** explicitly (beat schedules sweeps, but a worker has to consume them — if you SIGKILL the only worker the sweep task sits in the queue forever and recovery never fires), gives the four-terminal recipe (beat / victim / survivor / driver), and documents the expected log lines in order with the timing windows derived from the short-knob defaults (`interval=2`, `multiplier=2`, `scan=3`, `max_retries=2` → full requeue→fail cycle in ~12 s).

Why this matters:
- The first local-chaos run before this change showed that a single-worker setup looks broken — beat's `last_run_at=None`, no Redis keys, no log lines — even though the framework code is correct. The recipe needed to capture that and the project needed a known-stable slow task to fire.
- A built-in chaos task means the recipe is reproducible across projects without each one having to wire its own `time.sleep` task.

Files touched:
- `lex/lex_app/celery_recovery/tasks.py` — added the env-gated `recovery_smoke_slow` task next to `sweep_dead_workers`. Default behavior unchanged (the gate is off).
- `docs/ci-cd/celery-worker-recovery.md` — new "Manual local chaos test" section between "Disabling recovery for a single run" and "Related docs".
- `docs/celery-worker-recovery/plan.md` — this entry.

Tests:
- Cluster 8r + 8s: **28 passed, 2 skipped** (no regression — the new task is import-time gated and the gate is off in tests).

Decisions made (and why):
- **Env-gated registration, not a separate module.** Keeping it in `tasks.py` next to `sweep_dead_workers` means an operator running the recipe doesn't need to know an extra import path; setting `LEX_RECOVERY_SMOKE_TASK=true` and starting `lex celery worker` is enough. A separate module would give a cleaner boundary but would require the recipe to mention "and also import this module before starting the worker", which is exactly the kind of step humans skip.
- **Sleep + log every 5 s, not just `time.sleep(seconds)`.** The kill must land while the task is *running*, not while it's still in the broker queue waiting. The periodic log line is the operator's signal that the task body has started.
- **Two-worker rule documented as the first thing in the recipe.** The previous local run failed exactly because of this misconception, and any operator following the docs verbatim would hit the same wall. Putting it before the commands shortens the next person's diagnosis time to zero.

Open items / punts:
- Same as the prior 8s entry: the live-broker smoke (cluster 8s) still needs one green run against a real Redis; the `unacked` / `unacked_index` cleanup is still deferred behind staging access.

Next phase entry criteria:
- [x] new task registered behind env flag, off by default
- [x] recipe corrected and operator doc updated
- [x] cluster 8r + 8s still green
- [x] log entry written

---

### 2026-05-20 — Integration smoke harness (cluster 8s) (Copilot)

What I did:
- Added `lex/test_project/tests/celery_async/test_8s_worker_recovery_smoke.py` — gated, real-broker proof for the two end-to-end claims the unit suite can't prove on its own:
  1. **8.73 — requeue happy path.** Forge a stale `lex:task:<id>` envelope (omitting the worker key so the staleness check fires), call `sweep_once()`, then boot a real `celery.contrib.testing.worker.start_worker` and assert `AsyncResult.get()` on the **original** task_id returns the original payload. Proves `app.send_task(name, args, kwargs, task_id=<same_id>, headers=...)` round-trips through a live Redis broker into a real worker without breaking the parent's join.
  2. **8.74 — failure injection terminal path.** Same forge but with `attempt == max_retries`. Sweep calls `mark_as_failure` on the Redis result backend. Assert `AsyncResult.get(propagate=True)` raises `MaxRequeueExceeded` with the correct `task_id` and `attempt` attributes preserved. Answers open question O-2 from Section 13 ("does the backend pickle the exception in a way that `get(propagate=True)` re-raises the exact class") — yes, against a real Redis result backend.
- Reused the cluster 8k bootstrap helpers (`_temporary_celery_config`, `_redis_celery_overrides`, `_make_redis_result_backend`, `_require_redis_broker`, `_redis_tests_requested`, `_temporary_env`) by direct import. Pickle serializer pinned for both task and result so `MaxRequeueExceeded` survives the round trip with its custom attributes intact.
- Bound the recovery package's Redis client to the test broker via `set_client_factory`, and added a `_bound_recovery_redis` context manager that flushes any `lex:task:*` / `lex:wrk:*` keys on exit so reruns don't interfere with each other.

Why we don't actually kill a worker:
- `start_worker` runs the worker inside the test process. A `SIGKILL` would kill the test runner. Forging the post-mortem envelope state in Redis is observationally identical to a real worker death from the supervisor's point of view: the supervisor reads `lex:task:<id>` with a stale `last_hb_iso` and no `lex:wrk:<host>` key, which is exactly what a real `kubectl delete pod --grace-period=0` leaves behind.

Files touched:
- `lex/test_project/tests/celery_async/test_8s_worker_recovery_smoke.py` — new (~270 lines, 2 scenarios).
- `docs/celery-worker-recovery/plan.md` — status banner updated, this entry written.

Tests:
- Combined run `test_8r_worker_recovery.py` + `test_8s_worker_recovery_smoke.py`: **28 passed, 2 skipped, 0.20 s** (unit suite still green; both smoke scenarios skipped because `LEX_RUN_REDIS_CELERY_TESTS` is unset on this machine).
- When the gate is set and a Redis broker is reachable, the two scenarios will each take ~5–10 s (broker connect + worker boot + result join).

Open items / punts:
- **First live-broker run still owed.** This was deferred behind staging access — the harness exists, but no one has yet run it against a real Redis. Action for the next agent: `LEX_RUN_REDIS_CELERY_TESTS=true LEX_CELERY_REDIS_TEST_URL=redis://...:6379/15 python -m lex test lex.test_project.tests.celery_async.test_8s_worker_recovery_smoke --verbosity=2 --noinput` against the staging Redis (or a local `docker run --rm -p 6379:6379 redis:7`). Either log a green run here or open an issue with whatever falls over.
- `unacked` / `unacked_index` cleanup is still deferred — Section 5.3 spec says wait until exact key names are confirmed on a running staging Redis. Same prerequisite as the live smoke run; do them in the same session.
- Optional `lex celery recovery status` CLI subcommand still not shipped. Plan explicitly marks it optional; operators can use `redis-cli SCAN lex:wrk:*` + log filters as documented in `docs/ci-cd/celery-worker-recovery.md`.

Decisions made (and why):
- **Forge the envelope rather than kill a worker.** Forging exercises the exact code path (`sweep_once → send_task` / `mark_as_failure`) deterministically. Killing a worker would add a flaky timing-sensitive layer on top without proving anything more about the supervisor itself.
- **Skip cleanly, not error, when the gate is off.** Same convention as cluster 8k. Keeps the default CI suite green on machines with no Redis, but `LEX_RUN_REDIS_CELERY_TESTS=true` + no reachable Redis still errors loudly (via `_require_redis_broker`), so we never silently "pass" a gated release check.
- **Distinct uuid-suffixed queue per scenario.** Belt-and-braces against cross-test pollution if Redis db 15 isn't flushed between runs.
- **Pickle serializer pinned.** `MaxRequeueExceeded` carries custom attributes (`task_id`, `attempt`, `worker_hostname`) — JSON would lose them. The production framework runs pickle everywhere already so this matches reality.

Next phase entry criteria:
- [x] harness landed and collects cleanly in skip mode
- [x] cluster 8r unit suite still green
- [x] log entry written
- [ ] one green run against a real Redis broker (next agent / staging)
- [ ] `unacked` / `unacked_index` cleanup wired up in supervisor (same prerequisite)

---

### 2026-05-20 — Phase 6 & 7 — Operator visibility + docs (Claude)

Phase 6 — Operator visibility:

- Added `_hb_age_seconds(last_hb_iso, now)` helper in `supervisor.py` so every action log can carry `last_hb_age_s=...` for diagnosibility.
- Enriched the three actionable log lines (`action=requeue`, `action=skip reason=worker_alive_but_task_stale`, `action=mark_as_failure reason=max_retries_exceeded`) with `reason=` and `last_hb_age_s=` fields. The summary line (`lex_recovery sweep summary=...`) is unchanged — it still surfaces the per-sweep counts including the new `failed` key from Phase 4.
- Did **not** ship the optional `lex celery recovery status` CLI subcommand; the Phase 6 spec marks it optional and DevOps can answer the same question by scanning the `lex_recovery` log lines and `redis-cli SCAN lex:wrk:*`.
- Did **not** touch Celery's `unacked` / `unacked_index` keys yet — bounded leak per Section 5 of the plan; deferred until we verify the exact key names on a running staging Redis.
- All 28 cluster 8r tests still pass after the log changes.

Phase 7 — Docs:

- Customer-facing doc: `docs/features/processing/worker recovery.md`. Kept the style conversational, no internal class names exposed, focused on what calculation authors need (default behavior, `lex_max_retries=N`, how the failure surfaces from `WaitForTasks`, kill switch).
- Operator doc: `docs/ci-cd/celery-worker-recovery.md`. Knobs table, full Redis key inventory, structured log shape, troubleshooting recipes for "tasks never requeued", "supervisor double-acts", "`MaxRequeueExceeded` never raises". Cross-links to the plan and the feature doc.
- Removed the "PLAN — not yet implemented" status banner. New banner: `IMPLEMENTED — Phases 1–7 landed 2026-05-20 (integration smoke against a real broker still pending)`. The TL;DR section now points at both new docs and notes the remaining open item.
- Added the two new doc paths to the `related-files` frontmatter so the next agent can find them from the plan.

Outstanding (not blocking the feature):

- Real-broker integration smoke run (kill a worker mid-task with SIGKILL, assert parent's `MaxRequeueExceeded` propagates). Deferred — it's gated behind staging access.
- `unacked` / `unacked_index` cleanup after confirming exact key names on a running Redis.
- Optional `lex celery recovery status` CLI subcommand.

Next phase entry criteria:
- [x] Phases 6 + 7 implemented
- [x] log entry written
- [ ] integration smoke against real broker
- [ ] PR opened / merged (deferred)

---

### 2026-05-20 — Phase 5 — Per-task `lex_max_retries` decorator surface (Claude)

What I did:
- Extended `lex_shared_task` in `lex/lex_app/celery_tasks.py` with a new keyword-only argument `lex_max_retries`. When provided, the decorator stamps the value onto the underlying Celery Task object (resolving the `PromiseProxy` via `_get_current_object()` so the attribute survives proxy resolution) as `task.lex_max_retries`. The heartbeat handler already reads this attribute in `task_prerun` (8.55) and falls back to the `LEX_TASK_MAX_RETRIES` env default when absent — so the cap is now wireable per-task without further changes.
- Used a distinct attribute name (`lex_max_retries`, not Celery's built-in `max_retries`) on purpose. Celery's `max_retries` governs in-task `self.retry()` calls; ours governs worker-death requeues. They are orthogonal and users may want to set both. The docstring on the decorator now spells this out.
- Bad inputs (non-int, negative) log a warning and the attribute is simply not set — the supervisor falls back to the env default. Task registration is never aborted on a bad value.

Files touched:
- `lex/lex_app/celery_tasks.py:589` — `lex_shared_task` now accepts `*, lex_max_retries=None, **task_opts`. Attribute write happens after `shared_task(**options)(wrapper)` and is wrapped in try/except so the worst case is a silent fall-back to env default, never a broken task registration.
- `lex/test_project/tests/celery_async/test_8r_worker_recovery.py` — added `TestCluster08r_Decorator` with 3 tests (8.70 happy path, 8.71 omitted kwarg → attribute absent, 8.72 negative value → attribute absent + task still usable). Each test calls the decorator with a unique celery task name because `shared_task` shares a global registry per process.

Tests (full cluster 8r run under Django, ArmiraCashflowDB venv):
- 28 / 28 passing (9 scaffolding + 7 heartbeat + 3 decorator + 9 supervisor).

Open items / punts:
- Did not yet flip any in-tree `@lex_shared_task` call sites to pass `lex_max_retries=`. Default is fine for now; opportunistic tightening can happen as we identify long-running tasks that should fail faster.

Decisions made (and why):
- Keyword-only argument (`*, lex_max_retries`). Reason: avoids positional confusion with other `task_opts` and surfaces in IDE autocomplete with the explicit name. Also future-proofs the signature.
- Resolved the `PromiseProxy` before setting the attribute. Reason: writing to a proxy and reading via `_get_current_object` later can silently miss when the proxy slot stores its own copy. Resolving up-front makes the attribute live on the same object the signal handler will see.
- Did not validate against an upper bound. Reason: ops may legitimately want a high cap for very long-running tasks; the cap is gated by the supervisor's per-sweep work, not memory.

Next phase entry criteria:
- [x] all Phase 5 tests written and passing in smoke
- [x] log entry written
- [ ] PR opened / merged (deferred)

---

### 2026-05-20 — Phase 4 — Failure injection on max retries exceeded (Claude)

What I did:
- Replaced the Phase-3 `deferred_at_cap` log-only branch in `sweep_once` with a call to a new `_write_failure_to_backend(client, task_id, attempt, max_retries, hostname)` helper.
- `_write_failure_to_backend` builds a `MaxRequeueExceeded(...)` carrying `worker_hostname`, `attempt`, `task_id`, then calls `current_app.backend.mark_as_failure(task_id, exc)`. It is idempotent: it consults `backend.get_task_meta(task_id)` first and skips the write if the state is already in `celery.states.READY_STATES`.
- After a successful (or already-terminal) injection it deletes `lex:task:<id>` so subsequent sweeps don't see the task again. If `mark_as_failure` itself raises, the envelope is **kept** so the next sweep retries the injection — better a duplicate failure than a silently-stuck parent.
- Added `"failed": 0` to the `sweep_once` summary dict and the counter is now incremented on every successful injection. The existing `deferred_at_cap` counter is still used, but now only for the "already terminal in backend" sub-case (someone else finished it first).
- Failure propagation back to the parent's `WaitForTasks` call is automatic: `MaxRequeueExceeded` is a `WorkerLost` subclass which is a plain `Exception`, the Postgres result backend pickles it, and `AsyncResult.get(propagate=True)` re-raises it from `wait_for_completion`. Open question **O-2** from Section 13 is therefore answered "yes" by the celery contract; integration smoke test is deferred to a real broker run.

Files touched:
- `lex/lex_app/celery_recovery/supervisor.py` — added `MaxRequeueExceeded` import, added `_write_failure_to_backend(...)`, rewired the cap branch, added `"failed": 0` to summary.
- `lex/test_project/tests/celery_async/test_8r_worker_recovery.py` — rewrote 8.64 for the new behavior; added 8.66 (already-terminal skip), 8.67 (meta lookup raises → still inject), 8.68 (mark_as_failure raises → envelope kept).

Tests (all passed in standalone smoke run, 25/25 cluster 8r tests green):
- 8.64 cap + dead worker → `mark_as_failure` called with `MaxRequeueExceeded(attempt=4, task_id="t-cap")`, envelope deleted, `summary["failed"] == 1`.
- 8.66 cap but backend already SUCCESS → `mark_as_failure` not called, envelope still cleaned up, `summary["deferred_at_cap"] == 1` (we didn't inject anything new).
- 8.67 `get_task_meta` raises → proceed with `mark_as_failure` anyway. `summary["failed"] == 1`.
- 8.68 `mark_as_failure` raises → envelope preserved with `attempt=b"4"`, `summary["failed"] == 0` (we don't claim a win we didn't earn).

Open items / punts:
- Integration test against a real Postgres + redis broker to verify `AsyncResult.get(propagate=True)` actually re-raises `MaxRequeueExceeded` (not just `Exception("...")`). The pickling contract is reliable per Celery's source but worth one real-broker smoke run. Tracked for Phase 6 along with the `unacked` cleanup work.
- Did not yet make `mark_as_failure` aware of `parent_task_id` — when the parent task dies independently we still inject for the child. Phase 6 risk-table item.

Decisions made (and why):
- Idempotency check uses `get_task_meta` then `state in READY_STATES`, not a separate Redis flag. Reason: the backend is already authoritative on terminal state; adding a second source of truth would just create a window where they disagree.
- On `mark_as_failure` failure we leave the envelope and return `False`. The supervisor only increments `summary["failed"]` if we actually wrote the backend, which means the dashboards won't lie when the backend is having a bad minute.
- Used `celery.states.READY_STATES` rather than hand-rolling a set of terminal status strings. If celery adds a new terminal state in the future our check stays correct.

Next phase entry criteria:
- [x] all Phase 4 tests written and passing in smoke
- [x] log entry written
- [ ] PR opened / merged (deferred)

---

### 2026-05-20 — Phase 3 — Supervisor sweep + bounded requeue (Claude)

What I did:
- Added `supervisor.py` with `sweep_once(now=None)`, the lock helpers (`_acquire_lock` / `_release_lock` using SET NX EX), the staleness check, and `_republish_task` that uses `current_app.send_task(name, args, kwargs, task_id=<same>, headers={"lex_attempt": N+1})`.
- Added `tasks.py` with the beat shared task `lex.lex_app.celery_recovery.tasks.sweep_dead_workers`.
- Registered the beat entry in `settings.py` (only when `LEX_TASK_RECOVERY_ENABLED`). django-celery-beat's DatabaseScheduler ingests `CELERY_BEAT_SCHEDULE` on startup so no manual provisioning is needed.
- Persists `attempt = N+1` to the Redis hash *before* `send_task` so even if the header gets dropped somewhere in transport, the next `task_prerun` reads the right value.
- Phase 3 does **not** inject failure on cap exceeded — `deferred_at_cap` is just logged. Phase 4 wires `mark_as_failure`.

Files touched:
- `lex/lex_app/celery_recovery/supervisor.py` — new (~190 lines).
- `lex/lex_app/celery_recovery/tasks.py` — new.
- `lex/lex_app/settings.py` — added the `CELERY_BEAT_SCHEDULE["lex-celery-recovery-sweep"]` entry, gated on `LEX_TASK_RECOVERY_ENABLED`.
- `lex/test_project/tests/celery_async/test_8r_worker_recovery.py` — added `TestCluster08r_Supervisor` (6 tests: 8.60–8.65) and `nx=True` support to `FakeRedis.set`.

Tests:
- 8.60 fresh heartbeat → no action.
- 8.61 stale heartbeat + dead worker → `send_task` called with original `task_id`, `lex_attempt=N+1` header, attempt counter bumped in hash.
- 8.62 stale task heartbeat but worker key still alive → skip with WARN.
- 8.63 lock already held → skip.
- 8.64 `attempt+1 > max_retries` → `deferred_at_cap`, no `send_task`.
- 8.65 only a `:lock` key exists → not yielded by the scan.
- All 6 passed in standalone smoke run.

Open items / punts (carried forward):
- Did not touch Celery's `unacked` / `unacked_index` keys yet. With `visibility_timeout=float("inf")` they don't time out so they leak per dead worker. Bounded leak; Phase 6 cleans up after we verify the exact key names on a running staging Redis (open question O-1 in Section 13).
- Did not add `parent_task_id` to the envelope yet — not needed until orphan detection lands.

Decisions made (and why):
- Lock TTL is configurable via `LEX_TASK_LOCK_TTL` (default 30s). Long enough for one sweep to do its work; short enough that a dead supervisor doesn't block recovery for more than a minute.
- Missing `last_hb_iso` is treated as stale (rather than "ignore"). Reason: the only way that field disappears is the hash TTL elapsing, which only happens long after the worker died. Treating it as stale is the right safety default.
- Worker-key alive check is done **after** staleness so we don't pay an extra Redis round-trip on non-stale tasks (the common case).

Next phase entry criteria:
- [x] all Phase 3 tests written and passing in smoke
- [x] log entry written
- [ ] PR opened / merged (deferred)

---

### 2026-05-20 — Phase 2 — Heartbeat thread + signal handlers (Claude)

What I did:
- Added `redis_client.py` (lazy, factory-overridable connection wrapper) and `heartbeat.py` (in-flight registry, daemon heartbeat thread, four signal handlers).
- Wired `enable()` to call `heartbeat.connect_signal_handlers()`, and hooked `enable()` into `lex/lex_app/celery.py` right after `autodiscover_tasks`, wrapped in try/except so a missing Redis can never crash worker boot.
- Added a minimal in-memory `FakeRedis` to the test file so tests don't need a running broker.

Files touched:
- `lex/lex_app/celery_recovery/redis_client.py` — new. `get_client()` is the only entry point; `set_client_factory()` is the test hook.
- `lex/lex_app/celery_recovery/heartbeat.py` — new. ~280 lines. Contains the `_InFlightRegistry`, `HeartbeatThread`, `start_heartbeat`/`stop_heartbeat`, the four signal handlers, and `connect_signal_handlers()`.
- `lex/lex_app/celery_recovery/__init__.py` — `enable()` now connects signals via the heartbeat module. Still idempotent and still env-gated.
- `lex/lex_app/celery.py` — added the `from lex.lex_app import celery_recovery; celery_recovery.enable()` call.
- `lex/test_project/tests/celery_async/test_8r_worker_recovery.py` — added `FakeRedis` (in-memory shim) and `TestCluster08r_Heartbeat` (7 tests: 8.50–8.56).

Tests:
- 8.50 task_prerun first delivery → attempt=0
- 8.51 task_prerun reads `lex_attempt` header → attempt=2 on requeue
- 8.52 task_postrun deletes the hash
- 8.53 heartbeat writes `lex:wrk:<host>` with TTL
- 8.54 heartbeat refreshes `last_hb_iso` on in-flight tasks
- 8.55 per-task `task.lex_max_retries` override is recorded (Phase 5 will wire the decorator)
- 8.56 `worker_shutting_down` removes the worker key
- All 7 passed via standalone smoke run (no Django venv on this machine; next agent: run via `lex test ...test_8r_worker_recovery` to confirm under the Django runner).

Open items / punts:
- The heartbeat thread reads env vars on every tick via `_heartbeat_interval()`. This is intentional so tests can patch `LEX_TASK_HEARTBEAT_INTERVAL` and the change takes effect on the next loop. In production the values don't change. Keep this behavior.
- `task.lex_max_retries` is being *recorded* by `task_prerun` but no decorator is writing it yet. The fallback to `LEX_TASK_MAX_RETRIES` env default works today; the per-task surface lands in Phase 5.
- Did not yet stash `parent_task_id` in the envelope. Decision: add it in Phase 3 once the supervisor needs it for cross-checking whether the parent is still alive (risk-table item).

Decisions made (and why):
- `decode_responses=False`. Args/kwargs are pickled+base64; mixing decoded strings and raw bytes in the same hash is awkward. Tests treat field names as bytes too — keeps fake and real Redis consistent.
- Hash TTL = `ttl_seconds * 4` (longer than worker TTL). Reason: when the supervisor wakes up on the next scan after a worker dies, it must still find the envelope to re-publish. Worker key has the shorter TTL so it expires when the worker dies — that's the staleness signal.
- The heartbeat thread *never* raises out — failures log and the supervisor catches them via the natural missed-heartbeat path. Anything else would couple worker liveness to Redis liveness in a way we don't want.

Next phase entry criteria:
- [x] all Phase 2 tests written and passing in smoke
- [x] log entry written
- [ ] PR opened / merged (deferred)

---

### 2026-05-20 — Phase 1 — Module scaffolding + settings + feature flag (Claude)

What I did:
- Added the five recovery env knobs to `lex/lex_app/settings.py` right after the existing `celery_active = CELERY_ACTIVE` block: `LEX_TASK_RECOVERY_ENABLED` (default `true`), `LEX_TASK_HEARTBEAT_INTERVAL` (5), `LEX_TASK_HB_TTL_MULTIPLIER` (3), `LEX_TASK_SUPERVISOR_SCAN_INTERVAL` (10), `LEX_TASK_MAX_RETRIES` (4).
- Scaffolded `lex/lex_app/celery_recovery/` with `__init__.py`, `redis_keys.py`, `exceptions.py`.
- `enable()` is a no-op stub for now — it just flips a module-level flag and respects `LEX_TASK_RECOVERY_ENABLED`.
- `redis_keys.py` centralizes the namespace: `lex:wrk:<host>`, `lex:task:<task_id>`, `lex:task:<task_id>:lock`, plus SCAN patterns.
- `exceptions.py` defines `WorkerLost` (carries `worker_hostname`, `attempt`, `task_id`) and `MaxRequeueExceeded(WorkerLost)` so the parent can `except WorkerLost` and catch both the transient and terminal cases.

Files touched:
- `lex/lex_app/settings.py` — added the recovery knobs block.
- `lex/lex_app/celery_recovery/__init__.py` — new.
- `lex/lex_app/celery_recovery/redis_keys.py` — new.
- `lex/lex_app/celery_recovery/exceptions.py` — new.
- `lex/test_project/tests/celery_async/test_8r_worker_recovery.py` — new (Phase 1 portion only — keys, exceptions, `enable()` flag behavior; later phases extend this file).

Tests:
- 9 unit tests in `TestCluster08r_RecoveryScaffolding` covering keys, exceptions, and `enable()` idempotency / master-switch behavior.
- Verified via a direct-import smoke that the recovery package has zero Django dependencies and imports clean on a bare Python — important for unit-test runs without a configured Django.
- Did **not** run the Django test runner because this machine has no project venv at the path documented in `CLAUDE.md` (`project_example/.venv/`). Next agent: when you have the venv, run `lex test lex.test_project.tests.celery_async.test_8r_worker_recovery --verbosity=2 --noinput --keepdb` and confirm green before starting Phase 2.

Open items / punts:
- None for this phase. Heartbeat, signal handlers, supervisor are all Phase 2+.

Decisions made (and why):
- `enable()` reads the env var directly instead of `django.conf.settings` so the package stays import-safe in tests that don't configure Django. The Django settings entries duplicate the same values — that's fine and keeps the settings module the operator-facing documentation surface.
- Kept `MaxRequeueExceeded` as a subclass of `WorkerLost` per the rationale above. Single `except WorkerLost` is the recommended catch in `CalculationModel.execute_calculation`.

Next phase entry criteria:
- [x] all phase tests written
- [x] log entry written
- [ ] PR opened / merged (deferred — user has not asked to commit yet)

---

### 2026-05-20 — Plan written (Claude, this session)

- Read `docs/lex_topics/12-celery-async-dispatch.md`, `docs/features/processing/celery and async calculations.md`, `lex/lex_app/celery.py`, `lex/lex_app/celery_tasks.py`, `lex/core/tasks/CeleryTaskDispatcher.py`, `lex/lex_app/settings.py:370-447`.
- Identified the root cause of infra-only deadlock: `visibility_timeout=float("inf")` neutralizes Celery's only Redis-transport recovery signal, so `task_acks_late=True` + `task_reject_on_worker_lost=True` never fire on worker death.
- Confirmed the parent waits via `AsyncResult.get()` in `WaitForTasks.wait_for_completion` (`lex/lex_app/celery_tasks.py:415-432`) and that the result backend is Postgres, so `app.backend.mark_as_failure` is the right injection point.
- Confirmed concurrency=1 + post-task `Control.shutdown` makes the per-worker-per-task assumption safe.
- Wrote this plan in 7 phases.
- **No code changes yet.** Next agent starts at Phase 1.

### TEMPLATE — copy this when starting a new entry

```
### YYYY-MM-DD — Phase N — <short title> (<agent name>)

What I did:
- ...

Files touched:
- path/to/file.py — what changed

Tests:
- new test files / new test methods
- pass/fail summary
- coverage delta if measured

Open items / punts:
- ...

Decisions made (and why):
- ...

Next phase entry criteria:
- [ ] all phase tests green
- [ ] log entry written
- [ ] PR opened / merged
```

---

## 16. Glossary

- **Heartbeat key**: `lex:wrk:<hostname>` (worker liveness) and `last_hb_iso` field inside `lex:task:<task_id>` hash (task liveness).
- **Stale**: `now - last_hb_iso > LEX_TASK_HEARTBEAT_INTERVAL × LEX_TASK_HB_TTL_MULTIPLIER`.
- **Attempt**: number of times this `task_id` has been delivered to a worker. 0 on first dispatch from the parent. Incremented by the supervisor when it requeues.
- **Requeue**: `app.send_task(..., task_id=<same_id>, headers={"lex_attempt": N+1})`.
- **Failure injection**: `app.backend.mark_as_failure(<task_id>, WorkerLost(...))` so the parent's `AsyncResult.get()` raises.
- **Supervisor**: the periodic Celery beat task `lex.celery_recovery.tasks.sweep_dead_workers` that scans `lex:task:*` keys.
