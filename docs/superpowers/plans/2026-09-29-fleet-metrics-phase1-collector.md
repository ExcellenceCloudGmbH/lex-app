# Fleet metrics phase 1 — the collector (Instance Controller) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Once a day, the Instance Controller writes one row per instance per UTC day into its own database: the instance's dimensions, a status per source, and the metric values of the KPI register (spec §4) — collected from each instance database (aggregates only), the in-cluster Prometheus and the controller's own tables — and pings a Better Stack heartbeat only when those rows were actually written.

**Architecture:** A new Django app `fleet_metrics` in lex-instance-controller-backend. Pure source modules (instance database, Prometheus, controller, version) each return values or raise. `collect_instance_day` combines them and stores the result under rules that never destroy data a re-run cannot reproduce. `run_collection` chooses the days (yesterday plus a 35-day catch-up) and decides the heartbeat. `manage.py collect_fleet_metrics` is what the CronJob runs.

**Tech Stack:** Django 5.2, psycopg2, requests, PostgreSQL 16 for tests, pytest with `--strict-markers`.

**Spec:** `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md` in lex-app (§4, §5.2, §5.4, §5.5, §6, §7)

## Global Constraints

- Repository `lex-instance-controller-backend`; base `origin/main`; branch `feat/fleet-metrics-collector`; work in a worktree under `.claude/worktrees/` (add `.claude/` to `.git/info/exclude` first). The repository has **no git identity**: commit with `git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit …`, or the commit fails and a later `push -u` creates an empty remote branch.
- Python environment: `uv venv venv --python 3.12 && uv pip install -r requirements.txt` in the worktree (`*venv/` is ignored). Test database: `just test-db-up` (docker `postgres:16` on port 55432).
- Test command: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test <paths> -q`. `pytest.ini` has `--strict-markers`; use only `unit` and `integration`. The `origin/main` baseline has 22 failures under `instance_api/tests`; compare against it, never against zero.
- **Time:** the controller runs `USE_TZ=False` with naive Berlin time. In `fleet_metrics`, every day boundary is an aware UTC `datetime` built by `fleet_metrics.days`; it reaches Postgres as a parameter. Never `django.utils.timezone.now()` for day logic, never a naive Berlin boundary, never `date_trunc` or `::date` on a timestamp in SQL (spec §6).
- **Privacy:** instance-database queries return counts, percentiles and area names only. No author, user id, record id or payload value is ever returned to Python (spec §5.2).
- **Never wake an instance:** no request to an instance's public URL. The only HTTP call to an instance goes to `http://lex-backend-<rid>:7000/api/health`, and only when Prometheus shows an available replica.
- **Logging:** logger names under `fleet_metrics`. A failure for one instance is a WARNING; only a run that stored nothing logs at ERROR.
- `LexInstance`'s primary key is `instance_id` (use `.pk`, never `.id`).
- Settings follow the controller's convention: module-level lower-case names in `lex_instance_controller_backend/settings.py`, read as `from lex_instance_controller_backend import settings` and patched in tests with `patch.object(ic_settings, …)`.
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| Path | Responsibility |
|---|---|
| `fleet_metrics/apps.py`, `fleet_metrics/__init__.py` | App config |
| `fleet_metrics/catalog.py` | Names shared by code, storage and dashboards |
| `fleet_metrics/days.py` | UTC day windows |
| `fleet_metrics/models.py`, `fleet_metrics/migrations/0001_initial.py`, `0002_daily_view.py` | Storage (spec §5.5) |
| `fleet_metrics/grants.py` | SELECT for the Grafana reader role |
| `fleet_metrics/sources/id_range.py` | Primary-key binary search for time windows |
| `fleet_metrics/sources/instance_db.py` | Aggregates from one instance database |
| `fleet_metrics/sources/prometheus.py` | Platform metrics |
| `fleet_metrics/sources/controller.py` | Dimensions and deploy facts |
| `fleet_metrics/sources/version.py` | Version without waking; PyPI release dates |
| `fleet_metrics/collector.py` | One instance-day, stored |
| `fleet_metrics/run.py` | Which days; the run; the heartbeat decision |
| `fleet_metrics/heartbeat.py` | Verdict and Better Stack ping |
| `fleet_metrics/management/commands/collect_fleet_metrics.py` | The CronJob's command |
| `fleet_metrics/tests/…` | Tests, one file per module |
| `lex_instance_controller_backend/settings.py` | App registration and `fleet_metrics_*` settings |
| `pytest.ini` | `testpaths` gains `fleet_metrics/tests` |

---

### Task 1: Verify the Prometheus series the collector will query (read-only)

This needs access to a cluster (kubectl credentials for `lex-cluster-2`; see the note on project IDs below). If you do not have it, ask the user to run the commands and paste the output. Nothing here changes the cluster.

**Files:** none. The results decide three settings defaults in Task 2 (`fleet_metrics_api_path`, `fleet_metrics_namespace_label`, and whether the backend Deployment name pattern holds).

- [ ] **Step 1: Reach Prometheus in the test cluster**

The GCP project IDs are not the display names: test is `lex-test-343008`, prod is `superb-blend-305320`; both clusters are called `lex-cluster-2` in `europe-west3`.

```bash
gcloud container clusters get-credentials lex-cluster-2 --region europe-west3 --project lex-test-343008
kubectl config current-context   # must name lex-test-343008
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

- [ ] **Step 2: List the labels on one instance's request series**

In a second shell, with `<rid>` the `resource_identifier` of any running test instance:

```bash
curl -s 'http://localhost:9090/api/v1/series' --data-urlencode 'match[]=nginx_ingress_controller_requests{ingress="ingress-<rid>"}' | python3 -m json.tool | head -60
```

Expected: series carrying `host`, `path`, `status`, `ingress` and a namespace label. Write down:
- the exact `path` value of the API rule (expected `/api(/|$)(.*)`);
- the name of the label holding the ingress's namespace (`exported_namespace` if the ServiceMonitor does not honour labels, `namespace` if it does).

- [ ] **Step 3: Confirm the latency histogram and the replica series exist**

```bash
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=count(nginx_ingress_controller_request_duration_seconds_bucket{ingress="ingress-<rid>"})'
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=kube_deployment_status_replicas_available{deployment="lex-backend-<rid>"}'
```

Expected: a non-zero count for the first; one series for the second (for a dev-type instance, try `lex-devenv-<rid>`).

- [ ] **Step 4: Record the findings**

If the values match the defaults in Task 2 Step 3, go on. If not, use the observed values as those defaults, and add one line to the PR description saying what differed.

---

### Task 2: The app, its settings, its storage and the reader grants

**Files:**
- Create: `fleet_metrics/__init__.py` (empty), `fleet_metrics/apps.py`, `fleet_metrics/catalog.py`, `fleet_metrics/models.py`, `fleet_metrics/grants.py`, `fleet_metrics/migrations/__init__.py` (empty), `fleet_metrics/migrations/0001_initial.py` (generated), `fleet_metrics/migrations/0002_daily_view.py`
- Create: `fleet_metrics/tests/__init__.py` (empty), `fleet_metrics/tests/factories.py`, `fleet_metrics/tests/test_models_and_grants.py`
- Modify: `lex_instance_controller_backend/settings.py` (INSTALLED_APPS; a settings block after `prometheus_host`), `pytest.ini` (`testpaths`)

**Interfaces:**
- Produces: models `CollectionRun`, `InstanceDay`, `MetricValue`, `DeployEvent` (fields below); view `fleet_metrics_daily`; `ensure_reader_grants(using="default") -> bool`; `catalog` constants; settings `fleet_metrics_heartbeat_url`, `fleet_metrics_include_db_aliases`, `fleet_metrics_reader_role`, `fleet_metrics_catchup_days`, `fleet_metrics_prometheus_retention_days`, `fleet_metrics_api_path`, `fleet_metrics_namespace_label`; test helpers `make_client(...)`, `make_instance(...)`.

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
SKIPPED = "skipped"     # not applicable: no database yet, or outside Prometheus retention
DONE = frozenset({OK, EXCLUDED, SKIPPED})

# Where a metric value came from. A re-run replaces only the sources that succeeded.
SOURCE_DB = "db"
SOURCE_PROMETHEUS = "prometheus"
SOURCE_VERSION = "version"

# The usage recorder's state on an instance for a day.
RECORDER_ABSENT = "absent"      # no recorder tables, or no version row yet
RECORDER_PARTIAL = "partial"    # first appeared during the day; the silence check skips it
RECORDER_DISABLED = "disabled"  # LEX_USAGE_METRICS=false
RECORDER_ENABLED = "enabled"

# Where the lex-app version came from.
VERSION_RUNTIME_TABLE = "runtime_table"
VERSION_HEALTH = "health"
VERSION_CARRIED = "carried"
VERSION_UNKNOWN = "unknown"

# Operations that apply an instance's infrastructure and release (K8).
DEPLOY_OPERATION_TYPES = (
    "CREATE", "RECOVER", "RECONFIGURE", "RELEASE", "CHANGE_CLIENT", "UPDATE_INFRA", "PAUSE", "RESUME",
)
TERMINAL_OPERATION_STATUSES = ("SUCCESS", "FAILURE")

# lex-app CalculationModel states that end a run (K3).
TERMINAL_CALCULATION_STATES = ("SUCCESS", "ERROR", "ABORTED", "CANCELLED")

# Response classes (K5). 502/503/504 mean the backend was down, starting or scaled to zero.
STATUS_CLASSES = ("2xx", "3xx", "4xx", "5xx_unavailable", "5xx_app")
UNAVAILABLE_CODES = frozenset({"502", "503", "504"})
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
fleet_metrics_prometheus_retention_days = int(os.getenv("FLEET_METRICS_PROMETHEUS_RETENTION_DAYS", "180"))
fleet_metrics_api_path = os.getenv("FLEET_METRICS_API_PATH", "/api(/|$)(.*)")
fleet_metrics_namespace_label = os.getenv("FLEET_METRICS_NAMESPACE_LABEL", "exported_namespace")
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
"""Storage: one row per instance per UTC day, and a reader role that sees only this app."""

from datetime import date
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from fleet_metrics import catalog
from fleet_metrics.grants import ensure_reader_grants
from fleet_metrics.models import InstanceDay, MetricValue
from fleet_metrics.tests.factories import make_instance
from lex_instance_controller_backend import settings as ic_settings

pytestmark = pytest.mark.integration


def _day(instance, day=date(2026, 10, 1), **extra):
    fields = dict(
        instance=instance, instance_pk=instance.pk, day=day, instance_name=instance.instance_name,
        resource_identifier=instance.resource_identifier, instance_type=instance.type,
        release_stage=instance.release_stage, instance_status=instance.status,
        db_status=catalog.OK, prometheus_status=catalog.OK, controller_status=catalog.OK,
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
        MetricValue.objects.create(instance_day=row, metric="writes", dimension="person", value=3, source=catalog.SOURCE_DB)
        instance_pk = instance.pk
        instance.delete()

        row.refresh_from_db()
        self.assertIsNone(row.instance)
        self.assertEqual(row.instance_pk, instance_pk)
        self.assertEqual(row.values.get().value, 3)

    def test_the_daily_view_joins_dimensions_and_values(self):
        row = _day(make_instance(), client_name="acme")
        MetricValue.objects.create(instance_day=row, metric="writes", dimension="person", value=3, source=catalog.SOURCE_DB)
        with connection.cursor() as cursor:
            cursor.execute("SELECT day, client_name, metric, dimension, value FROM fleet_metrics_daily")
            self.assertEqual(cursor.fetchall(), [(date(2026, 10, 1), "acme", "writes", "person", 3.0)])


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
                "has_table_privilege(%s, 'instance_api_lexinstance', 'SELECT')",
                [self.ROLE, self.ROLE, self.ROLE],
            )
            self.assertEqual(cursor.fetchone(), (True, True, False))
```

- [ ] **Step 6: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_models_and_grants.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'fleet_metrics.models'` (or `grants`).

- [ ] **Step 7: Write the models**

`fleet_metrics/models.py`:

```python
"""Fleet metrics storage (spec §5.5): one row per instance per UTC day.

Days are UTC calendar days, stored as dates. The two timestamps (run start and end,
collection time) follow the controller's convention of naive Berlin time; nothing
derives a day from them.
"""

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
    recorder = models.CharField(max_length=20, default=catalog.RECORDER_ABSENT)
    recorder_silent = models.BooleanField(default=False)

    db_status = models.CharField(max_length=20)
    prometheus_status = models.CharField(max_length=20)
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


class DeployEvent(models.Model):
    """One finished operation of K8's kinds, keyed by the operation it copies."""

    operation_id = models.IntegerField(unique=True)
    instance_pk = models.IntegerField()
    day = models.DateField()
    operation_type = models.CharField(max_length=50)
    outcome = models.CharField(max_length=20)
    duration_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["day"], name="fleet_metrics_deploy_day_idx")]
```

- [ ] **Step 8: Generate the first migration and write the view migration**

Run: `venv/bin/python manage.py makemigrations fleet_metrics --settings=lex_instance_controller_backend.settings_test`
Expected: `fleet_metrics/migrations/0001_initial.py` creating the four models, with a dependency on the latest `instance_api` migration (`0075_instanceconfigmetadata_reflex` at the time of writing).

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

from fleet_metrics.models import CollectionRun, DeployEvent, InstanceDay, MetricValue
from lex_instance_controller_backend import settings

logger = logging.getLogger(__name__)

READER_RELATIONS = tuple(
    model._meta.db_table for model in (CollectionRun, InstanceDay, MetricValue, DeployEvent)
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
Expected: `5 passed`.

- [ ] **Step 11: Commit**

```bash
git add fleet_metrics pytest.ini lex_instance_controller_backend/settings.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): one row per instance per UTC day, readable by Grafana alone"
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
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): a day is a UTC day, built once and passed as a parameter"
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
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): find a day in an unindexed table through its primary key"
```

---

### Task 5: Writes, writers and editors from the audit

**Files:**
- Create: `fleet_metrics/sources/instance_db.py` (first part)
- Create: `fleet_metrics/tests/lex_schema.py`, `fleet_metrics/tests/test_instance_db_audit.py`

**Interfaces:**
- Consumes: `DayWindow`, `window_for` (Task 3); `id_range` (Task 4).
- Produces: `Value = tuple[str, str, float]`; constants `AUDIT`, `STATUS`, `API_KEYS`, `USAGE`, `VERSIONS`; `audit_metrics(cursor, window: DayWindow) -> tuple[list[Value], int]` returning the `writes`, `writers_*` and `editors_1d` values and `editors_1d` itself. Test helpers `create_lex_tables(cursor)`, `add_audit(cursor, **fields) -> int`.

- [ ] **Step 1: Write the lex-shaped fixture**

`fleet_metrics/tests/lex_schema.py`:

```python
"""lex-app's tables, in the shape the collector reads, for the controller's test database.

The columns match what lex-app's own contract test (cluster 6r, and 10.83 for the recorder
tables) pins. ``rest_framework_api_key_apikey`` is not created: the controller has the same
library installed, so its own table serves.
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

RECORDER_TABLES = """
CREATE TABLE lex_usage_daily (
    day date NOT NULL,
    actor_kind text NOT NULL,
    actor_id text NOT NULL,
    area text NOT NULL,
    first_seen timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (day, actor_kind, actor_id, area)
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
"""Writes by channel, writers over 1/7/28 days, and editors — on 25 Oct, when summer time ends."""

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
            add_audit(cursor, at=utc(2026, 10, 25, 12), author="Nightly import")
            add_audit(cursor, at=utc(2026, 10, 25, 13), author="system (initial_data_upload)")
            add_audit(cursor, at=utc(2026, 10, 25, 14), author="Technical User")
            # A save that failed: a write and a writer, but not an editor.
            add_audit(cursor, at=utc(2026, 10, 25, 15), author="Cy Twombly", status="failure")
            # The first instant of the next UTC day.
            add_audit(cursor, at=utc(2026, 10, 26, 0, 0, 0), author="Di Fisher")

    def _collect(self, session_zone):
        with connection.cursor() as cursor:
            cursor.execute(f"SET TIME ZONE '{session_zone}'")
            values, editors = audit_metrics(cursor, DAY)
        return {(metric, dimension): value for metric, dimension, value in values}, editors

    def test_the_day_is_counted_in_utc_whatever_the_session_zone(self):
        for zone in ("UTC", "Europe/Berlin"):
            with self.subTest(session_zone=zone):
                values, editors = self._collect(zone)
                self.assertEqual(values[("writes", "person")], 3.0)
                self.assertEqual(values[("writes", "api_key")], 2.0)
                self.assertEqual(values[("writes", "system")], 1.0)
                self.assertEqual(values[("writers_1d", "")], 2.0, "Ada and Cy; Bo wrote on 24 Oct UTC")
                self.assertEqual(values[("writers_7d", "")], 3.0, "Bo is inside the 7-day window")
                self.assertEqual(values[("writers_28d", "")], 3.0)
                self.assertEqual(values[("editors_1d", "")], 1.0, "Cy's save failed")
                self.assertEqual(editors, 1)

    def test_no_identity_comes_back(self):
        values, _ = self._collect("UTC")
        for (metric, dimension), value in values.items():
            self.assertIsInstance(value, float)
            self.assertNotIn(dimension, {"Ada Lovelace", "Bo Diddley", "Cy Twombly", "Nightly import"})

    def test_the_day_summer_time_starts(self):
        # 28 Mar 2027: 00:30 in Berlin (CET) is 23:30 UTC on 27 Mar; 01:30 UTC is 03:30 CEST.
        with connection.cursor() as cursor:
            add_audit(cursor, at=utc(2027, 3, 27, 23, 30), author="Eve Arden")
            add_audit(cursor, at=utc(2027, 3, 28, 1, 30), author="Flo Kennedy")
            cursor.execute("SET TIME ZONE 'Europe/Berlin'")
            values, _ = audit_metrics(cursor, window_for(datetime(2027, 3, 28).date()))
        values = {(m, d): v for m, d, v in values}
        self.assertEqual(values[("writers_1d", "")], 1.0, "Flo only; Eve wrote on 27 Mar UTC")

    def test_an_empty_audit_is_explicit_zeros(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_logging_auditlogstatus")
            cursor.execute("DELETE FROM audit_logging_auditlog")
        values, editors = self._collect("UTC")
        self.assertEqual(values[("writes", "person")], 0.0)
        self.assertEqual(values[("writers_28d", "")], 0.0)
        self.assertEqual(editors, 0)
```

- [ ] **Step 3: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_audit.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.sources.instance_db'`.

- [ ] **Step 4: Write the first part of the module**

`fleet_metrics/sources/instance_db.py`:

```python
"""Aggregates from one instance's database (spec §5.4.3, §5.4.4).

Every query here returns counts, percentiles or area names: never a person, a record or a
payload value. The SQL does not depend on the session time zone (every bound is a
``timestamptz`` parameter); :func:`instance_cursor` sets UTC anyway.
"""

from __future__ import annotations

from fleet_metrics import catalog
from fleet_metrics.days import DayWindow
from fleet_metrics.sources.id_range import IdRange, id_range

Value = tuple[str, str, float]

AUDIT = "audit_logging_auditlog"
STATUS = "audit_logging_auditlogstatus"
API_KEYS = "rest_framework_api_key_apikey"
USAGE = "lex_usage_daily"
VERSIONS = "lex_runtime_versions"

_TERMINAL = ", ".join(f"'{state}'" for state in catalog.TERMINAL_CALCULATION_STATES)

# How lex-app attributes an audit row (spec §4, K4). ``%%`` is a literal ``%`` under
# psycopg2's parameter style.
_IS_SYSTEM = "(a.author IS NULL OR a.author = '' OR a.author LIKE 'system%%')"
_IS_API_KEY = f"(a.author = 'Technical User' OR a.author IN (SELECT name FROM {API_KEYS}))"
_CHANNEL = f"CASE WHEN {_IS_SYSTEM} THEN 'system' WHEN {_IS_API_KEY} THEN 'api_key' ELSE 'person' END"
_IN_RANGE = "a.id >= %(low)s AND (%(high)s::bigint IS NULL OR a.id < %(high)s::bigint)"

_WRITES_SQL = f"""
WITH w AS (
    SELECT a.author, a.date, {_CHANNEL} AS channel
    FROM {AUDIT} a
    WHERE {_IN_RANGE} AND a.date >= %(since)s AND a.date < %(end)s
)
SELECT
    count(*) FILTER (WHERE channel = 'person'  AND date >= %(start)s),
    count(*) FILTER (WHERE channel = 'api_key' AND date >= %(start)s),
    count(*) FILTER (WHERE channel = 'system'  AND date >= %(start)s),
    count(DISTINCT author) FILTER (WHERE channel = 'person' AND date >= %(start)s),
    count(DISTINCT author) FILTER (WHERE channel = 'person' AND date >= %(since7)s),
    count(DISTINCT author) FILTER (WHERE channel = 'person')
FROM w
"""

_EDITORS_SQL = f"""
SELECT count(DISTINCT a.author)
FROM {AUDIT} a
JOIN {STATUS} s ON s.audit_log_id = a.id
WHERE {_IN_RANGE} AND a.date >= %(start)s AND a.date < %(end)s
  AND s.status = 'success'
  AND COALESCE(a.payload->>'is_calculated', '') NOT IN ({_TERMINAL}, 'IN_PROGRESS')
  AND NOT {_IS_SYSTEM} AND NOT {_IS_API_KEY}
"""


def _range_params(window_range: IdRange) -> dict:
    return {"low": window_range.low, "high": window_range.high}


def audit_metrics(cursor, window: DayWindow) -> tuple[list[Value], int]:
    """K1's proxy and K4's writes, plus ``editors_1d`` for the recorder-silence check (§7).

    ``editors_1d`` counts people with a successful, non-calculation audit row on the day: a
    calculation's row can be created when the run ends, after midnight, with no request that
    day, and a failed save leaves a row although the recorder only records successes.
    """
    since = window.since(28)
    window_range = id_range(cursor, AUDIT, "date", since, window.end)
    if window_range.empty:
        writes = (0, 0, 0, 0, 0, 0)
        editors = 0
    else:
        params = {
            **_range_params(window_range),
            "since": since, "since7": window.since(7), "start": window.start, "end": window.end,
        }
        cursor.execute(_WRITES_SQL, params)
        writes = cursor.fetchone()
        cursor.execute(_EDITORS_SQL, params)
        editors = cursor.fetchone()[0]
    person, api_key, system, writers_1d, writers_7d, writers_28d = writes
    values = [
        ("writes", "person", float(person)),
        ("writes", "api_key", float(api_key)),
        ("writes", "system", float(system)),
        ("writers_1d", "", float(writers_1d)),
        ("writers_7d", "", float(writers_7d)),
        ("writers_28d", "", float(writers_28d)),
        ("editors_1d", "", float(editors)),
    ]
    return values, int(editors)
```

- [ ] **Step 5: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_audit.py -q`
Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
git add fleet_metrics/sources/instance_db.py fleet_metrics/tests/lex_schema.py fleet_metrics/tests/test_instance_db_audit.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): writes, writers and editors, counted in UTC inside the instance"
```

---

### Task 6: Calculation runs from the audit (K3)

**Files:**
- Modify: `fleet_metrics/sources/instance_db.py` (append)
- Test: `fleet_metrics/tests/test_instance_db_calculations.py`

**Interfaces:**
- Produces: `calculation_metrics(cursor, window: DayWindow) -> list[Value]` — `calc_runs` by outcome (always all four), `calc_seconds_p50`/`calc_seconds_p95` (only when a run succeeded), `calc_people_28d`.

The rule (spec §2.2, pinned by lex-app's 6.120–6.122): a run is the audit rows sharing `resource` and a real `calculation_id` (empty or `test_id` means the row is its own run); it is finished when one of its rows carries a terminal `is_calculated`; its outcome is the last terminal state; it started at its earliest status `created_at` and finished at its latest `updated_at`; it belongs to the UTC day it finished.

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

    def test_people_who_ran_a_calculation_in_28_days(self):
        self.assertEqual(self._values()[("calc_people_28d", "")], 2.0, "Ada and Bo; system is not a person")

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

Append to `fleet_metrics/sources/instance_db.py`:

```python
_CALCULATIONS_SQL = f"""
WITH audit_rows AS (
    SELECT a.id, a.author,
           a.resource || ':' ||
               COALESCE(NULLIF(NULLIF(a.calculation_id, ''), 'test_id'), 'row-' || a.id::text) AS run_key,
           a.payload->>'is_calculated' AS state,
           s.created_at, s.updated_at,
           NOT {_IS_SYSTEM} AND NOT {_IS_API_KEY} AS by_person
    FROM {AUDIT} a
    JOIN {STATUS} s ON s.audit_log_id = a.id
    WHERE {_IN_RANGE}
),
runs AS (
    SELECT (array_agg(state ORDER BY id DESC) FILTER (WHERE state IN ({_TERMINAL})))[1] AS outcome,
           min(created_at) AS started_at,
           max(updated_at) AS finished_at,
           (array_agg(author ORDER BY id))[1] AS author,
           bool_or(by_person) AS by_person
    FROM audit_rows
    GROUP BY run_key
),
finished AS (
    SELECT outcome, author, by_person, finished_at,
           extract(epoch FROM finished_at - started_at) AS seconds
    FROM runs
    WHERE outcome IS NOT NULL AND finished_at >= %(since)s AND finished_at < %(end)s
)
SELECT
    count(*) FILTER (WHERE outcome = 'SUCCESS'   AND finished_at >= %(start)s),
    count(*) FILTER (WHERE outcome = 'ERROR'     AND finished_at >= %(start)s),
    count(*) FILTER (WHERE outcome = 'ABORTED'   AND finished_at >= %(start)s),
    count(*) FILTER (WHERE outcome = 'CANCELLED' AND finished_at >= %(start)s),
    percentile_cont(0.5)  WITHIN GROUP (ORDER BY seconds)
        FILTER (WHERE outcome = 'SUCCESS' AND finished_at >= %(start)s),
    percentile_cont(0.95) WITHIN GROUP (ORDER BY seconds)
        FILTER (WHERE outcome = 'SUCCESS' AND finished_at >= %(start)s),
    count(DISTINCT author) FILTER (WHERE by_person)
FROM finished
"""

#: A run can start up to this long before it finishes: workers stop at 24 hours.
RUN_LOOKBACK_DAYS = 2


def calculation_metrics(cursor, window: DayWindow) -> list[Value]:
    """K3: finished runs by outcome, durations of the successful ones, and K2's calculation reach."""
    since = window.since(28)
    window_range = id_range(cursor, AUDIT, "date", since - (window.end - window.start) * RUN_LOOKBACK_DAYS, window.end)
    if window_range.empty:
        success = error = aborted = cancelled = people = 0
        p50 = p95 = None
    else:
        cursor.execute(
            _CALCULATIONS_SQL,
            {**_range_params(window_range), "since": since, "start": window.start, "end": window.end},
        )
        success, error, aborted, cancelled, p50, p95, people = cursor.fetchone()
    values = [
        ("calc_runs", "success", float(success)),
        ("calc_runs", "error", float(error)),
        ("calc_runs", "aborted", float(aborted)),
        ("calc_runs", "cancelled", float(cancelled)),
        ("calc_people_28d", "", float(people)),
    ]
    if p50 is not None:
        values.append(("calc_seconds_p50", "", float(p50)))
    if p95 is not None:
        values.append(("calc_seconds_p95", "", float(p95)))
    return values
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_calculations.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/sources/instance_db.py fleet_metrics/tests/test_instance_db_calculations.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): count calculation runs, not audit rows, by the day they finished"
```

---

### Task 7: The recorder's tables, the whole instance, and the connection

**Files:**
- Modify: `fleet_metrics/sources/instance_db.py` (append)
- Test: `fleet_metrics/tests/test_instance_db_recorder.py`

**Interfaces:**
- Produces:
  - `recorder_state(cursor, window) -> tuple[str, Optional[str]]` — (`catalog.RECORDER_*`, version or None)
  - `recorder_metrics(cursor, window) -> list[Value]` — `active_people_1d/7d/28d`, `api_keys_28d`, `area_people_28d{area}`, `area_api_keys_28d{area}`
  - `InstanceDbResult(values: list[Value], recorder_state: str, version: Optional[str], recorder_silent: bool)`
  - `collect(cursor, window) -> InstanceDbResult`
  - `database_host(config_metadata) -> str`; `instance_cursor(instance, *, connect=psycopg2.connect)` (context manager yielding a cursor)

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_instance_db_recorder.py`:

```python
"""The recorder's state per day, its counts, the silence rule and the read-only connection."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test import SimpleTestCase, TestCase

from fleet_metrics import catalog
from fleet_metrics.days import window_for
from fleet_metrics.sources import instance_db
from fleet_metrics.tests.lex_schema import add_audit, create_lex_tables
from lex_instance_controller_backend import settings as ic_settings

DAY = window_for(date(2026, 10, 25))


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.integration
class RecorderTests(TestCase):
    def _cursor_with(self, *, recorder=True, versions=(), usage=()):
        cursor = connection.cursor()
        create_lex_tables(cursor, recorder=recorder)
        for version, first_seen, enabled in versions:
            cursor.execute(
                "INSERT INTO lex_runtime_versions (version, first_seen, last_seen, usage_enabled) VALUES (%s, %s, %s, %s)",
                [version, first_seen, first_seen, enabled],
            )
        for day, kind, actor, area in usage:
            cursor.execute(
                "INSERT INTO lex_usage_daily (day, actor_kind, actor_id, area) VALUES (%s, %s, %s, %s)",
                [day, kind, actor, area],
            )
        return cursor

    def test_no_tables_means_absent(self):
        cursor = self._cursor_with(recorder=False)
        self.assertEqual(instance_db.recorder_state(cursor, DAY), (catalog.RECORDER_ABSENT, None))

    def test_a_version_from_before_the_day_means_enabled(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 20), True)])
        self.assertEqual(instance_db.recorder_state(cursor, DAY), (catalog.RECORDER_ENABLED, "2.4.0"))

    def test_a_recorder_that_appeared_during_the_day_is_partial(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 25, 9), True)])
        self.assertEqual(instance_db.recorder_state(cursor, DAY)[0], catalog.RECORDER_PARTIAL)

    def test_switched_off_is_disabled(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 20), False)])
        self.assertEqual(instance_db.recorder_state(cursor, DAY)[0], catalog.RECORDER_DISABLED)

    def test_the_version_is_the_one_that_ran_at_the_end_of_the_day(self):
        cursor = self._cursor_with(versions=[
            ("2.4.0", utc(2026, 10, 20), True), ("2.4.1", utc(2026, 10, 27), True),
        ])
        self.assertEqual(instance_db.recorder_state(cursor, DAY)[1], "2.4.0")

    def test_people_keys_and_areas(self):
        cursor = self._cursor_with(
            versions=[("2.4.0", utc(2026, 10, 1), True)],
            usage=[
                (date(2026, 10, 25), "person", "7", "grid"),
                (date(2026, 10, 25), "person", "7", "exports"),
                (date(2026, 10, 25), "person", "8", "grid"),
                (date(2026, 10, 20), "person", "9", "history"),
                (date(2026, 10, 1), "person", "10", "grid"),   # 25 days before: inside 28
                (date(2026, 9, 27), "person", "11", "grid"),   # 29 days before: outside
                (date(2026, 10, 25), "api_key", "Nightly import", "grid"),
            ],
        )
        values = {(m, d): v for m, d, v in instance_db.recorder_metrics(cursor, DAY)}
        self.assertEqual(values[("active_people_1d", "")], 2.0)
        self.assertEqual(values[("active_people_7d", "")], 3.0)
        self.assertEqual(values[("active_people_28d", "")], 4.0)
        self.assertEqual(values[("api_keys_28d", "")], 1.0)
        self.assertEqual(values[("area_people_28d", "grid")], 3.0)
        self.assertEqual(values[("area_people_28d", "exports")], 1.0)
        self.assertEqual(values[("area_api_keys_28d", "grid")], 1.0)

    def test_an_edit_the_recorder_did_not_see_is_silence(self):
        cursor = self._cursor_with(versions=[("2.4.0", utc(2026, 10, 1), True)])
        add_audit(cursor, at=utc(2026, 10, 25, 9), author="Ada Lovelace")
        result = instance_db.collect(cursor, DAY)
        self.assertTrue(result.recorder_silent)

    def test_the_same_edit_with_a_row_is_not_silence(self):
        cursor = self._cursor_with(
            versions=[("2.4.0", utc(2026, 10, 1), True)],
            usage=[(date(2026, 10, 25), "person", "7", "records")],
        )
        add_audit(cursor, at=utc(2026, 10, 25, 9), author="Ada Lovelace")
        result = instance_db.collect(cursor, DAY)
        self.assertFalse(result.recorder_silent)
        self.assertEqual(result.recorder_state, catalog.RECORDER_ENABLED)
        self.assertEqual(result.version, "2.4.0")

    def test_without_a_recorder_the_audit_still_counts(self):
        cursor = self._cursor_with(recorder=False)
        add_audit(cursor, at=utc(2026, 10, 25, 9), author="Ada Lovelace")
        result = instance_db.collect(cursor, DAY)
        metrics = {(m, d) for m, d, _ in result.values}
        self.assertIn(("writers_1d", ""), metrics)
        self.assertIn(("calc_runs", "success"), metrics)
        self.assertNotIn(("active_people_1d", ""), metrics)
        self.assertFalse(result.recorder_silent, "No recorder, so nothing can be silent")


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

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_recorder.py -q`
Expected: `AttributeError: module 'fleet_metrics.sources.instance_db' has no attribute 'recorder_state'`.

- [ ] **Step 3: Append the recorder queries, `collect` and the connection**

Add to the imports at the top of `fleet_metrics/sources/instance_db.py`:

```python
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

import psycopg2

from lex_instance_controller_backend import settings
```

Append:

```python
_RECORDER_PRESENT_SQL = (
    f"SELECT to_regclass('public.{USAGE}') IS NOT NULL AND to_regclass('public.{VERSIONS}') IS NOT NULL"
)
_RECORDER_STATE_SQL = f"""
SELECT (SELECT min(first_seen) FROM {VERSIONS}), v.version, v.usage_enabled
FROM (SELECT version, usage_enabled FROM {VERSIONS}
      WHERE first_seen < %(end)s ORDER BY first_seen DESC LIMIT 1) v
"""
_USAGE_SQL = f"""
SELECT
    count(DISTINCT actor_id) FILTER (WHERE actor_kind = 'person' AND day = %(day)s),
    count(DISTINCT actor_id) FILTER (WHERE actor_kind = 'person' AND day > %(day)s - 7),
    count(DISTINCT actor_id) FILTER (WHERE actor_kind = 'person'),
    count(DISTINCT actor_id) FILTER (WHERE actor_kind = 'api_key')
FROM {USAGE}
WHERE day > %(day)s - 28 AND day <= %(day)s
"""
_AREAS_SQL = f"""
SELECT area,
       count(DISTINCT actor_id) FILTER (WHERE actor_kind = 'person'),
       count(DISTINCT actor_id) FILTER (WHERE actor_kind = 'api_key')
FROM {USAGE}
WHERE day > %(day)s - 28 AND day <= %(day)s
GROUP BY area
ORDER BY area
"""


def recorder_state(cursor, window: DayWindow) -> tuple[str, Optional[str]]:
    """The recorder's state on the day, and the lex-app version that ran at its end."""
    cursor.execute(_RECORDER_PRESENT_SQL)
    if not cursor.fetchone()[0]:
        return catalog.RECORDER_ABSENT, None
    cursor.execute(_RECORDER_STATE_SQL, {"end": window.end})
    row = cursor.fetchone()
    if row is None:
        return catalog.RECORDER_ABSENT, None
    first_seen, version, enabled = row
    if not enabled:
        return catalog.RECORDER_DISABLED, version
    if first_seen >= window.start:
        return catalog.RECORDER_PARTIAL, version
    return catalog.RECORDER_ENABLED, version


def recorder_metrics(cursor, window: DayWindow) -> list[Value]:
    """K1, K2 and K4's reads from the recorder's presence rows."""
    params = {"day": window.day}
    cursor.execute(_USAGE_SQL, params)
    people_1d, people_7d, people_28d, keys_28d = cursor.fetchone()
    values = [
        ("active_people_1d", "", float(people_1d)),
        ("active_people_7d", "", float(people_7d)),
        ("active_people_28d", "", float(people_28d)),
        ("api_keys_28d", "", float(keys_28d)),
    ]
    cursor.execute(_AREAS_SQL, params)
    for area, people, keys in cursor.fetchall():
        values.append(("area_people_28d", area, float(people)))
        values.append(("area_api_keys_28d", area, float(keys)))
    return values


@dataclass(frozen=True)
class InstanceDbResult:
    values: list[Value]
    recorder_state: str
    version: Optional[str]
    recorder_silent: bool


def collect(cursor, window: DayWindow) -> InstanceDbResult:
    """Everything one instance database contributes to one day."""
    audit_values, editors = audit_metrics(cursor, window)
    values = audit_values + calculation_metrics(cursor, window)
    state, version = recorder_state(cursor, window)
    silent = False
    if state in (catalog.RECORDER_ENABLED, catalog.RECORDER_PARTIAL):
        usage = recorder_metrics(cursor, window)
        values += usage
        active_today = next(v for m, _, v in usage if m == "active_people_1d")
        silent = state == catalog.RECORDER_ENABLED and editors > 0 and active_today == 0
    return InstanceDbResult(values=values, recorder_state=state, version=version, recorder_silent=silent)


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

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_instance_db_recorder.py -q`
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/sources/instance_db.py fleet_metrics/tests/test_instance_db_recorder.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): read the recorder, notice when it is silent, connect read-only"
```

---

### Task 8: Platform metrics from Prometheus

**Files:**
- Create: `fleet_metrics/sources/prometheus.py`
- Test: `fleet_metrics/tests/test_prometheus.py`

**Interfaces:**
- Produces: `PrometheusError`; `Prometheus(query_url: str, *, session=None, timeout=30.0)` with `instant(query, at) -> list[dict]` and `range(query, start, end, step_seconds) -> list[dict]`; `api_selector(instance, namespace: str) -> str`; `backend_deployment(instance) -> str`; `status_class(code: str) -> Optional[str]`; `platform_metrics(client, instance, window, namespace) -> list[Value]`; `backend_available_now(client, instance, namespace, now) -> bool`.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_prometheus.py`:

```python
"""Request classes, latency, and scale-to-zero, from Prometheus, for one instance and UTC day."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from fleet_metrics.days import window_for
from fleet_metrics.sources import prometheus
from lex_instance_controller_backend import settings as ic_settings

pytestmark = pytest.mark.unit
DAY = window_for(date(2026, 10, 25))
INSTANCE = SimpleNamespace(
    resource_identifier="books-prod-1", type="prod",
    config_metadata=SimpleNamespace(url="https://books.example.test"),
)


class FakeClient:
    """Answers by the first query fragment that matches."""

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


class SelectorTests(SimpleTestCase):
    def test_api_traffic_is_the_instance_host_on_the_api_rule_without_websockets(self):
        with patch.object(ic_settings, "fleet_metrics_api_path", "/api(/|$)(.*)"), \
                patch.object(ic_settings, "fleet_metrics_namespace_label", "exported_namespace"):
            self.assertEqual(
                prometheus.api_selector(INSTANCE, "default"),
                '{ingress="ingress-books-prod-1",host="books.example.test",path="/api(/|$)(.*)",'
                'status!="101",exported_namespace="default"}',
            )

    def test_dev_instances_run_a_devenv_deployment(self):
        self.assertEqual(prometheus.backend_deployment(INSTANCE), "lex-backend-books-prod-1")
        self.assertEqual(
            prometheus.backend_deployment(SimpleNamespace(resource_identifier="x-dev-2", type="dev")),
            "lex-devenv-x-dev-2",
        )

    def test_status_classes(self):
        self.assertEqual(
            [prometheus.status_class(code) for code in ("200", "304", "499", "502", "503", "504", "500", "101")],
            ["2xx", "3xx", "4xx", "5xx_unavailable", "5xx_unavailable", "5xx_unavailable", "5xx_app", None],
        )


class PlatformMetricsTests(SimpleTestCase):
    def test_requests_latency_and_scale_to_zero(self):
        replicas = [0, 0, 1, 1, 0, 1]   # six minutes: 3 down, 2 starts
        client = FakeClient(
            instant={
                "nginx_ingress_controller_requests": [vector("200", 900), vector("503", 12), vector("500", 3)],
                "histogram_quantile": [{"metric": {}, "value": [0, "1.25"]}],
            },
            ranges={"kube_deployment_status_replicas_available": [
                {"metric": {}, "values": [[i, str(v)] for i, v in enumerate(replicas)]}
            ]},
        )
        values = {(m, d): v for m, d, v in prometheus.platform_metrics(client, INSTANCE, DAY, "default")}

        self.assertEqual(values[("api_requests", "2xx")], 900.0)
        self.assertEqual(values[("api_requests", "5xx_unavailable")], 12.0)
        self.assertEqual(values[("api_requests", "5xx_app")], 3.0)
        self.assertEqual(values[("api_requests", "4xx")], 0.0, "explicit zero, not a missing row")
        self.assertEqual(values[("api_seconds_p95", "")], 1.25)
        self.assertEqual(values[("backend_down_minutes", "")], 3.0)
        self.assertEqual(values[("backend_starts", "")], 2.0)

        instant_times = {q[2] for q in client.queries if q[0] == "instant"}
        self.assertEqual(instant_times, {DAY.end}, "every instant query is evaluated at the day's end")
        range_query = next(q for q in client.queries if q[0] == "range")
        self.assertEqual((range_query[2], range_query[3], range_query[4]),
                         (DAY.start, DAY.end - timedelta(seconds=60), 60))

    def test_no_traffic_is_zeros_and_no_latency(self):
        values = {(m, d): v for m, d, v in prometheus.platform_metrics(FakeClient(), INSTANCE, DAY, "default")}
        self.assertEqual(values[("api_requests", "2xx")], 0.0)
        self.assertNotIn(("api_seconds_p95", ""), values)
        self.assertNotIn(("backend_starts", ""), values, "no replica series, no claim about starts")

    def test_a_nan_latency_is_dropped(self):
        client = FakeClient(instant={"histogram_quantile": [{"metric": {}, "value": [0, "NaN"]}]})
        values = {(m, d) for m, d, _ in prometheus.platform_metrics(client, INSTANCE, DAY, "default")}
        self.assertNotIn(("api_seconds_p95", ""), values)

    def test_available_now(self):
        now = datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)
        up = FakeClient(instant={"kube_deployment_status_replicas_available": [{"metric": {}, "value": [0, "1"]}]})
        down = FakeClient(instant={"kube_deployment_status_replicas_available": [{"metric": {}, "value": [0, "0"]}]})
        self.assertTrue(prometheus.backend_available_now(up, INSTANCE, "default", now))
        self.assertFalse(prometheus.backend_available_now(down, INSTANCE, "default", now))
        self.assertFalse(prometheus.backend_available_now(FakeClient(), INSTANCE, "default", now))


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

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_prometheus.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.sources.prometheus'`.

- [ ] **Step 3: Write the module**

`fleet_metrics/sources/prometheus.py`:

```python
"""Platform metrics for one instance and UTC day, from the in-cluster Prometheus (spec §5.4.5).

Queries are evaluated at the end of the day with ``[1d]`` windows. API traffic is selected by
the instance's ingress, its own host and the ``/api`` rule, because ``ingress-<rid>`` alone
also carries the single-page app, Streamlit, the AI host and Flower. WebSocket upgrades
(status 101) are excluded: their "duration" is the connection's lifetime.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import requests

from fleet_metrics import catalog
from fleet_metrics.days import DayWindow
from lex_instance_controller_backend import settings

Value = tuple[str, str, float]


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
    host = urlparse(instance.config_metadata.url).hostname or ""
    matchers = [
        f'ingress="ingress-{instance.resource_identifier}"',
        f'host="{host}"',
        f'path="{settings.fleet_metrics_api_path}"',
        'status!="101"',
    ]
    if namespace and settings.fleet_metrics_namespace_label:
        matchers.append(f'{settings.fleet_metrics_namespace_label}="{namespace}"')
    return "{" + ",".join(matchers) + "}"


def backend_deployment(instance) -> str:
    prefix = "lex-devenv" if instance.type == "dev" else "lex-backend"
    return f"{prefix}-{instance.resource_identifier}"


def _replica_query(instance, namespace: str) -> str:
    matchers = [f'deployment="{backend_deployment(instance)}"']
    if namespace:
        matchers.insert(0, f'namespace="{namespace}"')
    return "max(kube_deployment_status_replicas_available{" + ",".join(matchers) + "})"


def status_class(code: str) -> Optional[str]:
    if code in catalog.UNAVAILABLE_CODES:
        return "5xx_unavailable"
    if code.startswith("5"):
        return "5xx_app"
    if code[:1] in ("2", "3", "4"):
        return f"{code[0]}xx"
    return None


def _starts(samples: list[float]) -> int:
    return sum(1 for before, after in zip(samples, samples[1:]) if before == 0 and after > 0)


def platform_metrics(client, instance, window: DayWindow, namespace: str) -> list[Value]:
    """K5 (request classes), K9 (API p95) and K6 (minutes scaled down, starts from zero)."""
    selector = api_selector(instance, namespace)
    counts = {cls: 0.0 for cls in catalog.STATUS_CLASSES}
    requests_query = f"sum by (status) (increase(nginx_ingress_controller_requests{selector}[1d]))"
    for series in client.instant(requests_query, window.end):
        cls = status_class(series["metric"].get("status", ""))
        if cls is not None:
            counts[cls] += float(series["value"][1])
    values: list[Value] = [("api_requests", cls, value) for cls, value in counts.items()]

    latency_selector = selector[:-1] + ',status!~"5.."}'
    latency_query = (
        "histogram_quantile(0.95, sum by (le) "
        f"(increase(nginx_ingress_controller_request_duration_seconds_bucket{latency_selector}[1d])))"
    )
    latency = client.instant(latency_query, window.end)
    if latency:
        p95 = float(latency[0]["value"][1])
        if math.isfinite(p95):
            values.append(("api_seconds_p95", "", p95))

    replicas = client.range(
        _replica_query(instance, namespace), window.start, window.end - timedelta(seconds=60), 60,
    )
    if replicas:
        samples = [float(value) for _, value in replicas[0]["values"]]
        values.append(("backend_down_minutes", "", float(sum(1 for v in samples if v == 0))))
        values.append(("backend_starts", "", float(_starts(samples))))
    return values


def backend_available_now(client, instance, namespace: str, now: datetime) -> bool:
    """Whether the backend has a replica right now; the version probe asks before calling it."""
    result = client.instant(_replica_query(instance, namespace), now)
    return bool(result) and float(result[0]["value"][1]) > 0
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_prometheus.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/sources/prometheus.py fleet_metrics/tests/test_prometheus.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): request classes, latency and cold starts per instance day"
```

---

### Task 9: What the controller knows: dimensions and deploys

**Files:**
- Create: `fleet_metrics/sources/controller.py`
- Test: `fleet_metrics/tests/test_controller.py`

**Interfaces:**
- Produces: `Snapshot` (frozen dataclass: `instance_name, resource_identifier, instance_type, release_stage, instance_status, client_id, client_name, autoscaling`); `snapshot(instance) -> Snapshot`; `Deploy(operation_id: int, operation_type: str, outcome: str, duration_seconds: Optional[float])`; `deploys(instance, window) -> list[Deploy]`.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_controller.py`:

```python
"""Deploys by UTC day: an operation that ends at 01:30 Berlin on 25 Oct ended on 24 Oct."""

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


class DeployTests(TestCase):
    def setUp(self):
        self.instance = make_instance()

    def test_the_utc_day_not_the_berlin_date(self):
        # 01:30 CEST on 25 Oct is 23:30 UTC on 24 Oct.
        before = op(self.instance, "RELEASE", "SUCCESS",
                    datetime(2026, 10, 25, 1, 0), datetime(2026, 10, 25, 1, 30))
        after = op(self.instance, "RELEASE", "FAILURE",
                   datetime(2026, 10, 25, 9, 0), datetime(2026, 10, 25, 9, 10))

        on_24 = controller.deploys(self.instance, window_for(date(2026, 10, 24)))
        on_25 = controller.deploys(self.instance, window_for(date(2026, 10, 25)))

        self.assertEqual([d.operation_id for d in on_24], [before.id])
        self.assertEqual([(d.operation_id, d.outcome, d.duration_seconds) for d in on_25],
                         [(after.id, "FAILURE", 600.0)])

    def test_only_deploys_that_finished(self):
        op(self.instance, "RESTART", "SUCCESS", datetime(2026, 10, 20, 9), datetime(2026, 10, 20, 9, 5))
        op(self.instance, "RELEASE", "RUNNING", datetime(2026, 10, 20, 10), None)
        self.assertEqual(controller.deploys(self.instance, window_for(date(2026, 10, 20))), [])

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
"""What the Instance Controller itself knows: an instance's dimensions and its deploys."""

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


@dataclass(frozen=True)
class Deploy:
    operation_id: int
    operation_type: str
    outcome: str
    duration_seconds: Optional[float]


_DEPLOYS_SQL = f"""
SELECT id, operation_type, status, EXTRACT(EPOCH FROM completed_at - started_at)
FROM {InstanceOperation._meta.db_table}
WHERE instance_id = %s
  AND operation_type = ANY(%s)
  AND status = ANY(%s)
  AND completed_at >= %s AND completed_at < %s
ORDER BY id
"""


def deploys(instance, window: DayWindow) -> list[Deploy]:
    """Operations of K8's kinds that finished on the UTC day.

    Read in SQL against aware UTC bounds, with the duration computed in SQL: the ORM would
    hand back naive Berlin times, which are ambiguous in the hour Berlin repeats when summer
    time ends (spec §6, rule 4).
    """
    with connection.cursor() as cursor:
        cursor.execute(_DEPLOYS_SQL, [
            instance.pk, list(catalog.DEPLOY_OPERATION_TYPES), list(catalog.TERMINAL_OPERATION_STATUSES),
            window.start, window.end,
        ])
        rows = cursor.fetchall()
    return [
        Deploy(operation_id=row[0], operation_type=row[1], outcome=row[2],
               duration_seconds=float(row[3]) if row[3] is not None else None)
        for row in rows
    ]
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_controller.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/sources/controller.py fleet_metrics/tests/test_controller.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): deploys by the UTC day they finished, read past the naive ORM"
```

---

### Task 10: The version, without waking anything

**Files:**
- Create: `fleet_metrics/sources/version.py`
- Test: `fleet_metrics/tests/test_version.py`

**Interfaces:**
- Consumes: `decrypt_runtime_metadata(token, api_key)` from `instance_api/views/instance_operations/runtime_health.py`.
- Produces: `PYPI_URL`; `in_cluster_health_url(instance) -> str`; `version_in_cluster(instance, *, session=requests, timeout=3.0) -> Optional[str]`; `pypi_release_dates(*, session=requests, timeout=10.0) -> dict[str, date]`; `version_age_days(version: str, day: date, release_dates: dict[str, date]) -> Optional[int]`.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_version.py`:

```python
"""The version from the in-cluster health token, and its age from PyPI."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from django.test import SimpleTestCase

from fleet_metrics.sources import version
from instance_api.views.instance_operations.runtime_health import _derive_fernet_key

pytestmark = pytest.mark.unit
INSTANCE = SimpleNamespace(resource_identifier="books-prod-1",
                           config_metadata=SimpleNamespace(api_key="raw-api-key", url="https://books.example.test"))


class Session:
    def __init__(self, body):
        self.body = body
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: self.body)


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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_version.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.sources.version'`.

- [ ] **Step 3: Write the module**

`fleet_metrics/sources/version.py`:

```python
"""The lex-app version an instance runs, without waking it (spec §5.4.6)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import requests

from instance_api.views.instance_operations.runtime_health import decrypt_runtime_metadata

PYPI_URL = "https://pypi.org/pypi/lex-app/json"


def in_cluster_health_url(instance) -> str:
    return f"http://lex-backend-{instance.resource_identifier}:7000/api/health"


def version_in_cluster(instance, *, session=requests, timeout: float = 3.0) -> Optional[str]:
    """The version inside the instance's encrypted health token, through the in-cluster service.

    Never the public URL: a request through the ingress counts towards KEDA's scale-up
    trigger and would wake a backend that has scaled to zero. The caller asks Prometheus
    first whether a replica is up.
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
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_version.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/sources/version.py fleet_metrics/tests/test_version.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): read the version through the cluster, age it by PyPI"
```

---

### Task 11: One instance-day, stored

**Files:**
- Create: `fleet_metrics/collector.py`
- Test: `fleet_metrics/tests/test_collector.py`

**Interfaces:**
- Consumes: everything from Tasks 2–10.
- Produces: `RunContext` (dataclass: `prometheus: Optional[Prometheus]`, `release_dates: dict[str, date]`, `latest_day: date`, `namespace: str`, `include_db_aliases: bool`, `prometheus_retention_days: int`, `now: datetime`); `Outcome(status: str, values: list[Value] = [], error: str = "")`; `collect_instance_day(instance, window, ctx, run=None) -> InstanceDay`.

The storage rules (spec §5.2):
- Dimensions are refreshed on every collection.
- A source's status and values are replaced when it succeeds (`catalog.DONE`) — or when the row has never had that source succeed. When it fails after an earlier success, the earlier values and status are kept and the failure is only noted in `errors`: a re-run never destroys data it cannot reproduce (Prometheus keeps 180 days).
- Version values are re-derived each time.
- Deploy events are upserted by operation id when the controller source succeeds.

- [ ] **Step 1: Write the failing tests**

`fleet_metrics/tests/test_collector.py`:

```python
"""One instance on one UTC day: every source, the storage rules, and the version fallbacks."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from django.test import TestCase

from fleet_metrics import catalog
from fleet_metrics.collector import RunContext, collect_instance_day
from fleet_metrics.days import window_for
from fleet_metrics.models import DeployEvent, InstanceDay
from fleet_metrics.sources.controller import Deploy
from fleet_metrics.sources.instance_db import InstanceDbResult
from fleet_metrics.tests.factories import make_instance

pytestmark = pytest.mark.integration
DAY = date(2026, 10, 25)
WINDOW = window_for(DAY)
NOW = datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)


class FakePrometheus:
    def __init__(self, fail=False, up=False):
        self.fail, self.up = fail, up

    def instant(self, query, at):
        if self.fail:
            raise ConnectionError("prometheus unreachable")
        if "replicas_available" in query:
            return [{"metric": {}, "value": [0, "1" if self.up else "0"]}]
        if "nginx_ingress_controller_requests" in query:
            return [{"metric": {"status": "200"}, "value": [0, "100"]}]
        return []

    def range(self, query, start, end, step_seconds):
        if self.fail:
            raise ConnectionError("prometheus unreachable")
        return []


def ctx(prometheus=None, **overrides):
    fields = dict(
        prometheus=prometheus or FakePrometheus(), release_dates={"2.4.0": date(2026, 10, 5)},
        latest_day=DAY, namespace="default", include_db_aliases=False,
        prometheus_retention_days=180, now=NOW,
    )
    fields.update(overrides)
    return RunContext(**fields)


DB_RESULT = InstanceDbResult(
    values=[("writes", "person", 3.0), ("writers_1d", "", 2.0)],
    recorder_state=catalog.RECORDER_ENABLED, version="2.4.0", recorder_silent=False,
)


@contextmanager
def fake_cursor(instance):
    yield object()


class CollectInstanceDayTests(TestCase):
    def setUp(self):
        self.instance = make_instance()
        patchers = [
            patch("fleet_metrics.collector.instance_db.instance_cursor", fake_cursor),
            patch("fleet_metrics.collector.instance_db.collect", return_value=DB_RESULT),
            patch("fleet_metrics.collector.controller.deploys", return_value=[]),
        ]
        self.mocks = [p.start() for p in patchers]
        for p in patchers:
            self.addCleanup(p.stop)

    def _values(self, row):
        return {(v.metric, v.dimension): v.value for v in row.values.all()}

    def test_one_row_with_dimensions_statuses_and_values(self):
        row = collect_instance_day(self.instance, WINDOW, ctx())

        self.assertEqual((row.instance_pk, row.day, row.instance_name, row.client_name),
                         (self.instance.pk, DAY, "books", "acme"))
        self.assertEqual((row.db_status, row.prometheus_status, row.controller_status),
                         (catalog.OK, catalog.OK, catalog.OK))
        self.assertEqual((row.lex_app_version, row.version_source, row.recorder),
                         ("2.4.0", catalog.VERSION_RUNTIME_TABLE, catalog.RECORDER_ENABLED))
        values = self._values(row)
        self.assertEqual(values[("writes", "person")], 3.0)
        self.assertEqual(values[("api_requests", "2xx")], 100.0)
        self.assertEqual(values[("version_age_days", "")], 20.0)

    def test_running_it_twice_changes_nothing(self):
        collect_instance_day(self.instance, WINDOW, ctx())
        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(InstanceDay.objects.count(), 1)
        self.assertEqual(row.values.filter(metric="writes", dimension="person").count(), 1)

    def test_a_failure_after_a_success_keeps_what_was_collected(self):
        collect_instance_day(self.instance, WINDOW, ctx())
        row = collect_instance_day(self.instance, WINDOW, ctx(prometheus=FakePrometheus(fail=True)))

        self.assertEqual(row.prometheus_status, catalog.OK, "the earlier success stands")
        self.assertEqual(self._values(row)[("api_requests", "2xx")], 100.0)
        self.assertIn("kept", row.errors["prometheus"])

    def test_a_first_failure_is_recorded_as_one(self):
        row = collect_instance_day(self.instance, WINDOW, ctx(prometheus=FakePrometheus(fail=True)))
        self.assertEqual(row.prometheus_status, catalog.ERROR)
        self.assertIn("unreachable", row.errors["prometheus"])

    def test_a_separate_database_server_is_excluded(self):
        instance = make_instance("vault", rid="vault-prod-3", db_server_alias="COMPLIANCE")
        row = collect_instance_day(instance, WINDOW, ctx())
        self.assertEqual(row.db_status, catalog.EXCLUDED)
        self.assertNotIn(("writes", "person"), self._values(row))
        self.mocks[1].assert_not_called()

    def test_outside_prometheus_retention_is_skipped(self):
        row = collect_instance_day(self.instance, window_for(date(2026, 1, 2)),
                                   ctx(latest_day=DAY, prometheus_retention_days=180))
        self.assertEqual(row.prometheus_status, catalog.SKIPPED)

    def test_the_version_is_carried_when_nothing_was_deployed(self):
        self.mocks[1].return_value = InstanceDbResult(
            values=[], recorder_state=catalog.RECORDER_ABSENT, version=None, recorder_silent=False,
        )
        collect_instance_day(self.instance, window_for(date(2026, 10, 24)), ctx())
        InstanceDay.objects.filter(day=date(2026, 10, 24)).update(lex_app_version="2.3.2")

        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual((row.lex_app_version, row.version_source), ("2.3.2", catalog.VERSION_CARRIED))

    def test_a_deploy_breaks_the_carry(self):
        self.mocks[1].return_value = InstanceDbResult(
            values=[], recorder_state=catalog.RECORDER_ABSENT, version=None, recorder_silent=False,
        )
        collect_instance_day(self.instance, window_for(date(2026, 10, 24)), ctx())
        InstanceDay.objects.filter(day=date(2026, 10, 24)).update(lex_app_version="2.3.2")
        self.mocks[2].return_value = [Deploy(operation_id=41, operation_type="RELEASE", outcome="SUCCESS",
                                             duration_seconds=90.0)]

        row = collect_instance_day(self.instance, WINDOW, ctx())
        self.assertEqual(row.version_source, catalog.VERSION_UNKNOWN)
        self.assertEqual(DeployEvent.objects.get(operation_id=41).day, DAY)

    def test_the_health_probe_only_for_the_latest_day_and_only_when_up(self):
        self.mocks[1].return_value = InstanceDbResult(
            values=[], recorder_state=catalog.RECORDER_ABSENT, version=None, recorder_silent=False,
        )
        with patch("fleet_metrics.collector.version.version_in_cluster", return_value="2.3.2") as probe:
            down = collect_instance_day(self.instance, WINDOW, ctx(prometheus=FakePrometheus(up=False)))
            self.assertEqual(down.version_source, catalog.VERSION_UNKNOWN)
            probe.assert_not_called()

            up = collect_instance_day(self.instance, WINDOW, ctx(prometheus=FakePrometheus(up=True)))
            self.assertEqual((up.lex_app_version, up.version_source), ("2.3.2", catalog.VERSION_HEALTH))

            collect_instance_day(self.instance, window_for(date(2026, 10, 20)), ctx(prometheus=FakePrometheus(up=True)))
            self.assertEqual(probe.call_count, 1, "a backfilled day never probes")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_collector.py -q`
Expected: `ModuleNotFoundError: No module named 'fleet_metrics.collector'`.

- [ ] **Step 3: Write the module**

`fleet_metrics/collector.py`:

```python
"""One instance on one UTC day, from every source, stored (spec §5.4.2, §5.5).

Each source runs on its own; a failing source marks only itself. The storage rules never
destroy data a re-run cannot reproduce: a source that fails after an earlier success keeps
the earlier values (Prometheus keeps 180 days, so a late re-run could otherwise erase a
day it can no longer see).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from django.db import transaction

from fleet_metrics import catalog
from fleet_metrics.days import DayWindow
from fleet_metrics.models import CollectionRun, DeployEvent, InstanceDay, MetricValue
from fleet_metrics.sources import controller, instance_db, prometheus, version

logger = logging.getLogger(__name__)

Value = tuple[str, str, float]


@dataclass
class RunContext:
    """What every instance-day of one run shares."""

    prometheus: Optional[prometheus.Prometheus]
    release_dates: dict[str, date]
    latest_day: date
    namespace: str
    include_db_aliases: bool
    prometheus_retention_days: int
    now: datetime


@dataclass
class Outcome:
    status: str
    values: list[Value] = field(default_factory=list)
    error: str = ""


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def _database(instance, window: DayWindow, ctx: RunContext) -> tuple[Outcome, Optional[instance_db.InstanceDbResult]]:
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


def _platform(instance, window: DayWindow, ctx: RunContext) -> Outcome:
    if ctx.prometheus is None:
        return Outcome(catalog.ERROR, error="PROMETHEUS_HOST is not set")
    if (ctx.latest_day - window.day).days > ctx.prometheus_retention_days:
        return Outcome(catalog.SKIPPED, error="outside Prometheus retention")
    try:
        return Outcome(catalog.OK, prometheus.platform_metrics(ctx.prometheus, instance, window, ctx.namespace))
    except Exception as exc:
        logger.warning("fleet metrics: Prometheus for %s on %s: %s", instance.resource_identifier, window.day, exc)
        return Outcome(catalog.ERROR, error=_describe(exc))


def _version(instance, window: DayWindow, ctx: RunContext, db_version: Optional[str],
             deployed: bool) -> tuple[str, str]:
    if db_version:
        return db_version, catalog.VERSION_RUNTIME_TABLE
    if window.day == ctx.latest_day and ctx.prometheus is not None:
        try:
            if prometheus.backend_available_now(ctx.prometheus, instance, ctx.namespace, ctx.now):
                found = version.version_in_cluster(instance)
                if found:
                    return found, catalog.VERSION_HEALTH
        except Exception as exc:
            logger.warning("fleet metrics: version probe for %s: %s", instance.resource_identifier, exc)
    previous = (InstanceDay.objects.filter(instance_pk=instance.pk, day=window.day - timedelta(days=1))
                .values_list("lex_app_version", flat=True).first())
    if previous and not deployed:
        return previous, catalog.VERSION_CARRIED
    return "", catalog.VERSION_UNKNOWN


def _apply(row: InstanceDay, status_field: str, source: str, outcome: Outcome,
           errors: dict, replace: dict) -> None:
    """Take the source's outcome, unless it failed after an earlier success."""
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
    try:
        deploy_list = controller.deploys(instance, window)
        controller_outcome = Outcome(catalog.OK)
    except Exception as exc:
        logger.warning("fleet metrics: deploys of %s on %s: %s", instance.resource_identifier, window.day, exc)
        deploy_list, controller_outcome = [], Outcome(catalog.ERROR, error=_describe(exc))

    db_outcome, db_result = _database(instance, window, ctx)
    platform_outcome = _platform(instance, window, ctx)
    version_value, version_source = _version(
        instance, window, ctx, db_result.version if db_result else None, deployed=bool(deploy_list),
    )
    age = version.version_age_days(version_value, window.day, ctx.release_dates) if version_value else None

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

        _apply(row, "db_status", catalog.SOURCE_DB, db_outcome, errors, replace)
        if catalog.SOURCE_DB in replace:
            row.recorder = db_result.recorder_state if db_result else catalog.RECORDER_ABSENT
            row.recorder_silent = bool(db_result and db_result.recorder_silent)
        _apply(row, "prometheus_status", catalog.SOURCE_PROMETHEUS, platform_outcome, errors, replace)
        earlier_controller = row.controller_status if row.pk else ""
        if controller_outcome.status == catalog.OK or earlier_controller != catalog.OK:
            row.controller_status = controller_outcome.status
            if controller_outcome.error:
                errors["controller"] = controller_outcome.error
            else:
                errors.pop("controller", None)
        else:
            errors["controller"] = f"{controller_outcome.error} (kept the earlier status)"

        row.lex_app_version, row.version_source = version_value, version_source
        replace[catalog.SOURCE_VERSION] = [("version_age_days", "", float(age))] if age is not None else []
        row.errors = errors
        row.save()

        MetricValue.objects.filter(instance_day=row, source__in=list(replace)).delete()
        MetricValue.objects.bulk_create([
            MetricValue(instance_day=row, metric=metric, dimension=dimension, value=value, source=source)
            for source, values in replace.items()
            for metric, dimension, value in values
        ])
        if controller_outcome.status == catalog.OK:
            for deploy in deploy_list:
                DeployEvent.objects.update_or_create(
                    operation_id=deploy.operation_id,
                    defaults=dict(instance_pk=instance.pk, day=window.day, operation_type=deploy.operation_type,
                                  outcome=deploy.outcome, duration_seconds=deploy.duration_seconds),
                )
    return row
```

- [ ] **Step 4: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_collector.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/collector.py fleet_metrics/tests/test_collector.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): store an instance day without ever erasing what a re-run cannot see"
```

---

### Task 12: The run, the catch-up and the heartbeat

**Files:**
- Create: `fleet_metrics/heartbeat.py`, `fleet_metrics/run.py`
- Test: `fleet_metrics/tests/test_heartbeat.py`, `fleet_metrics/tests/test_run.py`

**Interfaces:**
- Produces (`heartbeat`): `THRESHOLD = 0.95`; `verdict(day: date, expected_pks: list[int]) -> tuple[bool, str]`; `send(url: str, ok: bool, message: str, *, session=requests, timeout=10.0) -> str` (returns a `CollectionRun.HEARTBEAT_*` value).
- Produces (`run`): `expected_instances()`; `created_day(instance) -> date`; `days_to_collect(instance, latest: date, catchup_days: int) -> list[date]`; `run_collection(*, trigger: str, days: Optional[list[date]] = None, instance_pks: Optional[list[int]] = None, ping: bool = True, now: Optional[datetime] = None) -> CollectionRun`.

- [ ] **Step 1: Write the failing heartbeat tests**

`fleet_metrics/tests/test_heartbeat.py`:

```python
"""The heartbeat means rows were written, not that the job ran (spec §7)."""

from datetime import date
from types import SimpleNamespace

import pytest
import requests
from django.test import SimpleTestCase, TestCase

from fleet_metrics import catalog, heartbeat
from fleet_metrics.models import CollectionRun, InstanceDay
from fleet_metrics.tests.factories import make_instance

DAY = date(2026, 10, 25)


def row(instance, **statuses):
    fields = dict(db_status=catalog.OK, prometheus_status=catalog.OK, controller_status=catalog.OK)
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

    def test_every_row_written_and_every_source_fine(self):
        for instance in self.instances:
            row(instance)
        ok, message = heartbeat.verdict(DAY, [i.pk for i in self.instances])
        self.assertTrue(ok, message)

    def test_a_missing_row_fails(self):
        for instance in self.instances[1:]:
            row(instance)
        ok, message = heartbeat.verdict(DAY, [i.pk for i in self.instances])
        self.assertFalse(ok)
        self.assertIn("1 expected instance(s) have no row", message)

    def test_one_failing_source_in_twenty_is_tolerated_two_are_not(self):
        row(self.instances[0], prometheus_status=catalog.ERROR)
        for instance in self.instances[1:]:
            row(instance)
        self.assertTrue(heartbeat.verdict(DAY, [i.pk for i in self.instances])[0], "19/20 is 95%")

        InstanceDay.objects.filter(instance_pk=self.instances[1].pk).update(prometheus_status=catalog.ERROR)
        ok, message = heartbeat.verdict(DAY, [i.pk for i in self.instances])
        self.assertFalse(ok)
        self.assertIn("prometheus_status: 18/20", message)

    def test_excluded_and_skipped_are_fine(self):
        row(self.instances[0], db_status=catalog.EXCLUDED)
        row(self.instances[1], prometheus_status=catalog.SKIPPED)
        for instance in self.instances[2:]:
            row(instance)
        self.assertTrue(heartbeat.verdict(DAY, [i.pk for i in self.instances])[0])

    def test_a_silent_recorder_fails(self):
        for instance in self.instances:
            row(instance)
        InstanceDay.objects.filter(instance_pk=self.instances[0].pk).update(recorder_silent=True)
        ok, message = heartbeat.verdict(DAY, [i.pk for i in self.instances])
        self.assertFalse(ok)
        self.assertIn("recorder silent", message)


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
"""Which days a run collects, and what a run records."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.test import TestCase

from fleet_metrics import catalog
from fleet_metrics.models import CollectionRun, InstanceDay
from fleet_metrics.run import days_to_collect, expected_instances, run_collection
from fleet_metrics.tests.factories import make_instance
from instance_api.models import LexInstance
from lex_instance_controller_backend import settings as ic_settings

pytestmark = pytest.mark.integration
LATEST = date(2026, 10, 25)
NOW = datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)


def done_row(instance, day, **statuses):
    fields = dict(db_status=catalog.OK, prometheus_status=catalog.OK, controller_status=catalog.OK)
    fields.update(statuses)
    InstanceDay.objects.create(
        instance=instance, instance_pk=instance.pk, day=day, instance_name="x", resource_identifier="x",
        instance_type="prod", release_stage="production", instance_status="RUNNING", **fields,
    )


class DaysToCollectTests(TestCase):
    def setUp(self):
        self.instance = make_instance()
        LexInstance.objects.filter(pk=self.instance.pk).update(creation_timestamp=datetime(2026, 10, 20, 12))
        self.instance.refresh_from_db()

    def test_from_creation_to_yesterday_then_only_what_is_missing(self):
        self.assertEqual(days_to_collect(self.instance, LATEST, 35),
                         [date(2026, 10, d) for d in range(20, 26)])
        for d in range(20, 25):
            done_row(self.instance, date(2026, 10, d))
        InstanceDay.objects.filter(day=date(2026, 10, 22)).update(prometheus_status=catalog.ERROR)
        self.assertEqual(days_to_collect(self.instance, LATEST, 35), [date(2026, 10, 22), LATEST])

    def test_yesterday_is_always_collected_again(self):
        for d in range(20, 26):
            done_row(self.instance, date(2026, 10, d))
        self.assertEqual(days_to_collect(self.instance, LATEST, 35), [LATEST])

    def test_the_catch_up_window_bounds_the_past(self):
        LexInstance.objects.filter(pk=self.instance.pk).update(creation_timestamp=datetime(2026, 1, 1))
        self.instance.refresh_from_db()
        days = days_to_collect(self.instance, LATEST, 3)
        self.assertEqual(days, [LATEST - timedelta(days=n) for n in (3, 2, 1, 0)])


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
                patch.object(ic_settings, "fleet_metrics_heartbeat_url", "https://hb/abc"), \
                patch.object(ic_settings, "prometheus_host", "http://prom/api/v1/query"):
            run = run_collection(trigger=CollectionRun.TRIGGER_CRON, now=NOW)

        grants.assert_called_once()
        self.assertEqual([c.args[1].day for c in collected.call_args_list], [date(2026, 10, 24), LATEST])
        self.assertEqual((run.status, run.instances_expected, run.instance_days_written, run.heartbeat),
                         (CollectionRun.STATUS_OK, 1, 2, CollectionRun.HEARTBEAT_SENT))
        self.assertEqual(send.call_args.args[:2], ("https://hb/abc", True))

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
plus 3 hours of grace) or a call to the failure endpoint opens an incident.
"""

from __future__ import annotations

import logging
from datetime import date

import requests

from fleet_metrics import catalog
from fleet_metrics.models import CollectionRun, InstanceDay

logger = logging.getLogger(__name__)

#: A source may fail for at most 5% of expected instances before the run counts as failed.
THRESHOLD = 0.95
_STATUS_FIELDS = ("db_status", "prometheus_status", "controller_status")


def verdict(day: date, expected_pks: list[int]) -> tuple[bool, str]:
    """(fine, why) for the rows of ``day`` against the instances that should have one."""
    rows = list(InstanceDay.objects.filter(day=day, instance_pk__in=expected_pks))
    total = len(expected_pks)
    problems = []
    missing = set(expected_pks) - {r.instance_pk for r in rows}
    if missing:
        problems.append(f"{len(missing)} expected instance(s) have no row for {day}")
    for status_field in _STATUS_FIELDS:
        good = sum(1 for r in rows if getattr(r, status_field) in catalog.DONE)
        if total and good < total * THRESHOLD:
            problems.append(f"{status_field}: {good}/{total} ok")
    silent = sum(1 for r in rows if r.recorder_silent)
    if silent:
        problems.append(f"recorder silent on {silent} instance(s)")
    return not problems, "; ".join(problems) or "ok"


def send(url: str, ok: bool, message: str, *, session=requests, timeout: float = 10.0) -> str:
    """Ping the heartbeat, or its failure endpoint. Returns a ``CollectionRun.HEARTBEAT_*`` value."""
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
"""A collection run: which instances, which days, and the heartbeat (spec §5.4.2, §7)."""

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
from fleet_metrics.sources import prometheus, version
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


def days_to_collect(instance, latest: date, catchup_days: int) -> list[date]:
    """Yesterday, plus every earlier day in the catch-up window that is missing or failed."""
    first = max(latest - timedelta(days=catchup_days), created_day(instance))
    if first > latest:
        return []
    complete = set(
        InstanceDay.objects.filter(
            instance_pk=instance.pk, day__gte=first, day__lt=latest,
            db_status__in=catalog.DONE, prometheus_status__in=catalog.DONE,
            controller_status__in=catalog.DONE,
        ).values_list("day", flat=True)
    )
    return [day for day in day_range(first, latest) if day == latest or day not in complete]


def _release_dates() -> dict[str, date]:
    try:
        return version.pypi_release_dates()
    except Exception as exc:
        logger.warning("fleet metrics: PyPI release dates unavailable; version ages are skipped: %s", exc)
        return {}


def run_collection(*, trigger: str, days: Optional[list[date]] = None,
                   instance_pks: Optional[list[int]] = None, ping: bool = True,
                   now: Optional[datetime] = None) -> CollectionRun:
    """Collect every expected instance for ``days`` (default: yesterday plus the catch-up)."""
    now = now or utc_now()
    latest = yesterday(now)
    run = CollectionRun.objects.create(trigger=trigger)
    try:
        grants.ensure_reader_grants()
    except Exception as exc:
        logger.warning("fleet metrics: reader grants not applied: %s", exc)

    ctx = RunContext(
        prometheus=prometheus.Prometheus(settings.prometheus_host) if settings.prometheus_host else None,
        release_dates=_release_dates(),
        latest_day=latest,
        namespace=getattr(settings, "k8s_namespace", "") or "",
        include_db_aliases=str(settings.fleet_metrics_include_db_aliases).lower() == "true",
        prometheus_retention_days=settings.fleet_metrics_prometheus_retention_days,
        now=now,
    )
    instances = list(expected_instances())
    if instance_pks:
        instances = [i for i in instances if i.pk in set(instance_pks)]

    written = failed = 0
    collected_days: list[date] = []
    for instance in instances:
        plan = days if days is not None else days_to_collect(instance, latest, settings.fleet_metrics_catchup_days)
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
            statuses = (getattr(row, "db_status", catalog.OK), getattr(row, "prometheus_status", catalog.OK),
                        getattr(row, "controller_status", catalog.OK))
            if any(status not in catalog.DONE for status in statuses):
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
        ok, message = heartbeat.verdict(latest, expected)
        run.summary = {"heartbeat_verdict": message}
        run.heartbeat = heartbeat.send(settings.fleet_metrics_heartbeat_url, ok, message)
    run.finished_at = django_timezone.now()
    run.save()
    return run
```

The heartbeat is decided only for the scheduled form (`days is None`): a backfill says nothing about yesterday.

- [ ] **Step 6: Run them and watch them pass**

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test fleet_metrics/tests/test_heartbeat.py fleet_metrics/tests/test_run.py -q`
Expected: `14 passed`.

- [ ] **Step 7: Commit**

```bash
git add fleet_metrics/heartbeat.py fleet_metrics/run.py fleet_metrics/tests/test_heartbeat.py fleet_metrics/tests/test_run.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): catch up missed days and ping only when the rows are there"
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
heartbeat only when yesterday's rows were written.

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
Expected: every fleet_metrics test passes (about 70).

Run: `venv/bin/pytest --ds=lex_instance_controller_backend.settings_test instance_api/tests -q`
Expected: the same 22 failures as `origin/main`, no new ones.

- [ ] **Step 5: Commit**

```bash
git add fleet_metrics/management fleet_metrics/tests/test_command.py
git -c user.name="hazempgm" -c user.email="30691209+hazempgm@users.noreply.github.com" commit -m "feat(fleet-metrics): the command the daily CronJob runs, and backfills"
```

---

### Task 14: The pull request

- [ ] **Step 1: Push and open the PR against `main`**

```bash
git push -u origin feat/fleet-metrics-collector
gh pr create --base main --title "feat(fleet-metrics): a daily per-instance snapshot of usage and platform health" --body "$(cat <<'EOF'
Fleet metrics phase 1, the collector (LEX-749 / LEX-751). Design: lex-app `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md`.

- New app `fleet_metrics`: one `InstanceDay` per instance per UTC day, with a status per source and long-format `MetricValue`s; `DeployEvent` per finished deploy; a `fleet_metrics_daily` view for Grafana.
- Sources: each instance database (aggregates only, read-only transaction, 60 s timeout, primary-key ranges instead of date scans), the in-cluster Prometheus, the controller's own operations, and the version through the in-cluster health service (never the public URL).
- `manage.py collect_fleet_metrics` catches up missed days (35) and pings the Better Stack heartbeat only when yesterday's rows were written.
- The Grafana reader role gets SELECT on these relations only; the grant is re-applied every run.

Deployment (CronJob, heartbeat, reader role, dashboards) is in instance-controller-deployment.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
