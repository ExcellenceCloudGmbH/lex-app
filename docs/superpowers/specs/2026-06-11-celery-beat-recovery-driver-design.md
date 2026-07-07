# Celery-beat recovery driver — design

> **Date:** 2026-06-11
> **Status:** Design approved, implementation pending
> **Branches:** lex-app `feat/celery-beat-recovery-driver`; chart `LEX_TERRAFORM_MODULES` (matching branch)

## Problem / motivation

Celery worker recovery (heartbeat-based detection of SIGKILL/OOM/evicted workers
+ bounded same-`task_id` requeue) is already implemented and deployed. The
canonical cluster driver is a dedicated **always-on `recovery-supervisor`
Deployment** (`replicas: 1`) that runs `scan_and_recover()` in-process on a loop.

We want the **scheduler tooling of Celery beat** — specifically a DB-driven
schedule that is visible and editable in the Django admin
(`django_celery_beat` `PeriodicTask` rows via `DatabaseScheduler`) — **without**
giving up the property that makes the supervisor correct on this cluster:

- Workers run as a **KEDA ScaledJob, scale-to-0** (`minReplicaCount: 0`,
  `maxReplicaCount: 21`, trigger = Redis `listLength ≥ 3` on db index 1).
- The backend runs as a **KEDA ScaledObject 0..1**, also scale-to-0.

Plain (vanilla) Celery beat is **wrong here**: beat only *enqueues* the sweep
task; a **worker** must dequeue and run it. With scale-to-0 workers that creates
a chicken-and-egg loop (to detect a dead worker, beat needs a worker to run the
sweep), forces a KEDA cold-start every interval just to run a ~50 ms scan (pod
churn), and pollutes the very Redis list KEDA scales on. See
`docs/celery-worker-recovery/deep-dive.md` §13.

## Chosen approach: embedded-beat, self-consuming recovery pod

Keep the **same one small always-on singleton pod**, but run it as a Celery
worker with **embedded beat** that consumes its own dedicated queue:

```
celery -A lex_app worker -B -Q recovery --concurrency 1 \
  --scheduler django_celery_beat.schedulers:DatabaseScheduler -l info
```

- `-B` — embedded beat fires the existing `sweep_dead_workers` task every
  `LEX_TASK_SUPERVISOR_SCAN_INTERVAL`.
- `-Q recovery` — a **dedicated queue this same pod consumes**, separate from the
  main KEDA-watched list. The scan therefore runs *in this pod*, independent of
  the scale-to-0 workers, and never adds noise to the autoscaling signal.
- `scan_and_recover()` runs in-process here; when it finds a dead worker's task
  it calls `_requeue()`, which re-dispatches to the **calc's own (main) queue**
  (`supervisor.py:85` — `incremented.get("queue") or _default_queue()`). That
  main-queue message raises `listLength`, so **KEDA scales the real workers
  0 → N** to do the recovered work.

This preserves every property of the supervisor (always-on, in-process scan, no
worker needed to *detect* deaths, requeue drives KEDA) while exposing the
schedule in the Django admin. The cost is one tiny broker round-trip and one
extra queue, bought purely for admin visibility.

### Why correctness holds (the non-circular property)

The recovery pod runs the scan **itself**. It needs workers only to *do the
recovered work afterwards*, never to *detect* deaths. Because the recovered task
is routed to the main queue (not `recovery`), and the recovery pod subscribes to
**only** `-Q recovery`, recovered work always flows outward to the scale-to-0
workers and never loops back to the recovery pod.

## What stays unchanged (pure reuse — no engine rewrite)

- Worker-side **heartbeat** handlers, wired on worker boot via `enable(app)`
  (`lex/lex_app/celery.py:317`). Workers keep stamping liveness keys as today.
- `scan_and_recover()` engine, the Redis **registry**, the **per-task lock**, and
  the **bounded-requeue** logic — untouched.
- `sweep_dead_workers` `@shared_task` (`supervisor.py:400`) and its
  `CELERY_BEAT_SCHEDULE` entry (`settings.py:540–559`).
- `_requeue()` routing — already targets the calc's main queue. No change.
- `run_recovery_supervisor` management command + `lex-recovery-supervisor`
  console script remain in the codebase as the local/fallback driver; they are
  simply not the cluster default anymore.

## App-side changes (lex-app)

1. **Route the sweep to the dedicated `recovery` queue.** Add
   `options={"queue": "recovery"}` to the `lex-celery-recovery-sweep` entry in
   `CELERY_BEAT_SCHEDULE` (or an equivalent `task_routes` rule keyed on
   `lex.lex_app.celery_recovery.supervisor.sweep_dead_workers`). This keeps
   sweeps off the main KEDA-watched list; the recovery pod self-consumes them.
2. **Exclude `sweep_dead_workers` from heartbeat tracking.** Add one guard in
   `lex/lex_app/celery_recovery/heartbeat.py` so the `task_prerun`/`task_postrun`
   handlers skip the sweep task by name. Otherwise the recovery pod would
   register/deregister a heartbeat for the sweep on every interval — harmless but
   needless Redis churn.
3. **No change to `_requeue`** — already routes recovered tasks to the main
   queue. Verified at `supervisor.py:85`.

## Infra changes (`LEX_TERRAFORM_MODULES/modules/lex-instance/chart`)

1. **Driver selector value** `workers.recoveryDriver: supervisor | beat`
   (default `supervisor`). Existing instances render unchanged; the Infra Fund 2
   / test instance sets `beat`.
2. **`recovery_supervisor.yaml`** renders only when
   `recoveryDriver == "supervisor"` (wrap the existing `{{ if .Values.workers.enabled }}`
   with the selector).
3. **New `celery_beat_recovery.yaml`** renders only when
   `recoveryDriver == "beat"`. Same singleton shape as the supervisor:
   - `kind: Deployment`, `replicas: 1`, `strategy: { type: Recreate }`
     (embedded beat must be a singleton — never double-fire).
   - `image: {{ .Values.workers.image }}`, worker `configmap` + `appEnvSecret`
     via `envFrom`.
   - **Same secret-backed volume mounts as the supervisor** (`iamSaKeySecret`
     at `/app/django-storages`, `sharepointCertSecret` at `/app/sharepoint-cert`)
     — `settings.py` reads GCS creds at import time, so without these the pod
     crash-loops before any recovery code runs.
   - `command: ["celery","-A","lex_app","worker","-B","-Q","recovery","--concurrency","1","--scheduler","django_celery_beat.schedulers:DatabaseScheduler","-l","info"]`.
   - `env`: `CELERY_ACTIVE=TRUE`, `LEX_TASK_RECOVERY_ENABLED=true`
     (belt-and-suspenders, matching the supervisor).
   - Resources small (~`64m` / `128Mi` request, `250m` / `256Mi` limit).
   - `terminationGracePeriodSeconds: 30`,
     `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"`, same
     nodeSelector/toleration gating as the supervisor.
4. **values.yaml** — add `workers.recoveryDriver` (default `supervisor`) and a
   `beat` resources block.
5. **Worker ScaledJob unchanged** — stays `minReplicaCount: 0`. Preserving
   scale-to-0 is the whole point; the recovery pod, not a warm worker, runs the
   scan.
6. `django_celery_beat` migrations run via the normal backend `migrate`
   (already in `INSTALLED_APPS`, `DatabaseScheduler` already configured).

## Data flow

```
[recovery pod: celery worker -B -Q recovery]
   │  embedded beat fires every LEX_TASK_SUPERVISOR_SCAN_INTERVAL
   ▼
 sweep_dead_workers (on 'recovery' queue, consumed by THIS pod)
   │
   ▼
 scan_and_recover()  ── reads Redis heartbeats/registry directly
   │  dead worker found, within retry budget
   ▼
 _requeue(task_id) ── app.send_task(..., queue=<main queue>)   # NOT 'recovery'
   │
   ▼
 main Redis list grows → KEDA ScaledJob listLength≥3 → spawn workers 0→N
   │
   ▼
 scaled worker consumes the recovered task (same task_id, parent .get() resolves)
```

## Failure modes

- **Recovery pod dies:** K8s restarts the singleton (`Recreate`). Missed sweeps
  are caught on the next pass; heartbeat TTLs mean nothing is lost, only delayed.
- **Two recovery pods briefly overlap (rollout):** `Recreate` prevents overlap;
  even if it happened, the per-task Redis lock (`try_acquire_recovery_lock`)
  makes concurrent scans safe.
- **Broker down during requeue:** `_requeue` raises before incrementing the retry
  count (grace granted first, count persisted only after a successful
  `send_task`), so the retry budget is not burned by a broker outage.
- **Beat clock skew / missed tick:** `expires` on the schedule entry bounds stale
  sweep messages; the next tick recovers.

## Testing

- **Unit:** extend `lex/tests/unit/infra/test_celery_recovery.py` to assert
  (a) `sweep_dead_workers` is excluded from heartbeat tracking, and (b) the beat
  schedule entry routes to the `recovery` queue while `_requeue` still targets the
  main queue.
- **Local / chaos:** reuse the existing harness
  (`~/LUND_IT/LexStressLab/D_WorkerRecovery`) with the recovery pod started as
  `celery -A lex_app worker -B -Q recovery -c 1` instead of
  `lex-recovery-supervisor`; kill a worker mid-task and confirm same-`task_id`
  requeue + bounded retries + ABORTED on budget exhaustion.
- **Cluster smoke:** on the test instance with `recoveryDriver: beat`, confirm the
  `PeriodicTask` row appears in Django admin, kill a worker mid-calc, and verify
  the calc resumes (not stuck IN_PROGRESS) and KEDA scales workers from 0.

## Rollout

- Default `recoveryDriver: supervisor` — no change to existing instances.
- Set `recoveryDriver: beat` only on the Infra Fund 2 / test instance.
- Both drivers call the identical engine; running both at once is safe (per-task
  lock) but wasteful, so exactly one renders per instance.

## Out of scope

- Removing the supervisor code path (kept as fallback/local driver).
- Any change to the worker ScaledJob scaling parameters.
- Replacing recovery with `visibility_timeout` (deliberately off — see
  `docs/celery-worker-recovery/deep-dive.md` §2).
