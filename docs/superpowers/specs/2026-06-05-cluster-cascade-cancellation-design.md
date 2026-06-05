# Cluster-Wide Cascade Cancellation — Design

> **Date:** 2026-06-05
> **Status:** Approved (design phase)
> **Scope:** lex-app framework only (`lex/`); no infra changes
> **Fixes:** Cancellation does not propagate to child calculations running on other
> worker pods — the parent shows `CANCELLED` while descendant workers keep computing.
> **Builds on:** the merged worker self-termination work (PR #586) — a worker whose only
> task is revoked self-terminates, so revoking every node's task *is* what kills the pods.
> **Explicitly out of scope:** requeue-on-death / heartbeat recovery (the deferred
> `celery_recovery` module) — that is a **separate, follow-up spec** built on the same
> Redis-state foundation introduced here.

---

## 1. Problem

`CalculationModel.cancel(instance, recursive=True)`
(`lex/core/models/CalculationModel.py:370`) runs in the **backend** process. It discovers
descendants to revoke by reading `ActiveCalculationStateStore`
(`lex/core/signals/ActiveCalculationStateStore.py`), which is **in-memory and
per-process** (by deliberate design — see its module docstring, written that way to fix a
DatabaseCache/transaction-visibility bug for the WebSocket reconciliation path).

A parent calculation running on **worker pod A** dispatches child calculations
(`child.save()` → `execute_calculation` → `dispatch_calculation_task()` →
`ActiveCalculationStateStore.set_task_id(record_id, task_id)`,
`CalculationModel.py:953`). Those child `task_id`s register in **worker A's** memory.
The backend's `cancel()` reads the **backend's** copy of the store, which never saw them.

Two distinct failure modes follow:

1. **Discovery gap.** The backend cannot enumerate descendant `task_id`s that live in
   other pods' memory. It revokes the parent (so the UI shows `CANCELLED`) but the
   children keep running on their pods. This is the reported symptom: *"I see a cancelled
   status, but some dangling workers received tasks and were executing them either way."*

2. **Late-pod gap.** Celery `revoke` is an in-memory broadcast to **currently-connected**
   workers. A KEDA-spawned pod that boots *after* the revoke broadcast and pulls an
   already-queued child task never learns it was revoked, and runs it to completion.

### Root insight

The kill mechanism is correct — the merged self-termination work means *revoking a
worker's only task makes the pod terminate*. What is missing is **cluster-wide discovery**:
`cancel()` must be able to enumerate every node's `task_id` regardless of which pod
registered it, then signal-revoke each. A thin cooperative check closes the late-pod gap.

Per the developer's directive: **cancellation happens through signals** (every
calculation, including the root, runs as a Celery task and has a `task_id` to revoke);
the cooperative Redis check is a **non-relied-upon addition**, not the primary mechanism.

---

## 2. Existing mechanisms we build on

- **`ActiveCalculationStateStore.set_task_id` / `clear`** — the existing mutators where a
  node's Celery `task_id` is attached / removed. These are the single write-through
  chokepoint for the new index.
- **Shared `calculation_id`** — children dispatched inside a parent share the parent's
  `calculation_id` (set in `calculate_hook` / operation context), already used by
  `find_descendants` for the recursive-cancel walk. It is the natural key for grouping a
  calculation tree.
- **`CalculationModel._revoke_celery_task(task_id)`** (`CalculationModel.py:728`) — the
  existing `current_app.control.revoke(task_id, terminate=True, signal="SIGTERM")` call.
  Reused unchanged as the per-node kill.
- **`CallbackTask.on_failure` cancellation mapping** (`lex/lex_app/celery_tasks.py:153`) —
  already maps `TaskRevokedError` / `Terminated` / `CalculationCancelled` onto `CANCELLED`
  rather than `ERROR`. The cooperative abort raises `CalculationCancelled`, so a late-pod
  abort lands as `CANCELLED` for free.
- **Celery's redis client** — Celery already runs on `redis://…/1` (broker). A lazily
  built redis-py client (reusing the Celery app's connection where possible) backs the new
  index under a dedicated `lex:calc:*` key namespace. No new infrastructure.

---

## 3. Design

A thin **cluster cancellation index** in Redis, additive beside the in-memory store. All
new state lives under `lex:calc:*`. Nothing touches infra, the worker image, or the
broker configuration.

### 3.1 Redis data model

| Key | Type | Contents | Lifetime |
|---|---|---|---|
| `lex:calc:tree:<calculation_id>` | HASH | `{record_id: task_id}` — one field per calculation node (root + every descendant), written by whichever pod ran the node | TTL refreshed on each write (default a few hours); `HDEL` per node on terminal state |
| `lex:calc:cancelled:<calculation_id>` | STRING | `"1"` — cooperative cancelled marker | short TTL (default ~1h) |

A small module (e.g. `lex/core/cancellation/cluster_cancel_index.py`) owns the client and
these operations. It degrades to a no-op when Redis is unavailable or `CELERY_ACTIVE` is
off, so local/sync/test execution is unaffected.

### 3.2 Write-through (the discovery fix)

At the existing registration chokepoint:

- **`ActiveCalculationStateStore.set_task_id(record_id, task_id)`** — after attaching the
  `task_id` in memory, look up the entry's `calculation_id` and
  `HSET lex:calc:tree:<calculation_id> <record_id> <task_id>`, then refresh the tree TTL.
  Every Celery-dispatched `CalculationModel` (root and children) passes through here, so
  every node becomes visible cluster-wide.
- **`ActiveCalculationStateStore.clear(record_id)`** — before/while popping the in-memory
  entry, read its `calculation_id` and `HDEL lex:calc:tree:<calculation_id> <record_id>`
  so terminal nodes leave the index.

All Redis calls are wrapped in best-effort try/except (mirroring the existing `set_task_id`
defensive logging) — a Redis failure never breaks a calculation or a registration.

### 3.3 Cancel flow (signals primary)

`CalculationModel.cancel(instance, recursive=True)` gains a cluster-wide discovery step,
otherwise preserving its current structure and return shape:

1. Resolve `calculation_id` for the target (unchanged).
2. **Discover the whole tree:** `HGETALL lex:calc:tree:<calculation_id>` →
   `{record_id: task_id}` across all pods, **unioned** with the existing in-memory
   `find_descendants(calculation_id)` (covers the local process and the no-Redis fallback).
3. **Set the cooperative marker** `lex:calc:cancelled:<calculation_id>` (TTL).
4. **Signal-revoke every discovered `task_id`** via `_revoke_celery_task` — the primary
   kill. Revoking the parent's own `task_id` also terminates a parent blocked in
   `WaitForTasks` (its task receives SIGTERM → `Terminated` → `CANCELLED`).
5. **Persist `CANCELLED`** on each node's DB row (model + pk derived from `record_id`, the
   same resolution the store already does) and broadcast status, as today.

The return dict keeps its existing keys (`cancelled`, `cancellable`, `status`,
`revoked_tasks`, `descendants_cancelled`); `revoked_tasks` now reflects the full
cluster-wide set.

### 3.4 Cooperative safety net (not relied upon)

`calc_and_save` (`lex/lex_app/celery_tasks.py:802`) checks
`lex:calc:cancelled:<calculation_id>` once at task start. If the marker is set, it raises
`CalculationCancelled` instead of running the model — so a child that a **late-booting
pod** pulled *after* the revoke broadcast lands as `CANCELLED` via the existing
`on_failure` mapping, rather than computing to completion.

This is the **only** cooperative check (cheapest, highest-value, at the natural task entry
point). The signal-revoke path remains the mechanism we rely on; if Redis is unavailable
the check is skipped and behavior falls back to signals-only.

### 3.5 Configuration

| Env var | Default | Purpose |
|---|---|---|
| `LEX_CLUSTER_CANCEL_ENABLED` | `true` | Master switch for the Redis index + cooperative check. `false` → cancel falls back to today's in-memory-only behavior. |
| `LEX_CLUSTER_CANCEL_TREE_TTL_SECONDS` | `14400` (4h) | TTL for `lex:calc:tree:*` so abandoned trees self-evict. |
| `LEX_CLUSTER_CANCEL_MARKER_TTL_SECONDS` | `3600` (1h) | TTL for `lex:calc:cancelled:*`. |

The index is additionally inert whenever the Redis client cannot be built or
`CELERY_ACTIVE` is not `true`, so local dev and the framework test suite are unaffected
regardless of the env vars.

---

## 4. Safety properties

- **Signals remain primary.** Every node is killed by `_revoke_celery_task` (SIGTERM). The
  cooperative marker only catches tasks a late pod has not started yet; it is skipped
  entirely when Redis is down.
- **Blocked parents handled.** A parent blocked in `WaitForTasks`/`AsyncResult.get()` is a
  node in the tree with its own `task_id`; revoking it terminates the parent task directly.
- **Local/sync/test unchanged.** With no Redis, `CELERY_ACTIVE` off, or
  `LEX_CLUSTER_CANCEL_ENABLED=false`, the index is a no-op and `cancel()` uses the existing
  in-memory `find_descendants`. The cluster-7 cancellation suite
  (`lex/test_project/tests/calculations/test_7n_cancellation.py`, sync, no broker) keeps
  passing without modification.
- **Best-effort, never fatal.** Every Redis interaction is wrapped; a failure logs and
  degrades to signals-only — it never breaks a calculation, a registration, or a cancel.
- **Additive coherence.** The Redis index is purely additive for cross-process discovery;
  the in-memory store stays authoritative within a process for snapshot/reconciliation.
- **Self-cleaning.** TTLs guarantee abandoned trees and markers evict even if a pod dies
  before calling `clear`.

---

## 5. Testing strategy

The `lex-testing` skill allocates the exact cluster/letter/scenarios at planning time.
Cancellation behavior is Cluster 8 ("Celery & Async") and/or Cluster 7 ("Calculation");
the existing `test_7n_cancellation.py` is the cluster-7 home to extend.

- **Unit (mocked redis client):**
  - `set_task_id` / `clear` write-through: `HSET` / `HDEL` on the right key with the
    entry's `calculation_id`; no-op and no raise when Redis is absent or disabled.
  - `cancel()` discovers the tree from Redis (`HGETALL`), unions with in-memory
    descendants, sets the cancelled marker, and calls `_revoke_celery_task` once per
    discovered `task_id`; return dict reflects the full set.
  - `calc_and_save` raises `CalculationCancelled` when the marker is set and runs normally
    when it is not / when Redis is absent.
  - All paths no-op cleanly when `CELERY_ACTIVE` is off or `LEX_CLUSTER_CANCEL_ENABLED` is
    false — proving local/test behavior is unchanged.
- **Regression:** the existing cluster-7 cancellation tests stay green with no edits.
- **Cluster acceptance** (integration, not unit) via the worker-recovery stress-lab scenario:
  - cancel a parent whose children run on multiple pods → every descendant worker pod
    terminates (no dangling workers);
  - over-provision so a late pod pulls an already-queued child after the revoke → it
    self-aborts and the row lands `CANCELLED`, not `SUCCESS`.

---

## 6. Acceptance criteria

1. Cancelling a parent calculation revokes and terminates **every** descendant task across
   all worker pods, not just the parent — no dangling workers keep computing.
2. A child task that a late-booting pod pulls after the revoke broadcast self-aborts and is
   recorded `CANCELLED` (cooperative net), rather than running to `SUCCESS`.
3. A parent blocked in `WaitForTasks` is terminated by the revoke of its own task.
4. With `LEX_CLUSTER_CANCEL_ENABLED=false`, no Redis, `CELERY_ACTIVE` off, or running
   locally, `cancel()` behaves exactly as it does today (in-memory discovery only).
5. New unit tests pass; the existing cluster-7 cancellation suite and the framework test
   suite stay green.

---

## 7. Files touched

- `lex/core/cancellation/cluster_cancel_index.py` — **new**: redis-py client + tree/marker
  operations (write-through, discover, set/check marker, TTLs, graceful degradation).
- `lex/core/signals/ActiveCalculationStateStore.py` — `set_task_id` / `clear` write through
  to the index.
- `lex/core/models/CalculationModel.py` — `cancel()` unions Redis-discovered tree with
  in-memory descendants, sets the marker, revokes the full set.
- `lex/lex_app/celery_tasks.py` — `calc_and_save` start-of-task cooperative marker check.
- `lex/lex_app/settings.py` — read the three `LEX_CLUSTER_CANCEL_*` knobs.
- New / extended test module under the allocated cluster tree.
