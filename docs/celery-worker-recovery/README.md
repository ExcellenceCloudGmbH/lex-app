# Celery Worker Recovery

A heartbeat-based safety net that detects abruptly-killed Celery workers and
recovers their in-flight tasks, so a long-running calculation never gets stuck
`IN_PROGRESS` forever.

## The problem

When a Celery worker dies cleanly, Celery handles the fallout. But our workers
do not always die cleanly. In the cluster they run as KEDA-scaled pods that can
be **SIGKILLed, OOM-killed, or evicted** out from under a running task. When
that happens mid-calculation, the calculation row is left at `IN_PROGRESS` and
any caller blocked on `AsyncResult(task_id).get()` waits forever. Nothing ever
flips it to a terminal state.

This subsystem closes that gap. Each worker re-stamps a short-lived Redis
"heartbeat" key while a task runs. A dedicated supervisor watches for tasks
that are still tracked as in-flight but whose heartbeat has expired — that
divergence is the unambiguous fingerprint of a dead worker — and either
re-runs the task or finalizes it as a terminal failure.

Code lives in `lex/lex_app/celery_recovery/`.

## Why heartbeats and not `visibility_timeout`

Celery's Redis transport has its own redelivery mechanism (`visibility_timeout`):
if a task isn't acked within that window, the broker re-delivers it. We have
**deliberately disabled that**. The Celery settings set
`visibility_timeout=float("inf")` together with `task_acks_late=True` and
`task_reject_on_worker_lost=True`. That combination neutralizes Celery's native
Redis redelivery on purpose (so long calculations are never silently
re-delivered and double-run by the broker).

The consequence: recovery **cannot** rely on `visibility_timeout`, and you
should **not** change it back. This subsystem is layered on top instead, using
heartbeats as an independent, explicit liveness signal. Treat
`visibility_timeout=inf` as a fixed design constraint.

## How it works, end to end

1. **Worker heartbeat thread.** When a task starts (`task_prerun`), the worker
   registers it in Redis and spins up a small daemon thread that re-stamps the
   task's heartbeat key every `LEX_TASK_HEARTBEAT_INTERVAL` seconds. The
   heartbeat key has a TTL of `interval * LEX_TASK_HB_TTL_MULTIPLIER`, so a
   couple of missed refreshes let it expire. On `task_postrun` the thread stops
   and the task is deregistered. (See `heartbeat.py`.)

2. **Redis keys.** For each tracked task the registry keeps three keys
   (namespaced per instance, see `redis_keys.py`):
   - an **index set** of every tracked `task_id`;
   - a **payload key** (`lex:recover:task:<id>`) holding the base64-pickled
     re-dispatch payload plus a `retries` count;
   - a **heartbeat key** (`lex:recover:hb:<id>`) — the short-TTL liveness marker.

   Everything in the registry is best-effort: if Redis is unreachable, every
   call degrades to a silent no-op. (See `registry.py`.)

3. **Supervisor scan.** `scan_and_recover()` enumerates the index set and, for
   each task, checks whether the heartbeat key still exists. A task that is
   still in the index but whose heartbeat has expired is **dead**.

4. **Same-`task_id` requeue.** For a dead task within budget, the supervisor
   re-dispatches the **same** `task_id` onto its original queue. Reusing the id
   matters: a parent blocked on `AsyncResult(task_id).get()` will receive the
   eventual result of the re-run. The re-dispatched message is itself what
   creates KEDA queue pressure to bring a fresh worker up.

5. **Bounded budget → terminal ABORTED.** Each requeue increments `retries`.
   Once `retries` reaches `LEX_TASK_MAX_RETRIES`, the supervisor stops retrying,
   writes a `MaxRequeueExceeded` FAILURE to the result backend (so the waiter
   unblocks instead of hanging), flips the stuck calculation row to `ABORTED`,
   and deregisters the task. (See `supervisor.py`, `exceptions.py`.)

## Deployment

The supervisor must be **always-on**, because a dead worker can only be detected
by a process that is still alive. In the cluster, neither the workers nor the
backend can host it reliably:

- Celery **workers** run as a KEDA **ScaledJob** — they scale to zero when idle.
- The **backend** runs as a KEDA **ScaledObject** scaled 0..1 — it also scales
  to zero.

So the supervisor gets its own dedicated **singleton Deployment**. The reference
manifest is
[`k8s/recovery-supervisor-deployment.yaml`](k8s/recovery-supervisor-deployment.yaml)
(`replicas: 1`, `strategy: Recreate`, `terminationGracePeriodSeconds: 30`). It
is a reference template that gets adapted into the lex-instance Helm chart.

**Critical:** the supervisor pod must point at the **same Redis instance and DB
index** the workers use, and share the same `INSTANCE_RESOURCE_IDENTIFIER`. If
it talks to a different Redis it sees an empty registry and silently recovers
nothing. Wire its `envFrom` to the same ConfigMap/Secret the backend and worker
pods already consume.

### Ways to run it

- **Console script** (preferred — what the manifest uses):
  ```
  lex-recovery-supervisor              # always-on loop
  lex-recovery-supervisor --once       # single pass, then exit
  lex-recovery-supervisor --interval 5
  ```
  Exposed via `[project.scripts]`; bootstraps Django then forwards to the
  management command (see `entrypoint.py`).

- **Management command** (same loop, via the lex CLI / Django):
  ```
  lex run_recovery_supervisor
  lex run_recovery_supervisor --once          # single sweep (cron / debugging)
  lex run_recovery_supervisor --interval 10
  ```
  `--interval` overrides `LEX_TASK_SUPERVISOR_SCAN_INTERVAL`; `--once` runs one
  `scan_and_recover()` pass and exits.

The command handles SIGTERM/SIGINT gracefully: it finishes the in-flight sweep,
then exits. On an unexpected crash it exits non-zero so Kubernetes restarts the
pod.

> A Celery-beat fallback also exists: the `sweep_dead_workers` task is scheduled
> as `lex-celery-recovery-sweep` (with `expires` set so the queue can't backlog).
> The dedicated singleton is preferred — the beat path would spawn a worker every
> interval just to poll.

## Configuration knobs

| Setting | Default | Controls |
|---|---|---|
| `LEX_TASK_RECOVERY_ENABLED` | `true` | Master switch. Disables all signal handlers, the heartbeat thread, and the supervisor sweep. |
| `LEX_TASK_HEARTBEAT_INTERVAL` | `5` (s) | How often the worker re-stamps its heartbeat key. |
| `LEX_TASK_HB_TTL_MULTIPLIER` | `3` | Heartbeat key TTL = interval × multiplier. Higher = more tolerance for a slow/paused worker before it's declared dead. |
| `LEX_TASK_SUPERVISOR_SCAN_INTERVAL` | `10` (s) | How often the supervisor scans for dead tasks (also the beat schedule period). |
| `LEX_TASK_MAX_RETRIES` | `4` | Requeue budget per task before giving up and finalizing `ABORTED`. |
| `LEX_TASK_REQUEUE_GRACE_SECONDS` | `60` (s) | Grace TTL granted to a heartbeat just before a requeue, so the re-run can start before the next scan re-detects it. Also the per-task recovery-lock TTL. |
| `LEX_TASK_PAYLOAD_TTL_SECONDS` | `86400` (24h) | How long a re-dispatch payload survives in Redis. Refreshed while the task is alive (see edge case C). |

`LEX_TASK_REQUEUE_GRACE_SECONDS` and `LEX_TASK_PAYLOAD_TTL_SECONDS` are read with
their defaults in code; set them as environment variables to override.

## Edge-case behavior

**A. Cancelled calculation → finalized CANCELLED, never requeued.** Before
requeuing a dead task the supervisor checks the cluster-wide cancellation marker
for that calculation. If the user had cancelled it, the supervisor finalizes the
stuck rows as `CANCELLED` (not `ABORTED`), deregisters the task, and does **not**
requeue — re-running work the user asked to stop would be wrong.

**B. Multi-supervisor double-act safety.** Before acting on a dead task the
supervisor takes a per-task Redis lock (`SET ... NX EX`). Only the replica that
wins the lock acts; others skip the task this window. So even if two supervisors
run, the same dead task is never requeued or finalized twice in one pass.
(Running >1 replica is safe but unnecessary — the manifest keeps it at 1.)

**C. Long-running tasks outliving the payload TTL.** The payload key defaults to
a 24h TTL, but some calculations run longer. Each heartbeat refresh also bumps
the payload key's expiry (only the expiry, never the value), so a task that is
still alive keeps a recoverable payload for as long as it runs.

**D. Broker outage during requeue must not burn the budget.** The requeue is
deliberately split around the dispatch. First `grant_grace` re-stamps the
heartbeat to a short TTL (delays re-detection only). Then `send_task` actually
re-dispatches — and may raise if the broker is down. Only **after** a successful
dispatch does `persist_payload` commit the incremented `retries`. So a failed
dispatch consumes only the grace window, not a retry slot; the next pass retries
cleanly.

**E. Revoke fast-path.** When a task is revoked (typically a user cancellation),
the `task_revoked` handler stops its heartbeat thread and deregisters it
immediately by request id. That removes it from the registry before the
supervisor could ever observe an expired heartbeat, so a revoked task can never
be mistaken for a dead worker and requeued.

## Enabling and disabling

Recovery does real work only when **both** `CELERY_ACTIVE` is true **and**
`LEX_TASK_RECOVERY_ENABLED` is true. `enable(app)` (wired from the Celery app
module) connects the heartbeat signal handlers only under those conditions, and
it is idempotent.

When recovery is disabled — or whenever Redis is simply unreachable — every
registry operation is a best-effort no-op and `is_alive()` fails safe (it never
declares a task dead when it can't read Redis). That means **local, synchronous,
and CI runs are completely unaffected**: no heartbeat threads, no requeues, no
surprises. Set `LEX_TASK_RECOVERY_ENABLED=false` to turn the whole subsystem off
in environments where no real Redis-backed Celery is running.

The `sweep_dead_workers` task is itself excluded from tracking, so the recovery
machinery never tries to recover its own sweeps.

## Recovery driver: supervisor (default) vs beat

Two interchangeable drivers run the identical `scan_and_recover()` engine; pick
one per instance via the Helm value `workers.recoveryDriver`:

- **`supervisor`** (default) — the dedicated `lex-recovery-supervisor` pod runs
  the scan loop in-process. No broker round-trip; nothing to schedule.
- **`beat`** — a singleton `lex-recovery-beat` pod runs `celery worker -B -Q
  recovery`: embedded beat fires `sweep_dead_workers` on the
  `django_celery_beat` DatabaseScheduler (schedule visible/editable in the
  Django admin), and the same pod consumes the dedicated `recovery` queue.
  Recovered tasks are still re-dispatched to their main queue, so KEDA scales
  real workers 0→N exactly as with the supervisor.

Both preserve the non-circular property (the scan runs in an always-on pod, not
in a scale-to-0 worker). Run exactly one; running both is safe (per-task Redis
lock) but wasteful.
