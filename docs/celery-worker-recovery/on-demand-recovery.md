# On-demand worker recovery

The recovery supervisor guards calculations whose worker died. Most of the time
there is nothing to guard — no calculation is running, so the pod sits idle
burning its reservation on every instance in the cluster.

This makes it **on demand**: the pod exists while calculation work is in flight
and scales back to zero when it isn't.

Related reading: [README](README.md) for the recovery subsystem itself,
[deep-dive](deep-dive.md) for the heartbeat/requeue mechanics.

---

## User stories

### Story 1 — the platform team (the reason we are doing this)

> **As** the team running the LEX cluster,
> **I want** the per-instance recovery pod to exist only while a calculation is
> in flight,
> **so that** we stop paying for an idle pod on every instance.

Each recovery pod reserves 250Mi / 50m. Across ~10 instances that is ~2.5Gi and
half a vCPU permanently reserved to guard workers that themselves scale to zero.
The reservation is what costs — the pod is idle by design between calculations.

**Acceptance criteria**

- With no calculation running, the instance runs **zero** recovery pods.
- Dispatching a calculation brings a supervisor up without operator action.
- The pod goes away again once the instance is idle, and does not flap when
  calculations arrive back-to-back.
- No change to the default: an instance that has not opted in behaves exactly
  as it does today.

### Story 2 — the LEX user (the guarantee that must not regress)

> **As** someone who triggered a long calculation,
> **I want** it to finish or fail visibly even if the worker running it is
> killed,
> **so that** I never find a row silently stuck `IN_PROGRESS` hours later.

This story is already satisfied by the always-on supervisor. It is written down
here because it is the constraint: **saving the pod must not weaken it.**

**Acceptance criteria**

- A worker killed mid-calculation still results in the task being retried, or
  the row being finalized `ABORTED` with a FAILURE result so any waiter
  unblocks.
- Recovery may be *delayed* by the time it takes a supervisor to come up. It
  must never be *skipped*.
- A calculation that is merely queued — no worker yet — is never mistaken for
  a dead one.

---

## How it works

The scale signal is not a separate metric. It **is** the recovery registry.

The registry already keeps a Redis SET (`<instance>:lex:recover:index`) of every
task currently in flight. KEDA's native `redis` scaler reads list length
(`LLEN`), not set cardinality, so the registry maintains a parallel LIST
(`<instance>:lex:recover:inflight`) that mirrors that SET exactly. KEDA watches
its length.

That identity is the whole design. The thing that decides whether a supervisor
should exist is the same thing the supervisor acts on, in the same Redis, written
by the same code path. There is no second source of truth to drift.

### Lifecycle of one calculation

```
   backend                     redis                    keda                pods
      │                          │                        │                   │
 1. dispatch ──── claim_dispatched ──▶ index +1           │            (no supervisor)
      │                       inflight LLEN 1 ──────────▶ poll (15s)
      │                          │                        └── scale 0→1 ──▶ supervisor up
      │                          │                        └── worker ScaledJob ──▶ worker up
 2.   │        worker prerun ─── register (upgrade) ──▶ status=running, heartbeat starts
      │                          │                        │                   │
 3.   │            supervisor loop (10s): heartbeat alive? → leave alone
      │                          │                        │                   │
 4. done ──────── deregister ──▶ index −1, LLEN 0         │
      │                          │                     cooldown 300s
      │                          │                        └── scale 1→0 ──▶ supervisor gone
```

1. **Dispatch.** The backend marks the row `IN_PROGRESS` and publishes the task.
   `CallbackTask.apply_async` claims it: payload written `SET NX`, then one Lua
   script adds the id to the index and — because the `SADD` inside it reports the
   id as newly indexed — pushes it onto the in-flight list. One script, so the
   two cannot diverge (case 9). No heartbeat is written; there is no worker yet.
2. **Worker start.** `task_prerun` upgrades the claim to `status="running"`,
   preserving the retry budget, and starts the heartbeat thread. The `SADD`
   returns 0 this time (already indexed), so the script pushes nothing — the
   list counts tasks, not events.
3. **While running.** The supervisor sweeps every 10s. A live heartbeat means
   skip. A dead one means the worker died: requeue the same `task_id` within the
   retry budget, or finalize `ABORTED` once it is exhausted. After each sweep it
   reconciles the list against the index.
4. **Completion.** `task_postrun` deregisters: index entry removed, every list
   occurrence drained. Once `LLEN` has been 0 for the cooldown, KEDA removes the
   pod.

### Why the signal rises at *dispatch*, not at task start

Ownership used to begin at `task_prerun`, inside the worker. A task whose worker
pod was still `Pending` — cluster full, node group at max — was invisible to the
whole recovery subsystem. That is what incident 1410 turned on.

Claiming at dispatch fixes ownership, and because the claim also drives the scale
signal, it means **the supervisor is already up while the worker pod is still
queued**. Had the signal been driven only by `task_prerun`, the supervisor would
have stayed at zero for exactly the window that most needs watching.

---

## Edge cases

| # | Scenario | Outcome |
|---|---|---|
| 1 | Task dispatched, cluster full, worker `Pending` 20 min | ✅ Handled |
| 2 | Supervisor pod itself cannot be scheduled | ✅ Unlikely — watch item |
| 3 | Redis restarts mid-calculation | ✅ No delta vs always-on |
| 4 | Back-to-back calculations | ✅ Handled |
| 5 | Worker dies while supervisor is at zero | ✅ Delayed, not lost |
| 6 | Supervisor killed mid-requeue | ✅ Handled |
| 7 | Two supervisors briefly overlap | ✅ Handled |
| 8 | List entry leaks (pod pinned up) | ✅ Self-heals |
| 9 | List entry lost (pod scaled away) | ✅ Impossible by construction |
| 10 | Worker `SIGKILL`ed after finishing, before postrun | ✅ Handled |
| 11 | Instance uses bitemporal future activations | ⛔ **Do not enable** |
| 12 | Calculation runs without Celery | ✅ Not applicable |

### 1. Dispatched but queued for a long time

**Given** a 20-way fan-out meets a node pool at its maximum, so most worker Jobs
sit `Pending`.
**When** the supervisor sweeps and finds claims with no heartbeat.
**Then** it does *not* treat them as dead. A claim carries
`status="dispatched"`; for those the broker message is the liveness story, not a
heartbeat. Within `LEX_TASK_DISPATCH_GRACE_SECONDS` (300) it waits. Past grace it
inspects the queue: message still there → wait; queue unreadable → wait (never
double-dispatch on uncertainty); message verifiably gone → requeue the same id.

The supervisor is up and idle for those 20 minutes. That is the intended cost —
it is the guard.

### 2. The supervisor pod cannot be scheduled

**Given** the cluster is full — the same condition as case 1.
**When** KEDA scales the supervisor 0→1.
**Then** in principle the pod could sit `Pending` too, leaving in-flight work
unguarded until capacity frees.

In practice the gap is small. The supervisor requests **128Mi / 50m**; a worker
requests **1000m / 4000Mi**. The scheduling failures in incident 1410 were
worker-sized pods not fitting — a pod twenty times smaller on CPU and thirty
times smaller on memory fits in slack those cannot use.

Deliberately **not** pre-emptively fixed. The fix, if it turns out to be needed,
is a `priorityClassName` letting the supervisor preempt a worker (one pod that
guards all the others should outrank them) — but that needs a cluster-scoped
PriorityClass in a second repo, so it is not worth paying for a risk that may
not exist. Treat it as a **test observation**: if the supervisor is ever seen
`Pending` during the trial, add it then.

### 3. Redis restarts mid-calculation

**Given** the per-instance Dragonfly restarts or is evicted.
**Then** the index and the list are both gone, `LLEN` reads 0, and after the
cooldown the supervisor scales itself down while calculations are still running.

That sounds like a regression, and it is worth being precise that it is not.
With the registry wiped, `list_tracked()` returns empty and `scan_and_recover`
iterates nothing — **an always-on supervisor recovers exactly as much as an
absent one: nothing.** The payloads it would need are gone with everything else.
Scale-to-zero changes the pod count, not the outcome. This is a pre-existing
property of a recovery subsystem that keeps its state in Redis, not something
running on demand introduces.

The tempting fix — turning on Dragonfly persistence — is worse than the problem.
Restoring a broker snapshot resurrects queue messages that may already have been
acked; with `task_acks_late=True` and `visibility_timeout=inf`, the ack is the
only thing that removes a message, so a stale snapshot un-acks completed work and
tasks re-execute. Duplicate calculation runs are a worse failure than a lost
registry.

The mitigation that does help already shipped: the startup reset spares untracked
`IN_PROGRESS` rows younger than `LEX_STARTUP_ABORT_MIN_AGE_SECONDS` (1800), so a
backend restart after a Redis wipe no longer blind-aborts healthy work.

### 4. Back-to-back calculations

**Given** calculation A finishes and B is dispatched 60s later.
**Then** the `cooldownPeriod` of 300s means the pod never went away. No flapping,
and B finds a supervisor already running.

### 5. A worker dies while the supervisor is at zero

**Given** the pod has scaled down, and a task is dispatched and its worker dies
before KEDA notices.
**Then** dispatch itself pushed onto the list, so KEDA scales up within one poll
(15s) plus pod start. Nothing deregisters a dead worker, so the registry entry is
still there when the supervisor arrives, and it recovers the task normally.

The load-bearing property: **the registry outlives the supervisor.** Scale-to-zero
can delay recovery by the time it takes a pod to start. It cannot skip it.

### 6. Supervisor killed mid-requeue

**Given** the pod is killed while holding a per-task recovery lock.
**Then** the lock is TTL-bounded and expires; the next supervisor retries. The
retry budget is committed only *after* `send_task` succeeds, so an interrupted
requeue never burns a retry.

### 7. Two supervisors overlap

**Given** a rollout, or KEDA scaling up during a `Recreate`.
**Then** `maxReplicaCount: 1` plus `strategy: Recreate` make this rare, and the
per-task Redis lock makes it safe when it happens: one replica claims a task, the
other skips it that pass.

### 8. A list entry leaks

**Given** a crash between the `SREM` and the `LREM` in `deregister`, leaving an
id on the list that the index no longer tracks.
**Then** `LLEN` never returns to 0 and the pod stays up forever — wasteful, not
unsafe.

Self-heals: the supervisor reconciles the list against the index after every
sweep, so the stale entry is dropped on the next pass. Reconciling only at
startup would have let this persist for the pod's entire life, which for an
on-demand pod can be days.

### 9. A list entry is lost

**Given** the reverse of case 8 — the index gains an id but the list does not —
so a tracked task is missing from the signal.
**Then** if it were the only in-flight task, `LLEN` would stay 0 and the
supervisor would never come up. Nothing would reconcile it either, because
reconciliation runs *in* the supervisor. Unlike case 8, this direction is
genuinely unsafe, and unlike case 3 it *is* a delta: with an always-on pod a
missing list entry is harmless.

**Closed by construction.** The two writes are a single Lua script
(`registry._track`), so the index and its mirror cannot diverge — either both
land or neither does, and "neither" is the ordinary best-effort degradation
every registry operation already has when Redis is unreachable. The `SADD`
result gates the `LPUSH` *inside* the script, so re-tracking an id already in
flight still never double-counts.

If a Redis without scripting is ever used, `_track` falls back to the
non-atomic pair — a weaker guarantee than the script, but better than no signal.

Note the asymmetry is deliberate everywhere else too: `reconcile_inflight_list()`
converges entry-by-entry and re-checks `SISMEMBER` before removing, rather than
doing `DEL` + rebuild, specifically so that a concurrent registration cannot be
wiped. Over-counting keeps the pod up; under-counting scales it away. Every
ambiguous case is resolved toward over-counting.

### 10. Worker killed after the work was done

**Given** a worker that persisted its result and was `SIGKILL`ed before
`task_postrun` ran.
**Then** the heartbeat expires and the entry looks dead — but requeuing would
re-run concluded work and could resurrect a finished outcome (an `ERROR` row
coming back `SUCCESS`). The supervisor checks two authoritative signals first,
the result backend and the calculation rows themselves, and deregisters instead
of requeueing. The list drains normally.

### 11. Instances using bitemporal future activations ⛔

**Given** an instance that relies on future-dated history activations.
**Then** **do not enable this.** Those fire from django-celery-beat
`ClockedSchedule` rows hosted on the `beat` recovery pod. Switching an instance
to the `supervisor` driver removes beat, and activations silently stop — no
error, they just never fire.

This is why the driver is per-instance and opt-in rather than a fleet default,
and why it stays that way until the clock moves to the global scheduler.

### 12. Calculations without Celery

**Given** an instance running calculations synchronously in the backend.
**Then** no task is dispatched, nothing is registered, and the supervisor stays
at zero. Correct — there is no separate worker that can die.

---

## Configuration

Terraform, per instance:

```hcl
config = {
  recovery_driver        = "supervisor"  # default "beat"
  recovery_scale_to_zero = true          # default false; ignored unless driver is "supervisor"
}
```

`recovery_scale_to_zero` is forced false unless the driver is `supervisor`, so
setting it on a beat instance is inert.

Relevant lex-app settings:

| Setting | Default | Meaning |
|---|---|---|
| `LEX_TASK_DISPATCH_GRACE_SECONDS` | 300 | How long a queued claim is left alone before the broker queue is consulted |
| `LEX_TASK_SUPERVISOR_SCAN_INTERVAL` | 10 | Sweep interval |
| `LEX_TASK_MAX_RETRIES` | 4 | Requeues before `ABORTED` |
| `LEX_STARTUP_ABORT_MIN_AGE_SECONDS` | 1800 | Untracked `IN_PROGRESS` rows younger than this survive a startup reset (`0` = legacy blind abort) |

KEDA: `pollingInterval` 15s, `cooldownPeriod` 300s, min 0 / max 1, trigger
`LLEN <instance>:lex:recover:inflight` in broker DB 1.

---

## Rollout checklist

1. Merge both halves. Nothing changes — every default is current behaviour.
2. Pick a non-production instance **with no future-dated activations**.
3. Set `recovery_driver = "supervisor"`, `recovery_scale_to_zero = true`.
4. Verify in-cluster, not on green unit tests:
   - idle instance → zero recovery pods
   - start a calculation → pod scales to 1
   - kill a worker mid-calculation → the row recovers
   - completion → list drains → pod back to 0
   - start two calculations back-to-back → no flapping
5. While testing, watch for the one thing not designed around: the supervisor
   pod itself sitting `Pending` (case 2). If it never happens, nothing further
   is needed.
