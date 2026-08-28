# Cluster-level scheduling — infra, lex-app, and the seam between them

**Companion to** `2026-08-04-global-scheduler-options.md`, which argues the
software design. This document answers what that one skipped: what actually gets
deployed, what changes in lex-app, **what each side assumes about the other**,
and how each approach fails in practice.

**Status:** decision support. Approach D is implemented (`feat/bitemporal-activation-reconcile`
+ `feat/activation-reconcile-retire-beat`); A, B and C are described as they would
have to be built.

---

## 0. Read this first: the seam is where this system already broke

Every approach below creates a contract between infrastructure and application
code. This codebase has already demonstrated, in production, that such contracts
fail **silently**, and the failure is worth stating before any option is judged.

Worker recovery has two halves. Workers write in-flight tasks into a Redis
registry; a supervisor pod reads that registry and requeues whatever a dead
worker abandoned. The application gates both halves behind one environment
variable:

```python
# lex/lex_app/settings.py
LEX_TASK_RECOVERY_ENABLED = os.getenv("LEX_TASK_RECOVERY_ENABLED", "false").lower() == "true"
```

Terraform set that variable on the **supervisor** pod and not on the **workers**.
The result: a healthy, `1/1 Running` supervisor sweeping an index that nobody
wrote to. On instance 1410 it executed the sweep **23,625 times over 2.5 days and
found nothing every single time**. On instance 1050 it meant a calculation that
died to an OOM kill was never recovered, and a user watched a spinner for two
hours.

Nothing alerted. The only signal the application emitted was
`logger.debug("Celery recovery disabled")` — invisible at INFO.

Three properties combined to make it invisible, and they are the lens for the
rest of this document:

1. **The default was off.** Absence of configuration meant absence of function.
2. **The two halves were configured separately.** Nothing checked they agreed.
3. **Neither half could tell.** The supervisor could not know that "empty index"
   meant "workers are mute" rather than "nothing to do."

When judging the options below, the question is not "does this work when
configured correctly" — they all do. It is **"what happens when infra and
lex-app disagree, and would anyone find out."**

---

## 1. The cluster this has to live in

| | |
|---|---|
| Cluster | `lex-cluster-2`, GKE, `europe-west3` |
| Instances | **53**, all in one namespace (`default`) |
| …running Celery workers | **6** (KEDA `ScaledJob`, 0→21 pods) |
| Per instance | backend + frontend Deployments, own Dragonfly (1 replica, 1 GiB PVC), own Postgres database on a shared server |
| Nodes | `e2-standard-4`, **spot**, pool capped at 15 |
| Backend scaling | optional KEDA `ScaledObject`, min 0, triggered by **nginx ingress request count** |
| Deployment path | instance controller → Terraform → Helm, `module_version` pinned **per instance** |

Three facts drive most conclusions:

**One namespace, 53 tenants.** No namespace boundary to lean on. Any RBAC grant
to a cluster-level component applies to every instance at once.

**The backend can be at 0 replicas**, and only ingress traffic wakes it. An
in-cluster call does not traverse the ingress, so a cluster-level component
**cannot wake a backend by calling it**.

**Version skew is the normal state.** `module_version` and the lex-app image are
pinned per instance. Instance 1050 runs `2.0.0rc220` — 83 commits behind — while
others run 2.1.x. Any contract between infra and lex-app must hold across every
version simultaneously deployed, which is not one version.

---

## 2. Approach A — cluster scheduler, instances register, scheduler calls back

### Infra

```
namespace: default
  Deployment  lex-scheduler                 replicas: 1
  Service     lex-scheduler                 ClusterIP  ← instances register here
  PVC or DB   lex-scheduler-timers          ← timers must survive a restart
  Secret      lex-scheduler-callback-keys   ← how it authenticates to 53 instances
```

### lex-app

Two new pieces, on opposite sides of the request:

```python
# 1. registration — called from _schedule_future_activation
def _register_with_scheduler(instance_id, due_at, ref):
    requests.post(f"{settings.LEX_SCHEDULER_URL}/timers",
                  json={"instance": instance_id, "due_at": due_at.isoformat(), "ref": ref},
                  headers={"Authorization": f"Bearer {settings.LEX_SCHEDULER_TOKEN}"})

# 2. callback endpoint — the scheduler calls this at fire time
class ActivateView(APIView):
    permission_classes = [HasSchedulerSignature]
    def post(self, request):
        activate_history_version(**request.data["ref"])
```

Plus deregistration on cancel and on delete, mirroring the existing
`PeriodicTask.objects.filter(name=...).delete()` path.

### The seam

| Infra promises lex-app | lex-app promises infra |
|---|---|
| `LEX_SCHEDULER_URL` is set and reachable | it will register every future-dated save |
| a callback will arrive at `due_at` | the callback endpoint exists at that path |
| the callback carries a verifiable signature | it will deregister on cancel/delete |
| the backend will be running to receive it | `ref` stays interpretable across versions |

That is **four independent assumptions in each direction**, none of them checked
at deploy time.

### How it goes wrong

**The 1410 failure, repeated.** `LEX_SCHEDULER_URL` is set on the backend
ConfigMap for the instances someone remembered. On the rest, registration is a
no-op or a swallowed exception, and those instances silently have no scheduling
at all. The scheduler looks healthy — it has timers, it fires them, its metrics
are green. It simply has no timers *from those instances*, and nothing can tell
the difference between "instance has nothing scheduled" and "instance never
called us." This is precisely the shape of the recovery-flag incident, and the
precedent says it will not be noticed for months.

**Version skew breaks `ref`.** The scheduler stores an opaque reference and hands
it back. When lex-app changes what it puts in `ref`, the scheduler is holding
timers created by the old version and returns them to a new one. With 53
instances on assorted versions, the scheduler must accept every historical
`ref` shape forever, or timers created before an upgrade fire into a handler
that cannot read them.

**The callback hits a backend at 0 replicas.** On `autoscaling: true` instances
the activation is simply late, and nothing distinguishes that from a slow
instance. The scheduler's retry looks identical to the fleet-wide healthy case.

**The instance is deleted; the timer is not.** Deregistration is a best-effort
call made by a component that is being torn down. The scheduler keeps calling a
Service that no longer resolves, forever, until someone notices the error rate.

**Verdict:** the cleanest cluster-level design, and it multiplies the exact
failure mode this codebase has already suffered — a silent configuration
disagreement between two independently deployed halves.

---

## 3. Approach B — scheduler polls every instance's database

### Infra

```
namespace: default
  Deployment      lex-scheduler       replicas: 1
  ServiceAccount  lex-scheduler
  Role/RoleBinding: secrets [get, list]   ← the whole story is in this line
```

### lex-app

**None.** That is the attraction, and it should be stated honestly: this is the
only option deployable against instances exactly as they are today — no release,
no migration, no coordination, no version-skew problem at deploy time.

### The seam — and why "no lex-app change" is misleading

There is no code change, but there is still a contract, and it is *larger* than
in any other option. The scheduler has to reimplement lex-app's scheduling
semantics from outside:

- which meta rows count (`meta_task_status = 'SCHEDULED'`)
- that superseded versions do not (`sys_to IS NULL`)
- which states are terminal (`DONE`, `CANCELLED`)
- that `valid_from` is timezone-aware and compared in UTC
- the 5-second grace window `activate_history_version` applies
- what "activation" means well enough to dispatch it correctly

None of that is an interface. It is a **copy of business logic**, living outside
the application, coupled to a database schema, with nothing that fails when the
two diverge — no import, no type, no test that spans both.

### How it goes wrong

**Schema drift, silently.** lex-app adds a state — say `FAILED`, which this very
project considered adding and rejected only because meta models are generated per
customer. The scheduler does not know about it and treats those rows as
outstanding forever, or ignores them entirely. Nothing breaks loudly; the
behaviour just becomes wrong on some instances.

**Version skew, permanently.** The scheduler queries 53 databases whose schemas
are pinned to whatever module and image each instance runs. Instance 1050 is 83
commits behind. The scheduler must satisfy every deployed version *at once*, and
every future change to the meta model becomes a coordinated release across a
component that has no idea which version it is talking to.

**The timezone class of bug, reintroduced.** This quarter already produced a
timezone incident (rc212→2.1.4). The scheduler would compare `valid_from` against
its own clock in its own timezone, in a process that is not Django and does not
share `USE_TZ` or `TIME_ZONE`. That is the same bug with a new home, and the
blast radius is 53 instances rather than one.

**The credential grant is not what it looks like.** Reaching 53 databases means
reading 53 `app-env-<instance>` Secrets, and in a single namespace that grant is
not "database credentials" — it is every key in every secret:

```
DJANGO_SECRET_KEY          POSTGRES_USERNAME / POSTGRES_PASSWORD
DJANGO_SUPERUSER_PASSWORD  REDIS_USERNAME / REDIS_PASSWORD
LEX_AI_METAGPT_LLM_CREDENTIALS
SHAREPOINT_API_CERTIFICATE_THUMBPRINT
```

A component whose job is knowing what time it is would hold the session signing
key, database superuser credentials, and the SharePoint certificate thumbprint
for all 53 customers. Kubernetes RBAC cannot scope this in one namespace without
naming each secret, and the list changes on every instance creation.

**Verdict:** cheapest to ship, most expensive to own. "No lex-app change" buys a
faster rollout by replacing a *typed* coupling with an *untyped* one, and pays
for it with namespace-wide credential access.

---

## 4. Approach C — central schedule store

### Infra

```
namespace: default
  Deployment  lex-scheduler       replicas: 1
  Database    scheduler DB + backups + migrations + monitoring
  Secret      per-instance write credentials (53 roles, or one shared)
```

### lex-app

`_schedule_future_activation` writes the timer to the central store instead of
locally, and cancellation must propagate there too:

```python
# instead of PeriodicTask.objects.create(...)
scheduler_db.timers.insert(instance=INSTANCE_ID, due_at=valid_from, ref=...)
# and in on_history_pre_delete__cancel_schedules
scheduler_db.timers.delete(instance=INSTANCE_ID, ref=...)
```

### The seam

This is the only option where the shared component sits in the **write path of a
user action**. Today, saving a future-dated record touches one database — the
instance's own. Under C it touches two, and the second is shared across 53
tenants.

### How it goes wrong

**An outage stops users saving, not just firing.** A and B degrade *firing* when
the scheduler is down. C degrades *writing*: if the central database is slow or
unavailable, nobody on any instance can save a future-dated record. A component
that was introduced to improve scheduling latency becomes a dependency of the
save button.

**Cancellation fails and a withdrawn change fires.** This is the dangerous one.
A user deletes a future-dated record; the local delete succeeds, the remote
cancel fails or is lost. The instance believes the change is withdrawn; the
scheduler still holds the timer and fires it. That is not a delay — it is
**applying a change the user explicitly retracted**, with the local system
showing it as cancelled.

**Restore-from-backup desynchronises silently.** Restoring an instance's database
to an earlier point rolls back its meta rows. The central store was not rolled
back and still holds timers for records that no longer exist in that state.
Nothing reconciles them.

**Tenant isolation becomes a code-review discipline.** Every query must filter by
instance. Nothing structural enforces it, and with 53 tenants in one table a
missing `WHERE` is a cross-tenant data exposure rather than a bug.

**Verdict:** a reasonable design for a different requirement — fleet-wide
operational visibility of scheduled work. Revisit it if that becomes the goal,
not as a side effect of needing a clock.

---

## 5. Approach D — reconcile in the backend (implemented)

### Infra

Nothing.

```diff
  # modules/lex-instance/configmaps.tf — dpag (backend) ConfigMap
+ "LEX_ACTIVATION_RECONCILE_ENABLED"          = "true"
+ "LEX_ACTIVATION_RECONCILE_INTERVAL_SECONDS" = "60"
```

No Deployment, Service, PVC, database, ServiceAccount, RBAC grant, secret,
network path or ingress rule.

### lex-app

`lex/core/services/activation_reconcile.py`. The backend queries **its own
database** for work the timer should have done:

```python
def reconcile_pending_activations(now=None) -> dict:
    for main_model, history_model, meta_model in iter_bitemporal_models():
        overdue = _overdue_history_ids(meta_model, history_model, now)
        for history_id in overdue:
            outcome = _outcome_of(activate_history_version(app_label, name, history_id))
            ...
```

Overdue means: a meta row still `SCHEDULED`, current (`sys_to IS NULL`), whose
history row's `valid_from` has passed and is inside the age window. Started from
`apps.ready()` behind `running_in_uvicorn()`, so only the served backend runs it —
never a management command or a worker.

### The seam — deliberately one-directional

| Infra promises lex-app | lex-app promises infra |
|---|---|
| *nothing* | it will catch up regardless |

This is the design property that matters most, and it is a direct response to §0.
**The code defaults to enabled:**

```python
LEX_ACTIVATION_RECONCILE_ENABLED = os.getenv("LEX_ACTIVATION_RECONCILE_ENABLED", "true") == "true"
#                                                                                ^^^^^^
```

Compare `LEX_TASK_RECOVERY_ENABLED`, which defaults to `"false"`. That inversion
is the whole lesson of the recovery incident applied: **absence of configuration
must not mean absence of function.** An instance that never receives the
ConfigMap key still catches up. Terraform setting it is documentation and an
override, not an enablement.

There is also nothing to disagree *with*. One process reads its own database and
acts. There is no second half to fall out of sync.

### How it goes wrong — the honest list

D is the safest option here, not a safe one. Its real weaknesses:

**1. The query is unindexed.** This is the most concrete flaw. `meta_task_status`
carries no `db_index`, and `valid_from` is indexed only when `_date_indexing` is
enabled. So every 60 seconds, for every bitemporal model, the backend runs:

```sql
SELECT history_object_id FROM <meta_table>
 WHERE meta_task_status = 'SCHEDULED' AND sys_to IS NULL;
```

— a **sequential scan**. On instances with small meta tables this is free. On one
with a large history it is a full scan per model per minute, forever, on the pod
that also serves user requests. It has not been measured on a large instance.

*Mitigations, in order of preference:* raise
`LEX_ACTIVATION_RECONCILE_INTERVAL_SECONDS` (a pure latency/cost trade with no
correctness impact); add `db_index=True` to `meta_task_status` — correct, but
meta models are generated per customer model, so it forces a migration in every
customer repository; or invert the query to start from the indexed `valid_from`
range, which helps only where date indexing is on. **Watch `pg_stat_statements`
on the largest instance before assuming this is fine.**

**2. A backend at 0 replicas runs no passes.** On `autoscaling: true` instances
the activation waits until the backend wakes. The window coincides with nobody
using the instance, so it is usually harmless — but an external consumer reading
that data at the scheduled time sees stale values. Which instances have
`autoscaling: true` is not yet enumerated.

**3. The attempt counter is in-memory.** A record that cannot activate is retried
`MAX_ATTEMPTS` times *per process*. A crash-looping backend resets the counter on
every restart, so a poison record could be retried indefinitely across restarts.
The alternative — a terminal `FAILED` status — was rejected because it forces a
migration in every customer repo for a value the database does not enforce. This
is a deliberate trade, not an oversight, but it is a trade.

**4. `MAX_AGE_DAYS` skips work silently unless someone reads the log.** Rows older
than the window are deliberately not replayed, which is right — a dormant
instance should not wake and apply a year of backdated changes. But the only
signal is a log line. Nothing alerts that an instance has activations it will
never apply.

**5. It masks the underlying breakage.** With catch-up in place, a completely
dead timer looks like a slightly late activation. That is the intended benefit,
and it means nobody will notice that the in-process scheduler is broken, because
the symptom is gone. If the timer path is ever meant to be fixed rather than
retired, this removes the pressure to do it.

**6. Latency is a floor, not a guarantee.** 60 s is worst case *per pass*, but a
slow pass on a large instance extends it. There is no deadline enforcement.

---

## 6. Side by side

| | A: register+callback | B: poll instance DBs | C: central store | D: reconcile |
|---|---|---|---|---|
| New Deployments | 1 (stateful) | 1 | 1 | **0** |
| New database / PVC | yes | no | yes + backups | **no** |
| RBAC grant | ideally none | **`secrets get` namespace-wide** | none | **none** |
| lex-app change | registration + endpoint + deregistration | **none** | write path | one module |
| Coupling type | HTTP contract + token | **untyped, to the DB schema** | two systems of record | none |
| Survives version skew | ⚠️ `ref` shape must be forever-compatible | ❌ must satisfy all 53 versions at once | ⚠️ | ✅ ships with the image |
| Fails silently when misconfigured | **yes — the §0 failure** | yes | yes (cancel path) | **no — defaults on** |
| Shared failure domain | firing | firing | **writing** | **none** |
| Worst realistic outcome | instances silently unscheduled | fleet credential exposure | **withdrawn change fires** | activation is late |
| Effort | high | medium | high | **shipped** |

---

## 7. Recommended sequence

1. **Ship D** (done). Fixes a live bug on 47 instances and removes the
   correctness dependency on any clock.
2. **Merge [#35](https://github.com/ExcellenceCloudGmbH/LEX_TERRAFORM_MODULES/pull/35).**
   Per-instance beat was retained only because it also ran the bitemporal clock;
   that blocker is gone and `values.yaml` now records it.
3. **Measure the query cost** (§5.1) on the largest instance before this runs
   fleet-wide for months.
4. **Measure lateness.** If nobody reports it, there is no scheduler to build.
5. **Only if precision is genuinely required**, build A as a *nudge* with D as the
   floor — so the shared component is introduced after evidence, and is never
   load-bearing.

**Do not build B.** It replaces a typed coupling with an untyped one, must
satisfy 53 simultaneously-deployed schema versions, and buys that with
namespace-wide access to every customer credential — for a component that only
needs to know what time it is.

---

## 8. Open questions

1. **Which instances have `autoscaling: true`?** Decides D's gap (§5.2) and
   whether A's HTTP callback is viable at all.
2. **What does the reconcile query cost on the largest instance?** (§5.1) The one
   number that could change D's default interval.
3. **Raise the recovery supervisor limit above 256Mi.** Adjacent, not caused by
   this: instance 1047's supervisor is `OOMKilled` 68 times in five hours because
   a Django boot settles just above the limit. It is also the evidence for why
   "add a small pod per instance" is not free.
4. **If A is ever built, where does its state live?** A scheduler that forgets
   timers on restart is useless; one with a database is C wearing a different
   hat. That question decides whether A is small.
