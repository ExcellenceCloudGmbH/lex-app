# Fleet metrics, phase 1: the KPIs and the architecture

**Status:** design, revision 2. It answers the review of 6 October 2026 and awaits review again.
**Date:** 2026-09-29; revision 2 dated 2026-10-08
**Linear:** [LEX-749](https://linear.app/lundgroup/issue/LEX-749) (umbrella) ·
[LEX-750](https://linear.app/lundgroup/issue/LEX-750) (week 1) ·
[LEX-751](https://linear.app/lundgroup/issue/LEX-751) (the build). Review follow-ups:
[LEX-988](https://linear.app/lundgroup/issue/LEX-988) value ·
[LEX-989](https://linear.app/lundgroup/issue/LEX-989) thresholds ·
[LEX-990](https://linear.app/lundgroup/issue/LEX-990) priorities ·
[LEX-991](https://linear.app/lundgroup/issue/LEX-991) K2 frontend ·
[LEX-992](https://linear.app/lundgroup/issue/LEX-992) K6 startup time ·
[LEX-993](https://linear.app/lundgroup/issue/LEX-993) K8 handoff ·
[LEX-994](https://linear.app/lundgroup/issue/LEX-994) delivery stages ·
[LEX-995](https://linear.app/lundgroup/issue/LEX-995) Prometheus
**Component:** the collector is a standalone component, **lex-analytics**, with its own
repository and database (decided 2026-10-08). Its contract with the Instance Controller is
`lex-analytics/docs/contract-instance-controller.md`.
**Plans:** one per repository, in `docs/superpowers/plans/`. The collector plan's R1 to R4 tasks
are implemented in lex-analytics rather than in the Instance Controller:
`2026-09-29-fleet-metrics-phase1-collector.md` (lex-instance-controller-backend),
`2026-09-29-fleet-metrics-phase1-infrastructure.md` (instance-controller-deployment),
`2026-09-29-fleet-metrics-phase1-recorder.md` (lex-app),
`2026-10-08-fleet-metrics-phase1-frontend.md` (process-admin-general-client). Each plan
marks the release its tasks belong to.

---

## 0. What revision 2 changes

| Review item | Change | Where |
|---|---|---|
| Value to stakeholders is unclear (LEX-988) | Every retained KPI names its stakeholder, the action it triggers now and in December, and a worked KPI → result → action → value example | §4.2 |
| Thresholds must be realistic and adjustable (LEX-989) | No threshold is approved yet. Each KPI has a baseline source, a provisional planning rule and a separate operational flag. Thresholds live in a versioned table, so changing one never touches a measurement | §4.4, §5.5 |
| Deliver 1 to 3 KPIs first; drop K4 (LEX-990) | Ranked register. Release 1 is **K1 (active writers), K3 and K7**. K4 is rejected. K9 comes last | §4.1, §4.3 |
| K2: how is it collected, including the frontend (LEX-991) | A measurement contract for interface usage events, kept separate from API activity | §5.3.2 |
| K6: startup time (LEX-992) | K6 becomes *starts and startup time*, split into four phases and classified by cause | §4.2 K6, §5.4.5 |
| K8: delegate to Infra and Instance Controller (LEX-993) | K8 is owned there. This project supplies the storage slot and the dashboard, and coordinates until owners are confirmed | §4.2 K8, §8.4 |
| Rollout too superficial (LEX-994) | Dated stages, each with output, owner, prerequisite and evidence. The 35-hour figure is reconciled with the new scope | §8 |
| Prometheus only if proportionate (LEX-995) | Checked against the live series. Release 1 does not use Prometheus at all; releases 2 and 4 reuse existing series and add no infrastructure | §5.8 |
| Its own project (decision, 8 October) | The collector becomes the standalone component lex-analytics with its own database. It reads instance databases through a read-only role and gets everything else from one controller endpoint | §5.1, §5.4, §5.5 |

---

## 1. What this document decides

LEX-749 asks for analytics and monitoring that record, untended, until the end of the
year, so that the numbers can guide the 2027 plan. This document settles:

1. **Scope** (§3): both instance health and product usage, led by product usage.
2. **The KPI register** (§4): which KPIs are retained, deferred or rejected and why, the
   order they are delivered in, the value each one creates, and how its thresholds are
   set.
3. **The architecture** (§5): what is collected where, where it lands, how long it is kept,
   who can read it, and why Prometheus is used where it is.
4. **Two correctness rules that have cost this codebase before** (§6 time zones, §7
   silence).
5. **Delivery** (§8): staged releases with dates, owners, evidence and an estimate.

**The name.** In the product, *Analytics* is already taken: it is the Streamlit tab on a
record and a list. This project is called **fleet metrics** everywhere, in code, tables
and dashboards.

---

## 2. What exists today

Read from the source: lex-app `lex-app-v2` at `d48449bf`, lex-instance-controller-backend
`main` at `1eb03f0`, instance-controller-deployment `develop` at `76620638`,
LEX_TERRAFORM_MODULES `main`, process-admin-general-client `lex-app-v2-pac-latest` at
`e24fed4b`. Cluster facts were read on 2026-10-08 from the production and test clusters
with read-only commands.

### 2.1 The production fleet

62 instances have a backend Deployment in production. 38 are set to run (one replica)
and 24 are paused (zero replicas). **No production instance scales to zero**: the
cluster has no backend ScaledObject, because autoscaling is off by default
(`config_metadata.py:219`, `autoscaling = BooleanField(default=False)`). Every backend
has a 600-second startup budget (startup probe: 120 × 5 s on `/health`).

### 2.2 Platform data is already recorded, per instance

kube-prometheus-stack scrapes the ingress controller and kube-state-metrics, with
retention set to 180 days and a 500 GiB volume in production. The series this project
needs were checked on the test cluster, which runs the same chart (§5.8):

- **Ingress requests**, `nginx_ingress_controller_requests` and
  `…_request_duration_seconds_bucket`, labelled per instance by `ingress="ingress-<rid>"`,
  `path="/api(/|$)(.*)"` for the API, `exported_namespace` for the controller environment,
  `host`, `method` and `status`.
- **Backend replicas**: `kube_deployment_spec_replicas` and
  `kube_deployment_status_replicas_available`.
- **Pod lifecycle timestamps**: `kube_pod_created`, `kube_pod_status_scheduled_time`,
  `kube_pod_status_initialized_time`, `kube_pod_container_state_started` (per container)
  and `kube_pod_status_ready_time`. Also init-container waiting and termination reasons,
  and container restarts. `kube_pod_status_container_ready_time` equals the ready time on
  every backend pod checked, so it cannot mark a container start.

Per-instance Grafana dashboards chart memory, CPU, volume use and requests per minute
from these series. No fleet view or daily rollup exists.

### 2.3 Product activity is partly recorded, in each instance's database

- **`AuditLog`**: one row per API create, update and delete (a bulk write creates one row
  per record): `date`, `author`, `resource`, `action`, `payload`. `author` is the person's
  display string, an API key's name for API-key requests, or `system…` for the framework.
  The table has no index on `date` or `author`.
- **`AuditLogStatus`**: one per audit row, `pending` → `success`/`failure`, with
  `created_at` and `updated_at`.
- **Calculations are countable from those two tables alone.** A calculation's root audit
  row ends with its terminal `is_calculated` state in the payload
  (`lex/audit_logging/utils/calculation_audit.py::_build_payload`). A plain edit of a
  calculation row resets `is_calculated` to `NOT_CALCULATED` before it is audited
  (`lex/api/views/model_entries/One.py::_should_reset_is_calculated_for_update`). So a
  root audit row whose payload holds `SUCCESS`, `ERROR`, `ABORTED` or `CANCELLED` is a
  finished run, and its status row's `created_at`/`updated_at` bracket it.
- **Not recorded anywhere:** reads, page views, exports, history browsing, search,
  Streamlit use, flows, and anything an API key reads.

### 2.4 The interface records nothing about use

The web interface (`lex-app-frontend`, pinned by lex-app, so upgrading lex-app upgrades
the interface) has no usage telemetry. Sentry's tracing and session replay are sampling
tools for errors, not counts, and they are not used here. Many areas cannot be told
apart from the backend at all. The show page, the edit drawer, a foreign-key hover card
and the calculation-log popup make the identical `GET …/one/<id>`. *Calculate* is the
same `PATCH` as saving an edit. The Streamlit *Analytics* view reaches the backend only
through a token request. The monitoring drawer and the record PDF make no backend call.
The frontend talks to the backend with the session cookie and a CSRF header.

### 2.5 Nothing in lex-app runs unattended

A paused backend has no process, and an autoscaled one (none in production today) stops
an hour after the last ingress request. Celery workers are KEDA jobs. The periodic
executors that run regardless of traffic are Instance Controller CronJobs
(`reap-stale-operations`, every 15 minutes) and pg_cron inside Cloud SQL.

### 2.6 What the Instance Controller knows

Every instance (`LexInstance`: type `prod`/`dev`/`ai`, release stage, status, client)
and every operation (`InstanceOperation`: type, actor, status, `started_at`,
`completed_at`; no version and no failure reason). It holds each instance's database
credentials, already used by the database export job, and each instance's API key. It
does **not** keep the lex-app version: the version exists only inside the instance's
encrypted `/api/health` token, fetched live when someone opens the instance list.

### 2.7 Two time-zone conventions meet here

- **lex-app:** `USE_TZ=True`; `TIME_ZONE` is `LEX_TIME_ZONE`, default `Europe/Berlin`; the
  Postgres session also runs in Berlin (`lex/lex_app/settings.py:838-839`). A naive
  `as_of` is read as UTC; a naive value anywhere else is read as Berlin.
- **Instance Controller:** `USE_TZ=False`, `TIME_ZONE='Europe/Berlin'`: every datetime in
  its code and ORM is naive Berlin local time.

§6 states the rules that keep a pipeline between the two correct.

---

## 3. Scope: both, product-led

**Product usage is the main build.** The 2027 plan is mostly product decisions: what to
invest in, what to retire, which customers need attention.

**Platform health is harvested, not built.** The data exists (§2.2). Phase 1 adds a daily
per-instance snapshot of the platform numbers that support a decision into the same store
as product usage, so both readings come from one place with one per-instance and
aggregate structure.

**Out of scope for phase 1:**

- Routing operational alerts to people when an instance misbehaves (LEX-451). Operational
  thresholds in this document highlight rows on a dashboard; they page nobody.
- Cost per instance (FinOps, LEX-837).
- LEX AI usage (its own KPI register, LEX-646).
- Generic click streams, page views and session recording. K2 uses a fixed list of
  product areas, one presence per person per area per day (§5.3.2).
- Customer-facing dashboards.

The one alert in scope is the pipeline's own: the collector announces when it stops
recording (§7).

---

## 4. The KPI register

### 4.1 Disposition and order

Nine candidates were proposed; the review approved eight, rejected one, and marked K9 as
lower value. A KPI is delivered in the release that its value, its readiness and its
cost justify:

| # | KPI | Disposition | Release | Why |
|---|---|---|---|---|
| K1 | Active people | Retained | **R1** as *active writers*; exact from R3 | Highest value. Writers come from the audit, on every instance, backfilled from 1 January |
| K3 | Calculation reliability | Retained | **R1** | Same source as K1, so the extra cost is small. Backfilled, with an action available the day it goes live |
| K7 | Version currency | Retained | **R1** | Cheapest of all, from the controller and the in-cluster health token. It is also the coverage dimension for every later release |
| K5 | Request success | Retained | R2 | High value. It needs the Prometheus source, and production retention depth is still unverified (§5.8) |
| K6 | Starts and startup time | Retained, extended (LEX-992) | R2 | Same Prometheus source as K5; feeds the scale-to-zero decision |
| K2 | Feature reach | Retained, extended (LEX-991) | R3 | High value, but it needs a lex-app release, a frontend release and a fleet upgrade |
| K9 | Response time | Retained, lowest priority | R4 | Lower value than the others (review, 6 October). One query on R2's source |
| K8 | Deploy reliability | Retained, delegated | Owned by Infra and Instance Controller | Review, 6 October; §4.2 K8, §8.4 |
| K4 | Integration share | **Rejected** | — | Rejected in the review of 6 October 2026 |

**Ranking.** Each candidate was scored from 1 (low) to 3 (high) on four criteria:
stakeholder value; whether an action is available in phase 1 and not only in December;
readiness (can it be collected today, backfilled, without a lex-app release); and cost
(3 = cheapest).

| KPI | Value | Phase-1 action | Readiness | Cost | Total | Rank |
|---|---|---|---|---|---|---|
| K1 (writers) | 3 | 3 | 3 | 2 | 11 | 1 |
| K3 | 3 | 3 | 3 | 2 | 11 | 1 |
| K7 | 2 | 3 | 2 | 3 | 10 | 3 |
| K5 | 3 | 2 | 2 | 2 | 9 | 4 |
| K6 | 2 | 2 | 2 | 2 | 8 | 5 |
| K2 | 3 | 1 | 1 | 1 | 6 | 6 |
| K9 | 1 | 1 | 2 | 3 | 7 | after R3, by value |

K1 and K3 tie because they share one source: building one makes the other cheap. K9
scores above K2 on the total, but it is placed last on purpose: its value is lower, and
cost alone should not move a KPI forward.

**Why R1 is K1, K3 and K7.** Together they answer the two product questions phase 1
exists for: *is the instance used* and *does its core feature work*. They also give the
operational list that every later release depends on: *which instances run which
version*. All three come from data that already exists, with no lex-app release and no
Prometheus. K1 and K3 are backfilled from 1 January, so their baselines exist on go-live
day. R1 needs work in two repositories: the Instance Controller and its deployment.

### 4.2 Each KPI: stakeholder, result, action, value

**Stakeholders** are named by role:

- **Product lead:** owns the 2027 roadmap.
- **Account owner:** responsible for a customer relationship.
- **Engineering lead:** owns lex-app quality.
- **Infra and Instance Controller owners:** run the platform, deploys and upgrades.
- **FinOps:** owns cost (LEX-837).

The worked examples use illustrative numbers. The real starting values come from the
baselines (§4.4).

#### K1 — Active people (R1: active writers; R3: active people)

- **Definition.** Distinct people active on the instance in the 28 UTC days ending on day
  D (1-day and 7-day kept as detail). Until an instance has the recorder (R3), *active*
  means *changed data*: `AuditLog` authors that are neither API keys nor the system,
  called **active writers**. Writers undercount people who only read, and every reading
  says which measure it is.
- **Stakeholders.** The account owner (phase 1), the product lead and FinOps (December).
- **Phase-1 action.** A weekly list of production instances whose writers fell to zero.
  The account owner contacts the customer. While K1 is the writer proxy, the action is a
  conversation, never a pause: a read-only customer is invisible to it.
- **December decision.** Which instances go on the 2027 review list (re-onboard, pause or
  retire), and where customer-success time goes first.
- **Value.** Churn risk is caught months before renewal. An instance that is truly
  unused can be paused, which frees its backend, workers and database share.
- **Worked example.**
  - KPI: instance A has had 0 active writers in each weekly reading for 8 weeks. Its last
    write was on 12 August.
  - Result: dormant, by the writer proxy.
  - Action: the account owner raises it at the next customer check-in. Either the customer
    restarts with an onboarding session, or agrees to pause the instance.
  - Value: a reactivated customer, or one backend and its database share no longer paid
    for.
- **Limit.** Counts are per instance; one person on two instances counts twice. Fleet
  totals are labelled *person-instance pairs*.

#### K2 — Feature reach (R3)

- **Definition.** For each product area (§5.3.2), the production instances where at
  least one person used it in the 28 days ending D, and the share of that instance's
  active people who did. Interface use (`ui`) and API activity (`api`) are reported
  separately. `ui` is the K2 figure; `api` is supporting detail and is labelled so.
- **Stakeholders.** The product lead.
- **Phase-1 action.** Limited, because data starts with the R3 upgrade in late November.
  The first four weeks name areas with no use at all. The product lead then asks two
  customers why before December.
- **December decision.** Which areas get 2027 investment and which are simplified or
  retired.
- **Value.** Engineering time moves from maintaining areas few people use to the areas
  everyone uses.
- **Worked example.**
  - KPI: *Reports* was opened on 1 of 30 production instances in 28 days. *History* was
    opened on 24 of them.
  - Result: reports are a candidate for retirement; history is a candidate for
    investment.
  - Action: reports get a simplify-or-retire assessment, and the one customer using them
    is asked how. History performance goes on the 2027 shortlist.
  - Value: effort lands where the reach is.
- **Honest limit for December.** K2 will have about four weeks of data on upgraded
  instances only. It can rule areas out; it cannot rank close ones.

#### K3 — Calculation reliability (R1)

- **Definition.** Finished calculation runs per day by outcome (success, error, aborted,
  cancelled). The **failure share** is (error + aborted) / (success + error + aborted);
  cancelled runs are a person's choice and count on neither side. Also the p50 and p95 duration of successful runs, from the request that
  started the run to its terminal state, queue wait included.
- **Stakeholders.** The engineering lead (phase 1), the product lead (December).
- **Phase-1 action.** The Product dashboard highlights an instance-day whose failures
  exceed that instance's own normal range (§4.4). Engineering reads those runs'
  calculation logs on the next working day.
- **December decision.** Whether calculation reliability or speed is a named 2027 theme,
  and which instances need a capacity review (worker concurrency, the KEDA maximum).
- **Value.** Failed runs are found before a customer reports them, often before month-end
  numbers are due. The 2027 theme rests on a year of evidence, not on the last escalation.
- **Worked example.**
  - KPI: instance B ended 14 of 40 finished runs in `ERROR` yesterday; its 90-day failure
    share is 2%.
  - Result: flagged on the dashboard.
  - Action: engineering opens the logs and finds a validation error after a new upload
    format. A fix goes into the next release, and the customer gets a workaround the
    same day.
  - Value: the month-end report is not late, and no support ticket was needed to find it.
- **Limit.** This is a rule over existing tables, not a dedicated record. R1's go-live
  checks it against `CalculationLog` on two instances. If they differ by more than 5%, a
  recorder hook at the terminal audit replaces the rule.

#### K5 — Request success (R2)

- **Definition.** Of the API requests reaching the instance (its ingress `ingress-<rid>`,
  path `/api(/|$)(.*)`, WebSocket upgrades excluded; §5.4.5), the share answered with a 5xx, split
  into **unavailable** (502, 503, 504: the backend is down or starting) and **application
  errors** (every other 5xx). Also `backend_unavailable_minutes`: minutes on D when the
  backend should run (spec replicas ≥ 1) but none was available.
- **Stakeholders.** The engineering lead and Infra.
- **Phase-1 action.** An instance-day highlighted for unavailability is checked by Infra
  that week: probes, memory, a start that never finished. Application-error highlights go
  to engineering with the instance's error log.
- **December decision.** Which reliability work 2027 funds before features, and the
  ceiling for any availability figure written into a contract.
- **Value.** Outages that nobody reported are found and fixed, and contracts promise only
  what the platform has shown it delivers.
- **Worked example.**
  - KPI: instance C answered 9% of API requests with 503 on Monday, and its backend was
    unavailable for 140 of the minutes it should have run.
  - Result: flagged.
  - Action: Infra finds the backend stuck in its migration container and fixes the
    migration.
  - Value: the instance is back before the customer escalates.
- **Why not synthetic uptime probes.** A probe through the ingress every minute would hold
  every autoscaled backend awake, because KEDA scales on ingress requests. K5 measures
  what people experienced.

#### K6 — Starts and startup time (R2)

- **Definition** (LEX-992). Every backend start on D, with its cause and its duration:
  - **A start** is a new backend pod: `kube_pod_created`.
  - **Ready** is the pod's `Ready` condition: the readiness probe on `/health` answered and
    the pod receives traffic.
  - **Startup duration** = ready − created, in four phases:
    - *scheduling*: created → scheduled;
    - *preparation*: scheduled → initialised, which covers the init containers that inject
      secrets and run the migrations;
    - *container start*: initialised → container started;
    - *application ready*: container started → ready.
  - **A failed start** is a pod that never became ready: deleted before it was ready, or
    still not ready 600 seconds (the startup budget) after it was created. Its
    init-container reason is kept, for example `CrashLoopBackOff` in `django-migrate`. A
    pod that became ready late is a slow start, not a failure, and counts in the startup
    percentiles.
  - **Cause**, in this order:
    1. The Instance Controller operation running when the pod was created, mapped to
       `deploy` (`CREATE`, `RECOVER`, `RECONFIGURE`, `RELEASE`, `UPDATE_INFRA`,
       `CHANGE_CLIENT`), `resume` or `restart`.
    2. Otherwise `rollout` when the pod belongs to a new ReplicaSet.
    3. Otherwise `scale_from_zero` on an autoscaled instance whose Deployment wanted zero
       replicas before (KEDA scales the wanted count; a node failure only drops the
       available count, and is a reschedule).
    4. Otherwise `reschedule` (node maintenance, eviction).
  - Container restarts inside a running pod are counted separately and are not starts.
  - Also `api_idle_hours`: hours of D without a single API request, the time a
    scale-to-zero backend could have been off.
- **Stakeholders.** Infra and Instance Controller owners; FinOps.
- **Phase-1 action.** Failed starts are listed on the Platform dashboard the next
  morning. A preparation phase far above its normal range points at a migration.
- **December decision.** Whether scale-to-zero can be offered in 2027, and to which
  instances. It is offered only if a start from cold is short enough for a person to wait
  through, and only where idle hours make the saving worth it. Also whether moving
  migrations out of startup is a 2027 item.
- **Value.** The cost decision is made with the wait it imposes in view. Starts that never
  finish stop being invisible.
- **Worked example** (with the 2026-10-08 snapshot, §4.4):
  - KPI: median startup 5¼ minutes, of which preparation takes 2½.
  - Result: a person opening a scaled-to-zero instance would wait over five minutes.
  - Action: scale-to-zero is not offered for interactive instances until preparation is
    shortened. That work is weighed against the idle hours of the instances that would
    benefit.
  - Value: no cost saving that customers would pay for with five-minute waits.
- **Not the same as K8.** K6 is the backend becoming ready, whatever started it. K8 is the
  whole deploy operation and belongs to its owners.

#### K7 — Version currency (R1)

- **Definition.** The lex-app version each instance ran on day D; its age in days (D minus
  the version's release date on PyPI); how many minor releases (major.minor; patches not
  counted) it is behind the newest; and the number of distinct versions in production.
- **Stakeholders.** Infra and Instance Controller owners (phase 1); the engineering lead
  and the product lead (December).
- **Phase-1 action.** A per-cycle list of production instances behind the newest release
  by more than the operational flag. It is the input to cycle upgrade planning, starting
  with the upgrade that carries R3.
- **December decision.** Whether 2027 needs an upgrade policy: a support window and
  scheduled fleet upgrades.
- **Value.** Fixes reach customers. K2 and exact K1 cover the fleet, because coverage
  equals upgraded instances. Support stops debugging long-fixed defects.
- **Worked example.**
  - KPI: 11 of 38 running production instances are more than two minor releases behind.
  - Result: above the flag.
  - Action: the Instance Controller owners schedule those 11 into the R3 upgrade window.
  - Value: in December, K2 covers 38 instances instead of 27, and 11 customers have the
    autumn fixes.

#### K8 — Deploy reliability (delegated to Infra and Instance Controller)

- **Owner.** Infra and Instance Controller (review, 6 October). This project coordinates
  the handoff until the owners are confirmed (§8.4). The owners define the measurement;
  this document records a starting proposal for them to accept or replace.
- **Proposal for the owners.** Operations that apply an instance's infrastructure and
  release (`CREATE`, `RECOVER`, `RECONFIGURE`, `RELEASE`, `CHANGE_CLIENT`,
  `UPDATE_INFRA`, `RESTART`, `PAUSE`, `RESUME`). For each: attempts, outcome, duration,
  the version deployed and a failure reason. `InstanceOperation` already holds type,
  actor, status and both timestamps. Version and failure reason do not exist and would
  need adding.
- **Interface.** The owners fill one row per operation in the fleet-metrics
  `DeployEvent` table (§5.5), or point the collector at a view with the same columns. The
  Platform dashboard reads it from there.
- **Thresholds.** The proposal's failure and duration figures are not adopted. The
  owners set K8's rules through the same threshold table (§4.4).

#### K9 — Response time (R4, lowest priority)

- **Definition.** The p95 duration of API requests (K5's selection, excluding 5xx) per
  instance per day.
- **Why lower.** Slow requests that matter already surface as 504s in K5, and slow
  calculations as durations in K3. K9 adds the slow-but-successful band.
- **Stakeholders.** The engineering lead.
- **Phase-1 action.** None planned. **December decision:** whether performance is a 2027
  theme, and for which instances.
- **Worked example.**
  - KPI: instance D's API p95 has risen every week of Q4.
  - Result: drifting.
  - Action: a performance investigation in 2027 planning.
  - Value: the slowdown is fixed before it becomes the customer's main complaint.
- **Limit.** Per-instance p95s cannot be combined into a fleet p95. The fleet view counts
  instances above the threshold instead.

### 4.3 Rejected and not adopted

| Candidate | Disposition |
|---|---|
| K4 Integration share | Rejected in the review of 6 October 2026. The API-key classification of audit authors stays, but only to exclude API keys from K1's writers |
| Page views, clicks, session length, session replay | Not adopted. K2 uses fixed product areas, one presence per person per day; a click stream adds no decision |
| Synthetic uptime per instance | Wakes scaled-to-zero backends (K5) |
| Error counts from error tracking | One project for every instance, with no instance tag (§10) |
| CPU, memory, storage | On the per-instance dashboards; sizing and cost belong to FinOps (LEX-837) |
| Keycloak login events as active users | Per Keycloak client, which several instances share |
| Pod restarts and OOM kills | Counted only as K6 container restarts; no separate decision |

### 4.4 Thresholds: baselines, provisional rules and how they change

No threshold in this document is approved. The 1 %, 5 %, 20 %, 90-day and 2-second
figures of the first proposal are withdrawn. Every KPI gets its starting values from its
own baseline, and those values are marked **provisional** until reviewed.

**Two kinds of threshold, kept apart:**

- **Planning rule:** read once, in December, over a quarter. It decides a 2027 item.
- **Operational flag:** read daily or weekly, per instance. It highlights a row on a
  dashboard for someone to look at. It never pages anyone; routing alerts is LEX-451.

**Baselines.** The table records what is available now and what each release adds.
*Observation* is the period the baseline covers. *Sample* is its unit and size.

| KPI | Baseline source | Observation | Sample | Status |
|---|---|---|---|---|
| K1 | R1 backfill of the audit | 1 Jan → go-live, ~43 weeks | weekly 28-day writer counts per instance, all production instances | At the R1 baseline review |
| K3 | R1 backfill of the audit | 1 Jan → go-live | every finished run, per instance and month | At the R1 baseline review |
| K7 | R1's first run | One day, then daily | every running production instance (38 on 2026-10-08) | At the R1 baseline review; there is no version history before R1 |
| K5 | R2 backfill from Prometheus | As far back as production retention reaches, at most 180 days | every API request per instance-day | At the R2 baseline review, once Infra has confirmed the retention depth (§5.8) |
| K6 | **Snapshot taken 2026-10-08**, then the R2 backfill | Each running backend's latest start | 33 production pods (the 38 running, minus 3 with restarted containers and 2 never ready) | See below |
| K2 | The first four weeks after an instance upgrades to R3 | Four weeks | areas × upgraded instances | Provisional through December |
| K9 | R2 backfill | As K5 | as K5 | At R4 |
| K8 | The owners' choice | — | — | Owners |

**K6 snapshot, 2026-10-08.** Production, created → ready, 33 pods:

| Phase | p50 | p90 | max |
|---|---|---|---|
| total | 315 s | 476 s | 4,764 s |
| scheduling | 0 s | 37 s | 183 s |
| preparation (secrets, migrations) | 144 s | 332 s | 4,557 s |
| container start | 23 s | 146 s | 238 s |
| application ready | 66 s | 127 s | 197 s |

Two more pods had not become ready after 83 hours, one with its migration container
restarting. Each running backend's latest start is a biased sample: long-running pods
are over-represented. R2's daily record replaces it.

**Provisional rules.** Each is relative to the KPI's own baseline, has a minimum sample
below which it is not judged, and is replaced at the baseline review.

| KPI | Planning rule (December) | Operational flag | Minimum sample |
|---|---|---|---|
| K1 | An instance with no active writers in each of 8 consecutive weekly readings goes on the review list. 8 weeks covers two monthly closes; the baseline review checks for quarterly users and extends it if they exist | The same condition, listed weekly | 8 weekly readings |
| K3 | The fleet's Q4 monthly failure share is above the highest month of Jan–Sep: the engine is a 2027 theme candidate | Failures on an instance-day above that instance's 90-day p95 of daily failures | 5 finished runs that day; 50 fleet-wide in a month |
| K7 | The share of running production instances more than two minor releases behind is higher in December than at the R1 baseline: an upgrade policy is a candidate | Instances more than two minor releases behind, listed per cycle | every running production instance |
| K5 | The unavailable share in Q4 is above the R2 baseline period's: availability work comes before features | An instance-day unavailable for more than its 90-day p95 of minutes | 100 API requests that day |
| K6 | Set at the R2 review from the startup distribution and a wait the product lead accepts for a cold start; the 600 s budget is a technical limit, not a target | A failed start; or preparation above the instance's 90-day p95 | every start |
| K2 | Set in December with the four weeks available, and marked as such | none | 4 weeks after the upgrade |
| K9 | Set at R4 from the R2 baseline | none in phase 1 | 100 requests that day |

**Who decides, when, and how a change is recorded.**

- The KPI's stakeholder proposes values. The product lead, as the LEX-749 reviewer,
  approves them.
- Values are reviewed at three points:
  - each release's baseline review (R1: by 30 October; R2: by 13 November);
  - on the first working day of November and of December;
  - finally at the December review.
- A change is a new row in the `Threshold` table (§5.5): KPI, metric, kind, value, minimum
  sample, `valid_from`, status (`provisional` or `agreed`), reason and the Linear issue
  that approved it. Rows are never edited or deleted.
- Dashboards draw, for each day, the threshold valid on that day. A chart that crosses a
  change shows both values.
- Measurements never depend on thresholds. The collector stores counts and durations
  only. Every flag and every share is computed when the dashboard reads, so changing a
  threshold re-reads the same data and nothing is re-collected.

---

## 5. Architecture

### 5.1 Overview

```
 Instance database (one per instance)          lex-analytics (standalone; one per environment)
┌──────────────────────────────────────┐      ┌──────────────────────────────────────────┐
│ audit_logging_auditlog       R1      │      │ CronJob collect-fleet-metrics, 02:30 UTC  │
│ audit_logging_auditlogstatus R1      │◄─────│   1. inventory from the controller   R1   │
│ lex_usage_daily             R3 new   │ role │   2. instance DB: aggregates only    R1   │
│ lex_runtime_versions        R3 new   │ lex_ │      (reader role, SELECT on 4 tables)    │
└──────────────────────────────────────┘ ana- │   3. version: observed by controller R1   │
        ▲ presence rows (api, ui)        lytics│   4. Prometheus: ingress, pods     R2/R4  │
   lex-app web process                  _reader│   5. upsert one row per instance/day R1   │
   recorder middleware + area endpoint        │   6. heartbeat, only if rows written R1   │
        ▲ area events                         └───────┬──────────────────────┬───────────┘
   web interface (lex-app-frontend)  R3               │ GET inventory        │ own database
                                                      ▼ (token)              ▼ lex_analytics
 Instance Controller                      ┌────────────────────────┐   Grafana, own organization,
   CronJob fleet-analytics-nightly 02:00: │ /api/fleet-analytics/  │   read-only role: Product,
   grants SELECT to the reader role;      │ inventory              │   Platform, Collection
   observes versions in-cluster           └────────────────────────┘
 Prometheus (existing series only) ◄── R2/R4 ── lex-analytics
```

There are four parts:

1. **The recorder** (R3) inside lex-app, and the interface's area events. Together they
   write one presence row per person, product area, channel and day into the instance's
   own database.
2. **The collector**, **lex-analytics** (R1): a standalone component with its own
   repository, image, CronJob and database. Once a day it reads aggregates from every
   instance database and, from R2, Prometheus. It writes one row per instance per day into
   its own database. It holds no instance password and no instance API key.
3. **The Instance Controller's side of the contract** (R1): a nightly job that grants
   the reader role `SELECT` on the four tables and observes each running instance's
   version, and one token-protected read-only endpoint that returns inventory, versions
   and operations.
4. **Grafana** reads lex-analytics' database through a role that can see nothing else.

**Why standalone.** The analytics component outlives phase 1 and will grow sources (AI
usage, cost) that have nothing to do with deploying instances. Its own database holds no
credentials, so Grafana's access is safe by construction rather than by careful grants.
The reader role makes "only aggregates leave an instance" a database guarantee rather
than a coding rule.

### 5.2 Principles

1. **Aggregate at the source; identities never leave an instance.** The collector's
   instance-database queries return counts and durations only. No user id, name, record
   id or payload crosses into the Instance Controller.
2. **Every day can be recomputed.** The collector derives day D from durable sources and
   upserts the result. Running it twice changes nothing. Running it in November for an
   October day, after a definition is corrected, corrects that day. This is the direct
   answer to LEX-749's named risk, *the wrong KPIs, instrumented well*.
3. **Explicit zeros and explicit gaps.** Every expected instance gets a row for every day,
   with a status per source. A missing row, a failed source and a genuine zero are three
   different things in the data.
4. **UTC days** (§6).
5. **Never wake an instance.** The collector reads databases and Prometheus directly and
   makes no call to any instance. The controller's version observation goes to the
   in-cluster service, never the ingress, and only when the backend pod is ready.
6. **Silence is loud** (§7).
7. **Store counts; derive rates and flags when reading.** Fleet rates are sums of
   numerators over sums of denominators. Thresholds apply at read time (§4.4).

### 5.3 The recorder and the interface events (lex-app, lex-app-frontend; R3)

#### 5.3.1 Tables and middleware

**Tables.** Raw SQL, installed by a `lex.core` migration exactly as
`0001_bitemporal_activation` installs the activation applier's table. The framework
registers every concrete Django model under `lex/` for history tracking and for the model
list, and an operational table belongs in neither.

```sql
CREATE TABLE IF NOT EXISTS lex_usage_daily (
    day         date        NOT NULL,              -- UTC calendar day
    actor_kind  text        NOT NULL,              -- 'person' | 'api_key'
    actor_id    text        NOT NULL,              -- auth_user.id, or the API key's name
    area        text        NOT NULL,              -- one of the areas in §5.3.2
    channel     text        NOT NULL,              -- 'api' | 'ui' | 'ui_embed'
    PRIMARY KEY (day, actor_kind, actor_id, area, channel)
);

CREATE TABLE IF NOT EXISTS lex_runtime_versions (
    version        text        PRIMARY KEY,
    first_seen     timestamptz NOT NULL DEFAULT now(),
    last_seen      timestamptz NOT NULL DEFAULT now(),
    usage_enabled  boolean     NOT NULL
);
```

A usage row holds no time of day: the day, the actor, the area and the channel are all it
says (§5.3.2, privacy boundary). Only the version table carries times, and they describe a
version, not a person.

**Middleware (`channel = 'api'`).** `UsageRecorderMiddleware` is the last entry in
`MIDDLEWARE`, after the Keycloak permissions middleware, so the user it sees is final. It
acts after the view returns:

- It records only successful requests (status below 400), not `OPTIONS` or `HEAD`, and
  only requests that resolved to a URL name.
- **Actor.** An authenticated Django user is recorded as `('person', user.pk)` and an
  API-key request as `('api_key', key name)`. The Instance Controller's own key
  (`LEX_API_KEY`) is never recorded. Anything else is skipped.
- **Area.** Taken from the URL name. This is the API view of an area, kept as supporting
  detail because the backend cannot tell several interface areas apart (§2.4):

| Area | URL names |
|---|---|
| `grid` | `model-entries-list` without `as_of` |
| `history` | `model-entries-list` with `as_of`; `model-history-list` |
| `record` | `model-one-entry-read-update-delete`, `model-one-entry-create`, `model-many-entries` |
| `export` | `model-export` |
| `calculation_logs` | `calculationlog-tree`, `init-calculation-logs`, `download-markdown-pdf` |
| `processes` | `process`, `run_step` |
| `reports` | `htmlreports` |
| `analytics` | `streamlit_token` |
| `search` | `global-search` |
| `files` | `file-download`, `sharepoint-file-download`, `sharepoint-file-share-link`, `sharepoint-file-preview-link` |
| `app` | any other authenticated request (model structure, project info, permissions, …) |

- **Write.** Insert-once (`ON CONFLICT DO NOTHING`), deduplicated in process memory by
  (day, actor, area, channel): one insert per person per area per day per process.
- **The day** is `datetime.now(timezone.utc).date()`, never `timezone.localdate()`, which
  is Berlin.
- **Runtime version.** On the first request from a person or an API key that a process
  serves, and at most hourly after that, the recorder upserts `lex_runtime_versions` with
  the running version. It gives K7 a version read without waking anything, and a version
  history. A request without an actor (`/health`, a probe, a login page) touches neither
  table, so health checks never depend on the database. An instance upgraded and not used
  since reads as *absent* until its first authenticated request.
- **Failure isolation.** Any exception inside the recorder is caught and logged at most
  once per process per hour. The response is never affected.
- **Switch.** `LEX_USAGE_METRICS=false` (or `0`, `no`, `off`), set per instance through
  the controller's custom environment, stops usage rows. Unset, empty, `true`, `1`, `yes`
  and `on` leave it on; any other value turns it off and logs one warning, since the
  switch is a privacy opt-out. The interface endpoint then answers 204 and records
  nothing. The version row is still written, with `usage_enabled = false`, so the
  collector reports *disabled* rather than *nobody used it*.

#### 5.3.2 The K2 measurement contract: interface area events (LEX-991)

**What counts as use.** A person *uses an area* on a day when they open it or perform its
defining action in the interface. The areas are a fixed list. Each is triggered at a
named place in the interface code (process-admin-general-client, `lex-app-v2-pac-latest`):

| Area | Counted when | Where the interface emits it |
|---|---|---|
| `grid` | A model's list view opens | `CustomLayout` route listener, `/:resource` |
| `record` | A record opens: show page, edit page or edit drawer | `CustomLayout` route listener; `CustomDatagrid` edit drawer |
| `create` | A create form opens | `/:resource/create`; `CustomList` create drawer |
| `history` | Full history, the History or Timeline tab, or as-of mode turns on | `historyViewLocation`; `CustomShow` `TrackingTabs`; `HistoryModeSwitch` |
| `audit_log` | The Audit Log tab opens | `CustomShow` `TrackingTabs` |
| `calculate` | A calculation is started | `CalculateFunctionality` |
| `calculation_logs` | The calculation-log popup or tree opens | `CalculationLogPopupHost`; `/calculation_log_tree` |
| `processes` | A process flow or process history opens | route listener, Process resources and `/process-history/…` |
| `reports` | An HTML report opens | route listener, report resources |
| `analytics` | The Analytics view opens on a list, a record or the home page | `CustomList` Analytics toggle; `CustomShow` Analytics tab; the Streamlit home page reports itself |
| `dashboard` | A widget dashboard opens | the dashboard component reports itself, at `/` and `custom_dashboard` |
| `search` | A global-search query runs | the header search |
| `export` | A grid export or a record PDF is made | `useAgGridExport`; `CustomShow` PDF button |
| `files` | A file is downloaded or previewed | `FieldView` file fields; SharePoint field |
| `monitoring` | The monitoring drawer opens | `ContentHeaderBar` |

Foreign-key dropdowns, hover cards and background refreshes emit nothing. They are not
a person choosing an area. The route `/` can show either the Streamlit home page or a
widget dashboard, so those two components report their own area instead of the route
listener. The interface list contains every API area except `app`, and adds the areas
the backend cannot see.

**How an event travels.**

1. The interface calls `recordUsage(area)`. The call is skipped if this browser tab
   already sent that area, with the same embed flag, today. The embed flag is part of
   the key because embedded frames share their tab's storage. Today means the UTC date,
   so the browser's time zone does not matter.
2. Otherwise the interface sends `POST api/usage/area` with the body
   `{"area": "<area>", "embed": <bool>}`, through the existing fetch wrapper:
   - same origin, session cookie and CSRF header;
   - `keepalive: true`;
   - fire and forget: a failed send is dropped, never retried, never shown.
3. lex-app takes the person from the session, never from the body, and rejects an area
   outside the list with 400. It records `('person', user.pk, area, 'ui')` through the
   recorder's insert-once path, or `'ui_embed'` when `embed` is true. It answers 204.
4. The collector counts distinct people per area and channel over 1, 7 and 28 days
   (`area_people_28d{area, channel}`). The day is fixed when lex-app records the event,
   so a tab left open across midnight produces one presence per day.

**Counting rules.**

- **Identity:** the Django user, inside the instance only. The interface never sends an
  identity.
- **Duplicates** are impossible in storage: the primary key is (day, actor, area,
  channel). The interface's per-tab dedupe saves requests, not correctness.
- **Embedded use:** the admin app inside a Streamlit page (`embed=true`) and the widget
  host (`/embed.html`, which lex-app frames; the older `/embed/widgets` route too) send
  `embed: true` and are counted as `ui_embed`. API requests
  carry no embed marker, so only the event can say so.
- **`api` and `ui` are never added together.** K2's figure is the collector's `ui_all`
  channel: distinct people over `ui` and `ui_embed` together, so a person who used an
  area both directly and embedded counts once. The `api` channel shows the same areas
  from the backend's side. It is labelled *API
  activity*, and it is the only channel API keys appear in.

**Gaps and coverage.**

- An instance has K2 coverage from the day it runs a lex-app version that contains the
  endpoint. lex-app pins the frontend package, so that version brings the emitting
  interface with it.
- K7's version dimension and `lex_runtime_versions.usage_enabled` give the coverage
  state per instance-day: `absent` (version too old), `enabled` or `disabled`.
- A day where the recorder saw active people through the API but no `ui` event at all is
  marked `ui_silent` on the Collection dashboard. That catches an interface that does not
  emit, for example one overridden to an older package. It does not fail the heartbeat:
  a person can legitimately work through the API only.

**Privacy boundary.** The event carries an area name and an embed flag. It never carries
a URL, model name, record id, search text, file name or time of day. The stored row is a
UTC date, an internal user id, an area and a channel, all inside the instance database.
Only counts leave. The switch in §5.3.1 turns events off with the rest of the recorder.

**Minimum changes.**

- **lex-app:** one view and URL (`api/usage/area`) beside the middleware, reusing its
  insert path, with tests.
- **Interface:** one module (`recordUsage`, the area list, the per-tab dedupe), one call
  in the `CustomLayout` route listener, and about a dozen explicit calls at the places in
  the table, with Vitest tests for the dedupe and the route mapping.
- **Rollout dependency:** an interface release, a lex-app release that pins it, then the
  fleet upgrade (§8).
- **Extra effort**, exposed for prioritisation: 8.5 hours (§8.2).

**Verification, end to end.**

1. On a test instance running the release, a test user opens a list, then a record's
   History tab, and starts a calculation.
2. In that instance's database, `lex_usage_daily` holds (`grid`, `ui`), (`record`, `ui`),
   (`history`, `ui`) and (`calculate`, `ui`) for that user and the UTC day, plus the
   home page's own area if the user landed on `/`, and no `reports` row. It holds the
   matching `api` rows from the middleware.
3. A collector run for that instance and day (`--instance`, `--from`, `--to`) stores
   `area_people_28d` of at least 1 for those four areas, and nothing for `reports`.
4. The Product dashboard's K2 panel shows the instance under `history`.
5. Negative case: reload the History tab 20 times. Still one row.
6. Repeat once in production on an internal instance after the upgrade. The result is
   recorded in the R3 acceptance issue.

### 5.4 The collector (lex-analytics)

#### 5.4.1 Where it runs

lex-analytics is a Django project (`lex_analytics`) with one app, `fleet_metrics`. It
lives in its own repository and has its own image and its own PostgreSQL database
(`lex_analytics`, on the controller's database server). It runs as a CronJob,
`collect-fleet-metrics`, with `timeZone: Etc/UTC`, schedule `30 2 * * *`,
`concurrencyPolicy: Forbid` and a two-hour deadline. Command:
`python manage.py collect_fleet_metrics`. It runs half an hour after the controller's
`fleet-analytics-nightly` job (02:00 UTC), whose grants and version observations it reads.

#### 5.4.2 What a run does

- **Expected instances:** every instance in the controller's inventory, which lists each
  `LexInstance` whose status is not `DELETED` or `DELETING`. Paused instances are included,
  so they produce explicit zeros. If the inventory cannot be read, the run fails, writes
  nothing and fails the heartbeat.
- **Days:** yesterday, plus every day in the previous 35 whose row is missing or has a
  failed source. `--from` and `--to` give explicit ranges for backfill. `--instance`
  restricts a run to one instance.
- **Per instance and day,** each source runs independently. A failing source marks only
  itself. Results are written in one transaction per instance and day, as an upsert.
- **The run ends** with the heartbeat decision (§7).
- **Sources by release:** R1 runs the controller, instance-database and version sources;
  R2 adds Prometheus; R3 adds the recorder tables to the instance-database source. A
  source not yet delivered is recorded as `skipped`, never as zero.

#### 5.4.3 Reaching an instance database

lex-analytics connects as one read-only role, `lex_analytics_reader`. Terraform creates it
on each database server with `LOGIN` and no privileges. The Instance Controller's nightly
job grants it `SELECT` on exactly four tables in every instance database:
`audit_logging_auditlog`, `audit_logging_auditlogstatus`, `lex_usage_daily` and
`lex_runtime_versions`, each where it exists. It also grants one column,
`rest_framework_api_key_apikey (name)`, so that API keys can be told apart from people
in the audit's author field: key names, never the hashed keys. The grant is made as the
instance's own user, which owns the tables. Nothing else is granted, so the role cannot
read customer data even through a coding mistake.

Host and database name come from the inventory. The connection goes directly to Cloud SQL
rather than through the pooler. Every instance's queries run inside `BEGIN READ ONLY`, with
`SET LOCAL statement_timeout = '60s'` and `SET LOCAL TIME ZONE 'UTC'`, and every query is
an aggregate.

A new instance becomes readable the night after its tables exist. Until then its
instance-database source fails, and the catch-up (§5.4.2) fills those days once the grant
is in place.

Instances whose database lives on a separate server (`db_server_alias`, used for
compliance isolation) are skipped by default and recorded as `excluded`.
`FLEET_METRICS_INCLUDE_DB_ALIASES` opts them in, given a reader role on that server.

#### 5.4.4 Windows without a date index

`AuditLog` and `AuditLogStatus` have no index on their timestamps and live on a database
server every customer shares, so the collector never scans them by date:

1. It finds the id range of a time window by binary search over the primary key; each
   probe is `SELECT id, <ts> FROM … WHERE id >= %s ORDER BY id LIMIT 1`, an index lookup.
2. It widens that range by ten minutes at each end.
3. It filters by timestamp exactly inside the range.

Ids and insert timestamps rise together (the timestamp is set immediately before the
insert); the margin absorbs the millisecond inversions of concurrent inserts. Calculation
runs are bounded by the status row's `created_at` over the two days before D, since
workers stop at 24 hours, then filtered on `updated_at`.

#### 5.4.5 Prometheus (R2 and R4)

lex-analytics reads the cluster's Prometheus (`FLEET_METRICS_PROMETHEUS_URL`, the same
in-cluster address the controller's `PROMETHEUS_HOST` names).
Every query is evaluated at the end of day D (`D+1 00:00:00Z`).

- **API traffic (K5, K9):** `nginx_ingress_controller_requests` and the duration
  histogram with:
  - `ingress="ingress-<rid>"` and `path="/api(/|$)(.*)"`, both verified on the test
    cluster;
  - `exported_namespace="<the controller environment's namespace>"`, which separates
    environments that share a cluster;
  - `status!="101"`.

  `ingress-<rid>` alone would mix the single-page app, Streamlit, Flower and the docs
  host. A day on which Prometheus answered but the instance's series is absent is zero
  requests, not a gap: the ingress controller creates a series only when traffic
  arrives.
- **Unavailable minutes (K5):** `kube_deployment_spec_replicas` and
  `kube_deployment_status_replicas_available` for `lex-backend-<rid>` (`lex-devenv-<rid>`
  for dev instances), through `query_range` at a 60-second step over the day.
- **Starts (K6):** the pod lifecycle timestamps of §2.2 for pods named `lex-backend-<rid>-…`
  whose `kube_pod_created` falls on D. The container start is
  `kube_pod_container_state_started{container="django-backend"}`; it moves on a container
  restart, which is why restarts are counted separately. They are joined on `(namespace, pod)` and written as
  one `StartEvent` each, with the four phase durations, the outcome and the
  init-container reason of a failed start. The cause is classified against the
  controller's operations of the same instance (§4.2 K6), read in SQL as UTC (§6). Pods
  live at least as long as their start, so a nightly query sees every start of the
  previous day while the pod's series are retained.
- **Idle hours (K6):** hours of D in which `increase(...[1h])` over the API selection is
  zero.
- **Container restarts (K6):** the increase over D of
  `kube_pod_container_status_restarts_total` for the backend container.

The label names and selectors are settings, so a mismatch is a configuration change.

#### 5.4.6 Version (R1)

lex-analytics never calls an instance and holds no API key, so the controller observes
versions. Its nightly job checks each backend pod's readiness
(`Kubectl.get_pod_readiness`). For a ready pod it reads the encrypted health token from
the in-cluster service, `http://lex-backend-<rid>.<namespace>.svc:7000/health`, never the
public URL, and decrypts it with the instance's API key inside the controller. It stores
the result as an `InstanceVersionObservation`. The inventory returns the latest
observation per instance.

lex-analytics then takes the version for day D from these sources, in order:

1. For the newest day collected: the controller's observation, if it was made after the
   end of D and no deploy-type operation finished between the end of D and the
   observation. A release at 01:00 UTC is therefore never credited to the day before. The
   observation sees the running version whatever the traffic. Source `observed`.
2. From R3: the instance's `lex_runtime_versions` table. Among rows with `first_seen`
   before the end of D, take the one with the greatest `least(last_seen, end of D)`, and on
   a tie the latest `first_seen`. That is the version seen running most recently by the end
   of D. It is right for a version still running after D, an upgrade during D, and a
   rollback, where a newer version stops being seen. The table only moves when the
   instance serves requests, so it ranks after the observation.
3. The previous day's stored version, when no deploy-type operation finished in between.
   Source `carried`.
4. `unknown`.

A re-run never replaces an `observed` version with a `carried` or `unknown` one. Version
age and releases behind come from the PyPI release list, cached per run.

### 5.5 Storage

In lex-analytics' own database (one per environment), owned by its `fleet_metrics` app:

| Table | Grain | Holds | Release |
|---|---|---|---|
| `CollectionRun` | one per run | trigger, days covered, instances expected / ok / failed, status, heartbeat outcome | R1 |
| `InstanceDay` | one per instance per UTC day | a snapshot of the dimensions (instance name, client, type, release stage, status, autoscaling, lex-app version and its source, recorder coverage state) and a status per source | R1 |
| `MetricValue` | one per instance, day, metric and dimension | the value; long format, so a new metric is a new key, not a migration | R1 |
| `Threshold` | one per threshold version | KPI, metric, kind (`planning`/`operational`), scope (`fleet`/`instance`), comparator, value, minimum sample, `valid_from`, status, reason, Linear issue; append-only | R1 |
| `StartEvent` | one per backend pod start | instance, UTC day, cause, outcome, four phase durations, failure reason | R2 |
| `DeployEvent` | one per deploy operation | instance, UTC day, operation type, outcome, duration, version, failure reason | slot for K8's owners |

A view, `fleet_metrics_daily`, joins `InstanceDay` and `MetricValue` for dashboards.
`InstanceDay` is unique on (instance id, day). The instance is identified by the
controller's numeric id, kept as a snapshot with its name and dimensions, so purging an
instance in the controller neither deletes its history nor breaks a reference.

**Size.** About 62 instances × 25 values a day, roughly 1,600 rows a day, and a handful
of start events. The data is kept indefinitely, with no pruning job, so there is nothing
to tend.

**Metric catalogue** (the contract between KPI and data):

| Metric | Dimension | KPI | Source | Release |
|---|---|---|---|---|
| `writers_1d`, `writers_7d`, `writers_28d` | — | K1 (writers) | audit | R1 |
| `calc_runs` | outcome: `success`, `error`, `aborted`, `cancelled` | K3 | audit | R1 |
| `calc_seconds_p50`, `calc_seconds_p95` | — | K3 | audit | R1 |
| `version_age_days`, `version_releases_behind` | — | K7 | version, PyPI | R1 |
| `api_requests` | class: `2xx`, `3xx`, `4xx`, `5xx_unavailable`, `5xx_app` | K5 | Prometheus | R2 |
| `backend_unavailable_minutes` | — | K5 | Prometheus | R2 |
| `backend_starts` | cause | K6 | `StartEvent` | R2 |
| `backend_failed_starts` | — | K6 | `StartEvent` | R2 |
| `backend_container_restarts` | — | K6 | Prometheus | R2 |
| `api_idle_hours` | — | K6 | Prometheus | R2 |
| `active_people_1d`, `active_people_7d`, `active_people_28d` | — | K1 | recorder | R3 |
| `area_people_28d` | `<area>:<channel>`; channel `api`, `ui`, `ui_embed` or `ui_all` | K2 | recorder | R3 |
| `editors_1d` | — | the recorder-silence check (§7) | audit | R3 |
| `api_seconds_p95` | — | K9 | Prometheus | R4 |

Startup percentiles are computed from `StartEvent` when reading.

### 5.6 Reading it: Grafana

- **Datasource.** A PostgreSQL datasource on lex-analytics' database, with a dedicated
  role that can `SELECT` only the `fleet_metrics` tables and view. That database holds
  no credentials of any kind, so the datasource cannot expose any.
- **Who creates it.** Terraform creates the role, the organization, the datasource and the
  dashboards in the cluster-services layer. The password never leaves Terraform.
- **Grants.** Every collector run grants `SELECT` on the fleet-metrics tables to that role,
  so the order in which the two changes deploy does not matter.
- **Dashboards**, in a *Fleet metrics* folder, kept as data in the infrastructure
  repository:
  - **Product:** K1, K3 and K7 in R1; K2 and exact K1 added in R3.
  - **Platform:** K5 and K6 in R2; K8 when its owners deliver; K9 in R4.
  - **Collection** (R1): coverage per source and release, the invariants of §7, the run
    history and the threshold log. This is the dashboard for each one-week check.

  Variables for instance, client, type and release stage, defaulting to production. A
  per-customer conversation and the aggregate product view are the same dashboard with
  different filters. Threshold lines come from the `Threshold` table, valid per day.
  Dashboard time zone: UTC.
- **Access.** The internal team only, through the existing Keycloak login, in a **separate
  Grafana organization**. In Grafana's open-source edition every member of an
  organization can query every datasource in it, so only an organization boundary
  isolates one.

### 5.7 Data protection

- **No personal data leaves an instance database.** The recorder's table holds a UTC date,
  an internal user id, an area and a channel. That is less than the audit log already
  holds about the same people.
- **The recorder and the interface events can be switched off per instance** (§5.3.1).
  The collector then reports K1 and K2 as disabled, not zero.
- **Instances on a separate database server are excluded by default** (§5.4.3).
- **The public documentation says so.** One paragraph in lex-app-docs, upstream of the
  docs mirror, on what the recorder stores and that only counts leave the instance.

### 5.8 Prometheus: is it proportionate? (LEX-995)

**Which KPIs need it.** K5, K6 and K9 (R2 and R4). R1 does not query Prometheus, so the
first release carries no Prometheus dependency at all.

**What exists versus what is new.**

| Need | Exists | New |
|---|---|---|
| API requests and status per instance | `nginx_ingress_controller_requests`, labels verified on test (§5.4.5) | — |
| Response time per instance | the ingress duration histogram | — |
| Replicas wanted and available | kube-state-metrics deployment series | — |
| Startup phases | kube-state-metrics pod lifecycle timestamps, all five present on test | — |
| Retention through December | 180 days configured; 500 GiB volume in production | confirm the depth actually held (below) |
| Scrape configs, exporters, recording rules, Prometheus storage | — | **none** |

**Cost.** About eight instant or range queries per instance-day, roughly 500 a night for
the fleet. Each touches a few series: the test cluster's ingress controller holds about
120 active request series at a time. That is less than one person scrolling a per-instance dashboard. Operational
effort is nil: the collector's own heartbeat covers it, and a failed Prometheus source
marks itself without blocking the database sources.

**Findings that shape the plan.**

- **Configured retention is a ceiling, not a guarantee.** The test Prometheus is set to 180
  days but holds data only from 18 August 2026. The production depth could not be read
  with the access used for this check. Infra confirms it before R2's backfill (§8.1,
  stage R2.0). Until then, K5 and K6 baselines are stated from the depth found, not from
  180 days.
- **Absent series are zeros.** The ingress controller writes no series for an instance
  without traffic, so absence on an answered query is recorded as zero requests.
- **The test volume will fill before retention prunes it.** The test volume grows about
  0.47 GB a day (27 of 63 GB used), so it fills around late December. This affects the
  test environment only; it is noted for Infra.

**Simpler alternatives considered.**

- A `kubectl` snapshot has no history: it gave §4.4's K6 snapshot and nothing else.
- Ingress access logs are not retained or parsed anywhere.
- A timer in lex-app middleware misses the 502/503 answers the ingress gives when the
  backend is down, and needs a release.
- Prometheus is the simplest source that both has history and sees unavailability.

**Verdict.** Proportionate: phase 1 reuses existing series and adds no infrastructure. The
safeguards stay as designed: counts not identities (§5.2, rule 1), per-instance
separation (§5.4.5), UTC boundaries (§6), zeros versus gaps (§5.2, rule 3, and above),
catch-up (§5.4.2), and no wake-ups (§5.2, rule 5).

---

## 6. Time-zone rules

1. **A day is a UTC calendar day:** `[D 00:00:00Z, D+1 00:00:00Z)`.
2. **The recorder's day** is `datetime.now(timezone.utc).date()`. The interface's per-tab
   dedupe also keys on the UTC date, so its time zone never matters.
3. **Every boundary goes to Postgres as an aware UTC datetime parameter.** Timestamp
   columns are compared as `timestamptz` to `timestamptz`. `date_trunc` and `::date` are
   never applied to a timestamp in SQL: their result depends on the session time zone,
   which is Berlin in lex-app sessions.
4. **The Instance Controller's ORM is naive Berlin, and the collector never feeds it naive
   Berlin boundaries.** On 2026-10-25 the UTC midnight boundary is 02:00 CEST, inside the
   hour Berlin repeats when summer time ends. Postgres resolves an ambiguous local time
   with the offset in force after the change (CET), which puts that boundary at 01:00Z: an
   hour late. Aware values pass through Django's PostgreSQL backend unchanged even with
   `USE_TZ=False`. Instance Controller timestamps are read in SQL as
   `… AT TIME ZONE 'UTC'`, or as durations computed in SQL.
5. **Prometheus** queries are evaluated at the day's end instant, in Unix seconds;
   kube-state-metrics timestamps are Unix seconds already.
6. **Grafana** dashboards are set to UTC; day filters arrive as epoch seconds.
7. **Tests pin every rule** at 2026-10-25 (summer time ends) and 2027-03-28 (summer time
   starts), including a query run in a session whose `TimeZone` is `Europe/Berlin`.

**Known limit.** Instances still on a lex-app version from before the move to
`USE_TZ=True` (July 2026) can store audit timestamps up to two hours off. For day buckets
this only moves activity between midnight and 02:00 UTC; K7's version dimension
identifies those instances.

---

## 7. Silence is loud

"Something registered but never reached looks identical to something working" (LEX-751).

- **The heartbeat means "rows were written", not "the job ran".** After a scheduled run,
  the collector pings a Better Stack heartbeat only if yesterday's row exists for every
  expected instance and each delivered source succeeded for at least 95% of them.
  Otherwise it calls the heartbeat's failure endpoint, naming the failing sources. No ping
  within 27 hours (24 hours plus 3 hours' grace) opens an incident. The controller's
  error-log alert matches only the backend and worker pods, so the heartbeat is the
  collector's alarm.
- **Recorder silence is detected from the data (R3).** If the recorder is enabled and the
  audit shows a person successfully changed data on day D, the recorder must have seen at
  least one person that day: `editors_1d > 0` implies `active_people_1d ≥ 1`. A violation
  marks the instance-day `recorder_silent`, fails the heartbeat, and shows on the
  Collection dashboard. `editors_1d` counts people with a **successful, non-calculation**
  audit row on D: a calculation's audit row can be written after midnight with no request
  that day, and a failed save leaves an audit row the recorder never sees.
- **Interface silence (R3)** is the `ui_silent` mark of §5.3.2: shown on the dashboard,
  not a heartbeat failure.
- **Invariants on the Collection dashboard:**
  - 1-day ≤ 7-day ≤ 28-day counts;
  - `active_people_1d ≥ editors_1d`, wherever the recorder is enabled;
  - one row per expected instance per day since go-live;
  - every `StartEvent` that is not `failed` has ready ≥ created.
- **Tests exercise the path that is reached, not the registration.**
  - The recorder's tests send real requests through the full middleware stack and assert
    rows.
  - The interface's tests assert the call made at each emitting place.
  - The collector's tests run its real SQL against a Postgres holding the audit and
    recorder tables in lex-app's shape.
  - A lex-app test pins the columns the collector depends on.

---

## 8. Delivery: stages, dates and estimate

### 8.1 Stages

The **proposed go-live of R1 is Tuesday 27 October 2026**, the first scheduled run. If
the plan is approved later than 14 October, every date moves by the same number of
working days. Each one-week check is booked as a dated Linear issue at its go-live, not
left to memory. Cycles: 5 is 5–18 October, 6 is 19 October – 1 November, 7 is 2–15
November, 8 is 16–29 November.

| Stage | Output | Owner | Prerequisite | Evidence of done | Date | Effort |
|---|---|---|---|---|---|---|
| **S0** Plan approved | This revision, the plans, the deck | Fleet-metrics owner (the LEX-749 assignee); review: product lead | LEX-988–995 answered | Reviewer approves the value mapping, ranking and provisional rules in Linear | 14 Oct (cycle 5) | in LEX-988–995 |
| **R1.1** Build | lex-analytics R1 (inventory client, instance-database and version sources; storage incl. `Threshold`; run, catch-up, heartbeat, command; image); the controller's side of the contract (nightly grants and version observations, inventory endpoint); both CronJobs, heartbeat, reader role, analytics database, Grafana organization, Product (R1) and Collection dashboards | Fleet-metrics owner (LEX-751) | S0 | The lex-analytics repository created; all PRs merged; tests green, including the two summer-time dates; a run in the test environment writes a row for every test instance and records its heartbeat as `skipped` (test has no Better Stack monitor) | 15–23 Oct | 29.5 h |
| **R1.2** Production go-live | First scheduled run; backfill 1 Jan → 26 Oct | Fleet-metrics owner; Infra applies Terraform | R1.1; Grafana organization members named | §8.3 go-live checks pass, including the 25 October boundary from the backfill | 27 Oct | 3 h |
| **R1.3** Baseline review | Baselines for K1, K3, K7; provisional values in `Threshold` | Fleet-metrics owner proposes; product lead approves | R1.2 | Approval recorded in Linear; rows in `Threshold` | by 30 Oct | 2 h |
| **R1.4** One-week check | The acceptance that matters | Fleet-metrics owner | 7 unattended days | §8.3 one-week checks; check issue closed with evidence | 3 Nov | 1 h |
| **K8.1** Handoff | Named owners in Infra and Instance Controller; event contract; delivery date | Fleet-metrics owner coordinates; owners decide | S0 | Owners recorded on LEX-993; contract agreed; `DeployEvent` interface accepted or replaced | owners by 16 Oct; contract by 30 Oct | 2 h (coordination) |
| **R2.0** Retention depth | The oldest sample in the production Prometheus | Infra | — | Value recorded on LEX-995 | by 30 Oct | Infra |
| **R2.1** Build and go-live | Prometheus source: K5, K6 (`StartEvent`), Platform dashboard; backfill as deep as R2.0 found | Fleet-metrics owner | R1.4 accepted; R2.0 | Spot checks within 1% of PromQL; three start events (a deploy, a restart, a resume) match pod and operation records within one scrape interval; baseline review done | build 3–6 Nov; go-live 10 Nov | 10.5 h |
| **R2.2** One-week check | as R1.4 for the Platform sources | Fleet-metrics owner | 7 days | as R1.4 | 17 Nov | 0.5 h |
| **R3.1** Build | Recorder, `api/usage/area` and docs paragraph (lex-app); area events (interface); collector reads both; K2 panels | Fleet-metrics owner, or a second developer in parallel from 19 Oct | S0 | PRs merged; §5.3.2 end-to-end verification on test | build by 13 Nov | 16 h |
| **R3.2** Releases and fleet upgrade | A frontend release, a lex-app release that pins it, and the upgrade of production instances | Release: fleet-metrics owner. Upgrade: Instance Controller owners, from K7's list | R3.1 | K7 shows the release on the upgraded instances; the §5.3.2 production verification recorded | releases by 13 Nov; upgrade 16–27 Nov (cycle 8) | upgrade: owners |
| **R3.3** One-week check | K2 and exact K1 | Fleet-metrics owner | 7 days after the first production upgrade | §7 invariants hold; coverage per instance matches K7 | one week after the first upgrade | 0.5 h |
| **R4** K9 | p95 per instance on R2's source | Fleet-metrics owner | R2.2 accepted | Spot check within 1% of PromQL | cycle 8 | 1.5 h |
| **D** December review | Q4 numbers against the agreed planning rules; 2027 plan inputs | Product lead with the fleet-metrics owner | R1–R3 | A written decision per KPI | 15 Dec | — |

**Necessary for the first release, and nothing else:**

- the lex-analytics component, its image and its CronJob;
- the controller's nightly job and inventory endpoint;
- one heartbeat and the Secret Manager entries for the token and the role passwords;
- the analytics database, the reader role, one Grafana organization and two dashboards.

No lex-app release, interface release, Prometheus query or fleet upgrade is in R1's path.

### 8.2 Estimate, reconciled with the first proposal

The first proposal's 35 hours covered nine KPIs in one go-live and was never approved as
an estimate. Bottom-up for the revised scope:

| Stage | Hours |
|---|---|
| R1 (collector 15, controller side of the contract 4, repository and image 2, infrastructure 8.5, go-live 3, baseline review 2, one-week check 1) | 35.5 |
| R2 (Prometheus source 3, startup measurement 3, Platform dashboard 2, go-live 1.5, baseline review 1, one-week check 0.5) | 11 |
| R3 (recorder 6, recorder reads 0.5, interface channel 0.5, endpoint 1.5, interface events 5, K2 panels 1, end-to-end verification 1.5, one-week check 0.5) | 16.5 |
| R4 (K9) | 1.5 |
| K8 coordination | 2 |
| **Total** | **66.5** |

From 35 to 66.5:

| Change | Hours |
|---|---|
| K4 rejected | −0.5 |
| K8 built by its owners, coordinated here (−0.5 build, +2 coordination) | +1.5 |
| Threshold table and three baseline reviews (LEX-989) | +4.5 |
| Startup duration and start causes (LEX-992) | +3 |
| Interface area events and their verification (LEX-991) | +8.5 |
| Three go-lives and three one-week checks instead of one (LEX-994) | +3.5 |
| Dashboards split across releases | +2 |
| K9 as its own small release | +1.5 |
| A standalone component (decision, 8 October): controller side of the contract 4, repository and image 2, own database and reader role 1.5 | +7.5 |
| **Net** | **+31.5** |

**R1 alone is 35.5 hours, LEX-751's 35-hour budget plus half an hour.** The standalone
component costs 7.5 hours in R1 and saves the later move out of the controller. R2 to R4
and the K8 coordination, 31 hours, need approval as a
follow-up budget; the review decides it at S0. The fleet upgrade (R3.2) and K8's build
are the owners' effort and are estimated by them.

### 8.3 Acceptance

**At each go-live:**

- The scheduled run's `CollectionRun` is `ok`, and Better Stack shows the heartbeat.
- Every expected production instance has yesterday's row. At least 95% have each
  delivered source `ok`. Excluded instances are listed with their reason.
- **Spot check** on three instances (a busy one, a quiet one, a paused one):
  - recompute the release's metrics by hand: `writers_28d` and `calc_runs` with SQL on the
    instance database in R1; `api_requests` with PromQL in R2;
  - the stored values match: exactly for database counts, within 1% for Prometheus.
- **Boundary check** (R1) on one busy instance: count writes from 23:00Z to 01:00Z
  across 24/25 October (the summer-time change) by hand, and confirm the split.
- **K3 validation** (R1): on two instances, compare a week of `calc_runs` with the
  distinct runs in `CalculationLog`. More than 5% apart means a recorder hook replaces
  the rule (§4.2 K3).
- **Start events** (R2): three classified starts checked against the pod's conditions or
  the controller's operation times.

**One week later** is the acceptance that matters (LEX-751):

- seven consecutive days of rows for every expected instance, with no gaps;
- every failure heartbeat explained, and healed by the catch-up;
- the spot check repeated for a day in the middle of the week;
- from R3, the §7 invariants hold on every day where the recorder is enabled.

Deployment alone is never acceptance.

### 8.4 The K8 handoff

- This project, as coordinator, records the receiving owners on LEX-993 once they agree.
  Names are not assumed.
- The owners decide:
  - the deployment event contract (attempts, outcome, duration, version, failure reason);
  - whether `InstanceOperation` gains version and failure-reason fields, or a new record
    takes them;
  - how the rows reach `DeployEvent` (§5.5);
  - their thresholds;
  - their delivery date.
- Historical coverage: `InstanceOperation` holds every operation since the controller
  began, without version or failure reason; the owners state how history is treated.
- The Platform dashboard has a K8 panel slot that reads `DeployEvent`. It shows *not yet
  delivered* until rows arrive.

---

## 9. Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| The fleet is upgraded late | K2 and exact K1 cover few instances in December | R1 gives writers on every instance from day one; K7 drives the upgrade list; coverage shown on the Collection dashboard; K2's December limit stated (§4.2 K2) |
| A KPI turns out to be defined wrongly | The phase-1 numbers mislead | Every day is recomputable from durable sources (§5.2) |
| A threshold turns out to be wrong | A decision is made on the wrong line | Thresholds are provisional, baseline-relative, versioned, and applied at read time (§4.4) |
| Production Prometheus holds less than expected | K5 and K6 baselines are short | R2.0 measures the depth before R2; baselines are stated from what exists |
| The collector loads the shared database server | Customers feel it at night | Primary-key ranges only (§5.4.4), one instance at a time, a 60-second limit, 02:30 UTC |
| The calculation rule miscounts | K3 is off | Validated at R1 go-live (§8.3); a recorder hook is the fallback |
| The reader role is granted too much | The analytics component could read customer data | Grants name four tables only, with no default privileges; a test proves another table is refused; read-only transactions and aggregate-only queries on top |
| The controller's contract changes under the component | The nightly run fails | Both sides pin the contract in tests; fields are only added, never removed or renamed without a version bump; a failed inventory fails the heartbeat |
| Instances on versions before `USE_TZ=True` | Up to two hours of skew at day boundaries | Negligible for daily counts; flagged by the version dimension |
| K8's owners are not confirmed in time | No deploy reliability in Q4 | K8 is outside the critical path; the panel shows *not yet delivered*; escalated at the R1 one-week check |
| The interface events are overridden by an older frontend package | K2 silently drops for that instance | `ui_silent` on the Collection dashboard (§5.3.2) |
| Someone outside the internal team reaches the fleet data | One customer sees another's usage | A separate Grafana organization holding the only fleet datasource; a role limited to the fleet tables (§5.6) |

---

## 10. Follow-ups outside phase 1

- Per-instance operational alerting, and who responds to it (LEX-451). The operational
  flags of §4.4 are its natural input.
- Instance and release tags in error tracking, so errors become attributable per instance.
- The Instance Controller's instance list should probe versions through the in-cluster
  service (§5.4.6) instead of the public URL, so opening the list cannot wake a
  scaled-to-zero backend.
- Aggregate-only SQL functions in each instance database, so that the reader role would
  need no table `SELECT` at all.
- A recorder hook at the terminal calculation audit, if K3's validation calls for it.
- Moving migrations out of backend startup, if K6 shows preparation dominates startup.

---

## 11. Defaults taken here, reversible by configuration

| Default | Setting |
|---|---|
| Instances on a separate database server are excluded | `FLEET_METRICS_INCLUDE_DB_ALIASES=false` |
| The recorder and the interface events are on (R3) | `LEX_USAGE_METRICS=true` |
| Dashboards default to release stage `production`, type `prod` | dashboard variables |
| The heartbeat alerts the fleet-metrics owner (the LEX-749 assignee) | Better Stack policy, in Terraform |
| The CronJob runs in the production and test controller environments only | chart values |
| Thresholds start provisional and baseline-relative | rows in `Threshold`, status `provisional` |
