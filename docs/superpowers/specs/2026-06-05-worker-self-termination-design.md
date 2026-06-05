# Worker Self-Termination — Design

> **Date:** 2026-06-05
> **Status:** Approved (design phase)
> **Scope:** lex-app framework only (`lex/`), no infra changes
> **Fixes:** Cluster bug #1 (cancellation doesn't kill worker pods) and #2 (idle worker pods never receive a task and live forever)
> **Explicitly out of scope:** #3 backend-crash recovery, #4 CalculationStatus consistency, the heartbeat/supervisor recovery module — all deferred to the recovery work.

---

## 1. Problem

lex-app instance Celery worker pods are created per-instance by a KEDA `ScaledJob`
(`scaledjob-<instance>`, Redis `listLength` trigger, `restartPolicy: Never`,
`activeDeadlineSeconds: 86400`). Two failure modes were reported in the GKE cluster that do
not reproduce locally:

1. **Cancellation doesn't kill worker pods.** `CalculationModel.cancel()` →
   `_revoke_celery_task()` (`lex/core/models/CalculationModel.py:728`) only calls
   `app.control.revoke(task_id, terminate=True, signal="SIGTERM")`. That terminates the
   *task* (SIGTERM to the pool child running it) but leaves the worker process — and
   therefore the pod — alive. Locally the worker process effectively *is* the task, so it
   "dies"; in-cluster the pod survives. lex-app has no Kubernetes API access, so it cannot
   delete the pod directly.

2. **Idle worker pods live forever.** KEDA spawns roughly one Job per queued message, but a
   `concurrency=4` Celery worker drains up to 4 messages at once. Surplus pods boot to an
   already-empty queue and **never receive a task**. The only existing self-shutdown path,
   `shutdown_worker_after_task_completion` (`lex/lex_app/celery.py:99`), is wired to
   `task_postrun` — it can only fire *after a task completes*. A pod that completes zero
   tasks never reaches it and runs until the 24h `activeDeadlineSeconds`.

### Root insight

Both bugs are the same gap: **the framework has no reliable way for an idle worker to
terminate its own process.** Because the KEDA Job uses `restartPolicy: Never`, a worker
process that exits cleanly causes the pod to terminate. So the entire fix is: *make an idle
worker shut itself down* — in the two situations the current `task_postrun` path misses
(after a cancel, and when no task was ever received).

---

## 2. Existing mechanism we build on

`lex/lex_app/celery.py` already contains the relevant machinery:

- **`lex_shutdown_if_idle(panel, completed_task_id=None)`** (`celery.py:36`) — a remote-control
  Panel command that runs in the worker **MainProcess**. It reads
  `celery.worker.state.active_requests` and `reserved_requests`, subtracts an optionally
  excluded task id, and — only if the resulting `pending` set is empty — schedules a SIGTERM
  on a 50ms `threading.Timer` (graceful warm shutdown).
- **`shutdown_worker_after_task_completion`** (`celery.py:99`) — a `task_postrun` handler
  (runs in the pool child) that `broadcast`s `lex_shutdown_if_idle` to its own MainProcess.
  It fires on **every** task completion, but because of the `active | reserved` check it only
  actually terminates the worker when **all** of that worker's tasks (across all concurrency
  slots) are done — a single completion on an otherwise-busy worker no-ops.
- **`_is_non_local_deployment_target()`** (`celery.py:82`) — returns `True` only when
  `DEPLOYMENT_TARGET` is set and is not `local`. This is the gate that keeps shutdown
  behavior off in local dev and the test suite.

The two new triggers reuse all of this. The concurrency-correct idle check is therefore
preserved for free.

---

## 3. Design

One shared decision helper plus two new triggers (alongside the existing `task_postrun` path,
which is retained unchanged), all in `lex/lex_app/celery.py`. Nothing touches infra, the worker
image, or the broker.

### 3.1 Refactor: `_warm_shutdown_if_idle(exclude_task_ids=())`

Extract the body of `lex_shutdown_if_idle` (read active|reserved, subtract excluded ids, and
if empty schedule the SIGTERM) into a plain MainProcess-callable function. Add a
module-level `_shutdown_scheduled` flag (threading-guarded) so that once a shutdown is
scheduled, subsequent calls are no-ops — preventing stacked SIGTERM timers.

- `lex_shutdown_if_idle` (the Panel command) becomes a thin wrapper that calls the helper
  with `exclude_task_ids={completed_task_id}` — the existing `task_postrun` broadcast path is
  unchanged and keeps working exactly as before.
- The two new MainProcess triggers (below) call the helper **directly** — they already run in
  MainProcess, so no self-broadcast is needed.

### 3.2 Trigger A — Cancel fast-path (`task_revoked` handler) — fixes #1

Connect a `@task_revoked.connect` handler. `cancel()` → `revoke(terminate=True)` produces
exactly a revoke event, and `task_revoked` fires in the worker **MainProcess**. The handler:

1. Returns immediately if `_is_non_local_deployment_target()` is false or the feature is
   disabled.
2. Calls `_warm_shutdown_if_idle(exclude_task_ids={revoked_task_id})`.

If the revoked task was the worker's only work → `pending` empty → SIGTERM → pod terminates
within ~1s of the cancel. If the worker has other live tasks (concurrency), `pending` is
non-empty → it stays up. Recursive/descendant cancellation fans out into individual revokes
that are each handled on their own worker.

Running in MainProcess is what makes this reliable: unlike `task_postrun`, it does not depend
on a hard-terminated child fork completing its signal handler.

### 3.3 Trigger B — Idle watchdog (daemon thread) — fixes #2

On `worker_ready`, start a single daemon thread (only when non-local and feature enabled):

- Seed `last_active` to a monotonic timestamp at thread start.
- Loop on a short interval (e.g. every 5s):
  - Read `active_requests | reserved_requests`.
  - If non-empty → refresh `last_active = monotonic()`.
  - If empty **and** `monotonic() - last_active >= LEX_WORKER_IDLE_SHUTDOWN_SECONDS` →
    call `_warm_shutdown_if_idle()` and stop the loop.
- Stop cleanly on `worker_shutting_down`.

A worker KEDA spawned that never gets a task is idle from `worker_ready` → after the timeout
it self-terminates. A worker that did work and went idle again is also caught (defense in
depth beneath the existing `task_postrun` path).

### 3.4 Configuration

| Env var | Default | Purpose |
|---|---|---|
| `LEX_WORKER_IDLE_SHUTDOWN_ENABLED` | `true` | Master switch for **both** new triggers (watchdog + cancel fast-path). `false` disables cleanly with no new behavior. |
| `LEX_WORKER_IDLE_SHUTDOWN_SECONDS` | `30` | Idle grace before the watchdog terminates a never-busy / idle worker. |

Both new triggers are additionally gated behind the existing `_is_non_local_deployment_target()`
check, so local dev and the framework test suite are unaffected regardless of the env vars.

---

## 4. Safety properties

- **Concurrency-correct:** the idle check is `(active | reserved)` across all pool slots — a
  worker only exits when every slot is free. Revoking one of four running tasks leaves three
  in `pending`, so the pod stays.
- **Blocked parents are protected:** a parent `CalculationModel` running in a worker and
  blocking on `WaitForTasks` / `AsyncResult.get()` is an **active request**, so it is never
  counted as idle. The watchdog cannot kill a blocked parent. (Preserved for free by reusing
  the existing `active_requests` check — this is the key invariant.)
- **Startup race avoided:** `last_active` is seeded at `worker_ready`, giving a legitimately
  spawned worker the full grace window to receive its task. Real broker round-trips are
  sub-second, so genuine work always lands inside the window; only KEDA's surplus pods time
  out.
- **Idempotent shutdown:** the `_shutdown_scheduled` flag ensures at most one SIGTERM timer is
  armed; the watchdog loop stops once it decides to shut down.
- **Consistent with existing intent:** the framework already terminates a worker once it goes
  fully idle after task completion; "idle ⇒ safe to exit, KEDA respawns if the queue grows" is
  the established model. This change only extends it to the never-got-a-task and cancelled
  cases.

---

## 5. Testing strategy

The `lex-testing` skill allocates the exact cluster/letter/scenarios at planning time;
Cluster 8 ("Celery & Async") is the home.

- **Unit (`SimpleTestCase`, mocked `celery.worker.state` + `os.kill`/`threading.Timer`):**
  `_warm_shutdown_if_idle` schedules SIGTERM iff `pending` is empty; correctly excludes the
  revoked/completed id; no-ops when local or feature-disabled; `_shutdown_scheduled` prevents
  a second timer.
- **Unit (injected clock + mocked state):** watchdog idle-decision — triggers only after
  `idle >= timeout`, refreshes `last_active` on activity, stops after deciding; `task_revoked`
  handler calls the helper with the revoked id excluded and respects the gates.
- **Cluster validation (acceptance, not unit):** the real SIGTERM → process-exit → pod
  termination is integration-level and is validated against the cluster via
  `~/LUND_IT/LexStressLab/D_WorkerRecovery`:
  - cancel a running calculation, observe the worker pod terminate within ~1s;
  - over-provision so surplus pods spawn with no task, observe them self-reap after the idle
    timeout.
  Recorded as the acceptance check in the implementation plan.
- All new behavior stays behind the non-local gate, so the existing ~350 framework tests stay
  green.

---

## 6. Acceptance criteria

1. Cancelling a running calculation terminates the worker pod that held the task within a few
   seconds, without affecting sibling tasks on a concurrency>1 worker.
2. A worker pod that boots and never receives a task terminates itself after
   `LEX_WORKER_IDLE_SHUTDOWN_SECONDS`.
3. A worker actively running a task — including a parent blocked in `WaitForTasks` — is never
   terminated by either trigger.
4. With `LEX_WORKER_IDLE_SHUTDOWN_ENABLED=false`, or running locally, behavior is identical to
   today.
5. New unit tests pass; existing framework test suite stays green.

---

## 7. Files touched

- `lex/lex_app/celery.py` — refactor `lex_shutdown_if_idle` into `_warm_shutdown_if_idle`;
  add `task_revoked` handler, `worker_ready`/`worker_shutting_down` watchdog wiring, and the
  `_shutdown_scheduled` guard.
- `lex/lex_app/settings.py` — read `LEX_WORKER_IDLE_SHUTDOWN_ENABLED` /
  `LEX_WORKER_IDLE_SHUTDOWN_SECONDS`.
- New test module under the Cluster 8 test tree (letter allocated by `lex-testing`).
