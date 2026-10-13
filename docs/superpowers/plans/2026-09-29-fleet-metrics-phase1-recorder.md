# Fleet metrics phase 1 — the usage recorder (lex-app) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every successful API request to a lex-app instance, and every product-area event the web interface sends, records a presence row in the instance's own database, once per person (or API key), product area, channel and UTC day, plus the running lex-app version. From those rows the Instance Controller counts active people (K1), feature reach (K2) and the version history (K7).

**Architecture:** Two raw-SQL tables installed by a `lex.core` migration. They are not Django models, for the same reason as `0001_bitemporal_activation`. One pure module names the product areas: `API_AREAS` maps URL names to areas, and `UI_AREAS` is the closed list the interface may send. A process-local recorder deduplicates writes, pauses itself on a database error and never raises. Two writers feed it:

- a middleware, last in `MIDDLEWARE`, which records API activity on the `api` channel after the view has returned;
- a session-authenticated endpoint, `POST api/usage/area`, which records interface events on the `ui` or `ui_embed` channel.

Separately, a contract test pins the audit columns and calculation-run behaviour that the Instance Controller's SQL reads.

**Tech Stack:** Django 6.0, Django REST framework 3.18, raw SQL through `django.db.connection`, PostgreSQL, pytest via `python -m lex pytest`.

**Spec:** `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md`, revision 2: §4.2 K1/K2/K7, §5.3.1, §5.3.2, §5.7, §6, §7, §8.1 (R3), §8.2.

**Release:** everything in this plan is **R3** (spec §4.1, §8.1 stages R3.1–R3.3). It ships in a lex-app release whose `requirements.txt` pins the lex-app-frontend release carrying the interface area events (`2026-10-08-fleet-metrics-phase1-frontend.md`). Neither half counts K2 alone. The endpoint without the interface receives no events. The interface without the endpoint sends events that no route records (§5.3.2, "Gaps and coverage"). Task 9 makes the pin and the release.

**Estimate:** 7.5 hours, spec §8.2's lex-app share of R3: the recorder 6, the endpoint 1.5. Each task states its size. The §5.3.2 end-to-end verification (1.5 h) and the collector's reads are budgeted separately in §8.2 and are not part of this plan.

## Global Constraints

- Base branch `lex-app-v2`; work branch `feat/fleet-metrics-recorder`. The worktree directory must start with `lex` (e.g. `.claude/worktrees/lex_fleet_recorder`), because `settings.py` otherwise appends the checkout's basename to `INSTALLED_APPS`.
- Tables are raw SQL, never Django models: `lex_usage_daily`, `lex_runtime_versions`. `lex/core/migrations` held only `0001_bitemporal_activation` at `d48449bf`. If `lex-app-v2` has gained a `0002` since, number this migration after the latest one and depend on it.
- A day is a UTC calendar day, computed as `datetime.now(timezone.utc).date()`, never `django.utils.timezone.localdate()`.
- The recorder never changes a response and never raises.
- **Channels.** `api` is written only by the middleware. `ui` and `ui_embed` are written only by the endpoint. The table refuses an API key on any channel but `api`.
- **Areas.** Both vocabularies live in `lex/core/services/usage_areas.py`, so the middleware and the endpoint cannot disagree:
  - `API_AREAS` is the URL-name map (spec §5.3.1).
  - `UI_AREAS` is the interface's list (spec §5.3.2). It contains every API area except `app`.
  - The interface mirrors `UI_AREAS` in `src/usage/areas.ts` of process-admin-general-client. The two lists change in the same release pair.
- **K4 is rejected** (spec §4.3). API keys are still recorded, on the `api` channel only. Nothing here derives or names an integration metric.
- **The endpoint is session-only, with DRF's CSRF check in force.** Never `csrf_exempt`, never `AllowAny`. The person comes from `request.user`, never from the body.
- The instance's own `LEX_API_KEY` is never recorded. Anonymous requests are never recorded.
- The switch is the environment variable `LEX_USAGE_METRICS`. It is on by default. `false`, `0`, `no` and `off` turn usage rows off on both writers. The version row is still written, with `usage_enabled = false`.
- Tests live in the cluster tree and follow `.claude/skills/lex-testing/SKILL.md`. Allocation as of `d48449bf`:
  - **10o**, scenarios **10.83–10.114** (cluster 10 `max_scenario` is 82; its last letter is `n`);
  - **6r**, scenarios **6.119–6.122** (cluster 6 `max_scenario` is 118; its last letter is `q`).

  Re-read `allocation.yaml` of clusters 10 and 6 before starting. If either letter or range is taken, use the next free letter and `max_scenario + 1`, and renumber the test names below to match.
- Test command (from the worktree root, local Postgres `django`/`lundadminlocal` on `localhost:5432`):
  `DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project <venv>/bin/python -m lex pytest <paths> -v`
- Never put a private customer project's name or path in a committed file. lex-app is public: no personal names, e-mail addresses or secrets either.
- Each writer adds at most two statements to a request: a savepoint-wrapped insert. No existing test counts queries around a full-stack request (checked at `d48449bf`: `test_10m` calls the view directly, `test_15i` calls the function, `test_12i` counts only its own table), so none should change. If one does, it counts through the client, and its budget needs the recorder's statements, not a skip.
- Commit subjects: conventional, lower-case, a sentence (`feat(usage): …`). Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| Path | Responsibility |
|---|---|
| `lex/core/sql/usage_metrics.sql` (create) | The two tables |
| `lex/core/migrations/0002_usage_metrics.py` (create) | Installs the SQL on PostgreSQL; no-op elsewhere |
| `lex/core/services/usage_areas.py` (create) | `API_AREAS`, `UI_AREAS`, `area_for`; pure |
| `lex/core/services/usage_metrics.py` (create) | Raw-SQL storage, channels, `utc_day`, the switch, the process-local `UsageRecorder` |
| `lex/core/middleware/usage_recorder.py` (create) | Actor resolution and the middleware (`api` channel) |
| `lex/api/views/usage/__init__.py`, `lex/api/views/usage/UsageArea.py` (create) | `POST api/usage/area` (`ui`, `ui_embed` channels) |
| `lex/process_admin/sites/process_admin_site.py` (modify: one import, one `path` in `url_patterns_for_react_admin`, lines 198-267) | The route, named `usage-area` |
| `lex/lex_app/settings.py:340-360` (modify) | Append the middleware to `MIDDLEWARE` |
| `lex/test_project/tests/api_layer/test_10o_usage_recorder.py` (create) | Cluster 10o |
| `lex/test_project/tests/audit_logging/test_6r_fleet_metrics_contract.py` (create) | Cluster 6r |
| `lex/test_project/test-plan/clusters/10-api_layer/{allocation.yaml,batches.md,cluster.md}` (modify) | Plan shards |
| `lex/test_project/test-plan/clusters/06-audit_logging/{allocation.yaml,batches.md,cluster.md}` (modify) | Plan shards |
| `lex/test_project/test-plan/progress/sessions/2026-MM-DD-fleet-metrics-recorder.md` (create) | Session fragment |
| `lex/test_project/test-plan/progress/dashboard.md` (regenerate) | Generated; never hand-edited |
| `requirements.txt:65` (modify, release pull request) | Pin the lex-app-frontend release that carries the interface events |
| lex-app-docs `content/ship-and-operate/monitoring and health.md` (modify, separate repo) | The public paragraph |

---

### Task 1: The tables and their migration

**Size:** 0.75 h

**Files:**
- Create: `lex/core/sql/usage_metrics.sql`
- Create: `lex/core/migrations/0002_usage_metrics.py`
- Test: `lex/test_project/tests/api_layer/test_10o_usage_recorder.py` (new file)

**Interfaces:**
- Produces:
  - `lex_usage_daily(day date, actor_kind text, actor_id text, area text, channel text, first_seen timestamptz)`, primary key `(day, actor_kind, actor_id, area, channel)`;
  - `lex_runtime_versions(version text primary key, first_seen timestamptz, last_seen timestamptz, usage_enabled boolean)`.

- [ ] **Step 1: Create the test file with the first test**

```python
"""The usage recorder: which product area each person used, per channel and UTC day.

Intent: fleet metrics (LEX-749) counts active people (K1) and feature reach (K2) per
instance. Everything it knows about reads comes from the rows written here, into the
instance's own database. There are two writers:

* the middleware writes API activity on the ``api`` channel;
* ``POST api/usage/area`` writes the web interface's area events on ``ui`` or ``ui_embed``.

K2 is read from the interface channels. The ``api`` channel is supporting detail.

A recorder that is registered but never reached looks exactly like an instance nobody
uses. So these tests drive real requests through the whole middleware stack and assert
rows, and they pin the UTC day the Instance Controller buckets by.
Cluster 10o — scenarios 10.83–10.114. Type: U, I and E.
Covers: lex/core/sql/usage_metrics.sql, lex/core/migrations/0002_usage_metrics.py,
lex/core/services/usage_areas.py, lex/core/services/usage_metrics.py,
lex/core/middleware/usage_recorder.py, lex/api/views/usage/UsageArea.py and the
``usage-area`` route in lex/process_admin/sites/process_admin_site.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_usage_recorder.py -v
"""

from __future__ import annotations

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

import pytest

pytestmark = pytest.mark.api_layer


def _columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            [table],
        )
        return {row[0] for row in cursor.fetchall()}


def _primary_key(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = tc.constraint_name
             AND kcu.table_name = tc.table_name
            WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_name = %s
            """,
            [table],
        )
        return {row[0] for row in cursor.fetchall()}


def _raw_insert(actor_kind: str, channel: str) -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO lex_usage_daily (day, actor_kind, actor_id, area, channel) "
            "VALUES ('2026-10-01', %s, '7', 'grid', %s)",
            [actor_kind, channel],
        )


class TestCluster10o_UsageTables(TestCase):
    """Cluster 10o: the recorder's tables exist, in the shape the collector reads."""

    # -- 10.83 ---------------------------------------------------------
    def test_10_83_migration_installs_both_tables_with_their_keys(self) -> None:
        """
        Scenario 10.83: the core migration installs the usage tables.
        Given: a database migrated to the latest ``core`` migration.
        When: the catalog is read, and rows outside the channel rules are inserted.
        Then: ``lex_usage_daily`` carries day, actor_kind, actor_id, area, channel and
              first_seen, keyed by the first five; ``lex_runtime_versions`` carries
              version, first_seen, last_seen and usage_enabled, keyed by version; an
              unknown channel and an API key on an interface channel are refused.
        """
        self.assertEqual(
            _columns("lex_usage_daily"),
            {"day", "actor_kind", "actor_id", "area", "channel", "first_seen"},
            "lex_usage_daily drifted from the shape the Instance Controller queries.",
        )
        self.assertEqual(
            _primary_key("lex_usage_daily"),
            {"day", "actor_kind", "actor_id", "area", "channel"},
            "The presence key must be (day, actor, area, channel): one row per person, area, "
            "channel and day, so interface use and API activity are never merged.",
        )
        self.assertEqual(
            _columns("lex_runtime_versions"),
            {"version", "first_seen", "last_seen", "usage_enabled"},
            "lex_runtime_versions drifted from the shape the Instance Controller queries.",
        )
        self.assertEqual(
            _primary_key("lex_runtime_versions"),
            {"version"},
            "One row per lex-app version: first_seen per version is the upgrade date.",
        )
        with self.assertRaises(IntegrityError, msg="channel must be api, ui or ui_embed"):
            _raw_insert("person", "web")
        with self.assertRaises(IntegrityError, msg="an API key appears on the api channel only"):
            _raw_insert("api_key", "ui")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -v`
Expected: FAIL — `AssertionError: set() != {'day', 'actor_kind', …}` (the tables do not exist).

- [ ] **Step 3: Write the SQL**

`lex/core/sql/usage_metrics.sql`:

```sql
-- Fleet metrics, phase 1: what the usage recorder writes, in the instance's own database.
--
-- Deliberately not Django models: the framework registers every concrete model under lex/
-- for history tracking and for the model list, and an operational table belongs in
-- neither (the same reason as bitemporal_activation.sql).
--
-- A row holds a UTC day, an internal user id or an API key's name, a product area and a
-- channel: 'api' for API activity (the middleware), 'ui' and 'ui_embed' for the web
-- interface's area events (POST api/usage/area). The Instance Controller reads counts
-- from these tables, never rows.
--
-- Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3)

CREATE TABLE IF NOT EXISTS lex_usage_daily (
    day         date        NOT NULL,
    actor_kind  text        NOT NULL CHECK (actor_kind IN ('person', 'api_key')),
    actor_id    text        NOT NULL,
    area        text        NOT NULL,
    channel     text        NOT NULL CHECK (channel IN ('api', 'ui', 'ui_embed')),
    first_seen  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (day, actor_kind, actor_id, area, channel),
    -- Only a person uses the interface; an API key appears on the api channel only.
    CHECK (channel = 'api' OR actor_kind = 'person')
);

CREATE TABLE IF NOT EXISTS lex_runtime_versions (
    version        text        PRIMARY KEY,
    first_seen     timestamptz NOT NULL DEFAULT now(),
    last_seen      timestamptz NOT NULL DEFAULT now(),
    usage_enabled  boolean     NOT NULL
);
```

- [ ] **Step 4: Write the migration**

`lex/core/migrations/0002_usage_metrics.py`:

```python
"""
Fleet metrics, phase 1: the usage recorder's two tables.

Installs ``lex/core/sql/usage_metrics.sql``:

* ``lex_usage_daily``: one row per person or API key, product area, channel and UTC day;
* ``lex_runtime_versions``: one row per lex-app version this instance has served, with
  when it was first and last seen.

Nothing here is a Django model on purpose, for the reason 0001 gives: the framework
registers every concrete model it discovers under ``lex/`` for history tracking and for the
model list. On a non-PostgreSQL backend this migration is a no-op and the recorder stays
inert (``lex.core.services.usage_metrics.supported``).

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3)
"""

from pathlib import Path

from django.db import migrations

SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "usage_metrics.sql"
TABLES = ("lex_usage_daily", "lex_runtime_versions")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SQL_PATH.read_text(encoding="utf-8"))


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")


class Migration(migrations.Migration):

    dependencies = [("core", "0001_bitemporal_activation")]

    operations = [
        migrations.RunPython(install, uninstall),
    ]
```

- [ ] **Step 5: Run the test and watch it pass**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -v`
Expected: `1 passed`.

- [ ] **Step 6: Confirm the SQL ships in the wheel**

`lex/core/sql/*.sql` is already in `[tool.setuptools.package-data]` (`pyproject.toml:55`, `"lex.core" = ["sql/*.sql"]`). `lex/core/sql` is already in `RUNTIME_DIRS` of `.github/scripts/check_wheel_runtime_files.py` (line 34). Verify against a built wheel:

Run: `python -m build --wheel --outdir /tmp/lex_wheel_check . && python .github/scripts/check_wheel_runtime_files.py --dist /tmp/lex_wheel_check`
Expected: the checker exits 0, and `unzip -l /tmp/lex_wheel_check/*.whl | grep usage_metrics.sql` lists `lex/core/sql/usage_metrics.sql`.

- [ ] **Step 7: Commit**

```bash
git add lex/core/sql/usage_metrics.sql lex/core/migrations/0002_usage_metrics.py lex/test_project/tests/api_layer/test_10o_usage_recorder.py
git commit -m "feat(usage): the tables fleet metrics reads, outside the model registry"
```

---

### Task 2: The two area vocabularies

**Size:** 0.5 h

**Files:**
- Create: `lex/core/services/usage_areas.py`
- Test: `lex/test_project/tests/api_layer/test_10o_usage_recorder.py` (append a class)

**Interfaces:**
- Produces:
  - constants `APP = "app"`, `GRID = "grid"`, `HISTORY = "history"`, `USAGE_AREA_URL_NAME = "usage-area"`;
  - `API_AREAS: Mapping[str, str]`, the URL name → area map;
  - `UI_AREAS: frozenset[str]`, the interface's closed list;
  - `area_for(url_name: Optional[str], query_params: Mapping[str, str]) -> Optional[str]`, where `None` means "do not record".

**Why `search` stays in `API_AREAS`.** Spec §5.3.1's table has no `search` row. But `global-search` is a live route in `lex-app-v2`: `api/global-search/<str:query>`, at `lex/process_admin/sites/process_admin_site.py:234-238`. So it stays mapped, and Task 4's 10.103 guards that it still resolves. The interface at `e24fed4b` calls the query-string form, `api/global-search/?q=…` (process-admin-general-client `src/api/global-search.ts:51-58`). That path does not match `<str:query>` and falls through to the SPA catch-all, so interface searches never reach this name. Interface search is counted by the `search` UI event (spec §5.3.2), not by this map.

- [ ] **Step 1: Append the failing tests**

Add to the imports at the top of the test file:

```python
from django.test import SimpleTestCase
```

Append:

```python
class TestCluster10o_AreaMap(SimpleTestCase):
    """Cluster 10o: a request's URL name decides its API area; the interface has its list."""

    EXPECTED = {
        "model-entries-list": "grid",
        "model-history-list": "history",
        "model-one-entry-read-update-delete": "record",
        "model-one-entry-create": "record",
        "model-many-entries": "record",
        "model-export": "export",
        "calculationlog-tree": "calculation_logs",
        "init-calculation-logs": "calculation_logs",
        "download-markdown-pdf": "calculation_logs",
        "process": "processes",
        "run_step": "processes",
        "htmlreports": "reports",
        "streamlit_token": "analytics",
        # Not in spec §5.3.1's table, but routed in lex-app-v2
        # (process_admin_site.py:234-238); kept on purpose, see the plan's Task 2.
        "global-search": "search",
        "file-download": "files",
        "sharepoint-file-download": "files",
        "sharepoint-file-share-link": "files",
        "sharepoint-file-preview-link": "files",
    }

    # Spec §5.3.2, "What counts as use": the interface's areas, name for name.
    SPEC_UI_AREAS = {
        "grid", "record", "create", "history", "audit_log", "calculate", "calculation_logs",
        "processes", "reports", "analytics", "dashboard", "search", "export", "files",
        "monitoring",
    }

    # -- 10.84 ---------------------------------------------------------
    def test_10_84_every_mapped_route_lands_in_its_area(self) -> None:
        """
        Scenario 10.84: the API area map matches the spec's table.
        Given: each URL name the spec assigns to an area.
        When: it is classified without query parameters.
        Then: it lands in that area, and the map holds exactly those names.
        """
        from lex.core.services.usage_areas import API_AREAS, area_for

        self.assertEqual(
            dict(API_AREAS), self.EXPECTED,
            "The API area map drifted from spec §5.3.1; the api channel would count the wrong areas.",
        )
        for url_name, area in self.EXPECTED.items():
            with self.subTest(url_name=url_name):
                self.assertEqual(area_for(url_name, {}), area, f"{url_name} must count as {area}")

    # -- 10.85 ---------------------------------------------------------
    def test_10_85_as_of_turns_a_list_into_history(self) -> None:
        """
        Scenario 10.85: the grid in as-of mode is history.
        Given: a list request carrying ``as_of``.
        When: it is classified.
        Then: it counts as ``history``; the same request without ``as_of`` is ``grid``,
              and ``as_of`` on any other route changes nothing.
        """
        from lex.core.services.usage_areas import area_for

        self.assertEqual(area_for("model-entries-list", {"as_of": "2026-09-01T00:00:00Z"}), "history")
        self.assertEqual(area_for("model-entries-list", {"as_of": ""}), "grid")
        self.assertEqual(area_for("model-export", {"as_of": "2026-09-01T00:00:00Z"}), "export")

    # -- 10.86 ---------------------------------------------------------
    def test_10_86_unmapped_routes_are_the_app_and_unnamed_ones_are_not_recorded(self) -> None:
        """
        Scenario 10.86: a named route nobody mapped still says "this person was here".
        Given: an unmapped URL name, a route with no name, and the usage endpoint's name.
        When: they are classified.
        Then: the unmapped name is ``app``. The unnamed route is ``None``: the SPA's
              catch-all and static assets are not use (spec §5.3.1). The usage endpoint
              is ``None`` too: its event is recorded on its interface channel, and a
              second ``api`` row for the same request would count it twice.
        """
        from lex.core.services.usage_areas import APP, API_AREAS, USAGE_AREA_URL_NAME, area_for

        self.assertEqual(area_for("model-structure", {}), APP)
        self.assertIsNone(area_for(None, {}))
        self.assertIsNone(area_for("", {}))
        self.assertIsNone(area_for(USAGE_AREA_URL_NAME, {}))
        self.assertNotIn(USAGE_AREA_URL_NAME, API_AREAS)

    # -- 10.104 --------------------------------------------------------
    def test_10_104_the_interface_list_is_the_spec_list_and_contains_every_api_area_but_app(self) -> None:
        """
        Scenario 10.104: the endpoint accepts exactly the spec's interface areas.
        Given: ``UI_AREAS`` and ``API_AREAS``.
        When: they are compared with spec §5.3.2 and with each other.
        Then: ``UI_AREAS`` is the spec's list, name for name; every API area except
              ``app`` is in it; ``app`` is not, because it is the catch-all for "some
              request", not a product area a person chooses.
        """
        from lex.core.services.usage_areas import APP, API_AREAS, UI_AREAS

        self.assertEqual(set(UI_AREAS), self.SPEC_UI_AREAS,
                         "The interface mirrors this list; change both in the same release pair.")
        self.assertLessEqual(set(API_AREAS.values()), set(UI_AREAS))
        self.assertNotIn(APP, UI_AREAS)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -k AreaMap -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lex.core.services.usage_areas'`.

- [ ] **Step 3: Write the module**

`lex/core/services/usage_areas.py`:

```python
"""The product areas fleet metrics counts (K2), in the two vocabularies that write them.

Two writers share this module, so they can never disagree (spec §5.3.1, §5.3.2):

* ``API_AREAS`` maps the URL name a request resolved to onto an area. It is the backend's
  view of an area, recorded on the ``api`` channel as supporting detail. The backend
  cannot tell several interface areas apart: the show page, the edit drawer and a hover
  card make the same request, and starting a calculation is the same PATCH as saving an
  edit. So K2 is never read from it.
* ``UI_AREAS`` is the closed list the web interface may send to ``POST api/usage/area``,
  recorded on the ``ui`` and ``ui_embed`` channels. It is K2's vocabulary. It contains
  every API area except ``app``. The interface mirrors it in process-admin-general-client
  (``src/usage/areas.ts``), so the two lists change in the same release pair.

A request is classified by URL name, never by path: names do not move when
``DJANGO_BASE_PATH`` prefixes every route. A named route nobody mapped is ``app``: the
person was here. Two kinds of request are not recorded at all (``None``):

* an unnamed route, such as the SPA's catch-all and static assets, which are not use;
* the usage endpoint itself, whose event is already recorded on its interface channel.

``calculate`` has no API area. The Instance Controller counts calculation runs from the
audit tables (K3), on every instance, whether or not it runs this recorder.

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3)
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Optional

APP = "app"
GRID = "grid"
HISTORY = "history"

#: The route name of ``POST api/usage/area`` (lex/process_admin/sites/process_admin_site.py).
USAGE_AREA_URL_NAME = "usage-area"

API_AREAS: Mapping[str, str] = MappingProxyType({
    "model-entries-list": GRID,
    "model-history-list": HISTORY,
    "model-one-entry-read-update-delete": "record",
    "model-one-entry-create": "record",
    "model-many-entries": "record",
    "model-export": "export",
    "calculationlog-tree": "calculation_logs",
    "init-calculation-logs": "calculation_logs",
    "download-markdown-pdf": "calculation_logs",
    "process": "processes",
    "run_step": "processes",
    "htmlreports": "reports",
    "streamlit_token": "analytics",
    # Routed in lex-app-v2, though absent from spec §5.3.1's table. The interface's own
    # search uses a query-string form this route does not match; see the plan, Task 2.
    "global-search": "search",
    "file-download": "files",
    "sharepoint-file-download": "files",
    "sharepoint-file-share-link": "files",
    "sharepoint-file-preview-link": "files",
})

UI_AREAS: frozenset[str] = frozenset({
    "grid", "record", "create", "history", "audit_log", "calculate", "calculation_logs",
    "processes", "reports", "analytics", "dashboard", "search", "export", "files",
    "monitoring",
})

_NOT_RECORDED = frozenset({USAGE_AREA_URL_NAME})


def area_for(url_name: Optional[str], query_params: Mapping[str, str]) -> Optional[str]:
    """The API area of a request that resolved to ``url_name``, or None to skip it.

    A list request carrying a non-empty ``as_of`` is the grid's history mode, so it counts
    as ``history``. An unmapped name is ``app``. An unnamed route and the usage endpoint
    are not recorded.
    """
    if not url_name or url_name in _NOT_RECORDED:
        return None
    area = API_AREAS.get(url_name, APP)
    if area == GRID and query_params.get("as_of"):
        return HISTORY
    return area
```

- [ ] **Step 4: Run them and watch them pass**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -k AreaMap -v`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add lex/core/services/usage_areas.py lex/test_project/tests/api_layer/test_10o_usage_recorder.py
git commit -m "feat(usage): name the product areas the api and the interface may record"
```

---

### Task 3: Storage, channels, the UTC day and the process-local recorder

**Size:** 1.25 h

**Files:**
- Create: `lex/core/services/usage_metrics.py`
- Test: `lex/test_project/tests/api_layer/test_10o_usage_recorder.py` (append two classes)

**Interfaces:**
- Consumes: the tables from Task 1.
- Produces (module `lex.core.services.usage_metrics`):
  - constants `USAGE_TABLE`, `VERSIONS_TABLE`, `USAGE_METRICS_ENV = "LEX_USAGE_METRICS"`, `PERSON = "person"`, `API_KEY = "api_key"`, `CHANNEL_API = "api"`, `CHANNEL_UI = "ui"`, `CHANNEL_UI_EMBED = "ui_embed"`, `PAUSE_AFTER_ERROR = timedelta(minutes=5)`, `VERSION_INTERVAL = timedelta(hours=1)`, `WARNING_INTERVAL = timedelta(hours=1)`, `MAX_SEEN = 100_000`
  - `utc_day(now: Optional[datetime] = None) -> date`, which raises `ValueError` for a naive datetime
  - `usage_metrics_enabled() -> bool`, `supported() -> bool`
  - `insert_presence(day: date, actor_kind: str, actor_id: str, area: str, channel: str) -> None`
  - `upsert_runtime_version(version: str, usage_enabled: bool) -> None`
  - `presence_rows() -> list[dict]`, `runtime_versions() -> list[dict]`, `clear_all() -> None`
  - `class UsageRecorder(clock: Callable[[], datetime] = _utc_now)`, with `record(actor: Optional[tuple[str, str]], area: Optional[str], channel: str = CHANNEL_API) -> None` and `reset() -> None`
  - `recorder: UsageRecorder`, the process singleton both writers use

- [ ] **Step 1: Append the failing tests**

Add to the imports at the top of the test file:

```python
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import DatabaseError

from lex._version import __version__
from lex.core.services import usage_metrics
```

Append:

```python
class TestCluster10o_Rules(SimpleTestCase):
    """Cluster 10o: the rules the recorder runs by: UTC days, and last place in MIDDLEWARE."""

    # -- 10.87 ---------------------------------------------------------
    def test_10_87_the_day_is_utc_even_when_berlin_has_moved_on(self) -> None:
        """
        Scenario 10.87: a request at 00:30 Berlin on 26 Oct is filed under 25 Oct.
        Given: 2026-10-25T23:30Z, the evening summer time ended, which is 00:30 on
               26 Oct in Berlin; and 00:30Z the same day, inside the hour Berlin repeats.
        When: its day is computed.
        Then: both are 2026-10-25, whether the instant is given in UTC or in Berlin time,
              and a naive datetime is refused rather than guessed.
        """
        self.assertEqual(
            usage_metrics.utc_day(datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc)),
            date(2026, 10, 25),
        )
        self.assertEqual(
            usage_metrics.utc_day(datetime(2026, 10, 26, 0, 30, tzinfo=ZoneInfo("Europe/Berlin"))),
            date(2026, 10, 25),
            "The same instant expressed in Berlin time must land on the same UTC day.",
        )
        self.assertEqual(
            usage_metrics.utc_day(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)),
            date(2026, 10, 25),
        )
        with self.assertRaises(ValueError):
            usage_metrics.utc_day(datetime(2026, 10, 25, 23, 30))

    # -- 10.88 ---------------------------------------------------------
    def test_10_88_the_middleware_is_the_last_one(self) -> None:
        """
        Scenario 10.88: the recorder sees the user every other middleware settled on.
        Given: the project settings.
        When: MIDDLEWARE is read.
        Then: the usage recorder is its last entry.
        """
        from django.conf import settings

        self.assertEqual(
            settings.MIDDLEWARE[-1],
            "lex.core.middleware.usage_recorder.UsageRecorderMiddleware",
            "The recorder must come after every authentication middleware.",
        )


class _Clock:
    """A settable clock for UsageRecorder."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _keys(rows) -> list[tuple]:
    return [(r["actor_kind"], r["actor_id"], r["area"], r["channel"]) for r in rows]


class TestCluster10o_Recorder(TestCase):
    """Cluster 10o: storage and the process-local front, against the real tables."""

    PERSON_7 = (usage_metrics.PERSON, "7")

    # -- 10.89 ---------------------------------------------------------
    def test_10_89_a_presence_row_is_written_once(self) -> None:
        """
        Scenario 10.89: inserting the same presence twice leaves one row.
        Given: an empty usage table.
        When: the same (day, actor, area, channel) is inserted twice.
        Then: one row exists.
        """
        usage_metrics.insert_presence(date(2026, 10, 1), "person", "7", "grid", "api")
        usage_metrics.insert_presence(date(2026, 10, 1), "person", "7", "grid", "api")

        self.assertEqual(
            usage_metrics.presence_rows(),
            [{"day": date(2026, 10, 1), "actor_kind": "person", "actor_id": "7",
              "area": "grid", "channel": "api"}],
        )

    # -- 10.90 ---------------------------------------------------------
    def test_10_90_the_version_row_is_upserted(self) -> None:
        """
        Scenario 10.90: one row per version, carrying the latest switch state.
        Given: a version recorded with usage on.
        When: it is recorded again with usage off.
        Then: one row, usage_enabled false, last_seen not before first_seen.
        """
        usage_metrics.upsert_runtime_version("9.9.9", True)
        usage_metrics.upsert_runtime_version("9.9.9", False)

        rows = usage_metrics.runtime_versions()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["version"], "9.9.9")
        self.assertFalse(rows[0]["usage_enabled"])
        self.assertGreaterEqual(rows[0]["last_seen"], rows[0]["first_seen"])

    # -- 10.91 ---------------------------------------------------------
    def test_10_91_the_recorder_writes_a_person_once_per_area_and_day(self) -> None:
        """
        Scenario 10.91: a busy person costs one insert per area per day.
        Given: a recorder with a fixed clock.
        When: the same person uses the grid twice and a record once, through the API.
        Then: one insert reaches the database for the grid, one for the record.
        """
        recorder = usage_metrics.UsageRecorder(clock=_Clock(datetime(2026, 10, 1, 9, tzinfo=timezone.utc)))
        with patch.object(usage_metrics, "insert_presence", wraps=usage_metrics.insert_presence) as spy:
            recorder.record(self.PERSON_7, "grid")
            recorder.record(self.PERSON_7, "grid")
            recorder.record(self.PERSON_7, "record")

        self.assertEqual(spy.call_count, 2, f"Expected one insert per area, got {spy.call_args_list}")
        self.assertEqual({row["area"] for row in usage_metrics.presence_rows()}, {"grid", "record"})

    # -- 10.92 ---------------------------------------------------------
    def test_10_92_a_new_utc_day_starts_at_utc_midnight(self) -> None:
        """
        Scenario 10.92: the same request either side of UTC midnight is two days.
        Given: a person at 2026-10-25T23:59:59Z and again at 2026-10-26T00:00:00Z.
        When: both are recorded.
        Then: two rows, filed under 25 and 26 October.
        """
        clock = _Clock(datetime(2026, 10, 25, 23, 59, 59, tzinfo=timezone.utc))
        recorder = usage_metrics.UsageRecorder(clock=clock)
        recorder.record(self.PERSON_7, "grid")
        clock.now = datetime(2026, 10, 26, 0, 0, 0, tzinfo=timezone.utc)
        recorder.record(self.PERSON_7, "grid")

        self.assertEqual(
            [row["day"] for row in usage_metrics.presence_rows()],
            [date(2026, 10, 25), date(2026, 10, 26)],
        )

    # -- 10.93 ---------------------------------------------------------
    def test_10_93_a_database_error_pauses_recording_and_never_raises(self) -> None:
        """
        Scenario 10.93: a failing table costs one warning, not every request.
        Given: inserts that raise DatabaseError.
        When: two requests are recorded within the pause, and one after it.
        Then: nothing raises; the second request does not touch the database; the third
              retries; one warning is logged in the hour.
        """
        clock = _Clock(datetime(2026, 10, 1, 9, tzinfo=timezone.utc))
        recorder = usage_metrics.UsageRecorder(clock=clock)
        with patch.object(usage_metrics, "insert_presence", side_effect=DatabaseError("simulated")) as failing, \
                self.assertLogs("lex.core.services.usage_metrics", level="WARNING") as logs:
            recorder.record(self.PERSON_7, "grid")
            clock.now += timedelta(minutes=1)
            recorder.record(self.PERSON_7, "record")
            clock.now += usage_metrics.PAUSE_AFTER_ERROR
            recorder.record(self.PERSON_7, "export")

        self.assertEqual(failing.call_count, 2, "Inside the pause the recorder must not retry")
        self.assertEqual(len(logs.records), 1, "At most one warning per hour")
        self.assertIn("could not be recorded", logs.output[0])

    # -- 10.105 --------------------------------------------------------
    def test_10_105_the_same_area_on_two_channels_is_two_rows(self) -> None:
        """
        Scenario 10.105: API activity and interface use are kept apart.
        Given: a recorder with a fixed clock.
        When: one person reaches the grid through the API, opens it in the interface, and
              opens it embedded.
        Then: three rows, one per channel. K2 reads ui and ui_embed and never adds api to
              them (spec §5.3.2).
        """
        recorder = usage_metrics.UsageRecorder(clock=_Clock(datetime(2026, 10, 1, 9, tzinfo=timezone.utc)))
        recorder.record(self.PERSON_7, "grid", usage_metrics.CHANNEL_API)
        recorder.record(self.PERSON_7, "grid", usage_metrics.CHANNEL_UI)
        recorder.record(self.PERSON_7, "grid", usage_metrics.CHANNEL_UI_EMBED)

        self.assertEqual(
            _keys(usage_metrics.presence_rows()),
            [("person", "7", "grid", "api"), ("person", "7", "grid", "ui"), ("person", "7", "grid", "ui_embed")],
        )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -k "Rules or Recorder" -v`
Expected: FAIL — `ImportError: cannot import name 'usage_metrics'`. (10.88 fails until Task 4; leave it red.)

- [ ] **Step 3: Write the module**

`lex/core/services/usage_metrics.py`:

```python
"""The usage recorder's storage and its process-local front.

Two raw-SQL tables in the instance's own database (``lex/core/sql/usage_metrics.sql``):

* ``lex_usage_daily``: one row per person or API key, product area, channel and UTC day;
* ``lex_runtime_versions``: one row per lex-app version this instance has served.

The channel says who saw the use:

* ``api`` is written by the middleware, for API activity;
* ``ui`` and ``ui_embed`` are written by ``POST api/usage/area``, for the web interface's
  area events, the latter when the interface runs embedded.

The Instance Controller reads counts from both tables once a day (fleet metrics, phase 1).
Nothing here can change a request's outcome:

* :meth:`UsageRecorder.record` never raises;
* a database error pauses recording for a few minutes instead of failing every request;
* a person costs one insert per area, channel and day per process.

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3, §6)
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone as dt_timezone
from typing import Any, Callable, Optional

from django.db import connection, transaction

from lex._version import __version__

logger = logging.getLogger(__name__)

USAGE_TABLE = "lex_usage_daily"
VERSIONS_TABLE = "lex_runtime_versions"

USAGE_METRICS_ENV = "LEX_USAGE_METRICS"
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})

PERSON = "person"
API_KEY = "api_key"

CHANNEL_API = "api"
CHANNEL_UI = "ui"
CHANNEL_UI_EMBED = "ui_embed"

#: A process remembers at most this many presence keys before starting over.
MAX_SEEN = 100_000
#: After a database error the recorder stays quiet this long instead of retrying per request.
PAUSE_AFTER_ERROR = timedelta(minutes=5)
#: The version row is refreshed at most this often per process.
VERSION_INTERVAL = timedelta(hours=1)
#: A failing recorder warns at most this often per process.
WARNING_INTERVAL = timedelta(hours=1)


def _utc_now() -> datetime:
    return datetime.now(dt_timezone.utc)


def utc_day(now: Optional[datetime] = None) -> date:
    """The UTC calendar day of an aware instant (default: now).

    Never ``django.utils.timezone.localdate()``: that is the configured zone, Berlin by
    default, which would file a request made at 00:30 in Berlin under the next UTC day.
    """
    moment = now if now is not None else _utc_now()
    if moment.tzinfo is None:
        raise ValueError("utc_day() needs an aware datetime; a naive one has no day")
    return moment.astimezone(dt_timezone.utc).date()


def usage_metrics_enabled() -> bool:
    """False when ``LEX_USAGE_METRICS`` is false, 0, no or off. Read on every call."""
    return os.getenv(USAGE_METRICS_ENV, "true").strip().lower() not in _FALSE_VALUES


def supported() -> bool:
    """The tables exist only on PostgreSQL; the migration is a no-op elsewhere."""
    return connection.vendor == "postgresql"


# ── storage ──────────────────────────────────────────────────────────────────


def insert_presence(day: date, actor_kind: str, actor_id: str, area: str, channel: str) -> None:
    """Insert one presence row; a row that already exists is left alone."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {USAGE_TABLE} (day, actor_kind, actor_id, area, channel) "
            f"VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [day, actor_kind, actor_id, area, channel],
        )


def upsert_runtime_version(version: str, usage_enabled: bool) -> None:
    """Record that ``version`` is serving now, and whether usage is being recorded."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {VERSIONS_TABLE} (version, usage_enabled) VALUES (%s, %s) "
            f"ON CONFLICT (version) DO UPDATE "
            f"SET last_seen = now(), usage_enabled = EXCLUDED.usage_enabled",
            [version, usage_enabled],
        )


def _rows(cursor) -> list[dict[str, Any]]:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def presence_rows() -> list[dict[str, Any]]:
    """Every presence row, ordered. For tests and operators."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT day, actor_kind, actor_id, area, channel FROM {USAGE_TABLE} "
            f"ORDER BY day, actor_kind, actor_id, area, channel"
        )
        return _rows(cursor)


def runtime_versions() -> list[dict[str, Any]]:
    """Every version row, oldest first. For tests and operators."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT version, first_seen, last_seen, usage_enabled FROM {VERSIONS_TABLE} "
            f"ORDER BY first_seen"
        )
        return _rows(cursor)


def clear_all() -> None:
    """Empty both tables. Tests only: they are not models, so the test flush never does."""
    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {USAGE_TABLE}")
        cursor.execute(f"DELETE FROM {VERSIONS_TABLE}")


# ── the process-local front ─────────────────────────────────────────────────


class UsageRecorder:
    """Deduplicates, throttles and isolates the writes one process makes."""

    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: set[tuple[date, str, str, str, str]] = set()
        self._seen_day: Optional[date] = None
        self._paused_until: Optional[datetime] = None
        self._version_due_at: Optional[datetime] = None
        self._warned_at: Optional[datetime] = None

    def reset(self) -> None:
        """Forget everything this process remembered. Tests only."""
        with self._lock:
            self._seen.clear()
            self._seen_day = None
            self._paused_until = None
            self._version_due_at = None
            self._warned_at = None

    def record(
        self,
        actor: Optional[tuple[str, str]],
        area: Optional[str],
        channel: str = CHANNEL_API,
    ) -> None:
        """Record that ``actor`` used ``area`` on ``channel``. Never raises.

        ``actor`` is ``(kind, id)``, or None for a request nobody is attributed to. ``area``
        is None for a request that is not recorded. In both cases the version row is
        still refreshed when it is due.
        """
        if not supported():
            return
        now = self._clock()
        try:
            enabled = usage_metrics_enabled()
            write_version, key = self._plan(now, actor, area, channel, enabled)
            if write_version:
                upsert_runtime_version(__version__, enabled)
            if key is not None:
                insert_presence(*key)
                with self._lock:
                    self._seen.add(key)
        except Exception:  # recording must never change a response
            self._pause(now)

    def _plan(self, now: datetime, actor, area, channel: str, enabled: bool):
        with self._lock:
            if self._paused_until is not None and now < self._paused_until:
                return False, None
            write_version = self._version_due_at is None or now >= self._version_due_at
            if write_version:
                self._version_due_at = now + VERSION_INTERVAL
            if not enabled or actor is None or area is None:
                return write_version, None
            day = utc_day(now)
            if day != self._seen_day or len(self._seen) >= MAX_SEEN:
                self._seen.clear()
                self._seen_day = day
            key = (day, actor[0], actor[1], area, channel)
            return write_version, (None if key in self._seen else key)

    def _pause(self, now: datetime) -> None:
        with self._lock:
            self._paused_until = now + PAUSE_AFTER_ERROR
            self._version_due_at = None
            warn = self._warned_at is None or now - self._warned_at >= WARNING_INTERVAL
            if warn:
                self._warned_at = now
        if warn:
            logger.warning(
                "Usage metrics could not be recorded; recording pauses for %s. "
                "The response was not affected.",
                PAUSE_AFTER_ERROR,
                exc_info=True,
            )


#: The one recorder per process, used by the middleware and the usage-area endpoint.
recorder = UsageRecorder()
```

- [ ] **Step 4: Run them and watch them pass**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -k "Rules or Recorder" -v`
Expected: 10.87, 10.89–10.93 and 10.105 pass; 10.88 still fails (the middleware is not installed yet).

- [ ] **Step 5: Commit**

```bash
git add lex/core/services/usage_metrics.py lex/test_project/tests/api_layer/test_10o_usage_recorder.py
git commit -m "feat(usage): record presence once per area, channel and day, pause on a database error"
```

---

### Task 4: The middleware, end to end (`api` channel)

**Size:** 1.5 h

**Files:**
- Create: `lex/core/middleware/usage_recorder.py`
- Modify: `lex/lex_app/settings.py` (the `MIDDLEWARE` list, lines 340-360)
- Test: `lex/test_project/tests/api_layer/test_10o_usage_recorder.py` (append a class)

**Interfaces:**
- Consumes:
  - `area_for` (Task 2);
  - `recorder`, `PERSON`, `API_KEY`, `CHANNEL_API` (Task 3);
  - `get_api_key_request_identity` and `is_instance_api_key_request` from `lex/api/utils/api_key_requests.py:62-92`.
- Produces: `actor_for(request) -> Optional[tuple[str, str]]`; `UsageRecorderMiddleware`.

- [ ] **Step 1: Append the failing end-to-end tests**

Add to the imports at the top of the test file:

```python
import os

from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework import status

from lex.core.services.usage_areas import API_AREAS
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, API_SIMPLE, ApiSimpleItem
```

Append:

```python
def _url_names(patterns) -> set[str]:
    names: set[str] = set()
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            names |= _url_names(pattern.url_patterns)
        elif isinstance(pattern, URLPattern) and pattern.name:
            names.add(pattern.name)
    return names


class TestCluster10o_RecorderEndToEnd(E2ETestCase):
    """Cluster 10o: real API requests through the whole middleware stack leave api rows."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        usage_metrics.clear_all()
        usage_metrics.recorder.reset()

    def tearDown(self) -> None:
        usage_metrics.clear_all()
        usage_metrics.recorder.reset()
        super().tearDown()

    def _get(self, url: str):
        before = usage_metrics.utc_day()
        response = self.client.get(url)
        after = usage_metrics.utc_day()
        return response, {before, after}

    def _assert_ok(self, response) -> None:
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            f"The request itself must succeed for this scenario: {response.status_code} {response.content[:300]!r}",
        )

    # -- 10.94 ---------------------------------------------------------
    def test_10_94_a_person_listing_records_the_grid(self) -> None:
        """
        Scenario 10.94: a person listing a model is active in the grid today, on the API.
        Given: a signed-in person.
        When: they GET a model's list endpoint.
        Then: one presence row: their user id, area ``grid``, channel ``api``, today's
              UTC day.
        """
        response, days = self._get(self.url_list(API_SIMPLE))
        self._assert_ok(response)

        rows = usage_metrics.presence_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn(rows[0]["day"], days)
        self.assertEqual(_keys(rows), [("person", str(self.user.pk), "grid", "api")])

    # -- 10.95 ---------------------------------------------------------
    def test_10_95_a_repeated_request_leaves_one_row(self) -> None:
        """
        Scenario 10.95: presence, not volume.
        Given: a signed-in person.
        When: they GET the same list three times.
        Then: one presence row.
        """
        for _ in range(3):
            self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        self.assertEqual(len(usage_metrics.presence_rows()), 1)

    # -- 10.96 ---------------------------------------------------------
    def test_10_96_history_is_recorded_from_both_of_its_routes(self) -> None:
        """
        Scenario 10.96: the history tab and the grid's as-of mode are history.
        Given: a record with history.
        When: the person opens its history, and the list with ``as_of``.
        Then: a ``history`` row on ``api``, and no ``grid`` row.
        """
        item = ApiSimpleItem.objects.create(name="h", value=1)
        self._assert_ok(self._get(self.url_history(API_SIMPLE, item.pk))[0])
        self._assert_ok(self._get(f"{self.url_list(API_SIMPLE)}?as_of=2030-01-01T00:00:00Z")[0])

        self.assertEqual(
            {(row["area"], row["channel"]) for row in usage_metrics.presence_rows()},
            {("history", "api")},
        )

    # -- 10.97 ---------------------------------------------------------
    def test_10_97_an_api_key_is_recorded_by_its_name_on_the_api_channel(self) -> None:
        """
        Scenario 10.97: an API key is an actor of its own, never a person.
        Given: a caller authenticated with an API key named "Nightly import".
        When: it GETs a list.
        Then: one row, actor ``api_key`` / "Nightly import", area ``grid``, channel
              ``api``, the only channel an API key appears on (spec §5.3.2).
        """
        self.authenticate_as_api_key(name="Nightly import")
        self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        self.assertEqual(
            _keys(usage_metrics.presence_rows()),
            [("api_key", "Nightly import", "grid", "api")],
        )

    # -- 10.98 ---------------------------------------------------------
    def test_10_98_the_instance_platform_key_is_never_recorded(self) -> None:
        """
        Scenario 10.98: the Instance Controller's own calls are not usage.
        Given: a caller whose key is the instance's LEX_API_KEY.
        When: it GETs a list.
        Then: no presence row.
        """
        raw_key = self.authenticate_as_api_key(name="instance")
        with patch.dict(os.environ, {"LEX_API_KEY": raw_key}):
            self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        self.assertEqual(usage_metrics.presence_rows(), [])

    # -- 10.99 ---------------------------------------------------------
    def test_10_99_a_failed_request_is_not_usage(self) -> None:
        """
        Scenario 10.99: only successful requests count.
        Given: a signed-in person.
        When: they GET a record that does not exist (404).
        Then: no presence row.
        """
        response, _ = self._get(self.url_detail(API_SIMPLE, 987654))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.assertEqual(usage_metrics.presence_rows(), [])

    # -- 10.100 --------------------------------------------------------
    def test_10_100_switched_off_records_the_version_but_no_usage(self) -> None:
        """
        Scenario 10.100: LEX_USAGE_METRICS=false stops usage rows and says so.
        Given: the switch set to false.
        When: a person GETs a list.
        Then: no presence row; one version row with usage_enabled false, so the collector
              reports "disabled" rather than "nobody used it".
        """
        with patch.dict(os.environ, {"LEX_USAGE_METRICS": "false"}):
            self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        self.assertEqual(usage_metrics.presence_rows(), [])
        versions = usage_metrics.runtime_versions()
        self.assertEqual([(v["version"], v["usage_enabled"]) for v in versions], [(__version__, False)])

    # -- 10.101 --------------------------------------------------------
    def test_10_101_the_running_version_is_recorded(self) -> None:
        """
        Scenario 10.101: a served request records the version serving it.
        Given: the switch at its default.
        When: a person GETs a list.
        Then: one version row: the running lex-app version, usage enabled.
        """
        self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        versions = usage_metrics.runtime_versions()
        self.assertEqual([(v["version"], v["usage_enabled"]) for v in versions], [(__version__, True)])

    # -- 10.102 --------------------------------------------------------
    def test_10_102_a_storage_failure_never_changes_the_response(self) -> None:
        """
        Scenario 10.102: the recorder failing is invisible to the person.
        Given: presence inserts that raise DatabaseError.
        When: a person GETs a list.
        Then: HTTP 200 with the normal body, and one warning.
        """
        ApiSimpleItem.objects.create(name="visible", value=1)
        with patch.object(usage_metrics, "insert_presence", side_effect=DatabaseError("simulated")), \
                self.assertLogs("lex.core.services.usage_metrics", level="WARNING") as logs:
            response, _ = self._get(self.url_list(API_SIMPLE))

        self._assert_ok(response)
        self.assertIn("visible", response.content.decode(), "The list body must be unaffected")
        self.assertEqual(len(logs.records), 1)

    # -- 10.103 --------------------------------------------------------
    def test_10_103_every_mapped_name_is_a_real_route(self) -> None:
        """
        Scenario 10.103: the area map cannot silently rot.
        Given: the live URLconf.
        When: its route names are collected.
        Then: every name in the API area map is among them; a renamed route would
              otherwise fall into ``app`` and its area would read zero forever.
        """
        missing = set(API_AREAS) - _url_names(get_resolver().url_patterns)
        self.assertEqual(missing, set(), f"Area map names with no route: {sorted(missing)}")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -k "RecorderEndToEnd or Rules" -v`
Expected: 10.88 FAILs. The E2E scenarios 10.94–10.97 and 10.100–10.102 FAIL (no rows, no version, no warning). 10.98, 10.99 and 10.103 already pass: nothing is recorded yet, and the map's names exist.

- [ ] **Step 3: Write the middleware**

`lex/core/middleware/usage_recorder.py`:

```python
"""Record which product area each successful API request used, per actor and UTC day.

Fleet metrics, phase 1: K1 active people, and the ``api`` channel of K2 feature reach,
which is supporting detail beside the interface's own area events (spec §5.3).

The middleware is the last entry in ``MIDDLEWARE``. So the user it reads is the one every
earlier middleware settled on, and it acts only after the view has returned. It never
changes the response. The storage layer pauses itself on a database error, and the
Instance Controller notices a silent recorder from the data (spec §7).

What is recorded:

* only a response below 400, to a method other than OPTIONS or HEAD, on a route with a
  URL name;
* a person: an authenticated Django user, by id;
* an API key: by the key's name, on this channel only.

Never recorded: the instance's own platform key (``LEX_API_KEY``, the Instance
Controller's), an anonymous request, an unnamed route, and ``POST api/usage/area``. That
endpoint records its event on an interface channel itself; ``area_for`` returns None for
it.

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3.1)
"""

from __future__ import annotations

import logging
from typing import Optional

from lex.api.utils.api_key_requests import (
    get_api_key_request_identity,
    is_instance_api_key_request,
)
from lex.core.services.usage_areas import area_for
from lex.core.services.usage_metrics import API_KEY, CHANNEL_API, PERSON, recorder

logger = logging.getLogger(__name__)

_SKIPPED_METHODS = frozenset({"OPTIONS", "HEAD"})


def actor_for(request) -> Optional[tuple[str, str]]:
    """``(kind, id)`` for whoever made ``request``, or None when nobody is attributed."""
    if is_instance_api_key_request(request):
        return None
    identity = get_api_key_request_identity(request)
    if identity is not None:
        return (API_KEY, identity.api_key_name)
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False) and getattr(user, "pk", None) is not None:
        return (PERSON, str(user.pk))
    return None


class UsageRecorderMiddleware:
    """See the module docstring."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._observe(request, response)
        except Exception:  # recording must never change a response
            logger.debug("Usage recorder skipped a request", exc_info=True)
        return response

    @staticmethod
    def _observe(request, response) -> None:
        if request.method in _SKIPPED_METHODS or response.status_code >= 400:
            return
        match = getattr(request, "resolver_match", None)
        if match is None or not match.url_name:
            return
        recorder.record(actor_for(request), area_for(match.url_name, request.GET), CHANNEL_API)
```

- [ ] **Step 4: Install it last**

In `lex/lex_app/settings.py`, the `MIDDLEWARE` list ends with the Keycloak permissions middleware (line 359). Add one entry after it:

```python
    "lex.api.middleware.keycloak_permissions.KeycloakPermissionsMiddleware",
    # Last, so it sees the user every middleware above settled on (fleet metrics, K1/K2).
    "lex.core.middleware.usage_recorder.UsageRecorderMiddleware",
]
```

- [ ] **Step 5: Run the whole file and watch it pass**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -v`
Expected: `23 passed`.

If 10.97 fails with a non-200 status, the API-key helper's permissions do not cover the list endpoint for `ApiSimpleItem`. Read `authenticate_as_api_key` in `lex/test_project/tests/_e2e_test_case.py:266-419`, which patches the login middleware for API keys, and pass the scopes it needs. Do not weaken the assertion on the recorded row.

- [ ] **Step 6: Run the API-layer cluster to catch regressions from the new middleware**

Run: `<test command> lex/test_project/tests/api_layer -q`
Expected: the same pass/fail tally as `lex-app-v2` before this branch, plus the 23 new passes. If anything else is red, compare against a run on the base branch.

- [ ] **Step 7: Commit**

```bash
git add lex/core/middleware/usage_recorder.py lex/lex_app/settings.py lex/test_project/tests/api_layer/test_10o_usage_recorder.py
git commit -m "feat(usage): every successful api request says which area it used"
```

---

### Task 5: The usage-area endpoint (`ui`, `ui_embed` channels)

**Size:** 1.5 h

**Files:**
- Create: `lex/api/views/usage/__init__.py` (empty), `lex/api/views/usage/UsageArea.py`
- Modify: `lex/process_admin/sites/process_admin_site.py`: one import beside the other view imports (lines 6-37), and one `path` in `url_patterns_for_react_admin` (lines 198-267)
- Test: `lex/test_project/tests/api_layer/test_10o_usage_recorder.py` (append a class)

**Interfaces:**
- Consumes: `UI_AREAS`, `USAGE_AREA_URL_NAME` (Task 2); `recorder`, `PERSON`, `CHANNEL_UI`, `CHANNEL_UI_EMBED` (Task 3).
- Produces: `POST api/usage/area`, route name `usage-area` in the `process_admin_rest_api` namespace. Body: exactly `{"area": <one of UI_AREAS>, "embed": <bool>}`. Answers:
  - 204 when the event was recorded, or deliberately not;
  - 400 for any other body;
  - 401 or 403 when there is no session user, or the session's CSRF token is missing.

  The interface plan calls this endpoint.

**Context, read before writing (lex-app-v2 at `d48449bf`):**

- **Where `api/` routes are registered.** The interface's API routes are built in `ProcessAdminSite._get_urls`, in `lex/process_admin/sites/process_admin_site.py:159-302`. The routes the interface calls sit in `url_patterns_for_react_admin`, at lines 198-267. They are mounted at `lex/lex_app/urls.py:42` (`path(process_admin_route, processAdminSite.urls)`), under the namespace `process_admin_rest_api` (`process_admin_site.py:47`, `:304-321`). So they carry the `DJANGO_BASE_PATH` prefix exactly like every other route the interface calls. `E2ETestCase._url` reverses that namespace (`_e2e_test_case.py:565-569`). A few framework endpoints sit at the root instead (`lex/lex_app/urls.py:35-40`, such as `api/user/` and `api/active-calculations`), because they are exempt from login (`settings.py:718-733`). This endpoint must not be exempt, so it goes into the process-admin site.
- **Authentication and permissions.** DRF's default authentication classes are `BearerMiddlewareAuthentication` then `SessionAuthentication` (`settings.py:761-771`). No default permission class is set, so each view names its own: `IsAuthenticated` in `lex/authentication/views/user_api.py:9-10`; `HasAPIKey | IsAuthenticated` in `lex/api/views/calculations/ActiveCalculations.py:15-17` and `lex/api/views/global_search_for_models/Search.py:14-15`. This view sets `authentication_classes = [SessionAuthentication]` and `permission_classes = [IsAuthenticated]`. `BearerMiddlewareAuthentication` is left out (`lex/authentication/authentication_backends/BearerMiddlewareAuthentication.py:3-18`), because the endpoint serves the interface's session only.
- **CSRF.** `CsrfViewMiddleware` (`settings.py:350`) skips DRF views, because `APIView.as_view()` is `csrf_exempt`. DRF's `SessionAuthentication.enforce_csrf` enforces CSRF instead, for every session-authenticated request. The interface sends `X-CSRFToken` from the `csrftoken` cookie (process-admin-general-client `src/api/httpClients/raHttpClient.ts:17-24`). Nothing in this task may weaken that.
- **Anonymous callers and API keys.** `ApiKeyAwareLoginRequiredMiddleware` (`lex/authentication/middleware.py:8-17`, `settings.py:356`) wraps oauth2_authcodeflow's `LoginRequiredMiddleware`. For a non-GET request under `^/api/` (that package's default `OIDC_MIDDLEWARE_API_URL_PATTERNS`), it answers an anonymous caller with a JSON 401 before DRF runs. An API key passes that middleware (`middleware.py:15-16`) but has no session user, so DRF refuses it. `SessionAuthentication` has no `authenticate_header`, so DRF answers 403. The codebase's convention for anonymous callers is to accept 401 or 403 (`test_10n_active_calculations_endpoint.py:162-166`), and these tests follow it.

- [ ] **Step 1: Append the failing tests**

Add to the imports at the top of the test file:

```python
from django.conf import settings
from django.middleware.csrf import get_token
from django.test import RequestFactory
from rest_framework.test import APIClient
```

Append:

```python
def _csrf_pair() -> tuple[str, str]:
    """The cookie secret and header token a browser holds once Django has set its cookie."""
    request = RequestFactory().get("/")
    token = get_token(request)
    return request.META["CSRF_COOKIE"], token


class TestCluster10o_UsageAreaEndpoint(E2ETestCase):
    """Cluster 10o: the interface's area events, through the real URL and middleware stack."""

    e2e_models = ALL_MODELS
    EVENT = {"area": "history", "embed": False}

    def setUp(self) -> None:
        super().setUp()
        usage_metrics.clear_all()
        usage_metrics.recorder.reset()

    def tearDown(self) -> None:
        usage_metrics.clear_all()
        usage_metrics.recorder.reset()
        super().tearDown()

    def _browser(self, *, with_csrf_token: bool = True) -> tuple[APIClient, dict]:
        """A signed-in browser session with CSRF enforced, as the interface has one."""
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        session = client.session
        session["oidc_expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        session.save()
        headers: dict = {}
        if with_csrf_token:
            secret, token = _csrf_pair()
            client.cookies[settings.CSRF_COOKIE_NAME] = secret
            headers["HTTP_X_CSRFTOKEN"] = token
        return client, headers

    def _send(self, body, *, client: APIClient | None = None, headers: dict | None = None):
        if client is None:
            client, headers = self._browser()
        return client.post(self._url("usage-area"), body, format="json", **(headers or {}))

    # -- 10.106 --------------------------------------------------------
    def test_10_106_an_interface_event_records_the_person_on_the_ui_channel(self) -> None:
        """
        Scenario 10.106: the interface says a person opened History today.
        Given: a signed-in browser session holding its CSRF token.
        When: it POSTs {"area": "history", "embed": false} to api/usage/area.
        Then: 204; exactly one row: the session's user id, ``history``, channel ``ui``,
              today's UTC day. The event's own request adds no ``api`` row.
        """
        before = usage_metrics.utc_day()
        response = self._send(self.EVENT)
        after = usage_metrics.utc_day()

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, response.content[:300])
        rows = usage_metrics.presence_rows()
        self.assertEqual(_keys(rows), [("person", str(self.user.pk), "history", "ui")])
        self.assertIn(rows[0]["day"], {before, after})

    # -- 10.107 --------------------------------------------------------
    def test_10_107_an_embedded_interface_is_recorded_as_ui_embed(self) -> None:
        """
        Scenario 10.107: use inside a Streamlit page or the widget host is told apart.
        Given: a signed-in browser session.
        When: it POSTs {"area": "grid", "embed": true}.
        Then: 204 and one row on channel ``ui_embed``.
        """
        response = self._send({"area": "grid", "embed": True})

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(_keys(usage_metrics.presence_rows()), [("person", str(self.user.pk), "grid", "ui_embed")])

    # -- 10.108 --------------------------------------------------------
    def test_10_108_a_body_outside_the_contract_is_refused(self) -> None:
        """
        Scenario 10.108: the event carries an area from the list and an embed flag, nothing else.
        Given: a signed-in browser session.
        When: it POSTs an area outside UI_AREAS (including the API-only ``app``), a body
              without ``embed``, a body with an extra key, a non-boolean ``embed``, a
              non-string area, or a JSON list.
        Then: 400 every time and no row. Refusing extra keys enforces spec §5.3.2's privacy
              boundary on the server too.
        """
        bodies = [
            {"area": "nonsense", "embed": False},
            {"area": "app", "embed": False},
            {"area": "grid"},
            {"area": "grid", "embed": False, "url": "/fund/42/show"},
            {"area": "grid", "embed": "true"},
            {"area": 3, "embed": False},
            ["grid", False],
        ]
        for body in bodies:
            with self.subTest(body=body):
                response = self._send(body)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content[:300])

        self.assertEqual(usage_metrics.presence_rows(), [])

    # -- 10.109 --------------------------------------------------------
    def test_10_109_an_anonymous_caller_is_refused(self) -> None:
        """
        Scenario 10.109: no session, no event.
        Given: a client with no session.
        When: it POSTs a valid event.
        Then: 401 or 403 (the login middleware answers 401 before DRF runs), and no row.
        """
        response = APIClient().post(self._url("usage-area"), self.EVENT, format="json")

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertEqual(usage_metrics.presence_rows(), [])

    # -- 10.110 --------------------------------------------------------
    def test_10_110_an_api_key_cannot_post_an_interface_event(self) -> None:
        """
        Scenario 10.110: an API key never appears on an interface channel.
        Given: a caller authenticated with an API key and no session.
        When: it POSTs a valid event.
        Then: 401 or 403, and no row.
        """
        self.authenticate_as_api_key(name="Nightly import")
        response = self.client.post(self._url("usage-area"), self.EVENT, format="json")

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertEqual(usage_metrics.presence_rows(), [])

    # -- 10.111 --------------------------------------------------------
    def test_10_111_a_session_without_its_csrf_token_is_refused(self) -> None:
        """
        Scenario 10.111: the CSRF check is in force.
        Given: a signed-in browser session that does not send its CSRF token.
        When: it POSTs a valid event.
        Then: 403 naming CSRF, and no row.
        """
        client, _ = self._browser(with_csrf_token=False)
        response = self._send(self.EVENT, client=client, headers={})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("CSRF", response.content.decode())
        self.assertEqual(usage_metrics.presence_rows(), [])

    # -- 10.112 --------------------------------------------------------
    def test_10_112_switched_off_answers_204_and_records_nothing(self) -> None:
        """
        Scenario 10.112: LEX_USAGE_METRICS=false turns the events off with the rest.
        Given: the switch set to false.
        When: a signed-in session POSTs a valid event.
        Then: 204, so the interface sees nothing unusual, and no presence row.
        """
        with patch.dict(os.environ, {"LEX_USAGE_METRICS": "false"}):
            response = self._send(self.EVENT)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(usage_metrics.presence_rows(), [])

    # -- 10.113 --------------------------------------------------------
    def test_10_113_repeated_events_leave_one_row(self) -> None:
        """
        Scenario 10.113: presence, not volume, whatever the interface's dedupe did.
        Given: a signed-in session.
        When: it POSTs the same event three times (three tabs, or a lost dedupe).
        Then: 204 each time and one row.
        """
        for _ in range(3):
            self.assertEqual(self._send(self.EVENT).status_code, status.HTTP_204_NO_CONTENT)

        self.assertEqual(len(usage_metrics.presence_rows()), 1)

    # -- 10.114 --------------------------------------------------------
    def test_10_114_the_day_is_the_servers_utc_day_across_midnight(self) -> None:
        """
        Scenario 10.114: a tab left open across midnight is one presence per UTC day.
        Given: an event at 2026-10-25T23:30Z, already 00:30 on 26 Oct in Berlin, and the
               same event at 2026-10-26T00:00Z.
        When: both are POSTed.
        Then: two ``ui`` rows, filed under 25 and 26 October: the day is fixed when lex-app
              records the event, in UTC (spec §5.3.2, §6).
        """
        clock = _Clock(datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc))
        with patch.object(usage_metrics.recorder, "_clock", clock):
            self.assertEqual(self._send({"area": "grid", "embed": False}).status_code, status.HTTP_204_NO_CONTENT)
            clock.now = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(self._send({"area": "grid", "embed": False}).status_code, status.HTTP_204_NO_CONTENT)

        self.assertEqual(
            [(r["day"], r["area"], r["channel"]) for r in usage_metrics.presence_rows()],
            [(date(2026, 10, 25), "grid", "ui"), (date(2026, 10, 26), "grid", "ui")],
        )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -k UsageAreaEndpoint -v`
Expected: 9 errors — `NoReverseMatch: Reverse for 'usage-area' not found`.

- [ ] **Step 3: Write the view**

`lex/api/views/usage/__init__.py`: empty.

`lex/api/views/usage/UsageArea.py`:

```python
"""``POST api/usage/area``: the web interface says a person used a product area today.

Fleet metrics, phase 1, K2 feature reach (spec §5.3.2). The interface calls this once per
browser tab, product area and UTC day. The body names an area and whether the interface
ran embedded, and nothing else:

* the person is the session's user, never a field of the body;
* the day is the server's UTC date when the event arrives;
* the row goes through the recorder's insert-once path, on channel ``ui``, or ``ui_embed``
  when ``embed`` is true.

Session-only, with DRF's CSRF check in force, because only the interface sends these
events. An anonymous caller is refused by the login middleware (401). An API key has no
session user and is refused here (403), so it never appears on an interface channel.

Answers:

* 204 — recorded, or deliberately not: ``LEX_USAGE_METRICS=false``, a non-PostgreSQL
  database, or a recorder paused after a database error. The interface cannot tell these
  apart and has no reason to.
* 400 — the body is not exactly ``{"area": <one of UI_AREAS>, "embed": <bool>}``. Extra
  keys are refused as well: an event carries nothing else (the privacy boundary).

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3.2)
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from lex.core.services.usage_areas import UI_AREAS
from lex.core.services.usage_metrics import CHANNEL_UI, CHANNEL_UI_EMBED, PERSON, recorder

BODY_KEYS = frozenset({"area", "embed"})


def _bad_request(detail: str) -> Response:
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


class UsageAreaView(APIView):
    """See the module docstring."""

    http_method_names = ["post"]
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, *args, **kwargs):
        body = request.data
        if not isinstance(body, dict) or set(body) != BODY_KEYS:
            return _bad_request('The body must be exactly {"area": <area>, "embed": <bool>}.')
        area, embed = body["area"], body["embed"]
        if not isinstance(area, str) or area not in UI_AREAS:
            return _bad_request("Unknown product area.")
        if not isinstance(embed, bool):
            return _bad_request('"embed" must be true or false.')
        recorder.record(
            (PERSON, str(request.user.pk)),
            area,
            CHANNEL_UI_EMBED if embed else CHANNEL_UI,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Register the route**

In `lex/process_admin/sites/process_admin_site.py`, add the import beside the other view imports:

```python
from lex.api.views.usage.UsageArea import UsageAreaView
```

Then add one entry at the end of `url_patterns_for_react_admin`, after `user-permissions` (lines 262-266):

```python
            path(
                "api/usage/area",
                UsageAreaView.as_view(),
                name="usage-area",  # lex.core.services.usage_areas.USAGE_AREA_URL_NAME
            ),
```

Do not add the path to `OIDC_MIDDLEWARE_NO_AUTH_URL_PATTERNS`.

- [ ] **Step 5: Run the whole file and watch it pass**

Run: `<test command> lex/test_project/tests/api_layer/test_10o_usage_recorder.py -v`
Expected: `32 passed`.

If 10.106 shows an extra `("person", …, "app", "api")` row, the middleware is not skipping the endpoint's own request. Check that the route's name equals `USAGE_AREA_URL_NAME`; do not filter in the test. If 10.111 passes without a token, a test client is not enforcing CSRF: `APIClient(enforce_csrf_checks=True)` is required. Never reach for `csrf_exempt`.

- [ ] **Step 6: Run the API-layer cluster again**

Run: `<test command> lex/test_project/tests/api_layer -q`
Expected: no new failures against `lex-app-v2`; the 32 new tests pass.

- [ ] **Step 7: Commit**

```bash
git add lex/api/views/usage lex/process_admin/sites/process_admin_site.py lex/test_project/tests/api_layer/test_10o_usage_recorder.py
git commit -m "feat(usage): the interface records the product areas a person opens"
```

---

### Task 6: The audit contract the collector depends on

**Size:** 0.75 h

**Files:**
- Test: `lex/test_project/tests/audit_logging/test_6r_fleet_metrics_contract.py` (create)

**Interfaces:**
- Consumes: `AuditLog`, `AuditLogStatus`, the audit-logging test models `ALL_MODELS`, `AUDIT_ATOMIC` and `AuditAtomicCalc` from `lex/test_project/tests/audit_logging/models.py`, and `E2ETestCase`.
- Produces: nothing for later tasks. This pins the behaviour that the Instance Controller's SQL for K1's writers and K3 relies on (collector plan, Tasks 5 and 6).

- [ ] **Step 1: Write the tests**

```python
"""The audit-table contract the Instance Controller's fleet-metrics collector reads.

Intent: fleet metrics (LEX-749) counts writes, writers (K1) and calculation runs (K3)
from each instance's audit tables. The SQL that does it lives in the Instance
Controller, not here. These tests pin the columns and the behaviour that SQL depends on,
so a lex-app change that would silently break the collector fails lex-app's own suite
first:

* the columns exist, and the timestamps are ``timestamptz``;
* API keys keep their name, which is how K1 leaves them out of the writers (spec §4.3);
* a finished calculation run leaves audit rows that carry its terminal ``is_calculated``
  in the payload and share the request's calculation id: one run, however many rows;
* a plain edit of a calculation row is audited with ``is_calculated = NOT_CALCULATED``, so
  it is never mistaken for a run.

Cluster 6r — scenarios 6.119–6.122. Type: I and E.
Covers: lex/audit_logging/models/AuditLog.py, lex/audit_logging/models/AuditLogStatus.py,
lex/audit_logging/utils/calculation_audit.py, lex/api/views/model_entries/One.py.
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6r_fleet_metrics_contract.py -v
"""

from __future__ import annotations

import time

from django.db import connection
from django.test import TestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AUDIT_ATOMIC, AuditAtomicCalc

import pytest

pytestmark = pytest.mark.audit_logging

TERMINAL_STATES = ("SUCCESS", "ERROR", "ABORTED", "CANCELLED")


class TestCluster6r_AuditColumns(TestCase):
    """Cluster 6r: the columns the collector's SQL names."""

    CONTRACT = {
        "audit_logging_auditlog": {
            "id", "date", "author", "resource", "action", "payload", "calculation_id", "object_id",
        },
        "audit_logging_auditlogstatus": {"id", "audit_log_id", "status", "created_at", "updated_at"},
        # K1 leaves API-key authors out of the writers by matching their name.
        "rest_framework_api_key_apikey": {"name"},
    }
    TIMESTAMPTZ = {
        ("audit_logging_auditlog", "date"),
        ("audit_logging_auditlogstatus", "created_at"),
        ("audit_logging_auditlogstatus", "updated_at"),
    }

    # -- 6.119 ---------------------------------------------------------
    def test_6_119_the_columns_the_collector_reads_exist(self) -> None:
        """
        Scenario 6.119: the audit tables keep the shape fleet metrics reads.
        Given: a migrated database.
        When: the catalog is read.
        Then: every column the collector names exists, and the three timestamps are
              ``timestamp with time zone`` (the collector compares them to UTC bounds).
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_name = ANY(%s)",
                [list(self.CONTRACT)],
            )
            rows = cursor.fetchall()
        present = {(table, column) for table, column, _ in rows}
        types = {(table, column): data_type for table, column, data_type in rows}
        for table, columns in self.CONTRACT.items():
            missing = {c for c in columns if (table, c) not in present}
            self.assertEqual(
                missing, set(),
                f"{table} lost columns the Instance Controller's fleet-metrics SQL reads: {sorted(missing)}",
            )
        for key in self.TIMESTAMPTZ:
            self.assertEqual(types.get(key), "timestamp with time zone", f"{key} must stay timestamptz")


class TestCluster6r_CalculationRunContract(E2ETestCase):
    """Cluster 6r: what a calculation run and a plain edit leave in the audit."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    e2e_unpatch = {"store_message", "build_cache_key", "ensure_terminal_calculation_audit"}

    SETTLE_TIMEOUT_S = 10.0
    POLL_S = 0.05

    @staticmethod
    def _run_id(calc) -> str:
        return f"{AUDIT_ATOMIC}_{calc.pk}_update_6r"

    def _terminal_rows(self, calc) -> list:
        rows = AuditLog.objects.filter(resource=AUDIT_ATOMIC, object_id=calc.pk).order_by("id")
        return [row for row in rows if (row.payload or {}).get("is_calculated") in TERMINAL_STATES]

    def _run_to_the_end(self, calc) -> list:
        response = self.client.patch(
            self.url_detail(AUDIT_ATOMIC, calc.pk, calc_id=self._run_id(calc)),
            data={"calculate": "true"}, format="json",
        )
        self.assertLess(response.status_code, 300, f"Trigger failed: {response.status_code}")
        deadline = time.monotonic() + self.SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            rows = self._terminal_rows(calc)
            finished = AuditLogStatus.objects.filter(
                audit_log__in=rows, status__in=("success", "failure"),
            ).exists()
            if rows and finished:
                return rows
            time.sleep(self.POLL_S)
        self.fail(f"No terminal audit row for {AUDIT_ATOMIC} pk={calc.pk} within {self.SETTLE_TIMEOUT_S}s")

    def _assert_one_run(self, calc, rows, expected_state: str) -> None:
        self.assertEqual(
            {row.calculation_id for row in rows}, {self._run_id(calc)},
            "Every terminal audit row of one run must carry the request's calculation id; "
            "the collector counts runs by it (collector plan, K3).",
        )
        self.assertEqual(rows[-1].payload["is_calculated"], expected_state)
        statuses = AuditLogStatus.objects.filter(audit_log__calculation_id=self._run_id(calc))
        self.assertTrue(statuses.exists(), "A run must leave status rows to time it by")
        for status_row in statuses:
            self.assertGreaterEqual(status_row.updated_at, status_row.created_at)

    # -- 6.120 ---------------------------------------------------------
    def test_6_120_a_successful_run_is_one_run_ending_in_success(self) -> None:
        """
        Scenario 6.120: a clean calculation is countable as one successful run.
        Given: an atomic calculation that succeeds.
        When: it is triggered through the API with a calculation id, as the frontend does.
        Then: its terminal audit rows all carry that id, the last one says SUCCESS, and
              its status rows bracket the run.
        """
        calc = AuditAtomicCalc.objects.create(name="ok", should_fail=False)
        self._assert_one_run(calc, self._run_to_the_end(calc), "SUCCESS")

    # -- 6.121 ---------------------------------------------------------
    def test_6_121_a_failed_run_is_one_run_ending_in_error(self) -> None:
        """
        Scenario 6.121: a failing calculation is countable as one failed run.
        Given: an atomic calculation whose calculate() raises.
        When: it is triggered through the API.
        Then: its terminal audit rows carry the id and the last one says ERROR.
        """
        calc = AuditAtomicCalc.objects.create(name="boom", should_fail=True)
        self._assert_one_run(calc, self._run_to_the_end(calc), "ERROR")

    # -- 6.122 ---------------------------------------------------------
    def test_6_122_a_plain_edit_of_a_calculation_row_is_not_a_run(self) -> None:
        """
        Scenario 6.122: editing a calculation row is an edit, not a calculation.
        Given: a calculation row.
        When: a person PATCHes its name (no ``calculate``).
        Then: its audit row says ``is_calculated = NOT_CALCULATED``, so the collector's
              run rule cannot count it.
        """
        calc = AuditAtomicCalc.objects.create(name="before")
        edit_id = f"{AUDIT_ATOMIC}_{calc.pk}_update_6r_edit"
        response = self.client.patch(
            self.url_detail(AUDIT_ATOMIC, calc.pk, calc_id=edit_id),
            data={"name": "after"}, format="json",
        )
        self.assertLess(response.status_code, 300, f"Edit failed: {response.status_code}")

        rows = list(AuditLog.objects.filter(calculation_id=edit_id))
        self.assertTrue(rows, "The edit must be audited")
        self.assertEqual(
            {(row.payload or {}).get("is_calculated") for row in rows}, {"NOT_CALCULATED"},
            "A plain edit must not look like a finished run to the collector",
        )
```

- [ ] **Step 2: Run them**

Run: `<test command> lex/test_project/tests/audit_logging/test_6r_fleet_metrics_contract.py -v`
Expected: `4 passed`. These pin existing behaviour, so they should pass on the first run.

If 6.120 or 6.121 fails on the calculation-id assertion, the framework does not carry the request's id to the terminal row. That is a real finding, not a test to soften:

1. Record it as a `BUG-NNN` in `known-bugs.md`.
2. Mark the test `@pytest.mark.xfail(strict=True)`.
3. Tell the owner of the collector plan. Its Task 6 counts runs by that id, and would then have to key them by `(resource, object_id)` within a day instead.

- [ ] **Step 3: Commit**

```bash
git add lex/test_project/tests/audit_logging/test_6r_fleet_metrics_contract.py
git commit -m "test(audit): pin the audit shape the fleet-metrics collector reads"
```

---

### Task 7: Bring the test-plan into sync

**Size:** 0.5 h

**Files:**
- Modify: `lex/test_project/test-plan/clusters/10-api_layer/allocation.yaml`, `batches.md`, `cluster.md`
- Modify: `lex/test_project/test-plan/clusters/06-audit_logging/allocation.yaml`, `batches.md`, `cluster.md`
- Create: `lex/test_project/test-plan/progress/sessions/<today>-fleet-metrics-recorder.md`
- Regenerate: `lex/test_project/test-plan/progress/dashboard.md`

- [ ] **Step 1: Add the 10o letter to `clusters/10-api_layer/allocation.yaml` and bump `max_scenario` to 114**

```yaml
  o:
    title: Usage recorder and interface area events (fleet metrics)
    scenarios: 10.83-10.114
    status: complete
    tests:
      pass: 32
      skip: 0
      xfail: 0
    note: fleet metrics phase 1 (LEX-749), release R3; raw-SQL tables with a channel column
      (api, ui, ui_embed), the two area vocabularies, the recorder, the middleware end to
      end, and POST api/usage/area (session-only, CSRF in force)
```

- [ ] **Step 2: Append the batch block to `clusters/10-api_layer/batches.md`**, matching the shape of the most recent block:

```markdown
### Batch 10o — Usage recorder and interface area events (fleet metrics, phase 1) ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.83 – 10.114 |
| Type | U + I + E |
| Files covered | `core/sql/usage_metrics.sql`, `core/migrations/0002_usage_metrics.py`, `core/services/usage_areas.py`, `core/services/usage_metrics.py`, `core/middleware/usage_recorder.py`, `api/views/usage/UsageArea.py`, the `usage-area` route in `process_admin/sites/process_admin_site.py` |
| Test file | `lex/test_project/tests/api_layer/test_10o_usage_recorder.py` |
| Test classes | `TestCluster10o_UsageTables`, `TestCluster10o_AreaMap`, `TestCluster10o_Rules`, `TestCluster10o_Recorder`, `TestCluster10o_RecorderEndToEnd`, `TestCluster10o_UsageAreaEndpoint` |
| Fixtures | api_layer `ALL_MODELS`; `usage_metrics.clear_all()` in setUp/tearDown (the tables are not models); a CSRF-enforcing `APIClient` for the endpoint |
| Tests landed | **32 pass / 0 fail** |
| Status | ✅ Complete |
| Note | Design: `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md` §5.3.1–§5.3.2. 10.103 guards the API area map against renamed routes; 10.104 pins `UI_AREAS` to the spec, which the interface mirrors. The interface half is process-admin-general-client batch F02 `d`. |
```

- [ ] **Step 3: Extend the scenario table in `clusters/10-api_layer/cluster.md`** with one row per scenario from 10.83 to 10.114. Use each test's one-line docstring as "Scenario" and its "Then" as "What We Assert".

- [ ] **Step 4: Do the same for cluster 6**:
  - letter `r` in `clusters/06-audit_logging/allocation.yaml`: `scenarios: 6.119-6.122`, `tests.pass: 4`, `max_scenario: 122`;
  - a `Batch 6r — The audit contract fleet metrics reads ✅` block in `batches.md`: type I + E; test file `lex/test_project/tests/audit_logging/test_6r_fleet_metrics_contract.py`; classes `TestCluster6r_AuditColumns`, `TestCluster6r_CalculationRunContract`;
  - four rows in `cluster.md`.

- [ ] **Step 5: Add the session fragment** `lex/test_project/test-plan/progress/sessions/<today>-fleet-metrics-recorder.md`:

```markdown
---
date: <today>
clusters: [10, 6]
tests_added: 36
suite_tally: <the pass/fail tally of the full run in Step 6>
---

Fleet metrics phase 1, the lex-app half of release R3: [batch 10o](../../clusters/10-api_layer/batches.md)
records per-day presence by channel. The middleware writes API activity on `api`, and
`POST api/usage/area` writes the interface's area events on `ui` and `ui_embed`. With the
running version (K7), that gives K1 and K2. [Batch 6r](../../clusters/06-audit_logging/batches.md)
pins the audit shape the Instance Controller's collector reads.
```

- [ ] **Step 6: Run both new files and the two clusters, then regenerate the dashboard**

Run: `<test command> lex/test_project/tests/api_layer lex/test_project/tests/audit_logging -q`
Expected: no new failures against `lex-app-v2`; the 36 new tests pass. Put the real tally in the session fragment.

Run: `python .github/scripts/test_plan_aggregates.py build && python .github/scripts/test_plan_aggregates.py check && python .github/scripts/test_plan_aggregates.py validate`
Expected: all three exit 0.

- [ ] **Step 7: Commit**

```bash
git add lex/test_project/test-plan
git commit -m "docs(test-plan): batches 10o and 6r, the fleet-metrics recorder, its endpoint and its contract"
```

---

### Task 8: The public paragraph (lex-app-docs)

**Size:** 0.25 h

lex-app-docs rejects a second push to a branch that already has a pull request. So the branch must hold the whole change before its first push.

**Files:**
- Modify (lex-app-docs, branch from `origin/main`): `content/ship-and-operate/monitoring and health.md`. Add a new `## Usage metrics` section between `## Logs` (line 53 at `baddbe4d`) and `## What to alert on` (line 64).

- [ ] **Step 1: Branch from main in a fresh worktree of lex-app-docs**

```bash
cd lex-app-docs
git fetch origin main
git worktree add -b docs/usage-metrics ../lex-app-docs-usage-metrics origin/main
```

- [ ] **Step 2: Add the section**

```markdown
## Usage metrics

An instance records which parts of the application are used, in its own database, so that
the platform team can see which features and instances are in use.

- **What is stored:** one row per person (by internal user id), product area and day. The
  areas are a fixed list: the grid, records, creating records, history, the audit log,
  calculations and their logs, processes, reports, analytics, dashboards, search, exports,
  files and monitoring.
- **Where it comes from:** the web interface reports an area when a person opens it or
  uses it. It sends only the area's name and whether it ran embedded in another page: never
  a page address, a record, a search term or a file name. API requests are recorded
  separately, per person or API key, as API activity. The two are never added together.
- **What leaves the instance:** only counts. Once a day the platform reads how many people
  used each area. No identity leaves the instance's database.
- **Turning it off:** set `LEX_USAGE_METRICS=false`. The instance then records only which
  lex-app version it runs, and accepts and discards the interface's reports.
```

- [ ] **Step 3: Check the site builds, commit once, push once**

Run the repository's own build or lint command (see its `package.json` or README), then:

```bash
git add "content/ship-and-operate/monitoring and health.md"
git commit -m "docs(operate): what the usage metrics record, and how to turn them off"
git push -u origin docs/usage-metrics
```

Open the pull request; the user merges it.

---

### Task 9: The pull request, the pin and the release dependency

**Size:** 0.5 h

- [ ] **Step 1: Push and open the PR against `lex-app-v2`**

```bash
git push -u origin feat/fleet-metrics-recorder
gh pr create --base lex-app-v2 --title "feat(usage): record which product areas are used, per person, channel and UTC day" --body "$(cat <<'EOF'
Fleet metrics phase 1, the lex-app half of release R3 (LEX-749 / LEX-751). Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md, revision 2 (§5.3).

- Two raw-SQL tables in the instance database: `lex_usage_daily` (person or API key, product area, channel, UTC day) and `lex_runtime_versions` (each lex-app version served, first and last seen).
- A middleware, last in `MIDDLEWARE`, records successful API requests on the `api` channel after the view returns. It never changes a response; a database error pauses recording for five minutes.
- `POST api/usage/area` records the web interface's area events on `ui` or `ui_embed`. It is session-only with the CSRF check in force; the person comes from the session, and the body must be exactly `{area, embed}`.
- `LEX_USAGE_METRICS=false` turns usage rows off per instance, on both writers; the version row still says so.
- Batch 6r pins the audit shape the Instance Controller's collector reads, so a change here that would break it fails this suite.

Tests: batches 10o (32) and 6r (4).

The interface half lands in process-admin-general-client; a follow-up one-line PR pins the lex-app-frontend release that carries it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Pin the interface release that emits the events**

Do this only after the frontend plan's release task has published the lex-app-frontend release to PyPI.

1. Open the one-line pull request that frontend-versioning.md describes (`docs/ci-cd/frontend-versioning.md:84-94`) in `requirements.txt:65`, for example `lex-app-frontend~=2.2.0` → `lex-app-frontend~=2.3.0`. Use the version that release actually published.
2. Merge it before cutting the lex-app release. `check_frontend_pin.py`, run by `pip_publish.yml:202`, refuses to publish a lex-app pinned to a frontend version that does not exist.
3. Cut the lex-app release that contains both this branch and the pin. A lex-app release with the endpoint but the old pin still works, but records no `ui` rows.

- [ ] **Step 3: Record the release dependency**

In LEX-751, note which lex-app release carries the recorder and pins the emitting interface. Production instances pick both up when they are upgraded to that release (spec §8.1, R3.2). Until then, the collector reports each instance's recorder state as `absent`, `disabled` or `enabled`.

The §5.3.2 end-to-end verification on a test instance is stage R3.1's evidence. It is budgeted separately (spec §8.2) and is not part of this plan.
