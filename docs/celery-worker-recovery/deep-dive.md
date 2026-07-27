# Celery Worker Recovery — Deep Dive

> A long-form, learning-oriented companion to the operator
> [`README.md`](README.md). The README tells you *what knobs exist and how to
> deploy*. This document explains *why the system is shaped the way it is*, walks
> every failure scenario step by step, shows how to prove it locally, explains
> how Redis itself makes it work, and compares the supervisor against the
> Celery-beat alternative.
>
> Every claim here is anchored to the source under `lex/lex_app/celery_recovery/`
> with file:line references so you can read along.

---

## Table of contents

1. [The problem in one paragraph](#1-the-problem-in-one-paragraph)
2. [Why `visibility_timeout=inf` forces this design](#2-why-visibility_timeoutinf-forces-this-design)
3. [The data model: four Redis keys](#3-the-data-model-four-redis-keys)
4. [How Redis works internally (and why we rely on it)](#4-how-redis-works-internally-and-why-we-rely-on-it)
5. [The two halves: worker heartbeat & supervisor scan](#5-the-two-halves-worker-heartbeat--supervisor-scan)
6. [Walkthrough: a normal calculation](#6-walkthrough-a-normal-calculation)
7. [Scenario A: a worker dies mid-calculation](#7-scenario-a-a-worker-dies-mid-calculation)
8. [Scenario B: repeated death → budget exhausted → ABORTED](#8-scenario-b-repeated-death--budget-exhausted--aborted)
9. [Scenario C: user cancels while the worker is dead](#9-scenario-c-user-cancels-while-the-worker-is-dead)
10. [Scenario D: the backend dies](#10-scenario-d-the-backend-dies)
11. [Scenario E: Redis dies](#11-scenario-e-redis-dies)
12. [Scenario F: the supervisor dies, or there are two](#12-scenario-f-the-supervisor-dies-or-there-are-two)
13. [Supervisor vs. Celery beat](#13-supervisor-vs-celery-beat)
14. [Testing it locally](#14-testing-it-locally)
15. [Configuration knobs and latency math](#15-configuration-knobs-and-latency-math)
16. [Glossary](#16-glossary)

---

## 1. The problem in one paragraph

A LEX calculation runs as a Celery task on a worker pod. That pod can die
**abruptly** — `SIGKILL`, OOM-kill, GKE node eviction, spot-instance reclaim.
When that happens mid-task, three bad things coincide: (1) the calculation's DB
row is stuck at `is_calculated = "IN_PROGRESS"` forever, because nothing flips
it; (2) any process blocked on `AsyncResult(task_id).get()` hangs forever; and
(3) the work is simply gone — it never resumes. This subsystem detects that
death and either re-runs the task or marks it terminally failed, so neither the
row nor the waiter is ever stuck.

---

## 2. Why `visibility_timeout=inf` forces this design

Celery's Redis transport has a built-in recovery mechanism called
**`visibility_timeout`**: when a worker picks up a message, the broker hides it
for that many seconds; if the worker doesn't acknowledge completion within the
window, the broker assumes the worker died and **redelivers** the message to
another worker.

LEX deliberately switches this off. In `settings.py`:

```python
# settings.py:398-409
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "global_keyprefix": f"{os.getenv('INSTANCE_RESOURCE_IDENTIFIER', 'celery')}:",
    "visibility_timeout": float("inf"),     # never auto-redeliver
}
CELERY_TASK_ACKS_LATE = True                 # ack only after the task completes
CELERY_TASK_REJECT_ON_WORKER_LOST = True
```

Why disable it? Because a legitimate LEX calculation can run for **hours**. With
a finite `visibility_timeout`, the broker would conclude "this worker is taking
too long, it must be dead" and **redeliver the message to a second worker — while
the first is still running it.** That double-run is far more dangerous than a
stuck row: two workers writing the same calculation's rows concurrently.
`visibility_timeout=inf` means "a message handed to a worker is never
auto-returned to the queue, no matter how long the worker is silent."

**The consequence is the whole reason this subsystem exists:** Celery's only
native death-recovery is now off. We cannot use message visibility to detect a
dead worker, because we just told the broker to ignore it forever. We need a
*different*, explicit liveness signal that we control — one that says "the worker
running this task is *actually* alive right now," independent of whether it has
acked the message. That signal is a **heartbeat**.

> **Treat `visibility_timeout=inf` as a fixed constraint.** Do not "fix" recovery
> by lowering it — that re-introduces the double-run hazard. The heartbeat layer
> is the correct place to handle death.

---

## 3. The data model: four Redis keys

Everything the system knows lives in four Redis keys per task (plus one shared
set). All keys are prefixed with the instance identifier so multiple LEX
instances sharing one Redis never collide (`redis_keys.py:14-16`):

```
prefix = "<INSTANCE_RESOURCE_IDENTIFIER>:"      e.g. "bbb-prod-16:"
```

| Key | Redis type | TTL | Role |
|---|---|---|---|
| `<p>lex:recover:index` | **SET** | none | The set of every `task_id` currently tracked. Cheap to enumerate each scan. |
| `<p>lex:recover:task:<id>` | **string** | 24h (`LEX_TASK_PAYLOAD_TTL_SECONDS`) | The **payload**: `base64(pickle({name, args, kwargs, queue, retries}))` — everything needed to re-send the task. |
| `<p>lex:recover:hb:<id>` | **string** `"1"` | **15s** (`interval × multiplier`) | The **heartbeat** / liveness marker. The running worker re-stamps it; if it expires, the worker is gone. |
| `<p>lex:recover:lock:<id>` | **string** `"1"` | 60s, `SET NX EX` | Per-task supervisor lock so two supervisors never act on the same dead task at once. |

Why base64-pickle for the payload? Because `args[0]` is a **pickled Django model
instance** (the `CalculationModel`), since `CELERY_TASK_SERIALIZER = "pickle"`
(`settings.py:390`). The pickled bytes aren't valid UTF-8, so we base64 them to
store as a Redis string. See `_encode`/`_decode` at `registry.py:99-104`.

### The one invariant that drives everything

> **"Tracked but no heartbeat" ⇒ the worker is dead.**

If `task_id ∈ index` **and** `payload:<id>` still exists (24h TTL — it survives)
**but** `hb:<id>` has expired (15s TTL — nobody refreshing it), the worker that
held it is gone. That single divergence is the only thing the supervisor keys
off (`registry.py:11-15`). It never consults `visibility_timeout`.

---

## 4. How Redis works internally (and why we rely on it)

You asked specifically how Redis itself makes this work. The recovery design
leans on four concrete Redis behaviours. Understanding them is understanding why
the system is correct.

> Note: in the cluster the "Redis" is actually **Dragonfly** (a Redis-compatible
> server — see the `dragonfly` Helm release). It speaks the Redis protocol and
> honours the same commands and TTL semantics, so everything below applies
> identically. Locally you run real Redis on `:6379`.

### 4.1 Keys, strings, sets, and the keyspace

Redis is an in-memory key-value store. A "key" is just a string name; its
**value** can be one of several types. We use exactly two:

- **String** — an opaque blob. We use it for `hb:<id>` (value `"1"`),
  `payload:<id>` (the base64 pickle), and `lock:<id>` (value `"1"`).
- **Set** — an unordered collection of unique members. We use one set,
  `index`, holding all tracked `task_id`s. `SADD` adds (`registry.py:141`),
  `SREM` removes (`registry.py:175`), `SMEMBERS` enumerates (`registry.py:188`).
  A set guarantees uniqueness, so registering the same id twice is harmless.

All of these live in a single flat namespace (the "keyspace"). That's why we
prefix every key with the instance id — it's the only isolation Redis gives us
within one logical database.

### 4.2 Logical databases (the `/1`, `/2` in the URL)

A single Redis server has numbered logical databases. The Celery broker uses
**db 1** (`redis://…/1`, `settings.py:374-377`); the result backend uses **db 2**
(`settings.py:374` region). The recovery client reuses the **broker URL**
(`registry.py:81`), so the recovery keys live in **db 1 alongside the broker
queue.** This matters in the cluster: KEDA's ScaledJob trigger watches the
broker list in db 1, so when the supervisor re-dispatches a task the message
lands in the same db KEDA is watching, and a worker pod gets spawned.

### 4.3 TTL and key expiration — the heart of "death detection"

This is the single most important Redis feature for us. Every key can carry a
**TTL** (time-to-live). We set it with the `ex=` argument on `SET`
(`registry.py:142`: `client.set(hb_key, "1", ex=15)`) or with `EXPIRE`
(`registry.py:162`). When the TTL elapses, **Redis deletes the key
automatically.** No process runs the deletion; Redis does it.

*How* Redis expires keys is itself worth knowing, because it affects timing:

- **Lazy expiration.** When any client touches a key (e.g. our supervisor calls
  `EXISTS hb:<id>`), Redis first checks whether it has expired; if so it deletes
  it on the spot and behaves as if the key were absent. So the moment the
  supervisor *asks* about an expired heartbeat, it reliably gets "gone."
- **Active expiration.** Independently, Redis runs a background cycle ~10×/second
  that samples keys with TTLs and evicts the expired ones, so memory is reclaimed
  even for keys nobody reads.

The practical upshot: a heartbeat with a 15s TTL is **guaranteed absent** to the
next reader at most a fraction of a second after the 15s elapses (lazy
expiration makes the read itself authoritative). We never have to poll for "has
it been long enough" — we just ask "does the key exist," and Redis's expiry
machinery has already made that answer correct. The worker proves it is alive by
**continuously pushing the TTL back into the future** every 5 seconds; the
instant it stops (death), the clock runs out and the key vanishes.

This is why the heartbeat is a far better death signal than a timestamp we'd
compare ourselves: there's no clock-skew between the writer and a reader, no "is
now() - last_seen > threshold" arithmetic, and no stale value left lying around.
The key is either there (alive) or not (dead), and Redis owns that transition.

### 4.4 Atomic `SET NX EX` — the lock that makes multi-supervisor safe

`try_acquire_recovery_lock` (`registry.py:285-286`) runs:

```python
client.set(lock_key, "1", nx=True, ex=max(1, ttl_seconds))
```

`NX` means "set **only if the key does not exist**"; `EX` attaches a TTL in the
same command. Redis executes each command **atomically** — it is single-threaded
for command execution, so there is no window between "check if it exists" and
"set it" where a second client could interleave. Exactly one caller's `SET NX`
returns success (the key was created); every other caller gets `nil`/`False`
because the key already exists. That is a textbook distributed lock:

- Two supervisor replicas both detect the same dead task in the same window.
- Both call `SET lock:<id> 1 NX EX 60`.
- Redis serializes them: the first wins (`True`), the second loses (`False`).
- The loser skips the task (`supervisor.py:256-258`), trusting the winner to
  handle it.
- The `EX 60` ensures the lock self-releases even if the winner then crashes —
  no deadlock, the next scan can re-acquire.

We never need `DEL` to release the lock; the TTL is the release. This is
deliberately the same best-effort style as the rest of the system — if Redis is
unreachable the call returns `False` (`registry.py:287-291`), so a supervisor
that can't take the lock simply skips, which is the safe outcome.

### 4.5 Why "best-effort" is safe: failure returns a safe default

Every registry function wraps its Redis calls in `try/except` and, on failure,
returns the **safe** value:

- writes (`register`, `refresh_heartbeat`, `deregister`, `grant_grace`,
  `persist_payload`) → silent no-op;
- `list_tracked` → `[]` (so the scan iterates nothing);
- `get_payload` → `None`;
- **`is_alive` → `True`** (`registry.py:194-203`) — the critical one: "if I
  can't read the heartbeat, assume the task is alive," so a Redis blip can never
  cause a *false* death detection and a spurious requeue;
- `try_acquire_recovery_lock` → `False` — "if I can't lock, don't act."

The bias is always toward **inaction under uncertainty**, which is why a Redis
outage degrades to "recovery pauses," never "recovery does something wrong"
(Scenario E below).

---

## 5. The two halves: worker heartbeat & supervisor scan

### Half A — the worker side (proof of life)

Wired by `enable(app)` (`__init__.py:32-54`), which connects three Celery signal
handlers with `weak=False` (so they aren't garbage-collected):

- `task_prerun` → `on_task_prerun`
- `task_postrun` → `on_task_postrun`
- `task_revoked` → `on_task_revoked`

**On `task_prerun`** (`heartbeat.py:58-89`), fired inside the worker just before
your task body runs:

1. `registry.register(...)` (`registry.py:107-144`):
   - reads any existing payload to **preserve the `retries` count** (so a
     re-dispatched task keeps its budget — idempotent across requeues);
   - writes `payload:<id>` (TTL 24h), `SADD`s the id to `index`, writes
     `hb:<id>` (TTL 15s).
2. starts a **daemon thread** (`_beat_loop`, `heartbeat.py:49-55`) that every 5s
   calls `registry.refresh_heartbeat(task_id)`.

`refresh_heartbeat` (`registry.py:147-164`) re-stamps `hb:<id>` to TTL 15s **and**
bumps `payload:<id>`'s expiry back to 24h (edge case C — a task running >24h
keeps a recoverable payload as long as it's alive; only the *expiry* is bumped,
never the payload *value*, which the supervisor may have rewritten).

The mechanism of detection: the heartbeat thread lives **inside the worker
process**. When the pod is `SIGKILL`ed, the process — and therefore the thread —
vanishes instantly. Nobody refreshes `hb:<id>`. ~15 seconds later Redis expires
it (§4.3). The task is now provably "tracked but no heartbeat."

**On `task_postrun`** (`heartbeat.py:92-104`), fired after the task reaches any
terminal state: stops the heartbeat thread and `deregister`s — deletes all three
keys. The supervisor will never see it again.

**On `task_revoked`** (`heartbeat.py:107-124`), edge case E: a revoked task (user
cancellation) may never reach `postrun`, so this handler reads `request.id`,
stops the local thread, and deregisters immediately — closing the window where
the supervisor could mistake a deliberately-cancelled task for a dead one.

### Half B — the supervisor side (the watchdog)

`scan_and_recover(app)` (`supervisor.py:226-280`) is one pass. For **every**
`task_id` in `index`:

```
checked += 1
if is_alive(task_id):                 # hb:<id> still exists → worker fine
    alive += 1; continue
payload = get_payload(task_id)
if payload is None:                    # hb gone AND payload gone → orphan
    deregister(task_id); orphaned += 1; continue
if not try_acquire_recovery_lock(...): # another supervisor owns it (edge B)
    skipped_locked += 1; continue
if _is_cancelled(_calculation_id_of(payload)):   # edge A
    _finalize_cancelled(...);  cancelled += 1; continue
if payload["retries"] < max_retries:   # default 4
    _requeue(app, task_id, payload);  requeued += 1
else:
    _give_up(app, task_id, payload);  gave_up += 1
```

It runs in a dedicated always-on pod via `run_forever()` (`supervisor.py:292-302`)
— a `while not _stop.wait(interval)` loop, default 10s. The management command
`run_recovery_supervisor` wraps it with SIGTERM/SIGINT → `stop_forever()` for
graceful shutdown, and `--once` for a single pass.

### The requeue itself (`_requeue`, `supervisor.py:69-110`)

The ordering is deliberate (edge case D):

```python
incremented = dict(payload); incremented["retries"] += 1
queue = incremented["queue"] or _default_queue()
registry.grant_grace(task_id, 60)                    # 1. delay next detection
app.send_task(name, args, kwargs,
              task_id=task_id, queue=queue)           # 2. re-dispatch (may raise)
registry.persist_payload(task_id, incremented)       # 3. commit retries++ AFTER send
```

- **Same `task_id`** is the linchpin: the result-backend key is keyed by
  `task_id`, so a parent blocked on `AsyncResult(task_id).get()` receives the
  result of the *recovered* run and never knows recovery happened.
- **grace before send** re-stamps `hb` to a 60s TTL so the next scan (10s later)
  sees a live heartbeat and doesn't double-requeue before the new worker starts.
- **persist after send**: if `send_task` raises (broker down), `retries++` is
  never committed — the budget isn't burned, and the next pass retries cleanly.

---

## 6. Walkthrough: a normal calculation

Defaults: interval 5s, hb TTL 15s.

```
t=0     Backend dispatches calc task → message on queue <inst>:<inst>, db1.
        KEDA sees listLength≥1, spawns a worker pod.
t≈2     Worker picks up the message. task_prerun fires:
          index += task_id
          payload:<id> = pickle({...retries:0}), TTL 24h
          hb:<id> = "1", TTL 15s
          daemon heartbeat thread starts
t=2..N  every 5s: refresh hb→15s, bump payload→24h. calculate_hook runs; row IN_PROGRESS.
t=N     task finishes. task_postrun fires:
          stop thread; deregister → DEL index member, payload, hb
        row flips IN_PROGRESS → SUCCESS (normal app logic, not recovery).
```

Supervisor passes during `t=2..N` all see `is_alive == True` → counted `alive`,
no action. After `t=N` the id isn't in `index` at all. Recovery never
intervened. Overhead: one Redis write every 5s.

---

## 7. Scenario A: a worker dies mid-calculation

A worker is 4 minutes into a heavy calculation; the GKE node is reclaimed; the
pod gets `SIGKILL`.

```
t=0      Worker SIGKILLed. Process gone → heartbeat thread gone.
         hb:<id> last stamped <5s ago, TTL 15s. payload present (TTL 24h, retries 0).
         task_id still in index. DB row still IN_PROGRESS.
         (ACKS_LATE + visibility_timeout=inf ⇒ Celery will NEVER redeliver.)
t≈0..15  supervisor passes still see hb alive → "alive", skip.
t=15     hb:<id> EXPIRES. Task is now "tracked but no heartbeat".
t≤25     next supervisor pass:
           is_alive → False; payload present (retries 0 < 4)
           lock acquired; not cancelled
           _requeue: grant_grace(60) → send_task(task_id=<id>) → persist_payload(retries:1)
         → message on queue ⇒ KEDA spawns a fresh worker pod.
t≈27     new worker picks up the SAME task_id. task_prerun fires:
           register() PRESERVES retries:1; fresh heartbeat thread starts.
         calculation re-runs from the top; row (re)set IN_PROGRESS.
t=M      finishes. postrun deregisters. row → SUCCESS.
         A parent on AsyncResult(<id>).get() unblocks here — it never knew worker 1 died.
```

**Worst-case detection-to-requeue latency** = hb TTL (15s) + one scan interval
(≤10s) ≈ **up to 25s** (tunable, §15). Note the task **re-runs from scratch**,
not from a checkpoint — recovery is at task granularity. That's why it's a
"requeue," not a "resume."

This exact cycle is what the local harness in §14 reproduces and proves.

---

## 8. Scenario B: repeated death → budget exhausted → ABORTED

Suppose the task is *poison*: every worker that runs it dies (e.g. it allocates
40 GB and gets OOM-killed every time). Without a bound this is an infinite
requeue loop spawning infinite pods.

The budget is `LEX_TASK_MAX_RETRIES = 4`. Each `_requeue` increments `retries`
(committed only on successful dispatch). After the 4th requeue
`payload["retries"] == 4`, so `retries < max_retries` is false →
`_give_up` (`supervisor.py:207-223`):

```python
exc = MaxRequeueExceeded(task_id, retries)
app.backend.mark_as_failure(task_id, exc)     # FAILURE result keyed by task_id
_abort_calculation_rows(payload, str(exc))    # IN_PROGRESS rows → ABORTED
registry.deregister(task_id)                  # stop tracking
```

- `mark_as_failure` writes a real FAILURE result, so **any parent on `.get()`
  unblocks** with `MaxRequeueExceeded` instead of hanging (`exceptions.py:6-19`).
- `_finalize_calculation_rows` (`supervisor.py:154-171`) flips **only rows still
  in `IN_PROGRESS`** (guarded `.filter(pk=…, is_calculated=IN_PROGRESS).update(…)`)
  to `ABORTED`, and calls `update_calculation_status` so UI/audit see the
  terminal state. The guard means it never clobbers a row that meanwhile reached
  SUCCESS/ERROR by other means.
- Deregister removes all keys; no more pods spawned for this task.

`ABORTED` is a distinct status (`CalculationModel.py:122`) meaning "server-side
recovery gave up," separate from `ERROR` (the calc raised) and `CANCELLED` (user
stopped it).

---

## 9. Scenario C: user cancels while the worker is dead

A subtle interleaving: the user clicks Cancel at almost the same moment the
worker dies. The normal cancel path (`app.control.revoke`) can't reach a dead
worker, and the row is stuck IN_PROGRESS. We must **not** re-run a calculation
the user explicitly cancelled.

Edge case A handles it. The cancellation subsystem (`cluster_cancel_index`, from
the earlier cascade-cancellation work) writes a marker
`<inst>:lex:calc:cancelled:<calculation_id>` (`cluster_cancel_index.py:150-158`).
In `scan_and_recover`, **before** deciding to requeue (`supervisor.py:261-264`):

```python
if _is_cancelled(_calculation_id_of(payload)):
    _finalize_cancelled(task_id, payload)   # rows → CANCELLED, no requeue, deregister
    cancelled += 1; continue
```

`_calculation_id_of` (`supervisor.py:124-133`) digs the id out of
`payload["kwargs"]["context"]["calculation_id"]`. `_is_cancelled`
(`supervisor.py:174-189`) is defensive on every front: a missing module or Redis
error degrades to `False`, falling back to the normal requeue path rather than
wrongly killing a live calc. `_finalize_cancelled` flips rows to `CANCELLED`
(not ABORTED) and does **not** write a FAILURE result — the user asked to stop;
that isn't a failure.

---

## 10. Scenario D: the backend dies

The backend originates calculations and often waits on `AsyncResult.get()`. It's
a KEDA ScaledObject 0..1 (scales to 0 when idle).

- **D1 — backend dies, worker fine.** The worker keeps heartbeating, the calc
  completes, `postrun` deregisters, row → SUCCESS. Recovery does nothing —
  nothing died from *its* view. When the backend returns, the result is already
  in the result backend (keyed by `task_id`) and the row is already SUCCESS.
  **Nothing lost.** The supervisor is a *separate* pod, so it keeps watching
  regardless of backend state — that's exactly why it isn't co-located with the
  backend.
- **D2 — backend was the only waiter and it dies.** The worker still finishes and
  writes the result; `CELERY_RESULT_EXPIRES = 3600` (`settings.py:415`) keeps it
  for an hour. If the backend never returns, the result expires — but the **DB
  row is the source of truth** and it's SUCCESS. Recovery isn't involved because
  no worker died.

Key point: **recovery liveness is decoupled from the backend.** A backend outage
never blinds recovery.

---

## 11. Scenario E: Redis dies

The most important robustness property, because Redis is *both* the broker *and*
the recovery store. The whole subsystem is built to **fail inert**, never fail
dangerous (see §4.5).

During a Redis outage:

- Workers run normally; heartbeat writes silently fail (debug log); tasks still
  complete.
- The supervisor's `list_tracked()` returns `[]`; each pass does nothing.
- `is_alive` returns `True` and `try_acquire_recovery_lock` returns `False`, so
  **no false requeues and no false ABORTs** are possible.

When Redis returns, the broker resumes. Tasks still running re-create their `hb`
on the next 5s beat. The one honest edge: a task whose `payload` key *expired*
during an outage **longer than 24h** becomes unrecoverable — but that requires a
>24h Redis outage, far beyond normal failure. For ordinary blips
(seconds–minutes) nothing has expired yet, so everything resumes cleanly. This
is the same best-effort contract `cluster_cancel_index` uses — recovery never
raises into the calculation path.

---

## 12. Scenario F: the supervisor dies, or there are two

- **Supervisor dies.** It's a Deployment `replicas: 1`, so K8s restarts it. While
  down, dead tasks aren't detected — but nothing is lost: `payload` keys persist
  24h and `index` membership persists, so the new supervisor's first scan catches
  every accumulated dead task. Detection is *delayed*, never *defeated*.
- **Two supervisors overlap** (e.g. during a `Recreate` rollout). Edge case B:
  the per-task `SET NX EX` lock (§4.4) guarantees only one acts on a given dead
  task per window; the other counts it `skipped_locked`. Even N supervisors never
  double-requeue. The chart uses `replicas:1` + `Recreate` to avoid overlap, but
  the lock makes it *correct* regardless.

The sweep task is also in `_UNTRACKED_TASK_NAMES` (`heartbeat.py:29`), so a
sweep-running worker that itself dies never gets *its sweep* requeued — no
self-amplification.

---

## 13. Supervisor vs. Celery beat

Both drivers run the **exact same engine** — `scan_and_recover()`. The difference
is entirely in *what triggers it*.

- **Beat path.** `CELERY_BEAT_SCHEDULE["lex-celery-recovery-sweep"]`
  (`settings.py:522`) enqueues the `sweep_dead_workers` task every interval; **a
  worker** dequeues and runs one pass. Recovery is itself a Celery task.
- **Supervisor path.** A dedicated always-on pod runs `run_forever()`, calling
  `scan_and_recover()` directly, in-process, on a loop. No broker round-trip, no
  worker involved.

### Why the supervisor wins here: the circularity problem

In our cluster, **workers scale to 0** (KEDA ScaledJob) and **the backend scales
to 0** (KEDA ScaledObject 0..1). The beat path makes recovery *depend on a worker
to run it* — but recovery exists precisely because workers die or aren't there.
That's a chicken-and-egg loop:

> To detect a dead worker, beat needs a worker to run the sweep. To get a worker,
> the sweep message must sit in the queue long enough for KEDA to spawn a pod. So
> every interval KEDA cold-starts a fresh worker pod just to run a ~50ms scan,
> then the pod dies — **pod churn every 10 seconds, forever, even when nothing is
> wrong.** And beat itself must run somewhere; co-located with the backend, it
> stops when the backend scales to 0 → no sweeps at all.

The supervisor breaks the circle by being independent of both: always on, reads
Redis directly, never needs a worker to detect deaths.

| | Beat-driven sweep | Dedicated supervisor |
|---|---|---|
| Runs `scan_and_recover` | via a Celery worker | directly, in-process |
| Needs a live worker to detect deaths | **yes** (circular) | no |
| Needs the backend up | usually (beat lives there) | no |
| Detection latency | scan interval **+ KEDA poll (5s) + pod cold-start (20–60s)** | scan interval only |
| Idle cost | "free" but constant pod churn | one small pod (50m / 128Mi) always on |
| Queue pollution | piles up sweep messages during downtime (needs the `expires` band-aid, `settings.py:515-528`) | nothing to pile up |
| Failure mode | if no worker can run the sweep, recovery silently stops | K8s restarts the singleton; backlog caught next scan |

### When beat *is* the right choice

Beat isn't wrong everywhere — it's wrong for *this* scale-to-0 topology. With
**always-on, statically-sized workers** (no KEDA, workers never at 0), the beat
path is genuinely nice: zero extra pods, naturally distributed, reuses existing
infra. That's why the code keeps **both** paths — they call the identical
function. Running both together is safe (the per-task lock prevents double-act)
but wasteful; the supervisor is the canonical driver for the cluster.

---

## 14. Testing it locally

Unit tests (`lex/tests/unit/infra/test_celery_recovery.py`, 11 tests) cover the
logic, but they can't simulate a real `SIGKILL`. To *prove* the requeue path you
need a real worker process you can kill. A self-contained harness lives at
`scripts/recovery_smoke/`.

### What the harness does

`harness.py` registers a plain `recovery_smoke.sleep` task — **no
CalculationModel, no DB** — so it exercises *only* the recovery machinery
(heartbeat → dead detection → same-`task_id` requeue → bounded budget), nothing
else. `run_test.sh` orchestrates the full cycle automatically.

### Prerequisites that make it a *faithful* test

- `CELERY_ACTIVE=TRUE` — otherwise tasks run synchronously, there's no worker to
  kill, and the registry no-ops.
- Real Redis on `:6379` (broker uses db 1).
- `-P solo` worker pool — so the task runs in the worker's **main** process and
  `kill -9` is a faithful pod-SIGKILL (with the default prefork pool you'd have
  to kill the child `ForkPoolWorker`, not the parent).

### Run it

```bash
# one-shot automated proof
bash scripts/recovery_smoke/run_test.sh

# or interactive, 4 terminals (env at the top of harness.py):
#  A: celery -A scripts.recovery_smoke.harness worker -P solo -Q celery -l info
#  B: lex run_recovery_supervisor --interval 3
#  C: python scripts/recovery_smoke/harness.py enqueue 60     # prints task_id
#  D: python scripts/recovery_smoke/harness.py watch          # live key viewer
# then `kill -9` the worker in A and watch B requeue the SAME id, C/D confirm.
```

### What a passing run looks like (real output)

```
17:37:24  worker A picks up task, sleeping 40s        (hb_ttl=3s → alive)
17:37:30  kill -9 worker A     ← simulated pod SIGKILL, mid-task
17:37:32  supervisor: checked=1 alive=1               (hb not expired yet)
17:37:36  supervisor: "requeued dead task 356301d5… attempt 1/2 on queue celery"
17:37:39  supervisor: alive=1   ← grace window prevents double-requeue (edge D)
17:37:42  start worker B        ← simulates KEDA bringing a fresh worker up
17:37:46  worker B: "Task recovery_smoke.sleep[356301d5…] received"  ← SAME id, retries=1
```

Every link fires: heartbeat expiry → dead detection → same-`task_id` requeue →
grace window → fresh worker re-runs it. `visibility_timeout=inf` meant Celery did
**not** redeliver on its own — the supervisor did.

### The one local≠cluster gap

This harness proves the **requeue transport**. It does **not** exercise the
`IN_PROGRESS → ABORTED/CANCELLED` DB-row flow, because the plain task has no
`CalculationModel`. To test that half, run the same kill against the `SlowCalc`
model in `LexStressLab/D_WorkerRecovery/SlowCalc.py` inside a project with a DB —
then you also see the row finalize on budget exhaustion.

---

## 15. Configuration knobs and latency math

| Setting | Default | Controls |
|---|---|---|
| `LEX_TASK_RECOVERY_ENABLED` | `true` | Master switch (handlers + thread + sweep). |
| `LEX_TASK_HEARTBEAT_INTERVAL` | `5` s | How often the worker re-stamps `hb`. |
| `LEX_TASK_HB_TTL_MULTIPLIER` | `3` | `hb` TTL = interval × this = 15s. Lower = faster detection, less tolerance for a slow beat. |
| `LEX_TASK_SUPERVISOR_SCAN_INTERVAL` | `10` s | Supervisor loop period (and beat period). |
| `LEX_TASK_MAX_RETRIES` | `4` | Requeue budget before ABORTED. |
| `LEX_TASK_REQUEUE_GRACE_SECONDS` | `60` s | Post-requeue grace `hb` TTL + lock TTL. |
| `LEX_TASK_PAYLOAD_TTL_SECONDS` | `86400` (24h) | Max recoverable task age. |

`LEX_TASK_REQUEUE_GRACE_SECONDS` and `LEX_TASK_PAYLOAD_TTL_SECONDS` are read with
their code defaults; set them as env vars to override.

**Detection latency** = `hb TTL` + (up to) `scan interval`
= `interval × multiplier` + `scan interval`. Defaults: 15 + 10 = **up to ~25s**.
Lower all three to detect faster, at the cost of more Redis writes and less
tolerance for a worker that's merely paused (e.g. GC, a slow syscall) rather than
dead. The multiplier is the safety margin: at multiplier 3, the worker can miss
**two** consecutive beats and still be considered alive.

---

## 16. Glossary

- **Heartbeat key** — `hb:<id>`, a short-TTL Redis string the running worker
  re-stamps; its expiry is the death signal.
- **Payload** — `task:<id>`, the base64-pickled `{name,args,kwargs,queue,retries}`
  needed to re-dispatch the task.
- **Index** — the Redis SET of all tracked `task_id`s.
- **Same-`task_id` requeue** — re-dispatching with the original id so the
  result-backend key (and any `.get()` waiter) resolves transparently.
- **Budget** — `LEX_TASK_MAX_RETRIES`; requeues allowed before `ABORTED`.
- **Grace window** — short `hb` TTL granted just before a requeue so the new run
  starts before the next scan re-detects the task.
- **ABORTED** — terminal status set when recovery gives up (distinct from
  `ERROR` = the calc raised, and `CANCELLED` = the user stopped it).
- **Supervisor** — the always-on process looping `scan_and_recover()`.
- **KEDA** — the autoscaler that spawns worker pods from broker queue depth; the
  reason workers (and the backend) scale to zero, and the reason the supervisor
  must be its own always-on pod.

---

*See also:* [`README.md`](README.md) (operator reference & deployment),
`lex/lex_app/celery_recovery/` (source), and
`lex/tests/unit/infra/test_celery_recovery.py` (unit tests).
