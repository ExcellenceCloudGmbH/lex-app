# Cluster-level scheduling — what each approach costs in infrastructure

**Companion to** `2026-08-04-global-scheduler-options.md`, which argues the
software design. This one answers the question that document skipped: *what
actually gets deployed, who holds which credential, and what does on-call see at
03:00 when it breaks.*

**Status:** decision support. Approach D is implemented (branch
`feat/bitemporal-activation-reconcile` + `feat/activation-reconcile-retire-beat`);
A, B and C are described as they would have to be built.

---

## 0. The cluster this has to live in

Every option below is judged against the environment as it actually is, not a
generic Kubernetes cluster. The relevant facts:

| | |
|---|---|
| Cluster | `lex-cluster-2`, GKE, `europe-west3` |
| Instances | **53**, all in one namespace (`default`) |
| …of which run Celery workers | **6** (KEDA `ScaledJob`, 0→21 pods) |
| Per instance | backend + frontend Deployments, own Dragonfly (1 replica, ClusterIP, 1 GiB PVC), own Postgres database on a shared server |
| Nodes | `e2-standard-4`, **spot**, pool capped at 15 |
| Backend scaling | optional KEDA `ScaledObject`, min 0, triggered by **nginx ingress request count** (`autoscaling` defaults false) |
| Recovery supervisor | Deployment, 128Mi request / **256Mi limit** |
| Deployment path | instance controller → Terraform → Helm, with `module_version` pinned **per instance** |

Three of these shape the answer more than the rest:

**One namespace, 53 tenants.** There is no namespace boundary to lean on. Any
RBAC grant a cluster-level component receives applies to every instance's
resources at once. "Read secrets in `default`" is not a scoped permission here —
it is fleet-wide.

**The backend can be scaled to zero.** On instances with `autoscaling: true`,
KEDA drops the backend to 0 replicas and the only trigger that brings it back is
**ingress traffic**. An in-cluster call does not pass through the ingress, so a
cluster-level component cannot wake a backend by calling it. This kills the
obvious callback design unless it is worked around explicitly.

**Per-instance pods are already proving fragile at this size.** Instance 1047's
recovery supervisor — a 256Mi pod doing almost nothing — is `OOMKilled` and has
restarted 68 times in five hours, because the Django app it boots settles just
above the limit. That is the empirical argument against "just add another small
pod per instance," and equally a warning that a single cluster-level pod needs
its sizing taken seriously rather than assumed trivial.

---

## 1. Approach A — cluster scheduler, instances register, scheduler calls back

### What gets deployed

```
namespace: default
  Deployment  lex-scheduler                 replicas: 1   (see HA below)
  Service     lex-scheduler                 ClusterIP     ← instances register here
  PVC or DB   lex-scheduler-timers          ← timers must survive a restart
  Secret      lex-scheduler-callback-keys   ← how it authenticates to 53 instances
  ServiceAccount + Role                     ← minimal, ideally none
```

Plus, in lex-app: a registration client (instance → scheduler at schedule time)
and a callback endpoint (scheduler → instance at fire time).

### The state problem nobody expects

A scheduler that forgets its timers on restart is useless, so it needs
persistence. That turns a "small stateless service" into a **stateful component
with a database, backups, and migrations** — the thing everyone assumes they are
avoiding by not choosing option C.

The alternative is for it to rebuild its timer set at boot by asking all 53
instances what they are waiting for, which reintroduces an inbound sweep and most
of option B's coupling.

### The callback problem

At fire time the scheduler has to reach the instance. Both routes have a defect:

**HTTP to the backend Service.** Works for all 53 instances, no broker needed:

```
http://lex-backend-<instance>.default.svc.cluster.local:7000/api/internal/activate
```

But on an `autoscaling: true` instance the backend may be at 0 replicas and the
KEDA trigger is ingress request count — an in-cluster call will not wake it. The
request fails, and the activation is late until traffic arrives by other means.
Fixing it means either a second KEDA trigger (a metrics-api scaler the scheduler
can poke) or pinning those instances to `minReplicas: 1`, which gives back the
saving that motivated backend autoscaling.

**Enqueue into the instance's broker.** Wakes a worker through the existing KEDA
`ScaledJob` trigger, so scaling works. But it needs each instance's Redis
password — 53 credentials — and only works for the **6** instances that run
Celery. It solves the wrong 11% of the fleet.

### Credentials

The scheduler must prove identity to 53 instances. Practical options, all
unattractive at this scale:

- **53 API keys mounted from `app-env-<instance>`** — requires `secrets get`
  across the namespace, which is exactly option B's grant with extra steps;
- **one shared signing key** — the scheduler signs a callback, instances verify.
  Better, but now one key compromise authorises calls into every instance;
- **mTLS / SPIFFE** — correct, and a significantly larger project than the
  scheduler itself.

### Failure modes

| Failure | Effect |
|---|---|
| Scheduler pod down | No activation fires on **any** instance until it returns |
| Scheduler DB/PVC lost | Every pending timer lost fleet-wide; no way to rebuild without sweeping instances |
| Callback fails (backend at 0) | That instance's activation is late, silently |
| Instance deleted | Orphan timers accumulate; needs a TTL or a deregistration hook |
| Node preempted (spot) | Rescheduled; timers survive only if the PVC does — and RWO on a zonal disk constrains where it can land |

### Ops burden

A new stateful service with its own backups, its own alerts, its own dashboard,
and a new failure mode for on-call to learn. The registration protocol becomes a
compatibility surface: an instance on an older lex-app that does not register is
silently unscheduled, and nothing surfaces that.

**Verdict:** the cleanest of the three cluster-level designs, and still a
genuinely large project once state, credentials and the scale-to-zero interaction
are priced in.

---

## 2. Approach B — scheduler polls every instance's database

### What gets deployed

```
namespace: default
  Deployment      lex-scheduler       replicas: 1
  ServiceAccount  lex-scheduler
  Role/RoleBinding: secrets [get, list]   ← the whole story is in this line
```

No lex-app change at all. That is the real attraction and it should be stated
honestly: this is the only option deployable against instances as they are today,
with no release, no migration, and no coordination.

### Why that RBAC line is the whole story

To reach 53 databases the scheduler needs 53 sets of credentials, which live in
the per-instance `app-env-<instance>` Secret. Granting `secrets get` in
`default` does not grant database credentials — it grants **every key in every
instance's secret**:

```
DJANGO_SECRET_KEY          POSTGRES_USERNAME / POSTGRES_PASSWORD
DJANGO_SUPERUSER_PASSWORD  REDIS_USERNAME / REDIS_PASSWORD
LEX_AI_METAGPT_LLM_CREDENTIALS
SHAREPOINT_API_CERTIFICATE_THUMBPRINT
```

A component whose entire job is knowing what time it is would hold the session
signing key, database superuser credentials, and the SharePoint certificate
thumbprint for all 53 customers. Any RCE, any dependency compromise, any logging
mistake that dumps its environment, is a fleet-wide credential incident.

There is no scoped version of this in a single namespace. Kubernetes RBAC cannot
express "these 53 secrets but not those" without naming each one, and the list
changes every time an instance is created.

### Operational reality

- 53 idle Postgres connections held against a shared server, from a pod that
  needs almost nothing from them; connection-pool tuning becomes its problem.
- A locked or slow instance database stalls the poll loop for everyone unless
  the loop is carefully concurrent — an easy thing to get wrong and a hard thing
  to notice.
- Credential rotation on any instance must be mirrored into the scheduler's
  refresh path, or that instance silently stops being scheduled.
- Debugging is cross-tenant by nature: reading the scheduler's logs means seeing
  identifiers from every customer.

### Failure modes

| Failure | Effect |
|---|---|
| Scheduler down | No activations anywhere |
| One instance DB slow/locked | Poll loop stalls → delays other tenants |
| Credential rotated | That instance silently unscheduled |
| Scheduler compromised | **Fleet-wide data and credential exposure** |

**Verdict:** cheapest to ship, most expensive to own. Every incident this quarter
has been about blast radius; this creates the largest one available and puts it
in a component that does not need any of that access to do its job.

---

## 3. Approach C — central schedule store

### What gets deployed

```
namespace: default
  Deployment  lex-scheduler          replicas: 1
  Database    scheduler DB (new Cloud SQL DB or a DB on the shared server)
              + backups, + migrations, + monitoring
  Secret      per-instance write credentials to that DB (53 roles, or one shared)
```

Instances write due-times into the shared store instead of their own database.

### The infra objection

This puts a shared component **in the write path of a user action**. Today,
saving a future-dated record touches one database — the instance's own. Under C
it touches two, and the second is shared across 53 tenants. If the scheduler
database is down or slow, users cannot save future-dated records *at all*, on
every instance simultaneously. A and B degrade firing; C degrades **writing**.

It also creates a second system of record. The instance's meta row and the
central timer row must agree through cancellation, deletion, instance clone, and
restore-from-backup. Restore is the one that should worry you: restoring an
instance's database to an earlier point silently desynchronises it from a central
store that was never rolled back.

Isolation stops being structural and becomes a discipline — every query must
filter by tenant, and nothing enforces it. Given 53 tenants in one namespace,
that is a lot of trust placed in code review.

**Verdict:** a reasonable design for a different requirement — fleet-wide
operational visibility of scheduled work. If calculation scheduling later needs
that, revisit it with visibility as the stated goal, not as a side effect of
needing a clock.

---

## 4. Approach D — reconcile in the backend (implemented)

### What gets deployed

Nothing.

```diff
  # modules/lex-instance/configmaps.tf — dpag (backend) ConfigMap
+ "LEX_ACTIVATION_RECONCILE_ENABLED"          = "true"
+ "LEX_ACTIVATION_RECONCILE_INTERVAL_SECONDS" = "60"
```

No new Deployment, no Service, no PVC, no database, no ServiceAccount, no RBAC
grant, no secret, no network path, no new port, no ingress rule. The work runs on
a daemon thread inside the backend process that already exists on every instance.

### How it works, in infra terms

The backend queries **its own database** for meta rows still marked `SCHEDULED`
whose history row's `valid_from` has passed, and applies them. The instance's
database was already the durable record of what should happen; the timer was
only ever an optimisation for *when* to look.

Because it needs nothing outside the instance, isolation is not engineered — it
is structural. There is no component that can reach across tenants because there
is no new component.

### Why it is set unconditionally

`LEX_ACTIVATION_RECONCILE_*` goes in the **unconditional** part of the backend
ConfigMap, not the `architecture == "MQ/Worker"` branch that carries the Celery
flags. The instances that need it most are the **47 without Celery**: their
timer is an in-process thread queue that is lost on every restart, with nothing
to rehydrate it. Gating on the worker architecture would skip the entire
population the change exists for.

### Failure modes

| Failure | Effect |
|---|---|
| Backend restarts | Catch-up runs **immediately on boot** — the restart is the trigger, not the problem |
| Loop thread dies | Next backend restart resumes it; activations are late, never lost |
| Instance backend at 0 replicas (`autoscaling: true`) | No pass runs while nothing is running. Activation is late until the backend wakes — the same window in which nobody is using the instance. Worth confirming which instances have this set. |
| A record cannot activate | Retried up to `MAX_ATTEMPTS` per process, then skipped, so one poison row cannot burn every pass |
| Instance dormant for months | Rows older than `MAX_AGE_DAYS` are reported, not replayed — no silent backdated flood on wake |

### Ops burden

One log line per pass that finds work:

```
Activation reconcile pass: {'models': 3, 'overdue': 1, 'activated': 1, 'failed': 0, 'gave_up': 0}
```

Silence when there is nothing to do. Nothing new to deploy, monitor, back up,
rotate or page on.

### Cost

Interval is worst-case lateness. At the default 60s, a change dated 14:00:00 is
live by 14:01:00. Sub-minute precision would need a clock — which is exactly what
approach A becomes if it is ever wanted, with this as the floor beneath it.

---

## 5. Side by side

| | A: register+callback | B: poll instance DBs | C: central store | D: reconcile |
|---|---|---|---|---|
| New Deployments | 1 (stateful) | 1 | 1 | **0** |
| New database / PVC | yes | no | yes + backups | **no** |
| New Secrets | 53 keys or 1 shared | — | 53 write creds | **0** |
| RBAC grant | ideally none | **`secrets get` namespace-wide** | none | **none** |
| lex-app change | registration API + callback endpoint | **none** | write path change | one module |
| Serves all 53 instances | HTTP variant only | Celery only (6) | yes | **yes** |
| Shared failure domain | firing | firing | **writing** | **none** |
| Blast radius if compromised | callbacks into instances | **all customer credentials** | all schedule data | n/a |
| Precision | seconds | seconds | seconds | interval (60s) |
| Effort | high | medium | high | **shipped** |

---

## 6. What this unblocks

The per-instance beat pod was retained for one reason: it also ran
django-celery-beat's clock for these activations, so switching
`recoveryDriver: beat → supervisor` silently stopped future-dated changes from
firing. That was the recorded blocker on
[LEX_TERRAFORM_MODULES#35](https://github.com/ExcellenceCloudGmbH/LEX_TERRAFORM_MODULES/pull/35).

With a catch-up floor, a missed timer costs latency rather than the activation.
Beat is no longer required for bitemporal, and `values.yaml` now says so where
the driver is chosen. #35 can proceed on its own merits.

---

## 7. Recommended sequence

1. **Ship D** (done). Fixes a live bug on 47 instances and removes the
   correctness dependency on any clock.
2. **Merge #35.** Retire per-instance beat; the blocker is gone.
3. **Measure.** If nobody reports activation lateness, there is no scheduler to
   build and this project is finished.
4. **Only if precision is genuinely required**, build A — as a *nudge*, with D
   underneath as the floor. That ordering means the shared component is
   introduced after evidence, and is never load-bearing when it is.

**Do not build B.** "No lex-app change" is a real advantage and the wrong thing
to optimise for: it purchases a faster rollout with permanent namespace-wide
access to every customer's credentials, for a component that only needs to know
what time it is.

---

## 8. Open infra questions

1. **Which instances have `autoscaling: true`?** Decides whether D has a gap
   (backend at 0 → no pass) and whether A's HTTP callback is viable at all. Small
   set expected; not yet enumerated.
2. **Raise the recovery supervisor limit above 256Mi.** Unrelated to this design
   but adjacent: instance 1047's supervisor is `OOMKilled` 68 times over five
   hours because a Django boot settles just above the limit. It is also the
   evidence for why "add a small pod per instance" is not free.
3. **If A is ever built, where does its state live?** A scheduler that forgets
   timers on restart is useless; a scheduler with a database is option C wearing
   a different hat. This is the question that decides whether A is small.
