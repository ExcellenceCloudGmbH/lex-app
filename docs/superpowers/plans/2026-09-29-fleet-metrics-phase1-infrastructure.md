# Fleet metrics phase 1 — the infrastructure (instance-controller-deployment) Implementation Plan

> **Superseded in part (2026-10-08).** The collector became the standalone component
> **lex-analytics**, with its own repository and database. It reads instance databases
> through the read-only role `lex_analytics_reader` and gets inventory, versions and
> operations from the Instance Controller's endpoint (contract:
> `lex-analytics/docs/contract-instance-controller.md`). R1 is implemented there and in the
> controller's `fleet_analytics` app. This plan stays the reference for the R2–R4 logic, the
> metric definitions and the tests, adapted to that layout. See spec §5.1 and §5.4.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the collector every night, hear about it when it stops writing, and give the internal team — and nobody else — Grafana dashboards over what it wrote, each release adding the panels for the KPIs it delivers.

**Architecture:** A `collect-fleet-metrics` CronJob in the Instance Controller chart, enabled per environment through its values file. A second Better Stack heartbeat, whose URL travels to the CronJob through Secret Manager and `app-env-secret`, exactly like `KEYCLOAK_DB_PASSWORD`. In the cluster-services layer: a login role for Grafana with no table privileges (the collector grants SELECT on its own tables, every run), a separate Grafana organization, a PostgreSQL datasource inside it, and the dashboards defined as HCL: Product and Collection in R1, Platform in R2, more panels in R3 and R4. Every threshold a panel shows is read from the collector's `Threshold` table, never written into a query.

**Tech Stack:** Helm 3, Terraform ~1.6 with Terragrunt 0.58, providers `BetterStackHQ/better-uptime`, `ricochet1k/postgresql`, `grafana/grafana`, `google`.

**Spec:** lex-app `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md`, revision 2 (§4.1 releases, §4.4 thresholds, §5.4.1 the CronJob, §5.5 storage, §5.6 Grafana, §5.8 Prometheus, §7 silence, §8.1 stages, §8.3 acceptance, §11 defaults)

## Releases

Spec revision 2 delivers fleet metrics in four releases (§4.1), with dated stages (§8.1). Build a release's tasks, run its rollout steps in Task 9, then start the next release. Each release is its own branch and PR, cut from `origin/develop` after the previous one merged.

| Release | What it shows | Tasks | Branch | Rollout (Task 9) |
|---|---|---|---|---|
| **R1** | K1 as active writers, K3, K7. No Prometheus | 1 CronJob · 2 heartbeat · 3 Secret Manager handoff · 4 reader role · 5 Grafana organization, datasource, **Product** (R1 panels) and **Collection** | `feat/fleet-metrics` | Step 1 R1.1 build, in test (15–23 Oct) · Step 2 R1.2 go-live (27 Oct) · Step 3 R1.4 one-week check (3 Nov) |
| **R2** | K5; K6 with startup time from `StartEvent`; K8's panel | 6 **Platform** dashboard; the start-event invariant on Collection | `feat/fleet-metrics-platform` | Step 4 R2.0 retention depth (Infra, by 30 Oct) · Step 5 R2.1 go-live (10 Nov) · Step 6 R2.2 check (17 Nov) |
| **R3** | K2 by area and channel; exact K1 | 7 Product R3 panels; silence marks and people invariants on Collection | `feat/fleet-metrics-k2` | Step 7 R3.3 check, one week after the first R3 upgrade |
| **R4** | K9 | 8 K9 on Platform | `feat/fleet-metrics-k9` | Step 8 R4 (cycle 8, 16–29 Nov) |

- R1 needs exactly what spec §8.1 lists for this repository: the CronJob, the heartbeat, the Secret Manager entry, the reader role, the Grafana organization and datasource, and two dashboards. Nothing in R1 reads Prometheus.
- Only R1 touches `chart/**`. R2–R4 change the Grafana module alone, which is applied by hand, so they need no chart bump and deploy nothing when they merge.
- Not built here. K4 is rejected (spec §4.3) and has no panel. K8 is measured by its owners, who fill `DeployEvent` (spec §4.2 K8, §8.4); Task 6 only draws it. Threshold values come from the baseline reviews (R1.3 by 30 Oct, R2's by 13 Nov) as rows in the collector's `Threshold` table; this plan draws them and never hard-codes one.

## Global Constraints

- Repository `instance-controller-deployment`; base `origin/develop` (the CronJob template and the Better Stack module exist only there); one branch and one PR into `develop` per release, named in the Releases table.
- Pushing `chart/**` to `develop` publishes the chart and deploys the **test** environment automatically; **the chart version in `chart/Chart.yaml` must be bumped** or CI republishes the old one. Only R1 changes the chart.
- The cluster-services layer is applied **by hand** (Terragrunt `before_hook` port-forwards to Cloud SQL); nothing applies it from CI. Prod platform is applied by the manual `gcp-ic-prod-deployment.yaml` workflow.
- GCP project IDs: test cluster `lex-test-343008` (environment `devops`), prod `superb-blend-305320`. Both clusters are called `lex-cluster-2`; check `kubectl config current-context` before anything that writes.
- Collector settings (read by the controller, defaults in its `settings.py`): `FLEET_METRICS_HEARTBEAT_URL`, `FLEET_METRICS_INCLUDE_DB_ALIASES` (default `false`), `FLEET_METRICS_READER_ROLE` (default `fleet_metrics_reader`), `FLEET_METRICS_CATCHUP_DAYS` (35), `FLEET_METRICS_PROMETHEUS_RETENTION_DAYS` (180), `FLEET_METRICS_API_PATH` (`/api(/|$)(.*)`), `FLEET_METRICS_NAMESPACE_LABEL` (`exported_namespace`). Only the heartbeat URL is set here; the others keep their defaults unless the collector plan's Prometheus check found different labels.
- `FLEET_METRICS_PROMETHEUS_RETENTION_DAYS` is a ceiling, not the depth. A backfill that includes Prometheus (R2, R4) never starts before the first full UTC day after the oldest sample (Task 9, Step 4): the collector records an absent series as zero requests (spec §5.8), so an earlier day would be stored as a day without traffic.
- The CronJob runs at `30 2 * * *` in `Etc/UTC`.
- Dashboards: time zone UTC; every day filter is TZ-independent (epoch seconds converted `AT TIME ZONE 'UTC'`), never `$__timeFilter` on a `date` column, whose cast to `timestamptz` follows the session time zone. `date_trunc` only on `day::timestamp` (without time zone); `generate_series` over days only with `::timestamp` bounds. Rates are sums of numerators over sums of denominators; a sum of per-instance people counts is labelled "person-instance pairs".
- Thresholds: never a literal in a query. A panel draws the `fleet_metrics_threshold` row valid on each day: `valid_from <= day`, the latest row per (kpi, metric, kind, scope), a later `id` winning a tie (spec §4.4, §5.5). Planning rules are dashed blue step lines named `planning rule (agreed)` / `planning rule (provisional)`, or a blue cell in a column named `planning rule`; operational flags are orange cells in a column named `operational flag` and never a line.
- No Grafana alert rules or contact points: an operational flag is a highlighted row, and routing alerts to people is LEX-451 (spec §3).
- The reader role sees only `fleet_metrics_*` relations, so every query reads only those.
- No personal data in this plan or in the PRs: Grafana organization members are asked for at apply time.
- Commit subjects are conventional and lower-case. Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| Path | Responsibility | Release |
|---|---|---|
| `chart/templates/fleet_metrics_cronjob.yaml` (create) | The CronJob | R1 |
| `chart/values.yaml` (modify) | `fleetMetrics` defaults (off) | R1 |
| `chart/Chart.yaml` (modify) | Version bump | R1 |
| `terraform/modules/instance-controller/values-prod.yaml`, `values-test.yaml` (modify) | Turn it on in prod and test | R1 |
| `terraform/modules/betterstack/main.tf` (modify) | The collector's heartbeat and its URL output | R1 |
| `terraform/layers/cluster-services/main.tf`, `variables.tf`, `providers.tf` (modify) | Secret for the URL; the reader role; the Grafana module | R1 |
| `terraform/modules/fleet-metrics-reader/main.tf` (create) | Grafana's login role on the Cloud SQL server | R1 |
| `terraform/modules/fleet-metrics-grafana/main.tf` (create) | Organization, datasource, folder, one resource per dashboard | R1 |
| `terraform/modules/fleet-metrics-grafana/dashboards.tf` (create; modified in R2, R3, R4) | Query rules, threshold helpers, variables, which panels each dashboard holds | R1 |
| `terraform/modules/fleet-metrics-grafana/panels_r1.tf` (create) | Product: K1 as active writers, K3, K7. Collection: coverage, invariants, runs, threshold log | R1 |
| `terraform/modules/fleet-metrics-grafana/panels_r2.tf` (create) | Platform: K5, K6, K8. Collection: the start-event invariant | R2 |
| `terraform/modules/fleet-metrics-grafana/panels_r3.tf` (create) | Product: K2 and exact K1. Collection: silence marks, people invariants | R3 |
| `terraform/modules/fleet-metrics-grafana/panels_r4.tf` (create) | Platform: K9 | R4 |
| `terraform/layers/platform/main.tf`, `variables.tf` (modify) | Read the URL into `app-env-secret` | R1 |
| `terraform/environments/prod/cluster-services/terragrunt.hcl`, `environments/prod/platform/terragrunt.hcl` (modify) | Turn it on in prod | R1 |
| `terraform/environments/devops/cluster-services/terragrunt.hcl` (modify) | The dashboards in the test Grafana, where each release's panels are checked first | R1 |

---

### Task 1: The CronJob (R1)

**Files:**
- Create: `chart/templates/fleet_metrics_cronjob.yaml`
- Modify: `chart/values.yaml`, `chart/Chart.yaml`, `terraform/modules/instance-controller/values-prod.yaml`, `terraform/modules/instance-controller/values-test.yaml`

**Interfaces:**
- Consumes: the collector's command `python manage.py collect_fleet_metrics` (collector plan, the command task).
- Produces: CronJob `collect-fleet-metrics`; values `fleetMetrics.enabled` (default `false`) and `fleetMetrics.schedule`.

- [ ] **Step 1: Write the template**

`chart/templates/fleet_metrics_cronjob.yaml`:

```yaml
{{ if .Values.fleetMetrics.enabled }}
{{ $imageUrl := printf "%s:%s" .Values.backend.image.name .Values.backend.image.tag -}}
# Fleet metrics (LEX-749): one row per instance per UTC day, written into the
# controller's database once a night. The Better Stack heartbeat it pings is
# its only alarm: the controller's error-log alert matches the backend and
# worker pods, not this one.
apiVersion: batch/v1
kind: CronJob
metadata:
  name: collect-fleet-metrics
spec:
  schedule: {{ .Values.fleetMetrics.schedule | quote }}
  timeZone: "Etc/UTC"
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 3600
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      activeDeadlineSeconds: 7200
      ttlSecondsAfterFinished: 86400
      template:
        metadata:
          labels:
            app: lex
            layer: maintenance
        spec:
          serviceAccountName: {{ .Values.k8sServiceAccounts.instanceController }}
          restartPolicy: Never
{{ if .Values.nodeSelector.enabled }}
          nodeSelector:
            cloud.google.com/gke-nodepool: {{ .Values.nodeSelector.nodepool }}
{{ end }}
{{ if .Values.toleration.enabled }}
          tolerations:
            - key: {{ .Values.toleration.key | quote }}
              operator: {{ .Values.toleration.operator | quote }}
              value: {{ .Values.toleration.value | quote }}
              effect: {{ .Values.toleration.effect | quote }}
{{ end }}
          containers:
            - name: collector
              image: {{ $imageUrl }}
              imagePullPolicy: Always
              command:
                - /bin/sh
                - -c
                - python manage.py collect_fleet_metrics --skip-checks
              envFrom:
                - configMapRef:
                    name: {{ .Values.backend.configMap }}
                - secretRef:
                    name: {{ .Values.backend.appEnvSecret }}
              env:
                - name: CLOUD_PROJECT_ID
                  value: {{ .Values.cloud.project_id }}
                - name: CLOUD_SQL_INSTANCE
                  value: {{ .Values.cloud.sqlInstance }}
                - name: CLOUD_REGION
                  value: {{ .Values.cloud.region }}
                - name: K8S_NAMESPACE
                  value: {{ .Values.namespace }}
                - name: POD_IP
                  valueFrom:
                    fieldRef:
                      fieldPath: status.podIP
              resources:
                requests:
                  cpu: 100m
                  memory: 256Mi
                limits:
                  memory: 768Mi
              volumeMounts:
                - name: service-connection-key-artifact-registry
                  mountPath: "/etc/sc-key-ar"
                  readOnly: true
          volumes:
            - name: service-connection-key-artifact-registry
              secret:
                secretName: gce-artifact-registry-service-connection-key
{{ end }}
```

- [ ] **Step 2: Add the defaults, off**

In `chart/values.yaml`, after the `staleOperationsReaper` block:

```yaml
# Daily fleet-metrics snapshot (LEX-749; see chart/templates/fleet_metrics_cronjob.yaml).
# Off by default; the prod and test values files turn it on.
fleetMetrics:
  enabled: false
  schedule: "30 2 * * *"
```

- [ ] **Step 3: Turn it on in prod and test**

Append to both `terraform/modules/instance-controller/values-prod.yaml` and `terraform/modules/instance-controller/values-test.yaml`:

```yaml
fleetMetrics:
  enabled: true
```

- [ ] **Step 4: Bump the chart**

In `chart/Chart.yaml`, change `version: 1.0.240` to the next patch version above whatever `develop` holds when you start (at the time of writing, `version: 1.0.241`).

- [ ] **Step 5: Render it and check**

Run: `helm lint chart && helm template ic chart --set fleetMetrics.enabled=true --set backend.image.name=img --set backend.image.tag=t | awk '/name: collect-fleet-metrics/,/volumes:/' | head -40`
Expected: lint passes; the rendered CronJob has `schedule: "30 2 * * *"`, `timeZone: "Etc/UTC"` and the command `python manage.py collect_fleet_metrics --skip-checks`.

Run: `helm template ic chart --set backend.image.name=img --set backend.image.tag=t | grep -c collect-fleet-metrics`
Expected: `0` — off unless a values file turns it on.

- [ ] **Step 6: Commit**

```bash
git add chart terraform/modules/instance-controller/values-prod.yaml terraform/modules/instance-controller/values-test.yaml
git commit -m "feat(chart): a nightly CronJob that collects fleet metrics, on in prod and test"
```

---

### Task 2: The heartbeat (R1)

**Files:**
- Modify: `terraform/modules/betterstack/main.tf`
- Modify: `terraform/layers/cluster-services/main.tf`

**Interfaces:**
- Produces: `betteruptime_heartbeat.fleet_metrics_collector`; module output `fleet_metrics_heartbeat_url` (sensitive); Secret Manager secret `FLEET_METRICS_HEARTBEAT_URL` in the cluster's project (only where Better Stack is enabled: prod).

- [ ] **Step 1: Add the heartbeat and its output to the module**

In `terraform/modules/betterstack/main.tf`, after `betteruptime_heartbeat.alertmanager_watchdog`:

```hcl
# --------------------------------------------------------------------------
# Fleet metrics collector (LEX-749). The controller's nightly CronJob pings this
# only when it has written yesterday's row for every instance (and fails it
# explicitly otherwise). Period one day, grace three hours: an incident at the
# latest ~27 h after the last good run. Counts towards the free-tier quota of
# 10 monitors + heartbeats; prod + dev use 6 before this one.
# --------------------------------------------------------------------------
resource "betteruptime_heartbeat" "fleet_metrics_collector" {
  name   = "Fleet metrics collector (${var.environment_label} cluster)"
  period = 86400
  grace  = 10800

  email = true
  call  = false
  sms   = false
  push  = false
}
```

At the end of the file:

```hcl
output "fleet_metrics_heartbeat_url" {
  value       = betteruptime_heartbeat.fleet_metrics_collector.url
  sensitive   = true
  description = "Heartbeat URL the fleet-metrics CronJob pings (FLEET_METRICS_HEARTBEAT_URL)."
}
```

- [ ] **Step 2: Hand the URL to Secret Manager**

In `terraform/layers/cluster-services/main.tf`, after the `keycloak_db_password_version` resource:

```hcl
# The platform layer reads cluster-services with skip_outputs, so the collector's
# heartbeat URL reaches the controller through Secret Manager, as the Keycloak
# database password does.
resource "google_secret_manager_secret" "fleet_metrics_heartbeat_url" {
  count    = var.betterstack_prometheus_alerting_enabled ? 1 : 0
  provider = google

  secret_id = "FLEET_METRICS_HEARTBEAT_URL"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "fleet_metrics_heartbeat_url" {
  count    = var.betterstack_prometheus_alerting_enabled ? 1 : 0
  provider = google

  secret      = google_secret_manager_secret.fleet_metrics_heartbeat_url[0].id
  secret_data = module.betterstack[0].fleet_metrics_heartbeat_url
}
```

Better Stack e-mails incidents to the account's on-call team, as it does for the Watchdog. Confirm with the user that the LEX-749 assignee receives those e-mails (spec §11); if not, add them to the team in Better Stack before go-live, since this heartbeat is the collector's only alarm.

- [ ] **Step 3: Validate the module**

Run: `cd terraform/modules/betterstack && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform fmt -check`
Expected: `Success! The configuration is valid.` and no files listed by `fmt`.

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/betterstack/main.tf terraform/layers/cluster-services/main.tf
git commit -m "feat(betterstack): a heartbeat the fleet-metrics collector pings after each good night"
```

---

### Task 3: The URL reaches the CronJob (R1)

**Files:**
- Modify: `terraform/layers/platform/main.tf`, `terraform/layers/platform/variables.tf`, `terraform/environments/prod/platform/terragrunt.hcl`

**Interfaces:**
- Consumes: secret `FLEET_METRICS_HEARTBEAT_URL` (Task 2).
- Produces: `FLEET_METRICS_HEARTBEAT_URL` in `app-env-secret`, which the CronJob gets through `envFrom`; variable `fleet_metrics_heartbeat_enabled` (default `false`).

- [ ] **Step 1: Add the variable**

In `terraform/layers/platform/variables.tf`:

```hcl
variable "fleet_metrics_heartbeat_enabled" {
  type        = bool
  default     = false
  description = "Read FLEET_METRICS_HEARTBEAT_URL from Secret Manager into the controller's app-env-secret. Only where cluster-services created that secret (Better Stack enabled: prod)."
}
```

- [ ] **Step 2: Read the secret, gated**

In `terraform/layers/platform/main.tf`, next to `data "google_secret_manager_secret_version" "keycloak_db_password"`:

```hcl
data "google_secret_manager_secret_version" "fleet_metrics_heartbeat_url" {
  count    = var.fleet_metrics_heartbeat_enabled ? 1 : 0
  provider = google-beta
  project  = var.configuration.gcp_project
  secret   = "FLEET_METRICS_HEARTBEAT_URL"
}
```

In the `app_env_secrets` map, after `KEYCLOAK_DB_PASSWORD`:

```hcl
      FLEET_METRICS_HEARTBEAT_URL: var.fleet_metrics_heartbeat_enabled ? data.google_secret_manager_secret_version.fleet_metrics_heartbeat_url[0].secret_data : ""
```

- [ ] **Step 3: Turn it on in prod**

In `terraform/environments/prod/platform/terragrunt.hcl`, inside `inputs = { … }`:

```hcl
  # LEX-749: the collector's Better Stack heartbeat (secret written by cluster-services).
  fleet_metrics_heartbeat_enabled = true
```

- [ ] **Step 4: Validate the layer**

Run: `cd terraform/layers/platform && terraform init -backend=false -input=false >/dev/null && terraform validate`
Expected: `Success! The configuration is valid.`

- [ ] **Step 5: Commit**

```bash
git add terraform/layers/platform terraform/environments/prod/platform/terragrunt.hcl
git commit -m "feat(platform): pass the fleet-metrics heartbeat URL to the controller"
```

---

### Task 4: Grafana's login role (R1)

**Files:**
- Create: `terraform/modules/fleet-metrics-reader/main.tf`
- Modify: `terraform/layers/cluster-services/main.tf`, `terraform/layers/cluster-services/variables.tf`

**Interfaces:**
- Produces: PostgreSQL role `fleet_metrics_reader` (login, random password, **no table privileges**); module outputs `user`, `password`.
- Relies on (collector plan, `fleet_metrics/grants.py`): at the start of every run, the collector grants this role SELECT on its own relations and on nothing else. From R1: `fleet_metrics_collectionrun`, `fleet_metrics_instanceday`, `fleet_metrics_metricvalue`, `fleet_metrics_threshold`, `fleet_metrics_deployevent` (K8's slot, empty until its owners fill it) and the view `fleet_metrics_daily`. From R2: `fleet_metrics_startevent`. A relation a later release adds is readable from the first run after its migration, whichever of the two repositories deploys first; until that run, a panel reading it shows "permission denied", which is why Task 9 runs the collector once by hand right after R2 deploys (Step 5). If K8's owners expose a view instead of filling `DeployEvent` (spec §4.2 K8), it joins that grant list in the collector, never a grant here.

- [ ] **Step 1: Write the module**

`terraform/modules/fleet-metrics-reader/main.tf`:

```hcl
# Grafana's login role for fleet metrics (LEX-749).
#
# It is created with no privileges on any table. The Instance Controller owns the
# fleet_metrics tables and grants this role SELECT on exactly those, at the start of
# every collection run (fleet_metrics/grants.py), because the tables are created by
# the controller's migrations after this role exists, and each release adds some
# (Threshold and the DeployEvent slot in R1, StartEvent in R2). CONNECT on the
# database and USAGE on the public schema are PostgreSQL's defaults for every role.
#
# Same connection pattern as modules/lex-postgres: the Terragrunt before_hook
# port-forwards to the Cloud SQL private IP, and initdeploy logs in.

terraform {
  required_providers {
    postgresql = {
      source = "ricochet1k/postgresql"
    }
  }
}

variable "role_name" {
  type    = string
  default = "fleet_metrics_reader"
}

variable "database_admin" {
  type    = string
  default = "initdeploy"
}

variable "database_admin_password" {
  type      = string
  sensitive = true
}

variable "forward_port" {
  description = "Ephemeral local port forwarded by Terragrunt"
  type        = number
}

provider "postgresql" {
  host            = "127.0.0.1"
  port            = var.forward_port
  scheme          = "postgres"
  username        = var.database_admin
  password        = var.database_admin_password
  sslmode         = "disable"
  connect_timeout = 15
  superuser       = false
}

resource "random_password" "reader" {
  length  = 32
  special = false
}

resource "postgresql_role" "reader" {
  name     = var.role_name
  login    = true
  password = random_password.reader.result
}

output "user" {
  value = postgresql_role.reader.name
}

output "password" {
  value     = postgresql_role.reader.password
  sensitive = true
}
```

- [ ] **Step 2: Add the switch to the layer**

In `terraform/layers/cluster-services/variables.tf`:

```hcl
variable "fleet_metrics_enabled" {
  type        = bool
  default     = false
  description = "Create Grafana's fleet-metrics reader role, organization, datasource and dashboards (LEX-749). The controller database is var.db_name."
}

variable "fleet_metrics_grafana_admins" {
  type        = list(string)
  default     = []
  description = "Grafana logins (email) that administer the fleet-metrics organization. Each must have signed in to Grafana once."
}

variable "fleet_metrics_grafana_viewers" {
  type        = list(string)
  default     = []
  description = "Grafana logins (email) that may view the fleet-metrics dashboards. Each must have signed in to Grafana once."
}
```

- [ ] **Step 3: Call the module**

In `terraform/layers/cluster-services/main.tf`, after the `module "prometheus"` block:

```hcl
# ---------------------------------------------------------------------------
# Fleet metrics (LEX-749): Grafana reads the controller's fleet_metrics tables
# through this role, inside its own organization (module fleet_metrics_grafana).
# ---------------------------------------------------------------------------
module "fleet_metrics_reader" {
  count  = var.fleet_metrics_enabled ? 1 : 0
  source = "../../modules/fleet-metrics-reader"

  database_admin          = data.google_secret_manager_secret_version.pg-admin-user.secret_data
  database_admin_password = data.google_secret_manager_secret_version.pg-admin-password.secret_data
  forward_port            = var.forward_port
}
```

- [ ] **Step 4: Validate**

Run: `cd terraform/modules/fleet-metrics-reader && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform fmt -check`
Expected: valid; nothing to format.

- [ ] **Step 5: Commit**

```bash
git add terraform/modules/fleet-metrics-reader terraform/layers/cluster-services/main.tf terraform/layers/cluster-services/variables.tf
git commit -m "feat(cluster-services): a login role for Grafana that owns nothing and reads nothing yet"
```

---

### Task 5: The Grafana organization, datasource, and the R1 dashboards (R1)

**Files:**
- Create: `terraform/modules/fleet-metrics-grafana/main.tf`, `terraform/modules/fleet-metrics-grafana/dashboards.tf`, `terraform/modules/fleet-metrics-grafana/panels_r1.tf`
- Modify: `terraform/layers/cluster-services/providers.tf`, `terraform/layers/cluster-services/main.tf`, `terraform/environments/prod/cluster-services/terragrunt.hcl`, `terraform/environments/devops/cluster-services/terragrunt.hcl`

**Interfaces:**
- Consumes: `module.fleet_metrics_reader[0].user/password` (Task 4); `module.cloud-sql-proxy.service` and `var.db_name` (existing in the layer); `module.gcp-secrets.grafana_password` (existing).
- Consumes (collector plan, R1 storage, spec §5.5). If the collector names a column differently, rename it here, not there:
  - `fleet_metrics_instanceday`: `id`, `instance_pk`, `day`, `instance_name`, `client_name`, `instance_type`, `release_stage`, `instance_status`, `autoscaling`, `lex_app_version`, `version_source` (`runtime_table`, `health`, `carried`, `unknown`), `recorder` (`absent`, `partial`, `disabled`, `enabled`), `controller_status`, `db_status`, `prometheus_status` (each `ok`, `error`, `excluded` or `skipped`; `skipped` is a source not delivered yet or not applicable), `errors`, `collected_at`.
  - `fleet_metrics_metricvalue` (`instance_day_id`, `metric`, `dimension`, `value`) and the view `fleet_metrics_daily` (`day`, `instance_pk`, `instance_name`, `client_name`, `instance_type`, `release_stage`, `instance_status`, `autoscaling`, `lex_app_version`, `recorder`, `metric`, `dimension`, `value`). Metric names are spec §5.5's catalogue; `calc_runs` carries all four outcomes every day, zeros included.
  - `fleet_metrics_collectionrun`: `started_at`, `finished_at` (naive Berlin, the controller's convention), `trigger`, `status`, `first_day`, `last_day`, `instances_expected`, `instance_days_written`, `instance_days_failed`, `heartbeat`, `summary`.
  - `fleet_metrics_threshold`: `id`, `kpi`, `metric`, `kind` (`planning`, `operational`), `scope` (`fleet`, `instance`), `comparator`, `value`, `min_sample`, `valid_from` (date), `status` (`provisional`, `agreed`), `reason`, `linear_issue`. Append-only.
- Consumes, as rows the baseline reviews write into `fleet_metrics_threshold` (R1.3 for K1, K3, K7; R2's review for K5, K6; R4 for K9). The dashboards read `value`, `min_sample` and `status`; `comparator` is shown in the threshold log, and each panel applies its rule's shape from spec §4.4. A review that changes a rule's numbers redraws history with no change here; one that changes its shape is a dashboard change.

  | Key | `kpi` | `metric` | `kind` | `scope` | `comparator` | `value` | `min_sample` | Drawn in |
  |---|---|---|---|---|---|---|---|---|
  | `k1_flag` | K1 | `writers_28d` | operational | instance | `<=` | most writers a weekly reading may hold and count as none (0) | weekly readings in a row (8) | Product K1 table and stat |
  | `k1_review` | K1 | `writers_28d` | planning | instance | `<=` | as `k1_flag` | as `k1_flag` (8) | Product K1 table |
  | `k3_share` | K3 | `calc_failure_share` | planning | fleet | `>` | highest monthly fleet failure share of Jan–Sep | finished runs in a month (50) | Product K3 monthly share |
  | `k3_flag` | K3 | `calc_failed_runs` | operational | instance | `>quantile_90d` | quantile of the instance's own daily failed runs over the 90 days before (0.95) | finished runs that day (5) | Product K3 flagged instance-days |
  | `k7_flag` | K7 | `version_releases_behind` | operational | instance | `>` | releases behind beyond which an instance is listed (2) | — | Product K7 tables and share |
  | `k7_share` | K7 | `version_behind_share` | planning | fleet | `>` | share of running instances behind the flag at the R1 baseline | — | Product K7 share |
  | `k5_share` | K5 | `api_unavailable_share` | planning | fleet | `>` | unavailable share of the R2 baseline period | — | Platform K5 share (R2) |
  | `k5_flag` | K5 | `backend_unavailable_minutes` | operational | instance | `>quantile_90d` | quantile of own daily minutes over 90 days (0.95) | API requests that day (100) | Platform K5 flagged (R2) |
  | `k6_wait` | K6 | `startup_seconds` | planning | fleet | `>` | the cold-start wait accepted at the R2 review, seconds | — | Platform K6 startup (R2) |
  | `k6_failed` | K6 | `backend_failed_starts` | operational | instance | `>` | failed starts an instance-day may have (0) | — | Platform K6 failed starts (R2) |
  | `k6_prep` | K6 | `preparation_seconds` | operational | instance | `>quantile_90d` | quantile of own preparation seconds over 90 days (0.95) | — | Platform K6 preparation (R2) |
  | `k9_p95` | K9 | `api_seconds_p95` | planning | instance | `>` | API p95 above which an instance counts, seconds (set at R4) | requests that day without 5xx (100) | Platform K9 (R4) |

  The values in brackets are spec §4.4's provisional starting points, not defaults: until a row exists, its line is absent and no cell is flagged, which is the expected state on go-live day. K2's planning rule is set in December (spec §4.4); it appears in the threshold log and nothing draws it.
- Produces: organization "Excellence Cloud — fleet metrics"; datasource uid `fleet-metrics`; folder uid `fleet-metrics`; dashboards `fleet-product` and `fleet-collection` (`fleet-platform` comes in Task 6).

Why an organization and not a folder: customers' users also sign in to this Grafana (their per-instance dashboards), and in Grafana's open-source edition every member of an organization can query every datasource in it. Only a separate organization keeps the fleet datasource out of their reach (spec §5.6).

- [ ] **Step 1: Add the provider**

In `terraform/layers/cluster-services/providers.tf`, inside `required_providers`:

```hcl
    grafana = {
      source  = "grafana/grafana"
      version = ">= 3.0"
    }
```

And a provider block (Grafana is reached through Cloudflare, like every other client of it):

```hcl
# Fleet metrics (LEX-749) dashboards. Server-admin basic auth, the same account the
# Instance Controller uses for per-instance dashboards.
provider "grafana" {
  url  = "https://grafana.${var.domain_name}"
  auth = "admin:${module.gcp-secrets.grafana_password}"
}
```

After the first `terragrunt init`, pin the version the lock file resolved (`version = "~> <major>.<minor>"`).

- [ ] **Step 2: Write the module's resources**

`terraform/modules/fleet-metrics-grafana/main.tf`:

```hcl
# Fleet metrics (LEX-749): an organization only the internal team belongs to, the one
# datasource that can read the controller's fleet_metrics tables, and the dashboards
# (dashboards.tf; two in R1, three from R2).

terraform {
  required_providers {
    grafana = {
      source = "grafana/grafana"
    }
  }
}

variable "db_host" {
  type        = string
  description = "host:port of the in-cluster Cloud SQL proxy that reaches the controller database."
}

variable "db_name" {
  type = string
}

variable "db_user" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "admins" {
  type    = list(string)
  default = []
}

variable "viewers" {
  type    = list(string)
  default = []
}

resource "grafana_organization" "fleet_metrics" {
  name         = "Excellence Cloud — fleet metrics"
  admin_user   = "admin"
  create_users = false
  admins       = var.admins
  viewers      = var.viewers
}

resource "grafana_data_source" "fleet_metrics" {
  org_id   = grafana_organization.fleet_metrics.org_id
  type     = "grafana-postgresql-datasource"
  name     = "Fleet metrics"
  uid      = "fleet-metrics"
  url      = var.db_host
  username = var.db_user

  json_data_encoded = jsonencode({
    database        = var.db_name
    sslmode         = "disable"
    postgresVersion = 1600
    timescaledb     = false
    maxOpenConns    = 5
  })

  secure_json_data_encoded = jsonencode({
    password = var.db_password
  })
}

resource "grafana_folder" "fleet_metrics" {
  org_id = grafana_organization.fleet_metrics.org_id
  title  = "Fleet metrics"
  uid    = "fleet-metrics"
}

# One resource per dashboard; local.dashboards maps a key to the dashboard's JSON.
resource "grafana_dashboard" "fleet_metrics" {
  for_each = local.dashboards

  org_id      = grafana_organization.fleet_metrics.org_id
  folder      = grafana_folder.fleet_metrics.uid
  config_json = each.value
  overwrite   = true

  depends_on = [grafana_data_source.fleet_metrics]
}
```

- [ ] **Step 3: Write the rules every dashboard shares**

`terraform/modules/fleet-metrics-grafana/dashboards.tf`:

```hcl
# The fleet-metrics dashboards (spec §5.6), as data. Panels live in panels_r<n>.tf, one
# file per release (spec §4.1); this file holds what they share and puts them together.
#
# Rules every query follows:
#   * a day filter never depends on the session time zone: the range comes in as epoch
#     seconds and is converted AT TIME ZONE 'UTC' (never $__timeFilter on a date column,
#     whose cast to timestamptz follows the session zone);
#   * date_trunc only on day::timestamp (without time zone), and generate_series over days
#     only with ::timestamp bounds: both are zone-free that way;
#   * rates are sums of numerators over sums of denominators, never averages of rates;
#   * a sum of per-instance people counts is "person-instance pairs", labelled so;
#   * a threshold is never a literal: it is the fleet_metrics_threshold row valid on the day
#     (threshold_at), so a new row redraws history and no measurement changes (spec §4.4);
#   * planning rules are dashed blue step lines named "planning rule (…)", or a blue cell in
#     a "planning rule" column; operational flags are orange cells in an "operational flag"
#     column, never a line. Neither is a Grafana alert: routing alerts is LEX-451.

locals {
  ds = { type = "grafana-postgresql-datasource", uid = "fleet-metrics" }

  from_day   = "(to_timestamp($__unixEpochFrom()) AT TIME ZONE 'UTC')::date"
  to_day     = "(to_timestamp($__unixEpochTo()) AT TIME ZONE 'UTC')::date"
  in_range   = "day BETWEEN ${local.from_day} AND ${local.to_day}"
  in_range_d = "d.day BETWEEN ${local.from_day} AND ${local.to_day}"
  in_range_e = "e.day BETWEEN ${local.from_day} AND ${local.to_day}"
  in_range_s = "s.day BETWEEN ${local.from_day} AND ${local.to_day}"
  latest_day = "(SELECT max(day) FROM fleet_metrics_instanceday)"
  latest     = "day = ${local.latest_day}"
  where      = "release_stage IN ($stage) AND instance_type IN ($type) AND client_name IN ($client) AND instance_name IN ($instance)"
  where_d    = "d.release_stage IN ($stage) AND d.instance_type IN ($type) AND d.client_name IN ($client) AND d.instance_name IN ($instance)"

  # The thresholds the panels draw (spec §4.4, §5.5). Each key is one (kpi, metric, kind,
  # scope); the rows behind it are append-only versions. Keys for R2–R4 are inert until
  # their panels exist.
  thresholds = {
    k1_flag   = { kpi = "K1", metric = "writers_28d", kind = "operational", scope = "instance" }
    k1_review = { kpi = "K1", metric = "writers_28d", kind = "planning", scope = "instance" }
    k3_share  = { kpi = "K3", metric = "calc_failure_share", kind = "planning", scope = "fleet" }
    k3_flag   = { kpi = "K3", metric = "calc_failed_runs", kind = "operational", scope = "instance" }
    k7_flag   = { kpi = "K7", metric = "version_releases_behind", kind = "operational", scope = "instance" }
    k7_share  = { kpi = "K7", metric = "version_behind_share", kind = "planning", scope = "fleet" }
    k5_share  = { kpi = "K5", metric = "api_unavailable_share", kind = "planning", scope = "fleet" }
    k5_flag   = { kpi = "K5", metric = "backend_unavailable_minutes", kind = "operational", scope = "instance" }
    k6_wait   = { kpi = "K6", metric = "startup_seconds", kind = "planning", scope = "fleet" }
    k6_failed = { kpi = "K6", metric = "backend_failed_starts", kind = "operational", scope = "instance" }
    k6_prep   = { kpi = "K6", metric = "preparation_seconds", kind = "operational", scope = "instance" }
    k9_p95    = { kpi = "K9", metric = "api_seconds_p95", kind = "planning", scope = "instance" }
  }

  # threshold_at[key]: a LEFT JOIN LATERAL that yields, under the alias <key>, the row valid
  # on the day written in place of @day@ — valid_from on or before it, the latest such row,
  # a later id winning a tie. With no row yet, <key>.value, .min_sample and .status are NULL.
  threshold_at = {
    for key, t in local.thresholds : key => join(" ", [
      "LEFT JOIN LATERAL (SELECT th.value, th.min_sample, th.status FROM fleet_metrics_threshold th",
      "WHERE th.kpi = '${t.kpi}' AND th.metric = '${t.metric}' AND th.kind = '${t.kind}' AND th.scope = '${t.scope}'",
      "AND th.valid_from <= @day@ ORDER BY th.valid_from DESC, th.id DESC LIMIT 1) AS ${key} ON true",
    ])
  }

  # The two series a planning rule draws, one per status, so a provisional value never
  # passes for an agreed one. Needs threshold_at[key] in the same query.
  planning_series = {
    for key in keys(local.thresholds) : key => join(", ", [
      "CASE WHEN ${key}.status = 'agreed' THEN ${key}.value END AS \"planning rule (agreed)\"",
      "CASE WHEN ${key}.status = 'provisional' THEN ${key}.value END AS \"planning rule (provisional)\"",
    ])
  }

  # Field overrides. Planning rules: dashed blue step lines, thin while provisional and
  # thick once agreed. Operational flags and planning-rule cells: coloured table cells.
  planning_style = [
    {
      matcher = { id = "byRegexp", options = "/^planning rule/" }
      properties = [
        { id = "color", value = { mode = "fixed", fixedColor = "blue" } },
        { id = "custom.drawStyle", value = "line" },
        { id = "custom.lineInterpolation", value = "stepAfter" },
        { id = "custom.lineStyle", value = { fill = "dash", dash = [10, 10] } },
        { id = "custom.fillOpacity", value = 0 },
        { id = "custom.stacking", value = { mode = "none", group = "A" } },
      ]
    },
    {
      matcher    = { id = "byName", options = "planning rule (provisional)" }
      properties = [{ id = "custom.lineWidth", value = 1 }]
    },
    {
      matcher    = { id = "byName", options = "planning rule (agreed)" }
      properties = [{ id = "custom.lineWidth", value = 3 }]
    },
  ]

  flag_cells = [
    for column, rule in {
      "operational flag" = { pattern = "^flag", colour = "orange" }
      "planning rule"    = { pattern = "^rule met", colour = "blue" }
      } : {
      matcher = { id = "byName", options = column }
      properties = [
        { id = "color", value = { mode = "fixed", fixedColor = "transparent" } },
        { id = "custom.cellOptions", value = { type = "color-background" } },
        { id = "mappings", value = [{ type = "regex", options = { pattern = rule.pattern, result = { color = rule.colour, index = 0 } } }] },
      ]
    }
  ]

  share_with_rule   = { defaults = { unit = "percentunit", min = 0 }, overrides = local.planning_style }
  seconds_with_rule = { defaults = { unit = "s", min = 0 }, overrides = local.planning_style }
  flag_table        = { defaults = {}, overrides = local.flag_cells }
  stacked_bars      = { defaults = { custom = { drawStyle = "bars", fillOpacity = 80, stacking = { mode = "normal", group = "A" } } }, overrides = [] }
  bars_by_row       = { orientation = "horizontal", displayMode = "basic", showUnfilled = true, reduceOptions = { values = true, calcs = [], fields = "" } }

  variables = [
    for v in [
      { name = "stage", label = "Release stage", default = "production", sql = "SELECT DISTINCT release_stage FROM fleet_metrics_instanceday ORDER BY 1" },
      { name = "type", label = "Type", default = "prod", sql = "SELECT DISTINCT instance_type FROM fleet_metrics_instanceday ORDER BY 1" },
      { name = "client", label = "Client", default = "$__all", sql = "SELECT DISTINCT client_name FROM fleet_metrics_instanceday ORDER BY 1" },
      { name = "instance", label = "Instance", default = "$__all", sql = "SELECT DISTINCT instance_name FROM fleet_metrics_instanceday WHERE client_name IN ($client) ORDER BY 1" },
    ] : {
      name       = v.name
      label      = v.label
      type       = "query"
      datasource = local.ds
      query      = v.sql
      definition = v.sql
      refresh    = 1
      multi      = true
      includeAll = true
      current    = { text = [v.default == "$__all" ? "All" : v.default], value = [v.default] }
    }
  ]

  # Which panels each dashboard holds, release by release (spec §4.1). Panels are only
  # ever appended, so a panel's id never changes.
  dashboard_specs = {
    product    = { uid = "fleet-product", title = "Fleet metrics: product", from = "now/y", panels = local.product_r1 }
    collection = { uid = "fleet-collection", title = "Fleet metrics: collection", from = "now-90d", panels = local.collection_r1 }
  }

  dashboards = {
    for key, spec in local.dashboard_specs : key => jsonencode({
      uid           = spec.uid
      title         = spec.title
      timezone      = "utc"
      editable      = false
      schemaVersion = 39
      time          = { from = spec.from, to = "now" }
      templating    = { list = local.variables }
      panels = [
        for index, panel in spec.panels : {
          id          = index + 1
          title       = panel.title
          description = try(panel.description, "")
          type        = panel.type
          datasource  = local.ds
          gridPos     = { x = panel.grid[0], y = panel.grid[1], w = panel.grid[2], h = panel.grid[3] }
          fieldConfig = try(panel.field_config, { defaults = {}, overrides = [] })
          options     = try(panel.options, {})
          targets = [{
            refId      = "A"
            datasource = local.ds
            editorMode = "code"
            rawQuery   = true
            format     = panel.format
            rawSql     = panel.sql
          }]
        }
      ]
    })
  }
}
```

- [ ] **Step 4: Write the R1 panels**

`terraform/modules/fleet-metrics-grafana/panels_r1.tf`:

```hcl
# Release 1 (spec §4.1, §8.1): K1 as active writers, K3 and K7 on Product, and the whole
# Collection dashboard. No Prometheus: every number comes from the controller and the
# instance databases.

locals {
  # K1's weekly readings (spec §4.4): writers_28d on the latest day and on every 7th day
  # before it. Per instance: today's writers, and how many readings in a row, counting back
  # from the latest, held no more writers than the threshold's value. Table and stat read it.
  k1_by_instance = <<-SQL
    WITH latest AS (
      SELECT max(day) AS day FROM fleet_metrics_instanceday
    ),
    now_rows AS (
      SELECT d.instance_pk, d.instance_name, d.client_name, d.instance_status,
             max(d.value) FILTER (WHERE d.metric = 'writers_1d') AS w1,
             max(d.value) FILTER (WHERE d.metric = 'writers_7d') AS w7,
             max(d.value) FILTER (WHERE d.metric = 'writers_28d') AS w28
      FROM fleet_metrics_daily d, latest
      WHERE d.day = latest.day AND ${local.where_d}
      GROUP BY 1, 2, 3, 4
    ),
    readings AS (
      SELECT d.instance_pk, d.day, d.value
      FROM fleet_metrics_daily d, latest
      WHERE d.metric = 'writers_28d' AND d.day > latest.day - 364 AND (latest.day - d.day) % 7 = 0
    )
    SELECT n.instance_name AS "instance", n.client_name AS "client", n.instance_status AS "status",
           n.w1 AS "writers 1d", n.w7 AS "writers 7d", n.w28 AS "writers 28d",
           qf.quiet AS "weekly readings without writers, in a row",
           CASE WHEN qf.quiet >= k1_flag.min_sample THEN 'flag · ' || k1_flag.status END AS "operational flag",
           CASE WHEN qr.quiet >= k1_review.min_sample THEN 'rule met · ' || k1_review.status END AS "planning rule"
    FROM now_rows n
    CROSS JOIN latest
    ${replace(local.threshold_at.k1_flag, "@day@", "latest.day")}
    ${replace(local.threshold_at.k1_review, "@day@", "latest.day")}
    LEFT JOIN LATERAL (
      SELECT count(*) AS quiet FROM readings r
      WHERE r.instance_pk = n.instance_pk
        AND r.day > coalesce((SELECT max(a.day) FROM readings a
                              WHERE a.instance_pk = n.instance_pk AND a.value > k1_flag.value), '-infinity')
    ) qf ON k1_flag.value IS NOT NULL
    LEFT JOIN LATERAL (
      SELECT count(*) AS quiet FROM readings r
      WHERE r.instance_pk = n.instance_pk
        AND r.day > coalesce((SELECT max(a.day) FROM readings a
                              WHERE a.instance_pk = n.instance_pk AND a.value > k1_review.value), '-infinity')
    ) qr ON k1_review.value IS NOT NULL
  SQL

  product_r1 = [
    {
      title        = "K1 · Active writers: people who changed data (reading is not counted) · person-instance pairs"
      description  = "Until an instance has the usage recorder (R3), K1 is this proxy: audit authors that are neither API keys nor the system. A customer who only reads is invisible here, so a low count is a reason to talk, never to pause (spec §4.2 K1)."
      type         = "timeseries"
      format       = "time_series"
      grid         = [0, 0, 16, 8]
      field_config = { defaults = { min = 0 }, overrides = [] }
      sql          = <<-SQL
        SELECT day::timestamp AS "time",
               sum(value) FILTER (WHERE metric = 'writers_28d') AS "active writers, 28 days",
               sum(value) FILTER (WHERE metric = 'writers_7d') AS "active writers, 7 days"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric IN ('writers_28d', 'writers_7d') AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title        = "K1 · Instances flagged: no active writers in the flag's weekly readings"
      description  = "Operational flag (spec §4.4): the threshold row valid on the latest day sets the readings in a row (min_sample) and the most writers a reading may hold. No row yet: nothing is flagged."
      type         = "stat"
      format       = "table"
      grid         = [16, 0, 8, 8]
      field_config = { defaults = { color = { mode = "fixed", fixedColor = "orange" } }, overrides = [] }
      options      = { colorMode = "value", graphMode = "none", reduceOptions = { values = false, calcs = ["lastNotNull"], fields = "" } }
      sql          = <<-SQL
        SELECT count(*) AS "flagged instances"
        FROM (${local.k1_by_instance}) k1
        WHERE "operational flag" IS NOT NULL
      SQL
    },
    {
      title        = "K1 · Active writers by instance (latest day), with the weekly-reading rule"
      description  = "Orange: the operational flag, listed weekly for the account owner. Blue: the planning rule for the December review list. Paused instances show their status (spec §4.2 K1)."
      type         = "table"
      format       = "table"
      grid         = [0, 8, 24, 10]
      field_config = local.flag_table
      sql          = <<-SQL
        ${local.k1_by_instance}
        ORDER BY 7 DESC NULLS LAST, 6, 1
      SQL
    },
    {
      title        = "K3 · Finished calculation runs by outcome (fleet)"
      type         = "timeseries"
      format       = "time_series"
      grid         = [0, 18, 12, 8]
      field_config = {
        defaults = local.stacked_bars.defaults
        overrides = [
          for name, colour in { success = "green", error = "red", aborted = "purple", cancelled = "#8e8e8e" } : {
            matcher    = { id = "byName", options = name }
            properties = [{ id = "color", value = { mode = "fixed", fixedColor = colour } }]
          }
        ]
      }
      sql = <<-SQL
        SELECT day::timestamp AS "time", dimension AS "metric", sum(value) AS "value"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'calc_runs' AND ${local.where}
        GROUP BY 1, 2
        ORDER BY 1
      SQL
    },
    {
      title        = "K3 · Failure share by month: error + aborted over success + error + aborted (fleet)"
      description  = "Cancelled runs are a person's choice: they count neither as failures nor in the denominator (spec §4.2 K3). The planning rule is the row valid on the month's last day in range; a month with fewer finished runs than its min_sample is not judged."
      type         = "timeseries"
      format       = "time_series"
      grid         = [12, 18, 12, 8]
      field_config = { defaults = { unit = "percentunit", min = 0, custom = { drawStyle = "bars", fillOpacity = 60 } }, overrides = local.planning_style }
      sql          = <<-SQL
        WITH monthly AS (
          SELECT date_trunc('month', day::timestamp) AS month, max(day) AS last_day,
                 sum(value) FILTER (WHERE dimension IN ('error', 'aborted')) AS failed,
                 sum(value) FILTER (WHERE dimension <> 'cancelled') AS finished
          FROM fleet_metrics_daily
          WHERE ${local.in_range} AND metric = 'calc_runs' AND ${local.where}
          GROUP BY 1
        )
        SELECT m.month AS "time",
               m.failed / nullif(m.finished, 0) AS "failure share",
               ${local.planning_series.k3_share}
        FROM monthly m
        ${replace(local.threshold_at.k3_share, "@day@", "m.last_day")}
        ORDER BY 1
      SQL
    },
    {
      title        = "K3 · Instance-days with more failed runs than the instance's own normal (operational flag)"
      description  = "Normal: the row's quantile of the instance's daily failed runs over the 90 days before. A day is judged only with at least min_sample finished runs. Engineering reads those runs' logs the next working day (spec §4.2 K3)."
      type         = "table"
      format       = "table"
      grid         = [0, 26, 12, 9]
      field_config = local.flag_table
      sql          = <<-SQL
        WITH daily AS (
          SELECT day, instance_pk, instance_name,
                 coalesce(sum(value) FILTER (WHERE dimension IN ('error', 'aborted')), 0) AS failed,
                 sum(value) AS finished
          FROM fleet_metrics_daily
          WHERE metric = 'calc_runs' AND ${local.where}
            AND day BETWEEN ${local.from_day} - 90 AND ${local.to_day}
          GROUP BY 1, 2, 3
        )
        SELECT d.day AS "day", d.instance_name AS "instance", d.finished AS "finished runs",
               d.failed AS "failed runs", round(n.normal::numeric, 1) AS "own normal",
               'flag · ' || k3_flag.status AS "operational flag"
        FROM daily d
        ${replace(local.threshold_at.k3_flag, "@day@", "d.day")}
        CROSS JOIN LATERAL (
          SELECT percentile_cont(k3_flag.value) WITHIN GROUP (ORDER BY p.failed) AS normal
          FROM daily p
          WHERE p.instance_pk = d.instance_pk AND p.day >= d.day - 90 AND p.day < d.day
        ) n
        WHERE ${local.in_range_d}
          AND k3_flag.value IS NOT NULL
          AND d.finished >= coalesce(k3_flag.min_sample, 0)
          AND d.failed > n.normal
        ORDER BY d.day DESC, d.failed DESC
      SQL
    },
    {
      title       = "K3 · Calculations by instance: runs, failure share, durations of successful runs"
      description = "Per-instance p50 and p95 cannot be combined across instances or days, so the table shows the latest day with a successful run and the highest daily p95 in range."
      type        = "table"
      format      = "table"
      grid        = [12, 26, 12, 9]
      sql         = <<-SQL
        SELECT instance_name AS "instance",
               sum(value) FILTER (WHERE metric = 'calc_runs') AS "finished runs",
               round((100 * sum(value) FILTER (WHERE metric = 'calc_runs' AND dimension IN ('error', 'aborted'))
                 / nullif(sum(value) FILTER (WHERE metric = 'calc_runs' AND dimension <> 'cancelled'), 0))::numeric, 2) AS "failure share (%)",
               (array_agg(value ORDER BY day DESC) FILTER (WHERE metric = 'calc_seconds_p50'))[1] AS "p50 (s), latest",
               (array_agg(value ORDER BY day DESC) FILTER (WHERE metric = 'calc_seconds_p95'))[1] AS "p95 (s), latest",
               max(value) FILTER (WHERE metric = 'calc_seconds_p95') AS "highest daily p95 (s)"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric IN ('calc_runs', 'calc_seconds_p50', 'calc_seconds_p95') AND ${local.where}
        GROUP BY instance_pk, instance_name
        HAVING sum(value) FILTER (WHERE metric = 'calc_runs') > 0
        ORDER BY 6 DESC NULLS LAST
      SQL
    },
    {
      title        = "K7 · Running instances further behind than the operational flag (share of those with a known version)"
      description  = "Releases behind the newest on PyPI, as the collector counts them (spec §4.2 K7). The share is computed against the flag valid on each day; the dashed line is the planning rule."
      type         = "timeseries"
      format       = "time_series"
      grid         = [0, 35, 12, 8]
      field_config = local.share_with_rule
      sql          = <<-SQL
        WITH per_day AS (
          SELECT d.day,
                 count(*) AS known,
                 count(*) FILTER (WHERE d.value > k7_flag.value) AS behind,
                 max(k7_flag.value) AS flag_value
          FROM fleet_metrics_daily d
          ${replace(local.threshold_at.k7_flag, "@day@", "d.day")}
          WHERE ${local.in_range_d} AND d.metric = 'version_releases_behind'
            AND d.instance_status = 'RUNNING' AND ${local.where_d}
          GROUP BY 1
        )
        SELECT p.day::timestamp AS "time",
               CASE WHEN p.flag_value IS NOT NULL THEN p.behind::float / nullif(p.known, 0) END AS "share behind",
               ${local.planning_series.k7_share}
        FROM per_day p
        ${replace(local.threshold_at.k7_share, "@day@", "p.day")}
        ORDER BY 1
      SQL
    },
    {
      title        = "K7 · Distinct versions and median version age (running instances)"
      type         = "timeseries"
      format       = "time_series"
      grid         = [12, 35, 8, 8]
      field_config = {
        defaults  = { min = 0 }
        overrides = [{ matcher = { id = "byName", options = "distinct versions" }, properties = [{ id = "custom.axisPlacement", value = "right" }] }]
      }
      sql = <<-SQL
        SELECT day::timestamp AS "time",
               count(DISTINCT lex_app_version) AS "distinct versions",
               percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS "median version age (days)"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'version_age_days' AND instance_status = 'RUNNING' AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title   = "K7 · Distinct versions in production (latest day)"
      type    = "stat"
      format  = "table"
      grid    = [20, 35, 4, 8]
      options = { colorMode = "none", graphMode = "none", reduceOptions = { values = false, calcs = ["lastNotNull"], fields = "" } }
      sql     = <<-SQL
        SELECT count(DISTINCT lex_app_version) AS "distinct versions"
        FROM fleet_metrics_instanceday
        WHERE ${local.latest} AND instance_status = 'RUNNING'
          AND lex_app_version NOT IN ('', 'unknown') AND ${local.where}
      SQL
    },
    {
      title  = "K7 · Versions in use (latest day, running instances)"
      type   = "table"
      format = "table"
      grid   = [0, 43, 8, 10]
      sql    = <<-SQL
        SELECT coalesce(nullif(d.lex_app_version, ''), 'unknown') AS "version",
               count(DISTINCT d.instance_pk) AS "instances",
               max(d.value) FILTER (WHERE d.metric = 'version_age_days') AS "age (days)",
               max(d.value) FILTER (WHERE d.metric = 'version_releases_behind') AS "releases behind"
        FROM fleet_metrics_daily d
        WHERE d.day = ${local.latest_day} AND d.instance_status = 'RUNNING' AND ${local.where_d}
        GROUP BY 1
        ORDER BY 4 DESC NULLS FIRST, 2 DESC
      SQL
    },
    {
      title        = "K7 · Running instances by version age (latest day): the per-cycle upgrade list"
      description  = "Orange: more releases behind than the operational flag. This list is the input to cycle upgrade planning, starting with the upgrade that carries R3 (spec §4.2 K7)."
      type         = "table"
      format       = "table"
      grid         = [8, 43, 16, 10]
      field_config = local.flag_table
      sql          = <<-SQL
        SELECT d.instance_name AS "instance", d.client_name AS "client",
               coalesce(nullif(d.lex_app_version, ''), 'unknown') AS "version",
               d.version_source AS "version read from",
               v.age AS "age (days)", v.behind AS "releases behind",
               CASE WHEN v.behind > k7_flag.value THEN 'flag · ' || k7_flag.status END AS "operational flag"
        FROM fleet_metrics_instanceday d
        CROSS JOIN LATERAL (
          SELECT max(m.value) FILTER (WHERE m.metric = 'version_age_days') AS age,
                 max(m.value) FILTER (WHERE m.metric = 'version_releases_behind') AS behind
          FROM fleet_metrics_metricvalue m
          WHERE m.instance_day_id = d.id
        ) v
        ${replace(local.threshold_at.k7_flag, "@day@", "d.day")}
        WHERE d.day = ${local.latest_day} AND d.instance_status = 'RUNNING' AND ${local.where_d}
        ORDER BY v.behind DESC NULLS FIRST, d.instance_name
      SQL
    },
  ]

  collection_r1 = [
    {
      title       = "Coverage per source (latest day)"
      description = "A source not delivered yet is recorded as skipped and shown here as not delivered, never as failed (spec §5.4.2). Prometheus arrives in R2. For the version, unknown means no source gave one."
      type        = "table"
      format      = "table"
      grid        = [0, 0, 12, 8]
      sql         = <<-SQL
        SELECT s.release AS "delivered in", s.source AS "source",
               count(*) FILTER (WHERE s.state = 'ok') AS "ok",
               count(*) FILTER (WHERE s.state = 'error') AS "failed",
               count(*) FILTER (WHERE s.state = 'excluded') AS "excluded",
               count(*) FILTER (WHERE s.state = 'skipped') AS "not delivered or not applicable",
               count(*) FILTER (WHERE s.state = 'unknown') AS "unknown"
        FROM fleet_metrics_instanceday d
        CROSS JOIN LATERAL (VALUES
          ('R1', 'controller', d.controller_status),
          ('R1', 'instance database', d.db_status),
          ('R1', 'version', CASE WHEN d.version_source = 'unknown' THEN 'unknown' ELSE 'ok' END),
          ('R2', 'Prometheus', d.prometheus_status)
        ) AS s(release, source, state)
        WHERE d.day = ${local.latest_day} AND ${local.where_d}
        GROUP BY 1, 2
        ORDER BY 1, 2
      SQL
    },
    {
      title       = "Coverage by lex-app version (latest day)"
      description = "K7's version is the coverage dimension of every later release (spec §4.1). From R3, instances on the release with the recorder show it enabled or disabled; older versions show it absent."
      type        = "table"
      format      = "table"
      grid        = [12, 0, 12, 8]
      sql         = <<-SQL
        SELECT coalesce(nullif(lex_app_version, ''), 'unknown') AS "version",
               count(*) AS "instances",
               count(*) FILTER (WHERE db_status = 'ok') AS "instance database ok",
               count(*) FILTER (WHERE db_status = 'error') AS "instance database failed",
               count(*) FILTER (WHERE recorder = 'enabled') AS "recorder enabled",
               count(*) FILTER (WHERE recorder = 'disabled') AS "recorder disabled",
               count(*) FILTER (WHERE recorder IN ('absent', 'partial')) AS "recorder absent or partial"
        FROM fleet_metrics_instanceday
        WHERE ${local.latest} AND ${local.where}
        GROUP BY 1
        ORDER BY 2 DESC, 1
      SQL
    },
    {
      title  = "Rows per day, and what failed"
      type   = "timeseries"
      format = "time_series"
      grid   = [0, 8, 24, 8]
      sql    = <<-SQL
        SELECT day::timestamp AS "time",
               count(*) AS "rows",
               count(*) FILTER (WHERE controller_status = 'error') AS "controller failed",
               count(*) FILTER (WHERE db_status = 'error') AS "instance database failed",
               count(*) FILTER (WHERE prometheus_status = 'error') AS "Prometheus failed",
               count(*) FILTER (WHERE version_source = 'unknown') AS "version unknown"
        FROM fleet_metrics_instanceday
        WHERE ${local.in_range} AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title  = "Failed sources, last 7 days"
      type   = "table"
      format = "table"
      grid   = [0, 16, 24, 8]
      sql    = <<-SQL
        SELECT day, instance_name AS "instance", controller_status AS "controller",
               db_status AS "instance database", prometheus_status AS "Prometheus", errors::text AS "errors"
        FROM fleet_metrics_instanceday
        WHERE day > ${local.latest_day} - 7
          AND (controller_status = 'error' OR db_status = 'error' OR prometheus_status = 'error')
          AND ${local.where}
        ORDER BY day DESC, instance_name
      SQL
    },
    {
      title       = "Invariant: writers 1 day ≤ 7 days ≤ 28 days (should be empty)"
      description = "Spec §7."
      type        = "table"
      format      = "table"
      grid        = [0, 24, 12, 8]
      sql         = <<-SQL
        SELECT day, instance_name AS "instance", w1 AS "writers 1d", w7 AS "writers 7d", w28 AS "writers 28d"
        FROM (
          SELECT day, instance_pk, instance_name,
                 max(value) FILTER (WHERE metric = 'writers_1d') AS w1,
                 max(value) FILTER (WHERE metric = 'writers_7d') AS w7,
                 max(value) FILTER (WHERE metric = 'writers_28d') AS w28
          FROM fleet_metrics_daily
          WHERE ${local.in_range} AND metric IN ('writers_1d', 'writers_7d', 'writers_28d') AND ${local.where}
          GROUP BY 1, 2, 3
        ) per_day
        WHERE w1 > w7 OR w7 > w28
        ORDER BY day DESC, instance_name
      SQL
    },
    {
      title       = "Invariant: one row per instance per day (missing days; should be empty)"
      description = "Gaps between an instance's first and last row (spec §7). An instance whose rows stopped shows in 'Last collected day per instance'."
      type        = "table"
      format      = "table"
      grid        = [12, 24, 12, 8]
      sql         = <<-SQL
        WITH instances AS (
          SELECT instance_pk, min(day) AS first_day, max(day) AS last_day,
                 (array_agg(instance_name ORDER BY day DESC))[1] AS instance_name
          FROM fleet_metrics_instanceday
          WHERE ${local.where}
          GROUP BY 1
        )
        SELECT g.day::date AS "missing day", i.instance_name AS "instance"
        FROM instances i
        CROSS JOIN LATERAL generate_series(
          greatest(i.first_day, ${local.from_day})::timestamp,
          least(i.last_day, ${local.to_day})::timestamp,
          interval '1 day'
        ) AS g(day)
        WHERE NOT EXISTS (
          SELECT 1 FROM fleet_metrics_instanceday x
          WHERE x.instance_pk = i.instance_pk AND x.day = g.day::date
        )
        ORDER BY 1 DESC, 2
      SQL
    },
    {
      title  = "Last collected day per instance"
      type   = "table"
      format = "table"
      grid   = [0, 32, 8, 9]
      sql    = <<-SQL
        SELECT instance_name AS "instance", max(day) AS "last day",
               ${local.latest_day} - max(day) AS "days behind the latest",
               max(collected_at) AS "collected at (controller time, Berlin)"
        FROM fleet_metrics_instanceday
        WHERE ${local.where}
        GROUP BY 1
        ORDER BY 3 DESC, 1
      SQL
    },
    {
      title       = "Runs"
      description = "Times are the controller's naive Berlin time; days are UTC."
      type        = "table"
      format      = "table"
      grid        = [8, 32, 16, 9]
      sql         = <<-SQL
        SELECT started_at AS "started (Berlin)", finished_at AS "finished (Berlin)", trigger, status,
               first_day AS "first day", last_day AS "last day", instances_expected AS "expected",
               instance_days_written AS "instance-days written", instance_days_failed AS "instance-days failed",
               heartbeat, summary::text AS "summary"
        FROM fleet_metrics_collectionrun
        ORDER BY started_at DESC
        LIMIT 60
      SQL
    },
    {
      title       = "Threshold log"
      description = "Every version of every threshold, newest first per rule (spec §4.4). Rows are never edited or deleted. Blue: planning rules; orange: operational flags."
      type        = "table"
      format      = "table"
      grid        = [0, 41, 24, 10]
      field_config = {
        defaults = {}
        overrides = [{
          matcher = { id = "byName", options = "kind" }
          properties = [
            { id = "custom.cellOptions", value = { type = "color-background" } },
            { id = "mappings", value = [{ type = "value", options = { planning = { color = "blue", index = 0 }, operational = { color = "orange", index = 1 } } }] },
          ]
        }]
      }
      sql = <<-SQL
        SELECT t.kpi AS "KPI", t.metric, t.kind, t.scope, t.comparator, t.value,
               t.min_sample AS "minimum sample", t.valid_from AS "valid from", t.status,
               CASE
                 WHEN t.valid_from > (now() AT TIME ZONE 'UTC')::date THEN 'scheduled'
                 WHEN t.id = (SELECT c.id FROM fleet_metrics_threshold c
                              WHERE c.kpi = t.kpi AND c.metric = t.metric AND c.kind = t.kind AND c.scope = t.scope
                                AND c.valid_from <= (now() AT TIME ZONE 'UTC')::date
                              ORDER BY c.valid_from DESC, c.id DESC LIMIT 1) THEN 'in force'
                 ELSE 'superseded'
               END AS "today",
               t.reason, t.linear_issue AS "Linear issue"
        FROM fleet_metrics_threshold t
        ORDER BY t.kpi, t.metric, t.kind, t.scope, t.valid_from DESC, t.id DESC
      SQL
    },
  ]
}
```

- [ ] **Step 5: Call the module from the layer**

In `terraform/layers/cluster-services/main.tf`, after `module "fleet_metrics_reader"`:

```hcl
module "fleet_metrics_grafana" {
  count  = var.fleet_metrics_enabled ? 1 : 0
  source = "../../modules/fleet-metrics-grafana"

  db_host     = "${module.cloud-sql-proxy.service}:5432"
  db_name     = var.db_name
  db_user     = module.fleet_metrics_reader[0].user
  db_password = module.fleet_metrics_reader[0].password
  admins      = var.fleet_metrics_grafana_admins
  viewers     = var.fleet_metrics_grafana_viewers
}
```

- [ ] **Step 6: Turn it on in prod and test**

In `terraform/environments/prod/cluster-services/terragrunt.hcl` and `terraform/environments/devops/cluster-services/terragrunt.hcl`, inside `inputs` (both already set `db_name` to the controller database):

```hcl
  # LEX-749: fleet metrics in Grafana, in its own organization. Every login listed
  # must have signed in to Grafana (through Keycloak) once before the apply.
  fleet_metrics_enabled         = true
  fleet_metrics_grafana_admins  = []
  fleet_metrics_grafana_viewers = []
```

Before each apply, ask the user for the two lists: the Grafana logins (their Keycloak e-mail addresses) of the LEX-749 assignee as admin, and of the colleagues who may view. Enter them in this private repository only; they never go into the public lex-app repository.

- [ ] **Step 7: Validate the module and the layer, and render the dashboards**

Run: `cd terraform/modules/fleet-metrics-grafana && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform fmt -check`
Expected: valid; nothing to format. If `fmt` lists a file, run `terraform fmt`, read the diff, and check again.

Run: `cd terraform/layers/cluster-services && terraform init -backend=false -input=false >/dev/null && terraform validate`
Expected: valid.

Render, from `terraform/modules/fleet-metrics-grafana`, with `V='-var db_host=h:5432 -var db_name=d -var db_user=u -var db_password=p'`:

Run: `echo 'keys(local.dashboards)' | terraform console $V`
Expected: `["collection", "product"]`.

Run: `echo 'length(regexall("@day@", join("", values(local.dashboards))))' | terraform console $V`
Expected: `0` — every threshold join got its day.

Run: `echo '[for p in jsondecode(local.dashboards.product).panels : p.targets[0].rawSql if startswith(p.title, "K3 · Failure share")][0]' | terraform console $V`
Expected: SQL with `LEFT JOIN LATERAL (SELECT th.value, th.min_sample, th.status FROM fleet_metrics_threshold th WHERE th.kpi = 'K3' AND th.metric = 'calc_failure_share' … AND th.valid_from <= m.last_day …) AS k3_share ON true`, the two `planning rule` columns, and `$__unixEpochFrom()`, `$stage` left literal.

The queries meet a real schema for the first time in Task 9, Step 1, where every panel must render without a query error in the test Grafana before anything reaches production.

- [ ] **Step 8: Commit**

```bash
git add terraform/modules/fleet-metrics-grafana terraform/layers/cluster-services terraform/environments/prod/cluster-services/terragrunt.hcl terraform/environments/devops/cluster-services/terragrunt.hcl
git commit -m "feat(grafana): fleet-metrics product and collection dashboards in an organization only the team can open"
```

---

### Task 6: The Platform dashboard (R2)

On branch `feat/fleet-metrics-platform`, cut from `origin/develop` after R1 merged.

**Files:**
- Create: `terraform/modules/fleet-metrics-grafana/panels_r2.tf`
- Modify: `terraform/modules/fleet-metrics-grafana/dashboards.tf`

**Interfaces:**
- Consumes (collector plan, R2): metrics `api_requests` (dimension `2xx`, `3xx`, `4xx`, `5xx_unavailable`, `5xx_app`), `backend_unavailable_minutes`, `backend_starts` (dimension: cause), `backend_failed_starts`, `backend_container_restarts`, `api_idle_hours`; `prometheus_status` filled; and `fleet_metrics_startevent`: `id`, `instance_pk`, `day`, `pod`, `cause` (`deploy`, `resume`, `restart`, `rollout`, `scale_from_zero`, `reschedule`), `outcome` (`ready`, `failed`), `startup_seconds` (created → ready; NULL when failed), `scheduling_seconds`, `preparation_seconds`, `container_start_seconds`, `application_ready_seconds`, `failure_reason`.
- Consumes (K8's owners, spec §4.2 K8, §8.4): `fleet_metrics_deployevent` with `instance_pk`, `day`, `outcome`, `duration_seconds`. The panel counts `outcome = 'FAILURE'` as a failure, as `InstanceOperation` does today; if the K8.1 contract names outcomes differently, change that one literal.
- Consumes: threshold keys `k5_share`, `k5_flag`, `k6_wait`, `k6_failed`, `k6_prep` (Task 5's table).
- Produces: dashboard `fleet-platform`; the start-event invariant on Collection.

- [ ] **Step 1: Write the R2 panels**

`terraform/modules/fleet-metrics-grafana/panels_r2.tf`:

```hcl
# Release 2 (spec §4.1, §8.1): the Platform dashboard — K5 request success, K6 starts and
# startup time, and K8's panel, which reads what its owners deliver into DeployEvent.
# Also the start-event invariant on Collection (spec §7).

locals {
  platform_r2 = [
    {
      title        = "K5 · API requests answered 5xx (share of all API requests, fleet)"
      description  = "Unavailable: 502, 503, 504, the backend down or starting. Application errors: every other 5xx. WebSocket upgrades are excluded (spec §4.2 K5)."
      type         = "timeseries"
      format       = "time_series"
      grid         = [0, 0, 12, 8]
      field_config = local.share_with_rule
      sql          = <<-SQL
        WITH per_day AS (
          SELECT day,
                 sum(value) FILTER (WHERE dimension = '5xx_unavailable') AS unavailable,
                 sum(value) FILTER (WHERE dimension = '5xx_app') AS app_errors,
                 sum(value) AS requests
          FROM fleet_metrics_daily
          WHERE ${local.in_range} AND metric = 'api_requests' AND ${local.where}
          GROUP BY 1
        )
        SELECT p.day::timestamp AS "time",
               p.unavailable / nullif(p.requests, 0) AS "unavailable (502, 503, 504)",
               p.app_errors / nullif(p.requests, 0) AS "application errors (other 5xx)",
               ${local.planning_series.k5_share}
        FROM per_day p
        ${replace(local.threshold_at.k5_share, "@day@", "p.day")}
        ORDER BY 1
      SQL
    },
    {
      title        = "K5 · API requests by class (fleet)"
      type         = "timeseries"
      format       = "time_series"
      grid         = [12, 0, 12, 8]
      field_config = local.stacked_bars
      sql          = <<-SQL
        SELECT day::timestamp AS "time", dimension AS "metric", sum(value) AS "value"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'api_requests' AND ${local.where}
        GROUP BY 1, 2
        ORDER BY 1
      SQL
    },
    {
      title        = "K5 · Backend unavailable while it should run (instance-minutes, fleet)"
      description  = "Minutes with spec replicas ≥ 1 and none available (backend_unavailable_minutes)."
      type         = "timeseries"
      format       = "time_series"
      grid         = [0, 8, 12, 8]
      field_config = { defaults = { min = 0 }, overrides = [] }
      sql          = <<-SQL
        SELECT day::timestamp AS "time", sum(value) AS "unavailable instance-minutes"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'backend_unavailable_minutes' AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title  = "K5 · By instance over the range"
      type   = "table"
      format = "table"
      grid   = [12, 8, 12, 8]
      sql    = <<-SQL
        SELECT instance_name AS "instance",
               sum(value) FILTER (WHERE metric = 'api_requests') AS "API requests",
               round((100 * sum(value) FILTER (WHERE metric = 'api_requests' AND dimension = '5xx_unavailable')
                 / nullif(sum(value) FILTER (WHERE metric = 'api_requests'), 0))::numeric, 2) AS "unavailable (%)",
               round((100 * sum(value) FILTER (WHERE metric = 'api_requests' AND dimension = '5xx_app')
                 / nullif(sum(value) FILTER (WHERE metric = 'api_requests'), 0))::numeric, 2) AS "application errors (%)",
               sum(value) FILTER (WHERE metric = 'backend_unavailable_minutes') AS "unavailable minutes"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric IN ('api_requests', 'backend_unavailable_minutes') AND ${local.where}
        GROUP BY instance_pk, instance_name
        ORDER BY 3 DESC NULLS LAST, 5 DESC NULLS LAST
      SQL
    },
    {
      title        = "K5 · Instance-days unavailable for longer than the instance's own normal (operational flag)"
      description  = "Normal: the row's quantile of the instance's daily unavailable minutes over the 90 days before; judged only with at least min_sample API requests that day. Infra checks a flagged day that week (spec §4.2 K5)."
      type         = "table"
      format       = "table"
      grid         = [0, 16, 24, 8]
      field_config = local.flag_table
      sql          = <<-SQL
        WITH daily AS (
          SELECT day, instance_pk, instance_name,
                 max(value) FILTER (WHERE metric = 'backend_unavailable_minutes') AS minutes,
                 sum(value) FILTER (WHERE metric = 'api_requests') AS requests
          FROM fleet_metrics_daily
          WHERE metric IN ('backend_unavailable_minutes', 'api_requests') AND ${local.where}
            AND day BETWEEN ${local.from_day} - 90 AND ${local.to_day}
          GROUP BY 1, 2, 3
        )
        SELECT d.day AS "day", d.instance_name AS "instance", d.minutes AS "unavailable minutes",
               round(n.normal::numeric, 1) AS "own normal (minutes)", d.requests AS "API requests",
               'flag · ' || k5_flag.status AS "operational flag"
        FROM daily d
        ${replace(local.threshold_at.k5_flag, "@day@", "d.day")}
        CROSS JOIN LATERAL (
          SELECT percentile_cont(k5_flag.value) WITHIN GROUP (ORDER BY p.minutes) AS normal
          FROM daily p
          WHERE p.instance_pk = d.instance_pk AND p.day >= d.day - 90 AND p.day < d.day AND p.minutes IS NOT NULL
        ) n
        WHERE ${local.in_range_d}
          AND k5_flag.value IS NOT NULL
          AND coalesce(d.requests, 0) >= coalesce(k5_flag.min_sample, 0)
          AND d.minutes > n.normal
        ORDER BY d.day DESC, d.minutes DESC
      SQL
    },
    {
      title        = "K6 · Backend starts by cause (fleet)"
      description  = "Cause, in order: the controller operation running at pod creation (deploy, resume, restart), else rollout, else scale_from_zero, else reschedule (spec §4.2 K6)."
      type         = "timeseries"
      format       = "time_series"
      grid         = [0, 24, 12, 8]
      field_config = local.stacked_bars
      sql          = <<-SQL
        SELECT day::timestamp AS "time", dimension AS "metric", sum(value) AS "value"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'backend_starts' AND ${local.where}
        GROUP BY 1, 2
        ORDER BY 1
      SQL
    },
    {
      title        = "K6 · Failed starts, and container restarts (which are not starts)"
      type         = "timeseries"
      format       = "time_series"
      grid         = [12, 24, 12, 8]
      field_config = { defaults = { min = 0, custom = { drawStyle = "bars", fillOpacity = 60 } }, overrides = [] }
      sql          = <<-SQL
        SELECT day::timestamp AS "time",
               sum(value) FILTER (WHERE metric = 'backend_failed_starts') AS "failed starts",
               sum(value) FILTER (WHERE metric = 'backend_container_restarts') AS "container restarts"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric IN ('backend_failed_starts', 'backend_container_restarts') AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title        = "K6 · Startup time, created → ready (successful starts, fleet): p50 and p90"
      description  = "Percentiles over start events, so they combine across instances. The dashed line is the cold-start wait accepted at the R2 review; the 600 s startup budget is a technical limit, not a target (spec §4.4)."
      type         = "timeseries"
      format       = "time_series"
      grid         = [0, 32, 12, 8]
      field_config = local.seconds_with_rule
      sql          = <<-SQL
        WITH per_day AS (
          SELECT s.day,
                 percentile_cont(0.5) WITHIN GROUP (ORDER BY s.startup_seconds) AS p50,
                 percentile_cont(0.9) WITHIN GROUP (ORDER BY s.startup_seconds) AS p90
          FROM fleet_metrics_startevent s
          JOIN fleet_metrics_instanceday d ON d.instance_pk = s.instance_pk AND d.day = s.day
          WHERE ${local.in_range_s} AND s.outcome = 'ready' AND ${local.where_d}
          GROUP BY 1
        )
        SELECT p.day::timestamp AS "time", p.p50 AS "p50", p.p90 AS "p90",
               ${local.planning_series.k6_wait}
        FROM per_day p
        ${replace(local.threshold_at.k6_wait, "@day@", "p.day")}
        ORDER BY 1
      SQL
    },
    {
      title       = "K6 · Startup phases over the range (successful starts)"
      description = "The four phases of spec §4.2 K6; preparation covers the init containers that inject secrets and run migrations."
      type        = "table"
      format      = "table"
      grid        = [12, 32, 12, 8]
      sql         = <<-SQL
        SELECT ph.phase AS "phase",
               percentile_cont(0.5) WITHIN GROUP (ORDER BY ph.seconds) AS "p50 (s)",
               percentile_cont(0.9) WITHIN GROUP (ORDER BY ph.seconds) AS "p90 (s)",
               max(ph.seconds) AS "max (s)",
               count(ph.seconds) AS "starts"
        FROM fleet_metrics_startevent s
        JOIN fleet_metrics_instanceday d ON d.instance_pk = s.instance_pk AND d.day = s.day
        CROSS JOIN LATERAL (VALUES
          (1, 'total, created → ready', s.startup_seconds),
          (2, 'scheduling', s.scheduling_seconds),
          (3, 'preparation (secrets, migrations)', s.preparation_seconds),
          (4, 'container start', s.container_start_seconds),
          (5, 'application ready', s.application_ready_seconds)
        ) AS ph(position, phase, seconds)
        WHERE ${local.in_range_s} AND s.outcome = 'ready' AND ${local.where_d}
        GROUP BY ph.position, ph.phase
        ORDER BY ph.position
      SQL
    },
    {
      title        = "K6 · Failed starts by instance-day (operational flag)"
      description  = "A start is failed when its pod was deleted before ready or was not ready within the 600-second budget; the init-container reason is kept (spec §4.2 K6). Listed the next morning."
      type         = "table"
      format       = "table"
      grid         = [0, 40, 12, 8]
      field_config = local.flag_table
      sql          = <<-SQL
        SELECT s.day AS "day", d.instance_name AS "instance", count(*) AS "failed starts",
               string_agg(DISTINCT s.cause, ', ') AS "causes",
               string_agg(DISTINCT coalesce(s.failure_reason, 'no init-container reason'), ', ') AS "reasons",
               CASE WHEN count(*) > max(k6_failed.value) THEN 'flag · ' || max(k6_failed.status) END AS "operational flag"
        FROM fleet_metrics_startevent s
        JOIN fleet_metrics_instanceday d ON d.instance_pk = s.instance_pk AND d.day = s.day
        ${replace(local.threshold_at.k6_failed, "@day@", "s.day")}
        WHERE ${local.in_range_s} AND s.outcome = 'failed' AND ${local.where_d}
        GROUP BY s.day, d.instance_pk, d.instance_name
        ORDER BY s.day DESC, 3 DESC
      SQL
    },
    {
      title        = "K6 · Starts whose preparation took longer than the instance's own normal (operational flag)"
      description  = "Normal: the row's quantile of the instance's preparation seconds over the 90 days before. Far above normal points at a migration (spec §4.2 K6)."
      type         = "table"
      format       = "table"
      grid         = [12, 40, 12, 8]
      field_config = local.flag_table
      sql          = <<-SQL
        WITH starts AS (
          SELECT s.day, s.instance_pk, d.instance_name, s.cause, s.preparation_seconds
          FROM fleet_metrics_startevent s
          JOIN fleet_metrics_instanceday d ON d.instance_pk = s.instance_pk AND d.day = s.day
          WHERE s.day BETWEEN ${local.from_day} - 90 AND ${local.to_day}
            AND s.preparation_seconds IS NOT NULL AND ${local.where_d}
        )
        SELECT x.day AS "day", x.instance_name AS "instance", x.cause AS "cause",
               x.preparation_seconds AS "preparation (s)", round(n.normal::numeric, 1) AS "own normal (s)",
               'flag · ' || k6_prep.status AS "operational flag"
        FROM starts x
        ${replace(local.threshold_at.k6_prep, "@day@", "x.day")}
        CROSS JOIN LATERAL (
          SELECT percentile_cont(k6_prep.value) WITHIN GROUP (ORDER BY p.preparation_seconds) AS normal
          FROM starts p
          WHERE p.instance_pk = x.instance_pk AND p.day >= x.day - 90 AND p.day < x.day
        ) n
        WHERE x.day BETWEEN ${local.from_day} AND ${local.to_day}
          AND k6_prep.value IS NOT NULL
          AND x.preparation_seconds > n.normal
        ORDER BY x.day DESC, x.preparation_seconds DESC
      SQL
    },
    {
      title        = "K6 · Hours without a single API request (share of running instance-hours)"
      description  = "The time a scale-to-zero backend could have been off (api_idle_hours, spec §4.2 K6)."
      type         = "timeseries"
      format       = "time_series"
      grid         = [0, 48, 12, 8]
      field_config = { defaults = { unit = "percentunit", min = 0, max = 1 }, overrides = [] }
      sql          = <<-SQL
        SELECT day::timestamp AS "time",
               sum(value) / nullif(24 * count(*), 0) AS "idle, all running instances",
               sum(value) FILTER (WHERE autoscaling) / nullif(24 * count(*) FILTER (WHERE autoscaling), 0) AS "idle, autoscaled instances"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'api_idle_hours' AND instance_status = 'RUNNING' AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title  = "K6 · Idle hours and starts by running instance, over the range"
      type   = "table"
      format = "table"
      grid   = [12, 48, 12, 8]
      sql    = <<-SQL
        SELECT d.instance_name AS "instance", bool_or(d.autoscaling) AS "autoscaling",
               round((sum(d.value) FILTER (WHERE d.metric = 'api_idle_hours')
                 / nullif(count(*) FILTER (WHERE d.metric = 'api_idle_hours'), 0))::numeric, 1) AS "idle hours per day",
               sum(d.value) FILTER (WHERE d.metric = 'backend_starts') AS "starts",
               sum(d.value) FILTER (WHERE d.metric = 'backend_failed_starts') AS "failed starts"
        FROM fleet_metrics_daily d
        WHERE ${local.in_range_d} AND d.instance_status = 'RUNNING'
          AND d.metric IN ('api_idle_hours', 'backend_starts', 'backend_failed_starts') AND ${local.where_d}
        GROUP BY d.instance_pk, d.instance_name
        ORDER BY 3 DESC NULLS LAST
      SQL
    },
    {
      title       = "K8 · Deploy reliability, owned by Infra and the Instance Controller"
      description = "Measured by K8's owners, who fill DeployEvent (spec §4.2 K8, §8.4). Shows 'not yet delivered' while that table is empty. K8's thresholds are the owners' rows in the threshold log."
      type        = "table"
      format      = "table"
      grid        = [0, 56, 24, 7]
      sql         = <<-SQL
        WITH weeks AS (
          SELECT date_trunc('week', e.day::timestamp)::date AS week,
                 count(*) AS deploys,
                 round((100.0 * count(*) FILTER (WHERE e.outcome = 'FAILURE') / count(*))::numeric, 2) AS failure_share,
                 percentile_cont(0.95) WITHIN GROUP (ORDER BY e.duration_seconds) AS p95
          FROM fleet_metrics_deployevent e
          JOIN fleet_metrics_instanceday d ON d.instance_pk = e.instance_pk AND d.day = e.day
          WHERE ${local.in_range_e} AND ${local.where_d}
          GROUP BY 1
        )
        SELECT week::text AS "week starting", deploys AS "deploys",
               failure_share AS "failure share (%)", p95 AS "p95 duration (s)"
        FROM weeks
        UNION ALL
        SELECT 'not yet delivered', NULL, NULL, NULL
        WHERE NOT EXISTS (SELECT 1 FROM fleet_metrics_deployevent)
        ORDER BY 1 DESC
      SQL
    },
  ]

  collection_r2 = [
    {
      title       = "Invariant: every start that did not fail has ready ≥ created (should be empty)"
      description = "Spec §7."
      type        = "table"
      format      = "table"
      grid        = [0, 51, 24, 7]
      sql         = <<-SQL
        SELECT s.day AS "day", d.instance_name AS "instance", s.pod AS "pod", s.cause AS "cause",
               s.outcome AS "outcome", s.startup_seconds AS "created → ready (s)"
        FROM fleet_metrics_startevent s
        JOIN fleet_metrics_instanceday d ON d.instance_pk = s.instance_pk AND d.day = s.day
        WHERE ${local.in_range_s} AND s.outcome <> 'failed'
          AND (s.startup_seconds IS NULL OR s.startup_seconds < 0) AND ${local.where_d}
        ORDER BY s.day DESC, d.instance_name
      SQL
    },
  ]
}
```

- [ ] **Step 2: Add the Platform dashboard**

In `terraform/modules/fleet-metrics-grafana/dashboards.tf`, replace `dashboard_specs` with:

```hcl
  dashboard_specs = {
    product    = { uid = "fleet-product", title = "Fleet metrics: product", from = "now/y", panels = local.product_r1 }
    platform   = { uid = "fleet-platform", title = "Fleet metrics: platform", from = "now-90d", panels = local.platform_r2 }
    collection = { uid = "fleet-collection", title = "Fleet metrics: collection", from = "now-90d", panels = concat(local.collection_r1, local.collection_r2) }
  }
```

- [ ] **Step 3: Validate and render**

Run: `cd terraform/modules/fleet-metrics-grafana && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform fmt -check`
Expected: valid; nothing to format.

With `V` as in Task 5, Step 7:

Run: `echo 'keys(local.dashboards)' | terraform console $V`
Expected: `["collection", "platform", "product"]`.

Run: `echo 'length(regexall("@day@", join("", values(local.dashboards))))' | terraform console $V`
Expected: `0`.

Run: `echo '[for p in jsondecode(local.dashboards.platform).panels : p.targets[0].rawSql if startswith(p.title, "K8")][0]' | terraform console $V`
Expected: the weekly query, then `UNION ALL SELECT 'not yet delivered', NULL, NULL, NULL WHERE NOT EXISTS (SELECT 1 FROM fleet_metrics_deployevent)`.

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/fleet-metrics-grafana
git commit -m "feat(grafana): a platform dashboard for request success, starts and startup time"
```

---

### Task 7: K2 and exact K1 on Product (R3)

On branch `feat/fleet-metrics-k2`, cut from `origin/develop` after R2 merged.

**Files:**
- Create: `terraform/modules/fleet-metrics-grafana/panels_r3.tf`
- Modify: `terraform/modules/fleet-metrics-grafana/dashboards.tf`

**Interfaces:**
- Consumes (collector plan, R3): `area_people_28d` with `dimension = '<area>:<channel>'` (`channel` one of `api`, `ui`, `ui_embed`, and `ui_all` = distinct people over `ui` and `ui_embed` together, computed by the collector; spec §5.3.2); `active_people_1d`, `active_people_7d`, `active_people_28d`, `editors_1d`; `fleet_metrics_instanceday.recorder` filled, `recorder_silent` and `ui_silent` (booleans).
- Produces: Product gains K2 by area and channel and exact K1; Collection gains the silence marks and the people invariants.

K2's figure is the `ui_all` channel: distinct people over `ui` and `ui_embed` together, so a person who used an area both directly and embedded counts once. `ui` and `ui_embed` are shown only as a breakdown and never summed; `api` is supporting detail, labelled "API activity" (spec §5.3.2). K2 is judged only on instances with the recorder enabled.

- [ ] **Step 1: Write the R3 panels**

`terraform/modules/fleet-metrics-grafana/panels_r3.tf`:

```hcl
# Release 3 (spec §4.1, §8.1): K2 feature reach by area and channel, exact K1 from the
# usage recorder, and the recorder's silence marks and invariants on Collection (spec §7).

locals {
  # The product areas of spec §5.3.2, in its order, plus the API-only `app` (§5.3.1),
  # listed so an area nobody used shows as 0 instead of disappearing.
  k2_areas = "VALUES ('grid'), ('record'), ('create'), ('history'), ('audit_log'), ('calculate'), ('calculation_logs'), ('processes'), ('reports'), ('analytics'), ('dashboard'), ('search'), ('export'), ('files'), ('monitoring'), ('app')"

  product_r3 = [
    {
      title   = "K2 · Instances with the recorder enabled (latest day)"
      type    = "stat"
      format  = "table"
      grid    = [0, 53, 4, 10]
      options = { colorMode = "none", graphMode = "none", reduceOptions = { values = false, calcs = ["lastNotNull"], fields = "" } }
      sql     = <<-SQL
        SELECT count(*) AS "instances"
        FROM fleet_metrics_instanceday
        WHERE ${local.latest} AND recorder = 'enabled' AND ${local.where}
      SQL
    },
    {
      title       = "K2 · Feature reach in the interface: instances where each area was used in 28 days (ui_all, latest day)"
      description = "Interface events only; API activity is in the table below and never added here (spec §5.3.2). Data starts with the R3 upgrade: in December it can rule areas out, not rank close ones (spec §4.2 K2)."
      type        = "bargauge"
      format      = "table"
      grid        = [4, 53, 10, 10]
      options     = local.bars_by_row
      sql         = <<-SQL
        WITH areas(area) AS (${local.k2_areas}),
        used AS (
          SELECT split_part(dimension, ':', 1) AS area, count(DISTINCT instance_pk) AS instances
          FROM fleet_metrics_daily
          WHERE ${local.latest} AND metric = 'area_people_28d' AND value > 0
            AND split_part(dimension, ':', 2) = 'ui_all'
            AND recorder = 'enabled' AND ${local.where}
          GROUP BY 1
        )
        SELECT a.area AS "area", coalesce(u.instances, 0) AS "instances"
        FROM areas a
        LEFT JOIN used u USING (area)
        WHERE a.area <> 'app'
        ORDER BY 2 DESC, 1
      SQL
    },
    {
      title        = "K1 · Active people (recorder) and active writers, 28 days, on instances with the recorder · person-instance pairs"
      description  = "Exact K1 counts everyone the recorder saw, readers included; writers are the R1 proxy on the same instances, so the gap between the lines is people who only read."
      type         = "timeseries"
      format       = "time_series"
      grid         = [14, 53, 10, 10]
      field_config = {
        defaults  = { min = 0 }
        overrides = [{ matcher = { id = "byName", options = "instances with the recorder" }, properties = [{ id = "custom.axisPlacement", value = "right" }] }]
      }
      sql = <<-SQL
        SELECT day::timestamp AS "time",
               sum(value) FILTER (WHERE metric = 'active_people_28d') AS "active people (recorder)",
               sum(value) FILTER (WHERE metric = 'writers_28d') AS "active writers (audit)",
               count(DISTINCT instance_pk) AS "instances with the recorder"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND recorder = 'enabled'
          AND metric IN ('active_people_28d', 'writers_28d') AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title       = "K2 · Feature reach by area and channel (latest day, instances with the recorder)"
      description = "K2 is ui_all: distinct people over direct and embedded use. People sums across instances are person-instance pairs. 'API activity' is the backend's view of the same areas, the only channel API keys appear in."
      type        = "table"
      format      = "table"
      grid        = [0, 63, 24, 10]
      sql         = <<-SQL
        WITH areas(area) AS (${local.k2_areas}),
        rows AS (
          SELECT instance_pk, split_part(dimension, ':', 1) AS area, split_part(dimension, ':', 2) AS channel, value
          FROM fleet_metrics_daily
          WHERE ${local.latest} AND metric = 'area_people_28d' AND recorder = 'enabled' AND ${local.where}
        ),
        people AS (
          SELECT sum(value) AS active
          FROM fleet_metrics_daily
          WHERE ${local.latest} AND metric = 'active_people_28d' AND recorder = 'enabled' AND ${local.where}
        )
        SELECT a.area AS "area",
               count(DISTINCT r.instance_pk) FILTER (WHERE r.channel = 'ui_all' AND r.value > 0) AS "instances (interface)",
               sum(r.value) FILTER (WHERE r.channel = 'ui_all') AS "people (interface), person-instance pairs",
               round((100 * sum(r.value) FILTER (WHERE r.channel = 'ui_all')
                 / nullif(max(p.active), 0))::numeric, 1) AS "share of active people (%)",
               sum(r.value) FILTER (WHERE r.channel = 'ui_embed') AS "of which embedded (ui_embed)",
               count(DISTINCT r.instance_pk) FILTER (WHERE r.channel = 'api' AND r.value > 0) AS "API activity: instances",
               sum(r.value) FILTER (WHERE r.channel = 'api') AS "API activity: people"
        FROM areas a
        LEFT JOIN rows r ON r.area = a.area
        CROSS JOIN people p
        GROUP BY a.area
        ORDER BY 2 DESC, 1
      SQL
    },
    {
      title  = "K1 · Active people by instance (latest day, recorder enabled)"
      type   = "table"
      format = "table"
      grid   = [0, 73, 24, 9]
      sql    = <<-SQL
        SELECT instance_name AS "instance", client_name AS "client",
               coalesce(nullif(lex_app_version, ''), 'unknown') AS "version",
               max(value) FILTER (WHERE metric = 'active_people_1d') AS "people 1d",
               max(value) FILTER (WHERE metric = 'active_people_7d') AS "people 7d",
               max(value) FILTER (WHERE metric = 'active_people_28d') AS "people 28d",
               max(value) FILTER (WHERE metric = 'writers_28d') AS "writers 28d"
        FROM fleet_metrics_daily
        WHERE ${local.latest} AND recorder = 'enabled' AND ${local.where}
        GROUP BY instance_pk, instance_name, client_name, lex_app_version
        ORDER BY 6 NULLS FIRST, 1
      SQL
    },
  ]

  collection_r3 = [
    {
      title       = "Silence marks per day: recorder silent fails the heartbeat, ui silent does not"
      description = "recorder_silent: the audit shows a person changed data and the recorder saw nobody (spec §7). ui_silent: people through the API, no interface event at all, for example an interface overridden to an older package (spec §5.3.2)."
      type        = "timeseries"
      format      = "time_series"
      grid        = [0, 58, 12, 8]
      sql         = <<-SQL
        SELECT day::timestamp AS "time",
               count(*) FILTER (WHERE recorder_silent) AS "recorder silent",
               count(*) FILTER (WHERE ui_silent) AS "ui silent"
        FROM fleet_metrics_instanceday
        WHERE ${local.in_range} AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title  = "Silence marks, by instance-day"
      type   = "table"
      format = "table"
      grid   = [12, 58, 12, 8]
      sql    = <<-SQL
        SELECT day, instance_name AS "instance", coalesce(nullif(lex_app_version, ''), 'unknown') AS "version",
               recorder AS "recorder state",
               CASE WHEN recorder_silent THEN 'yes' END AS "recorder silent",
               CASE WHEN ui_silent THEN 'yes' END AS "ui silent"
        FROM fleet_metrics_instanceday
        WHERE ${local.in_range} AND (recorder_silent OR ui_silent) AND ${local.where}
        ORDER BY day DESC, instance_name
      SQL
    },
    {
      title       = "Invariant: people 1 day ≤ 7 days ≤ 28 days, and people 1 day ≥ editors 1 day (recorder enabled; should be empty)"
      description = "Spec §7."
      type        = "table"
      format      = "table"
      grid        = [0, 66, 24, 8]
      sql         = <<-SQL
        SELECT day, instance_name AS "instance", p1 AS "people 1d", p7 AS "people 7d", p28 AS "people 28d", e1 AS "editors 1d"
        FROM (
          SELECT day, instance_pk, instance_name,
                 max(value) FILTER (WHERE metric = 'active_people_1d') AS p1,
                 max(value) FILTER (WHERE metric = 'active_people_7d') AS p7,
                 max(value) FILTER (WHERE metric = 'active_people_28d') AS p28,
                 max(value) FILTER (WHERE metric = 'editors_1d') AS e1
          FROM fleet_metrics_daily
          WHERE ${local.in_range} AND recorder = 'enabled'
            AND metric IN ('active_people_1d', 'active_people_7d', 'active_people_28d', 'editors_1d') AND ${local.where}
          GROUP BY 1, 2, 3
        ) per_day
        WHERE p1 > p7 OR p7 > p28 OR e1 > p1
        ORDER BY day DESC, instance_name
      SQL
    },
  ]
}
```

- [ ] **Step 2: Add the panels to Product and Collection**

In `terraform/modules/fleet-metrics-grafana/dashboards.tf`, replace `dashboard_specs` with:

```hcl
  dashboard_specs = {
    product    = { uid = "fleet-product", title = "Fleet metrics: product", from = "now/y", panels = concat(local.product_r1, local.product_r3) }
    platform   = { uid = "fleet-platform", title = "Fleet metrics: platform", from = "now-90d", panels = local.platform_r2 }
    collection = { uid = "fleet-collection", title = "Fleet metrics: collection", from = "now-90d", panels = concat(local.collection_r1, local.collection_r2, local.collection_r3) }
  }
```

- [ ] **Step 3: Validate and render**

Run: `cd terraform/modules/fleet-metrics-grafana && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform fmt -check`
Expected: valid; nothing to format.

Run: `echo 'length(jsondecode(local.dashboards.product).panels)' | terraform console $V`
Expected: `17` (12 from R1, 5 from R3).

Run: `echo '[for p in jsondecode(local.dashboards.product).panels : p.targets[0].rawSql if startswith(p.title, "K2 · Feature reach in the interface")][0]' | terraform console $V`
Expected: SQL selecting `split_part(dimension, ':', 2) = 'ui_all'` and excluding `app`.

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/fleet-metrics-grafana
git commit -m "feat(grafana): feature reach by area and channel, and active people, on the product dashboard"
```

---

### Task 8: K9 on Platform (R4)

On branch `feat/fleet-metrics-k9`, cut from `origin/develop` after R3's panels merged.

**Files:**
- Create: `terraform/modules/fleet-metrics-grafana/panels_r4.tf`
- Modify: `terraform/modules/fleet-metrics-grafana/dashboards.tf`

**Interfaces:**
- Consumes (collector plan, R4): `api_seconds_p95` (K5's selection without 5xx); threshold key `k9_p95`.
- Produces: two K9 panels at the end of Platform.

Per-instance p95s cannot be combined into a fleet p95; the fleet view counts instances above the planning rule instead (spec §4.2 K9). K9 has no operational flag.

- [ ] **Step 1: Write the R4 panels**

`terraform/modules/fleet-metrics-grafana/panels_r4.tf`:

```hcl
# Release 4 (spec §4.1, §8.1): K9 response time, the lowest-priority KPI, on R2's source.

locals {
  platform_r4 = [
    {
      title       = "K9 · Running instances with an API p95 above the planning rule (successful requests)"
      description = "Each instance's p95 against the row valid that day; an instance-day counts only with at least min_sample requests without a 5xx."
      type        = "timeseries"
      format      = "time_series"
      grid        = [0, 63, 12, 8]
      field_config = {
        defaults  = { min = 0 }
        overrides = [{ matcher = { id = "byName", options = "instances above the planning rule" }, properties = [{ id = "color", value = { mode = "fixed", fixedColor = "blue" } }] }]
      }
      sql = <<-SQL
        WITH per_instance AS (
          SELECT d.day, d.instance_pk,
                 max(d.value) FILTER (WHERE d.metric = 'api_seconds_p95') AS p95,
                 sum(d.value) FILTER (WHERE d.metric = 'api_requests' AND d.dimension NOT IN ('5xx_unavailable', '5xx_app')) AS requests
          FROM fleet_metrics_daily d
          WHERE ${local.in_range_d} AND d.instance_status = 'RUNNING'
            AND d.metric IN ('api_seconds_p95', 'api_requests') AND ${local.where_d}
          GROUP BY 1, 2
        )
        SELECT p.day::timestamp AS "time",
               count(*) FILTER (WHERE p.p95 IS NOT NULL AND coalesce(p.requests, 0) >= coalesce(k9_p95.min_sample, 0)) AS "instances measured",
               count(*) FILTER (WHERE p.p95 > k9_p95.value AND coalesce(p.requests, 0) >= coalesce(k9_p95.min_sample, 0)) AS "instances above the planning rule"
        FROM per_instance p
        ${replace(local.threshold_at.k9_p95, "@day@", "p.day")}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title        = "K9 · API p95 by running instance (latest day)"
      type         = "table"
      format       = "table"
      grid         = [12, 63, 12, 8]
      field_config = local.flag_table
      sql          = <<-SQL
        WITH per_instance AS (
          SELECT d.day, d.instance_pk, d.instance_name,
                 max(d.value) FILTER (WHERE d.metric = 'api_seconds_p95') AS p95,
                 sum(d.value) FILTER (WHERE d.metric = 'api_requests' AND d.dimension NOT IN ('5xx_unavailable', '5xx_app')) AS requests
          FROM fleet_metrics_daily d
          WHERE d.day = ${local.latest_day} AND d.instance_status = 'RUNNING'
            AND d.metric IN ('api_seconds_p95', 'api_requests') AND ${local.where_d}
          GROUP BY 1, 2, 3
        )
        SELECT p.instance_name AS "instance", p.p95 AS "API p95 (s)", p.requests AS "requests without 5xx",
               CASE WHEN p.p95 > k9_p95.value AND coalesce(p.requests, 0) >= coalesce(k9_p95.min_sample, 0)
                    THEN 'rule met · ' || k9_p95.status END AS "planning rule"
        FROM per_instance p
        ${replace(local.threshold_at.k9_p95, "@day@", "p.day")}
        ORDER BY 2 DESC NULLS LAST
      SQL
    },
  ]
}
```

- [ ] **Step 2: Add the panels to Platform**

In `terraform/modules/fleet-metrics-grafana/dashboards.tf`, in `dashboard_specs`, change the `platform` line to:

```hcl
    platform   = { uid = "fleet-platform", title = "Fleet metrics: platform", from = "now-90d", panels = concat(local.platform_r2, local.platform_r4) }
```

- [ ] **Step 3: Validate and render**

Run: `cd terraform/modules/fleet-metrics-grafana && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform fmt -check`
Expected: valid; nothing to format.

Run: `echo '[for p in jsondecode(local.dashboards.platform).panels : p.title if startswith(p.title, "K9")]' | terraform console $V`
Expected: the two K9 titles.

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/fleet-metrics-grafana
git commit -m "feat(grafana): API response time per instance on the platform dashboard"
```

---

### Task 9: Roll out, release by release, and record the evidence

Every step here changes a real environment or a shared record. Get the user's go-ahead before each apply, before each production run or backfill, and before creating a Linear issue. Infra applies Terraform; the fleet-metrics owner (the LEX-749 assignee) runs the checks. Evidence is numbers in a comment, never "done". Dates are spec §8.1's; if the plan is approved later than 14 October, every date moves by the same number of working days.

Commands the steps use (bash):

```bash
# Before anything that writes: production must name superb-blend-305320, test lex-test-343008.
kubectl config current-context

# A collector run now, exactly as the CronJob runs it (it pings the heartbeat where one exists).
job="fleet-metrics-$(date -u +%Y%m%d%H%M)"
kubectl -n default create job "$job" --from=cronjob/collect-fleet-metrics
kubectl -n default logs -f "job/$job"

# A backfill of UTC days FIRST..LAST (inclusive), one calendar month per invocation, never
# pinging the heartbeat. Stops at the first failing month, which is then re-run alone.
backfill() {
  local from=$1 last=$2 to
  while [[ ! "$from" > "$last" ]]; do
    to=$(date -u -d "$(date -u -d "$from" +%Y-%m-01) +1 month -1 day" +%F)
    [[ "$to" > "$last" ]] && to=$last
    echo "backfill $from .. $to"
    kubectl -n default exec statefulset/lex-instance-controller-backend -- \
      python manage.py collect_fleet_metrics --from "$from" --to "$to" --no-heartbeat || return 1
    from=$(date -u -d "$to +1 day" +%F)
  done
}

# Prometheus, for R2.0, R2 and R4 (read-only); queries go to http://localhost:9090.
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

What the reader role can read, run in Grafana > Explore in the fleet-metrics organization (datasource "Fleet metrics"). The result must list only `fleet_metrics_*` relations:

```sql
SELECT c.relname
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm') AND has_table_privilege(c.oid, 'SELECT')
ORDER BY 1;
```

Backfills read the shared customer database server: start them after 18:00 Berlin.

- [ ] **Step 1 (R1.1, 15–23 Oct): Merge, and prove it in test**

Prerequisite: the collector's R1 (lex-instance-controller-backend `feat/fleet-metrics-collector`) merged and in the controller backend image; otherwise the first run fails with an unknown command.

```bash
git push -u origin feat/fleet-metrics
gh pr create --base develop --title "feat(fleet-metrics): the nightly collector, its heartbeat and its first dashboards" --body "$(cat <<'EOF'
Fleet metrics phase 1, release 1: the infrastructure half (LEX-749 / LEX-751). Design: lex-app `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md`, revision 2.

- CronJob `collect-fleet-metrics` (02:30 UTC), on in prod and test via their values files.
- A second Better Stack heartbeat; its URL reaches the controller through Secret Manager and `app-env-secret`, like `KEYCLOAK_DB_PASSWORD`.
- cluster-services: a Grafana login role with no privileges of its own (the collector grants SELECT on its tables every run), a separate Grafana organization, a PostgreSQL datasource, and two dashboards: Product (K1 as active writers, K3, K7) and Collection. Thresholds are read from the collector's `Threshold` table; none is written into a query.

No Prometheus query in this release. Needs the collector's release 1 in the backend image first.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

1. After review, merge. CI publishes the chart and deploys the test environment.
2. Infra applies cluster-services for test by hand, from `terraform/environments/devops/cluster-services`: `terragrunt plan`, review, `terragrunt apply` (organization members asked for first, Task 5 Step 6). Better Stack is off in test, so no heartbeat or secret is created there.
3. Run the collector once in test (context `lex-test-343008`). Expected log: `Fleet metrics run N: ok`, or `degraded` with named sources.
4. Check: every test instance has yesterday's row (Collection, "Rows per day"); every Product and Collection panel renders without a query error (panel menu > Inspect > Query shows the SQL that ran and any error); the reader's relation list is exactly `fleet_metrics_collectionrun`, `fleet_metrics_daily`, `fleet_metrics_deployevent`, `fleet_metrics_instanceday`, `fleet_metrics_metricvalue`, `fleet_metrics_threshold`.

The spec's R1.1 evidence includes a ping of a test heartbeat. The test cluster has no Better Stack (Task 2), so here the evidence is the run's `heartbeat = skipped` with the verdict it logged; the first real ping is R1.2's.

**Evidence** (comment on LEX-751): the PR; the run's id, status, duration and instance-days written, and from those the seconds per instance-day; rows against test instances; any panel that errored, and its fix; the reader's relation list.

- [ ] **Step 2 (R1.2, 27 Oct): Production go-live, backfill from 1 January, and the boundary check**

Prerequisites: Step 1's evidence; the Grafana organization members named.

1. **26 Oct.** Infra applies prod cluster-services by hand (heartbeat, secret, reader role, organization, datasource, Product, Collection), then runs `gcp-ic-prod-deployment.yaml` for the platform layer with the new chart version. Then `kubectl -n default get cronjob collect-fleet-metrics` shows schedule `30 2 * * *`.
2. **The first scheduled run, 27 Oct 02:30 UTC**, collects 26 Oct and catches up the 35 days before it. If Step 1's seconds per instance-day × 62 instances × 36 days exceeds 90 minutes, run `backfill 2026-09-21 2026-10-25` on 26 Oct after 18:00 Berlin so the scheduled run stays inside its two-hour deadline. Otherwise start nothing by hand: the scheduled run is the go-live.
3. **27 Oct morning, the go-live checks (spec §8.3):**
   - Collection, "Runs": the scheduled run is `ok` with heartbeat `sent`; Better Stack shows the heartbeat received.
   - Collection, "Coverage per source": every expected production instance has 26 Oct's row; controller, instance database and version are `ok` for at least 95%; Prometheus is "not delivered" everywhere; excluded instances are listed with their reason.
   - The reader's relation list is the R1 set of Step 1.
   - Spot check three instances (a busy one, a quiet one, a paused one) for 26 Oct. On each instance's database, with its own credentials as the database export job uses them, run the two queries below and compare with Grafana Explore: `SELECT metric, dimension, value FROM fleet_metrics_daily WHERE instance_name = '<instance>' AND day = '2026-10-26' AND metric IN ('writers_28d', 'calc_runs') ORDER BY 1, 2;`. Database counts match exactly.

     ```sql
     -- writers_28d for 26 Oct: the 28 UTC days 29 Sep … 26 Oct. The session runs in Berlin
     -- on purpose: the result must not depend on it.
     BEGIN READ ONLY;
     SET LOCAL TIME ZONE 'Europe/Berlin';
     SELECT count(DISTINCT a.author) AS writers_28d
     FROM audit_logging_auditlog a
     WHERE a.date >= TIMESTAMPTZ '2026-09-29 00:00:00+00' AND a.date < TIMESTAMPTZ '2026-10-27 00:00:00+00'
       AND a.author IS NOT NULL AND a.author <> '' AND a.author NOT LIKE 'system%'
       AND a.author <> 'Technical User'
       AND a.author NOT IN (SELECT name FROM rest_framework_api_key_apikey);
     ROLLBACK;
     ```

     ```sql
     -- calc_runs by outcome for 26 Oct, by the rule of spec §2.3, written by hand: a run is the
     -- rows sharing resource and a real calculation_id; its outcome is its last terminal state;
     -- it belongs to the UTC day of its latest status update.
     BEGIN READ ONLY;
     SET LOCAL TIME ZONE 'Europe/Berlin';
     WITH rows AS (
       SELECT a.resource,
              CASE WHEN coalesce(a.calculation_id, '') IN ('', 'test_id') THEN 'row:' || a.id
                   ELSE a.calculation_id END AS run,
              a.payload->>'is_calculated' AS state,
              s.updated_at
       FROM audit_logging_auditlog a
       JOIN audit_logging_auditlogstatus s ON s.audit_log_id = a.id
       WHERE s.created_at >= TIMESTAMPTZ '2026-10-24 00:00:00+00'
         AND s.created_at <  TIMESTAMPTZ '2026-10-27 00:00:00+00'
     ),
     runs AS (
       SELECT resource, run, max(updated_at) AS finished_at,
              (array_agg(state ORDER BY updated_at DESC)
                 FILTER (WHERE state IN ('SUCCESS', 'ERROR', 'ABORTED', 'CANCELLED')))[1] AS outcome
       FROM rows
       GROUP BY 1, 2
     )
     SELECT lower(outcome) AS outcome, count(*) AS runs
     FROM runs
     WHERE outcome IS NOT NULL
       AND finished_at >= TIMESTAMPTZ '2026-10-26 00:00:00+00' AND finished_at < TIMESTAMPTZ '2026-10-27 00:00:00+00'
     GROUP BY 1
     ORDER BY 1;
     ROLLBACK;
     ```

   - Before the backfill, note the stored `writers_1d` and `calc_runs` of the boundary instance (sub-step 5) for 24 and 25 Oct; the first run's catch-up wrote them.
4. **27 Oct after 18:00 Berlin, the backfill:** `backfill 2026-01-01 2026-10-26`. The days the first run already wrote are written again; their values must not change (spec §5.2, rule 2). Prometheus is not a source in R1, so every backfilled day stores it as `skipped`. Collection, "Missing days", shows nothing for 1 Jan – 26 Oct afterwards.
5. **The boundary check (24/25 Oct, the summer-time change)**, on one busy instance. 00:00Z on 25 Oct is 02:00 CEST, inside the hour Berlin repeats (spec §6, rule 4).

   ```sql
   BEGIN READ ONLY;
   SET LOCAL TIME ZONE 'Europe/Berlin';
   -- Writes by people from 23:00Z to 01:00Z, split at UTC midnight. If one side is empty,
   -- pick a busier instance: the check needs writes on both sides.
   SELECT (a.date AT TIME ZONE 'UTC')::date AS utc_day, count(*) AS writes, count(DISTINCT a.author) AS authors
   FROM audit_logging_auditlog a
   WHERE a.date >= TIMESTAMPTZ '2026-10-24 23:00:00+00' AND a.date < TIMESTAMPTZ '2026-10-25 01:00:00+00'
     AND a.author IS NOT NULL AND a.author <> '' AND a.author NOT LIKE 'system%'
     AND a.author <> 'Technical User' AND a.author NOT IN (SELECT name FROM rest_framework_api_key_apikey)
   GROUP BY 1 ORDER BY 1;
   -- Writers per whole UTC day: must equal the stored writers_1d of 24 and 25 Oct.
   SELECT (a.date AT TIME ZONE 'UTC')::date AS utc_day, count(DISTINCT a.author) AS writers_1d
   FROM audit_logging_auditlog a
   WHERE a.date >= TIMESTAMPTZ '2026-10-24 00:00:00+00' AND a.date < TIMESTAMPTZ '2026-10-26 00:00:00+00'
     AND a.author IS NOT NULL AND a.author <> '' AND a.author NOT LIKE 'system%'
     AND a.author <> 'Technical User' AND a.author NOT IN (SELECT name FROM rest_framework_api_key_apikey)
   GROUP BY 1 ORDER BY 1;
   ROLLBACK;
   ```

   Pass: the stored `writers_1d` equals the second query for both days, and the stored values are the same before and after the backfill.
6. **K3 validation** on two instances: the week 19–25 Oct of `calc_runs` (sum of all outcomes) against the distinct runs `CalculationLog` holds:

   ```sql
   SELECT count(DISTINCT "calculationId") AS runs_logged
   FROM audit_logging_calculationlog
   WHERE timestamp >= TIMESTAMPTZ '2026-10-19 00:00:00+00' AND timestamp < TIMESTAMPTZ '2026-10-26 00:00:00+00'
     AND "calculationId" NOT IN ('', 'test_id');
   ```

   More than 5% apart: a recorder hook replaces the rule (spec §4.2 K3, §10); record that decision.
7. **Book the one-week check.** Ask the user to approve creating a Linear issue (team LEX) under LEX-751, assigned to the LEX-749 assignee, due **3 Nov 2026**, titled "Fleet metrics R1: a week of data, checked", with Step 3's checklist.

**Evidence** (comment on LEX-751): the run's id, status and heartbeat, with the Better Stack time; rows against expected; each R1 source's `ok` share; the excluded instances and reasons; the reader's relation list; the spot-check table (3 instances × `writers_28d` and four `calc_runs`, hand against stored); the boundary split and both days' writers, before and after the backfill; the K3 comparison with its percentage and decision; the backfill's months and run statuses; the link to the 3 Nov issue.

- [ ] **Step 3 (R1.4, 3 Nov): The one-week check**

Work through the 3 Nov issue and close it with the numbers:

- Seven consecutive days, 27 Oct – 2 Nov, with a row for every expected instance: Collection's "Missing days" is empty for that range, and "Last collected day per instance" shows 2 Nov for every instance still in the fleet.
- Seven `cron` runs in "Runs". Every `failure-sent` heartbeat is explained in the issue and healed by the catch-up (that day's sources later `ok`); Better Stack has no open incident.
- The spot check of Step 2, repeated for 30 Oct on the same three instances.
- The writers invariant is empty.
- R1.3's provisional rows (K1, K3, K7) are in the threshold log, marked `in force`, and Product draws them: the K3 and K7 planning lines, and the flag columns of K1, K3 and K7 where a rule is met.
- K8: LEX-993 names its owners, or the issue records the escalation (spec §9).

**Evidence:** the closed issue with those numbers. R2 starts only after it is closed.

- [ ] **Step 4 (R2.0, Infra, by 30 Oct): Read how far back production Prometheus reaches**

Read-only, and independent of R1's steps: Infra can do it any day before 30 Oct. Production retention is configured at 180 days, but configured retention is a ceiling, not a guarantee: the test Prometheus holds data only from 18 August (spec §5.8).

```bash
kubectl config current-context   # must name superb-blend-305320
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
# second shell:
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=prometheus_tsdb_lowest_timestamp_seconds'
date -u -d @<value>
```

If more than one series comes back (one per Prometheus replica), take the latest value: it is the depth every replica holds.

**Evidence** (comment on LEX-995): the value, its UTC time, the date it was read, and the first full UTC day after it. That day is where R2.1's backfill starts. The oldest sample moves forward as retention prunes, so R2.1 and R4 read it again on their backfill day.

Note for Infra, about the test environment only (it blocks no release): on 2026-10-08 the test cluster's Prometheus volume held 27 of its 63 GB and grows about 0.47 GB a day, so it fills around late December, before the 180-day retention would prune anything. Whether to grow that volume or cap retention by size there is Infra's call.

- [ ] **Step 5 (R2.1, build 3–6 Nov, go-live 10 Nov): Platform, Prometheus backfill, and start events**

Prerequisites: Step 3's issue closed; Step 4's value on LEX-995; the collector's R2 (Prometheus source, `StartEvent`, the grant on it) merged and in the controller image; Task 6 committed on `feat/fleet-metrics-platform`.

```bash
git push -u origin feat/fleet-metrics-platform
gh pr create --base develop --title "feat(fleet-metrics): the platform dashboard" --body "$(cat <<'EOF'
Fleet metrics phase 1, release 2 (LEX-749 / LEX-751): the Platform dashboard — K5 request success, K6 starts and startup time from `StartEvent`, and K8's panel, which shows "not yet delivered" until its owners fill `DeployEvent`. Collection gains the start-event invariant. Thresholds come from the `Threshold` table.

Grafana module only: no chart change, nothing deploys on merge; cluster-services is applied by hand.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

1. After review, merge. In test: Infra applies cluster-services; the controller with the collector's R2 is deployed there; run the collector once. Every Platform panel renders without a query error, start events appear for test pods, and the reader's list now includes `fleet_metrics_startevent`.
2. **9 Nov.** In production, Infra deploys the controller release carrying the collector's R2 and applies cluster-services (Platform, the Collection invariant). Run the collector once by hand straight away: its grant makes `fleet_metrics_startevent` readable; until it runs, the start panels show "permission denied".
3. **9 Nov after 18:00 Berlin, the Prometheus backfill.** Read the oldest sample again (Step 4), then `backfill <first full UTC day after it> 2026-11-08`. Never earlier (Global Constraints). Days before it keep Prometheus `skipped`.
4. **10 Nov, the go-live checks (spec §8.3):**
   - The scheduled run is `ok` with heartbeat `sent`; Better Stack received it.
   - Collection, "Coverage per source": Prometheus `ok` for at least 95% of production instances on 9 Nov.
   - Spot check `api_requests` for 9 Nov on three instances (busy, quiet, paused) against PromQL, within 1%. Fold the statuses into the five classes (502, 503, 504 → `5xx_unavailable`; any other 5xx → `5xx_app`). An empty answer for a paused instance is zero requests (spec §5.8).

     ```bash
     curl -s 'http://localhost:9090/api/v1/query' \
       --data-urlencode 'query=sum by (status) (increase(nginx_ingress_controller_requests{ingress="ingress-<rid>", path="/api(/|$)(.*)", exported_namespace="default", status!="101"}[1d]))' \
       --data-urlencode "time=$(date -u -d '2026-11-10 00:00:00' +%s)"
     ```

   - Three start events from `fleet_metrics_startevent` — a deploy, a restart and a resume — against the pod's own timestamps, within one scrape interval: ready − created equals `startup_seconds`, initialized − scheduled equals `preparation_seconds`. The cause matches the controller operation running when the pod was created (the controller shows naive Berlin times; convert them, spec §6).

     ```bash
     for m in kube_pod_created kube_pod_status_scheduled_time kube_pod_status_initialized_time kube_pod_status_ready_time; do
       curl -s 'http://localhost:9090/api/v1/query' \
         --data-urlencode "query=max_over_time(${m}{namespace=\"default\", pod=\"<pod>\"}[1d])" \
         --data-urlencode "time=$(date -u -d '<the start day + 1> 00:00:00' +%s)"
     done
     ```

   - Collection's start-event invariant is empty; Platform's K8 panel shows "not yet delivered", or the owners' weeks if K8.1 has delivered.
   - The reader's list is the R1 set plus `fleet_metrics_startevent`.
   - R2's baseline review (spec §4.4, by 13 Nov): K5 and K6 rows in `Threshold`. Once they exist, the K5 and K6 planning lines and flag tables draw them.
5. **Book the one-week check.** Ask the user to approve a Linear issue under LEX-751, assigned to the LEX-749 assignee, due **17 Nov 2026**, titled "Fleet metrics R2: a week of platform data, checked", with Step 6's checklist.

**Evidence** (comment on LEX-751): the PR; the run and heartbeat; the Prometheus `ok` share; the backfill's first day (with the oldest sample re-read) and its months; the PromQL spot-check table; the three start events with pod times, stored durations and causes; the reader's list; the K8 panel's state; the baseline-review link; the link to the 17 Nov issue.

- [ ] **Step 6 (R2.2, 17 Nov): The platform one-week check**

Work through the 17 Nov issue and close it with the numbers:

- Seven days, 10–16 Nov, with Prometheus `ok` for at least 95% of instances each day.
- Every `failure-sent` heartbeat explained and healed by the catch-up.
- The PromQL spot check of Step 5, repeated for 13 Nov.
- The start-event invariant is empty, and start events per day equal `backend_starts` summed over causes:

  ```sql
  SELECT s.day, count(*) AS start_events,
         (SELECT sum(m.value) FROM fleet_metrics_daily m WHERE m.day = s.day AND m.metric = 'backend_starts') AS backend_starts
  FROM fleet_metrics_startevent s
  WHERE s.day BETWEEN '2026-11-10' AND '2026-11-16'
  GROUP BY s.day
  ORDER BY 1;
  ```

- K5 and K6 rows in the threshold log, drawn on Platform.
- K8's state, from LEX-993.

**Evidence:** the closed issue with those numbers.

- [ ] **Step 7 (R3: panels by 13 Nov; R3.3 one week after the first R3 upgrade): K2 and exact K1**

Prerequisites: the collector's R3 (recorder tables read, `ui_silent`, `recorder_silent`) in the controller image; Task 7 committed on `feat/fleet-metrics-k2`.

```bash
git push -u origin feat/fleet-metrics-k2
gh pr create --base develop --title "feat(fleet-metrics): feature reach and active people on the product dashboard" --body "$(cat <<'EOF'
Fleet metrics phase 1, release 3 (LEX-749 / LEX-751): K2 feature reach by area and channel (ui_all, distinct people over direct and embedded use, is the K2 figure; api is labelled API activity), exact K1 from the usage recorder, and on Collection the silence marks and the people invariants.

Grafana module only: no chart change, nothing deploys on merge; cluster-services is applied by hand.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

1. **Before the first production upgrade.** After review, merge. Infra applies cluster-services in test. On the test instance used for spec §5.3.2's end-to-end verification, Product's K2 panels show it under `grid`, `record`, `history` and `calculate` and nothing under `reports`; every new panel renders without a query error. Then Infra applies production. The new panels stay empty until an instance runs the R3 release.
2. **At the first production upgrade (R3.2, 16–27 Nov).** Ask the user to approve a Linear issue under LEX-751, assigned to the LEX-749 assignee, due seven days after that upgrade, titled "Fleet metrics R3: a week of interface data, checked", with the checklist below.
3. **R3.3, one week after the first upgrade.** Work through the issue and close it with the numbers:
   - The §7 invariants hold on every day the recorder is enabled: Collection's people invariant is empty, and every `recorder_silent` instance-day is explained and its failed heartbeat healed.
   - Every `ui_silent` mark is explained: a day of API-only work, or an interface that does not emit.
   - Coverage matches K7: on "Coverage by lex-app version", every instance on the R3 release shows the recorder `enabled` or `disabled`, none `absent`, and older versions show `absent`.
   - Product: K2 lists the upgraded instances; on recorder days, active people are at least active writers.
   - The spec §5.3.2 production verification (R3.2) is recorded in the R3 acceptance issue.

**Evidence:** the closed issue with those numbers, and the PR on LEX-751.

- [ ] **Step 8 (R4, cycle 8: 16–29 Nov): K9**

Prerequisites: Step 6's issue closed; the collector's R4 (`api_seconds_p95`) in the controller image; Task 8 committed on `feat/fleet-metrics-k9`.

```bash
git push -u origin feat/fleet-metrics-k9
gh pr create --base develop --title "feat(fleet-metrics): API response time on the platform dashboard" --body "$(cat <<'EOF'
Fleet metrics phase 1, release 4 (LEX-749 / LEX-751): K9, the API p95 per instance, counted against the planning rule in the `Threshold` table. Per-instance p95s are never combined into a fleet p95.

Grafana module only: no chart change, nothing deploys on merge; cluster-services is applied by hand.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

1. After review, merge. Infra applies cluster-services in test, checks the two K9 panels render, then applies production after the controller release with the collector's R4 is deployed there.
2. After 18:00 Berlin, read the oldest Prometheus sample again (Step 4) and `backfill <first full UTC day after it> <yesterday>`, so K9's baseline covers the same depth as K5's (spec §4.4).
3. Spot check `api_seconds_p95` for one busy instance on two days against PromQL, within 1%:

   ```bash
   curl -s 'http://localhost:9090/api/v1/query' \
     --data-urlencode 'query=histogram_quantile(0.95, sum by (le) (increase(nginx_ingress_controller_request_duration_seconds_bucket{ingress="ingress-<rid>", path="/api(/|$)(.*)", exported_namespace="default", status!~"101|5.."}[1d])))' \
     --data-urlencode "time=$(date -u -d '<day + 1> 00:00:00' +%s)"
   ```

4. K9's planning row, set at R4 from the R2 baseline (spec §4.4), is in the threshold log, and the K9 panels count instances against it.

**Evidence** (comment on LEX-751): the PR; the backfill's first day and months; the two spot checks, PromQL against stored; the K9 threshold row.
