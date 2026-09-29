# Fleet metrics, phase 1: the KPIs and the architecture

**Status:** design — the week-1 deliverable of LEX-750; awaiting review
**Date:** 2026-09-29
**Linear:** [LEX-749](https://linear.app/lundgroup/issue/LEX-749) (umbrella) ·
[LEX-750](https://linear.app/lundgroup/issue/LEX-750) (week 1: this document) ·
[LEX-751](https://linear.app/lundgroup/issue/LEX-751) (week 2: the build)
**Plans:** one per repository, in `docs/superpowers/plans/`:
`2026-09-29-fleet-metrics-phase1-collector.md` (lex-instance-controller-backend),
`2026-09-29-fleet-metrics-phase1-infrastructure.md` (instance-controller-deployment),
`2026-09-29-fleet-metrics-phase1-recorder.md` (lex-app)

---

## 1. What this document decides

LEX-749 asks for analytics and monitoring that are live early in October and then record,
untended, until the end of the year, so that the numbers can guide the 2027 plan. LEX-750
asks for three things before anything is built: settle whether this is about instances,
product usage or both; write down each KPI with the decision it lets us make; and agree an
architecture week 2 can build without further design calls. This document does all three:

1. **Scope** (§3): both, product-led. Product usage is the new build; platform health is
   harvested from data that is already being recorded.
2. **The KPI register** (§4): nine KPIs, each with the question it answers in December and
   the change to the 2027 plan it could cause.
3. **The architecture** (§5): what is collected where, where it lands, how long it is kept,
   and who can read it.
4. **Two correctness rules that have cost this codebase before** (§6 time zones, §7
   silence).
5. **Week 2** (§8): the build order, the estimate, and the check a week after go-live that
   is the real acceptance.

**The name.** In the product, *Analytics* is already taken: it is the Streamlit tab on a
record and a list. This project is called **fleet metrics** everywhere, in code, tables
and dashboards, so that "the analytics are broken" never has to be disambiguated.

---

## 2. What exists today

Read from the source, not assumed: lex-app `lex-app-v2` at `d48449bf`,
lex-instance-controller-backend `main` at `1eb03f0`, instance-controller-deployment
`develop`, LEX_TERRAFORM_MODULES `main`.

### 2.1 Platform data is already recorded, per instance, for 180 days

kube-prometheus-stack scrapes the ingress controller and kube-state-metrics, with a
180-day retention. For every instance, Prometheus holds request counts and durations by
host, ingress rule path and exact status code
(`nginx_ingress_controller_requests`, `…_request_duration_seconds_bucket`), and the
backend Deployment's available replicas (`kube_deployment_status_replicas_available`).
Per-instance Grafana dashboards already chart memory, CPU, volume use and requests per
minute from the same series.

What does not exist is any fleet view or daily rollup: nothing turns these series into a
number someone reads in December.

### 2.2 Product activity is partly recorded, in each instance's database

- **`AuditLog`** — one row per API create, update and delete (a bulk write creates one row
  per record): `date`, `author`, `resource`, `action`, `payload`. `author` is the person's
  display string, an API key's name for API-key requests, or `system…` for the framework.
  The table has no index on `date` or `author`.
- **`AuditLogStatus`** — one per audit row, `pending` → `success`/`failure`, with
  `created_at` and `updated_at`.
- **Calculations are countable from those two tables alone.** A calculation's root audit
  row ends with its terminal `is_calculated` state in the payload
  (`lex/audit_logging/utils/calculation_audit.py::_build_payload`), and a plain edit of a
  calculation row resets `is_calculated` to `NOT_CALCULATED` before it is audited
  (`lex/api/views/model_entries/One.py::_should_reset_is_calculated_for_update`). So a
  root audit row whose payload holds `SUCCESS`, `ERROR`, `ABORTED` or `CANCELLED` is a
  finished run, and its status row's `created_at`/`updated_at` bracket it.
- **Not recorded anywhere:** reads, page views, exports, history browsing, search,
  Streamlit use, flows, and anything an API key reads.

### 2.3 Nothing in lex-app runs unattended

Autoscaled backends scale to zero an hour after the last ingress request, and Celery
workers are KEDA jobs. No lex-app process is guaranteed to be up at any given time. The
periodic executors that run regardless of traffic are Instance Controller CronJobs
(`reap-stale-operations`, every 15 minutes) and pg_cron inside Cloud SQL.

### 2.4 What the Instance Controller knows

Every instance (`LexInstance`: type `prod`/`dev`/`ai`, release stage, status, client),
every operation with its start, end and outcome (`InstanceOperation`), each instance's
database credentials (already used by the database export job) and each instance's API
key. It does **not** know which lex-app version ran when: the version exists only inside
the instance's encrypted `/api/health` token, fetched live when someone opens the
instance list.

### 2.5 Two time-zone conventions meet here

- **lex-app:** `USE_TZ=True`; `TIME_ZONE` is `LEX_TIME_ZONE`, default `Europe/Berlin`; the
  Postgres session also runs in Berlin (`lex/lex_app/settings.py:838-839`). A naive
  `as_of` is read as UTC; a naive value anywhere else is read as Berlin.
- **Instance Controller:** `USE_TZ=False`, `TIME_ZONE='Europe/Berlin'`: every datetime in
  its code and ORM is naive Berlin local time.

Each convention is consistent on its own. A pipeline that reads from one and writes into
the other is exactly where the defect class behind LEX-564 comes back, so §6 states the
rules and the tests that pin them.

---

## 3. Scope: both, product-led

**Product usage is the main build.** The 2027 plan is mostly product decisions: what to
invest in, what to retire, which customers need attention. Apart from writes, nothing
recorded today can inform any of them.

**Platform health is harvested, not built.** The data exists (§2.1). Phase 1 adds a daily
per-instance snapshot of the few platform numbers that support a 2027 decision
(availability, cold starts, versions, deploys, response time) into the same store as
product usage. Both readings then come from one place with one per-instance and aggregate
structure.

**Out of scope for phase 1:**

- Routing operational alerts to people when an instance misbehaves. That is an operations
  process with its own owner and response rules (LEX-451), not a KPI.
- Cost per instance (FinOps, LEX-837).
- LEX AI usage (its own KPI register, LEX-646).
- Frontend click streams and page-view tracking.
- Customer-facing dashboards.

The one alert that is in scope is the pipeline's own: the collector announces when it
stops recording (§7).

---

## 4. The KPI register

Nine KPIs. Each passes LEX-750's three tests: it names a decision, it means the same
thing in December as in October, and it records without anyone tending it.

The decision rules below are **proposals**. They are written now so that every number is
collected for a stated purpose, and they are to be confirmed or revised at the December
review. They are not alert thresholds.

A **production instance**, wherever this document counts them, is one with release stage
`production` and type `prod`. Every instance is collected; the filter applies only when
reading (§5.6).

| # | KPI | The question it answers in December | Coverage from day one |
|---|---|---|---|
| K1 | Active people per instance, 28 days | Which instances are in real use, and which are dormant? | A proxy on every instance, backfilled; the exact count per instance from its upgrade to the recorder release |
| K2 | Feature reach | Which product areas get 2027 investment, and which are retired? | From the recorder release; calculations on every instance from day one |
| K3 | Calculation reliability | Is the calculation engine a 2027 reliability or performance theme? | Every instance, backfilled |
| K4 | Integration share | Does the integration surface (API, MCP) get a 2027 track? | Writes on every instance, backfilled; reads from the recorder release |
| K5 | Request success | Which reliability work does 2027 fund, and what availability can be promised? | Every instance; up to 180 days back |
| K6 | Cold starts | Does scale-to-zero stay the default in 2027? | Every autoscaled instance; up to 180 days back |
| K7 | Version currency | Does 2027 need an upgrade policy? | Every running instance; history from the recorder release |
| K8 | Deploy reliability | Is the deploy path a 2027 item? | Every instance, full history |
| K9 | Response time (supporting) | Is performance a 2027 theme, and for which instances? | Every instance; up to 180 days back |

### K1 — Active people per instance

- **Definition.** Distinct people who made at least one successful authenticated request
  to the instance in the 28 UTC days ending on day D. The same over 1 and 7 days is kept
  as supporting detail.
- **Proxy until an instance has the recorder:** distinct people who changed data in the
  window (`AuditLog` authors that are neither API keys nor the system), called *active
  writers*. It undercounts people who only read.
- **Decision.** An instance with at most one active person for eight consecutive weeks goes
  on the 2027 review list: re-onboard, pause or retire (the last two save cost). The
  instances with the most active people get first call on customer-success time.
- **Why 28 days.** Finance customers work in monthly cycles. A 7-day window would mark a
  month-end-only user dormant three weeks out of four.
- **Limit.** Counts are per instance and do not add up across instances: one person on
  two instances counts twice. Fleet totals are labelled *person-instance pairs*.

### K2 — Feature reach

- **Definition.** For each product area, the number of production instances where at
  least one person used it in the 28 days ending D, and the share of that instance's
  active people who did.
- **Areas:** grid · records (view, create, edit and delete, through pages and drawers) ·
  history (the history tab, as-of mode) · exports · calculations · calculation logs ·
  flows · reports · Streamlit · search · files (downloads, SharePoint). The mapping from
  endpoints to areas is in §5.3.
- **Decision.** An area reached by fewer than three production instances across Q4 gets no
  new investment in 2027 and is assessed for simplification or removal. The three areas
  with the widest reach get the 2027 performance and polish budget.
- **Limits.** A request classifies an endpoint, not a UI element: a reference-field
  dropdown that calls the list endpoint counts as *grid*. The measure is presence per
  person per day, not request counts, so an area that is called repeatedly cannot inflate
  itself.

### K3 — Calculation reliability

- **Definition.** Finished calculation runs per day by outcome (success, error, aborted,
  cancelled). The **failure share** is (error + aborted) / finished. Cancelled runs are a
  person's choice and are not failures. Also the p50 and p95 duration of successful runs,
  from the request that started the run to its terminal state: queue wait included,
  because that is what the person waits for.
- **Decision.** A fleet failure share above 5% in any month of Q4 makes calculation
  reliability a named 2027 theme. Instances whose p95 duration grows month over month get
  a capacity review (worker concurrency, the KEDA maximum).
- **Source.** `AuditLog` and `AuditLogStatus` (§2.2), bucketed by the day the status row
  reached its terminal state.
- **Limit.** This is a rule over existing tables rather than a dedicated record. Week 2
  checks it against `CalculationLog` on two instances (§8.3). If the two disagree by more
  than 5%, phase 2 adds a recorder hook at the terminal audit and replaces the rule.

### K4 — Integration share

- **Definition.** The share of data changes made through API keys, against people and the
  system (initial data uploads, calculation bookkeeping). From the recorder: API keys
  active in the last 28 days, and the areas they touch, which covers integrations that
  only read.
- **Decision.** If API-key writes are at least 20% of writes on five or more production
  instances in Q4, the integration surface gets its own 2027 track. The same numbers are
  evidence for the MCP decision due this year (LEX-74).
- **Classification of `AuditLog.author`:** `system…` or empty → system; a name in
  `rest_framework_api_key_apikey`, or `Technical User` → API key; anything else → person.

### K5 — Request success

- **Definition.** Of the API requests reaching the instance's backend (the instance host,
  ingress rule `/api`, WebSocket upgrades excluded), the share answered with a 5xx, split
  into **unavailable** (502, 503, 504: the backend is down, starting or scaled to zero)
  and **application errors** (every other 5xx).
- **Decision.** Unavailable above 1% fleet-wide in any Q4 month funds platform
  availability work (startup, probes, scale-to-zero) ahead of features. Application errors
  above 0.5% fund code quality. The Q4 figure is the ceiling for any availability
  commitment written into a 2027 contract.
- **Why not synthetic uptime probes.** A probe through the ingress every minute would keep
  every autoscaled backend awake, because KEDA scales on ingress requests. That would
  change both the cost and the very thing being measured. K5 measures what people
  experienced.
- **Limit.** Prometheus' `increase` slightly undercounts short-lived ingress-controller
  pods. Shares are unaffected in practice; absolute request counts are approximate.

### K6 — Cold starts

- **Definition.** For autoscaled instances: backend starts from zero replicas per day, and
  the minutes per day with no available backend replica.
- **Decision.** Read together with K5's unavailable share on the same instances, this
  decides whether scale-to-zero stays the 2027 default, and for which tier. Minutes scaled
  down is the saving. Starts, and the unavailable responses around them, are the price
  people pay.
- **Limit.** The Instance Controller's instance list currently probes each instance's
  public health URL, and that can itself wake a backend (§10). Until that changes, starts
  are an upper bound on starts that people caused.

### K7 — Version currency

- **Definition.** The lex-app version each instance ran on day D; its age in days (D minus
  that version's release date on PyPI); and the number of distinct versions in production.
- **Decision.** If more than a third of production instances run a version older than 90
  days in December, 2027 gets an upgrade policy: a support window and scheduled fleet
  upgrades. The version is also the dimension that shows whether K3 and K5 improved after
  a release.
- **Source** (§5.4.6): the recorder's version table; before an instance has it, the
  health token fetched through the in-cluster service, never through the ingress;
  otherwise the previous day's version carried forward when no deploy happened in between.

### K8 — Deploy reliability

- **Definition.** Operations that apply the instance's infrastructure and release
  (`CREATE`, `RECOVER`, `RECONFIGURE`, `RELEASE`, `CHANGE_CLIENT`, `UPDATE_INFRA`, `PAUSE`,
  `RESUME`) finished per day, their failure share, and their p95 duration. The operation
  type is kept as a dimension.
- **Decision.** A failure share above 10% in Q4, or p95 durations approaching the backend's
  ten-minute startup budget, puts the deploy path into 2027 (for example, running
  migrations outside the startup window).
- **Source.** `InstanceOperation`, in the Instance Controller.

### K9 — Response time (supporting)

- **Definition.** The p95 duration of API requests (K5's selection, excluding 5xx) per
  instance per day.
- **Decision.** Instances with a p95 above two seconds for four consecutive weeks get a
  performance investigation. Fleet-wide drift across Q4 makes performance a 2027 theme.
- **Limit.** Per-instance p95s cannot be combined into a fleet p95, so the fleet view
  shows how many instances are above the threshold instead.

### 4.1 Considered and not adopted

| Candidate | Why not |
|---|---|
| Page views, clicks, session length | No decision beyond K1 and K2, and it needs a frontend release |
| Synthetic uptime per instance | Wakes scaled-to-zero backends (K5) |
| Error counts from error tracking | All instances report to one project with no instance tag, so nothing is attributable per instance today (§10) |
| CPU, memory, storage | Already on the per-instance dashboards; the decisions they serve (sizing, cost) belong to FinOps (LEX-837) |
| Keycloak login events as active users | Attributed per Keycloak client, and several instances share a client; long sessions produce no login events |
| Pod restarts and OOM kills | No decision beyond K5 |

---

## 5. Architecture

### 5.1 Overview

```
 Instance database (one per instance)            Instance Controller (one per environment)
┌──────────────────────────────────────┐        ┌─────────────────────────────────────────┐
│ audit_logging_auditlog       exists  │        │ CronJob collect-fleet-metrics            │
│ audit_logging_auditlogstatus exists  │◄───────│   daily 02:30 UTC                        │
│ lex_usage_daily        new: recorder │ read-  │   1. inventory and operations (own DB)   │
│ lex_runtime_versions   new: recorder │ only,  │   2. instance DB: aggregates only        │
└──────────────────────────────────────┘ counts │   3. Prometheus: ingress, kube-state     │
                 ▲                        only  │   4. version: runtime table, else the    │
                 │ presence rows                │      in-cluster health service           │
   lex-app web process                          │   5. upsert one row per instance per day │
   UsageRecorderMiddleware                      │   6. Better Stack heartbeat, only if the │
                                                │      rows were written                   │
 Prometheus (180-day retention) ◄───────────────│                                          │
                                                └──────────────────┬──────────────────────┘
                                                                   │ fleet_metrics tables
                                                                   ▼
                                                Grafana, read-only role: Product,
                                                Platform and Collection dashboards
```

There are three parts:

1. **The recorder**, inside lex-app, writes one presence row per person, product area and
   day into the instance's own database.
2. **The collector**, inside the Instance Controller, reads aggregates from every instance
   database, from Prometheus and from its own tables once a day. It writes one row per
   instance per day into its own database.
3. **Grafana** reads those rows through a role that can see nothing else.

### 5.2 Principles

1. **Aggregate at the source; identities never leave an instance.** The collector's
   instance-database queries return counts and durations only. No user id, name, record
   id or payload crosses into the Instance Controller.
2. **Every day can be recomputed.** The collector derives day D for an instance from
   durable sources and upserts the result. Running it twice changes nothing. Running it in
   November for an October day, after a KPI definition has been corrected, corrects that
   day. The sources outlive the window: the audit and recorder tables and the Instance
   Controller's tables are kept, and Prometheus keeps 180 days, longer than October to
   December. This is the direct answer to LEX-749's named risk, *the wrong KPIs,
   instrumented well*: a wrong definition over these sources can be corrected
   retroactively.
3. **Explicit zeros and explicit gaps.** Every expected instance gets a row for every day,
   with a status per source. A missing row, a failed source and a genuine zero are three
   different things in the data.
4. **UTC days** (§6).
5. **Never wake an instance.** The collector reads databases and Prometheus directly. Its
   one HTTP call to an instance goes to the in-cluster service, and only when the backend
   is already running.
6. **Silence is loud** (§7).
7. **Store counts; derive rates when reading.** Fleet rates are sums of numerators over
   sums of denominators, never averages of per-instance rates.

### 5.3 The recorder (lex-app)

**Tables.** They are raw SQL, installed by a `lex.core` migration exactly as
`0001_bitemporal_activation` installs the activation applier's table, and for the same
reason: the framework registers every concrete Django model under `lex/` for history
tracking and for the model list, and an operational table belongs in neither.

```sql
CREATE TABLE IF NOT EXISTS lex_usage_daily (
    day         date        NOT NULL,              -- UTC calendar day
    actor_kind  text        NOT NULL,              -- 'person' | 'api_key'
    actor_id    text        NOT NULL,              -- auth_user.id, or the API key's name
    area        text        NOT NULL,              -- see the area map below
    first_seen  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (day, actor_kind, actor_id, area)
);

CREATE TABLE IF NOT EXISTS lex_runtime_versions (
    version        text        PRIMARY KEY,
    first_seen     timestamptz NOT NULL DEFAULT now(),
    last_seen      timestamptz NOT NULL DEFAULT now(),
    usage_enabled  boolean     NOT NULL
);
```

**Middleware.** `UsageRecorderMiddleware` is the last entry in `MIDDLEWARE`, after the
Keycloak permissions middleware, so the user it sees is final. It acts after the view has
returned:

- It records only successful requests (status below 400), not `OPTIONS` or `HEAD`, and
  only requests that resolved to a URL name.
- **Actor.** An authenticated Django user is recorded as `('person', user.pk)`. An API-key
  request is recorded as `('api_key', key name)`. The instance's own platform key
  (`LEX_API_KEY`, the Instance Controller's) is never recorded. Anything else is skipped.
- **Area.** Taken from the URL name (table below). A list request carrying `as_of` counts
  as history.
- **Write.** Insert-once (`ON CONFLICT DO NOTHING`), deduplicated in process memory by
  (day, actor, area). A busy person therefore costs one insert per area per day per
  process.
- **The day** is `datetime.now(timezone.utc).date()`, never `timezone.localdate()`, which
  is Berlin.

**Runtime version.** On the first request a process serves, and at most hourly after
that, the recorder upserts `lex_runtime_versions` with the running `lex._version.__version__`.
This gives K7 a version that the collector can read without waking anything, and it gives
each instance a version history (`first_seen` per version is the upgrade date) that exists
nowhere today.

**Failure isolation.** Any exception inside the recorder is caught and logged at most once
per process per hour. The response is never affected. Recording failures surface through
the collector (§7), not through the request.

**Switch.** `LEX_USAGE_METRICS=false`, set per instance through the controller's custom
environment, stops usage rows. The version row is still written, with
`usage_enabled = false`, so the collector reports *recorder disabled* rather than *nobody
used it*.

**Area map** (URL names of the process admin site,
`lex/process_admin/sites/process_admin_site.py`):

| Area | URL names |
|---|---|
| `grid` | `model-entries-list` without `as_of` |
| `history` | `model-entries-list` with `as_of`; `model-history-list` |
| `records` | `model-one-entry-read-update-delete`, `model-one-entry-create`, `model-many-entries` |
| `exports` | `model-export` |
| `calculation_logs` | `calculationlog-tree`, `init-calculation-logs`, `download-markdown-pdf` |
| `flows` | `process`, `run_step` |
| `reports` | `htmlreports` |
| `streamlit` | `streamlit_token` |
| `search` | `global-search` |
| `files` | `file-download`, `sharepoint-file-download`, `sharepoint-file-share-link`, `sharepoint-file-preview-link` |
| `app` | any other authenticated request (model structure, project info, permissions, …) |

`calculations` is deliberately not a recorder area. K3's audit source covers it on every
instance, including those that have not upgraded.

### 5.4 The collector (Instance Controller)

#### 5.4.1 Where it runs

The collector is a new Django app, `fleet_metrics`, in lex-instance-controller-backend,
run by a new CronJob, `collect-fleet-metrics`, in the Instance Controller chart. The
CronJob follows `reap-stale-operations`: same image, same environment and secret, same
service account. It adds `timeZone: Etc/UTC`, schedule `30 2 * * *`,
`concurrencyPolicy: Forbid` and a two-hour deadline. Its command is
`python manage.py collect_fleet_metrics --skip-checks`.

#### 5.4.2 What a run does

- **Expected instances:** every `LexInstance` whose status is not `DELETED` or `DELETING`.
  Paused instances are included, so they produce explicit zeros.
- **Days:** yesterday, plus every day in the previous 35 whose row is missing or has a
  failed source. This catch-up means a week-long outage heals itself on the next
  successful run. `--from` and `--to` give explicit ranges for backfill, and `--instance`
  restricts a run to one instance.
- **Per instance and day,** each source runs independently. A failing source marks only
  itself. The results are written in one transaction per instance and day, as an upsert.
- **The run ends** with the heartbeat decision (§7).

#### 5.4.3 Reaching an instance database

The collector connects with the instance's own database credentials, the ones the
database export job already uses. It connects to the direct Cloud SQL service rather than
the connection pooler. Every instance's queries run inside `BEGIN READ ONLY`, with
`SET LOCAL statement_timeout = '60s'` and `SET LOCAL TIME ZONE 'UTC'`, and every query is
an aggregate.

Instances whose database lives on a separate server (`db_server_alias`, used for
compliance isolation) are skipped by default and recorded as `excluded`. The setting
`FLEET_METRICS_INCLUDE_DB_ALIASES` opts them in.

**Why not a dedicated read-only role in phase 1.** A dedicated role is the better
boundary, and it is the phase-2 hardening (§10). But it needs a grant installed into every
instance database at deploy time, and re-granted for tables created by later migrations.
Those are more moving parts that can fail silently, on the one path that has to record
unattended. Phase 1's guarantee rests on the read-only transaction and on aggregate-only
queries, and tests cover both.

#### 5.4.4 Windows without a date index

`AuditLog` and `AuditLogStatus` have no index on their timestamps, can be large, and live
on a database server that every customer shares. So the collector never scans them by
date:

1. It finds the id range of a time window by binary search over the primary key. Each
   probe is `SELECT id, <ts> FROM … WHERE id >= %s ORDER BY id LIMIT 1`, an index lookup.
2. It widens that range by ten minutes at each end.
3. It filters by timestamp exactly inside the range.

The search is valid because ids and insert timestamps rise together: the timestamp is set
immediately before the insert. The ten-minute margin absorbs the millisecond inversions of
concurrent inserts. Calculation runs are bounded by the status row's `created_at` over the
two days before D, since workers stop at 24 hours, and then filtered on `updated_at`.

#### 5.4.5 Prometheus

The collector uses the `PROMETHEUS_HOST` already present in the Instance Controller's
settings. Queries are evaluated at the end of day D (`D+1 00:00:00Z`) with `[1d]` windows.
The series are selected per instance as follows:

- **API traffic:** `ingress="ingress-<rid>"`, `host="<the instance's host>"`,
  `path="/api(/|$)(.*)"`, `status!="101"`, plus the namespace label in clusters where
  several controller environments share one cluster. `ingress-<rid>` alone would mix the
  single-page app, the API, Streamlit, the AI host and Flower.
- **Backend replicas:** `kube_deployment_status_replicas_available` for `lex-backend-<rid>`
  (`lex-devenv-<rid>` for dev instances), fetched with `query_range` at a 60-second step
  over the day.

The label names are checked against the live Prometheus as week 2's first task. The
selectors are settings, so a mismatch is a configuration change, not a code change.

#### 5.4.6 Version

The collector tries these sources in order:

1. The instance's `lex_runtime_versions` row with the latest `last_seen` before the end of
   D.
2. The encrypted health token from the in-cluster service
   (`http://lex-backend-<rid>:7000/api/health`). This is used only when collecting the most
   recent day, and only when Prometheus shows an available replica at that moment. It
   never uses the public URL.
3. The previous day's version, when no operation of K8's kinds finished in between.
4. `unknown`.

Version age is the version's release date on PyPI (cached per run).

#### 5.4.7 Deploy facts

`InstanceOperation` rows of K8's kinds that finished on D are copied as one `DeployEvent`
each, keyed by operation id, so fleet percentiles can be computed when reading. Their
timestamps are read in SQL as UTC (§6).

### 5.5 Storage

The data lives in the Instance Controller's database (one per environment), in new tables
owned by the `fleet_metrics` app:

| Table | Grain | Holds |
|---|---|---|
| `CollectionRun` | one per run | trigger, days covered, instances expected / ok / failed, status, heartbeat outcome |
| `InstanceDay` | one per instance per UTC day | a snapshot of the dimensions (instance name, client, type, release stage, status, autoscaling, lex-app version and its source, recorder state) and a status per source |
| `MetricValue` | one per instance, day, metric and dimension | the value; long format, so a new metric is a new key, not a migration |
| `DeployEvent` | one per operation | instance, UTC day, operation type, outcome, duration |

A view, `fleet_metrics_daily`, joins `InstanceDay` and `MetricValue` for dashboard queries.

`InstanceDay` is unique on (instance id, day). The foreign key to the instance is nullable,
and the numeric instance id is kept as a snapshot, as `ReleaseAudit` does. Purging an
instance therefore neither deletes its history nor fails because of it.

**Size.** Roughly 55 instances × 45 values a day is about 2,500 rows a day, under a million
a year. The data is kept indefinitely, with no pruning job, so there is nothing to tend.

**Metric catalogue** (the contract between KPI and data):

| Metric | Dimension | KPI | Source |
|---|---|---|---|
| `writers_1d`, `writers_7d`, `writers_28d` | — | K1 proxy | audit |
| `editors_1d` | — | the recorder-silence check (§7) | audit |
| `writes` | channel: `person`, `api_key`, `system` | K4 | audit |
| `calc_runs` | outcome: `success`, `error`, `aborted`, `cancelled` | K3 | audit |
| `calc_seconds_p50`, `calc_seconds_p95` | — | K3 | audit |
| `calc_people_28d` | — | K2 (calculations) | audit |
| `active_people_1d`, `active_people_7d`, `active_people_28d` | — | K1 | recorder |
| `area_people_28d` | area | K2 | recorder |
| `api_keys_28d` | — | K4 | recorder |
| `area_api_keys_28d` | area | K4 | recorder |
| `api_requests` | class: `2xx`, `3xx`, `4xx`, `5xx_unavailable`, `5xx_app` | K5 | Prometheus |
| `api_seconds_p95` | — | K9 | Prometheus |
| `backend_starts` | — | K6 | Prometheus |
| `backend_down_minutes` | — | K6 | Prometheus |
| `version_age_days` | — | K7 | version and PyPI |

K8 reads `DeployEvent` directly.

### 5.6 Reading it: Grafana

- **Datasource.** A PostgreSQL datasource on the Instance Controller's database, with a
  dedicated role that can `SELECT` only the `fleet_metrics` tables and view. The Instance
  Controller's database holds instance credentials, so Grafana must never read it with the
  controller's own role.
- **Who creates it.** Terraform creates the role and the datasource in the cluster-services
  layer, which already manages Grafana's database role. The password never leaves
  Terraform.
- **Grants.** The `fleet_metrics` migration grants `SELECT` on its own tables to that role
  if the role exists, and every collector run re-applies the grant. The order in which the
  two changes are deployed therefore does not matter.
- **Dashboards.** Three, in a *Fleet metrics* folder, kept as JSON in the infrastructure
  repository:
  - **Product:** K1 to K4.
  - **Platform:** K5 to K9.
  - **Collection:** coverage per source, the invariants of §7 and the run history. This is
    the dashboard for the check a week after go-live.

  Each dashboard has variables for instance, client, type and release stage, defaulting to
  production. A per-customer conversation and the aggregate product view are the same
  dashboard with different filters. Dashboard time zone: UTC.
- **Access.** The internal team, through the existing Keycloak login. No customer access in
  phase 1.

### 5.7 Data protection

- **No personal data leaves an instance database.** The new instance table holds a UTC
  date, an internal user id and an area: no time of day, no record and no content. That is
  less than the audit log already holds about the same people.
- **The recorder can be switched off per instance** (§5.3). The collector then reports that
  instance's K1 and K2 as disabled, not as zero.
- **Instances on a separate database server are excluded by default** (§5.4.3).
- **The public documentation says so.** lex-app's documentation gets one paragraph on what
  the recorder stores and on the fact that only counts leave the instance. It goes in
  lex-app-docs, which is upstream of the docs mirror.

---

## 6. Time-zone rules

1. **A day is a UTC calendar day:** `[D 00:00:00Z, D+1 00:00:00Z)`.
2. **The recorder's day** is `datetime.now(timezone.utc).date()`.
3. **Every boundary goes to Postgres as an aware UTC datetime parameter.** Timestamp columns
   are compared as `timestamptz` to `timestamptz`. `date_trunc` and `::date` are never
   applied to a timestamp in SQL, because their result depends on the session time zone,
   which is Berlin in lex-app sessions and whatever the connection says elsewhere.
4. **The Instance Controller's ORM is naive Berlin, and the collector never feeds it naive
   Berlin boundaries.** On 2026-10-25 the UTC midnight boundary is 02:00 CEST, inside the
   hour Berlin repeats when summer time ends. Postgres resolves an ambiguous local time
   with the offset in force after the change (CET), which puts that boundary at 01:00Z: an
   hour late. Aware values pass through Django's
   PostgreSQL backend unchanged even with `USE_TZ=False`. Instance Controller timestamps
   are read in SQL as `… AT TIME ZONE 'UTC'`, or as durations computed in SQL, never
   through the ORM's naive values.
5. **Prometheus** queries are evaluated at the day's end instant, in Unix seconds.
6. **Grafana** dashboards are set to UTC.
7. **Tests pin every rule** at 2026-10-25 (summer time ends) and 2027-03-28 (summer time
   starts), including a query run in a session whose `TimeZone` is `Europe/Berlin`.

**Known limit.** Instances still on a lex-app version from before the move to
`USE_TZ=True` (July 2026) can store audit timestamps up to two hours off. For day buckets
this only moves activity between midnight and 02:00 UTC, which is negligible for daily
counts, and K7's version dimension identifies those instances.

---

## 7. Silence is loud

"Something registered but never reached looks identical to something working" (LEX-751).

- **The heartbeat means "rows were written", not "the job ran".** After a scheduled run,
  the collector pings a Better Stack heartbeat only if yesterday's row exists for every
  expected instance and each source succeeded for at least 95% of them. Otherwise it calls
  the heartbeat's failure endpoint, naming the failing sources. No ping within 27 hours
  (a 24-hour period plus 3 hours' grace) opens an incident. The collector's pod is not
  covered by the controller's error-log alert, which matches only the backend and worker
  pods, so the heartbeat is its alarm.
- **Recorder silence is detected from the data.** If the recorder is enabled on an instance
  and the audit shows a person successfully changed data on day D, the recorder must have
  seen at least one person that day: `editors_1d > 0` implies `active_people_1d ≥ 1`. A
  violation marks the instance-day `recorder_silent`, fails the heartbeat, and shows on
  the Collection dashboard.
  - `editors_1d` counts people with a **successful, non-calculation** audit row on D, and
    is used for this check only. Both exclusions prevent false alarms. A calculation's
    audit row can be created when the run ends, possibly after midnight, with no request
    that day. A failed save leaves an audit row too, but the recorder only records
    successful requests.
- **Invariants on the Collection dashboard:**
  - 1-day ≤ 7-day ≤ 28-day counts;
  - `active_people_1d ≥ editors_1d`, wherever the recorder is enabled;
  - one row per expected instance per day since go-live.
- **Tests exercise the path that is reached, not the registration.** The recorder's tests
  send real requests through the full middleware stack and assert rows; they do not merely
  check that the middleware is listed. The collector's tests run its real SQL against a
  Postgres holding the audit and recorder tables in lex-app's shape. A lex-app test pins
  the columns the collector depends on, so a lex-app change that would break the collector
  fails lex-app's own CI.

---

## 8. Week 2: order, estimate and acceptance

### 8.1 Order

1. **Verify the Prometheus labels** live (read-only queries).
2. **The collector** (Instance Controller) with the audit, controller, Prometheus and
   version sources. This records every instance from its first night, with no lex-app
   upgrade.
3. **The infrastructure:** the CronJob, the heartbeat, the reader role, the datasource and
   the dashboards.
4. **The test environment, then production.** Backfill: audit and controller sources from
   2026-01-01, Prometheus sources as far back as retention reaches.
5. **The recorder** (lex-app), in the next release, in parallel with steps 2–4. Production
   instances pick it up when upgraded. Proposed: a fleet upgrade in cycle 5 (Oct 5–18), so
   that K1 and K2 cover the fleet for most of Q4.
6. **The check a week after go-live** (§8.3).

### 8.2 Estimate

| Part | Repository | Hours |
|---|---|---|
| Collector: models, sources, runner, command, heartbeat, tests | lex-instance-controller-backend | 18 |
| CronJob, heartbeat, reader role, datasource, three dashboards | instance-controller-deployment | 8 |
| Recorder: SQL, migration, middleware, tests, documentation paragraph | lex-app, lex-app-docs | 6 |
| Deploy, verify, backfill | — | 3 |
| **Total** | | **35** |

That matches LEX-751's 35 hours. The recorder is independent of the other parts and can be
built by someone else at the same time.

### 8.3 Acceptance

**At go-live:**

- The scheduled run's `CollectionRun` is `ok`, and Better Stack shows the heartbeat.
- Every expected production instance has yesterday's row. At least 95% have the database
  and Prometheus sources `ok`. Excluded instances are listed with their reason.
- **Spot check** on three instances (a busy one, a quiet one and an autoscaled one):
  - recompute `writes`, `writers_28d`, `calc_runs` and `api_requests` by hand, with SQL on
    the instance database and PromQL;
  - the stored values match: exactly for the database counts, within 1% for the Prometheus
    ones.
- **Boundary check** on one busy instance: count writes from 23:00Z to 01:00Z across one
  midnight by hand, and confirm the split between the two days.
- **K3 validation:** on two instances, compare a week of `calc_runs` with the number of
  distinct runs in `CalculationLog`. If they disagree by more than 5%, K3 becomes a phase-2
  recorder hook (§4, K3).

**One week later** is the acceptance that matters (LEX-751). It is scheduled at go-live as
a dated Linear issue assigned to the fleet-metrics owner, not left to memory:

- seven consecutive days of rows for every expected instance, with no gaps;
- every failure heartbeat explained, and healed by the catch-up;
- the spot check repeated for a day in the middle of that week;
- where the recorder is enabled, the invariants of §7 hold on every day.

---

## 9. Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| The fleet is upgraded late | K1 and K2 cover only some instances | Day-one proxies (active writers, calculation people) on every instance; coverage shown on the Collection dashboard; a fleet upgrade proposed for cycle 5 |
| A KPI turns out to be defined wrongly | The phase-1 numbers mislead | Every day is recomputable from durable sources (§5.2); a corrected definition is re-run over the whole window |
| The collector loads the shared database server | Customers feel it at night | Primary-key ranges only (§5.4.4), one instance at a time, a 60-second statement timeout, 02:30 UTC |
| The calculation rule miscounts | K3 is off | Validated in week 2 (§8.3); a recorder hook is the fallback |
| Prometheus labels differ from the chart | K5, K6 and K9 are empty | The first task verifies them; the selectors are settings |
| The collector holds instance credentials | A bug could read more than aggregates | Read-only transactions, aggregate-only queries, tests, review; a dedicated role in phase 2 |
| Instances on versions before `USE_TZ=True` | Up to two hours of skew at day boundaries | Negligible for daily counts; flagged by the version dimension |
| The controller's instance list wakes backends | K6 overstates starts that people caused | Stated as a limit of K6; a follow-up changes the probe (§10) |

---

## 10. Follow-ups outside phase 1

- Per-instance operational alerting, and who responds to it (LEX-451).
- Instance and release tags in error tracking, so that errors become attributable per
  instance.
- The Instance Controller's instance list should probe versions through the in-cluster
  service (§5.4.6) instead of the public URL, so that opening the list stops waking
  scaled-to-zero backends.
- A dedicated read-only database role, with aggregate-only functions in each instance
  database (the hardening deferred in §5.4.3).
- A recorder hook at the terminal calculation audit, if K3's validation calls for it.

---

## 11. Defaults taken here, reversible by configuration

| Default | Setting |
|---|---|
| Instances on a separate database server are excluded | `FLEET_METRICS_INCLUDE_DB_ALIASES=false` |
| The recorder is on | `LEX_USAGE_METRICS=true` |
| Dashboards default to release stage `production`, type `prod` | dashboard variables |
| The heartbeat alerts the fleet-metrics owner (the LEX-749 assignee) | Better Stack policy, in Terraform |
| The CronJob runs in the production and test controller environments only | chart values |
