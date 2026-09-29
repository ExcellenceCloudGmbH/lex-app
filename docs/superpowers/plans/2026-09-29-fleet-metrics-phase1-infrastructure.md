# Fleet metrics phase 1 — the infrastructure (instance-controller-deployment) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the collector every night, hear about it when it stops writing, and give the internal team — and nobody else — three Grafana dashboards over what it wrote.

**Architecture:** A `collect-fleet-metrics` CronJob in the Instance Controller chart, enabled per environment through its values file. A second Better Stack heartbeat, whose URL travels to the CronJob through Secret Manager and `app-env-secret`, exactly like `KEYCLOAK_DB_PASSWORD`. In the cluster-services layer: a login role for Grafana with no table privileges (the collector grants SELECT on its own tables), a separate Grafana organization, a PostgreSQL datasource inside it, and three dashboards defined as HCL.

**Tech Stack:** Helm 3, Terraform ~1.6 with Terragrunt 0.58, providers `BetterStackHQ/better-uptime`, `ricochet1k/postgresql`, `grafana/grafana`, `google`.

**Spec:** lex-app `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md` (§5.4.1, §5.6, §7, §8)

## Global Constraints

- Repository `instance-controller-deployment`; base `origin/develop` (the CronJob template and the Better Stack module exist only there); branch `feat/fleet-metrics`; PR into `develop`.
- Pushing `chart/**` to `develop` publishes the chart and deploys the **test** environment automatically; **the chart version in `chart/Chart.yaml` must be bumped** or CI republishes the old one.
- The cluster-services layer is applied **by hand** (Terragrunt `before_hook` port-forwards to Cloud SQL); nothing applies it from CI. Prod platform is applied by the manual `gcp-ic-prod-deployment.yaml` workflow.
- GCP project IDs: test cluster `lex-test-343008` (environment `devops`), prod `superb-blend-305320`. Both clusters are called `lex-cluster-2`; check `kubectl config current-context` before anything that writes.
- Collector settings (read by the controller, defaults in its `settings.py`): `FLEET_METRICS_HEARTBEAT_URL`, `FLEET_METRICS_INCLUDE_DB_ALIASES` (default `false`), `FLEET_METRICS_READER_ROLE` (default `fleet_metrics_reader`), `FLEET_METRICS_CATCHUP_DAYS` (35), `FLEET_METRICS_PROMETHEUS_RETENTION_DAYS` (180), `FLEET_METRICS_API_PATH` (`/api(/|$)(.*)`), `FLEET_METRICS_NAMESPACE_LABEL` (`exported_namespace`). Only the heartbeat URL is set here; the others keep their defaults unless the collector plan's Task 1 found different labels.
- The CronJob runs at `30 2 * * *` in `Etc/UTC`.
- Dashboards: time zone UTC; every day filter is TZ-independent (epoch seconds converted `AT TIME ZONE 'UTC'`), never `$__timeFilter` on a `date` column, whose cast to `timestamptz` follows the session time zone.
- Commit subjects are conventional and lower-case. Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| Path | Responsibility |
|---|---|
| `chart/templates/fleet_metrics_cronjob.yaml` (create) | The CronJob |
| `chart/values.yaml` (modify) | `fleetMetrics` defaults (off) |
| `chart/Chart.yaml` (modify) | Version bump |
| `terraform/modules/instance-controller/values-prod.yaml`, `values-test.yaml` (modify) | Turn it on in prod and test |
| `terraform/modules/betterstack/main.tf` (modify) | The collector's heartbeat and its URL output |
| `terraform/layers/cluster-services/main.tf`, `variables.tf`, `providers.tf` (modify) | Secret for the URL; the reader role; the Grafana module |
| `terraform/modules/fleet-metrics-reader/main.tf` (create) | Grafana's login role on the Cloud SQL server |
| `terraform/modules/fleet-metrics-grafana/main.tf`, `dashboards.tf` (create) | Organization, datasource, folder, dashboards |
| `terraform/layers/platform/main.tf`, `variables.tf` (modify) | Read the URL into `app-env-secret` |
| `terraform/environments/prod/cluster-services/terragrunt.hcl`, `environments/prod/platform/terragrunt.hcl` (modify) | Turn it on in prod |

---

### Task 1: The CronJob

**Files:**
- Create: `chart/templates/fleet_metrics_cronjob.yaml`
- Modify: `chart/values.yaml`, `chart/Chart.yaml`, `terraform/modules/instance-controller/values-prod.yaml`, `terraform/modules/instance-controller/values-test.yaml`

**Interfaces:**
- Consumes: the collector's command `python manage.py collect_fleet_metrics` (collector plan, Task 13).
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

### Task 2: The heartbeat

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

### Task 3: The URL reaches the CronJob

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

### Task 4: Grafana's login role

**Files:**
- Create: `terraform/modules/fleet-metrics-reader/main.tf`
- Modify: `terraform/layers/cluster-services/main.tf`, `terraform/layers/cluster-services/variables.tf`

**Interfaces:**
- Produces: PostgreSQL role `fleet_metrics_reader` (login, random password, **no table privileges**); module outputs `user`, `password`. The collector grants it SELECT on its own relations every run (collector plan, Task 2).

- [ ] **Step 1: Write the module**

`terraform/modules/fleet-metrics-reader/main.tf`:

```hcl
# Grafana's login role for fleet metrics (LEX-749).
#
# It is created with no privileges on any table. The Instance Controller owns the
# fleet_metrics tables and grants this role SELECT on exactly those, at the start of
# every collection run (fleet_metrics/grants.py), because the tables are created by
# the controller's migrations after this role exists. CONNECT on the database and
# USAGE on the public schema are PostgreSQL's defaults for every role.
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

### Task 5: The Grafana organization, datasource and dashboards

**Files:**
- Create: `terraform/modules/fleet-metrics-grafana/main.tf`, `terraform/modules/fleet-metrics-grafana/dashboards.tf`
- Modify: `terraform/layers/cluster-services/providers.tf`, `terraform/layers/cluster-services/main.tf`, `terraform/environments/prod/cluster-services/terragrunt.hcl`

**Interfaces:**
- Consumes: `module.fleet_metrics_reader[0].user/password` (Task 4); `module.cloud-sql-proxy.service` and `var.db_name` (existing in the layer); `module.gcp-secrets.grafana_password` (existing).
- Produces: organization "Excellence Cloud — fleet metrics"; datasource uid `fleet-metrics`; folder uid `fleet-metrics`; dashboards `fleet-product`, `fleet-platform`, `fleet-collection`.

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
# datasource that can read the controller's fleet_metrics tables, and three dashboards.

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

resource "grafana_dashboard" "fleet_metrics" {
  for_each = local.dashboards

  org_id      = grafana_organization.fleet_metrics.org_id
  folder      = grafana_folder.fleet_metrics.uid
  config_json = jsonencode(each.value)
  overwrite   = true

  depends_on = [grafana_data_source.fleet_metrics]
}
```

- [ ] **Step 3: Write the dashboards**

`terraform/modules/fleet-metrics-grafana/dashboards.tf`:

```hcl
# The three fleet-metrics dashboards (spec §5.6), as data.
#
# Rules every query follows:
#   * a day filter never depends on the session time zone: the range comes in as epoch
#     seconds and is converted AT TIME ZONE 'UTC' (never $__timeFilter on a date column,
#     whose cast to timestamptz follows the session zone);
#   * rates are sums of numerators over sums of denominators, never averages of rates;
#   * a sum of per-instance people counts is "person-instance pairs", labelled so.

locals {
  ds = { type = "grafana-postgresql-datasource", uid = "fleet-metrics" }

  in_range   = "day BETWEEN (to_timestamp($__unixEpochFrom()) AT TIME ZONE 'UTC')::date AND (to_timestamp($__unixEpochTo()) AT TIME ZONE 'UTC')::date"
  in_range_e = "e.day BETWEEN (to_timestamp($__unixEpochFrom()) AT TIME ZONE 'UTC')::date AND (to_timestamp($__unixEpochTo()) AT TIME ZONE 'UTC')::date"
  latest     = "day = (SELECT max(day) FROM fleet_metrics_instanceday)"
  where      = "release_stage IN ($stage) AND instance_type IN ($type) AND client_name IN ($client) AND instance_name IN ($instance)"
  where_d    = "d.release_stage IN ($stage) AND d.instance_type IN ($type) AND d.client_name IN ($client) AND d.instance_name IN ($instance)"

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

  product_panels = [
    {
      title = "K1 · Active people, 28 days (person-instance pairs)", type = "timeseries", format = "time_series", grid = [0, 0, 12, 8]
      sql   = <<-SQL
        SELECT day::timestamp AS "time",
               sum(value) FILTER (WHERE metric = 'active_people_28d') AS "active people (recorder)",
               sum(value) FILTER (WHERE metric = 'writers_28d') AS "active writers (audit, every instance)"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title = "K1 · Dormant instances: at most one person in 28 days", type = "stat", format = "table", grid = [12, 0, 4, 8]
      sql   = <<-SQL
        SELECT count(*) AS "dormant"
        FROM (
          SELECT instance_pk,
                 coalesce(max(value) FILTER (WHERE metric = 'active_people_28d'),
                          max(value) FILTER (WHERE metric = 'writers_28d')) AS people
          FROM fleet_metrics_daily
          WHERE ${local.latest} AND ${local.where}
          GROUP BY instance_pk
        ) per_instance
        WHERE people <= 1
      SQL
    },
    {
      title = "K1 · Instances by activity (latest day)", type = "table", format = "table", grid = [16, 0, 8, 8]
      sql   = <<-SQL
        SELECT instance_name AS "instance", client_name AS "client", recorder,
               max(value) FILTER (WHERE metric = 'active_people_28d') AS "active people 28d",
               max(value) FILTER (WHERE metric = 'writers_28d') AS "writers 28d"
        FROM fleet_metrics_daily
        WHERE ${local.latest} AND ${local.where}
        GROUP BY 1, 2, 3
        ORDER BY 4 NULLS LAST, 5
      SQL
    },
    {
      title = "K2 · Feature reach: instances using each area in 28 days (latest day)", type = "bargauge", format = "table", grid = [0, 8, 12, 9]
      sql   = <<-SQL
        SELECT area, instances FROM (
          SELECT dimension AS area, count(DISTINCT instance_pk) AS instances
          FROM fleet_metrics_daily
          WHERE ${local.latest} AND metric = 'area_people_28d' AND value > 0 AND ${local.where}
          GROUP BY 1
          UNION ALL
          SELECT 'calculations', count(DISTINCT instance_pk)
          FROM fleet_metrics_daily
          WHERE ${local.latest} AND metric = 'calc_people_28d' AND value > 0 AND ${local.where}
        ) reach
        ORDER BY instances DESC
      SQL
    },
    {
      title = "K2 · Recorder coverage (latest day)", type = "bargauge", format = "table", grid = [12, 8, 12, 9]
      sql   = <<-SQL
        SELECT recorder, count(*) AS instances
        FROM fleet_metrics_instanceday
        WHERE ${local.latest} AND ${local.where}
        GROUP BY 1
        ORDER BY 2 DESC
      SQL
    },
    {
      title = "K3 · Calculation failure share (error + aborted over finished; cancelled excluded)", type = "timeseries", format = "time_series", grid = [0, 17, 12, 8]
      sql   = <<-SQL
        SELECT day::timestamp AS "time",
               sum(value) FILTER (WHERE dimension IN ('error', 'aborted'))
                 / nullif(sum(value) FILTER (WHERE dimension IN ('success', 'error', 'aborted')), 0) AS "failure share"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'calc_runs' AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title = "K3 · Calculation durations by instance (latest day)", type = "table", format = "table", grid = [12, 17, 12, 8]
      sql   = <<-SQL
        SELECT instance_name AS "instance",
               max(value) FILTER (WHERE metric = 'calc_seconds_p95') AS "p95 (s)",
               max(value) FILTER (WHERE metric = 'calc_seconds_p50') AS "p50 (s)",
               sum(value) FILTER (WHERE metric = 'calc_runs') AS "runs"
        FROM fleet_metrics_daily
        WHERE ${local.latest} AND ${local.where}
        GROUP BY 1
        HAVING sum(value) FILTER (WHERE metric = 'calc_runs') > 0
        ORDER BY 2 DESC NULLS LAST
      SQL
    },
    {
      title = "K4 · Writes by channel (share)", type = "timeseries", format = "time_series", grid = [0, 25, 12, 8]
      sql   = <<-SQL
        SELECT day::timestamp AS "time", dimension AS "metric",
               sum(value) / nullif(sum(sum(value)) OVER (PARTITION BY day), 0) AS "value"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'writes' AND ${local.where}
        GROUP BY day, dimension
        ORDER BY 1
      SQL
    },
    {
      title = "K4 · API keys active in 28 days, by area (key-instance pairs, latest day)", type = "bargauge", format = "table", grid = [12, 25, 12, 8]
      sql   = <<-SQL
        SELECT dimension AS area, sum(value) AS "api keys"
        FROM fleet_metrics_daily
        WHERE ${local.latest} AND metric = 'area_api_keys_28d' AND ${local.where}
        GROUP BY 1
        ORDER BY 2 DESC
      SQL
    },
  ]

  platform_panels = [
    {
      title = "K5 · API requests answered 5xx (share)", type = "timeseries", format = "time_series", grid = [0, 0, 12, 8]
      sql   = <<-SQL
        SELECT day::timestamp AS "time",
               sum(value) FILTER (WHERE dimension = '5xx_unavailable') / nullif(sum(value), 0) AS "unavailable (502/503/504)",
               sum(value) FILTER (WHERE dimension = '5xx_app') / nullif(sum(value), 0) AS "application errors"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'api_requests' AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title = "K5 · 5xx share by instance, last 7 days", type = "table", format = "table", grid = [12, 0, 12, 8]
      sql   = <<-SQL
        SELECT instance_name AS "instance",
               sum(value) FILTER (WHERE dimension = '5xx_unavailable') / nullif(sum(value), 0) AS "unavailable",
               sum(value) FILTER (WHERE dimension = '5xx_app') / nullif(sum(value), 0) AS "application errors",
               sum(value) AS "requests"
        FROM fleet_metrics_daily
        WHERE day > (SELECT max(day) FROM fleet_metrics_instanceday) - 7 AND metric = 'api_requests' AND ${local.where}
        GROUP BY 1
        ORDER BY 2 DESC NULLS LAST
      SQL
    },
    {
      title = "K6 · Backend starts from zero per day (autoscaled instances)", type = "timeseries", format = "time_series", grid = [0, 8, 12, 8]
      sql   = <<-SQL
        SELECT day::timestamp AS "time", sum(value) AS "starts"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'backend_starts' AND autoscaling AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title = "K6 · Share of the day scaled down (autoscaled instances)", type = "timeseries", format = "time_series", grid = [12, 8, 12, 8]
      sql   = <<-SQL
        SELECT day::timestamp AS "time", sum(value) / nullif(1440 * count(*), 0) AS "scaled down"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'backend_down_minutes' AND autoscaling AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title = "K7 · Versions in use (latest day)", type = "table", format = "table", grid = [0, 16, 8, 8]
      sql   = <<-SQL
        SELECT coalesce(nullif(lex_app_version, ''), 'unknown') AS "version", count(*) AS "instances"
        FROM fleet_metrics_instanceday
        WHERE ${local.latest} AND ${local.where}
        GROUP BY 1
        ORDER BY 2 DESC
      SQL
    },
    {
      title = "K7 · Instances on a version older than 90 days (share)", type = "timeseries", format = "time_series", grid = [8, 16, 16, 8]
      sql   = <<-SQL
        SELECT day::timestamp AS "time",
               (count(*) FILTER (WHERE value > 90))::float / nullif(count(*), 0) AS "older than 90 days"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'version_age_days' AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title = "K8 · Deploys per week: count, failure share, p95 duration", type = "table", format = "table", grid = [0, 24, 12, 8]
      sql   = <<-SQL
        SELECT date_trunc('week', e.day::timestamp) AS "week",
               count(*) AS "deploys",
               (count(*) FILTER (WHERE e.outcome = 'FAILURE'))::float / count(*) AS "failure share",
               percentile_cont(0.95) WITHIN GROUP (ORDER BY e.duration_seconds) AS "p95 (s)"
        FROM fleet_metrics_deployevent e
        JOIN fleet_metrics_instanceday d ON d.instance_pk = e.instance_pk AND d.day = e.day
        WHERE ${local.in_range_e} AND ${local.where_d}
        GROUP BY 1
        ORDER BY 1 DESC
      SQL
    },
    {
      title = "K9 · Instances with an API p95 over 2 s", type = "timeseries", format = "time_series", grid = [12, 24, 12, 8]
      sql   = <<-SQL
        SELECT day::timestamp AS "time",
               count(DISTINCT instance_pk) FILTER (WHERE value > 2) AS "over 2 s",
               count(DISTINCT instance_pk) AS "measured"
        FROM fleet_metrics_daily
        WHERE ${local.in_range} AND metric = 'api_seconds_p95' AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
  ]

  collection_panels = [
    {
      title = "Rows per day, and what failed", type = "timeseries", format = "time_series", grid = [0, 0, 24, 8]
      sql   = <<-SQL
        SELECT day::timestamp AS "time",
               count(*) AS "rows",
               count(*) FILTER (WHERE db_status = 'error') AS "database errors",
               count(*) FILTER (WHERE prometheus_status = 'error') AS "Prometheus errors",
               count(*) FILTER (WHERE controller_status = 'error') AS "controller errors",
               count(*) FILTER (WHERE recorder_silent) AS "recorder silent"
        FROM fleet_metrics_instanceday
        WHERE ${local.in_range} AND ${local.where}
        GROUP BY 1
        ORDER BY 1
      SQL
    },
    {
      title = "Failed sources, last 7 days", type = "table", format = "table", grid = [0, 8, 24, 8]
      sql   = <<-SQL
        SELECT day, instance_name AS "instance", db_status, prometheus_status, controller_status,
               recorder_silent, errors::text AS "errors"
        FROM fleet_metrics_instanceday
        WHERE day > (SELECT max(day) FROM fleet_metrics_instanceday) - 7
          AND (db_status = 'error' OR prometheus_status = 'error' OR controller_status = 'error' OR recorder_silent)
          AND ${local.where}
        ORDER BY day DESC, instance_name
      SQL
    },
    {
      title = "Invariant violations (should be empty)", type = "table", format = "table", grid = [0, 16, 24, 8]
      sql   = <<-SQL
        SELECT day, instance_name AS "instance", recorder, w1, w7, w28, p1, p7, p28, e1
        FROM (
          SELECT day, instance_name, recorder,
                 max(value) FILTER (WHERE metric = 'writers_1d') AS w1,
                 max(value) FILTER (WHERE metric = 'writers_7d') AS w7,
                 max(value) FILTER (WHERE metric = 'writers_28d') AS w28,
                 max(value) FILTER (WHERE metric = 'active_people_1d') AS p1,
                 max(value) FILTER (WHERE metric = 'active_people_7d') AS p7,
                 max(value) FILTER (WHERE metric = 'active_people_28d') AS p28,
                 max(value) FILTER (WHERE metric = 'editors_1d') AS e1
          FROM fleet_metrics_daily
          WHERE ${local.in_range} AND ${local.where}
          GROUP BY 1, 2, 3
        ) per_day
        WHERE w1 > w7 OR w7 > w28 OR p1 > p7 OR p7 > p28 OR (recorder = 'enabled' AND e1 > p1)
        ORDER BY day DESC
      SQL
    },
    {
      title = "Last collected day per instance", type = "table", format = "table", grid = [0, 24, 12, 8]
      sql   = <<-SQL
        SELECT instance_name AS "instance", max(day) AS "last day", max(collected_at) AS "collected at"
        FROM fleet_metrics_instanceday
        WHERE ${local.where}
        GROUP BY 1
        ORDER BY 2, 1
      SQL
    },
    {
      title = "Runs", type = "table", format = "table", grid = [12, 24, 12, 8]
      sql   = <<-SQL
        SELECT started_at, finished_at, trigger, status, instances_expected, instance_days_written,
               instance_days_failed, heartbeat, summary::text AS "summary"
        FROM fleet_metrics_collectionrun
        ORDER BY started_at DESC
        LIMIT 30
      SQL
    },
  ]

  dashboards = {
    for key, spec in {
      product    = { uid = "fleet-product", title = "Fleet metrics: product", panels = local.product_panels }
      platform   = { uid = "fleet-platform", title = "Fleet metrics: platform", panels = local.platform_panels }
      collection = { uid = "fleet-collection", title = "Fleet metrics: collection", panels = local.collection_panels }
    } : key => {
      uid           = spec.uid
      title         = spec.title
      timezone      = "utc"
      editable      = false
      schemaVersion = 39
      time          = { from = "now-90d", to = "now" }
      templating    = { list = local.variables }
      panels = [
        for index, panel in spec.panels : {
          id         = index + 1
          title      = panel.title
          type       = panel.type
          datasource = local.ds
          gridPos    = { x = panel.grid[0], y = panel.grid[1], w = panel.grid[2], h = panel.grid[3] }
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
    }
  }
}
```

- [ ] **Step 4: Call the module from the layer**

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

- [ ] **Step 5: Turn it on in prod**

In `terraform/environments/prod/cluster-services/terragrunt.hcl`, inside `inputs`:

```hcl
  # LEX-749: fleet metrics in Grafana, in its own organization. Every login listed
  # must have signed in to Grafana (through Keycloak) once before the apply.
  fleet_metrics_enabled         = true
  fleet_metrics_grafana_admins  = []
  fleet_metrics_grafana_viewers = []
```

Before the apply, ask the user for the two lists: the Grafana logins (their Keycloak e-mail addresses) of the LEX-749 assignee as admin, and of the colleagues who may view. Enter them in this private repository only; they never go into the public lex-app repository.

- [ ] **Step 6: Validate the module and the layer**

Run: `cd terraform/modules/fleet-metrics-grafana && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform fmt -check`
Expected: valid; nothing to format.

Run: `cd terraform/layers/cluster-services && terraform init -backend=false -input=false >/dev/null && terraform validate`
Expected: valid.

Render one dashboard to check the JSON Grafana will receive:

Run: `cd terraform/modules/fleet-metrics-grafana && echo 'jsonencode(local.dashboards.collection)' | terraform console -var db_host=h:5432 -var db_name=d -var db_user=u -var db_password=p | head -c 600`
Expected: a JSON string starting `{"editable":false,"panels":[` with `"rawSql"` fields in which `${local.…}` has been replaced by SQL and `$__unixEpochFrom()`, `$stage` remain literal.

- [ ] **Step 7: Commit**

```bash
git add terraform/modules/fleet-metrics-grafana terraform/layers/cluster-services terraform/environments/prod/cluster-services/terragrunt.hcl
git commit -m "feat(grafana): fleet-metrics dashboards in an organization only the team can open"
```

---

### Task 6: Roll out, confirm it records, and schedule the check that matters

Every step here changes a real environment. Get the user's go-ahead before each apply, and before the first production run.

- [ ] **Step 1: Open the PR into `develop` and let CI deploy the test environment**

```bash
git push -u origin feat/fleet-metrics
gh pr create --base develop --title "feat(fleet-metrics): the nightly collector, its heartbeat and its dashboards" --body "$(cat <<'EOF'
Fleet metrics phase 1, the infrastructure half (LEX-749 / LEX-751). Design: lex-app `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md`.

- CronJob `collect-fleet-metrics` (02:30 UTC), on in prod and test via their values files.
- A second Better Stack heartbeat; its URL reaches the controller through Secret Manager and `app-env-secret`, like `KEYCLOAK_DB_PASSWORD`.
- cluster-services: a Grafana login role with no privileges of its own (the collector grants SELECT on its tables), a separate Grafana organization, a PostgreSQL datasource and three dashboards.

Needs the collector (lex-instance-controller-backend `feat/fleet-metrics-collector`) in the backend image first.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Merging publishes the chart and deploys the test environment; the controller backend image must already contain the collector, or the first run fails with an unknown command.

- [ ] **Step 2: Apply cluster-services for the test cluster (by hand)**

Only if you want the dashboards in the test Grafana too: set `fleet_metrics_enabled = true` in `terraform/environments/devops/cluster-services/terragrunt.hcl` with `db_name` pointing at the test controller database, then from that directory run `terragrunt plan` and, after review, `terragrunt apply`. Better Stack is off in the test cluster, so no heartbeat exists there and the collector logs `heartbeat skipped`.

- [ ] **Step 3: Run the collector once in test, by hand, and look**

```bash
kubectl config current-context   # must name lex-test-343008
kubectl -n default create job fleet-metrics-now --from=cronjob/collect-fleet-metrics
kubectl -n default logs -f job/fleet-metrics-now
```

The test controller environment and its instances run in the `default` namespace of the test cluster, as prod's do in the prod cluster.

Expected: `Fleet metrics run N: ok` (or `degraded` with named sources). Check in the test controller database: `SELECT day, count(*), count(*) FILTER (WHERE db_status = 'ok') FROM fleet_metrics_instanceday GROUP BY 1 ORDER BY 1 DESC LIMIT 5;`

- [ ] **Step 4: Production**

1. Apply prod cluster-services by hand (heartbeat, secret, reader role, organization, datasource, dashboards).
2. Run the prod deployment workflow (`gcp-ic-prod-deployment.yaml`) for the platform layer with the new chart version.
3. Run the collector once by hand as in Step 3 (namespace `default`, context `superb-blend-305320`).
4. Backfill the months before the 35-day catch-up window, one month per invocation, inside the controller backend:

   ```bash
   kubectl -n default exec statefulset/lex-instance-controller-backend -- \
     python manage.py collect_fleet_metrics --from 2026-01-01 --to 2026-01-31 --no-heartbeat
   ```

   Repeat for each month up to the day before the catch-up window starts; the nightly run covers the last 35 days by itself. Prometheus-derived values exist only as far back as its retention (180 days); older days are stored with that source `skipped`.

- [ ] **Step 5: The go-live checks (spec §8.3)**

- The next scheduled run's `CollectionRun` is `ok` and Better Stack shows the heartbeat received.
- Every expected production instance has yesterday's row; at least 95% have the database and Prometheus sources `ok`; excluded instances are listed with their reason.
- Spot check three instances (a busy one, a quiet one, an autoscaled one): recompute `writes`, `writers_28d`, `calc_runs` with SQL on the instance database and `api_requests` with PromQL; database counts match exactly, Prometheus ones within 1%.
- Boundary check on one busy instance: writes from 23:00Z to 01:00Z across one midnight, split by hand, match the two days.
- K3 validation: on two instances, a week of `calc_runs` against the number of distinct runs in `CalculationLog`; more than 5% apart makes K3 a phase-2 recorder hook.

- [ ] **Step 6: Put the week-later check in the calendar**

Ask the user to approve creating a Linear issue under LEX-751, assigned to the LEX-749 assignee, due seven days after go-live, titled "Fleet metrics: a week of data, checked", with this checklist:

- seven consecutive days of rows for every expected instance, no gaps;
- every failure heartbeat explained and healed by the catch-up;
- the spot check repeated for a day in the middle of the week;
- where the recorder is enabled, the Collection dashboard's invariant table is empty.
