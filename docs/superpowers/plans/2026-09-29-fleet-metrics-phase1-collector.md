# Fleet metrics phase 1 — the collector (Instance Controller) Implementation Plan

> **Superseded in part (2026-10-08).** The collector became the standalone component
> **lex-analytics**, with its own repository and database. It reads instance databases
> through the read-only role `lex_analytics_reader` and gets inventory, versions and
> operations from the Instance Controller's endpoint (contract:
> `lex-analytics/docs/contract-instance-controller.md`). R1 is implemented there and in the
> controller's `fleet_analytics` app. This plan stays the reference for the R2–R4 logic, the
> metric definitions and the tests, adapted to that layout. See spec §5.1 and §5.4.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Once a day, the Instance Controller writes one row per instance per UTC day into its own database: the instance's dimensions, a status per source, and the metric values of the KPI register (spec §4) — and pings a Better Stack heartbeat only when those rows were actually written. **Release 1 is K1 as active writers, K3 and K7**, collected from each instance database (aggregates only), the controller's own tables and the in-cluster health service, with **no Prometheus at all**. Release 2 adds Prometheus (K5, K6 with startup duration), release 3 the recorder's tables (K2, exact K1), release 4 K9.

**Architecture:** A new Django app `fleet_metrics` in lex-instance-controller-backend. Pure source modules each return values or raise: in R1 the instance database, the controller, and the version (probed only when `Kubectl.get_pod_readiness` reports the backend ready); from R2 Prometheus and backend starts. `collect_instance_day` combines them and stores the result under rules that never destroy data a re-run cannot reproduce. `run_collection` chooses the days (yesterday plus a 35-day catch-up), runs only the sources this environment has turned on (`FLEET_METRICS_SOURCES`), and decides the heartbeat over those. `manage.py collect_fleet_metrics` is what the CronJob runs. The collector stores counts and durations only; thresholds live in an append-only `Threshold` table that the dashboards apply.

**Tech Stack:** Django 5.2, psycopg2, requests, packaging, PostgreSQL 16 for tests, pytest with `--strict-markers`.

**Spec:** `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md` in lex-app, revision 2 (§4, §5.2, §5.3.2, §5.4, §5.5, §5.8, §6, §7, §8)

## Releases

The spec delivers fleet metrics in four releases (§4.1, §8.1). Each release is its own pull request on `main` (Task 14). **R1 is shipped and accepted — the one-week check of spec §8.1, stage R1.4 — before any R2 code is merged.** A release's code ships inert: an environment turns its source on by adding it to `FLEET_METRICS_SOURCES` (Task 12), and until then that source is recorded as `skipped`.

| Release | KPIs | Tasks, in order | `FLEET_METRICS_SOURCES` |
|---|---|---|---|
| **R1** | K1 as active writers, K3, K7. No Prometheus | 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14 (Step 1) | `controller,db,version` (the default) |
| **R2** | K5, K6 with startup duration | 1, 8, 15, 16, 14 (Step 2) | adds `prometheus` |
| **R3** | K2, exact K1, and `editors_1d` for the recorder-silence check | 17, 18, 14 (Step 3) | adds `recorder` |
| **R4** | K9 | 19, 14 (Step 4) | unchanged: K9 rides on `prometheus` |

Task numbers are those of the first revision, so the other plans' references stay valid; the document is ordered by release. Task 1 is read-only and off R1's path: Infra may run it while R1 is built (stage R2.0). Not built here: the KPI rejected in spec §4.3, and K8, which its owners build (§4.2 K8, §8.4); `DeployEvent` is their empty slot, and the collector never writes it.

## Global Constraints

- Repository `lex-instance-controller-backend`; base `origin/main`; one branch per release: `feat/fleet-metrics-collector` (R1), `feat/fleet-metrics-prometheus` (R2), `feat/fleet-metrics-recorder-reads` (R3), `feat/fleet-metrics-k9` (R4). Work in a worktree under `.claude/worktrees/` (add `.claude/` to `.git/info/exclude` first). The repository has **no git identity**: set `GIT_NAME` and `GIT_EMAIL` from the lex checkout first (`GIT_NAME=$(git -C <lex checkout> config user.name)`, likewise `user.email`) and commit with `git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit …`, or the commit fails and a later `push -u` creates an empty remote branch.
- Python environment: `uv venv venv --python 3.12 && uv pip install -r requirements.txt` in the worktree (`*venv/` is ignored). Test database: `just test-db-up` (docker `postgres:16` on port 55432).
- Test command: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test <paths> -q`. `pytest.ini` has `--strict-markers`; use only `unit` and `integration`. The `origin/main` baseline has 22 failures under `instance_api/tests`; compare against it, never against zero.
- **Time:** the controller runs `USE_TZ=False` with naive Berlin time. In `fleet_metrics`, every day boundary is an aware UTC `datetime` built by `fleet_metrics.days`; it reaches Postgres as a parameter. Never `django.utils.timezone.now()` for day logic, never a naive Berlin boundary, never `date_trunc` or `::date` on a timestamp in SQL. Instance Controller timestamps are compared in SQL against aware UTC parameters, or read as `… AT TIME ZONE 'UTC'` and made aware in Python; never through the naive Berlin values the ORM returns (spec §6).
- **Privacy:** instance-database queries return counts and percentiles only (and, from R3, area and channel names). No author, user id, record id or payload value is ever returned to Python (spec §5.2). API keys and the system are excluded from K1's writers, and no count per API key is kept (spec §4.3).
- **R1 queries no Prometheus.** `fleet_metrics/sources/prometheus.py` is created in Task 8 (R2); nothing in Tasks 2–13 imports it.
- **Never wake an instance:** no request to an instance's public URL. The only HTTP call to an instance goes to `http://lex-backend-<rid>:7000/api/health`, only for the most recent day, and only when `Kubectl.get_pod_readiness` reports the backend pod ready (Task 10, spec §5.4.6).
- **kubectl from the CronJob:** `Kubectl.get_pod_readiness` runs `kubectl get pods` in `settings.k8s_namespace`, so the CronJob's service account (the one `reap-stale-operations` uses, spec §5.4.1) must be allowed to list pods there. The first run in the test environment (spec §8.1, R1.1) shows it: running instances get `version_source = health`.
- **Measurements, not judgements:** the collector stores counts and durations. No threshold, share or flag is computed here (spec §4.4, §5.2 rule 7).
- **Logging:** logger names under `fleet_metrics`. A failure for one instance is a WARNING; only a run that stored nothing logs at ERROR.
- `LexInstance`'s primary key is `instance_id` (use `.pk`, never `.id`).
- Settings follow the controller's convention: module-level lower-case names in `lex_instance_controller_backend/settings.py`, read as `from lex_instance_controller_backend import settings` and patched in tests with `patch.object(ic_settings, …)`.
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| Path | Responsibility | Release |
|---|---|---|
| `fleet_metrics/apps.py`, `fleet_metrics/__init__.py` | App config | R1 |
| `fleet_metrics/catalog.py` | Names shared by code, storage and dashboards; the delivered-sources setting | R1; R2 and R3 add names |
| `fleet_metrics/days.py` | UTC day windows | R1 |
| `fleet_metrics/models.py`, `fleet_metrics/migrations/0001_initial.py`, `0002_daily_view.py` | Storage (spec §5.5): every table, including `Threshold`, the R2 `StartEvent` and K8's `DeployEvent` slot | R1 |
| `fleet_metrics/admin.py` | `Threshold`: add, never change or delete | R1 |
| `fleet_metrics/grants.py` | SELECT for the Grafana reader role | R1 |
| `fleet_metrics/sources/id_range.py` | Primary-key binary search for time windows | R1 |
| `fleet_metrics/sources/instance_db.py` | Writers, calculation runs, the read-only connection (R1); recorder tables, `editors_1d`, the two silences (R3) | R1, R3 |
| `fleet_metrics/sources/controller.py` | Dimensions and the deploy check behind the version carry (R1); operations for start causes (R2) | R1, R2 |
| `fleet_metrics/sources/version.py` | Version without waking (readiness through `Kubectl`); age and releases behind from PyPI | R1 |
| `fleet_metrics/sources/prometheus.py` | K5 and K6 platform metrics (R2); K9's p95 (R4) | R2, R4 |
| `fleet_metrics/sources/starts.py` | Backend starts: phases, outcome, cause | R2 |
| `fleet_metrics/collector.py` | One instance-day, stored | R1; R2 and R3 extend |
| `fleet_metrics/run.py` | Which days and sources; the run; the heartbeat decision | R1; R2 extends |
| `fleet_metrics/heartbeat.py` | Verdict over the delivered sources; Better Stack ping | R1; R3 adds recorder silence |
| `fleet_metrics/management/commands/collect_fleet_metrics.py` | The CronJob's command | R1 |
| `fleet_metrics/tests/…` | Tests, one file per module; `lex_schema.py` holds lex-app's tables in the shape the collector reads | per task |
| `lex_instance_controller_backend/settings.py` | App registration and `fleet_metrics_*` settings (R1); the Prometheus settings (R2) | R1, R2 |
| `requirements.txt` | `packaging`, to order releases for K7 | R1 |
| `pytest.ini` | `testpaths` gains `fleet_metrics/tests` | R1 |

---

## Release 1 — K1 (active writers), K3, K7

Branch `feat/fleet-metrics-collector` from `origin/main`.

### Task 2: The app, its settings, its storage and the reader grants

**One migration for every table.** `StartEvent` (written from R2, Task 16) and `DeployEvent` (the slot K8's owners fill, spec §4.2 K8, §8.4) are created here, empty, together with the R3 columns of `InstanceDay` (`recorder`, `recorder_silent`, `ui_silent`). One migration and one grant list mean that neither R2, R3 nor K8 needs a schema change in this app; an empty table costs nothing.

**Files:**
- Create: `fleet_metrics/__init__.py` (empty), `fleet_metrics/apps.py`, `fleet_metrics/catalog.py`, `fleet_metrics/models.py`, `fleet_metrics/admin.py`, `fleet_metrics/grants.py`, `fleet_metrics/migrations/__init__.py` (empty), `fleet_metrics/migrations/0001_initial.py` (generated), `fleet_metrics/migrations/0002_daily_view.py`
- Create: `fleet_metrics/tests/__init__.py` (empty), `fleet_metrics/tests/factories.py`, `fleet_metrics/tests/test_models_and_grants.py`
- Modify: `lex_instance_controller_backend/settings.py` (INSTALLED_APPS; a settings block after `prometheus_host`), `pytest.ini` (`testpaths`)

**Interfaces:**
- Produces: models `CollectionRun`, `InstanceDay`, `MetricValue`, `Threshold` (with `ThresholdIsAppendOnly`, `THRESHOLD_COMPARATORS`, `validate_comparator`), `StartEvent`, `DeployEvent` (fields below); `ThresholdAdmin`; view `fleet_metrics_daily`; `ensure_reader_grants(using="default") -> bool`; `catalog` constants and `catalog.parse_sources(raw: str) -> frozenset[str]`; settings `fleet_metrics_heartbeat_url`, `fleet_metrics_include_db_aliases`, `fleet_metrics_reader_role`, `fleet_metrics_catchup_days`, `fleet_metrics_sources`; test helpers `make_client(...)`, `make_instance(...)`.

- [ ] **Step 1: Register the tests**

In `pytest.ini`, change `testpaths = instance_api/tests` to:

```ini
testpaths = instance_api/tests fleet_metrics/tests
```

- [ ] **Step 2: Write the app config and the catalogue**

`fleet_metrics/apps.py`:

```python
from django.apps import AppConfig


class FleetMetricsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fleet_metrics"
    verbose_name = "Fleet metrics"
```

`fleet_metrics/catalog.py`:

```python
"""Names shared by the collector, its storage and the dashboards (spec §5.5).

The dashboards query these strings, so renaming one is a dashboard change too.
"""

# How a source fared for one instance on one day.
OK = "ok"
ERROR = "error"
EXCLUDED = "excluded"   # deliberately not collected (a separate database server)
SKIPPED = "skipped"     # not applicable (no database yet, a day outside Prometheus' data) or not delivered yet
DONE = frozenset({OK, EXCLUDED, SKIPPED})

# The sources, by the release that delivers them (spec §5.4.2). FLEET_METRICS_SOURCES names the
# ones an environment collects; a source it does not name is recorded as SKIPPED.
SOURCE_CONTROLLER = "controller"
SOURCE_DB = "db"
SOURCE_VERSION = "version"
SOURCE_PROMETHEUS = "prometheus"   # R2
SOURCE_RECORDER = "recorder"       # R3: the recorder's tables, read through the database source

#: The sources this build can collect. Task 16 (R2) adds SOURCE_PROMETHEUS, Task 18 (R3) SOURCE_RECORDER.
IMPLEMENTED_SOURCES = frozenset({SOURCE_CONTROLLER, SOURCE_DB, SOURCE_VERSION})

#: The InstanceDay column holding each judged source's status. The version has none (it always
#: ends in a value or ``unknown``); the recorder is judged by its silence (spec §7).
STATUS_FIELDS = {
    SOURCE_CONTROLLER: "controller_status",
    SOURCE_DB: "db_status",
    SOURCE_PROMETHEUS: "prometheus_status",
}


def parse_sources(raw: str) -> frozenset[str]:
    """The delivered sources named by ``FLEET_METRICS_SOURCES``.

    A name this build does not collect is a configuration error and raises: a source turned
    on before its code is deployed must not look delivered.
    """
    sources = frozenset(name.strip() for name in raw.split(",") if name.strip())
    if not sources:
        raise ValueError("FLEET_METRICS_SOURCES names no source")
    unknown = sources - IMPLEMENTED_SOURCES
    if unknown:
        raise ValueError(f"FLEET_METRICS_SOURCES names sources this build does not collect: {sorted(unknown)}")
    return sources


# The recorder's coverage of an instance on a day (spec §5.3.2), from R3.
# Before R3 the column holds SKIPPED: the recorder's tables are not read.
RECORDER_ABSENT = "absent"      # no recorder tables, or no version row by the end of the day
RECORDER_ENABLED = "enabled"
RECORDER_DISABLED = "disabled"  # LEX_USAGE_METRICS=false

# Where the lex-app version came from (spec §5.4.6).
VERSION_RUNTIME_TABLE = "runtime_table"   # R3
VERSION_HEALTH = "health"
VERSION_CARRIED = "carried"
VERSION_UNKNOWN = "unknown"

#: Operations that deploy an instance's infrastructure and release (spec §4.2 K6, cause
#: ``deploy``). One of them finishing on a day stops the version being carried into it (§5.4.6).
DEPLOY_OPERATION_TYPES = ("CREATE", "RECOVER", "RECONFIGURE", "RELEASE", "UPDATE_INFRA", "CHANGE_CLIENT")

# lex-app CalculationModel states that end a run (K3).
TERMINAL_CALCULATION_STATES = ("SUCCESS", "ERROR", "ABORTED", "CANCELLED")
```

- [ ] **Step 3: Add the settings and register the app**

In `lex_instance_controller_backend/settings.py`, after `prometheus_host = os.getenv("PROMETHEUS_HOST")`:

```python
# Fleet metrics (LEX-749): the daily per-instance snapshot written by
# `manage.py collect_fleet_metrics` (CronJob collect-fleet-metrics). See fleet_metrics/.
fleet_metrics_heartbeat_url = os.getenv("FLEET_METRICS_HEARTBEAT_URL", "")
fleet_metrics_include_db_aliases = os.getenv("FLEET_METRICS_INCLUDE_DB_ALIASES", "false")
fleet_metrics_reader_role = os.getenv("FLEET_METRICS_READER_ROLE", "fleet_metrics_reader")
fleet_metrics_catchup_days = int(os.getenv("FLEET_METRICS_CATCHUP_DAYS", "35"))
# The sources this environment collects. Release 1: controller,db,version. Release 2 adds
# prometheus, release 3 recorder. A source not listed is recorded as skipped.
fleet_metrics_sources = os.getenv("FLEET_METRICS_SOURCES", "controller,db,version")
```

In `INSTALLED_APPS`, after `'automation.apps.AutomationConfig',` add `'fleet_metrics.apps.FleetMetricsConfig',`.

- [ ] **Step 4: Write the test helpers**

`fleet_metrics/tests/factories.py`:

```python
"""Rows the fleet-metrics tests need, built the way the controller's own tests build them."""

from __future__ import annotations

from instance_api.models import LexInstance
from instance_api.models.config_metadata import ClientConfigMetadata, InstanceConfigMetadata


def make_client(name: str = "acme") -> ClientConfigMetadata:
    row = ClientConfigMetadata(client_id=f"LEX-{name}", internal_client_id=f"internal-{name}", client_name=name)
    row.save(compute_config_variables=False)
    return row


def make_instance(
    name: str = "books",
    *,
    rid: str = "books-prod-1",
    status: str = LexInstance.RUNNING,
    instance_type: str = LexInstance.TYPE_PROD,
    client: ClientConfigMetadata | None = None,
    database_name: str = "default_books_prod_1",
    db_server_alias: str = "",
    autoscaling: bool = True,
    url: str = "https://books.example.test",
    api_key: str = "raw-api-key",
) -> LexInstance:
    config = InstanceConfigMetadata(
        url=url, database_name=database_name, database_username="user1234abcd",
        db_server_alias=db_server_alias, autoscaling=autoscaling, api_key=api_key,
    )
    config.save(compute_config_variables=False)
    return LexInstance.objects.create(
        instance_name=name, resource_identifier=rid, type=instance_type, status=status,
        config_metadata=config, client_config_metadata=client or make_client(),
    )
```

If `from instance_api.models import LexInstance` fails, import it from `instance_api.models.models` (check `instance_api/models/__init__.py`).

- [ ] **Step 5: Write the failing storage tests**

`fleet_metrics/tests/test_models_and_grants.py`:

```python
"""Storage: one row per instance per UTC day, append-only thresholds, and a reader that sees only this app."""

from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase

from fleet_metrics import catalog
from fleet_metrics.admin import ThresholdAdmin
from fleet_metrics.grants import ensure_reader_grants
from fleet_metrics.models import InstanceDay, MetricValue, Threshold, ThresholdIsAppendOnly
from fleet_metrics.tests.factories import make_instance
from lex_instance_controller_backend import settings as ic_settings

pytestmark = pytest.mark.integration


def _day(instance, day=date(2026, 10, 1), **extra):
    fields = dict(
        instance=instance, instance_pk=instance.pk, day=day, instance_name=instance.instance_name,
        resource_identifier=instance.resource_identifier, instance_type=instance.type,
        release_stage=instance.release_stage, instance_status=instance.status,
        db_status=catalog.OK, controller_status=catalog.OK,
    )
    fields.update(extra)
    return InstanceDay.objects.create(**fields)


class InstanceDayTests(TestCase):
    def test_one_row_per_instance_and_day(self):
        instance = make_instance()
        _day(instance)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _day(instance)

    def test_history_survives_the_instance_being_purged(self):
        instance = make_instance()
        row = _day(instance)
        MetricValue.objects.create(instance_day=row, metric="calc_runs", dimension="success", value=3, source=catalog.SOURCE_DB)
        instance_pk = instance.pk
        instance.delete()

        row.refresh_from_db()
        self.assertIsNone(row.instance)
        self.assertEqual(row.instance_pk, instance_pk)
        self.assertEqual(row.values.get().value, 3)

    def test_the_daily_view_joins_dimensions_and_values(self):
        row = _day(make_instance(), client_name="acme")
        MetricValue.objects.create(instance_day=row, metric="calc_runs", dimension="success", value=3, source=catalog.SOURCE_DB)
        with connection.cursor() as cursor:
            cursor.execute("SELECT day, client_name, metric, dimension, value FROM fleet_metrics_daily")
            self.assertEqual(cursor.fetchall(), [(date(2026, 10, 1), "acme", "calc_runs", "success", 3.0)])


class ThresholdTests(TestCase):
    """Spec §4.4: a change is a new row; rows are never edited or deleted."""

    def _threshold(self, **extra):
        # K3's provisional flag: failures above the instance's own 90-day p95 (spec §4.4).
        fields = dict(
            kpi="K3", metric="calc_runs", kind=Threshold.KIND_OPERATIONAL, scope=Threshold.SCOPE_INSTANCE,
            comparator=">quantile_90d", value=0.95, unit="quantile", min_sample=5, valid_from=date(2026, 10, 27),
            status=Threshold.STATUS_PROVISIONAL, reason="R1 baseline review", linear_issue="LEX-989",
        )
        fields.update(extra)
        return Threshold.objects.create(**fields)

    def test_a_change_is_a_new_row(self):
        self._threshold()
        self._threshold(value=0.9, valid_from=date(2026, 11, 2), status=Threshold.STATUS_AGREED)
        self.assertEqual(list(Threshold.objects.order_by("valid_from").values_list("value", flat=True)), [0.95, 0.9])

    def test_saving_an_existing_row_raises(self):
        row = self._threshold()
        row.value = 0.5
        with self.assertRaises(ThresholdIsAppendOnly):
            row.save()
        row.refresh_from_db()
        self.assertEqual(row.value, 0.95)

    def test_deleting_a_row_raises(self):
        row = self._threshold()
        with self.assertRaises(ThresholdIsAppendOnly):
            row.delete()
        self.assertTrue(Threshold.objects.filter(pk=row.pk).exists())

    def test_kind_scope_comparator_and_status_are_checked(self):
        self._threshold(comparator=">", value=0.0, unit="count")
        with self.assertRaises(ValidationError):
            self._threshold(kind="alert")
        with self.assertRaises(ValidationError):
            self._threshold(comparator="!=")
        with self.assertRaises(ValidationError):
            self._threshold(comparator=">quantile_30d")

    def test_the_admin_never_changes_or_deletes_one(self):
        row = self._threshold()
        model_admin = ThresholdAdmin(Threshold, AdminSite())
        request = RequestFactory().get("/admin/")
        self.assertFalse(model_admin.has_change_permission(request, row))
        self.assertFalse(model_admin.has_delete_permission(request, row))


class ReaderGrantTests(TestCase):
    ROLE = "fleet_metrics_test_reader"

    def test_no_role_no_grants(self):
        with patch.object(ic_settings, "fleet_metrics_reader_role", "fleet_metrics_role_that_does_not_exist"):
            self.assertFalse(ensure_reader_grants())

    def test_the_reader_sees_the_fleet_tables_and_nothing_else(self):
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE ROLE "{self.ROLE}"')
        with patch.object(ic_settings, "fleet_metrics_reader_role", self.ROLE):
            self.assertTrue(ensure_reader_grants())
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT has_table_privilege(%s, 'fleet_metrics_instanceday', 'SELECT'), "
                "has_table_privilege(%s, 'fleet_metrics_daily', 'SELECT'), "
                "has_table_privilege(%s, 'fleet_metrics_threshold', 'SELECT'), "
                "has_table_privilege(%s, 'instance_api_lexinstance', 'SELECT')",
                [self.ROLE, self.ROLE, self.ROLE, self.ROLE],
            )
            self.assertEqual(cursor.fetchone(), (True, True, True, False))
```

- [ ] **Step 6: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_models_and_grants.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'fleet_metrics.models'` (or `admin`, `grants`).

- [ ] **Step 7: Write the models**

`fleet_metrics/models.py`:

```python
"""Fleet metrics storage (spec §5.5): one row per instance per UTC day.

Days are UTC calendar days, stored as dates. The timestamps (run start and end, collection
time, a threshold's recording time) follow the controller's convention of naive Berlin time;
nothing derives a day from them.

Every table is created by the first migration, including the ones filled later: ``StartEvent``
from R2, and ``DeployEvent``, the slot K8's owners fill (spec §4.2 K8, §8.4).
"""

from django.core.exceptions import ValidationError
from django.db import models

from fleet_metrics import catalog


class CollectionRun(models.Model):
    """One execution of ``collect_fleet_metrics``."""

    TRIGGER_CRON = "cron"
    TRIGGER_MANUAL = "manual"

    STATUS_RUNNING = "running"
    STATUS_OK = "ok"
    STATUS_DEGRADED = "degraded"
    STATUS_FAILED = "failed"

    HEARTBEAT_SENT = "sent"
    HEARTBEAT_FAILURE_SENT = "failure-sent"
    HEARTBEAT_SKIPPED = "skipped"
    HEARTBEAT_ERROR = "error"

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    trigger = models.CharField(max_length=20)
    status = models.CharField(max_length=20, default=STATUS_RUNNING)
    first_day = models.DateField(null=True, blank=True)
    last_day = models.DateField(null=True, blank=True)
    instances_expected = models.IntegerField(default=0)
    instance_days_written = models.IntegerField(default=0)
    instance_days_failed = models.IntegerField(default=0)
    heartbeat = models.CharField(max_length=20, blank=True, default="")
    summary = models.JSONField(default=dict, blank=True)


class InstanceDay(models.Model):
    """One instance on one UTC day: its dimensions, and how each source fared."""

    instance = models.ForeignKey(
        "instance_api.LexInstance", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="fleet_metric_days",
    )
    instance_pk = models.IntegerField()
    day = models.DateField()

    instance_name = models.CharField(max_length=200)
    resource_identifier = models.CharField(max_length=200)
    instance_type = models.CharField(max_length=20)
    release_stage = models.CharField(max_length=20)
    instance_status = models.CharField(max_length=50)
    client_id = models.CharField(max_length=200, blank=True, default="")
    client_name = models.CharField(max_length=200, blank=True, default="")
    autoscaling = models.BooleanField(null=True)

    lex_app_version = models.CharField(max_length=50, blank=True, default="")
    version_source = models.CharField(max_length=20, default=catalog.VERSION_UNKNOWN)
    recorder = models.CharField(max_length=20, default=catalog.SKIPPED)   # coverage, from R3
    recorder_silent = models.BooleanField(default=False)                  # R3; fails the heartbeat
    ui_silent = models.BooleanField(default=False)                        # R3; shown, never alarmed

    db_status = models.CharField(max_length=20)
    prometheus_status = models.CharField(max_length=20, default=catalog.SKIPPED)   # from R2
    controller_status = models.CharField(max_length=20)
    errors = models.JSONField(default=dict, blank=True)

    run = models.ForeignKey(
        CollectionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="instance_days",
    )
    collected_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["instance_pk", "day"], name="fleet_metrics_instance_day_unique"),
        ]
        indexes = [models.Index(fields=["day"], name="fleet_metrics_day_idx")]


class MetricValue(models.Model):
    """One value of one metric (and dimension) for one instance-day."""

    instance_day = models.ForeignKey(InstanceDay, on_delete=models.CASCADE, related_name="values")
    metric = models.CharField(max_length=64)
    dimension = models.CharField(max_length=64, blank=True, default="")
    value = models.FloatField()
    source = models.CharField(max_length=20)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["instance_day", "metric", "dimension"], name="fleet_metrics_value_unique",
            ),
        ]
        indexes = [models.Index(fields=["metric", "dimension"], name="fleet_metrics_metric_idx")]


class ThresholdIsAppendOnly(Exception):
    """A threshold changes by a new row with a later ``valid_from`` (spec §4.4), never by an edit."""


#: Plain comparators test the metric against ``value``. Baseline-relative ones test it against
#: the instance's own 90-day quantile, and ``value`` is then that quantile: ``>quantile_90d``
#: with 0.95 reads "above the instance's 90-day p95" (spec §4.4).
THRESHOLD_COMPARATORS = (">", ">=", "<", "<=", ">quantile_90d", "<quantile_90d")


def validate_comparator(comparator: str) -> None:
    if comparator not in THRESHOLD_COMPARATORS:
        raise ValidationError(f"comparator {comparator!r} is not one of {', '.join(THRESHOLD_COMPARATORS)}")


class Threshold(models.Model):
    """One version of one threshold (spec §4.4, §5.5). Append-only.

    The collector never reads this table. The dashboards draw, for each day, the row valid on
    that day: the latest ``valid_from`` on or before it, and among equals the latest
    ``recorded_at``. ``unit`` says what ``value`` is in (``count``, ``seconds``, ``share``, or
    ``quantile`` for a baseline-relative comparator). Rows are added through the admin at each
    baseline review (spec §8.1).
    """

    KIND_PLANNING = "planning"
    KIND_OPERATIONAL = "operational"
    KINDS = [(KIND_PLANNING, "planning rule"), (KIND_OPERATIONAL, "operational flag")]
    SCOPE_FLEET = "fleet"
    SCOPE_INSTANCE = "instance"
    SCOPES = [(SCOPE_FLEET, "fleet"), (SCOPE_INSTANCE, "instance")]
    STATUS_PROVISIONAL = "provisional"
    STATUS_AGREED = "agreed"
    STATUSES = [(STATUS_PROVISIONAL, "provisional"), (STATUS_AGREED, "agreed")]

    kpi = models.CharField(max_length=8)                       # "K1" … "K9"
    metric = models.CharField(max_length=64)                   # a catalogue metric (spec §5.5)
    kind = models.CharField(max_length=20, choices=KINDS)
    scope = models.CharField(max_length=20, choices=SCOPES)
    comparator = models.CharField(max_length=20, validators=[validate_comparator])
    value = models.FloatField()
    unit = models.CharField(max_length=40, blank=True, default="")
    min_sample = models.PositiveIntegerField(null=True, blank=True)   # below it, nothing is judged
    valid_from = models.DateField()                            # a UTC day
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_PROVISIONAL)
    reason = models.TextField()
    linear_issue = models.CharField(max_length=32, blank=True, default="")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["kpi", "metric", "valid_from"], name="fleet_metrics_threshold_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ThresholdIsAppendOnly(
                f"threshold {self.pk} is recorded; add a new row with a later valid_from instead"
            )
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ThresholdIsAppendOnly(f"threshold {self.pk} is recorded; thresholds are never deleted")


class StartEvent(models.Model):
    """One backend pod start (K6). Written by the Prometheus source from R2 (Task 16); empty before.

    ``cause`` is one of ``catalog.START_CAUSES`` and ``outcome`` ``ready`` or ``failed`` (both
    added in Task 15). Phases a failed start never reached are null, and so is its ``ready_at``.
    ``created_at`` and ``ready_at`` are written as aware UTC instants, which Django's PostgreSQL
    backend passes through unchanged (spec §6, rule 4). Through the ORM they read back as naive
    Berlin, like every controller timestamp, so dashboards and checks read them in SQL.
    """

    instance_pk = models.IntegerField()
    day = models.DateField()
    pod = models.CharField(max_length=253)
    cause = models.CharField(max_length=20)
    outcome = models.CharField(max_length=10)
    created_at = models.DateTimeField()
    ready_at = models.DateTimeField(null=True, blank=True)
    scheduling_seconds = models.FloatField(null=True, blank=True)
    preparation_seconds = models.FloatField(null=True, blank=True)
    container_start_seconds = models.FloatField(null=True, blank=True)
    application_ready_seconds = models.FloatField(null=True, blank=True)
    failure_reason = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["instance_pk", "pod"], name="fleet_metrics_start_pod_unique")]
        indexes = [models.Index(fields=["day"], name="fleet_metrics_start_day_idx")]


class DeployEvent(models.Model):
    """One deploy operation (K8). A slot for K8's owners (spec §4.2 K8, §8.4).

    The owners fill it, or point the dashboards at a view with the same columns. The collector
    never writes it. ``version`` and ``failure_reason`` stay null until the owners record them.
    """

    operation_id = models.IntegerField(unique=True)
    instance_pk = models.IntegerField()
    day = models.DateField()
    operation_type = models.CharField(max_length=50)
    outcome = models.CharField(max_length=20)
    duration_seconds = models.FloatField(null=True, blank=True)
    version = models.CharField(max_length=50, null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["day"], name="fleet_metrics_deploy_day_idx")]
```

`fleet_metrics/admin.py`:

```python
"""Thresholds are recorded through the admin and never edited or deleted (spec §4.4)."""

from django.contrib import admin

from fleet_metrics.models import Threshold


@admin.register(Threshold)
class ThresholdAdmin(admin.ModelAdmin):
    """Add-only, as ``ReleaseAuditAdmin`` is read-only: a change is a new row with a later ``valid_from``."""

    list_display = (
        "kpi", "metric", "kind", "scope", "comparator", "value", "unit", "min_sample",
        "valid_from", "status", "linear_issue", "recorded_at",
    )
    list_filter = ("kpi", "kind", "scope", "status")
    ordering = ("kpi", "metric", "-valid_from", "-recorded_at")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
```

The model's `save` and `delete` guard every other path. Queryset `update()` and `delete()` bypass them; nothing in this app issues them, and the reader role cannot write.

- [ ] **Step 8: Generate the first migration and write the view migration**

Run: `venv/bin/python manage.py makemigrations fleet_metrics --settings=lex_instance_controller_backend.settings_test`
Expected: `fleet_metrics/migrations/0001_initial.py` creating the six models, with a dependency on the latest `instance_api` migration (`0075_instanceconfigmetadata_reflex` at the time of writing).

`fleet_metrics/migrations/0002_daily_view.py`:

```python
"""The flat view the dashboards query: one row per instance, day, metric and dimension."""

from django.db import migrations

VIEW = """
CREATE OR REPLACE VIEW fleet_metrics_daily AS
SELECT d.day, d.instance_pk, d.instance_name, d.client_name, d.instance_type, d.release_stage,
       d.instance_status, d.autoscaling, d.lex_app_version, d.recorder,
       v.metric, v.dimension, v.value
FROM fleet_metrics_instanceday d
JOIN fleet_metrics_metricvalue v ON v.instance_day_id = d.id
"""


class Migration(migrations.Migration):

    dependencies = [("fleet_metrics", "0001_initial")]

    operations = [
        migrations.RunSQL(VIEW, reverse_sql="DROP VIEW IF EXISTS fleet_metrics_daily"),
    ]
```

- [ ] **Step 9: Write the grants**

`fleet_metrics/grants.py`:

```python
"""SELECT on this app's tables for Grafana's reader role (spec §5.6).

The role is created by Terraform (instance-controller-deployment, cluster-services layer)
with no table privileges; the controller owns these tables, so it grants them here. It runs
at the start of every collection: whichever of the two changes is deployed first, the next
run completes the pairing. The reader never gets anything else in this database, which also
holds instance credentials.
"""

from __future__ import annotations

import logging

from django.db import connections

from fleet_metrics.models import CollectionRun, DeployEvent, InstanceDay, MetricValue, StartEvent, Threshold
from lex_instance_controller_backend import settings

logger = logging.getLogger(__name__)

READER_RELATIONS = tuple(
    model._meta.db_table
    for model in (CollectionRun, InstanceDay, MetricValue, Threshold, StartEvent, DeployEvent)
) + ("fleet_metrics_daily",)


def ensure_reader_grants(using: str = "default") -> bool:
    """Grant the reader role SELECT on the fleet-metrics relations. False if it does not exist."""
    role = settings.fleet_metrics_reader_role
    if not role:
        return False
    connection = connections[using]
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
        if cursor.fetchone() is None:
            logger.info("fleet metrics: reader role %s does not exist; nothing granted", role)
            return False
        quoted_role = connection.ops.quote_name(role)
        for relation in READER_RELATIONS:
            cursor.execute(f"GRANT SELECT ON {connection.ops.quote_name(relation)} TO {quoted_role}")
    return True
```

- [ ] **Step 10: Run the tests and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_models_and_grants.py -q`
Expected: `10 passed`.

- [ ] **Step 11: Commit**

```bash
git add fleet_metrics pytest.ini lex_instance_controller_backend/settings.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): one row per instance per UTC day, append-only thresholds, readable by Grafana alone"
```

---

### Task 3: UTC day windows

**Files:**
- Create: `fleet_metrics/days.py`
- Test: `fleet_metrics/tests/test_days.py`

**Interfaces:**
- Produces: `DayWindow(day: date, start: datetime, end: datetime)` with `since(days: int) -> datetime`; `window_for(day: date) -> DayWindow`; `utc_now() -> datetime`; `yesterday(now: Optional[datetime] = None) -> date`; `day_range(first: date, last: date) -> list[date]`.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_days.py`:

```python
"""A day is a UTC calendar day, whatever Berlin's clock does (spec §6)."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.test import SimpleTestCase

from fleet_metrics.days import day_range, window_for, yesterday

pytestmark = pytest.mark.unit
BERLIN = ZoneInfo("Europe/Berlin")


class DayWindowTests(SimpleTestCase):
    def test_a_window_is_one_utc_day_even_when_berlin_has_25_hours(self):
        window = window_for(date(2026, 10, 25))
        self.assertEqual(window.start, datetime(2026, 10, 25, tzinfo=timezone.utc))
        self.assertEqual(window.end, datetime(2026, 10, 26, tzinfo=timezone.utc))
        self.assertEqual(window.end - window.start, timedelta(hours=24))

    def test_a_window_is_one_utc_day_when_berlin_has_23_hours(self):
        window = window_for(date(2027, 3, 28))
        self.assertEqual(window.end - window.start, timedelta(hours=24))

    def test_since_counts_back_from_the_end_of_the_day(self):
        window = window_for(date(2026, 10, 28))
        self.assertEqual(window.since(28), datetime(2026, 10, 1, tzinfo=timezone.utc))

    def test_yesterday_is_the_last_complete_utc_day(self):
        # 00:30 in Berlin on 26 Oct is still 25 Oct in UTC, so 25 Oct is not complete yet.
        self.assertEqual(yesterday(datetime(2026, 10, 26, 0, 30, tzinfo=BERLIN)), date(2026, 10, 24))
        self.assertEqual(yesterday(datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)), date(2026, 10, 25))

    def test_yesterday_refuses_a_naive_datetime(self):
        with self.assertRaises(ValueError):
            yesterday(datetime(2026, 10, 26, 2, 30))

    def test_day_range_is_inclusive(self):
        self.assertEqual(
            day_range(date(2026, 10, 30), date(2026, 11, 1)),
            [date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1)],
        )
        self.assertEqual(day_range(date(2026, 11, 2), date(2026, 11, 1)), [])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_days.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.days'`.

- [ ] **Step 3: Write the module**

`fleet_metrics/days.py`:

```python
"""UTC days, the only kind of day fleet metrics knows (spec §6).

The controller runs with ``USE_TZ=False`` and naive Berlin time, so
``django.utils.timezone.now()`` is Berlin wall-clock time here. Nothing in this app uses it
for day logic: every boundary is an aware UTC datetime built below, and it reaches Postgres
as a ``timestamptz`` parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class DayWindow:
    """One UTC calendar day: ``[start, end)``, both aware UTC."""

    day: date
    start: datetime
    end: datetime

    def since(self, days: int) -> datetime:
        """The aware UTC start of the ``days``-day window that ends with this day."""
        return self.end - timedelta(days=days)


def window_for(day: date) -> DayWindow:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return DayWindow(day=day, start=start, end=start + timedelta(days=1))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def yesterday(now: Optional[datetime] = None) -> date:
    """The last complete UTC day before ``now`` (an aware datetime; default: now)."""
    moment = now if now is not None else utc_now()
    if moment.tzinfo is None:
        raise ValueError("yesterday() needs an aware datetime; a naive one has no day")
    return moment.astimezone(timezone.utc).date() - timedelta(days=1)


def day_range(first: date, last: date) -> list[date]:
    """Every day from ``first`` to ``last``, inclusive; empty when ``last`` is earlier."""
    if last < first:
        return []
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_days.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/days.py fleet_metrics/tests/test_days.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): a day is a UTC day, built once and passed as a parameter"
```

---

### Task 4: Finding a time window through the primary key

**Files:**
- Create: `fleet_metrics/sources/__init__.py` (empty), `fleet_metrics/sources/id_range.py`
- Test: `fleet_metrics/tests/test_id_range.py`

**Interfaces:**
- Produces: `first_id_at_or_after(cursor, table: str, ts_column: str, moment: datetime) -> Optional[int]`; `MARGIN = timedelta(minutes=10)`; `IdRange(low: Optional[int], high: Optional[int])` with `empty -> bool`; `id_range(cursor, table, ts_column, start, end) -> IdRange`.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_id_range.py`:

```python
"""Windows of an unindexed timestamp, found by binary search over the primary key (spec §5.4.4)."""

import math
from datetime import datetime, timedelta, timezone

import pytest
from django.db import connection
from django.test import TestCase

from fleet_metrics.sources.id_range import IdRange, MARGIN, first_id_at_or_after, id_range

pytestmark = pytest.mark.integration
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


class _Counting:
    """A cursor that counts its statements."""

    def __init__(self, cursor):
        self.cursor = cursor
        self.executions = 0

    def execute(self, sql, params=None):
        self.executions += 1
        return self.cursor.execute(sql, params)

    def fetchone(self):
        return self.cursor.fetchone()


class FirstIdTests(TestCase):
    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE fm_probe (id bigint PRIMARY KEY, ts timestamptz NOT NULL)")
            # ids with gaps: 10, 20, 30, ... 10000; ts one minute apart.
            cursor.execute(
                "INSERT INTO fm_probe (id, ts) "
                "SELECT n * 10, %s::timestamptz + (n - 1) * interval '1 minute' FROM generate_series(1, 1000) n",
                [T0],
            )

    def _first(self, moment, cursor=None):
        if cursor is not None:
            return first_id_at_or_after(cursor, "fm_probe", "ts", moment)
        with connection.cursor() as c:
            return first_id_at_or_after(c, "fm_probe", "ts", moment)

    def test_it_finds_the_first_row_at_or_after_a_moment(self):
        self.assertEqual(self._first(T0), 10)
        self.assertEqual(self._first(T0 + timedelta(minutes=5)), 60)
        self.assertEqual(self._first(T0 + timedelta(minutes=5, seconds=1)), 70)

    def test_before_everything_and_after_everything(self):
        self.assertEqual(self._first(T0 - timedelta(days=1)), 10)
        self.assertIsNone(self._first(T0 + timedelta(days=30)))

    def test_it_costs_a_logarithmic_number_of_lookups(self):
        with connection.cursor() as cursor:
            counting = _Counting(cursor)
            self._first(T0 + timedelta(minutes=500), counting)
        # one min/max query plus the probes over an id space of ~10000
        self.assertLessEqual(counting.executions, 1 + math.ceil(math.log2(10000)) + 1)

    def test_an_empty_table_has_no_rows_anywhere(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM fm_probe")
        self.assertIsNone(self._first(T0))

    def test_a_window_is_widened_by_the_margin(self):
        with connection.cursor() as cursor:
            window = id_range(cursor, "fm_probe", "ts", T0 + timedelta(minutes=100), T0 + timedelta(minutes=200))
        self.assertEqual(
            window,
            IdRange(low=10 * (100 - 10 + 1), high=10 * (200 + 10 + 1)),
            "low is the first row 10 minutes before the start; high the first row 10 minutes after the end",
        )
        self.assertEqual(MARGIN, timedelta(minutes=10))

    def test_a_window_with_nothing_after_its_start_is_empty(self):
        with connection.cursor() as cursor:
            window = id_range(cursor, "fm_probe", "ts", T0 + timedelta(days=30), T0 + timedelta(days=31))
        self.assertTrue(window.empty)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_id_range.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.sources.id_range'`.

- [ ] **Step 3: Write the module**

`fleet_metrics/sources/id_range.py`:

```python
"""The id range of a time window, found through the primary key (spec §5.4.4).

lex-app's audit tables have no index on their timestamps, can be large, and live on a
database server every customer shares. Their ids and insert timestamps rise together (the
timestamp is set immediately before the insert), so the first id at or after a moment can be
found by binary search, one primary-key lookup per probe, and every window query then runs
over an id range instead of scanning by date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

#: Widening at each end of a window; absorbs the millisecond id/timestamp inversions of
#: concurrent inserts.
MARGIN = timedelta(minutes=10)


def first_id_at_or_after(cursor, table: str, ts_column: str, moment: datetime) -> Optional[int]:
    """The smallest id whose ``ts_column`` is at or after ``moment``; None if there is none."""
    cursor.execute(f"SELECT min(id), max(id) FROM {table}")
    low, high = cursor.fetchone()
    if low is None:
        return None
    probe = f"SELECT id, {ts_column} FROM {table} WHERE id >= %s ORDER BY id LIMIT 1"
    answer = None
    while low <= high:
        middle = (low + high) // 2
        cursor.execute(probe, [middle])
        row = cursor.fetchone()
        if row is None:
            high = middle - 1
            continue
        found_id, found_ts = row
        if found_ts >= moment:
            answer = found_id
            high = middle - 1
        else:
            low = found_id + 1
    return answer


@dataclass(frozen=True)
class IdRange:
    """Rows with ``low <= id`` and, when ``high`` is set, ``id < high``."""

    low: Optional[int]
    high: Optional[int]

    @property
    def empty(self) -> bool:
        return self.low is None


def id_range(cursor, table: str, ts_column: str, start: datetime, end: datetime) -> IdRange:
    """The id range covering ``[start, end)``, widened by :data:`MARGIN` at both ends."""
    low = first_id_at_or_after(cursor, table, ts_column, start - MARGIN)
    if low is None:
        return IdRange(None, None)
    return IdRange(low, first_id_at_or_after(cursor, table, ts_column, end + MARGIN))
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_id_range.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/sources fleet_metrics/tests/test_id_range.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): find a day in an unindexed table through its primary key"
```

---

### Task 5: Active writers from the audit (K1)

Until an instance has the recorder (R3), K1 is **active writers**: `AuditLog` authors that are neither API keys nor the system, over 1, 7 and 28 days (spec §4.2 K1). The author classification exists only to exclude those two; nothing is counted per channel.

**Files:**
- Create: `fleet_metrics/sources/instance_db.py` (first part)
- Create: `fleet_metrics/tests/lex_schema.py`, `fleet_metrics/tests/test_instance_db_audit.py`

**Interfaces:**
- Consumes: `DayWindow`, `window_for` (Task 3); `id_range` (Task 4).
- Produces: `Value = tuple[str, str, float]`; constants `AUDIT`, `STATUS`, `API_KEYS`; `audit_metrics(cursor, window: DayWindow) -> list[Value]` returning `writers_1d`, `writers_7d`, `writers_28d`. Test helpers `create_lex_tables(cursor)`, `add_audit(cursor, **fields) -> int`.

- [ ] **Step 1: Write the lex-shaped fixture**

`fleet_metrics/tests/lex_schema.py`:

```python
"""lex-app's tables, in the shape the collector reads, for the controller's test database.

The columns match what lex-app's own contract test (cluster 6r) pins.
``rest_framework_api_key_apikey`` is not created: the controller has the same library
installed, so its own table serves. The recorder's tables join in R3 (Task 17).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from psycopg2.extras import Json

LEX_TABLES = """
CREATE TABLE audit_logging_auditlog (
    id bigserial PRIMARY KEY,
    date timestamptz NOT NULL,
    author varchar(255),
    resource varchar(255) NOT NULL,
    action varchar(10) NOT NULL,
    payload jsonb,
    calculation_id text,
    content_type_id integer,
    object_id integer
);
CREATE TABLE audit_logging_auditlogstatus (
    id bigserial PRIMARY KEY,
    audit_log_id bigint NOT NULL REFERENCES audit_logging_auditlog (id),
    status varchar(20) NOT NULL,
    error_traceback text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
"""


def create_lex_tables(cursor) -> None:
    cursor.execute(LEX_TABLES)


def add_audit(
    cursor,
    *,
    at: datetime,
    author: Optional[str],
    resource: str = "invoice",
    action: str = "update",
    payload: Optional[dict] = None,
    calculation_id: Optional[str] = None,
    status: str = "success",
    status_created: Optional[datetime] = None,
    status_updated: Optional[datetime] = None,
) -> int:
    """One audit row and its status row, as lex-app writes them."""
    cursor.execute(
        "INSERT INTO audit_logging_auditlog (date, author, resource, action, payload, calculation_id) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        [at, author, resource, action, Json(payload) if payload is not None else None, calculation_id],
    )
    audit_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO audit_logging_auditlogstatus (audit_log_id, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s)",
        [audit_id, status, status_created or at, status_updated or at],
    )
    return audit_id
```

- [ ] **Step 2: Write the failing tests**

`fleet_metrics/tests/test_instance_db_audit.py`:

```python
"""Active writers over 1/7/28 days (K1 in R1) — on 25 Oct, when summer time ends."""

from datetime import datetime, timezone

import pytest
from django.db import connection
from django.test import TestCase
from rest_framework_api_key.models import APIKey

from fleet_metrics.days import window_for
from fleet_metrics.sources.instance_db import audit_metrics
from fleet_metrics.tests.lex_schema import add_audit, create_lex_tables

pytestmark = pytest.mark.integration
DAY = window_for(datetime(2026, 10, 25).date())


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class AuditMetricsTests(TestCase):
    def setUp(self):
        APIKey.objects.create_key(name="Nightly import")
        with connection.cursor() as cursor:
            create_lex_tables(cursor)
            # Inserted in time order, as lex-app writes them: ids and dates rise together.
            # 01:30 in Berlin on 25 Oct, but 24 Oct in UTC: not today's writer.
            add_audit(cursor, at=utc(2026, 10, 24, 23, 30), author="Bo Diddley")
            add_audit(cursor, at=utc(2026, 10, 25, 8), author="Ada Lovelace")
            add_audit(cursor, at=utc(2026, 10, 25, 10), author="Ada Lovelace")
            # API keys and the framework are not people (spec §4.2 K1).
            add_audit(cursor, at=utc(2026, 10, 25, 12), author="Nightly import")
            add_audit(cursor, at=utc(2026, 10, 25, 13), author="system (initial_data_upload)")
            add_audit(cursor, at=utc(2026, 10, 25, 14), author="Technical User")
            add_audit(cursor, at=utc(2026, 10, 25, 14, 30), author=None)
            # A save that failed still shows a person at work.
            add_audit(cursor, at=utc(2026, 10, 25, 15), author="Cy Twombly", status="failure")
            # The first instant of the next UTC day.
            add_audit(cursor, at=utc(2026, 10, 26, 0, 0, 0), author="Di Fisher")

    def _collect(self, session_zone):
        with connection.cursor() as cursor:
            cursor.execute(f"SET TIME ZONE '{session_zone}'")
            values = audit_metrics(cursor, DAY)
        return {(metric, dimension): value for metric, dimension, value in values}

    def test_the_day_is_counted_in_utc_whatever_the_session_zone(self):
        for zone in ("UTC", "Europe/Berlin"):
            with self.subTest(session_zone=zone):
                values = self._collect(zone)
                self.assertEqual(values[("writers_1d", "")], 2.0, "Ada and Cy; Bo wrote on 24 Oct UTC")
                self.assertEqual(values[("writers_7d", "")], 3.0, "Bo is inside the 7-day window")
                self.assertEqual(values[("writers_28d", "")], 3.0)

    def test_api_keys_and_the_system_are_not_writers(self):
        values = self._collect("UTC")
        self.assertEqual(set(values), {("writers_1d", ""), ("writers_7d", ""), ("writers_28d", "")},
                         "writers only: nothing is counted per channel")
        self.assertEqual(values[("writers_1d", "")], 2.0,
                         "not 5: the API key, the system and Technical User are not people")

    def test_no_identity_comes_back(self):
        values = self._collect("UTC")
        for (metric, dimension), value in values.items():
            self.assertIsInstance(value, float)
            self.assertNotIn(dimension, {"Ada Lovelace", "Bo Diddley", "Cy Twombly", "Nightly import"})

    def test_the_day_summer_time_starts(self):
        # 28 Mar 2027: 00:30 in Berlin (CET) is 23:30 UTC on 27 Mar; 01:30 UTC is 03:30 CEST.
        with connection.cursor() as cursor:
            add_audit(cursor, at=utc(2027, 3, 27, 23, 30), author="Eve Arden")
            add_audit(cursor, at=utc(2027, 3, 28, 1, 30), author="Flo Kennedy")
            cursor.execute("SET TIME ZONE 'Europe/Berlin'")
            values = audit_metrics(cursor, window_for(datetime(2027, 3, 28).date()))
        values = {(m, d): v for m, d, v in values}
        self.assertEqual(values[("writers_1d", "")], 1.0, "Flo only; Eve wrote on 27 Mar UTC")

    def test_an_empty_audit_is_explicit_zeros(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_logging_auditlogstatus")
            cursor.execute("DELETE FROM audit_logging_auditlog")
        values = self._collect("UTC")
        self.assertEqual(values[("writers_1d", "")], 0.0)
        self.assertEqual(values[("writers_28d", "")], 0.0)
```

- [ ] **Step 3: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_audit.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.sources.instance_db'`.

- [ ] **Step 4: Write the first part of the module**

`fleet_metrics/sources/instance_db.py`:

```python
"""Aggregates from one instance's database (spec §5.4.3, §5.4.4).

Every query here returns counts or percentiles: never a person, a record or a payload
value. The SQL does not depend on the session time zone (every bound is a ``timestamptz``
parameter); :func:`instance_cursor` sets UTC anyway.
"""

from __future__ import annotations

from fleet_metrics.days import DayWindow
from fleet_metrics.sources.id_range import IdRange, id_range

Value = tuple[str, str, float]

AUDIT = "audit_logging_auditlog"
STATUS = "audit_logging_auditlogstatus"
API_KEYS = "rest_framework_api_key_apikey"

# How lex-app attributes an audit row (spec §2.3): a person's display string, an API key's
# name, or ``system…`` for the framework. K1's writers are the people (spec §4.2 K1); this
# classification exists only to exclude the other two. ``%%`` is a literal ``%`` under
# psycopg2's parameter style.
_IS_SYSTEM = "(a.author IS NULL OR a.author = '' OR a.author LIKE 'system%%')"
_IS_API_KEY = f"(a.author = 'Technical User' OR a.author IN (SELECT name FROM {API_KEYS}))"
_IS_PERSON = f"NOT {_IS_SYSTEM} AND NOT {_IS_API_KEY}"
_IN_RANGE = "a.id >= %(low)s AND (%(high)s::bigint IS NULL OR a.id < %(high)s::bigint)"

_WRITERS_SQL = f"""
SELECT
    count(DISTINCT a.author) FILTER (WHERE a.date >= %(start)s),
    count(DISTINCT a.author) FILTER (WHERE a.date >= %(since7)s),
    count(DISTINCT a.author)
FROM {AUDIT} a
WHERE {_IN_RANGE} AND a.date >= %(since)s AND a.date < %(end)s
  AND {_IS_PERSON}
"""


def _range_params(window_range: IdRange) -> dict:
    return {"low": window_range.low, "high": window_range.high}


def audit_metrics(cursor, window: DayWindow) -> list[Value]:
    """K1 in R1: distinct people who changed data in the 1, 7 and 28 days ending with the day."""
    since = window.since(28)
    window_range = id_range(cursor, AUDIT, "date", since, window.end)
    if window_range.empty:
        writers = (0, 0, 0)
    else:
        cursor.execute(_WRITERS_SQL, {
            **_range_params(window_range),
            "since": since, "since7": window.since(7), "start": window.start, "end": window.end,
        })
        writers = cursor.fetchone()
    writers_1d, writers_7d, writers_28d = writers
    return [
        ("writers_1d", "", float(writers_1d)),
        ("writers_7d", "", float(writers_7d)),
        ("writers_28d", "", float(writers_28d)),
    ]
```

- [ ] **Step 5: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_audit.py -q`
Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add fleet_metrics/sources/instance_db.py fleet_metrics/tests/lex_schema.py fleet_metrics/tests/test_instance_db_audit.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): active writers, counted in UTC inside the instance"
```

---

### Task 6: Calculation runs from the audit (K3)

**Files:**
- Modify: `fleet_metrics/sources/instance_db.py` (append)
- Test: `fleet_metrics/tests/test_instance_db_calculations.py`

**Interfaces:**
- Produces: `calculation_metrics(cursor, window: DayWindow) -> list[Value]` — `calc_runs` by outcome (always all four), `calc_seconds_p50`/`calc_seconds_p95` (only when a run succeeded).

The rule (spec §2.3, pinned by lex-app's 6.120–6.122): a run is the audit rows sharing `resource` and a real `calculation_id` (empty or `test_id` means the row is its own run); it is finished when one of its rows carries a terminal `is_calculated`; its outcome is the last terminal state; it started at its earliest status `created_at` and finished at its latest `updated_at`; it belongs to the UTC day it finished.

The collector stores counts by outcome only. K3's **failure share** is computed when the dashboards read: (error + aborted) / (success + error + aborted). Cancelled runs are a person's choice and are left out of both numerator and denominator (spec §4.2 K3).

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_instance_db_calculations.py`:

```python
"""Calculation runs: one run however many audit rows it left, bucketed by the day it finished."""

from datetime import datetime, timezone

import pytest
from django.db import connection
from django.test import TestCase

from fleet_metrics.days import window_for
from fleet_metrics.sources.instance_db import calculation_metrics
from fleet_metrics.tests.lex_schema import add_audit, create_lex_tables

pytestmark = pytest.mark.integration
DAY = window_for(datetime(2026, 10, 25).date())


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class CalculationMetricsTests(TestCase):
    def setUp(self):
        with connection.cursor() as c:
            create_lex_tables(c)
            # Inserted in time order, as lex-app writes them: ids and dates rise together.
            # C: started 24 Oct, finished 25 Oct: counts for 25 Oct, 5400 s.
            add_audit(c, at=utc(2026, 10, 24, 23), author="Bo", resource="forecast",
                      payload={"is_calculated": "SUCCESS"}, calculation_id="forecast_3_update_c",
                      status_updated=utc(2026, 10, 25, 0, 30))
            # A: the request row, then the terminal row; one run, 300 s.
            add_audit(c, at=utc(2026, 10, 25, 9), author="Ada", resource="forecast",
                      payload={"is_calculated": "IN_PROGRESS"}, calculation_id="forecast_1_update_a")
            add_audit(c, at=utc(2026, 10, 25, 9, 5), author="Ada", resource="forecast",
                      payload={"is_calculated": "SUCCESS"}, calculation_id="forecast_1_update_a")
            # B: one row reused for the terminal state; a failure.
            add_audit(c, at=utc(2026, 10, 25, 10), author="Ada", resource="forecast",
                      payload={"is_calculated": "ERROR"}, calculation_id="forecast_2_update_b",
                      status="failure", status_updated=utc(2026, 10, 25, 10, 2))
            # D: two rows without a real id are two runs, by the system.
            add_audit(c, at=utc(2026, 10, 25, 11), author="system", resource="forecast",
                      payload={"is_calculated": "SUCCESS"}, calculation_id="test_id")
            add_audit(c, at=utc(2026, 10, 25, 11), author="system", resource="forecast",
                      payload={"is_calculated": "SUCCESS"}, calculation_id=None)
            # E: a person cancelled it.
            add_audit(c, at=utc(2026, 10, 25, 12), author="Ada", resource="forecast",
                      payload={"is_calculated": "CANCELLED"}, calculation_id="forecast_5_update_e",
                      status="failure")
            # A plain edit of a calculation row is not a run.
            add_audit(c, at=utc(2026, 10, 25, 13), author="Cy", resource="forecast",
                      payload={"is_calculated": "NOT_CALCULATED"}, calculation_id="forecast_6_update_f")
            # Finished on 26 Oct: not today's.
            add_audit(c, at=utc(2026, 10, 26, 1), author="Cy", resource="forecast",
                      payload={"is_calculated": "SUCCESS"}, calculation_id="forecast_7_update_g")

    def _values(self):
        with connection.cursor() as c:
            c.execute("SET TIME ZONE 'Europe/Berlin'")
            return {(m, d): v for m, d, v in calculation_metrics(c, DAY)}

    def test_runs_by_outcome(self):
        values = self._values()
        self.assertEqual(values[("calc_runs", "success")], 4.0, "A, C and the two id-less rows of D")
        self.assertEqual(values[("calc_runs", "error")], 1.0)
        self.assertEqual(values[("calc_runs", "cancelled")], 1.0)
        self.assertEqual(values[("calc_runs", "aborted")], 0.0)

    def test_durations_of_successful_runs(self):
        values = self._values()
        # successful durations: A 300 s, C 5400 s, D 0 s and 0 s
        self.assertAlmostEqual(values[("calc_seconds_p50", "")], 150.0)
        self.assertAlmostEqual(values[("calc_seconds_p95", "")], 300 + 0.85 * (5400 - 300))

    def test_no_successful_run_means_no_percentile(self):
        with connection.cursor() as c:
            c.execute("DELETE FROM audit_logging_auditlogstatus")
            c.execute("DELETE FROM audit_logging_auditlog")
        values = self._values()
        self.assertEqual(values[("calc_runs", "success")], 0.0)
        self.assertNotIn(("calc_seconds_p95", ""), values)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_calculations.py -q`
Expected: `ImportError: cannot import name 'calculation_metrics'`.

- [ ] **Step 3: Append the calculation query**

Add to the imports at the top of `fleet_metrics/sources/instance_db.py`:

```python
from datetime import timedelta

from fleet_metrics import catalog
```

Append:

```python
_TERMINAL = ", ".join(f"'{state}'" for state in catalog.TERMINAL_CALCULATION_STATES)

_CALCULATIONS_SQL = f"""
WITH audit_rows AS (
    SELECT a.id,
           a.resource || ':' ||
               COALESCE(NULLIF(NULLIF(a.calculation_id, ''), 'test_id'), 'row-' || a.id::text) AS run_key,
           a.payload->>'is_calculated' AS state,
           s.created_at, s.updated_at
    FROM {AUDIT} a
    JOIN {STATUS} s ON s.audit_log_id = a.id
    WHERE {_IN_RANGE}
),
runs AS (
    SELECT (array_agg(state ORDER BY id DESC) FILTER (WHERE state IN ({_TERMINAL})))[1] AS outcome,
           min(created_at) AS started_at,
           max(updated_at) AS finished_at
    FROM audit_rows
    GROUP BY run_key
),
finished AS (
    SELECT outcome, extract(epoch FROM finished_at - started_at) AS seconds
    FROM runs
    WHERE outcome IS NOT NULL AND finished_at >= %(start)s AND finished_at < %(end)s
)
SELECT
    count(*) FILTER (WHERE outcome = 'SUCCESS'),
    count(*) FILTER (WHERE outcome = 'ERROR'),
    count(*) FILTER (WHERE outcome = 'ABORTED'),
    count(*) FILTER (WHERE outcome = 'CANCELLED'),
    percentile_cont(0.5)  WITHIN GROUP (ORDER BY seconds) FILTER (WHERE outcome = 'SUCCESS'),
    percentile_cont(0.95) WITHIN GROUP (ORDER BY seconds) FILTER (WHERE outcome = 'SUCCESS')
FROM finished
"""

#: A run can start up to this long before it finishes: workers stop at 24 hours (spec §5.4.4).
RUN_LOOKBACK = timedelta(days=2)


def calculation_metrics(cursor, window: DayWindow) -> list[Value]:
    """K3: runs that finished on the day, by outcome, and the durations of the successful ones.

    Counts only. The failure share is (error + aborted) / (success + error + aborted), computed
    by the dashboards; cancelled runs are in neither part (spec §4.2 K3).
    """
    window_range = id_range(cursor, AUDIT, "date", window.start - RUN_LOOKBACK, window.end)
    if window_range.empty:
        success = error = aborted = cancelled = 0
        p50 = p95 = None
    else:
        cursor.execute(
            _CALCULATIONS_SQL, {**_range_params(window_range), "start": window.start, "end": window.end},
        )
        success, error, aborted, cancelled, p50, p95 = cursor.fetchone()
    values = [
        ("calc_runs", "success", float(success)),
        ("calc_runs", "error", float(error)),
        ("calc_runs", "aborted", float(aborted)),
        ("calc_runs", "cancelled", float(cancelled)),
    ]
    if p50 is not None:
        values.append(("calc_seconds_p50", "", float(p50)))
    if p95 is not None:
        values.append(("calc_seconds_p95", "", float(p95)))
    return values
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_calculations.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/sources/instance_db.py fleet_metrics/tests/test_instance_db_calculations.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): count calculation runs, not audit rows, by the day they finished"
```

---

### Task 7: The whole instance, and the read-only connection

R1 reads the audit only. The recorder's tables (`lex_usage_daily`, `lex_runtime_versions`) and `editors_1d` are read from R3, in Task 17.

**Files:**
- Modify: `fleet_metrics/sources/instance_db.py` (append)
- Test: `fleet_metrics/tests/test_instance_db_connection.py`

**Interfaces:**
- Produces:
  - `InstanceDbResult(values: list[Value])` — Task 17 adds the recorder's fields, with defaults
  - `collect(cursor, window) -> InstanceDbResult` — K1's writers and K3
  - `database_host(config_metadata) -> str`; `instance_cursor(instance, *, connect=psycopg2.connect)` (context manager yielding a cursor)

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_instance_db_connection.py`:

```python
"""The whole instance in R1, and the read-only connection it is read through (spec §5.4.3)."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from django.db import connection
from django.test import SimpleTestCase, TestCase

from fleet_metrics.days import window_for
from fleet_metrics.sources import instance_db
from fleet_metrics.tests.lex_schema import add_audit, create_lex_tables
from lex_instance_controller_backend import settings as ic_settings

DAY = window_for(date(2026, 10, 25))


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.integration
class CollectTests(TestCase):
    def test_writers_and_calculations_from_one_database(self):
        with connection.cursor() as cursor:
            create_lex_tables(cursor)
            add_audit(cursor, at=utc(2026, 10, 25, 9), author="Ada Lovelace", resource="forecast",
                      payload={"is_calculated": "SUCCESS"}, calculation_id="forecast_1_update_a")
            result = instance_db.collect(cursor, DAY)
        values = {(m, d): v for m, d, v in result.values}
        self.assertEqual(values[("writers_1d", "")], 1.0)
        self.assertEqual(values[("calc_runs", "success")], 1.0)

    def test_r1_reads_no_recorder_table(self):
        with connection.cursor() as cursor:
            create_lex_tables(cursor)
            result = instance_db.collect(cursor, DAY)
        self.assertFalse({m for m, _, _ in result.values} & {"active_people_1d", "area_people_28d", "editors_1d"})


@pytest.mark.integration
class ReadOnlySessionTests(TestCase):
    def test_the_session_refuses_writes_and_runs_in_utc(self):
        db = connection.settings_dict

        def connect(**kwargs):
            # The instance's connection, pointed at the test database instead of Cloud SQL.
            return psycopg2.connect(**{**kwargs, "host": db["HOST"], "port": db["PORT"], "dbname": db["NAME"],
                                       "user": db["USER"], "password": db["PASSWORD"]})

        instance = SimpleNamespace(config_metadata=SimpleNamespace(
            db_server_alias="", database_name="unused", database_username="unused",
            get_database_password=lambda: "unused",
        ))
        with patch.object(ic_settings, "database_domain", "unused.svc"):
            with instance_db.instance_cursor(instance, connect=connect) as cursor:
                cursor.execute("SHOW TimeZone")
                self.assertEqual(cursor.fetchone()[0], "UTC")
                with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction):
                    cursor.execute("CREATE TABLE fm_must_not_exist (id int)")


@pytest.mark.unit
class ConnectionTests(SimpleTestCase):
    def _meta(self, alias=""):
        return SimpleNamespace(
            db_server_alias=alias, database_name="db_one", database_username="user1",
            get_database_password=lambda: "secret",
        )

    def test_the_direct_server_unless_an_alias_is_set(self):
        with patch.object(ic_settings, "database_domain", "direct.svc"), \
                patch.object(ic_settings, "alternate_db_servers", {"ALT": {"database_domain": "alt.svc"}}):
            self.assertEqual(instance_db.database_host(self._meta()), "direct.svc")
            self.assertEqual(instance_db.database_host(self._meta("ALT")), "alt.svc")
            with self.assertRaises(LookupError):
                instance_db.database_host(self._meta("MISSING"))

    def test_the_session_is_read_only_utc_and_bounded(self):
        connection_mock = MagicMock()
        cursor = connection_mock.cursor.return_value.__enter__.return_value
        connect = MagicMock(return_value=connection_mock)
        instance = SimpleNamespace(config_metadata=self._meta())
        with patch.object(ic_settings, "database_domain", "direct.svc"):
            with instance_db.instance_cursor(instance, connect=connect) as yielded:
                self.assertIs(yielded, cursor)

        kwargs = connect.call_args.kwargs
        self.assertEqual((kwargs["host"], kwargs["dbname"], kwargs["user"], kwargs["password"]),
                         ("direct.svc", "db_one", "user1", "secret"))
        connection_mock.set_session.assert_called_once_with(readonly=True, autocommit=False)
        statements = [c.args[0] for c in cursor.execute.call_args_list]
        self.assertEqual(statements, ["SET LOCAL statement_timeout = '60s'", "SET LOCAL TIME ZONE 'UTC'"])
        connection_mock.rollback.assert_called_once()
        connection_mock.close.assert_called_once()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_connection.py -q`
Expected: `AttributeError: module 'fleet_metrics.sources.instance_db' has no attribute 'collect'`.

- [ ] **Step 3: Append `collect` and the connection**

Add to the imports at the top of `fleet_metrics/sources/instance_db.py`:

```python
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg2

from lex_instance_controller_backend import settings
```

Append:

```python
@dataclass(frozen=True)
class InstanceDbResult:
    """What one instance database contributes to one day. R3 (Task 17) adds the recorder's fields."""

    values: list[Value]


def collect(cursor, window: DayWindow) -> InstanceDbResult:
    """Everything one instance database contributes to one day in R1: K1's writers and K3."""
    return InstanceDbResult(values=audit_metrics(cursor, window) + calculation_metrics(cursor, window))


def database_host(config_metadata) -> str:
    """The direct Cloud SQL service, or the server an alias names. Never the pooler."""
    alias = config_metadata.db_server_alias
    if not alias:
        return settings.database_domain
    server = settings.alternate_db_servers.get(alias)
    if not server:
        raise LookupError(f"database server alias {alias!r} is not configured")
    return server["database_domain"]


@contextmanager
def instance_cursor(instance, *, connect=psycopg2.connect):
    """A read-only, UTC, 60-second-bounded cursor on the instance's own database.

    It connects with the instance's own credentials — the ones the database export job
    already uses — and every transaction is READ ONLY.
    """
    meta = instance.config_metadata
    conn = connect(
        host=database_host(meta), port=5432, dbname=meta.database_name,
        user=meta.database_username, password=meta.get_database_password(),
        connect_timeout=10, application_name="fleet-metrics",
    )
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '60s'")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            yield cursor
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_connection.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/sources/instance_db.py fleet_metrics/tests/test_instance_db_connection.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): read the whole instance through a read-only UTC session"
```

---

### Task 9: What the controller knows: dimensions, and whether a deploy finished

The controller's own facts in R1 are an instance's dimensions and one yes/no: did a deploy-kind operation finish on the day? That answer stops the version being carried (Task 11, spec §5.4.6). Nothing is copied into `DeployEvent`: K8's owners fill it (spec §8.4).

**Files:**
- Create: `fleet_metrics/sources/controller.py`
- Test: `fleet_metrics/tests/test_controller.py`

**Interfaces:**
- Produces: `Snapshot` (frozen dataclass: `instance_name, resource_identifier, instance_type, release_stage, instance_status, client_id, client_name, autoscaling`); `snapshot(instance) -> Snapshot`; `deploy_finished(instance, window) -> bool`. Test helper `op(instance, operation_type, status, started, completed)`.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_controller.py`:

```python
"""What the controller knows, by UTC day: an operation that ends at 01:30 Berlin on 25 Oct ended on 24 Oct."""

from datetime import date, datetime

import pytest
from django.test import TestCase

from fleet_metrics import catalog
from fleet_metrics.days import window_for
from fleet_metrics.sources import controller
from fleet_metrics.tests.factories import make_client, make_instance
from instance_api.models.enums import OperationType
from instance_api.models.operation import InstanceOperation

pytestmark = pytest.mark.integration


def op(instance, operation_type, status, started, completed):
    """Timestamps are naive Berlin, exactly as the controller's ORM stores them (USE_TZ=False)."""
    return InstanceOperation.objects.create(
        instance=instance, operation_type=operation_type, status=status,
        started_at=started, completed_at=completed,
    )


class DeployFinishedTests(TestCase):
    def setUp(self):
        self.instance = make_instance()

    def test_the_utc_day_not_the_berlin_date(self):
        # 01:30 CEST on 25 Oct is 23:30 UTC on 24 Oct.
        op(self.instance, "RELEASE", "SUCCESS", datetime(2026, 10, 25, 1, 0), datetime(2026, 10, 25, 1, 30))
        self.assertTrue(controller.deploy_finished(self.instance, window_for(date(2026, 10, 24))))
        self.assertFalse(controller.deploy_finished(self.instance, window_for(date(2026, 10, 25))))

    def test_the_day_summer_time_starts(self):
        # 28 Mar 2027: 00:30 CET is 23:30 UTC on 27 Mar. A failed release counts too.
        op(self.instance, "RELEASE", "FAILURE", datetime(2027, 3, 28, 0, 0), datetime(2027, 3, 28, 0, 30))
        self.assertTrue(controller.deploy_finished(self.instance, window_for(date(2027, 3, 27))))
        self.assertFalse(controller.deploy_finished(self.instance, window_for(date(2027, 3, 28))))

    def test_only_deploy_kinds_that_finished(self):
        op(self.instance, "RESTART", "SUCCESS", datetime(2026, 10, 20, 9), datetime(2026, 10, 20, 9, 5))
        op(self.instance, "RELEASE", "RUNNING", datetime(2026, 10, 20, 10), None)
        self.assertFalse(controller.deploy_finished(self.instance, window_for(date(2026, 10, 20))))

    def test_the_deploy_kinds_exist_in_the_controller(self):
        known = {member.value for member in OperationType}
        self.assertLessEqual(set(catalog.DEPLOY_OPERATION_TYPES), known)

    def test_the_snapshot(self):
        instance = make_instance("ledger", rid="ledger-prod-2", client=make_client("globex"), autoscaling=False)
        snap = controller.snapshot(instance)
        self.assertEqual(
            (snap.instance_name, snap.resource_identifier, snap.instance_type, snap.client_id,
             snap.client_name, snap.autoscaling, snap.release_stage),
            ("ledger", "ledger-prod-2", "prod", "LEX-globex", "globex", False, "production"),
        )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_controller.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.sources.controller'`.

- [ ] **Step 3: Write the module**

`fleet_metrics/sources/controller.py`:

```python
"""What the Instance Controller itself knows: an instance's dimensions, and whether it was deployed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import connection

from fleet_metrics import catalog
from fleet_metrics.days import DayWindow
from instance_api.models.operation import InstanceOperation


@dataclass(frozen=True)
class Snapshot:
    instance_name: str
    resource_identifier: str
    instance_type: str
    release_stage: str
    instance_status: str
    client_id: str
    client_name: str
    autoscaling: Optional[bool]


def snapshot(instance) -> Snapshot:
    client = instance.client_config_metadata
    meta = instance.config_metadata
    return Snapshot(
        instance_name=instance.instance_name,
        resource_identifier=instance.resource_identifier,
        instance_type=instance.type,
        release_stage=instance.release_stage,
        instance_status=instance.status,
        client_id=getattr(client, "client_id", "") or "",
        client_name=getattr(client, "client_name", "") or "",
        autoscaling=getattr(meta, "autoscaling", None),
    )


_DEPLOY_FINISHED_SQL = f"""
SELECT EXISTS (
    SELECT 1 FROM {InstanceOperation._meta.db_table}
    WHERE instance_id = %s
      AND operation_type = ANY(%s)
      AND completed_at >= %s AND completed_at < %s
)
"""


def deploy_finished(instance, window: DayWindow) -> bool:
    """Whether an operation of a deploy kind finished on the UTC day (spec §5.4.6, step 3).

    Read in SQL against aware UTC bounds: the ORM would hand back naive Berlin times, which
    are ambiguous in the hour Berlin repeats when summer time ends (spec §6, rule 4). Any
    outcome counts: a failed release may still have replaced the version.
    """
    with connection.cursor() as cursor:
        cursor.execute(_DEPLOY_FINISHED_SQL, [
            instance.pk, list(catalog.DEPLOY_OPERATION_TYPES), window.start, window.end,
        ])
        return bool(cursor.fetchone()[0])
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_controller.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/sources/controller.py fleet_metrics/tests/test_controller.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): dimensions, and deploys by the UTC day they finished"
```

---

### Task 10: The version, without waking anything (K7)

R1 asks the cluster, never Prometheus, whether the backend is up. The readiness check reuses the controller's own helper, `Kubectl.get_pod_readiness(labels_dict) -> bool` (`subprocess_lib/Kubectl.py`): it runs `kubectl get pods --selector=<labels> -n <settings.k8s_namespace>` and is true only when every container of every matching pod is ready, so a paused backend (no pod) answers false. `IOs.check_instance_status` calls it the same way, with `{**instance.config_metadata.resource_labels, "layer": IOs.BACKEND}` (`instance_api/views/instance_operations/IOs.py:91-95`, `IOs.BACKEND = "backend"`). The runtime table `lex_runtime_versions` is a version source from R3 (Task 18); before that it is not read.

`version_releases_behind` counts **minor releases** (major.minor lines) newer than the running one among the final releases PyPI had published by the day; patch releases and pre-releases are not counted. K7's provisional flag, "more than two minor releases behind" (spec §4.4), reads it directly.

**Files:**
- Create: `fleet_metrics/sources/version.py`
- Test: `fleet_metrics/tests/test_version.py`
- Modify: `requirements.txt` (add `packaging`)

**Interfaces:**
- Consumes: `decrypt_runtime_metadata(token, api_key)` from `instance_api/views/instance_operations/runtime_health.py`; `Kubectl.get_pod_readiness(labels_dict) -> bool` from `subprocess_lib/Kubectl.py`.
- Produces: `PYPI_URL`; `BACKEND_LAYER`; `backend_ready(instance, *, kubectl=Kubectl) -> bool`; `in_cluster_health_url(instance) -> str`; `version_in_cluster(instance, *, session=requests, timeout=3.0) -> Optional[str]`; `pypi_release_dates(*, session=requests, timeout=10.0) -> dict[str, date]`; `version_age_days(version: str, day: date, release_dates: dict[str, date]) -> Optional[int]`; `version_releases_behind(version: str, day: date, release_dates: dict[str, date]) -> Optional[int]`.

- [ ] **Step 1: Pin `packaging`**

Add one line, `packaging`, to `requirements.txt`. It is already installed through pytest; the collector imports it at runtime to order releases, so it is pinned here explicitly. Re-run `uv pip install -r requirements.txt`.

- [ ] **Step 2: Write the failing tests**

`fleet_metrics/tests/test_version.py`:

```python
"""The version from the in-cluster health token when the backend is ready, and its currency from PyPI."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from django.test import SimpleTestCase

from fleet_metrics.sources import version
from instance_api.views.instance_operations.runtime_health import _derive_fernet_key

pytestmark = pytest.mark.unit
INSTANCE = SimpleNamespace(
    resource_identifier="books-prod-1",
    config_metadata=SimpleNamespace(api_key="raw-api-key", url="https://books.example.test",
                                    resource_labels={"instance": "books-prod-1"}),
)


class Session:
    def __init__(self, body):
        self.body = body
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: self.body)


class FakeKubectl:
    def __init__(self, ready):
        self.ready, self.calls = ready, []

    def get_pod_readiness(self, labels):
        self.calls.append(labels)
        return self.ready


class ReadinessTests(SimpleTestCase):
    def test_readiness_is_asked_for_this_instances_backend_pod(self):
        kubectl = FakeKubectl(True)
        self.assertTrue(version.backend_ready(INSTANCE, kubectl=kubectl))
        self.assertEqual(kubectl.calls, [{"instance": "books-prod-1", "layer": "backend"}])

    def test_without_labels_nothing_is_asked(self):
        bare = SimpleNamespace(resource_identifier="x", config_metadata=SimpleNamespace(resource_labels={}))
        kubectl = FakeKubectl(True)
        self.assertFalse(version.backend_ready(bare, kubectl=kubectl))
        self.assertEqual(kubectl.calls, [], "the layer alone would select every instance's backend")


class VersionTests(SimpleTestCase):
    def test_the_in_cluster_service_never_the_public_url(self):
        token = Fernet(_derive_fernet_key("raw-api-key")).encrypt(
            json.dumps({"lex_app_version": "2.4.0"}).encode()
        ).decode()
        session = Session({"status": "Healthy :)", "runtime": token})

        self.assertEqual(version.version_in_cluster(INSTANCE, session=session), "2.4.0")
        self.assertEqual(session.urls, ["http://lex-backend-books-prod-1:7000/api/health"])

    def test_no_token_no_version(self):
        self.assertIsNone(version.version_in_cluster(INSTANCE, session=Session({"status": "Healthy :)"})))

    def test_pypi_release_dates_take_the_first_upload(self):
        session = Session({"releases": {
            "2.3.2": [{"upload_time_iso_8601": "2026-09-29T13:05:00.000000Z"},
                      {"upload_time_iso_8601": "2026-09-30T08:00:00.000000Z"}],
            "2.3.3": [],
        }})
        self.assertEqual(version.pypi_release_dates(session=session), {"2.3.2": date(2026, 9, 29)})
        self.assertEqual(session.urls, [version.PYPI_URL])

    def test_age_in_days(self):
        dates = {"2.3.2": date(2026, 9, 29)}
        self.assertEqual(version.version_age_days("2.3.2", date(2026, 12, 28), dates), 90)
        self.assertIsNone(version.version_age_days("0.0.0.dev0", date(2026, 12, 28), dates))

    def test_releases_behind_counts_minor_releases_published_by_the_day(self):
        dates = {"2.3.2": date(2026, 9, 29), "2.3.3": date(2026, 10, 6), "2.4.0rc1": date(2026, 10, 8),
                 "2.4.0": date(2026, 10, 14), "2.4.1": date(2026, 10, 20), "2.5.0": date(2026, 10, 30)}
        self.assertEqual(version.version_releases_behind("2.3.2", date(2026, 10, 25), dates), 1,
                         "2.4 only: 2.3.3 and 2.4.1 are patches, 2.4.0rc1 a pre-release, 2.5.0 came later")
        self.assertEqual(version.version_releases_behind("2.3.2", date(2026, 10, 31), dates), 2, "2.4 and 2.5")
        self.assertEqual(version.version_releases_behind("2.4.0", date(2026, 10, 25), dates), 0,
                         "a patch behind is not a minor release behind")
        self.assertIsNone(version.version_releases_behind("0.0.0.dev0", date(2026, 10, 25), dates))
```

- [ ] **Step 3: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_version.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.sources.version'`.

- [ ] **Step 4: Write the module**

`fleet_metrics/sources/version.py`:

```python
"""The lex-app version an instance runs, without waking it (spec §5.4.6)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import requests
from packaging.version import InvalidVersion, Version

from instance_api.views.instance_operations.runtime_health import decrypt_runtime_metadata
from subprocess_lib.Kubectl import Kubectl

PYPI_URL = "https://pypi.org/pypi/lex-app/json"

#: The backend pod's ``layer`` label, as ``IOs.check_instance_status`` selects it
#: (``instance_api/views/instance_operations/IOs.py``, ``IOs.BACKEND``).
BACKEND_LAYER = "backend"


def backend_ready(instance, *, kubectl=Kubectl) -> bool:
    """Whether the instance's backend pod is ready now; the version probe asks before calling it.

    Asked of the cluster through the controller's ``Kubectl.get_pod_readiness``, never of
    Prometheus: R1 has no Prometheus dependency (spec §5.8). Without the instance's resource
    labels the selector would match every backend in the namespace, so nothing is asked.
    """
    meta = instance.config_metadata
    labels = (getattr(meta, "resource_labels", None) or {}) if meta is not None else {}
    if not labels:
        return False
    return kubectl.get_pod_readiness({**labels, "layer": BACKEND_LAYER})


def in_cluster_health_url(instance) -> str:
    return f"http://lex-backend-{instance.resource_identifier}:7000/api/health"


def version_in_cluster(instance, *, session=requests, timeout: float = 3.0) -> Optional[str]:
    """The version inside the instance's encrypted health token, through the in-cluster service.

    Never the public URL: a request through the ingress counts towards KEDA's scale-up
    trigger and would wake a backend that has scaled to zero. The caller asks
    :func:`backend_ready` first.
    """
    api_key = instance.config_metadata.api_key
    if not api_key:
        return None
    response = session.get(in_cluster_health_url(instance), timeout=timeout)
    response.raise_for_status()
    token = (response.json() or {}).get("runtime")
    if not isinstance(token, str) or not token:
        return None
    return decrypt_runtime_metadata(token, api_key).get("lex_app_version") or None


def pypi_release_dates(*, session=requests, timeout: float = 10.0) -> dict[str, date]:
    """Each lex-app release on PyPI and the UTC day of its first upload."""
    response = session.get(PYPI_URL, timeout=timeout)
    response.raise_for_status()
    dates: dict[str, date] = {}
    for release, files in (response.json().get("releases") or {}).items():
        stamps = [f["upload_time_iso_8601"] for f in files if f.get("upload_time_iso_8601")]
        if stamps:
            first = min(datetime.fromisoformat(s.replace("Z", "+00:00")) for s in stamps)
            dates[release] = first.astimezone(timezone.utc).date()
    return dates


def version_age_days(version: str, day: date, release_dates: dict[str, date]) -> Optional[int]:
    """Days between a version's release and ``day``; None for a version PyPI does not know."""
    released = release_dates.get(version)
    return None if released is None else max((day - released).days, 0)


def version_releases_behind(version: str, day: date, release_dates: dict[str, date]) -> Optional[int]:
    """Minor releases behind: the major.minor lines newer than ``version``'s among the final
    releases PyPI had published by ``day``.

    Patch releases are not counted (2.4.1 is not behind 2.4.3), nor are pre-releases and dev
    releases. None for a version PyPI does not know.
    """
    if version not in release_dates:
        return None
    try:
        running = Version(version)
    except InvalidVersion:
        return None
    newer_lines = set()
    for name, released in release_dates.items():
        try:
            candidate = Version(name)
        except InvalidVersion:
            continue
        if not candidate.is_prerelease and released <= day and candidate.release[:2] > running.release[:2]:
            newer_lines.add(candidate.release[:2])
    return len(newer_lines)
```

- [ ] **Step 5: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_version.py -q`
Expected: `7 passed`.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt fleet_metrics/sources/version.py fleet_metrics/tests/test_version.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): read the version through the cluster once the backend is ready; age it by PyPI"
```

---

### Task 11: One instance-day, stored

**Files:**
- Create: `fleet_metrics/collector.py`
- Test: `fleet_metrics/tests/test_collector.py`

**Interfaces:**
- Consumes: everything from Tasks 2–10.
- Produces: `RunContext` (dataclass: `sources: frozenset[str]`, `release_dates: dict[str, date]`, `latest_day: date`, `include_db_aliases: bool`, `now: datetime`; Task 16 adds the Prometheus fields with defaults); `Outcome(status: str, values: list[Value] = [], error: str = "", ran: bool = True)`; `collect_instance_day(instance, window, ctx, run=None) -> InstanceDay`.

The storage rules (spec §5.2, §5.4.2):
- Dimensions are refreshed on every collection.
- A source's status and values are replaced when it succeeds (`catalog.DONE`) — or when the row has never had that source succeed. When it fails after an earlier success, the earlier values and status are kept and the failure is only noted in `errors`.
- A source that did not run — not delivered in this environment, or (from R2) a day outside the data Prometheus holds — never changes a stored row. A new row records it as `skipped`.
- The version is re-derived each time, but a version read from the instance (the health token; from R3 the runtime table) is never replaced by a carried or unknown one: a later re-run cannot read that day's version again.
- `DeployEvent` is never written here.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_collector.py`:

```python
"""One instance on one UTC day in R1: the delivered sources, the storage rules, the version fallbacks."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.test import TestCase

from fleet_metrics import catalog
from fleet_metrics.collector import RunContext, collect_instance_day
from fleet_metrics.days import window_for
from fleet_metrics.models import DeployEvent, InstanceDay
from fleet_metrics.sources.instance_db import InstanceDbResult
from fleet_metrics.tests.factories import make_instance

pytestmark = pytest.mark.integration
DAY = date(2026, 10, 25)
WINDOW = window_for(DAY)
NOW = datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)
R1 = frozenset({catalog.SOURCE_CONTROLLER, catalog.SOURCE_DB, catalog.SOURCE_VERSION})


def ctx(**overrides):
    fields = dict(
        sources=R1,
        release_dates={"2.4.0": date(2026, 10, 5), "2.4.1": date(2026, 10, 20), "2.5.0": date(2026, 10, 21)},
        latest_day=DAY, include_db_aliases=False, now=NOW,
    )
    fields.update(overrides)
    return RunContext(**fields)


DB_RESULT = InstanceDbResult(values=[("writers_1d", "", 2.0), ("calc_runs", "success", 4.0)])


@contextmanager
def fake_cursor(instance):
    yield object()


@contextmanager
def unreachable(instance):
    raise ConnectionError("instance database unreachable")
    yield


class CollectInstanceDayTests(TestCase):
    def setUp(self):
        self.instance = make_instance()
        patchers = {
            "cursor": patch("fleet_metrics.collector.instance_db.instance_cursor", fake_cursor),
            "collect": patch("fleet_metrics.collector.instance_db.collect", return_value=DB_RESULT),
            "deployed": patch("fleet_metrics.collector.controller.deploy_finished", return_value=False),
            "ready": patch("fleet_metrics.collector.version.backend_ready", return_value=False),
            "probe": patch("fleet_metrics.collector.version.version_in_cluster", return_value="2.4.0"),
        }
        self.mocks = {name: p.start() for name, p in patchers.items()}
        for p in patchers.values():
            self.addCleanup(p.stop)

    def _values(self, row):
        return {(v.metric, v.dimension): v.value for v in row.values.all()}

    def _yesterday_ran(self, version):
        collect_instance_day(self.instance, window_for(DAY - timedelta(days=1)), ctx())
        InstanceDay.objects.filter(day=DAY - timedelta(days=1)).update(lex_app_version=version)

    def test_one_row_with_dimensions_statuses_and_values(self):
        self.mocks["ready"].return_value = True
        row = collect_instance_day(self.instance, WINDOW, ctx())

        self.assertEqual((row.instance_pk, row.day, row.instance_name, row.client_name),
                         (self.instance.pk, DAY, "books", "acme"))
        self.assertEqual((row.controller_status, row.db_status), (catalog.OK, catalog.OK))
        self.assertEqual((row.prometheus_status, row.recorder), (catalog.SKIPPED, catalog.SKIPPED),
                         "R2's and R3's sources are not delivered in R1")
        self.assertEqual((row.lex_app_version, row.version_source), ("2.4.0", catalog.VERSION_HEALTH))
        values = self._values(row)
        self.assertEqual(values[("writers_1d", "")], 2.0)
        self.assertEqual(values[("version_age_days", "")], 20.0)
        self.assertEqual(values[("version_releases_behind", "")], 1.0, "2.5; 2.4.1 is only a patch")

    def test_running_it_twice_changes_nothing(self):
        collect_instance_day(self.instance, WINDOW, ctx())
        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(InstanceDay.objects.count(), 1)
        self.assertEqual(row.values.filter(metric="writers_1d").count(), 1)

    def test_a_failure_after_a_success_keeps_what_was_collected(self):
        collect_instance_day(self.instance, WINDOW, ctx())
        with patch("fleet_metrics.collector.instance_db.instance_cursor", unreachable):
            row = collect_instance_day(self.instance, WINDOW, ctx())

        self.assertEqual(row.db_status, catalog.OK, "the earlier success stands")
        self.assertEqual(self._values(row)[("writers_1d", "")], 2.0)
        self.assertIn("kept", row.errors["db"])

    def test_a_first_failure_is_recorded_as_one(self):
        with patch("fleet_metrics.collector.instance_db.instance_cursor", unreachable):
            row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(row.db_status, catalog.ERROR)
        self.assertIn("unreachable", row.errors["db"])

    def test_a_separate_database_server_is_excluded(self):
        instance = make_instance("vault", rid="vault-prod-3", db_server_alias="COMPLIANCE")
        row = collect_instance_day(instance, WINDOW, ctx())
        self.assertEqual(row.db_status, catalog.EXCLUDED)
        self.assertNotIn(("writers_1d", ""), self._values(row))
        self.mocks["collect"].assert_not_called()

    def test_a_source_not_delivered_is_skipped_and_erases_nothing(self):
        without_db = frozenset({catalog.SOURCE_CONTROLLER, catalog.SOURCE_VERSION})
        row = collect_instance_day(self.instance, WINDOW, ctx(sources=without_db))
        self.assertEqual(row.db_status, catalog.SKIPPED)
        self.mocks["collect"].assert_not_called()

        collect_instance_day(self.instance, WINDOW, ctx())
        row = collect_instance_day(self.instance, WINDOW, ctx(sources=without_db))
        self.assertEqual(row.db_status, catalog.OK, "a source that did not run leaves the row alone")
        self.assertEqual(self._values(row)[("writers_1d", "")], 2.0)

    def test_the_version_is_carried_when_nothing_was_deployed(self):
        self._yesterday_ran("2.3.2")
        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual((row.lex_app_version, row.version_source), ("2.3.2", catalog.VERSION_CARRIED))

    def test_a_deploy_breaks_the_carry_and_no_deploy_event_is_written(self):
        self._yesterday_ran("2.3.2")
        self.mocks["deployed"].return_value = True

        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(row.version_source, catalog.VERSION_UNKNOWN)
        self.assertFalse(DeployEvent.objects.exists(), "K8's owners fill DeployEvent; the collector never does")

    def test_an_unreadable_controller_breaks_the_carry_too(self):
        self._yesterday_ran("2.3.2")
        self.mocks["deployed"].side_effect = ConnectionError("controller database gone")

        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(row.controller_status, catalog.ERROR)
        self.assertEqual(row.version_source, catalog.VERSION_UNKNOWN, "a deploy cannot be ruled out")

    def test_the_health_probe_only_for_the_latest_day_and_only_when_ready(self):
        down = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(down.version_source, catalog.VERSION_UNKNOWN)
        self.mocks["probe"].assert_not_called()

        self.mocks["ready"].return_value = True
        up = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual((up.lex_app_version, up.version_source), ("2.4.0", catalog.VERSION_HEALTH))

        collect_instance_day(self.instance, window_for(date(2026, 10, 20)), ctx())
        self.assertEqual(self.mocks["probe"].call_count, 1, "a backfilled day never probes")

    def test_a_re_run_keeps_a_version_it_can_no_longer_read(self):
        self.mocks["ready"].return_value = True
        collect_instance_day(self.instance, WINDOW, ctx())

        row = collect_instance_day(self.instance, WINDOW, ctx(latest_day=DAY + timedelta(days=1)))
        self.assertEqual((row.lex_app_version, row.version_source), ("2.4.0", catalog.VERSION_HEALTH))
        self.assertEqual(self._values(row)[("version_age_days", "")], 20.0)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_collector.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.collector'`.

- [ ] **Step 3: Write the module**

`fleet_metrics/collector.py`:

```python
"""One instance on one UTC day, from every delivered source, stored (spec §5.4.2, §5.5).

Each source runs on its own; a failing source marks only itself. The storage rules never
destroy data a re-run cannot reproduce: a source that fails after an earlier success keeps
the earlier values, a source that did not run leaves the row alone, and a version read from
the instance is never replaced by a guess.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from django.db import transaction

from fleet_metrics import catalog
from fleet_metrics.days import DayWindow
from fleet_metrics.models import CollectionRun, InstanceDay, MetricValue
from fleet_metrics.sources import controller, instance_db, version

logger = logging.getLogger(__name__)

Value = tuple[str, str, float]

#: Versions read from the instance itself. A re-run that can only carry or not know keeps them.
_READ_VERSIONS = (catalog.VERSION_RUNTIME_TABLE, catalog.VERSION_HEALTH)


@dataclass
class RunContext:
    """What every instance-day of one run shares. Task 16 (R2) adds the Prometheus fields."""

    sources: frozenset[str]
    release_dates: dict[str, date]
    latest_day: date
    include_db_aliases: bool
    now: datetime


@dataclass
class Outcome:
    status: str
    values: list[Value] = field(default_factory=list)
    error: str = ""
    ran: bool = True   # False: the source did not run, so it says nothing about a stored row


def _not_delivered() -> Outcome:
    return Outcome(catalog.SKIPPED, ran=False)


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def _controller(instance, window: DayWindow, ctx: RunContext) -> tuple[Outcome, Optional[bool]]:
    """The controller's own facts. ``deployed`` is None when they could not be read."""
    if catalog.SOURCE_CONTROLLER not in ctx.sources:
        return _not_delivered(), None
    try:
        return Outcome(catalog.OK), controller.deploy_finished(instance, window)
    except Exception as exc:
        logger.warning("fleet metrics: operations of %s on %s: %s", instance.resource_identifier, window.day, exc)
        return Outcome(catalog.ERROR, error=_describe(exc)), None


def _database(instance, window: DayWindow, ctx: RunContext) -> tuple[Outcome, Optional[instance_db.InstanceDbResult]]:
    if catalog.SOURCE_DB not in ctx.sources:
        return _not_delivered(), None
    meta = instance.config_metadata
    if meta is None or not meta.database_name:
        return Outcome(catalog.SKIPPED, error="the instance has no database yet"), None
    if meta.db_server_alias and not ctx.include_db_aliases:
        return Outcome(catalog.EXCLUDED, error=f"database server {meta.db_server_alias} is excluded"), None
    try:
        with instance_db.instance_cursor(instance) as cursor:
            result = instance_db.collect(cursor, window)
    except Exception as exc:
        logger.warning("fleet metrics: database of %s on %s: %s", instance.resource_identifier, window.day, exc)
        return Outcome(catalog.ERROR, error=_describe(exc)), None
    return Outcome(catalog.OK, list(result.values)), result


def _version(instance, window: DayWindow, ctx: RunContext, deployed: Optional[bool]) -> tuple[str, str]:
    """The version that ran at the end of the day, and where it came from (spec §5.4.6)."""
    if window.day == ctx.latest_day:
        try:
            if version.backend_ready(instance):
                found = version.version_in_cluster(instance)
                if found:
                    return found, catalog.VERSION_HEALTH
        except Exception as exc:
            logger.warning("fleet metrics: version probe for %s: %s", instance.resource_identifier, exc)
    previous = (InstanceDay.objects.filter(instance_pk=instance.pk, day=window.day - timedelta(days=1))
                .values_list("lex_app_version", flat=True).first())
    if previous and deployed is False:
        return previous, catalog.VERSION_CARRIED
    return "", catalog.VERSION_UNKNOWN


def _version_values(found: str, day: date, release_dates: dict[str, date]) -> list[Value]:
    if not found:
        return []
    values: list[Value] = []
    age = version.version_age_days(found, day, release_dates)
    if age is not None:
        values.append(("version_age_days", "", float(age)))
    behind = version.version_releases_behind(found, day, release_dates)
    if behind is not None:
        values.append(("version_releases_behind", "", float(behind)))
    return values


def _apply(row: InstanceDay, status_field: str, source: str, outcome: Outcome,
           errors: dict, replace: dict) -> None:
    """Take the source's outcome, unless it did not run or failed after an earlier success."""
    if not outcome.ran:
        if not row.pk:
            setattr(row, status_field, catalog.SKIPPED)
            if outcome.error:
                errors[source] = outcome.error
        return
    earlier = getattr(row, status_field, "") if row.pk else ""
    if outcome.status in catalog.DONE or earlier != catalog.OK:
        setattr(row, status_field, outcome.status)
        replace[source] = outcome.values
        if outcome.error:
            errors[source] = outcome.error
        else:
            errors.pop(source, None)
    else:
        errors[source] = f"{outcome.error} (kept the values collected earlier)"


def collect_instance_day(instance, window: DayWindow, ctx: RunContext,
                         run: Optional[CollectionRun] = None) -> InstanceDay:
    """Collect and store one instance on one UTC day. Raises only if storing fails."""
    controller_outcome, deployed = _controller(instance, window, ctx)
    db_outcome, db_result = _database(instance, window, ctx)
    version_delivered = catalog.SOURCE_VERSION in ctx.sources
    found, found_source = _version(instance, window, ctx, deployed) if version_delivered else ("", catalog.SKIPPED)

    with transaction.atomic():
        row = (InstanceDay.objects.select_for_update()
               .filter(instance_pk=instance.pk, day=window.day).first()
               or InstanceDay(instance_pk=instance.pk, day=window.day))
        row.instance = instance
        for name, value in asdict(controller.snapshot(instance)).items():
            setattr(row, name, value)
        row.run = run
        errors = dict(row.errors or {})
        replace: dict[str, list[Value]] = {}

        _apply(row, "controller_status", catalog.SOURCE_CONTROLLER, controller_outcome, errors, replace)
        _apply(row, "db_status", catalog.SOURCE_DB, db_outcome, errors, replace)

        if version_delivered:
            keep_read = bool(row.pk) and row.version_source in _READ_VERSIONS and found_source not in _READ_VERSIONS
            if not keep_read:
                row.lex_app_version, row.version_source = found, found_source
            replace[catalog.SOURCE_VERSION] = _version_values(row.lex_app_version, window.day, ctx.release_dates)
        elif not row.pk:
            row.version_source = catalog.SKIPPED

        row.errors = errors
        row.save()

        MetricValue.objects.filter(instance_day=row, source__in=list(replace)).delete()
        MetricValue.objects.bulk_create([
            MetricValue(instance_day=row, metric=metric, dimension=dimension, value=value, source=source)
            for source, values in replace.items()
            for metric, dimension, value in values
        ])
    return row
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_collector.py -q`
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/collector.py fleet_metrics/tests/test_collector.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): store an instance day without ever erasing what a re-run cannot see"
```

---

### Task 12: The run, the delivered sources, the catch-up and the heartbeat

The 95% rule of spec §7 applies to **delivered** sources only. A source not yet delivered in this environment is `skipped` in every row and is neither ok nor failed: the verdict does not judge it, and it does not reopen a day for the catch-up. `FLEET_METRICS_SOURCES` turns sources on, release by release (spec §5.4.2).

The test environment has no Better Stack monitor. With `FLEET_METRICS_HEARTBEAT_URL` empty, the run records `heartbeat = skipped` and succeeds: the heartbeat never decides a run's status.

**Files:**
- Create: `fleet_metrics/heartbeat.py`, `fleet_metrics/run.py`
- Test: `fleet_metrics/tests/test_heartbeat.py`, `fleet_metrics/tests/test_run.py`

**Interfaces:**
- Produces (`heartbeat`): `THRESHOLD = 0.95`; `verdict(day: date, expected_pks: list[int], sources: frozenset[str]) -> tuple[bool, str]`; `send(url: str, ok: bool, message: str, *, session=requests, timeout=10.0) -> str` (returns a `CollectionRun.HEARTBEAT_*` value).
- Produces (`run`): `expected_instances()`; `created_day(instance) -> date`; `days_to_collect(instance, latest: date, catchup_days: int, sources: frozenset[str]) -> list[date]`; `run_collection(*, trigger: str, days: Optional[list[date]] = None, instance_pks: Optional[list[int]] = None, ping: bool = True, now: Optional[datetime] = None) -> CollectionRun`.

- [ ] **Step 1: Write the failing heartbeat tests**

`fleet_metrics/tests/test_heartbeat.py`:

```python
"""The heartbeat means rows were written by the delivered sources, not that the job ran (spec §7)."""

from datetime import date
from types import SimpleNamespace

import pytest
import requests
from django.test import SimpleTestCase, TestCase

from fleet_metrics import catalog, heartbeat
from fleet_metrics.models import CollectionRun, InstanceDay
from fleet_metrics.tests.factories import make_instance

DAY = date(2026, 10, 25)
R1 = frozenset({catalog.SOURCE_CONTROLLER, catalog.SOURCE_DB, catalog.SOURCE_VERSION})
R2 = R1 | {catalog.SOURCE_PROMETHEUS}


def row(instance, **statuses):
    fields = dict(db_status=catalog.OK, controller_status=catalog.OK)
    fields.update(statuses)
    return InstanceDay.objects.create(
        instance=instance, instance_pk=instance.pk, day=DAY, instance_name=instance.instance_name,
        resource_identifier=instance.resource_identifier, instance_type=instance.type,
        release_stage=instance.release_stage, instance_status=instance.status, **fields,
    )


@pytest.mark.integration
class VerdictTests(TestCase):
    def setUp(self):
        self.instances = [make_instance(f"i{n}", rid=f"i{n}-prod-{n}") for n in range(20)]

    def _verdict(self, sources=R1):
        return heartbeat.verdict(DAY, [i.pk for i in self.instances], sources)

    def test_every_row_written_and_every_source_fine(self):
        for instance in self.instances:
            row(instance)
        ok, message = self._verdict()
        self.assertTrue(ok, message)

    def test_a_missing_row_fails(self):
        for instance in self.instances[1:]:
            row(instance)
        ok, message = self._verdict()
        self.assertFalse(ok)
        self.assertIn("1 expected instance(s) have no row", message)

    def test_one_failing_source_in_twenty_is_tolerated_two_are_not(self):
        row(self.instances[0], db_status=catalog.ERROR)
        for instance in self.instances[1:]:
            row(instance)
        self.assertTrue(self._verdict()[0], "19/20 is 95%")

        InstanceDay.objects.filter(instance_pk=self.instances[1].pk).update(db_status=catalog.ERROR)
        ok, message = self._verdict()
        self.assertFalse(ok)
        self.assertIn("db_status: 18/20", message)

    def test_excluded_and_skipped_are_fine(self):
        row(self.instances[0], db_status=catalog.EXCLUDED)
        row(self.instances[1], controller_status=catalog.SKIPPED)
        for instance in self.instances[2:]:
            row(instance)
        self.assertTrue(self._verdict()[0])

    def test_a_source_not_delivered_is_neither_ok_nor_failed(self):
        for n, instance in enumerate(self.instances):
            row(instance, prometheus_status=catalog.ERROR if n < 5 else catalog.SKIPPED)
        ok, message = self._verdict(R1)
        self.assertTrue(ok, message)
        self.assertNotIn("prometheus", message)

        ok, message = self._verdict(R2)
        self.assertFalse(ok)
        self.assertIn("prometheus_status: 15/20 ok", message)


@pytest.mark.unit
class SendTests(SimpleTestCase):
    class Session:
        def __init__(self, fail=False):
            self.fail, self.calls = fail, []

        def post(self, url, data, timeout):
            self.calls.append(url)
            if self.fail:
                raise requests.ConnectionError("down")
            return SimpleNamespace(raise_for_status=lambda: None)

    def test_success_pings_the_url_failure_pings_fail(self):
        session = self.Session()
        self.assertEqual(heartbeat.send("https://hb/abc", True, "ok", session=session), CollectionRun.HEARTBEAT_SENT)
        self.assertEqual(heartbeat.send("https://hb/abc", False, "db", session=session),
                         CollectionRun.HEARTBEAT_FAILURE_SENT)
        self.assertEqual(session.calls, ["https://hb/abc", "https://hb/abc/fail"])

    def test_no_url_is_skipped_and_an_unreachable_one_is_an_error(self):
        self.assertEqual(heartbeat.send("", True, "ok"), CollectionRun.HEARTBEAT_SKIPPED)
        self.assertEqual(heartbeat.send("https://hb/abc", True, "ok", session=self.Session(fail=True)),
                         CollectionRun.HEARTBEAT_ERROR)
```

- [ ] **Step 2: Write the failing run tests**

`fleet_metrics/tests/test_run.py`:

```python
"""Which days and sources a run collects, and what a run records."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase, TestCase

from fleet_metrics import catalog
from fleet_metrics.models import CollectionRun, InstanceDay
from fleet_metrics.run import days_to_collect, expected_instances, run_collection
from fleet_metrics.tests.factories import make_instance
from instance_api.models import LexInstance
from lex_instance_controller_backend import settings as ic_settings

LATEST = date(2026, 10, 25)
NOW = datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)
R1 = frozenset({catalog.SOURCE_CONTROLLER, catalog.SOURCE_DB, catalog.SOURCE_VERSION})


def done_row(instance, day, **statuses):
    fields = dict(db_status=catalog.OK, controller_status=catalog.OK)
    fields.update(statuses)
    InstanceDay.objects.create(
        instance=instance, instance_pk=instance.pk, day=day, instance_name="x", resource_identifier="x",
        instance_type="prod", release_stage="production", instance_status="RUNNING", **fields,
    )


@pytest.mark.unit
class SourcesSettingTests(SimpleTestCase):
    def test_release_1_is_the_default(self):
        self.assertEqual(catalog.parse_sources(ic_settings.fleet_metrics_sources), R1)
        self.assertEqual(catalog.parse_sources(" controller, db,version "), R1)

    def test_a_source_this_build_does_not_collect_is_refused(self):
        with self.assertRaises(ValueError):
            catalog.parse_sources("controller,db,version,clickstream")

    def test_no_source_at_all_is_refused(self):
        with self.assertRaises(ValueError):
            catalog.parse_sources(" , ")


@pytest.mark.integration
class DaysToCollectTests(TestCase):
    def setUp(self):
        self.instance = make_instance()
        LexInstance.objects.filter(pk=self.instance.pk).update(creation_timestamp=datetime(2026, 10, 20, 12))
        self.instance.refresh_from_db()

    def test_from_creation_to_yesterday_then_only_what_is_missing(self):
        self.assertEqual(days_to_collect(self.instance, LATEST, 35, R1),
                         [date(2026, 10, d) for d in range(20, 26)])
        for d in range(20, 25):
            done_row(self.instance, date(2026, 10, d))
        InstanceDay.objects.filter(day=date(2026, 10, 22)).update(db_status=catalog.ERROR)
        self.assertEqual(days_to_collect(self.instance, LATEST, 35, R1), [date(2026, 10, 22), LATEST])

    def test_yesterday_is_always_collected_again(self):
        for d in range(20, 26):
            done_row(self.instance, date(2026, 10, d))
        self.assertEqual(days_to_collect(self.instance, LATEST, 35, R1), [LATEST])

    def test_the_catch_up_window_bounds_the_past(self):
        LexInstance.objects.filter(pk=self.instance.pk).update(creation_timestamp=datetime(2026, 1, 1))
        self.instance.refresh_from_db()
        days = days_to_collect(self.instance, LATEST, 3, R1)
        self.assertEqual(days, [LATEST - timedelta(days=n) for n in (3, 2, 1, 0)])

    def test_a_source_not_delivered_does_not_reopen_a_day(self):
        for d in range(20, 25):
            done_row(self.instance, date(2026, 10, d))
        InstanceDay.objects.filter(day=date(2026, 10, 22)).update(prometheus_status=catalog.ERROR)
        self.assertEqual(days_to_collect(self.instance, LATEST, 35, R1), [LATEST])
        self.assertEqual(days_to_collect(self.instance, LATEST, 35, R1 | {catalog.SOURCE_PROMETHEUS}),
                         [date(2026, 10, 22), LATEST])


@pytest.mark.integration
class RunCollectionTests(TestCase):
    def setUp(self):
        self.running = make_instance("a", rid="a-prod-1")
        self.deleted = make_instance("b", rid="b-prod-2", status=LexInstance.DELETED)
        for instance in (self.running, self.deleted):
            LexInstance.objects.filter(pk=instance.pk).update(creation_timestamp=datetime(2026, 10, 24, 12))

    def test_deleted_instances_are_not_expected(self):
        self.assertEqual([i.pk for i in expected_instances()], [self.running.pk])

    @patch("fleet_metrics.run.version.pypi_release_dates", return_value={})
    @patch("fleet_metrics.run.grants.ensure_reader_grants", return_value=True)
    def test_a_scheduled_run_collects_records_and_pings(self, grants, _dates):
        def collect(instance, window, ctx, run=None):
            done_row(instance, window.day)

        with patch("fleet_metrics.run.collect_instance_day", side_effect=collect) as collected, \
                patch("fleet_metrics.run.heartbeat.send", return_value=CollectionRun.HEARTBEAT_SENT) as send, \
                patch.object(ic_settings, "fleet_metrics_heartbeat_url", "https://hb/abc"):
            run = run_collection(trigger=CollectionRun.TRIGGER_CRON, now=NOW)

        grants.assert_called_once()
        self.assertEqual([c.args[1].day for c in collected.call_args_list], [date(2026, 10, 24), LATEST])
        self.assertEqual(collected.call_args.args[2].sources, R1)
        self.assertEqual((run.status, run.instances_expected, run.instance_days_written, run.heartbeat),
                         (CollectionRun.STATUS_OK, 1, 2, CollectionRun.HEARTBEAT_SENT))
        self.assertEqual(run.summary["sources"], ["controller", "db", "version"])
        self.assertEqual(send.call_args.args[:2], ("https://hb/abc", True))

    @patch("fleet_metrics.run.version.pypi_release_dates", return_value={})
    @patch("fleet_metrics.run.grants.ensure_reader_grants", return_value=True)
    def test_without_a_heartbeat_url_the_run_records_skipped_and_succeeds(self, _grants, _dates):
        def collect(instance, window, ctx, run=None):
            done_row(instance, window.day)

        with patch("fleet_metrics.run.collect_instance_day", side_effect=collect), \
                patch.object(ic_settings, "fleet_metrics_heartbeat_url", ""):
            run = run_collection(trigger=CollectionRun.TRIGGER_CRON, now=NOW)
        self.assertEqual((run.status, run.heartbeat), (CollectionRun.STATUS_OK, CollectionRun.HEARTBEAT_SKIPPED),
                         "the test environment has no Better Stack monitor")

    @patch("fleet_metrics.run.version.pypi_release_dates", return_value={})
    @patch("fleet_metrics.run.grants.ensure_reader_grants", return_value=True)
    def test_a_manual_backfill_never_pings(self, _grants, _dates):
        with patch("fleet_metrics.run.collect_instance_day"), \
                patch("fleet_metrics.run.heartbeat.send") as send:
            run = run_collection(trigger=CollectionRun.TRIGGER_MANUAL, days=[date(2026, 10, 24)],
                                 ping=False, now=NOW)
        send.assert_not_called()
        self.assertEqual(run.heartbeat, "")

    @patch("fleet_metrics.run.version.pypi_release_dates", return_value={})
    @patch("fleet_metrics.run.grants.ensure_reader_grants", return_value=True)
    def test_a_run_that_stores_nothing_fails(self, _grants, _dates):
        with patch("fleet_metrics.run.collect_instance_day", side_effect=RuntimeError("db gone")), \
                patch("fleet_metrics.run.heartbeat.send", return_value=CollectionRun.HEARTBEAT_FAILURE_SENT):
            run = run_collection(trigger=CollectionRun.TRIGGER_CRON, now=NOW)
        self.assertEqual(run.status, CollectionRun.STATUS_FAILED)
        self.assertEqual(run.instance_days_written, 0)

    def test_a_source_this_build_does_not_collect_stops_the_run(self):
        with patch.object(ic_settings, "fleet_metrics_sources", "controller,db,version,clickstream"):
            with self.assertRaises(ValueError):
                run_collection(trigger=CollectionRun.TRIGGER_CRON, now=NOW)
        self.assertFalse(CollectionRun.objects.exists())
```

- [ ] **Step 3: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_heartbeat.py fleet_metrics/tests/test_run.py -q`
Expected: `ModuleNotFoundError` for `fleet_metrics.heartbeat` / `fleet_metrics.run`.

- [ ] **Step 4: Write the heartbeat module**

`fleet_metrics/heartbeat.py`:

```python
"""The Better Stack heartbeat: pinged only when yesterday's rows were written (spec §7).

The collector's pod is not covered by the controller's error-log alert, which matches only
the backend and worker pods, so this heartbeat is its alarm: a missed ping (24-hour period
plus 3 hours of grace) or a call to the failure endpoint opens an incident. Only the sources
delivered in this environment are judged: a source not delivered yet is ``skipped`` in every
row and is neither ok nor failed.
"""

from __future__ import annotations

import logging
from datetime import date

import requests

from fleet_metrics import catalog
from fleet_metrics.models import CollectionRun, InstanceDay

logger = logging.getLogger(__name__)

#: A delivered source may fail for at most 5% of expected instances before the run counts as failed.
THRESHOLD = 0.95


def verdict(day: date, expected_pks: list[int], sources: frozenset[str]) -> tuple[bool, str]:
    """(fine, why) for the rows of ``day`` against the instances that should have one."""
    rows = list(InstanceDay.objects.filter(day=day, instance_pk__in=expected_pks))
    total = len(expected_pks)
    problems = []
    missing = set(expected_pks) - {r.instance_pk for r in rows}
    if missing:
        problems.append(f"{len(missing)} expected instance(s) have no row for {day}")
    for source in sorted(sources):
        status_field = catalog.STATUS_FIELDS.get(source)
        if status_field is None:
            continue
        good = sum(1 for r in rows if getattr(r, status_field) in catalog.DONE)
        if total and good < total * THRESHOLD:
            problems.append(f"{status_field}: {good}/{total} ok")
    return not problems, "; ".join(problems) or "ok"


def send(url: str, ok: bool, message: str, *, session=requests, timeout: float = 10.0) -> str:
    """Ping the heartbeat, or its failure endpoint. Returns a ``CollectionRun.HEARTBEAT_*`` value.

    No URL — the test environment has no Better Stack monitor — is ``skipped``, not a failure.
    """
    if not url:
        return CollectionRun.HEARTBEAT_SKIPPED
    target = url if ok else f"{url.rstrip('/')}/fail"
    try:
        response = session.post(target, data=message.encode("utf-8")[:1000], timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("fleet metrics: the Better Stack heartbeat could not be reached: %s", exc)
        return CollectionRun.HEARTBEAT_ERROR
    return CollectionRun.HEARTBEAT_SENT if ok else CollectionRun.HEARTBEAT_FAILURE_SENT
```

Before relying on `/fail`, check Better Stack's heartbeat documentation for the failure-report URL; if it differs, change `target` and the test together. A wrong failure URL only delays the alert to the 27-hour timeout.

- [ ] **Step 5: Write the run module**

`fleet_metrics/run.py`:

```python
"""A collection run: which instances, which days, which sources, and the heartbeat (spec §5.4.2, §7)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from django.utils import timezone as django_timezone

from fleet_metrics import catalog, grants, heartbeat
from fleet_metrics.collector import RunContext, collect_instance_day
from fleet_metrics.days import day_range, utc_now, window_for, yesterday
from fleet_metrics.models import CollectionRun, InstanceDay
from fleet_metrics.sources import version
from instance_api.models import LexInstance
from lex_instance_controller_backend import settings

logger = logging.getLogger(__name__)
_BERLIN = ZoneInfo("Europe/Berlin")


def expected_instances():
    """Every instance that is not deleted. Paused ones are included: they produce explicit zeros."""
    return (LexInstance.objects.exclude(status__in=[LexInstance.DELETED, LexInstance.DELETING])
            .select_related("config_metadata", "client_config_metadata").order_by("instance_id"))


def created_day(instance) -> date:
    """The UTC day an instance was created. Its timestamp is naive Berlin, as the ORM stores it;
    an hour's ambiguity cannot move a creation date by more than a day, and only at midnight."""
    stamp = instance.creation_timestamp
    if stamp is None:
        return date.min
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_BERLIN)
    return stamp.astimezone(timezone.utc).date()


def _judged(sources: frozenset[str]) -> list[str]:
    """The status columns of the delivered sources that have one."""
    return [catalog.STATUS_FIELDS[source] for source in sorted(sources) if source in catalog.STATUS_FIELDS]


def days_to_collect(instance, latest: date, catchup_days: int, sources: frozenset[str]) -> list[date]:
    """Yesterday, plus every earlier day in the catch-up window that is missing or has a delivered source failed."""
    first = max(latest - timedelta(days=catchup_days), created_day(instance))
    if first > latest:
        return []
    complete = set(
        InstanceDay.objects.filter(
            instance_pk=instance.pk, day__gte=first, day__lt=latest,
            **{f"{status_field}__in": catalog.DONE for status_field in _judged(sources)},
        ).values_list("day", flat=True)
    )
    return [day for day in day_range(first, latest) if day == latest or day not in complete]


def _release_dates() -> dict[str, date]:
    try:
        return version.pypi_release_dates()
    except Exception as exc:
        logger.warning("fleet metrics: PyPI release dates unavailable; version currency is skipped: %s", exc)
        return {}


def run_collection(*, trigger: str, days: Optional[list[date]] = None,
                   instance_pks: Optional[list[int]] = None, ping: bool = True,
                   now: Optional[datetime] = None) -> CollectionRun:
    """Collect every expected instance for ``days`` (default: yesterday plus the catch-up).

    A ``FLEET_METRICS_SOURCES`` naming a source this build cannot collect raises before the
    run is recorded: the job fails and the missing heartbeat raises the alarm.
    """
    now = now or utc_now()
    latest = yesterday(now)
    sources = catalog.parse_sources(settings.fleet_metrics_sources)
    run = CollectionRun.objects.create(trigger=trigger, summary={"sources": sorted(sources)})
    try:
        grants.ensure_reader_grants()
    except Exception as exc:
        logger.warning("fleet metrics: reader grants not applied: %s", exc)

    ctx = RunContext(
        sources=sources,
        release_dates=_release_dates() if catalog.SOURCE_VERSION in sources else {},
        latest_day=latest,
        include_db_aliases=str(settings.fleet_metrics_include_db_aliases).lower() == "true",
        now=now,
    )
    instances = list(expected_instances())
    if instance_pks:
        instances = [i for i in instances if i.pk in set(instance_pks)]

    written = failed = 0
    collected_days: list[date] = []
    for instance in instances:
        plan = (days if days is not None
                else days_to_collect(instance, latest, settings.fleet_metrics_catchup_days, sources))
        for day in plan:
            try:
                row = collect_instance_day(instance, window_for(day), ctx, run)
            except Exception:
                failed += 1
                logger.warning("fleet metrics: %s on %s could not be stored", instance.resource_identifier, day,
                               exc_info=True)
                continue
            written += 1
            collected_days.append(day)
            if any(getattr(row, status_field, catalog.OK) not in catalog.DONE for status_field in _judged(sources)):
                failed += 1

    run.instances_expected = len(instances)
    run.instance_days_written = written
    run.instance_days_failed = failed
    run.first_day = min(collected_days) if collected_days else None
    run.last_day = max(collected_days) if collected_days else None
    if written == 0 and instances:
        run.status = CollectionRun.STATUS_FAILED
        logger.error("fleet metrics: run %s stored nothing for %d instance(s)", run.pk, len(instances))
    else:
        run.status = CollectionRun.STATUS_DEGRADED if failed else CollectionRun.STATUS_OK

    if ping and days is None:
        expected = [i.pk for i in instances if created_day(i) <= latest]
        ok, message = heartbeat.verdict(latest, expected, sources)
        run.summary["heartbeat_verdict"] = message
        run.heartbeat = heartbeat.send(settings.fleet_metrics_heartbeat_url, ok, message)
    run.finished_at = django_timezone.now()
    run.save()
    return run
```

The heartbeat is decided only for the scheduled form (`days is None`): a backfill says nothing about yesterday.

- [ ] **Step 6: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_heartbeat.py fleet_metrics/tests/test_run.py -q`
Expected: `20 passed`.

- [ ] **Step 7: Commit**

```bash
git add fleet_metrics/heartbeat.py fleet_metrics/run.py fleet_metrics/tests/test_heartbeat.py fleet_metrics/tests/test_run.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): catch up missed days and ping only when the delivered sources wrote their rows"
```

---

### Task 13: The command the CronJob runs

**Files:**
- Create: `fleet_metrics/management/__init__.py`, `fleet_metrics/management/commands/__init__.py` (both empty), `fleet_metrics/management/commands/collect_fleet_metrics.py`
- Test: `fleet_metrics/tests/test_command.py`

**Interfaces:**
- Consumes: `run_collection` (Task 12), `day_range`, `yesterday` (Task 3).
- Produces: `python manage.py collect_fleet_metrics [--day D] [--from D --to D] [--instance PK]... [--no-heartbeat]`.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_command.py`:

```python
"""``manage.py collect_fleet_metrics``: the scheduled default, backfills, and a failed run."""

from datetime import date
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from fleet_metrics.models import CollectionRun

pytestmark = pytest.mark.unit
RUN = "fleet_metrics.management.commands.collect_fleet_metrics.run_collection"


def _run(status=CollectionRun.STATUS_OK):
    return SimpleNamespace(pk=7, status=status, instance_days_written=3, instance_days_failed=0,
                           heartbeat=CollectionRun.HEARTBEAT_SENT)


class CommandTests(SimpleTestCase):
    def _call(self, *args):
        out = StringIO()
        call_command("collect_fleet_metrics", *args, stdout=out)
        return out.getvalue()

    @patch(RUN, return_value=_run())
    def test_the_scheduled_default(self, run):
        out = self._call()
        self.assertEqual(run.call_args.kwargs,
                         {"trigger": CollectionRun.TRIGGER_CRON, "days": None, "instance_pks": None, "ping": True})
        self.assertIn("run 7: ok", out)

    @patch(RUN, return_value=_run())
    def test_one_day(self, run):
        self._call("--day", "2026-10-25")
        self.assertEqual(run.call_args.kwargs["days"], [date(2026, 10, 25)])
        self.assertEqual(run.call_args.kwargs["trigger"], CollectionRun.TRIGGER_MANUAL)

    @patch(RUN, return_value=_run())
    def test_a_backfill_range_for_one_instance_without_a_ping(self, run):
        self._call("--from", "2026-10-01", "--to", "2026-10-03", "--instance", "12", "--no-heartbeat")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["days"], [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)])
        self.assertEqual((kwargs["instance_pks"], kwargs["ping"]), ([12], False))

    def test_to_without_from_is_refused(self):
        with self.assertRaises(CommandError):
            self._call("--to", "2026-10-03")

    @patch(RUN, return_value=_run(CollectionRun.STATUS_FAILED))
    def test_a_failed_run_fails_the_job(self, _run_mock):
        with self.assertRaises(CommandError):
            self._call()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_command.py -q`
Expected: `CommandError: Unknown command: 'collect_fleet_metrics'`.

- [ ] **Step 3: Write the command**

`fleet_metrics/management/commands/collect_fleet_metrics.py`:

```python
"""
Collect fleet metrics: one row per instance per UTC day (LEX-749).

Scheduled (CronJob collect-fleet-metrics, 02:30 UTC): no arguments. Collects yesterday for
every instance, catches up missing or failed days of the last 35, and pings the Better Stack
heartbeat only when yesterday's rows were written. FLEET_METRICS_SOURCES names the sources
this environment collects.

Backfill or repair (no heartbeat unless it is the scheduled form):

    python manage.py collect_fleet_metrics --from 2026-01-01 --to 2026-01-31
    python manage.py collect_fleet_metrics --day 2026-10-25 --instance 42
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from fleet_metrics.days import day_range, yesterday
from fleet_metrics.models import CollectionRun
from fleet_metrics.run import run_collection


class Command(BaseCommand):
    help = "Collect fleet metrics: one row per instance per UTC day."

    def add_arguments(self, parser):
        parser.add_argument("--day", type=date.fromisoformat, help="Collect exactly this UTC day (YYYY-MM-DD).")
        parser.add_argument("--from", dest="first", type=date.fromisoformat, help="First UTC day of a backfill.")
        parser.add_argument("--to", dest="last", type=date.fromisoformat,
                            help="Last UTC day of a backfill (default: yesterday).")
        parser.add_argument("--instance", dest="instances", type=int, action="append",
                            help="Only this instance id; repeatable.")
        parser.add_argument("--no-heartbeat", action="store_true", help="Never ping the heartbeat.")

    def handle(self, *args, **options):
        if options["last"] and not options["first"]:
            raise CommandError("--to needs --from")
        days = None
        if options["day"]:
            days = [options["day"]]
        elif options["first"]:
            days = day_range(options["first"], options["last"] or yesterday())
        trigger = CollectionRun.TRIGGER_CRON if days is None and not options["instances"] else CollectionRun.TRIGGER_MANUAL

        run = run_collection(
            trigger=trigger, days=days, instance_pks=options["instances"],
            ping=not options["no_heartbeat"],
        )
        self.stdout.write(
            f"Fleet metrics run {run.pk}: {run.status}; {run.instance_days_written} instance-day(s) written, "
            f"{run.instance_days_failed} with a failed source; heartbeat {run.heartbeat or 'not sent'}."
        )
        if run.status == CollectionRun.STATUS_FAILED:
            raise CommandError("Fleet metrics stored nothing; see the run's summary and the log.")
```

- [ ] **Step 4: Run the whole app's tests**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests -q`
Expected: every fleet_metrics test passes (about 80).

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test instance_api/tests -q`
Expected: the same 22 failures as `origin/main`, no new ones.

Run: `git grep -n "sources.prometheus\|PROMETHEUS" -- fleet_metrics`
Expected: no match. R1 carries no Prometheus dependency (spec §5.8).

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/management fleet_metrics/tests/test_command.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): the command the daily CronJob runs, and backfills"
```

---

### Task 14: The pull requests, one per release

One PR per release, R1 first. R2, R3 and R4 are follow-up PRs on top: each branch starts from `origin/main` once the previous release's PR is merged. If you start one while the previous PR is still open, branch from that PR's branch and rebase onto `origin/main` when it merges. Always open the PR against `main`, never against the previous release's branch: merging the base of a stacked PR strands the child.

- [ ] **Step 1: R1 — after Task 13**

```bash
git push -u origin feat/fleet-metrics-collector
gh pr create --base main --title "feat(fleet-metrics): release 1 — active writers, calculation reliability and version currency per instance day" --body "$(cat <<'EOF'
Fleet metrics phase 1, the collector, release 1 (LEX-749 / LEX-751). Design: lex-app `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md`, revision 2.

- New app `fleet_metrics`: one `InstanceDay` per instance per UTC day, a status per source, long-format `MetricValue`s, and a `fleet_metrics_daily` view for Grafana. An append-only `Threshold` table (add through the admin; never changed or deleted). `StartEvent` (filled from release 2) and `DeployEvent` (the slot the deploy-reliability owners fill) are created empty, so later releases need no schema change.
- Release 1 sources: each instance database (aggregates only, read-only transaction, 60 s timeout, primary-key ranges instead of date scans) for active writers (K1) and calculation runs by outcome (K3); the controller's own tables; the lex-app version (K7) through the in-cluster health service, only when `Kubectl.get_pod_readiness` reports the backend ready, with its age and the minor releases it is behind on PyPI. **No Prometheus query.**
- `FLEET_METRICS_SOURCES` (default `controller,db,version`) names the delivered sources. Others are recorded as `skipped`; the heartbeat judges delivered sources only.
- `manage.py collect_fleet_metrics` catches up missed days (35) and pings the Better Stack heartbeat only when yesterday's rows were written.
- The Grafana reader role gets SELECT on these relations only; the grant is re-applied every run.

Deployment (CronJob, heartbeat, reader role, dashboards) is in instance-controller-deployment.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: R2 — after Task 16; merge only once R1's one-week check is accepted (spec §8.1, R1.4)**

```bash
git push -u origin feat/fleet-metrics-prometheus
gh pr create --base main --title "feat(fleet-metrics): release 2 — request success, starts and startup time from Prometheus" --body "$(cat <<'EOF'
Fleet metrics phase 1, the collector, release 2 (LEX-749 / LEX-995). Builds on release 1, already on `main`.

- Prometheus source (existing series only): API requests by class (K5) on `ingress-<rid>`, path `/api(/|$)(.*)`, the controller's namespace, WebSocket upgrades excluded; backend unavailable minutes; API idle hours; backend container restarts (K6).
- One `StartEvent` per backend pod created on the day: four phase durations, outcome (ready, or failed when not ready within 600 s), the init-container reason of a failed start, and its cause (`deploy`, `resume`, `restart`, `rollout`, `scale_from_zero`, `reschedule`) read against the controller's operations in UTC.
- Off until an environment adds `prometheus` to `FLEET_METRICS_SOURCES`; days older than `FLEET_METRICS_PROMETHEUS_SINCE` are `skipped`, never zero.
- Production retention depth found: <the day from Task 1>. <Any selector or label that differed from the defaults.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: R3 — after Task 18**

```bash
git push -u origin feat/fleet-metrics-recorder-reads
gh pr create --base main --title "feat(fleet-metrics): release 3 — feature reach and active people from the usage recorder" --body "$(cat <<'EOF'
Fleet metrics phase 1, the collector, release 3 (LEX-749 / LEX-991). Builds on releases 1 and 2.

- Reads lex-app's `lex_usage_daily` (key: day, actor kind, actor, area, channel) and `lex_runtime_versions`: `active_people_1d/7d/28d` over every channel (exact K1), `area_people_28d` per area for `api`, `ui`, `ui_embed` and `ui_all` (dimension `<area>:<channel>`; `ui_all`, distinct people over both interface channels, is K2), and `editors_1d` from the audit.
- Recorder coverage per instance-day: `absent`, `enabled` or `disabled`. `recorder_silent` fails the heartbeat; `ui_silent` (API use, no interface event) is shown only.
- The runtime table becomes the first version source.
- Off until an environment adds `recorder` to `FLEET_METRICS_SOURCES`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: R4 — after Task 19; once R2's one-week check is accepted (spec §8.1, R2.2)**

```bash
git push -u origin feat/fleet-metrics-k9
gh pr create --base main --title "feat(fleet-metrics): release 4 — API response time p95" --body "$(cat <<'EOF'
Fleet metrics phase 1, the collector, release 4 (LEX-749). `api_seconds_p95` per instance-day from the ingress duration histogram, on release 2's API selection with 5xx excluded (K9). No new source: it is collected wherever `prometheus` is delivered.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Release 2 — K5, K6 (starts and startup time)

Branch `feat/fleet-metrics-prometheus` (Task 14). Nothing here is merged before R1 is accepted.

### Task 1: Confirm production's Prometheus before R2 (read-only; stage R2.0)

The selectors and series were verified live on the test cluster on 2026-10-08 (spec §2.2, §5.4.5, §5.8). This task is a verification, not a discovery: does **production** hold the same labels, and how far back? Nothing here writes code or changes a cluster, so it can run while R1 is built; spec §8.1 gives it to Infra (stage R2.0, by 30 October). If you do not have access to the production cluster, ask Infra to run the commands and paste the output.

**Files:** none. The results set `FLEET_METRICS_PROMETHEUS_SINCE` and, only if production differs from test, the selector and container defaults of Task 8.

- [ ] **Step 1: Reach Prometheus in production, read-only**

The GCP project IDs are not the display names: test is `lex-test-343008`, prod is `superb-blend-305320`; both clusters are called `lex-cluster-2` in `europe-west3`. `get-credentials` and `port-forward` change nothing in the cluster; switch back to the test context afterwards.

```bash
gcloud container clusters get-credentials lex-cluster-2 --region europe-west3 --project superb-blend-305320
kubectl config current-context   # must name superb-blend-305320
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

- [ ] **Step 2: Find the retention depth actually held**

In a second shell:

```bash
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=prometheus_tsdb_lowest_timestamp_seconds'
```

Expected: a Unix time per Prometheus replica. Confirm the series this release reads reach that far, at one day after it:

```bash
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=count(nginx_ingress_controller_requests)' --data-urlencode 'time=<lowest + 86400>'
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=count(kube_pod_created)' --data-urlencode 'time=<lowest + 86400>'
```

Expected: both answer with a count. The oldest complete UTC day is the first day that starts after the lowest timestamp and on which both answer. If the first query returns nothing, find the day by halving: query `count(up)` at earlier and later times until it stops answering.

- [ ] **Step 3: Confirm the API selector on one production instance**

With `<rid>` the `resource_identifier` of a busy running production instance:

```bash
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=count by (host, exported_namespace) (nginx_ingress_controller_requests{ingress="ingress-<rid>",path="/api(/|$)(.*)",status!="101"})'
```

Expected: one row whose `exported_namespace` is the production controller's namespace (`K8S_NAMESPACE`) and whose `host` is the instance's own. If two hosts appear, another host shares the API rule: the selector of Task 8 then needs a `host` matcher too (record it in the R2 PR).

- [ ] **Step 4: Confirm the replica, lifecycle, owner and restart series**

```bash
for m in kube_pod_created kube_pod_status_scheduled_time kube_pod_status_initialized_time \
         kube_pod_container_state_started kube_pod_status_ready_time kube_pod_owner \
         kube_pod_container_status_restarts_total kube_pod_init_container_status_waiting_reason \
         kube_pod_init_container_status_terminated_reason; do
  echo "== $m"
  curl -s 'http://localhost:9090/api/v1/query' --data-urlencode "query=$m{pod=~\"lex-backend-<rid>-.*\"}" | python3 -m json.tool | head -30
done
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=kube_replicaset_created{replicaset=~"lex-backend-<rid>-.*"}'
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=kube_deployment_spec_replicas{deployment="lex-backend-<rid>"}'
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=kube_deployment_status_replicas_available{deployment="lex-backend-<rid>"}'
```

Expected: the five lifecycle timestamps for the running pod; `kube_pod_owner` with `owner_kind="ReplicaSet"`; one series each for the replica gauges (for a dev-type instance, try `lex-devenv-<rid>`). The two init-container reason series exist only while a reason applies, so they may be empty.

Confirm, as found on test (13 pods, 2026-10-08), that `kube_pod_container_state_started{container="django-backend"}` exists for the pod and lies between its initialized and ready times; a different container name becomes `fleet_metrics_backend_container`'s default.

- [ ] **Step 5: Record the findings**

The oldest day goes on LEX-995 (spec §8.1, R2.0) and becomes `FLEET_METRICS_PROMETHEUS_SINCE` in the infrastructure plan. Any label or container that differs from Task 8's defaults becomes that default, with one line in the R2 PR description.

---

### Task 8: Platform metrics from Prometheus (K5, K6)

**Files:**
- Create: `fleet_metrics/sources/prometheus.py`
- Test: `fleet_metrics/tests/test_prometheus.py`
- Modify: `lex_instance_controller_backend/settings.py` (R2 settings), `fleet_metrics/catalog.py` (append)

**Interfaces:**
- Produces: settings `fleet_metrics_prometheus_retention_days`, `fleet_metrics_prometheus_since`, `fleet_metrics_api_path`, `fleet_metrics_namespace_label`, `fleet_metrics_backend_container`; `catalog.STATUS_CLASSES`, `catalog.UNAVAILABLE_CODES`; `PrometheusError`; `Prometheus(query_url: str, *, session=None, timeout=30.0)` with `instant(query, at) -> list[dict]` and `range(query, start, end, step_seconds) -> list[dict]`; `api_selector(instance, namespace: str) -> str`; `backend_deployment(instance) -> str`; `backend_pods(instance) -> str`; `deployment_matchers(instance, namespace: str) -> str`; `status_class(code: str) -> Optional[str]`; `platform_metrics(client, instance, window, namespace) -> list[Value]` — `api_requests{class}`, `backend_unavailable_minutes`, `api_idle_hours`, `backend_container_restarts`.

- [ ] **Step 1: Add the settings and the response classes**

In `lex_instance_controller_backend/settings.py`, after the fleet-metrics block of Task 2:

```python
# Fleet metrics, release 2: the Prometheus source (spec §5.4.5). Collector plan, Task 1,
# confirmed these on production. Days before FLEET_METRICS_PROMETHEUS_SINCE (YYYY-MM-DD, the
# oldest day production holds) are skipped, never read as zero.
fleet_metrics_prometheus_retention_days = int(os.getenv("FLEET_METRICS_PROMETHEUS_RETENTION_DAYS", "180"))
fleet_metrics_prometheus_since = os.getenv("FLEET_METRICS_PROMETHEUS_SINCE", "")
fleet_metrics_api_path = os.getenv("FLEET_METRICS_API_PATH", "/api(/|$)(.*)")
fleet_metrics_namespace_label = os.getenv("FLEET_METRICS_NAMESPACE_LABEL", "exported_namespace")
# The backend pod's application container: its restarts (K6) and its start time (K6 phases).
fleet_metrics_backend_container = os.getenv("FLEET_METRICS_BACKEND_CONTAINER", "django-backend")
```

Append to `fleet_metrics/catalog.py`:

```python
# Response classes (K5, R2). 502/503/504 mean the backend was down or starting (spec §4.2 K5).
STATUS_CLASSES = ("2xx", "3xx", "4xx", "5xx_unavailable", "5xx_app")
UNAVAILABLE_CODES = frozenset({"502", "503", "504"})
```

- [ ] **Step 2: Write the failing tests**

`fleet_metrics/tests/test_prometheus.py`:

```python
"""Request classes, unavailability, idle hours and restarts, from Prometheus, for one instance and UTC day."""

import re
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from fleet_metrics.days import window_for
from fleet_metrics.sources import prometheus
from lex_instance_controller_backend import settings as ic_settings

pytestmark = pytest.mark.unit
DAY = window_for(date(2026, 10, 25))
INSTANCE = SimpleNamespace(resource_identifier="books-prod-1", type="prod")


class FakeClient:
    """Answers by the first query fragment that matches; records every query."""

    def __init__(self, instant=None, ranges=None):
        self.instant_answers = instant or {}
        self.range_answers = ranges or {}
        self.queries = []

    def instant(self, query, at):
        self.queries.append(("instant", query, at))
        return next((v for k, v in self.instant_answers.items() if k in query), [])

    def range(self, query, start, end, step_seconds):
        self.queries.append(("range", query, start, end, step_seconds))
        return next((v for k, v in self.range_answers.items() if k in query), [])


def vector(status, value):
    return {"metric": {"status": status}, "value": [0, str(value)]}


def scalar(value):
    return {"metric": {}, "value": [0, str(value)]}


def series(values, step=60):
    return {"metric": {}, "values": [[i * step, str(v)] for i, v in enumerate(values)]}


def collect(client):
    return {(m, d): v for m, d, v in prometheus.platform_metrics(client, INSTANCE, DAY, "default")}


class SelectorTests(SimpleTestCase):
    def test_api_traffic_is_the_ingress_api_rule_in_the_namespace_without_websockets(self):
        with patch.object(ic_settings, "fleet_metrics_api_path", "/api(/|$)(.*)"), \
                patch.object(ic_settings, "fleet_metrics_namespace_label", "exported_namespace"):
            self.assertEqual(
                prometheus.api_selector(INSTANCE, "default"),
                '{ingress="ingress-books-prod-1",path="/api(/|$)(.*)",status!="101",exported_namespace="default"}',
            )

    def test_dev_instances_run_a_devenv_deployment(self):
        self.assertEqual(prometheus.backend_deployment(INSTANCE), "lex-backend-books-prod-1")
        self.assertEqual(
            prometheus.backend_deployment(SimpleNamespace(resource_identifier="x-dev-2", type="dev")),
            "lex-devenv-x-dev-2",
        )

    def test_the_pod_pattern_stops_at_the_deployment(self):
        pattern = re.compile(prometheus.backend_pods(SimpleNamespace(resource_identifier="books", type="prod")))
        self.assertTrue(pattern.fullmatch("lex-backend-books-7d9c8b6f5d-x2k4q"))
        self.assertFalse(pattern.fullmatch("lex-backend-books-prod-7d9c8b6f5d-x2k4q"))

    def test_status_classes(self):
        self.assertEqual(
            [prometheus.status_class(code) for code in ("200", "304", "499", "502", "503", "504", "500", "101")],
            ["2xx", "3xx", "4xx", "5xx_unavailable", "5xx_unavailable", "5xx_unavailable", "5xx_app", None],
        )


class PlatformMetricsTests(SimpleTestCase):
    def test_requests_by_class(self):
        values = collect(FakeClient(instant={
            "nginx_ingress_controller_requests": [vector("200", 900), vector("503", 12), vector("500", 3)],
        }))
        self.assertEqual(values[("api_requests", "2xx")], 900.0)
        self.assertEqual(values[("api_requests", "5xx_unavailable")], 12.0)
        self.assertEqual(values[("api_requests", "5xx_app")], 3.0)
        self.assertEqual(values[("api_requests", "4xx")], 0.0, "explicit zero, not a missing row")

    def test_an_absent_series_on_an_answered_query_is_zero(self):
        values = collect(FakeClient())
        self.assertEqual(values[("api_requests", "2xx")], 0.0)
        self.assertEqual(values[("backend_unavailable_minutes", "")], 0.0)
        self.assertEqual(values[("api_idle_hours", "")], 24.0)
        self.assertEqual(values[("backend_container_restarts", "")], 0.0)

    def test_unavailable_minutes_count_only_when_a_replica_was_wanted(self):
        # Six minutes. Wanted 1,1,1,0,0,1; available 0,1,0,0,0,0: down at minutes 0, 2 and 5.
        # Minutes 3 and 4 were paused, not unavailable.
        values = collect(FakeClient(ranges={
            "kube_deployment_spec_replicas": [series([1, 1, 1, 0, 0, 1])],
            "kube_deployment_status_replicas_available": [series([0, 1, 0, 0, 0, 0])],
        }))
        self.assertEqual(values[("backend_unavailable_minutes", "")], 3.0)

    def test_idle_hours(self):
        hourly = [0] * 20 + [5, 0, 12, 3]
        values = collect(FakeClient(ranges={"nginx_ingress_controller_requests": [series(hourly, step=3600)]}))
        self.assertEqual(values[("api_idle_hours", "")], 21.0)

    def test_container_restarts_of_the_backend_container(self):
        client = FakeClient(instant={"kube_pod_container_status_restarts_total": [scalar(2)]})
        with patch.object(ic_settings, "fleet_metrics_backend_container", "django-backend"):
            values = collect(client)
        self.assertEqual(values[("backend_container_restarts", "")], 2.0)
        query = next(q[1] for q in client.queries if "restarts_total" in q[1])
        self.assertIn('container="django-backend"', query)
        self.assertIn('pod=~"lex-backend-books-prod-1-[a-z0-9]+-[a-z0-9]+"', query)

    def test_every_query_is_evaluated_at_the_end_of_the_day(self):
        client = FakeClient()
        collect(client)
        self.assertEqual({q[2] for q in client.queries if q[0] == "instant"}, {DAY.end})
        minute_ranges = {(q[2], q[3]) for q in client.queries if q[0] == "range" and q[4] == 60}
        self.assertEqual(minute_ranges, {(DAY.start, DAY.end - timedelta(seconds=60))})
        hour_range = next(q for q in client.queries if q[0] == "range" and q[4] == 3600)
        self.assertEqual((hour_range[2], hour_range[3]), (DAY.start + timedelta(hours=1), DAY.end))


class ClientTests(SimpleTestCase):
    def test_the_range_endpoint_is_derived_from_the_query_endpoint(self):
        client = prometheus.Prometheus("http://prom.monitoring:9090/api/v1/query")
        self.assertEqual(client.range_url, "http://prom.monitoring:9090/api/v1/query_range")

    def test_a_failed_query_raises(self):
        class Session:
            def get(self, url, params, timeout):
                return SimpleNamespace(raise_for_status=lambda: None,
                                       json=lambda: {"status": "error", "error": "bad query"})

        client = prometheus.Prometheus("http://prom/api/v1/query", session=Session())
        with self.assertRaises(prometheus.PrometheusError):
            client.instant("up", DAY.end)
```

- [ ] **Step 3: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_prometheus.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.sources.prometheus'`.

- [ ] **Step 4: Write the module**

`fleet_metrics/sources/prometheus.py`:

```python
"""Platform metrics for one instance and UTC day, from the in-cluster Prometheus (spec §5.4.5). Release 2.

Every query is evaluated at the end of the day. API traffic is the instance's ingress on the
``/api`` rule in the controller's namespace, without WebSocket upgrades (status 101, whose
"duration" is the connection's lifetime): ``ingress-<rid>`` alone would also carry the
single-page app, Streamlit, Flower and the docs host. The ingress controller creates a series
only when traffic arrives, so an absent series on an answered query is zero, not a gap.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import requests

from fleet_metrics import catalog
from fleet_metrics.days import DayWindow
from lex_instance_controller_backend import settings

Value = tuple[str, str, float]
_MINUTE = 60
_HOUR = 3600


class PrometheusError(RuntimeError):
    """Prometheus answered, but not with a result."""


class Prometheus:
    """A minimal client for the HTTP API; ``query_url`` is the controller's PROMETHEUS_HOST."""

    def __init__(self, query_url: str, *, session=None, timeout: float = 30.0) -> None:
        if not query_url:
            raise PrometheusError("PROMETHEUS_HOST is not set")
        self.query_url = query_url
        self.range_url = query_url.rsplit("/", 1)[0] + "/query_range"
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, url: str, params: dict) -> list[dict]:
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success":
            raise PrometheusError(body.get("error") or "query failed")
        return body["data"]["result"]

    def instant(self, query: str, at: datetime) -> list[dict]:
        return self._get(self.query_url, {"query": query, "time": at.timestamp()})

    def range(self, query: str, start: datetime, end: datetime, step_seconds: int) -> list[dict]:
        return self._get(self.range_url, {
            "query": query, "start": start.timestamp(), "end": end.timestamp(), "step": step_seconds,
        })


def api_selector(instance, namespace: str) -> str:
    matchers = [
        f'ingress="ingress-{instance.resource_identifier}"',
        f'path="{settings.fleet_metrics_api_path}"',
        'status!="101"',
    ]
    if namespace and settings.fleet_metrics_namespace_label:
        matchers.append(f'{settings.fleet_metrics_namespace_label}="{namespace}"')
    return "{" + ",".join(matchers) + "}"


def backend_deployment(instance) -> str:
    prefix = "lex-devenv" if instance.type == "dev" else "lex-backend"
    return f"{prefix}-{instance.resource_identifier}"


def backend_pods(instance) -> str:
    """The backend's pod names, ``<deployment>-<pod-template-hash>-<suffix>``, as a regular expression.

    Exactly two segments after the deployment's name, so ``lex-backend-books`` never matches
    the pods of ``lex-backend-books-prod``.
    """
    return f"{backend_deployment(instance)}-[a-z0-9]+-[a-z0-9]+"


def deployment_matchers(instance, namespace: str) -> str:
    matchers = [f'deployment="{backend_deployment(instance)}"']
    if namespace:
        matchers.insert(0, f'namespace="{namespace}"')
    return ",".join(matchers)


def status_class(code: str) -> Optional[str]:
    if code in catalog.UNAVAILABLE_CODES:
        return "5xx_unavailable"
    if code.startswith("5"):
        return "5xx_app"
    if code[:1] in ("2", "3", "4"):
        return f"{code[0]}xx"
    return None


def _samples(result: list[dict]) -> dict[float, float]:
    """A range result's first series as {unix time: value}; empty when the series is absent."""
    if not result:
        return {}
    return {float(t): float(v) for t, v in result[0]["values"]}


def platform_metrics(client, instance, window: DayWindow, namespace: str) -> list[Value]:
    """K5 (requests by class, unavailable minutes) and K6 (idle hours, container restarts)."""
    selector = api_selector(instance, namespace)

    counts = {cls: 0.0 for cls in catalog.STATUS_CLASSES}
    requests_query = f"sum by (status) (increase(nginx_ingress_controller_requests{selector}[1d]))"
    for series in client.instant(requests_query, window.end):
        cls = status_class(series["metric"].get("status", ""))
        if cls is not None:
            counts[cls] += float(series["value"][1])
    values: list[Value] = [("api_requests", cls, value) for cls, value in counts.items()]

    # Minutes the backend should have run (spec replicas >= 1) with none available.
    deployment = deployment_matchers(instance, namespace)
    last_minute = window.end - timedelta(seconds=_MINUTE)
    wanted = _samples(client.range(
        f"max(kube_deployment_spec_replicas{{{deployment}}})", window.start, last_minute, _MINUTE))
    available = _samples(client.range(
        f"max(kube_deployment_status_replicas_available{{{deployment}}})", window.start, last_minute, _MINUTE))
    unavailable = sum(1 for moment, want in wanted.items() if want >= 1 and available.get(moment) == 0)
    values.append(("backend_unavailable_minutes", "", float(unavailable)))

    # Hours of the day without one API request: each point is the hour that ends at it.
    hourly = _samples(client.range(
        f"sum(increase(nginx_ingress_controller_requests{selector}[1h]))",
        window.start + timedelta(seconds=_HOUR), window.end, _HOUR,
    ))
    busy = sum(1 for value in hourly.values() if value > 0)
    values.append(("api_idle_hours", "", float(24 - busy)))

    # Restarts inside running backend pods; they are not starts (spec §4.2 K6).
    restart_matchers = [f'pod=~"{backend_pods(instance)}"']
    if namespace:
        restart_matchers.insert(0, f'namespace="{namespace}"')
    if settings.fleet_metrics_backend_container:
        restart_matchers.append(f'container="{settings.fleet_metrics_backend_container}"')
    restarts = client.instant(
        "sum(increase(kube_pod_container_status_restarts_total{" + ",".join(restart_matchers) + "}[1d]))",
        window.end,
    )
    values.append(("backend_container_restarts", "", float(restarts[0]["value"][1]) if restarts else 0.0))
    return values
```

- [ ] **Step 5: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_prometheus.py -q`
Expected: `12 passed`.

- [ ] **Step 6: Commit**

```bash
git add lex_instance_controller_backend/settings.py fleet_metrics/catalog.py fleet_metrics/sources/prometheus.py fleet_metrics/tests/test_prometheus.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): request classes, unavailable minutes, idle hours and restarts per instance day"
```

---

### Task 15: Backend starts, their phases and their causes (K6)

A start is a new backend pod. Its four phases come from kube-state-metrics, joined on `(namespace, pod)` (spec §4.2 K6):
- *scheduling* = scheduled − created (`kube_pod_status_scheduled_time`, `kube_pod_created`);
- *preparation* = initialized − scheduled (`kube_pod_status_initialized_time`): secret injection and migrations;
- *container start* = container started − initialized, where container started is `max by (namespace, pod) (kube_pod_container_state_started{container="django-backend", pod=~"lex-backend-<rid>-…"})`, first observed value in the day's range;
- *application ready* = ready − container started (`kube_pod_status_ready_time`).

`kube_pod_status_container_ready_time` is not used: on the test cluster it equals `kube_pod_status_ready_time` on every backend pod (13 checked), so it cannot mark the container start. A start is **failed** when it is not ready within the 600-second startup budget, or never. The cause is tried in the spec's order against the controller's operations, read in SQL as UTC.

**Files:**
- Create: `fleet_metrics/sources/starts.py`
- Modify: `fleet_metrics/sources/controller.py` (append), `fleet_metrics/catalog.py` (append)
- Test: `fleet_metrics/tests/test_starts.py`; `fleet_metrics/tests/test_controller.py` (append)

**Interfaces:**
- Consumes: `backend_deployment`, `backend_pods`, `deployment_matchers` and the setting `fleet_metrics_backend_container` (default `django-backend`) (Task 8).
- Produces: `catalog.CAUSE_*`, `catalog.START_CAUSES`, `catalog.START_CAUSE_BY_OPERATION`, `catalog.START_READY`, `catalog.START_FAILED`; `controller.Operation(operation_type, started, completed)`; `controller.operations_during(instance, window) -> list[Operation]`; `starts.PodStart` with `outcome` and `phases() -> dict[str, Optional[float]]`; `starts.pod_starts(client, instance, window, namespace) -> list[PodStart]`; `starts.classify(start, operations, *, from_zero: Callable[[], bool]) -> str`; `starts.replicas_before(client, instance, namespace, moment) -> Optional[float]`; `starts.classified_starts(client, instance, window, namespace, operations, *, autoscaling: bool) -> list[tuple[PodStart, str]]`; `starts.start_metrics(classified) -> list[Value]` — `backend_starts{cause}` and `backend_failed_starts`.

- [ ] **Step 1: Add the causes to the catalogue**

Append to `fleet_metrics/catalog.py`:

```python
# Why a backend started (K6, R2), in the order they are tried (spec §4.2 K6).
CAUSE_DEPLOY = "deploy"
CAUSE_RESUME = "resume"
CAUSE_RESTART = "restart"
CAUSE_ROLLOUT = "rollout"
CAUSE_SCALE_FROM_ZERO = "scale_from_zero"
CAUSE_RESCHEDULE = "reschedule"
START_CAUSES = (CAUSE_DEPLOY, CAUSE_RESUME, CAUSE_RESTART, CAUSE_ROLLOUT, CAUSE_SCALE_FROM_ZERO, CAUSE_RESCHEDULE)

#: The controller operation running when a pod was created, and the cause it gives the start.
START_CAUSE_BY_OPERATION = {
    **{kind: CAUSE_DEPLOY for kind in DEPLOY_OPERATION_TYPES},
    "RESUME": CAUSE_RESUME,
    "RESTART": CAUSE_RESTART,
}

START_READY = "ready"
START_FAILED = "failed"
```

- [ ] **Step 2: Write the failing tests**

`fleet_metrics/tests/test_starts.py`:

```python
"""Backend starts: the four phases, a failed start, and why each start happened (spec §4.2 K6)."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from fleet_metrics import catalog
from fleet_metrics.days import window_for
from fleet_metrics.sources import starts as starts_mod
from fleet_metrics.sources.controller import Operation
from instance_api.models.enums import OperationType
from lex_instance_controller_backend import settings as ic_settings

pytestmark = pytest.mark.unit
DAY = window_for(date(2026, 10, 25))
T = datetime(2026, 10, 25, 9, tzinfo=timezone.utc)
INSTANCE = SimpleNamespace(resource_identifier="books-prod-1", type="prod")
REPLICASET = "lex-backend-books-prod-1-7d9c8b6f5d"
POD = f"{REPLICASET}-x2k4q"


def at(seconds):
    return T + timedelta(seconds=seconds)


def stamp(pod, moment):
    """A kube-state-metrics timestamp series, as Prometheus answers it."""
    return {"metric": {"namespace": "default", "pod": pod}, "value": [0, str(moment.timestamp())]}


def labelled(value, **labels):
    return {"metric": {"namespace": "default", **labels}, "value": [0, str(value)]}


class FakeClient:
    """Answers instant queries by the first metric name the query contains."""

    def __init__(self, answers):
        self.answers = answers
        self.queries = []

    def instant(self, query, at):
        self.queries.append(("instant", query, at))
        return next((v for k, v in self.answers.items() if k in query), [])


def recorded(**overrides):
    """Prometheus' answers for one backend pod created at 09:00 UTC and ready 315 s later."""
    answers = {
        "kube_pod_created": [stamp(POD, at(0)), stamp(f"{REPLICASET}-old01", T - timedelta(days=3))],
        "kube_pod_status_scheduled_time": [stamp(POD, at(2))],
        "kube_pod_status_initialized_time": [stamp(POD, at(146))],
        "kube_pod_container_state_started": [stamp(POD, at(250))],
        "kube_pod_status_ready_time": [stamp(POD, at(315))],
        "kube_pod_owner": [labelled(1, pod=POD, owner_kind="ReplicaSet", owner_name=REPLICASET)],
        "kube_replicaset_created": [labelled((T - timedelta(days=2)).timestamp(), replicaset=REPLICASET)],
    }
    answers.update(overrides)
    return FakeClient(answers)


class PodStartTests(SimpleTestCase):
    def test_a_start_and_its_four_phases(self):
        found = starts_mod.pod_starts(recorded(), INSTANCE, DAY, "default")
        self.assertEqual([s.pod for s in found], [POD], "a pod created three days earlier is not today's start")
        self.assertEqual(found[0].outcome, catalog.START_READY)
        self.assertEqual(found[0].phases(), {
            "scheduling_seconds": 2.0, "preparation_seconds": 144.0,
            "container_start_seconds": 104.0, "application_ready_seconds": 65.0,
        })

    def test_a_start_that_never_became_ready_failed_and_keeps_its_reason(self):
        client = recorded(
            kube_pod_status_initialized_time=[], kube_pod_container_state_started=[],
            kube_pod_status_ready_time=[],
            kube_pod_init_container_status_waiting_reason=[
                labelled(1, pod=POD, container="django-migrate", reason="CrashLoopBackOff")],
        )
        start = starts_mod.pod_starts(client, INSTANCE, DAY, "default")[0]
        self.assertEqual(start.outcome, catalog.START_FAILED)
        self.assertEqual(start.failure_reason, "CrashLoopBackOff in django-migrate")
        self.assertEqual(start.phases()["scheduling_seconds"], 2.0)
        self.assertIsNone(start.phases()["preparation_seconds"])

    def test_ready_after_the_startup_budget_is_a_failed_start(self):
        client = recorded(kube_pod_status_ready_time=[stamp(POD, at(601))])
        self.assertEqual(starts_mod.pod_starts(client, INSTANCE, DAY, "default")[0].outcome, catalog.START_FAILED)

    def test_the_container_start_is_the_backend_containers_first_start_in_the_range(self):
        client = recorded()
        with patch.object(ic_settings, "fleet_metrics_backend_container", "django-backend"):
            starts_mod.pod_starts(client, INSTANCE, DAY, "default")
        query = next(q[1] for q in client.queries if "kube_pod_container_state_started" in q[1])
        self.assertTrue(query.startswith("max by (namespace, pod) (min_over_time(kube_pod_container_state_started{"))
        self.assertIn('container="django-backend"', query)
        self.assertFalse(any("container_ready_time" in q[1] for q in client.queries),
                         "ContainersReady equals Ready on every backend pod, so it marks nothing")

    def test_a_day_without_a_new_pod_costs_one_query(self):
        client = FakeClient({})
        self.assertEqual(starts_mod.pod_starts(client, INSTANCE, DAY, "default"), [])
        self.assertEqual(len(client.queries), 1)

    def test_lifecycle_series_are_read_past_the_end_of_the_day(self):
        client = recorded()
        starts_mod.pod_starts(client, INSTANCE, DAY, "default")
        self.assertEqual({q[2] for q in client.queries}, {DAY.end + starts_mod.LOOKAHEAD})
        self.assertIn("[87300s]", client.queries[0][1], "24 hours plus the 15-minute lookahead")


OPERATIONS = [
    Operation("PAUSE", at(-120), at(5000)),     # pausing starts nothing
    Operation("RELEASE", at(-60), at(400)),
    Operation("RESTART", at(1000), at(1100)),
    Operation("RESUME", at(2000), None),         # still running
]


class ClassifyTests(SimpleTestCase):
    def _start(self, created, replicaset_created=None):
        return starts_mod.PodStart(namespace="default", pod=POD, created=created,
                                   replicaset_created=replicaset_created)

    def test_the_operation_running_at_creation_decides(self):
        for created, cause in ((at(0), catalog.CAUSE_DEPLOY), (at(1050), catalog.CAUSE_RESTART),
                               (at(3000), catalog.CAUSE_RESUME)):
            with self.subTest(created=created):
                self.assertEqual(starts_mod.classify(self._start(created), OPERATIONS, from_zero=lambda: True), cause)

    def test_without_an_operation_a_new_replicaset_is_a_rollout(self):
        start = self._start(at(600), replicaset_created=at(590))
        self.assertEqual(starts_mod.classify(start, [], from_zero=lambda: True), catalog.CAUSE_ROLLOUT)

    def test_then_scale_from_zero_then_reschedule(self):
        old = at(600) - timedelta(days=2)
        self.assertEqual(starts_mod.classify(self._start(at(600), old), [], from_zero=lambda: True),
                         catalog.CAUSE_SCALE_FROM_ZERO)
        self.assertEqual(starts_mod.classify(self._start(at(600), old), [], from_zero=lambda: False),
                         catalog.CAUSE_RESCHEDULE)

    def test_replicas_are_asked_only_when_nothing_else_explains_the_start(self):
        asked = []
        starts_mod.classify(self._start(at(0)), OPERATIONS, from_zero=lambda: asked.append(1) or True)
        self.assertEqual(asked, [])

    def test_every_operation_that_starts_a_backend_exists_in_the_controller(self):
        known = {member.value for member in OperationType}
        self.assertLessEqual(set(catalog.START_CAUSE_BY_OPERATION), known)
        self.assertNotIn("PAUSE", catalog.START_CAUSE_BY_OPERATION)


class ClassifiedStartsTests(SimpleTestCase):
    def _client(self):
        return recorded(kube_replicaset_created=[], kube_deployment_spec_replicas=[labelled(0)])

    def test_an_autoscaled_backend_with_no_replica_before_scaled_from_zero(self):
        client = self._client()
        [(start, cause)] = starts_mod.classified_starts(client, INSTANCE, DAY, "default", [], autoscaling=True)
        self.assertEqual(cause, catalog.CAUSE_SCALE_FROM_ZERO)
        asked = next(q for q in client.queries if "kube_deployment_spec_replicas" in q[1])
        self.assertEqual(asked[2], start.created - timedelta(minutes=1))

    def test_a_backend_that_does_not_autoscale_was_rescheduled(self):
        client = self._client()
        [(_, cause)] = starts_mod.classified_starts(client, INSTANCE, DAY, "default", [], autoscaling=False)
        self.assertEqual(cause, catalog.CAUSE_RESCHEDULE)
        self.assertFalse(any("kube_deployment_spec_replicas" in q[1] for q in client.queries))


class StartMetricsTests(SimpleTestCase):
    def test_counts_by_cause_with_explicit_zeros(self):
        ready = starts_mod.PodStart("default", POD, at(0), ready=at(300))
        failed = starts_mod.PodStart("default", f"{REPLICASET}-y7z8w", at(10))
        values = {(m, d): v for m, d, v in starts_mod.start_metrics(
            [(ready, catalog.CAUSE_DEPLOY), (failed, catalog.CAUSE_DEPLOY)])}
        self.assertEqual(values[("backend_starts", catalog.CAUSE_DEPLOY)], 2.0)
        self.assertEqual(values[("backend_starts", catalog.CAUSE_RESCHEDULE)], 0.0)
        self.assertEqual(values[("backend_failed_starts", "")], 1.0)
```

Append to `fleet_metrics/tests/test_controller.py` (add `timezone` to its `datetime` import):

```python
class OperationsDuringTests(TestCase):
    """Start causes need the controller's operations in UTC, read past the naive Berlin ORM (spec §6, rule 4)."""

    def setUp(self):
        self.instance = make_instance()

    def test_the_day_summer_time_ends(self):
        # 01:30 CEST on 25 Oct is 23:30 UTC on 24 Oct; 04:00 CET is 03:00 UTC; 09:00 CET is 08:00 UTC.
        op(self.instance, "RESTART", "SUCCESS", datetime(2026, 10, 25, 1, 30), datetime(2026, 10, 25, 4, 0))
        op(self.instance, "RELEASE", "RUNNING", datetime(2026, 10, 25, 9, 0), None)
        op(self.instance, "DB_EXPORT", "SUCCESS", datetime(2026, 10, 25, 10, 0), datetime(2026, 10, 25, 10, 5))
        found = controller.operations_during(self.instance, window_for(date(2026, 10, 25)))
        self.assertEqual(found, [
            controller.Operation("RESTART", datetime(2026, 10, 24, 23, 30, tzinfo=timezone.utc),
                                 datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)),
            controller.Operation("RELEASE", datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc), None),
        ])

    def test_the_day_summer_time_starts(self):
        # 28 Mar 2027: 01:30 CET is 00:30 UTC; 03:30 CEST is 01:30 UTC.
        op(self.instance, "RESUME", "SUCCESS", datetime(2027, 3, 28, 1, 30), datetime(2027, 3, 28, 3, 30))
        found = controller.operations_during(self.instance, window_for(date(2027, 3, 28)))
        self.assertEqual(found, [controller.Operation(
            "RESUME", datetime(2027, 3, 28, 0, 30, tzinfo=timezone.utc), datetime(2027, 3, 28, 1, 30, tzinfo=timezone.utc),
        )])
```

- [ ] **Step 3: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_starts.py fleet_metrics/tests/test_controller.py -q`
Expected: `ImportError: cannot import name 'Operation'` (and `No module named 'fleet_metrics.sources.starts'`).

- [ ] **Step 4: Append the operations reader**

In `fleet_metrics/sources/controller.py`, add to the imports `from datetime import datetime, timedelta, timezone`, then append:

```python
@dataclass(frozen=True)
class Operation:
    """A controller operation, with aware UTC times."""

    operation_type: str
    started: datetime
    completed: Optional[datetime]


#: An operation started this long before the day can still be running when a pod is created.
OPERATION_LOOKBACK = timedelta(days=1)

_OPERATIONS_SQL = f"""
SELECT operation_type, started_at AT TIME ZONE 'UTC', completed_at AT TIME ZONE 'UTC'
FROM {InstanceOperation._meta.db_table}
WHERE instance_id = %s
  AND operation_type = ANY(%s)
  AND started_at >= %s AND started_at < %s
  AND (completed_at IS NULL OR completed_at >= %s)
ORDER BY started_at, id
"""


def operations_during(instance, window: DayWindow) -> list[Operation]:
    """The instance's operations of a start-causing kind that overlap the UTC day (spec §4.2 K6).

    Times are read in SQL as ``… AT TIME ZONE 'UTC'`` and made aware here, never through the
    ORM's naive Berlin values (spec §6, rule 4).
    """
    with connection.cursor() as cursor:
        cursor.execute(_OPERATIONS_SQL, [
            instance.pk, list(catalog.START_CAUSE_BY_OPERATION),
            window.start - OPERATION_LOOKBACK, window.end, window.start,
        ])
        rows = cursor.fetchall()
    return [
        Operation(
            operation_type=kind,
            started=started.replace(tzinfo=timezone.utc),
            completed=completed.replace(tzinfo=timezone.utc) if completed is not None else None,
        )
        for kind, started, completed in rows
    ]
```

- [ ] **Step 5: Write the starts module**

`fleet_metrics/sources/starts.py`:

```python
"""Backend starts (K6, release 2): one per backend pod created on the UTC day (spec §4.2 K6, §5.4.5).

A start is a new backend pod (``kube_pod_created``). Its startup runs from creation to the pod's
``Ready`` condition, in four phases taken from kube-state-metrics' condition times and the
backend container's start time, joined on ``(namespace, pod)``. A start not ready within the
600-second startup budget, or never, has failed; its init-container reason is kept. The series
are read up to a lookahead past the day, so a pod created just before midnight is judged with
its full budget.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from fleet_metrics import catalog
from fleet_metrics.days import DayWindow
from fleet_metrics.sources.controller import Operation
from fleet_metrics.sources.prometheus import backend_deployment, backend_pods, deployment_matchers
from lex_instance_controller_backend import settings

Value = tuple[str, str, float]
Key = tuple[str, str]   # (namespace, pod)

#: The backend's startup probe: 120 × 5 s on /health (spec §2.1).
STARTUP_BUDGET = timedelta(seconds=600)
#: A pod created at 23:59 has until 00:09 to become ready, and needs a scrape to show it.
LOOKAHEAD = timedelta(minutes=15)
#: A pod created this soon after its ReplicaSet belongs to a new ReplicaSet: a rollout.
NEW_REPLICASET = timedelta(minutes=2)

#: The pod's condition times (spec §2.2), by the PodStart field they fill. The container start
#: comes from ``kube_pod_container_state_started`` instead (see :func:`pod_starts`).
#: ``kube_pod_status_container_ready_time`` is not used: it equals the ready time on every
#: backend pod checked on the test cluster, so it marks nothing.
_CONDITIONS = {
    "scheduled": "kube_pod_status_scheduled_time",
    "initialized": "kube_pod_status_initialized_time",
    "ready": "kube_pod_status_ready_time",
}


@dataclass(frozen=True)
class PodStart:
    namespace: str
    pod: str
    created: datetime
    scheduled: Optional[datetime] = None
    initialized: Optional[datetime] = None
    container_started: Optional[datetime] = None
    ready: Optional[datetime] = None
    replicaset_created: Optional[datetime] = None
    failure_reason: str = ""

    @property
    def outcome(self) -> str:
        if self.ready is not None and self.ready - self.created <= STARTUP_BUDGET:
            return catalog.START_READY
        return catalog.START_FAILED

    def phases(self) -> dict[str, Optional[float]]:
        """The four phases in seconds (spec §4.2 K6); None where a start never reached a boundary.

        scheduling = scheduled − created; preparation = initialized − scheduled;
        container start = container started − initialized; application ready = ready − container started.
        """
        def between(first: Optional[datetime], last: Optional[datetime]) -> Optional[float]:
            return (last - first).total_seconds() if first is not None and last is not None else None

        return {
            "scheduling_seconds": between(self.created, self.scheduled),
            "preparation_seconds": between(self.scheduled, self.initialized),
            "container_start_seconds": between(self.initialized, self.container_started),
            "application_ready_seconds": between(self.container_started, self.ready),
        }


def _when(series: dict) -> datetime:
    return datetime.fromtimestamp(float(series["value"][1]), tz=timezone.utc)


def _key(labels: dict) -> Key:
    return labels.get("namespace", ""), labels.get("pod", "")


def _times(result: list[dict]) -> dict[Key, datetime]:
    return {_key(series["metric"]): _when(series) for series in result}


def _reasons(result: list[dict]) -> dict[Key, str]:
    """Init-container reasons by pod, as ``<reason> in <container>``. ``Completed`` is no failure."""
    reasons: dict[Key, str] = {}
    for series in result:
        labels = series["metric"]
        if labels.get("reason") in (None, "", "Completed"):
            continue
        reasons.setdefault(_key(labels), f'{labels["reason"]} in {labels.get("container", "?")}')
    return reasons


def pod_starts(client, instance, window: DayWindow, namespace: str) -> list[PodStart]:
    """The backend pods created on the UTC day, with their condition times and failure reason."""
    matchers = [f'pod=~"{backend_pods(instance)}"']
    if namespace:
        matchers.insert(0, f'namespace="{namespace}"')
    pods = ",".join(matchers)
    at = window.end + LOOKAHEAD
    span = f"[{int((at - window.start).total_seconds())}s]"

    def over_span(function: str, metric: str, extra: str = "", suffix: str = "") -> list[dict]:
        return client.instant(f"{function}({metric}{{{pods}{extra}}}{span}){suffix}", at)

    created = {key: moment for key, moment in _times(over_span("min_over_time", "kube_pod_created")).items()
               if window.start <= moment < window.end}
    if not created:
        return []
    conditions = {name: _times(over_span("min_over_time", metric)) for name, metric in _CONDITIONS.items()}
    # The backend container's start. ``state_started`` moves on every container restart: that is
    # why restarts are counted separately (``backend_container_restarts``), and why the first
    # value observed in the day's range is taken, so a pod whose container restarted before it
    # was first ready keeps the phases of its first start.
    container = settings.fleet_metrics_backend_container
    started_matchers = pods + (f',container="{container}"' if container else "")
    conditions["container_started"] = _times(client.instant(
        f"max by (namespace, pod) (min_over_time(kube_pod_container_state_started{{{started_matchers}}}{span}))", at))
    owners = {_key(series["metric"]): series["metric"].get("owner_name", "")
              for series in over_span("max_over_time", "kube_pod_owner", ',owner_kind="ReplicaSet"')}
    replicaset_matchers = [f'replicaset=~"{backend_deployment(instance)}-[a-z0-9]+"']
    if namespace:
        replicaset_matchers.insert(0, f'namespace="{namespace}"')
    replicasets = {
        series["metric"].get("replicaset", ""): _when(series)
        for series in client.instant(
            f"max_over_time(kube_replicaset_created{{{','.join(replicaset_matchers)}}}{span})", at)
    }
    reasons = _reasons(over_span("max_over_time", "kube_pod_init_container_status_waiting_reason", suffix=" > 0"))
    terminated = _reasons(over_span("max_over_time", "kube_pod_init_container_status_terminated_reason", suffix=" > 0"))
    for key, reason in terminated.items():
        reasons.setdefault(key, reason)

    found = []
    for key in sorted(created, key=created.get):
        start = PodStart(
            namespace=key[0], pod=key[1], created=created[key],
            replicaset_created=replicasets.get(owners.get(key, "")),
            **{name: times.get(key) for name, times in conditions.items()},
        )
        if start.outcome == catalog.START_FAILED:
            start = replace(start, failure_reason=reasons.get(key, ""))
        found.append(start)
    return found


def classify(start: PodStart, operations: list[Operation], *, from_zero: Callable[[], bool]) -> str:
    """Why the backend started, tried in the spec's order (§4.2 K6).

    ``from_zero`` is asked last, and only when nothing else explains the start: it costs a query.
    """
    running = [
        op for op in operations
        if op.operation_type in catalog.START_CAUSE_BY_OPERATION
        and op.started <= start.created and (op.completed is None or op.completed >= start.created)
    ]
    if running:
        return catalog.START_CAUSE_BY_OPERATION[max(running, key=lambda op: op.started).operation_type]
    if start.replicaset_created is not None and start.created - start.replicaset_created <= NEW_REPLICASET:
        return catalog.CAUSE_ROLLOUT
    if from_zero():
        return catalog.CAUSE_SCALE_FROM_ZERO
    return catalog.CAUSE_RESCHEDULE


def replicas_before(client, instance, namespace: str, moment: datetime) -> Optional[float]:
    """The backend's wanted replicas one minute before ``moment``; None if Prometheus has no sample."""
    result = client.instant(
        f"max(kube_deployment_spec_replicas{{{deployment_matchers(instance, namespace)}}})",
        moment - timedelta(minutes=1),
    )
    return float(result[0]["value"][1]) if result else None


def classified_starts(client, instance, window: DayWindow, namespace: str, operations: list[Operation],
                      *, autoscaling: bool) -> list[tuple[PodStart, str]]:
    """The day's starts, each with its cause."""
    classified = []
    for start in pod_starts(client, instance, window, namespace):
        def from_zero(start: PodStart = start) -> bool:
            return autoscaling and replicas_before(client, instance, namespace, start.created) == 0

        classified.append((start, classify(start, operations, from_zero=from_zero)))
    return classified


def start_metrics(classified: list[tuple[PodStart, str]]) -> list[Value]:
    """``backend_starts`` by cause, with explicit zeros, and ``backend_failed_starts``."""
    by_cause = {cause: 0.0 for cause in catalog.START_CAUSES}
    for _, cause in classified:
        by_cause[cause] += 1
    failed = sum(1 for start, _ in classified if start.outcome == catalog.START_FAILED)
    return [("backend_starts", cause, count) for cause, count in by_cause.items()] + [
        ("backend_failed_starts", "", float(failed)),
    ]
```

- [ ] **Step 6: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_starts.py fleet_metrics/tests/test_controller.py -q`
Expected: `21 passed` (14 in `test_starts.py`, 7 in `test_controller.py`).

- [ ] **Step 7: Commit**

```bash
git add fleet_metrics/catalog.py fleet_metrics/sources/controller.py fleet_metrics/sources/starts.py fleet_metrics/tests/test_starts.py fleet_metrics/tests/test_controller.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): every backend start, its four phases, its outcome and its cause"
```

---

### Task 16: The Prometheus source in the run

**Files:**
- Modify: `fleet_metrics/catalog.py`, `fleet_metrics/collector.py`, `fleet_metrics/run.py`
- Test: `fleet_metrics/tests/test_collector_prometheus.py`; `fleet_metrics/tests/test_run.py` (append)

**Interfaces:**
- Consumes: Tasks 8 and 15.
- Produces: `RunContext` gains `prometheus: Optional[Prometheus] = None`, `namespace: str = ""`, `prometheus_retention_days: int = 180`, `prometheus_since: Optional[date] = None`; `StartEvent` rows; `prometheus_status` on every row.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_collector_prometheus.py`:

```python
"""Release 2 in the collector: the Prometheus source, the day's starts, and the days it cannot see."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import TestCase

from fleet_metrics import catalog
from fleet_metrics.collector import RunContext, collect_instance_day
from fleet_metrics.days import window_for
from fleet_metrics.models import StartEvent
from fleet_metrics.sources.instance_db import InstanceDbResult
from fleet_metrics.sources.starts import PodStart
from fleet_metrics.tests.factories import make_instance

pytestmark = pytest.mark.integration
DAY = date(2026, 10, 25)
WINDOW = window_for(DAY)
NOW = datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)
R1 = frozenset({catalog.SOURCE_CONTROLLER, catalog.SOURCE_DB, catalog.SOURCE_VERSION})
R2 = R1 | {catalog.SOURCE_PROMETHEUS}
T = datetime(2026, 10, 25, 9, tzinfo=timezone.utc)
START = PodStart(
    namespace="default", pod="lex-backend-books-prod-1-7d9c8b6f5d-x2k4q", created=T,
    scheduled=T + timedelta(seconds=2), initialized=T + timedelta(seconds=146),
    container_started=T + timedelta(seconds=250), ready=T + timedelta(seconds=315),
)


def ctx(**overrides):
    fields = dict(sources=R2, release_dates={}, latest_day=DAY, include_db_aliases=False, now=NOW,
                  prometheus=object(), namespace="default")
    fields.update(overrides)
    return RunContext(**fields)


@contextmanager
def fake_cursor(instance):
    yield object()


class PrometheusSourceTests(TestCase):
    def setUp(self):
        self.instance = make_instance()
        patchers = {
            "cursor": patch("fleet_metrics.collector.instance_db.instance_cursor", fake_cursor),
            "collect": patch("fleet_metrics.collector.instance_db.collect", return_value=InstanceDbResult(values=[])),
            "deployed": patch("fleet_metrics.collector.controller.deploy_finished", return_value=False),
            "ready": patch("fleet_metrics.collector.version.backend_ready", return_value=False),
            "operations": patch("fleet_metrics.collector.controller.operations_during", return_value=[]),
            "platform": patch("fleet_metrics.collector.prometheus.platform_metrics",
                              return_value=[("api_requests", "2xx", 100.0)]),
            "starts": patch("fleet_metrics.collector.starts.classified_starts",
                            return_value=[(START, catalog.CAUSE_DEPLOY)]),
        }
        self.mocks = {name: p.start() for name, p in patchers.items()}
        for p in patchers.values():
            self.addCleanup(p.stop)

    def _values(self, row):
        return {(v.metric, v.dimension): v.value for v in row.values.all()}

    def test_platform_values_and_one_start_event_per_pod(self):
        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(row.prometheus_status, catalog.OK)
        values = self._values(row)
        self.assertEqual(values[("api_requests", "2xx")], 100.0)
        self.assertEqual(values[("backend_starts", catalog.CAUSE_DEPLOY)], 1.0)
        self.assertEqual(values[("backend_starts", catalog.CAUSE_RESCHEDULE)], 0.0)
        self.assertEqual(values[("backend_failed_starts", "")], 0.0)
        event = StartEvent.objects.get()
        self.assertEqual((event.instance_pk, event.day, event.pod, event.cause, event.outcome),
                         (self.instance.pk, DAY, START.pod, catalog.CAUSE_DEPLOY, catalog.START_READY))
        self.assertEqual((event.scheduling_seconds, event.preparation_seconds,
                          event.container_start_seconds, event.application_ready_seconds),
                         (2.0, 144.0, 104.0, 65.0))
        with connection.cursor() as cursor:
            # Read in SQL: through the ORM the instants come back as naive Berlin (spec §6, rule 4).
            cursor.execute("SELECT extract(epoch FROM created_at), extract(epoch FROM ready_at) "
                           "FROM fleet_metrics_startevent")
            self.assertEqual([float(v) for v in cursor.fetchone()],
                             [T.timestamp(), (T + timedelta(seconds=315)).timestamp()])

    def test_a_re_run_replaces_the_days_start_events(self):
        collect_instance_day(self.instance, WINDOW, ctx())
        collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(StartEvent.objects.count(), 1)

    def test_a_failure_after_a_success_keeps_values_and_start_events(self):
        collect_instance_day(self.instance, WINDOW, ctx())
        self.mocks["platform"].side_effect = ConnectionError("prometheus unreachable")
        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(row.prometheus_status, catalog.OK)
        self.assertEqual(self._values(row)[("api_requests", "2xx")], 100.0)
        self.assertEqual(StartEvent.objects.count(), 1)
        self.assertIn("kept", row.errors["prometheus"])

    def test_a_day_before_the_data_prometheus_holds_is_skipped_and_erases_nothing(self):
        early = ctx(prometheus_since=DAY + timedelta(days=1))
        row = collect_instance_day(self.instance, window_for(DAY - timedelta(days=3)), early)
        self.assertEqual(row.prometheus_status, catalog.SKIPPED)
        self.mocks["platform"].assert_not_called()

        collect_instance_day(self.instance, WINDOW, ctx())
        row = collect_instance_day(self.instance, WINDOW, early)
        self.assertEqual(row.prometheus_status, catalog.OK, "a day it can no longer see keeps what was read")
        self.assertEqual(StartEvent.objects.count(), 1)

    def test_beyond_retention_is_skipped(self):
        row = collect_instance_day(self.instance, window_for(date(2026, 1, 2)), ctx(prometheus_retention_days=180))
        self.assertEqual(row.prometheus_status, catalog.SKIPPED)

    def test_r1_sources_never_query_prometheus(self):
        row = collect_instance_day(self.instance, WINDOW, ctx(sources=R1))
        self.assertEqual(row.prometheus_status, catalog.SKIPPED)
        self.mocks["platform"].assert_not_called()
        self.mocks["starts"].assert_not_called()

    def test_no_prometheus_host_is_an_error(self):
        row = collect_instance_day(self.instance, WINDOW, ctx(prometheus=None))
        self.assertEqual(row.prometheus_status, catalog.ERROR)
```

Append to `RunCollectionTests` in `fleet_metrics/tests/test_run.py`:

```python
    @patch("fleet_metrics.run.version.pypi_release_dates", return_value={})
    @patch("fleet_metrics.run.grants.ensure_reader_grants", return_value=True)
    def test_release_2_turns_prometheus_on_by_configuration(self, _grants, _dates):
        seen = []
        with patch("fleet_metrics.run.collect_instance_day", side_effect=lambda i, w, c, run=None: seen.append(c)), \
                patch.object(ic_settings, "fleet_metrics_sources", "controller,db,version,prometheus"), \
                patch.object(ic_settings, "prometheus_host", "http://prom/api/v1/query"), \
                patch.object(ic_settings, "fleet_metrics_prometheus_since", "2026-08-18"):
            run_collection(trigger=CollectionRun.TRIGGER_MANUAL, days=[LATEST], ping=False, now=NOW)
        self.assertIn(catalog.SOURCE_PROMETHEUS, seen[0].sources)
        self.assertIsNotNone(seen[0].prometheus)
        self.assertEqual(seen[0].prometheus_since, date(2026, 8, 18))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_collector_prometheus.py fleet_metrics/tests/test_run.py -q`
Expected: `TypeError: RunContext.__init__() got an unexpected keyword argument 'prometheus'`, and `ValueError` from `parse_sources` for `prometheus`.

- [ ] **Step 3: Implement**

In `fleet_metrics/catalog.py`:

```python
IMPLEMENTED_SOURCES = frozenset({SOURCE_CONTROLLER, SOURCE_DB, SOURCE_VERSION, SOURCE_PROMETHEUS})
```

In `fleet_metrics/collector.py`, change the imports to:

```python
from fleet_metrics.models import CollectionRun, InstanceDay, MetricValue, StartEvent
from fleet_metrics.sources import controller, instance_db, prometheus, starts, version
```

append the R2 fields to `RunContext`:

```python
    prometheus: Optional[prometheus.Prometheus] = None
    namespace: str = ""
    prometheus_retention_days: int = 180
    prometheus_since: Optional[date] = None
```

add after `_database`:

```python
def _platform(instance, window: DayWindow, ctx: RunContext) -> tuple[Outcome, list[tuple[starts.PodStart, str]]]:
    """K5 and K6 from Prometheus, with the day's starts; their causes need the controller's operations."""
    if catalog.SOURCE_PROMETHEUS not in ctx.sources:
        return _not_delivered(), []
    if ctx.prometheus is None:
        return Outcome(catalog.ERROR, error="PROMETHEUS_HOST is not set"), []
    if ((ctx.latest_day - window.day).days > ctx.prometheus_retention_days
            or (ctx.prometheus_since is not None and window.day < ctx.prometheus_since)):
        # Not a reading of zero: Prometheus no longer holds, or never held, this day.
        return Outcome(catalog.SKIPPED, error="outside the data Prometheus holds", ran=False), []
    try:
        values = prometheus.platform_metrics(ctx.prometheus, instance, window, ctx.namespace)
        classified = starts.classified_starts(
            ctx.prometheus, instance, window, ctx.namespace, controller.operations_during(instance, window),
            autoscaling=bool(getattr(instance.config_metadata, "autoscaling", False)),
        )
    except Exception as exc:
        logger.warning("fleet metrics: Prometheus for %s on %s: %s", instance.resource_identifier, window.day, exc)
        return Outcome(catalog.ERROR, error=_describe(exc)), []
    return Outcome(catalog.OK, values + starts.start_metrics(classified)), classified
```

and in `collect_instance_day`, after `db_outcome, db_result = _database(instance, window, ctx)`:

```python
    platform_outcome, classified = _platform(instance, window, ctx)
```

after `_apply(row, "db_status", …)`:

```python
        _apply(row, "prometheus_status", catalog.SOURCE_PROMETHEUS, platform_outcome, errors, replace)
```

and after the `MetricValue.objects.bulk_create(…)` call, still inside the transaction:

```python
        if catalog.SOURCE_PROMETHEUS in replace:
            StartEvent.objects.filter(instance_pk=instance.pk, day=window.day).delete()
            StartEvent.objects.bulk_create([
                StartEvent(instance_pk=instance.pk, day=window.day, pod=start.pod, cause=cause,
                           outcome=start.outcome, created_at=start.created, ready_at=start.ready,
                           failure_reason=start.failure_reason, **start.phases())
                for start, cause in classified
            ])
```

In `fleet_metrics/run.py`, change `from fleet_metrics.sources import version` to `from fleet_metrics.sources import prometheus, version`, and append to the `RunContext(...)` call:

```python
        prometheus=(prometheus.Prometheus(settings.prometheus_host)
                    if catalog.SOURCE_PROMETHEUS in sources and settings.prometheus_host else None),
        namespace=settings.k8s_namespace or "",
        prometheus_retention_days=settings.fleet_metrics_prometheus_retention_days,
        prometheus_since=(date.fromisoformat(settings.fleet_metrics_prometheus_since)
                          if settings.fleet_metrics_prometheus_since else None),
```

- [ ] **Step 4: Run the whole app's tests**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests -q`
Expected: every fleet_metrics test passes (about 120), R1's unchanged.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/catalog.py fleet_metrics/collector.py fleet_metrics/run.py fleet_metrics/tests/test_collector_prometheus.py fleet_metrics/tests/test_run.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): the Prometheus source, turned on by configuration"
```

- [ ] **Step 6: Open the R2 PR (Task 14, Step 2), then turn it on per environment**

After the merge, the infrastructure plan sets `FLEET_METRICS_SOURCES=controller,db,version,prometheus` and `FLEET_METRICS_PROMETHEUS_SINCE=<Task 1's day>`. The catch-up does not revisit days on which Prometheus was `skipped`, so backfill once, as deep as Task 1 found (spec §8.1, R2.1):

```bash
python manage.py collect_fleet_metrics --from <Task 1's day> --to <yesterday> --no-heartbeat
```

---

## Release 3 — K2, exact K1

Branch `feat/fleet-metrics-recorder-reads` (Task 14).

### Task 17: The recorder's tables, editors and the two silences

The recorder (lex-app, spec §5.3.1) and the interface events (§5.3.2) write `lex_usage_daily` with the primary key `(day, actor_kind, actor_id, area, channel)`, channel `'api' | 'ui' | 'ui_embed'`. The collector reads people only: `active_people_*` count a person once whatever the channel (exact K1). `area_people_28d` has the dimension `"<area>:<channel>"` and four channels: the stored `api`, `ui` and `ui_embed`, and **`ui_all`**, the distinct people over `ui` and `ui_embed` together, computed in the instance SQL. `ui_all` is the K2 figure: nobody sums `ui` and `ui_embed`, which would count twice a person who used both. `api` is supporting detail. The coverage state is `absent`, `enabled` or `disabled`. `editors_1d`, moved here from R1, serves only the recorder-silence check (spec §7).

**Files:**
- Modify: `fleet_metrics/sources/instance_db.py`, `fleet_metrics/tests/lex_schema.py`, `fleet_metrics/catalog.py` (append)
- Test: `fleet_metrics/tests/test_instance_db_recorder.py`

**Interfaces:**
- Produces: `catalog.CHANNEL_API`, `catalog.INTERFACE_CHANNELS`, `catalog.CHANNEL_UI_ALL`, `catalog.area_channel(area, channel) -> str`; `USAGE`, `VERSIONS`; `editors_1d(cursor, window) -> int`; `Recorder(coverage: str, version: Optional[str], whole_day: bool)`; `recorder_state(cursor, window) -> Recorder`; `recorder_metrics(cursor, window) -> tuple[list[Value], bool]` (values, `ui_silent`); `InstanceDbResult` gains `recorder: str = catalog.SKIPPED`, `version: Optional[str] = None`, `recorder_silent: bool = False`, `ui_silent: bool = False`; `collect(cursor, window, *, recorder: bool = False) -> InstanceDbResult`. Test helper `create_lex_tables(cursor, *, recorder=False)`.

- [ ] **Step 1: Extend the fixture and the catalogue**

In `fleet_metrics/tests/lex_schema.py`, change the docstring's last sentence to "The recorder's tables are created on request, as lex-app's recorder migration installs them (cluster 10o)." and replace `create_lex_tables` with:

```python
RECORDER_TABLES = """
CREATE TABLE lex_usage_daily (
    day date NOT NULL,
    actor_kind text NOT NULL,
    actor_id text NOT NULL,
    area text NOT NULL,
    channel text NOT NULL,
    first_seen timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (day, actor_kind, actor_id, area, channel)
);
CREATE TABLE lex_runtime_versions (
    version text PRIMARY KEY,
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen timestamptz NOT NULL DEFAULT now(),
    usage_enabled boolean NOT NULL
);
"""


def create_lex_tables(cursor, *, recorder: bool = False) -> None:
    cursor.execute(LEX_TABLES)
    if recorder:
        cursor.execute(RECORDER_TABLES)
```

Append to `fleet_metrics/catalog.py`:

```python
# The recorder's channels (R3, spec §5.3.2). ``api`` is supporting detail.
CHANNEL_API = "api"
INTERFACE_CHANNELS = ("ui", "ui_embed")
#: Distinct people over both interface channels, computed in the instance SQL: the K2 figure.
#: Summing ``ui`` and ``ui_embed`` would count twice a person who used both.
CHANNEL_UI_ALL = "ui_all"


def area_channel(area: str, channel: str) -> str:
    """The dimension of ``area_people_28d``: ``<area>:<channel>``."""
    return f"{area}:{channel}"
```

- [ ] **Step 2: Write the failing tests**

`fleet_metrics/tests/test_instance_db_recorder.py`:

```python
"""The recorder's coverage, its counts by channel, editors, and both silences (spec §5.3.2, §7)."""

from datetime import date, datetime, timezone

import pytest
from django.db import connection
from django.test import TestCase
from rest_framework_api_key.models import APIKey

from fleet_metrics import catalog
from fleet_metrics.days import window_for
from fleet_metrics.sources import instance_db
from fleet_metrics.tests.lex_schema import add_audit, create_lex_tables

pytestmark = pytest.mark.integration
DAY = window_for(date(2026, 10, 25))
D25, D20, D01, S27 = date(2026, 10, 25), date(2026, 10, 20), date(2026, 10, 1), date(2026, 9, 27)


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class RecorderTests(TestCase):
    def _cursor_with(self, *, recorder=True, versions=(), usage=()):
        cursor = connection.cursor()
        create_lex_tables(cursor, recorder=recorder)
        for version, first_seen, enabled in versions:
            cursor.execute(
                "INSERT INTO lex_runtime_versions (version, first_seen, last_seen, usage_enabled) VALUES (%s, %s, %s, %s)",
                [version, first_seen, first_seen, enabled],
            )
        for day, kind, actor, area, channel in usage:
            cursor.execute(
                "INSERT INTO lex_usage_daily (day, actor_kind, actor_id, area, channel) VALUES (%s, %s, %s, %s, %s)",
                [day, kind, actor, area, channel],
            )
        return cursor

    def test_no_tables_means_absent(self):
        cursor = self._cursor_with(recorder=False)
        self.assertEqual(instance_db.recorder_state(cursor, DAY), instance_db.Recorder(catalog.RECORDER_ABSENT, None, False))

    def test_a_version_row_means_enabled(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 20), True)])
        self.assertEqual(instance_db.recorder_state(cursor, DAY), instance_db.Recorder(catalog.RECORDER_ENABLED, "2.4.0", True))

    def test_a_recorder_that_appeared_during_the_day_covers_part_of_it(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 25, 9), True)])
        state = instance_db.recorder_state(cursor, DAY)
        self.assertEqual((state.coverage, state.whole_day), (catalog.RECORDER_ENABLED, False))

    def test_switched_off_is_disabled(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 20), False)])
        self.assertEqual(instance_db.recorder_state(cursor, DAY).coverage, catalog.RECORDER_DISABLED)

    def test_the_version_is_the_one_that_ran_at_the_end_of_the_day(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 20), True), ("2.4.1", utc(2026, 10, 27), True)])
        self.assertEqual(instance_db.recorder_state(cursor, DAY).version, "2.4.0")

    def test_people_over_every_channel_and_areas_by_channel(self):
        cursor = self._cursor_with(
            versions=[("2.4.0", utc(2026, 10, 1), True)],
            usage=[
                (D25, "person", "7", "grid", "ui"),
                (D25, "person", "7", "grid", "api"),
                (D25, "person", "7", "export", "ui"),
                (D25, "person", "8", "grid", "ui"),
                (D25, "person", "8", "grid", "ui_embed"),  # 8 used the grid in both interfaces
                (D20, "person", "9", "history", "ui"),
                (D01, "person", "10", "grid", "api"),      # 25 days before: inside 28
                (S27, "person", "11", "grid", "ui"),       # 29 days before: outside
                (D25, "api_key", "Nightly import", "grid", "api"),
            ],
        )
        usage, ui_silent = instance_db.recorder_metrics(cursor, DAY)
        values = {(m, d): v for m, d, v in usage}
        self.assertEqual(values[("active_people_1d", "")], 2.0, "7 once, whatever the channel, and 8")
        self.assertEqual(values[("active_people_7d", "")], 3.0)
        self.assertEqual(values[("active_people_28d", "")], 4.0)
        self.assertEqual(values[("area_people_28d", "grid:ui")], 2.0)
        self.assertEqual(values[("area_people_28d", "grid:ui_embed")], 1.0)
        self.assertEqual(values[("area_people_28d", "grid:ui_all")], 2.0, "7 and 8: not 3, the sum of ui and ui_embed")
        self.assertEqual(values[("area_people_28d", "grid:api")], 2.0, "people only: the API key is not counted")
        self.assertEqual(values[("area_people_28d", "export:ui_all")], 1.0)
        self.assertEqual(values[("area_people_28d", "history:ui_all")], 1.0)
        self.assertNotIn(("area_people_28d", "export:api"), values)
        self.assertEqual({m for m, _ in values}, {"active_people_1d", "active_people_7d", "active_people_28d", "area_people_28d"})
        self.assertFalse(ui_silent)

    def test_api_use_with_no_interface_event_is_ui_silent(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 1), True)],
                                   usage=[(D25, "person", "7", "grid", "api")])
        result = instance_db.collect(cursor, DAY, recorder=True)
        self.assertTrue(result.ui_silent)
        self.assertFalse(result.recorder_silent)

    def test_an_edit_the_recorder_did_not_see_is_silence(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 1), True)])
        add_audit(cursor, at=utc(2026, 10, 25, 9), author="Ada Lovelace")
        self.assertTrue(instance_db.collect(cursor, DAY, recorder=True).recorder_silent)

    def test_the_same_edit_with_a_row_is_not_silence(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 1), True)],
                                   usage=[(D25, "person", "7", "record", "api")])
        add_audit(cursor, at=utc(2026, 10, 25, 9), author="Ada Lovelace")
        result = instance_db.collect(cursor, DAY, recorder=True)
        self.assertFalse(result.recorder_silent)
        self.assertEqual((result.recorder, result.version), (catalog.RECORDER_ENABLED, "2.4.0"))

    def test_on_the_day_the_recorder_appears_an_earlier_edit_is_not_silence(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 25, 10), True)])
        add_audit(cursor, at=utc(2026, 10, 25, 9), author="Ada Lovelace")
        self.assertFalse(instance_db.collect(cursor, DAY, recorder=True).recorder_silent)

    def test_without_the_recorder_source_nothing_of_it_is_read(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 1), True)])
        add_audit(cursor, at=utc(2026, 10, 25, 9), author="Ada Lovelace")
        result = instance_db.collect(cursor, DAY)
        self.assertEqual((result.recorder, result.version, result.recorder_silent),
                         (catalog.SKIPPED, None, False))
        self.assertFalse({m for m, _, _ in result.values} & {"active_people_1d", "editors_1d"})

    def test_without_tables_the_audit_still_counts(self):
        cursor = self._cursor_with(recorder=False)
        add_audit(cursor, at=utc(2026, 10, 25, 9), author="Ada Lovelace")
        result = instance_db.collect(cursor, DAY, recorder=True)
        metrics = {(m, d) for m, d, _ in result.values}
        self.assertIn(("writers_1d", ""), metrics)
        self.assertIn(("editors_1d", ""), metrics)
        self.assertNotIn(("active_people_1d", ""), metrics)
        self.assertEqual(result.recorder, catalog.RECORDER_ABSENT)
        self.assertFalse(result.recorder_silent, "no recorder, so nothing can be silent")


class EditorsTests(TestCase):
    def test_editors_are_people_with_a_successful_non_calculation_change(self):
        APIKey.objects.create_key(name="Nightly import")
        with connection.cursor() as cursor:
            create_lex_tables(cursor)
            add_audit(cursor, at=utc(2026, 10, 24, 23, 30), author="Bo Diddley")
            add_audit(cursor, at=utc(2026, 10, 25, 8), author="Ada Lovelace")
            add_audit(cursor, at=utc(2026, 10, 25, 12), author="Nightly import")
            add_audit(cursor, at=utc(2026, 10, 25, 13), author="system (initial_data_upload)")
            add_audit(cursor, at=utc(2026, 10, 25, 14), author="Eve Arden", resource="forecast",
                      payload={"is_calculated": "SUCCESS"}, calculation_id="forecast_1_update_a")
            add_audit(cursor, at=utc(2026, 10, 25, 15), author="Cy Twombly", status="failure")
            for zone in ("UTC", "Europe/Berlin"):
                with self.subTest(session_zone=zone):
                    cursor.execute(f"SET TIME ZONE '{zone}'")
                    self.assertEqual(instance_db.editors_1d(cursor, DAY), 1,
                                     "Ada only: Cy's save failed, Eve's row ends a calculation")
```

- [ ] **Step 3: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_recorder.py -q`
Expected: `AttributeError: module 'fleet_metrics.sources.instance_db' has no attribute 'recorder_state'`.

- [ ] **Step 4: Implement**

In `fleet_metrics/sources/instance_db.py`, add `from typing import Optional` to the imports, `USAGE = "lex_usage_daily"` and `VERSIONS = "lex_runtime_versions"` beside the other table names, and replace `InstanceDbResult` and `collect` with:

```python
@dataclass(frozen=True)
class InstanceDbResult:
    """What one instance database contributes to one day."""

    values: list[Value]
    recorder: str = catalog.SKIPPED       # coverage: absent, enabled or disabled; SKIPPED before R3
    version: Optional[str] = None         # from lex_runtime_versions (R3)
    recorder_silent: bool = False         # fails the heartbeat (spec §7)
    ui_silent: bool = False               # shown only (spec §5.3.2)


_EDITORS_SQL = f"""
SELECT count(DISTINCT a.author)
FROM {AUDIT} a
JOIN {STATUS} s ON s.audit_log_id = a.id
WHERE {_IN_RANGE} AND a.date >= %(start)s AND a.date < %(end)s
  AND s.status = 'success'
  AND COALESCE(a.payload->>'is_calculated', '') NOT IN ({_TERMINAL}, 'IN_PROGRESS')
  AND {_IS_PERSON}
"""


def editors_1d(cursor, window: DayWindow) -> int:
    """People with a successful, non-calculation audit row on the day: the recorder-silence check (spec §7).

    A calculation's row can be written when the run ends, after midnight, with no request that
    day, and a failed save leaves a row although the recorder records only successes.
    """
    window_range = id_range(cursor, AUDIT, "date", window.start, window.end)
    if window_range.empty:
        return 0
    cursor.execute(_EDITORS_SQL, {**_range_params(window_range), "start": window.start, "end": window.end})
    return int(cursor.fetchone()[0])


_RECORDER_PRESENT_SQL = (
    f"SELECT to_regclass('public.{USAGE}') IS NOT NULL AND to_regclass('public.{VERSIONS}') IS NOT NULL"
)
# The version that started last before the day's end ran at its end. ``last_seen`` keeps moving
# while a version runs, so the newest ``last_seen`` before the end would skip one still running.
_RECORDER_STATE_SQL = f"""
SELECT (SELECT min(first_seen) FROM {VERSIONS}), v.version, v.usage_enabled
FROM (SELECT version, usage_enabled FROM {VERSIONS}
      WHERE first_seen < %(end)s ORDER BY first_seen DESC LIMIT 1) v
"""
_INTERFACE = ", ".join(f"'{channel}'" for channel in catalog.INTERFACE_CHANNELS)
_PEOPLE_SQL = f"""
SELECT
    count(DISTINCT actor_id) FILTER (WHERE day = %(day)s),
    count(DISTINCT actor_id) FILTER (WHERE day > %(day)s - 7),
    count(DISTINCT actor_id),
    count(DISTINCT actor_id) FILTER (WHERE day = %(day)s AND channel = '{catalog.CHANNEL_API}'),
    count(*) FILTER (WHERE day = %(day)s AND channel IN ({_INTERFACE}))
FROM {USAGE}
WHERE actor_kind = 'person' AND day > %(day)s - 28 AND day <= %(day)s
"""
# One row per area and stored channel, plus ``ui_all``: distinct people over both interface
# channels, counted here because the sum of ``ui`` and ``ui_embed`` would count some people twice.
_AREAS_SQL = f"""
SELECT area, channel, count(DISTINCT actor_id)
FROM {USAGE}
WHERE actor_kind = 'person' AND day > %(day)s - 28 AND day <= %(day)s
GROUP BY area, channel
UNION ALL
SELECT area, '{catalog.CHANNEL_UI_ALL}', count(DISTINCT actor_id)
FROM {USAGE}
WHERE actor_kind = 'person' AND day > %(day)s - 28 AND day <= %(day)s AND channel IN ({_INTERFACE})
GROUP BY area
ORDER BY 1, 2
"""


@dataclass(frozen=True)
class Recorder:
    coverage: str             # catalog.RECORDER_ABSENT, RECORDER_ENABLED or RECORDER_DISABLED
    version: Optional[str]    # the version that ran at the end of the day
    whole_day: bool           # the recorder was running when the day began


def recorder_state(cursor, window: DayWindow) -> Recorder:
    """The recorder's coverage of the day, and the lex-app version that ran at its end."""
    cursor.execute(_RECORDER_PRESENT_SQL)
    if not cursor.fetchone()[0]:
        return Recorder(catalog.RECORDER_ABSENT, None, False)
    cursor.execute(_RECORDER_STATE_SQL, {"end": window.end})
    row = cursor.fetchone()
    if row is None:
        return Recorder(catalog.RECORDER_ABSENT, None, False)
    first_seen, version, enabled = row
    coverage = catalog.RECORDER_ENABLED if enabled else catalog.RECORDER_DISABLED
    return Recorder(coverage, version, whole_day=first_seen < window.start)


def recorder_metrics(cursor, window: DayWindow) -> tuple[list[Value], bool]:
    """Exact K1 and K2 from the presence rows, and whether the interface was silent (spec §5.3.2).

    ``area_people_28d`` comes per area for ``api``, ``ui``, ``ui_embed`` and ``ui_all`` (the K2
    figure). ``ui_silent``: people were seen on the ``api`` channel but no ``ui`` or ``ui_embed``
    row exists for the day — an interface that does not emit, such as an older package.
    """
    params = {"day": window.day}
    cursor.execute(_PEOPLE_SQL, params)
    people_1d, people_7d, people_28d, api_people_1d, interface_rows_1d = cursor.fetchone()
    values = [
        ("active_people_1d", "", float(people_1d)),
        ("active_people_7d", "", float(people_7d)),
        ("active_people_28d", "", float(people_28d)),
    ]
    cursor.execute(_AREAS_SQL, params)
    for area, channel, people in cursor.fetchall():
        values.append(("area_people_28d", catalog.area_channel(area, channel), float(people)))
    return values, bool(api_people_1d > 0 and interface_rows_1d == 0)


def collect(cursor, window: DayWindow, *, recorder: bool = False) -> InstanceDbResult:
    """Everything one instance database contributes to one day.

    ``recorder`` is true from R3, when ``FLEET_METRICS_SOURCES`` lists it: then ``editors_1d``,
    the presence rows and the runtime version are read too.
    """
    values = audit_metrics(cursor, window) + calculation_metrics(cursor, window)
    if not recorder:
        return InstanceDbResult(values=values)
    editors = editors_1d(cursor, window)
    values.append(("editors_1d", "", float(editors)))
    state = recorder_state(cursor, window)
    recorder_silent = ui_silent = False
    if state.coverage == catalog.RECORDER_ENABLED:
        usage, ui_silent = recorder_metrics(cursor, window)
        values += usage
        active_today = next(v for m, _, v in usage if m == "active_people_1d")
        # A recorder that appeared during the day never saw the edits made before it.
        recorder_silent = state.whole_day and editors > 0 and active_today == 0
    return InstanceDbResult(values=values, recorder=state.coverage, version=state.version,
                            recorder_silent=recorder_silent, ui_silent=ui_silent)
```

- [ ] **Step 5: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_recorder.py fleet_metrics/tests/test_instance_db_connection.py -q`
Expected: `18 passed` (13 new, Task 7's 5 unchanged).

- [ ] **Step 6: Commit**

```bash
git add fleet_metrics/catalog.py fleet_metrics/sources/instance_db.py fleet_metrics/tests/lex_schema.py fleet_metrics/tests/test_instance_db_recorder.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): read the recorder by channel, and notice when it or the interface is silent"
```

---

### Task 18: The recorder source in the run

**Files:**
- Modify: `fleet_metrics/catalog.py`, `fleet_metrics/collector.py`, `fleet_metrics/heartbeat.py`
- Test: `fleet_metrics/tests/test_collector_recorder.py`; `fleet_metrics/tests/test_heartbeat.py` (append)

**Interfaces:**
- Consumes: Task 17.
- Produces: `InstanceDay.recorder`, `recorder_silent`, `ui_silent` filled; `lex_runtime_versions` as the first version source (spec §5.4.6, step 1); a heartbeat that fails on recorder silence.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_collector_recorder.py`:

```python
"""Release 3 in the collector: coverage, both silences, and the runtime-table version."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from django.test import TestCase

from fleet_metrics import catalog
from fleet_metrics.collector import RunContext, collect_instance_day
from fleet_metrics.days import window_for
from fleet_metrics.sources.instance_db import InstanceDbResult
from fleet_metrics.tests.factories import make_instance

pytestmark = pytest.mark.integration
DAY = date(2026, 10, 25)
WINDOW = window_for(DAY)
NOW = datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)
R1 = frozenset({catalog.SOURCE_CONTROLLER, catalog.SOURCE_DB, catalog.SOURCE_VERSION})
R3 = R1 | {catalog.SOURCE_RECORDER}
RECORDED = InstanceDbResult(
    values=[("active_people_1d", "", 0.0), ("editors_1d", "", 1.0)],
    recorder=catalog.RECORDER_ENABLED, version="2.4.1", recorder_silent=True, ui_silent=True,
)


def ctx(**overrides):
    fields = dict(sources=R3, release_dates={}, latest_day=DAY, include_db_aliases=False, now=NOW)
    fields.update(overrides)
    return RunContext(**fields)


@contextmanager
def fake_cursor(instance):
    yield object()


class RecorderSourceTests(TestCase):
    def setUp(self):
        self.instance = make_instance()
        patchers = {
            "cursor": patch("fleet_metrics.collector.instance_db.instance_cursor", fake_cursor),
            "collect": patch("fleet_metrics.collector.instance_db.collect", return_value=RECORDED),
            "deployed": patch("fleet_metrics.collector.controller.deploy_finished", return_value=False),
            "ready": patch("fleet_metrics.collector.version.backend_ready", return_value=True),
            "probe": patch("fleet_metrics.collector.version.version_in_cluster", return_value="2.4.0"),
        }
        self.mocks = {name: p.start() for name, p in patchers.items()}
        for p in patchers.values():
            self.addCleanup(p.stop)

    def test_the_runtime_table_is_the_first_version_source(self):
        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual((row.lex_app_version, row.version_source), ("2.4.1", catalog.VERSION_RUNTIME_TABLE))
        self.mocks["probe"].assert_not_called()

    def test_coverage_and_both_silences_are_stored(self):
        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual((row.recorder, row.recorder_silent, row.ui_silent), (catalog.RECORDER_ENABLED, True, True))
        self.assertTrue(self.mocks["collect"].call_args.kwargs["recorder"])

    def test_before_r3_the_recorder_is_skipped_and_not_read(self):
        self.mocks["collect"].return_value = InstanceDbResult(values=[])
        row = collect_instance_day(self.instance, WINDOW, ctx(sources=R1))
        self.assertEqual(row.recorder, catalog.SKIPPED)
        self.assertFalse(self.mocks["collect"].call_args.kwargs["recorder"])
        self.assertEqual(row.version_source, catalog.VERSION_HEALTH, "R1's probe still answers")
```

Append to `VerdictTests` in `fleet_metrics/tests/test_heartbeat.py`:

```python
    def test_a_silent_recorder_fails(self):
        for instance in self.instances:
            row(instance)
        InstanceDay.objects.filter(instance_pk=self.instances[0].pk).update(recorder_silent=True)
        ok, message = self._verdict()
        self.assertFalse(ok)
        self.assertIn("recorder silent", message)

    def test_a_silent_interface_is_shown_not_alarmed(self):
        for instance in self.instances:
            row(instance)
        InstanceDay.objects.filter(instance_pk=self.instances[0].pk).update(ui_silent=True)
        self.assertTrue(self._verdict()[0], "a person can work through the API only (spec §5.3.2)")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_collector_recorder.py fleet_metrics/tests/test_heartbeat.py -q`
Expected: failures — `KeyError: 'recorder'` on the `collect` call's kwargs, and `test_a_silent_recorder_fails` passing the verdict.

- [ ] **Step 3: Implement**

In `fleet_metrics/catalog.py`:

```python
IMPLEMENTED_SOURCES = frozenset({SOURCE_CONTROLLER, SOURCE_DB, SOURCE_VERSION, SOURCE_PROMETHEUS, SOURCE_RECORDER})
```

In `fleet_metrics/collector.py`:
- in `_database`, call `instance_db.collect(cursor, window, recorder=catalog.SOURCE_RECORDER in ctx.sources)`;
- give `_version` a last parameter `db_version: Optional[str] = None` and make this its first step:

```python
    if db_version:
        return db_version, catalog.VERSION_RUNTIME_TABLE
```

- pass it from `collect_instance_day`: `_version(instance, window, ctx, deployed, db_result.version if db_result else None)`;
- after `_apply(row, "db_status", …)`:

```python
        if catalog.SOURCE_DB in replace:
            row.recorder = db_result.recorder if db_result else catalog.SKIPPED
            row.recorder_silent = bool(db_result and db_result.recorder_silent)
            row.ui_silent = bool(db_result and db_result.ui_silent)
```

In `fleet_metrics/heartbeat.py`, at the end of `verdict`, before the `return`:

```python
    # R3. ``ui_silent`` is shown on the Collection dashboard and never fails the heartbeat (spec §5.3.2).
    silent = sum(1 for r in rows if r.recorder_silent)
    if silent:
        problems.append(f"recorder silent on {silent} instance(s)")
```

- [ ] **Step 4: Run the whole app's tests**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests -q`
Expected: every fleet_metrics test passes (about 140).

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/catalog.py fleet_metrics/collector.py fleet_metrics/heartbeat.py fleet_metrics/tests/test_collector_recorder.py fleet_metrics/tests/test_heartbeat.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): the recorder source, its coverage, and an alarm when it falls silent"
```

- [ ] **Step 6: Open the R3 PR (Task 14, Step 3), turn it on, and verify end to end on test**

After the merge, the infrastructure plan adds `recorder` to `FLEET_METRICS_SOURCES`. Turning it on before an instance has the recorder is safe: that instance's coverage is `absent`. Then, on a test instance running the lex-app release with the recorder (spec §5.3.2, verification steps 1–3), after the test user's session:

```bash
python manage.py collect_fleet_metrics --instance <pk> --from <D> --to <D> --no-heartbeat
```

Expected, in `fleet_metrics_daily` for that instance and day: `area_people_28d` of at least 1 for the dimensions `grid:ui_all`, `record:ui_all`, `history:ui_all` and `calculate:ui_all` (and their `:ui` rows), and none for `reports:ui_all`.

---

## Release 4 — K9

Branch `feat/fleet-metrics-k9` (Task 14), once R2's one-week check is accepted (spec §8.1, R4).

### Task 19: API response time p95 (K9)

**Files:**
- Modify: `fleet_metrics/sources/prometheus.py`
- Test: `fleet_metrics/tests/test_prometheus.py` (append)

**Interfaces:**
- Produces: `api_seconds_p95` from `platform_metrics`, when the day had API requests outside 5xx. No new source: it is collected wherever `prometheus` is delivered.

- [ ] **Step 1: Write the failing tests**

Append to `fleet_metrics/tests/test_prometheus.py`:

```python
class LatencyTests(SimpleTestCase):
    def test_the_p95_of_api_requests_without_5xx(self):
        client = FakeClient(instant={"histogram_quantile": [scalar(1.25)]})
        values = collect(client)
        self.assertEqual(values[("api_seconds_p95", "")], 1.25)
        query = next(q[1] for q in client.queries if "histogram_quantile" in q[1])
        self.assertIn('status!~"5.."', query)
        self.assertIn('status!="101"', query)

    def test_no_traffic_no_latency(self):
        self.assertNotIn(("api_seconds_p95", ""), collect(FakeClient()))

    def test_a_nan_latency_is_dropped(self):
        self.assertNotIn(("api_seconds_p95", ""), collect(FakeClient(instant={"histogram_quantile": [scalar("NaN")]})))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_prometheus.py -q`
Expected: `KeyError: ('api_seconds_p95', '')`.

- [ ] **Step 3: Implement**

In `fleet_metrics/sources/prometheus.py`, add `import math` to the imports and, in `platform_metrics`, before `return values`:

```python
    # K9 (R4): the p95 of the same API requests, 5xx excluded (spec §4.2 K9). Per-instance p95s
    # cannot be combined into a fleet p95; the dashboards count instances above a threshold.
    latency_selector = selector[:-1] + ',status!~"5.."}'
    latency = client.instant(
        "histogram_quantile(0.95, sum by (le) "
        f"(increase(nginx_ingress_controller_request_duration_seconds_bucket{latency_selector}[1d])))",
        window.end,
    )
    if latency:
        p95 = float(latency[0]["value"][1])
        if math.isfinite(p95):
            values.append(("api_seconds_p95", "", p95))
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_prometheus.py -q`
Expected: `15 passed`.

- [ ] **Step 5: Commit, open the R4 PR (Task 14, Step 4), and backfill**

```bash
git add fleet_metrics/sources/prometheus.py fleet_metrics/tests/test_prometheus.py
git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "feat(fleet-metrics): API response time p95 per instance day"
```

After the merge, backfill as R2 did (`--from <Task 1's day> --to <yesterday> --no-heartbeat`) so K9's baseline covers the same depth (spec §4.4).
