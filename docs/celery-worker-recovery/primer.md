# Worker recovery, from scratch

A ground-up explanation of what worker recovery is, why LEX needs it, how it is
built, what is currently broken, and what we could do about it.

**Who this is for:** someone who knows LEX as a product but has not worked with
Kubernetes, Redis, or Celery. Every concept is introduced before it is used, and
each one links to the official documentation so you can go deeper on anything
that matters to you. You do not need to read the linked material to follow the
argument — it is there so the argument is checkable.

**Sibling documents:** [README](README.md) is the terse reference for the
subsystem; [on-demand-recovery](on-demand-recovery.md) covers the specific change
to run the recovery pod only when needed. This page is the one to read first.

---

## Part 1 — The problem, in plain terms

A user opens LEX and clicks *Calculate*. That calculation might take seconds or
it might take an hour. Either way the user expects one of two endings: it
finishes, or it fails with a message.

There is a third ending we must never allow: it silently stops, and the row sits
at `IN_PROGRESS` forever. Nobody is told. The user comes back tomorrow and finds
a spinner.

Preventing that third ending is the entire job of worker recovery.

To understand *how* a calculation can silently stop, you need to know a little
about where it actually runs.

---

## Part 2 — Background you need

Five concepts. Skim them; come back when a later section leans on one.

### 2.1 Containers and Kubernetes

LEX does not run on "a server". It runs as a set of **containers** — packaged
programs — scheduled onto a pool of machines by **Kubernetes** (k8s), which
decides what runs where and restarts things that die.

The unit Kubernetes schedules is a **Pod**: one or more containers that live and
die together.

- 📖 [Kubernetes: Pods](https://kubernetes.io/docs/concepts/workloads/pods/)

A **Deployment** is a controller that keeps *N* copies of a Pod running. If one
dies, it makes another.

- 📖 [Kubernetes: Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)

The key thing to internalise: **a Pod can be destroyed at any moment, for reasons
that have nothing to do with your code.** The machine it was on can be reclaimed,
the cluster can be short on memory, a deploy can replace it. Recovery exists
because this is normal, not exceptional.

### 2.2 How Pods die — and why it matters so much

Not all deaths are equal. This distinction runs through the whole document.

**Graceful.** Kubernetes sends the process a `SIGTERM` signal — "please finish
up" — waits (by default 30 seconds), then force-kills. The program gets a chance
to clean up. Rollouts, `kubectl delete`, node drains, and planned evictions all
work this way.

**Abrupt.** The process is killed instantly with `SIGKILL`, or the machine simply
vanishes. No warning, no cleanup. Two common causes:

- **OOM kill** — the container used more memory than allowed, so the Linux kernel
  kills it. Instant, no signal.
- **Node loss** — the underlying machine disappears. Our worker machines are
  [**Spot VMs**](https://cloud.google.com/compute/docs/instances/spot): much
  cheaper, but Google can reclaim them.

- 📖 [Kubernetes: node-pressure eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/)

**Who gets OOM-killed first** is decided by the container's **QoS class**, which
comes from whether it declared how much memory it needs:

| Class | Declared | Killed |
|---|---|---|
| `Guaranteed` | requests = limits | last |
| `Burstable` | requests set | middle |
| `BestEffort` | **nothing declared** | **first** |

- 📖 [Kubernetes: Pod QoS classes](https://kubernetes.io/docs/concepts/workloads/pods/pod-qos/)
- 📖 [Kubernetes: managing container resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)

Remember `BestEffort`. It comes back in Part 6 as a live problem.

### 2.3 Celery: how a calculation actually runs

When you click *Calculate*, the web backend does not run the calculation. It
writes a **message** describing the work onto a queue, and a separate process —
a **worker** — picks it up and runs it. The library that does this is
[**Celery**](https://docs.celeryq.dev/en/stable/).

The queue lives in a **broker**. Ours is Redis (next section).

- 📖 [Celery: workers](https://docs.celeryq.dev/en/stable/userguide/workers.html)

Two settings matter later:

- **`task_acks_late = True`** — a worker confirms ("acknowledges") a message only
  *after* finishing the work, not when it picks it up. So if the worker dies
  mid-calculation the message is still considered undelivered.
- **`visibility_timeout = inf`** — Celery's Redis transport can automatically
  re-deliver a message that has not been acknowledged within some time. We have
  **deliberately disabled** that by setting the timeout to infinity.

  Why disable a built-in recovery mechanism? Because it is time-based, and a LEX
  calculation can legitimately run for hours. Any finite timeout would eventually
  re-deliver a message for work that is still running, and the calculation would
  execute twice. Turning it off is correct — but it means *we* now owe the system
  a recovery mechanism, which is what Part 3 is.

- 📖 [Celery: using Redis as a broker (incl. visibility timeout)](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- 📖 [Celery: configuration reference](https://docs.celeryq.dev/en/stable/userguide/configuration.html)

### 2.4 Redis (and Dragonfly)

**Redis** is a database that keeps everything in memory, which makes it very
fast. It stores simple data structures — strings, lists, sets, hashes — rather
than tables.

- 📖 [Redis: data types](https://redis.io/docs/latest/develop/data-types/)

Three of its features are load-bearing here:

- **Keys can expire.** Set a key with a 15-second lifetime and it deletes itself.
  This is how the heartbeat in Part 3 works.
- **Numbered databases.** One Redis server holds several numbered namespaces
  (`0`, `1`, `2`, …). We use `1` for the Celery broker and `2` for websockets.
- **Persistence is optional.** Memory is lost when the process stops, so Redis
  can write a **snapshot** to disk and reload it on start.

- 📖 [Redis: persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)

We do not actually run Redis — we run [**Dragonfly**](https://www.dragonflydb.io/docs/getting-started),
a drop-in replacement that speaks the same protocol. Everything above applies;
only the configuration flags differ.

- 📖 [Dragonfly: flags](https://www.dragonflydb.io/docs/managing-dragonfly/flags)
- 📖 [Dragonfly: snapshots and backups](https://www.dragonflydb.io/docs/managing-dragonfly/backups)

### 2.5 KEDA: running things only when needed

Workers are expensive and idle most of the time, so we do not keep them running.
[**KEDA**](https://keda.sh/docs/latest/concepts/scaling-deployments/) watches an
external signal — for us, *how many messages are on the queue* — and starts or
stops pods to match. No work, no pods, no cost.

- 📖 [KEDA: scaling Deployments (ScaledObject)](https://keda.sh/docs/latest/concepts/scaling-deployments/)
- 📖 [KEDA: scaling Jobs (ScaledJob)](https://keda.sh/docs/latest/concepts/scaling-jobs/)
- 📖 [KEDA: the Redis Lists scaler we use](https://keda.sh/docs/latest/scalers/redis-lists/)

One detail that shapes our design: KEDA's Redis scaler measures the **length of a
list** (`LLEN`). It cannot measure the size of a *set*. Part 4 explains why that
forced an extra data structure.

---

## Part 3 — How recovery works today

Now the actual mechanism. Code lives in `lex/lex_app/celery_recovery/`.

### The core idea: heartbeats

While a worker runs a task, it repeatedly re-stamps a Redis key that expires
after a few seconds — a **heartbeat**. Think of it as saying "still alive" every
5 seconds into a key that forgets after 15.

If the worker dies abruptly, nobody re-stamps it, and the key evaporates.

Separately, the system keeps a **registry**: a list of every task believed to be
in flight, along with enough information to re-run it.

That gives an unambiguous fingerprint of a dead worker:

> **the task is still in the registry, but its heartbeat is gone.**

No timers, no guessing about how long a calculation *should* take — which is
exactly the trap `visibility_timeout` falls into.

### The three keys

For every in-flight task, Redis holds:

| Key | Type | Purpose |
|---|---|---|
| `…:lex:recover:index` | Set | every task id currently in flight |
| `…:lex:recover:task:<id>` | String | how to re-run it, plus a retry counter |
| `…:lex:recover:hb:<id>` | String, short TTL | the heartbeat |

All keys are prefixed with the instance identifier so instances sharing a Redis
cannot collide.

### The supervisor

A small always-on process wakes every 10 seconds and, for each task in the
registry:

1. **Heartbeat alive?** Leave it alone.
2. **Heartbeat gone?** The worker died. Check whether the work actually finished
   anyway (a worker can be killed *after* writing its result). If it did,
   just stop tracking it.
3. **Genuinely unfinished?** Re-dispatch the same task, up to 4 times.
4. **Out of retries?** Mark the calculation `ABORTED` and write a failure result,
   so anything waiting on it stops waiting.

Step 2 matters more than it looks: re-running finished work could turn a recorded
`ERROR` into a `SUCCESS`, silently rewriting history.

### Ownership starts at dispatch

An earlier version only registered a task once a worker *started* it. That left a
gap: between "the backend queued the work" and "a worker picked it up", the task
existed nowhere in the registry.

That gap caused a real production incident (instance 1410). The cluster was full,
so worker pods sat waiting for capacity. Meanwhile the backend restarted, and on
startup LEX sweeps rows stuck at `IN_PROGRESS` and marks them `ABORTED`. A
perfectly healthy, merely-queued calculation was killed because nothing claimed
ownership of it.

The fix: register at **dispatch**, before any worker exists. A task claimed that
way carries `status="dispatched"` and no heartbeat, because a missing heartbeat
proves nothing for a task that has not started. For those the supervisor checks
the broker queue instead: message still there → wait; message gone → re-dispatch.

---

## Part 4 — Running the supervisor only when needed

The supervisor is one small pod per instance, running 24/7, doing nothing
whenever no calculation is running. Across ~10 instances that is a permanent
reservation for a guard with nothing to guard.

So: make it on-demand, using KEDA. But KEDA needs a signal, and the natural one —
"is the registry non-empty?" — is a **set**, which the Redis scaler cannot read.

Hence a fourth key: `…:lex:recover:inflight`, a **list** kept as an exact mirror
of the registry set. KEDA watches its length. Non-empty exactly while work is in
flight.

The two writes (add to set, add to list) happen inside a single Lua script so
they cannot disagree — if the list missed an entry, the pod would never start and
that task would be unguarded.

The full design, including twelve edge cases with verdicts, is in
[on-demand-recovery.md](on-demand-recovery.md).

---

## Part 5 — Where the pieces live

```
┌─ per customer instance ──────────────────────────────────┐
│                                                          │
│  backend (always on) ── queues work ──┐                  │
│                                       ▼                  │
│                              ┌──────────────────┐        │
│                              │  Redis/Dragonfly │        │
│                              │  DB 1: broker    │        │
│                              │       + registry │        │
│                              │  DB 2: websockets│        │
│                              └──────────────────┘        │
│                                   ▲        ▲             │
│                     heartbeats ───┘        └─── watches  │
│                          │                        │      │
│                    workers (0→21, KEDA)      supervisor  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

Everything is deployed by a [Helm](https://helm.sh/docs/topics/charts/) chart,
driven by Terraform, driven by the instance controller. Three layers — which
matters because a setting has to be plumbed through *all* of them to be reachable
(see problem 6).

---

## Part 6 — Current problems

Ordered by how much they should worry you.

### Problem 1 — Redis persistence is half-configured

> **This section corrects an earlier claim.** We previously wrote that Dragonfly
> has no persistence at all. That was wrong — inferred from configuration rather
> than checked. A colleague's in-cluster testing disproved it. The corrected
> picture is below.

Dragonfly **does** save to disk, by accident of defaults rather than by
configuration. Its `--dir` option defaults to the process's working directory;
the container image sets that to `/data`; `/data` is where the disk is mounted.
So it writes there without anything asking it to.

- 📖 [Docker: `WORKDIR`](https://docs.docker.com/reference/dockerfile/#workdir)
- 📖 [Kubernetes: persistent volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)

But there is **no periodic save**. The only save is on graceful shutdown. So:

| How it died | Snapshot? | Result |
|---|---|---|
| Graceful (rollout, delete, drain) | yes | reloads intact |
| **Abrupt (OOM, node loss)** | **no** | reloads the *last graceful* snapshot — possibly the pod's entire lifetime old |

**Why an old snapshot is dangerous.** The same database holds the Celery queue.
Restoring an old copy of the queue brings back messages for work that has since
been completed. Because `task_acks_late` means an acknowledgement is the only
thing that removes a message, restoring un-does those acknowledgements — and the
work runs a second time. For a financial calculation that creates records, running
twice is a genuine data problem.

Which leads to the uncomfortable conclusion: **shutdown-only saving is the worst
of the three possible positions.**

- Never save → nothing is ever resurrected. Predictable.
- Save every 5 minutes → at most 5 minutes of work can be resurrected. Bounded.
- Save only at shutdown → an arbitrarily old queue can be resurrected. Unbounded.

Nobody chose this. It is what the defaults produced.

### Problem 2 — The Redis container is `BestEffort`

It declares no memory requests or limits, so it is **first in line to be OOM
killed** under memory pressure — which is exactly the death that writes no
snapshot. See §2.2.

We recently added an annotation (`safe-to-evict: "false"`) intended to protect
these pods, and it is worth being precise: that annotation tells the
[cluster autoscaler](https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/FAQ.md)
not to pick that node when shrinking the cluster. It does **not** stop the Linux
kernel OOM killer, and it does not stop a Spot VM being reclaimed. It closes one
door and leaves the two that matter open.

- 📖 [Kubernetes: cluster autoscaling](https://kubernetes.io/docs/concepts/cluster-administration/cluster-autoscaling/)

### Problem 3 — Snapshots accumulate forever on a 1 GiB disk

Every graceful shutdown writes a new snapshot file; nothing deletes the old ones.
One production instance has 152 files going back to September 2025.

When the disk fills, the shutdown save **fails silently**, and from then on every
restart falls back to the stale path from Problem 1. A slow leak that converts
the safe case into the dangerous one.

### Problem 4 — The queue and the registry cannot have different policies

Both live in Redis **database 1**. Persistence in Dragonfly is per *server*, not
per database.

They want opposite things:

- the **registry** wants to survive a crash, so recovery still works afterwards;
- the **queue** wants to be forgotten, so nothing is resurrected.

You cannot currently give them different answers. Any fix has to either accept
one side's loss or defuse the other side's hazard (see Solution C).

### Problem 5 — Double execution has a second, unrelated cause

[PR #648](https://github.com/ExcellenceCloudGmbH/lex-app/pull/648) documents a
real double-execution bug that needs no snapshot restore at all:

1. A calculation is queued while no worker is running.
2. The backend restarts; the startup sweep marks the row `ABORTED` — which also
   reopens the guard that stops a user starting the same calculation twice.
3. The user clicks again → a second message.
4. Workers start and drain **both**.

The hole is that the sweep abandons the row without cancelling the queued
message. Same symptom as the persistence hazard, different cause — and, usefully,
the same fix helps both (Solution C).

### Problem 6 — The on-demand supervisor is unreachable from the UI

The chart supports it. Terraform supports it. But the instance controller builds
its Terraform input without those fields, so it can never send them, and the
default (`beat`, the old always-on driver) always wins. This is why a test
instance still showed a `recovery-beat` pod after being "switched".

A stopgap tag with inverted defaults exists for evaluation. The real fix is to
add the two fields to the controller.

### Problem 7 — The old driver also runs the calendar

The always-on pod being replaced (`recovery-beat`) does a second job: it runs
[django-celery-beat](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html),
which fires **future-dated history activations**. Switch an instance to the
supervisor and those silently stop firing — no error, they just never happen.

This is why the change is opt-in per instance and not a fleet-wide default. It
stays that way until the scheduled work moves to a global scheduler.

### Problem 8 — Nothing detects any of the above

A failed save, a full disk, a stale restore, a resurrected task: all silent.

---

## Part 7 — Possible solutions

Four options. They are not alternatives to each other — the recommendation is a
sequence.

### A — Make Redis genuinely forgetful

Disable snapshots entirely, so nothing can ever be resurrected.

- **Good:** simple, predictable, matches what the recovery design already assumes.
- **Bad:** throws away something that currently works. Today a graceful restart
  preserves the registry; this would lose it every time.

### B — Make Redis properly durable

Add a periodic snapshot (`--snapshot_cron`, reachable through the chart).

- **Good:** bounds staleness to one interval; the registry survives crashes, so
  recovery keeps working through a Redis restart.
- **Bad:** makes resurrection *more frequent* — every abrupt death now replays up
  to one interval of completed work. On its own this trades one hazard for
  another.

### C — Make resurrection harmless, then choose freely ⭐

The insight: **double execution is not really a persistence problem.** It is a
missing check at the start of a task. A task that refuses to run when its
calculation is already finished makes a resurrected message a no-op.

Two pieces:

1. [#648](https://github.com/ExcellenceCloudGmbH/lex-app/pull/648)'s fix — when
   the startup sweep abandons a row, mark the queued message cancelled so the
   existing task-start checks reject it. Covers *aborted* calculations.
2. Extend that check to *completed* ones. Today a resurrected message for a task
   that succeeded passes straight through: the row says `SUCCESS`, no cancel
   marker exists, and nothing asks "has this already been done?"

Once both are in, B stops being a trade-off and becomes simply correct.

### D — Reach the dangerous state less often

Independent of A–C, and cheap:

| Change | Effect |
|---|---|
| Give Dragonfly resource requests/limits | Leaves `BestEffort` → far less likely to be OOM-killed. **Highest value single change.** |
| Bound snapshot retention, or grow the 1 GiB disk | Stops Problem 3 turning safe restarts into dangerous ones |
| Stop two pods sharing one disk during a rollout | Needs either a [StatefulSet](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/) or a `Recreate` strategy the chart does not currently expose |
| Alert on save failure and disk usage | Ends Problem 8 |

### Recommended order

1. **D now.** No design debate, no behaviour change, directly addresses the paths
   that actually lose data.
2. **C next.** The load-bearing change. It fixes a real bug on its own merits and
   removes the coupling that makes the persistence question hard.
3. **B then, deliberately.** With resurrection defused, periodic snapshots are
   straightforwardly good. Pick the interval against how much replay you can
   absorb.
4. **Not the status quo.** Shutdown-only saving, on a `BestEffort` container,
   with unbounded snapshots on a 1 GiB disk, is an accident rather than a
   decision.

Separately, and unrelated to persistence: add the two recovery fields to the
instance controller (Problem 6), and move scheduled activations off the beat pod
(Problem 7).

---

## Part 8 — If you want to go deeper

Roughly in the order that will make this document easiest to re-read.

**Kubernetes**
- [Pods](https://kubernetes.io/docs/concepts/workloads/pods/) ·
  [Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/) ·
  [StatefulSets](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)
- [Resource requests and limits](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/) →
  [QoS classes](https://kubernetes.io/docs/concepts/workloads/pods/pod-qos/) →
  [node-pressure eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/)
- [Persistent volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
- [Cluster autoscaling](https://kubernetes.io/docs/concepts/cluster-administration/cluster-autoscaling/) ·
  [autoscaler FAQ](https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/FAQ.md)

**Redis / Dragonfly**
- [Redis data types](https://redis.io/docs/latest/develop/data-types/) —
  sets and lists, and why the difference forced a fourth key
- [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
- [Dragonfly getting started](https://www.dragonflydb.io/docs/getting-started) ·
  [flags](https://www.dragonflydb.io/docs/managing-dragonfly/flags) ·
  [snapshots](https://www.dragonflydb.io/docs/managing-dragonfly/backups)

**Celery**
- [Workers](https://docs.celeryq.dev/en/stable/userguide/workers.html)
- [Redis broker, and the visibility timeout](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- [Configuration reference](https://docs.celeryq.dev/en/stable/userguide/configuration.html)
  (look up `task_acks_late`)

**KEDA**
- [Scaling Deployments](https://keda.sh/docs/latest/concepts/scaling-deployments/) ·
  [scaling Jobs](https://keda.sh/docs/latest/concepts/scaling-jobs/) ·
  [Redis Lists scaler](https://keda.sh/docs/latest/scalers/redis-lists/)

**Packaging**
- [Helm charts](https://helm.sh/docs/topics/charts/) ·
  [Dockerfile reference](https://docs.docker.com/reference/dockerfile/)
- [GCP Spot VMs](https://cloud.google.com/compute/docs/instances/spot) — why
  abrupt death is routine rather than rare

**In this repository**
- [README](README.md) — terse reference for the subsystem
- [on-demand-recovery](on-demand-recovery.md) — the scale-to-zero design, twelve
  edge cases, and the full durability analysis
- `lex/lex_app/celery_recovery/` — the code: `registry.py`, `supervisor.py`,
  `heartbeat.py`
