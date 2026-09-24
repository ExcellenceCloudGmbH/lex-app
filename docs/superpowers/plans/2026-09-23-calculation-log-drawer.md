# Calculation Log Drawer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A finished calculation's log — and its traceback, where the model records one — opens from the calculation row in a right-hand drawer, without leaving the table.

**Architecture:** The grid endpoint annotates each calculation row with the id of the newest run it started (one query per page, by the same `<model>_<pk>_` prefix rule the frontend's `useResolvedCalculationId` already uses). The status cell gains a log button that opens a `CalculationLogDrawer`: the live `CalculationLogStream` while running, `CalculationLogTree` once finished, led by a headline for ERROR / ABORTED / CANCELLED. The drawer shell is split out of `FormDrawer` as `SideDrawer`, which gains a persisted full-width toggle.

**Tech Stack:** Django + DRF (lex-app), React 18 + react-admin 5 + MUI + vitest (process-admin-general-client, "PAC").

**Spec:** `docs/superpowers/specs/2026-09-23-calculation-log-drawer-design.md` (lex-app). Read its *Amendments* section first — five things the first draft said were corrected against the source.

## Global Constraints

- Reserved field names, exactly: `lex_reserved_calculation_id`, `lex_reserved_has_calculation_log`.
- Run rule, exactly: a run started from record `pk` of model `m` has a `calculationId` starting `f"{m._meta.model_name}_{pk}_"`; the newest is the highest `CalculationLog.id`. The trailing underscore is load-bearing.
- No migration. `calculationId` is already `db_index=True` (migration `0005`).
- Failure text priority, exactly: `calculation_error_message`, then `error_message`.
- Drawer widths, exactly: forms `min(640px, 90vw)`; log `min(1000px, 92vw)`; expanded `100vw`.
- Expansion is remembered in `localStorage` under `lex.calculationLogDrawer.expanded`; storage errors never throw.
- The running body is `CalculationLogStream` — it shares the Actions column's socket through `acquireLogSocket`. Never open a socket in the drawer.
- No log button in history view or under `suppressLogViewer`.
- `FormDrawer`'s props and callers do not change; `EditDrawer.cluster.test.tsx` (F5.60–F5.63) passes untouched.
- lex-app worktree directories must be named `lex_*` (Django derives an app label from the directory name).
- The test plan forbids `skip_hooks=True`. Create `LogRootCalc` rows with `bulk_create`, which skips `save()`, as cluster 15 already does.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Compare test and type-check results against a baseline taken before your change. Both repos carry pre-existing failures (cluster 15 has 9; PAC's `Run linters` CI job is red on its own base branch). Only the delta is evidence.

## Environments

**lex-app** — worktree `/home/syscall/Documents/lex_logdrawer`, already on branch `docs/calculation-log-drawer-spec` carrying the spec and this plan. Before Task 1:

```bash
cd /home/syscall/Documents/lex_logdrawer && git switch -c feat/calculation-log-drawer
```

Run cluster-15 tests from that directory with:

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$PWD/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest lex/test_project/tests/calculation_logging/ -q
```

The remote is HTTPS with no credential helper; push over SSH: `git push git@github.com:ExcellenceCloudGmbH/lex-app.git HEAD:feat/calculation-log-drawer`.

**PAC** — created in Task 4 as `/home/syscall/LUND_IT/pac_logdrawer`, borrowing the main checkout's `node_modules`. `tsc` and `vitest` work that way; `eslint` does not (the borrowed install has ESLint 9, the branch's config is v8-style) — lint is CI's.

## File Map

**lex-app**

| File | Responsibility |
|---|---|
| `lex/audit_logging/utils/latest_calculation.py` (create) | The run rule: `run_prefix`, `latest_calculation_ids`, `annotate_latest_calculation`, `is_calculation_model` |
| `lex/api/serializers/base_serializers.py` (modify) | Declare the two reserved fields on calculation-model serializers only |
| `lex/api/views/model_entries/List.py` (modify) | Annotate the grid's leaf page for calculation models |
| `lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py` (create) | Scenarios 15.39–15.46 |
| `lex/test_project/test-plan/clusters/15-calculation_logging/{allocation.yaml,batches.md}` (modify) | Batch 15i |

**PAC**

| File | Responsibility |
|---|---|
| `src/components/SideDrawer/SideDrawer.tsx` (create) | Drawer shell: panel, header, close, persisted expand toggle |
| `src/components/model-components/forms/FormDrawer.tsx` (modify) | `SideDrawer` plus the react-admin form cascade |
| `src/components/model-components/CalculateFunctionality/calculationRunHeadline.ts` (create) | Pure: what the drawer says before the log, per status |
| `src/pages/CalculationLogTree/CalculationLogTree.tsx` (modify) | `showTitle` prop |
| `src/components/model-components/CalculateFunctionality/CalculationLogDrawer.tsx` (create) | Headline, then stream or tree |
| `src/components/model-components/CalculateFunctionality/CalculateFunctionality.tsx` (modify) | Log button, permanent play button, spinner removed |
| tests under each directory's `__test__/` | F12.73–F12.78, F7.55–F7.58 |
| `docs/test-plan/clusters/{F07-calc_status,F12-embed_streamlit}/{allocation.yaml,batches.md}` (modify) | Batches 7f and 12m |

**Order and merging.** Phase A and Phase B are separate PRs. Either can merge first: without Phase A, rows simply never carry `lex_reserved_has_calculation_log`, so the log button shows only while a run is live.

---

## Phase A — lex-app

### Task 1: Resolve each row's newest run in one query

**Files:**
- Create: `lex/audit_logging/utils/latest_calculation.py`
- Create: `lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py`

**Interfaces:**
- Produces:
  - `run_prefix(model_name: str, pk: object) -> str`
  - `latest_calculation_ids(model_name: str, pks: Iterable[object]) -> Dict[str, str]` — maps `str(pk)` to the newest `calculationId`; records with no run are absent.
  - `annotate_latest_calculation(rows, model) -> None` — sets `row._lex_calculation_id: Optional[str]` and `row._has_calculation_log: bool` on every row; never raises.
  - `is_calculation_model(model) -> bool`

- [ ] **Step 1: Take the cluster-15 baseline**

Run the cluster-15 command from *Environments*. Record the failed/passed counts. Expect about 9 pre-existing failures; they are not yours.

- [ ] **Step 2: Write the failing tests**

Create `lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py`:

```python
"""Cluster 15i — a calculation row learns its latest run.

Scenarios 15.39 – 15.46.

Intent (reported 2026-09-23): "once they are done, we lose the logs and we
would have to do a lot of steps to get them." The live log is served from
cache, and the root calculation purges that cache when it completes, so a
finished run's log had no path back from the row that started it. The durable
copy is in the CalculationLog table; what was missing is the row knowing which
run to open.

The rule is the one the frontend's ``useResolvedCalculationId`` already
applies: a run started from record ``pk`` has an id beginning
``f"{model}_{pk}_"``, and the newest is the highest ``CalculationLog.id``.
Stating it twice, in two languages, is what makes one row open one run in the
table and in every widget.
"""
from __future__ import annotations

from unittest import mock
from uuid import uuid4

import pytest
from django.db import connection
from rest_framework import status

from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.latest_calculation import latest_calculation_ids

from . import _CalcLogTestCase
from .models import LogRootCalc

pytestmark = pytest.mark.calculation_logging


def _run(model_name, pk, text="step"):
    """Write one log row for a new run started from ``(model_name, pk)``.

    The id has the exact shape the frontend mints:
    ``{model}_{pk}_update_{uuid}``.
    """
    calculation_id = f"{model_name}_{pk}_update_{uuid4().hex}"
    CalculationLog.objects.create(calculationId=calculation_id, calculation_log=text)
    return calculation_id


def _row(name):
    """A LogRootCalc row, written without running a calculation.

    ``bulk_create`` skips ``save()`` and therefore the calculation hooks — the
    cluster's own idiom, because the test plan forbids ``skip_hooks=True``.
    """
    LogRootCalc.objects.bulk_create(
        [LogRootCalc(name=name, child_mode="log_only", units_csv="")]
    )
    return LogRootCalc.objects.get(name=name)


def _ag(**overrides):
    req = {
        "startRow": 0,
        "endRow": 100,
        "rowGroupCols": [],
        "groupKeys": [],
        "pivotCols": [],
        "pivotMode": False,
        "valueCols": [],
        "sortModel": [],
        "filterModel": {},
    }
    req.update(overrides)
    return req


class TestCluster15i_TheRunRule(_CalcLogTestCase):
    """Which run belongs to which row — the rule, independent of the grid."""

    def test_15_39_the_newest_run_by_id_wins(self):
        """Scenario 15.39: a record calculated twice opens its second run.

        Newest by ``id``, not ``timestamp`` — the frontend resolver sorts by
        ``id DESC``, and two rules would open two different runs.
        """
        _run("logrootcalc", 7)
        newest = _run("logrootcalc", 7)

        self.assertEqual(latest_calculation_ids("logrootcalc", [7]), {"7": newest})

    def test_15_40_record_1_never_resolves_record_11s_runs(self):
        """Scenario 15.40: the trailing underscore is load-bearing.

        Without it, ``logrootcalc_1`` is a prefix of ``logrootcalc_11_…`` and
        record 1 would open a run it never started.
        """
        eleven = _run("logrootcalc", 11)

        self.assertEqual(latest_calculation_ids("logrootcalc", [1]), {})
        self.assertEqual(latest_calculation_ids("logrootcalc", [1, 11]), {"11": eleven})

    def test_15_41_a_run_belongs_to_the_longest_matching_prefix(self):
        """Scenario 15.41: string keys containing ``_``.

        ``m_a_b_update_…`` starts with both ``m_a_`` and ``m_a_b_``. It can only
        have been started by record ``a_b``: record ``a``'s own runs begin
        ``m_a_update_``. Matched shortest-first, record ``a`` would steal it.
        """
        short = _run("m", "a")
        longer = _run("m", "a_b")

        self.assertEqual(
            latest_calculation_ids("m", ["a", "a_b"]),
            {"a": short, "a_b": longer},
        )

    def test_15_42_a_whole_page_is_one_query(self):
        """Scenario 15.42: never one query per grid row.

        Fifty records with two runs each resolve in a single query. This is the
        property that stops the annotation turning a page load into N+1.
        """
        for pk in range(1, 51):
            _run("logrootcalc", pk)
            _run("logrootcalc", pk)

        with self.assertNumQueries(1):
            result = latest_calculation_ids("logrootcalc", range(1, 51))

        self.assertEqual(len(result), 50)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: the cluster-15 command with `lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py -v` in place of the directory.
Expected: collection error — `ModuleNotFoundError: No module named 'lex.audit_logging.utils.latest_calculation'`.

- [ ] **Step 4: Write the implementation**

Create `lex/audit_logging/utils/latest_calculation.py`:

```python
"""The newest calculation run started from each record on a page.

A calculation row carries ``is_calculated`` and nothing else, so on its own the
grid cannot tell which rows have a finished log to open. This resolves, for a
whole page of rows in one query, the ``calculationId`` of the newest run each
row started.

The rule is the one ``useResolvedCalculationId`` applies in the frontend,
stated here in Python so the table and every widget open the same run for the
same row: a run started from record ``pk`` of model ``m`` has an id beginning
``f"{m}_{pk}_"`` (the frontend mints ``{m}_{pk}_update_{uuid}``), and the newest
is the one with the highest ``CalculationLog.id``.

The trailing underscore is load-bearing: without it, record 1 would match
record 11's runs.

Served by the index ``calculationId`` already has (migration 0005). On
PostgreSQL, Django adds a ``text_pattern_ops`` companion index for an indexed
text field, and that is what serves ``LIKE 'prefix%'``.
"""
from __future__ import annotations

import logging
from functools import reduce
from operator import or_
from typing import Dict, Iterable, Tuple

from django.db.models import Max, Q

logger = logging.getLogger(__name__)


def is_calculation_model(model) -> bool:
    """Whether ``model`` is a ``CalculationModel`` subclass.

    Imported lazily: ``lex.core.models.CalculationModel`` pulls in much of the
    framework, and the serializer layer imports this module.
    """
    from lex.core.models.CalculationModel import CalculationModel

    return isinstance(model, type) and issubclass(model, CalculationModel)


def run_prefix(model_name: str, pk: object) -> str:
    """The id prefix every run started from this record carries."""
    return f"{model_name}_{pk}_"


def latest_calculation_ids(model_name: str, pks: Iterable[object]) -> Dict[str, str]:
    """Map ``str(pk)`` to the ``calculationId`` of the newest run it started.

    Records that never started a run are absent. One query whatever the number
    of records: one ``startswith`` per record, ORed, collapsed to one row per
    run before it leaves the database.
    """
    from lex.audit_logging.models.CalculationLog import CalculationLog

    prefixes = {str(pk): run_prefix(model_name, pk) for pk in pks}
    if not prefixes:
        return {}

    runs = (
        CalculationLog.objects.filter(
            reduce(or_, (Q(calculationId__startswith=p) for p in prefixes.values()))
        )
        .values("calculationId")
        .annotate(newest=Max("id"))
    )

    # Longest prefix first. For string keys containing "_", a run id can start
    # with two of the page's prefixes ("m_a_" and "m_a_b_"); the longer one is
    # the record that started it, because record "a"'s own runs begin
    # "m_a_update_". Integer keys cannot collide.
    by_length = sorted(prefixes.items(), key=lambda item: len(item[1]), reverse=True)
    best: Dict[str, Tuple[int, str]] = {}
    for run in runs:
        calculation_id = run["calculationId"]
        for pk, prefix in by_length:
            if calculation_id.startswith(prefix):
                if pk not in best or run["newest"] > best[pk][0]:
                    best[pk] = (run["newest"], calculation_id)
                break
    return {pk: calculation_id for pk, (_, calculation_id) in best.items()}


def annotate_latest_calculation(rows, model) -> None:
    """Set ``_lex_calculation_id`` and ``_has_calculation_log`` on each row.

    Never raises. A failure costs the log button, not the page: every row is
    marked as having no log and the grid still loads.
    """
    try:
        ids = latest_calculation_ids(model._meta.model_name, [row.pk for row in rows])
    except Exception:
        logger.warning(
            "Could not resolve the latest calculation runs for %s; "
            "serving the page without them.",
            model._meta.label,
            exc_info=True,
        )
        ids = {}
    for row in rows:
        calculation_id = ids.get(str(row.pk))
        row._lex_calculation_id = calculation_id
        row._has_calculation_log = calculation_id is not None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: the same command as Step 3.
Expected: 4 passed.

- [ ] **Step 6: Prove 15.41 discriminates**

In `latest_calculation_ids`, temporarily change `reverse=True` to `reverse=False`. Run the file. Expected: 15.41 fails and nothing else does. Restore `reverse=True` and re-run: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add lex/audit_logging/utils/latest_calculation.py lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py
git commit -F - <<'MSG'
feat(calculations): resolve each row's newest run in one query

A calculation row carries only is_calculated, so the grid cannot tell which
rows have a finished log to open. latest_calculation_ids resolves a whole
page in one query, by the rule the frontend's useResolvedCalculationId
already applies: the newest calculationId starting "<model>_<pk>_", by id.
One rule in two languages is what makes one row open one run everywhere.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

---

### Task 2: Declare the run fields on calculation serializers

**Files:**
- Modify: `lex/api/serializers/base_serializers.py`
- Test: `lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py`

**Interfaces:**
- Consumes: `is_calculation_model` (Task 1); instance attributes `_lex_calculation_id`, `_has_calculation_log` (Task 1).
- Produces: `CALCULATION_ID_NAME = "lex_reserved_calculation_id"`, `HAS_CALCULATION_LOG_NAME = "lex_reserved_has_calculation_log"`, `_calculation_run_fields(model) -> dict`; `LexSerializer.get_lex_reserved_calculation_id`, `LexSerializer.get_lex_reserved_has_calculation_log`.

- [ ] **Step 1: Write the failing test**

Append to `test_15i_latest_run_on_the_row.py`:

```python
class TestCluster15i_TheSerializer(_CalcLogTestCase):
    """Only calculation models grow the two fields."""

    def test_15_43_only_calculation_models_carry_the_run_fields(self):
        """Scenario 15.43: declared where they mean something, nowhere else.

        Every other model's rows would otherwise carry two permanent nulls — a
        payload cost on every list in the application, for nothing.
        """
        from lex.api.serializers.base_serializers import model2serializer

        calculation_fields = model2serializer(LogRootCalc)().fields
        other_fields = model2serializer(CalculationLog)().fields

        self.assertIn("lex_reserved_calculation_id", calculation_fields)
        self.assertIn("lex_reserved_has_calculation_log", calculation_fields)
        self.assertNotIn("lex_reserved_calculation_id", other_fields)
        self.assertNotIn("lex_reserved_has_calculation_log", other_fields)
```

- [ ] **Step 2: Run it to verify it fails**

Run the test file. Expected: 15.43 fails — `'lex_reserved_calculation_id' not found`.

- [ ] **Step 3: Add the constants and the field helper**

In `base_serializers.py`, replace:

```python
LEX_SCOPES_NAME = "lex_reserved_scopes"
```

with:

```python
LEX_SCOPES_NAME = "lex_reserved_scopes"
# The newest run a calculation row started, and whether it has one. Declared
# only on serializers of CalculationModel subclasses; see _calculation_run_fields.
CALCULATION_ID_NAME = "lex_reserved_calculation_id"
HAS_CALCULATION_LOG_NAME = "lex_reserved_has_calculation_log"
```

Add to the import block at the top, next to the existing `from lex.audit_logging.utils.content_types import safe_get_content_type`:

```python
from lex.audit_logging.utils.latest_calculation import is_calculation_model
```

Directly after the `_get_lexmodel_fields` function, add:

```python
def _calculation_run_fields(model) -> dict:
    """The two latest-run fields, for calculation models only.

    Values are read from attributes the list view sets for a whole page at
    once (``annotate_latest_calculation``). Nothing here queries per row.
    """
    if not is_calculation_model(model):
        return {}
    return {
        CALCULATION_ID_NAME: serializers.SerializerMethodField(),
        HAS_CALCULATION_LOG_NAME: serializers.SerializerMethodField(),
    }
```

- [ ] **Step 4: Add the getters and keep the fields past the visibility filter**

In `LexSerializer`, replace:

```python
    # ------------------------------------------------------------------
    # Scopes computation
    # ------------------------------------------------------------------
    def get_lex_reserved_scopes(self, instance):
```

with:

```python
    # ------------------------------------------------------------------
    # Latest calculation run (declared only for CalculationModel subclasses)
    # ------------------------------------------------------------------
    def get_lex_reserved_calculation_id(self, instance):
        """The newest run started from this row, when the list resolved it."""
        return getattr(instance, "_lex_calculation_id", None)

    def get_lex_reserved_has_calculation_log(self, instance):
        """Whether that run exists. Unannotated rows answer False, never a query."""
        return bool(getattr(instance, "_has_calculation_log", False))

    # ------------------------------------------------------------------
    # Scopes computation
    # ------------------------------------------------------------------
    def get_lex_reserved_scopes(self, instance):
```

`AuditLogSerializer` defines its own `get_lex_reserved_has_calculation_log`; as a subclass its version wins, so audit log behaviour is unchanged.

Replace:

```python
        'calculation_record', 'lex_reserved_scopes', 'id', 'id_field', SHORT_DESCR_NAME
    })
```

with:

```python
        'calculation_record', 'lex_reserved_scopes', 'id', 'id_field', SHORT_DESCR_NAME,
        # Without these two, to_representation's visibility filter strips the
        # run fields silently and the log button never appears.
        CALCULATION_ID_NAME, HAS_CALCULATION_LOG_NAME,
    })
```

- [ ] **Step 5: Declare the fields in both serializer factories**

In `model2serializer`, replace:

```python
    # ensure our internal fields are always present
    all_fields = list(fields) + [ID_FIELD_NAME, SHORT_DESCR_NAME, "id", LEX_SCOPES_NAME]

    return type(
        class_name,
        (RestApiModelSerializerTemplate,),
        {
            ID_FIELD_NAME: pk_alias,
            "Meta": type(
```

with:

```python
    # ensure our internal fields are always present
    run_fields = _calculation_run_fields(model)
    all_fields = list(fields) + [
        ID_FIELD_NAME, SHORT_DESCR_NAME, "id", LEX_SCOPES_NAME, *run_fields,
    ]

    return type(
        class_name,
        (RestApiModelSerializerTemplate,),
        {
            ID_FIELD_NAME: pk_alias,
            **run_fields,
            "Meta": type(
```

In `_wrap_custom_serializer`, replace:

```python
def _wrap_custom_serializer(custom_cls, model_class):
    meta = getattr(custom_cls, "Meta", type("Meta", (), {}))
```

with:

```python
def _wrap_custom_serializer(custom_cls, model_class):
    meta = getattr(custom_cls, "Meta", type("Meta", (), {}))
    run_fields = _calculation_run_fields(model_class)
```

Replace:

```python
        for extra in (ID_FIELD_NAME, SHORT_DESCR_NAME, "id", LEX_SCOPES_NAME):
```

with:

```python
        for extra in (ID_FIELD_NAME, SHORT_DESCR_NAME, "id", LEX_SCOPES_NAME, *run_fields):
```

Replace:

```python
        "get_short_description": lambda self, obj: str(obj),
        "Meta": NewMeta,
    }
```

with:

```python
        "get_short_description": lambda self, obj: str(obj),
        **run_fields,
        "Meta": NewMeta,
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run the test file. Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add lex/api/serializers/base_serializers.py lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py
git commit -F - <<'MSG'
feat(serializers): calculation rows can carry their latest run

Declares lex_reserved_calculation_id and lex_reserved_has_calculation_log
on serializers of CalculationModel subclasses only, in both factories, and
lists them in _SYSTEM_FIELDS — without which the visibility filter in
to_representation would strip them and the log button would never appear.
The getters read attributes set once per page; they never query per row.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

---

### Task 3: Annotate the grid page, pin the regression, record batch 15i, open the PR

**Files:**
- Modify: `lex/api/views/model_entries/List.py`
- Test: `lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py`
- Modify: `lex/test_project/test-plan/clusters/15-calculation_logging/allocation.yaml`, `batches.md`; regenerated `lex/test_project/test-plan/progress/dashboard.md`

**Interfaces:**
- Consumes: `annotate_latest_calculation`, `is_calculation_model` (Task 1); the serializer fields (Task 2).
- Produces: grid rows for calculation models carrying `lex_reserved_calculation_id: string | null` and `lex_reserved_has_calculation_log: boolean` — the contract Phase B reads.

- [ ] **Step 1: Write the failing tests**

Append to `test_15i_latest_run_on_the_row.py`:

```python
class TestCluster15i_TheGrid(_CalcLogTestCase):
    """What the grid endpoint actually returns — the contract the frontend reads."""

    def _post(self, **overrides):
        return self.client.post(
            self.url_list("logrootcalc"),
            data={"request": _ag(**overrides)},
            format="json",
        )

    def _row_data(self, resp, pk):
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return {row["id"]: row for row in resp.data["rowData"]}[pk]

    def test_15_44_a_finished_run_is_still_reachable_after_its_cache_is_gone(self):
        """Scenario 15.44: the regression, in one sentence.

        Completion purges the calculation's cache — which is what the live
        endpoint reads, and why the reported log "disappeared". The row must
        still name its run afterwards, and the run's rows must still exist.
        """
        from lex.audit_logging.utils.CacheManager import CacheManager

        row = _row("reported")
        calculation_id = _run("logrootcalc", row.pk, text="the log the user lost")
        CacheManager.cleanup_calculation(calculation_id=calculation_id)

        data = self._row_data(self._post(), row.pk)

        self.assertEqual(data["lex_reserved_calculation_id"], calculation_id)
        self.assertIs(data["lex_reserved_has_calculation_log"], True)
        self.assertTrue(CalculationLog.objects.filter(calculationId=calculation_id).exists())

    def test_15_44_a_row_that_never_ran_says_so(self):
        """Scenario 15.44 (second half): null and false, not absent."""
        row = _row("never-ran")

        data = self._row_data(self._post(), row.pk)

        self.assertIsNone(data["lex_reserved_calculation_id"])
        self.assertIs(data["lex_reserved_has_calculation_log"], False)

    def test_15_45_a_resolution_failure_still_serves_the_page(self):
        """Scenario 15.45: a missing door is recoverable; a grid that won't load is not."""
        row = _row("still-loads")
        _run("logrootcalc", row.pk)

        with mock.patch(
            "lex.audit_logging.utils.latest_calculation.latest_calculation_ids",
            side_effect=RuntimeError("database unavailable"),
        ):
            data = self._row_data(self._post(), row.pk)

        self.assertIsNone(data["lex_reserved_calculation_id"])
        self.assertIs(data["lex_reserved_has_calculation_log"], False)

    def test_15_46_the_prefix_lookup_is_served_by_an_index(self):
        """Scenario 15.46: the guard against someone removing ``db_index``.

        ``calculationId`` is ``db_index=True``; on PostgreSQL Django adds a
        ``text_pattern_ops`` companion, which is what makes ``LIKE 'prefix%'``
        an index probe. Without it, every page load of every calculation table
        scans an append-only log table.
        """
        if connection.vendor != "postgresql":
            self.skipTest("the pattern-ops companion index is PostgreSQL-specific")

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = %s",
                [CalculationLog._meta.db_table],
            )
            definitions = [row[0] for row in cursor.fetchall()]

        self.assertTrue(
            any('"calculationId"' in d and "pattern_ops" in d for d in definitions),
            f"no pattern-ops index on calculationId; indexes: {definitions}",
        )
```

- [ ] **Step 2: Run them to verify they fail**

Run the test file. Expected: 15.44 (both) and 15.45 fail — `KeyError: 'lex_reserved_calculation_id'` or `None != '<id>'`, because nothing annotates the page yet. 15.46 passes already: it guards an existing index, it does not test new code.

- [ ] **Step 3: Annotate the leaf page**

In `List.py`, at the end of `_execute_leaf_level`, replace this block (it appears once):

```python
        row_count = qs.count()
        page_qs = qs[start_row:end_row]
        serializer = self.get_serializer(page_qs, many=True)
        return {
            "rowData": serializer.data,
            "rowCount": row_count,
        }
```

with:

```python
        row_count = qs.count()
        page_qs = qs[start_row:end_row]

        from lex.audit_logging.utils.latest_calculation import (
            annotate_latest_calculation,
            is_calculation_model,
        )

        if is_calculation_model(self._ag_model_class):
            # A calculation row carries only `is_calculated`; the grid needs to
            # know which rows have a finished log to open. Resolved for the
            # whole page in one query and set on the instances the serializer
            # reads — the same shape as the audit log branch above.
            page_rows = list(page_qs)
            annotate_latest_calculation(page_rows, self._ag_model_class)
            serializer = self.get_serializer(page_rows, many=True)
        else:
            serializer = self.get_serializer(page_qs, many=True)
        return {
            "rowData": serializer.data,
            "rowCount": row_count,
        }
```

The local import follows `_annotate_has_calculation_log` in the same file, which imports `CalculationLog` inside the method.

- [ ] **Step 4: Run the tests to verify they pass**

Run the test file. Expected: 9 passed (1 skipped instead of passed on a non-PostgreSQL database).

- [ ] **Step 5: Prove 15.44 discriminates**

Temporarily revert Step 3 (`git stash push -- lex/api/views/model_entries/List.py`). Run the file: 15.44 and 15.45 fail. `git stash pop`, run again: 9 passed.

- [ ] **Step 6: Compare the cluster against the baseline**

Run the whole cluster-15 command. Expected: the baseline's failures unchanged, passed count up by exactly 9.

- [ ] **Step 7: Record batch 15i**

In `lex/test_project/test-plan/clusters/15-calculation_logging/allocation.yaml`, change `max_scenario: 38` to `max_scenario: 46`, and append to the `letters:` map:

```yaml
  i:
    title: A calculation row learns its latest run
    scenarios: 15.39-15.46
    status: complete
    tests:
      pass: 9
      skip: 0
      xfail: 0
    note: >-
      Reported as "once they are done, we lose the logs and we would have to do a lot of steps to get
      them." Literally true: the live log is served from cache and the root calculation purges that
      cache on completion (CacheManager.cleanup_calculation), so a finished run had no path back from
      the row that started it. The durable copy was always in the CalculationLog table; what was
      missing was the row knowing which run to open. The grid endpoint now annotates calculation rows
      with lex_reserved_calculation_id / lex_reserved_has_calculation_log, one query per page, by the
      rule the frontend's useResolvedCalculationId already applies - newest calculationId starting
      "<model>_<pk>_", by id - so one row opens one run in the table and in every widget. 15.44 is the
      regression; 15.42 is the guard against it becoming N+1; 15.46 guards the index that makes the
      prefix match a probe rather than a scan. No migration: calculationId has been db_index=True since
      0005.
```

Append to `batches.md`:

```markdown
## Batch 15i — a calculation row learns its latest run (15.39-15.46)

| | |
| --- | --- |
| Scenario range | 15.39 – 15.46 |
| Type | U + API |
| Files covered | `lex/audit_logging/utils/latest_calculation.py` (new), `lex/api/serializers/base_serializers.py` (`_calculation_run_fields`, `_SYSTEM_FIELDS`, both factories), `lex/api/views/model_entries/List.py` (`_execute_leaf_level`) |
| Test file | `lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py` |
| Tests landed | **9 pass / 0 fail** |
| Status | ✅ Complete |
| Paired with | PAC batches `7f` and `12m` — the status cell's log button and the drawer that read these two fields |

**The rule is stated twice, deliberately.** `useResolvedCalculationId` resolves a record's run in the
browser; this resolves a whole page on the server. Both use the newest `calculationId` starting
`<model>_<pk>_`, by `id`. A second rule — the spec's first draft proposed the generic foreign key —
would let one row open different runs in the table and in a widget.

**15.41 exists because the obvious implementation is wrong for string keys.** A run id can start
with two of a page's prefixes; only the longest is the record that started it. Verified as a guard:
matching shortest-first fails 15.41 and nothing else.
```

Then regenerate and validate:

```bash
/home/syscall/Documents/lex/.venv-test/bin/python .github/scripts/test_plan_aggregates.py build
/home/syscall/Documents/lex/.venv-test/bin/python .github/scripts/test_plan_aggregates.py validate | tail -1
```

Expected: `validate` reports the same number of problems as before this change (take that number with `git stash` if unsure) and none mentioning `15i` or `15.39`–`15.46`.

- [ ] **Step 8: Commit and open the PR**

```bash
git add lex/api/views/model_entries/List.py lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py lex/test_project/test-plan/
git commit -F - <<'MSG'
feat(grid): a calculation row names the run its log is in

Reported as "once they are done, we lose the logs". The live log is
cache-backed and the root calculation purges that cache on completion, so a
finished run had no path back from its row. The grid's leaf page now
annotates calculation rows with their newest run, in one query, and
serializes the annotated instances — the shape the audit log branch above
it already uses. A failure to resolve costs the log button, never the page.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
git push git@github.com:ExcellenceCloudGmbH/lex-app.git HEAD:feat/calculation-log-drawer
gh pr create --repo ExcellenceCloudGmbH/lex-app --base lex-app-v2 --head feat/calculation-log-drawer \
  --title "feat(grid): a calculation row names the run its log is in"
```

Write the PR body from the spec's *The problem* and *The data* sections and this task's baseline comparison, ending with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

## Phase B — process-admin-general-client

### Task 4: Split `SideDrawer` out of `FormDrawer`

**Files:**
- Create: `src/components/SideDrawer/SideDrawer.tsx`
- Modify: `src/components/model-components/forms/FormDrawer.tsx`
- Create: `src/components/SideDrawer/__test__/SideDrawer.test.tsx`

**Interfaces:**
- Produces:
  - `export default function SideDrawer(props: SideDrawerProps): JSX.Element`
  - `export interface SideDrawerProps { open: boolean; onClose: () => void; icon: ReactNode; title: ReactNode; idPill?: string | null; closeLabel?: string; width?: string; expandStorageKey?: string; bodySx?: SxProps<Theme>; children: ReactNode }` — a toggle is offered exactly when `expandStorageKey` is given.
  - `export const FORM_DRAWER_WIDTH = 'min(640px, 90vw)'`, `export const EXPANDED_WIDTH = '100vw'`
  - `export function readExpanded(key: string | undefined): boolean`, `export function writeExpanded(key: string | undefined, expanded: boolean): void`
  - DOM: a root element `data-testid="side-drawer"` with `data-expanded="true" | "false"`; toggle buttons named `Expand to full width` / `Collapse to side panel`.

- [ ] **Step 1: Create the worktree and take the baselines**

```bash
cd /home/syscall/LUND_IT/process-admin-general-client && git fetch origin lex-app-v2-pac-latest
git worktree add /home/syscall/LUND_IT/pac_logdrawer -b feat/calculation-log-drawer origin/lex-app-v2-pac-latest
ln -sfn /home/syscall/LUND_IT/process-admin-general-client/node_modules /home/syscall/LUND_IT/pac_logdrawer/node_modules
cd /home/syscall/LUND_IT/pac_logdrawer
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep -c 'error TS'
./node_modules/.bin/vitest run 2>&1 | grep -E '^ Test Files|^      Tests'
```

Record both numbers. The borrowed install produces a large fixed number of failures unrelated to this work; only the delta is evidence.

- [ ] **Step 2: Write the failing tests**

Create `src/components/SideDrawer/__test__/SideDrawer.test.tsx`:

```tsx
/**
 * F12.73–F12.74 — the drawer shell shared by forms and the calculation log.
 *
 * Logs carry tables and code laid out for a wide dialog, so the log drawer can
 * expand to the full viewport. Someone who reads logs all day should not
 * re-expand on every row, so the choice is remembered — and a browser that
 * refuses storage must cost the memory, never the drawer.
 *
 * @group unit
 */
import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SideDrawer, { EXPANDED_WIDTH, readExpanded, writeExpanded } from '../SideDrawer'

const KEY = 'test.sideDrawer.expanded'

function renderDrawer(props: Partial<React.ComponentProps<typeof SideDrawer>> = {}) {
  return render(
    <SideDrawer
      open
      onClose={() => {}}
      icon={<span />}
      title='Calculation log'
      idPill='orders #42'
      expandStorageKey={KEY}
      {...props}
    >
      <div>body</div>
    </SideDrawer>,
  )
}

afterEach(() => {
  window.localStorage.clear()
  vi.restoreAllMocks()
})

describe('F12.73–F12.74 — SideDrawer', () => {
  it('F12.73 the toggle takes it to the full viewport width and back', async () => {
    const user = userEvent.setup()
    renderDrawer()
    const root = screen.getByTestId('side-drawer')
    expect(root).toHaveAttribute('data-expanded', 'false')

    await user.click(screen.getByRole('button', { name: 'Expand to full width' }))
    expect(root).toHaveAttribute('data-expanded', 'true')
    const paper = document.querySelector('.MuiDrawer-paper') as HTMLElement
    expect(window.getComputedStyle(paper).width).toBe(EXPANDED_WIDTH)

    await user.click(screen.getByRole('button', { name: 'Collapse to side panel' }))
    expect(root).toHaveAttribute('data-expanded', 'false')
  })

  it('F12.73 a drawer without a storage key offers no toggle', () => {
    renderDrawer({ expandStorageKey: undefined })
    expect(screen.queryByRole('button', { name: 'Expand to full width' })).not.toBeInTheDocument()
  })

  it('F12.74 the choice survives a remount', async () => {
    const user = userEvent.setup()
    const first = renderDrawer()
    await user.click(screen.getByRole('button', { name: 'Expand to full width' }))
    first.unmount()

    renderDrawer()
    expect(screen.getByTestId('side-drawer')).toHaveAttribute('data-expanded', 'true')
  })

  it('F12.74 storage that throws costs the memory, not the drawer', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })

    expect(readExpanded(KEY)).toBe(false)
    expect(() => writeExpanded(KEY, true)).not.toThrow()
    renderDrawer()
    expect(screen.getByTestId('side-drawer')).toHaveAttribute('data-expanded', 'false')
  })
})
```

- [ ] **Step 3: Run them to verify they fail**

Run: `./node_modules/.bin/vitest run src/components/SideDrawer`
Expected: FAIL — `Failed to resolve import "../SideDrawer"`.

- [ ] **Step 4: Write `SideDrawer`**

Create `src/components/SideDrawer/SideDrawer.tsx`:

```tsx
import React, { JSX, ReactNode, useState } from 'react'
import { Box, Drawer, IconButton, Tooltip, Typography, useMediaQuery, useTheme } from '@mui/material'
import type { SxProps, Theme } from '@mui/material/styles'
import CloseIcon from '@mui/icons-material/Close'
import OpenInFullIcon from '@mui/icons-material/OpenInFull'
import CloseFullscreenIcon from '@mui/icons-material/CloseFullscreen'

/** The width a form drawer has always had. */
export const FORM_DRAWER_WIDTH = 'min(640px, 90vw)'
/** What "expanded" means: the whole viewport. */
export const EXPANDED_WIDTH = '100vw'

export interface SideDrawerProps {
  open: boolean
  onClose: () => void
  /** Shown at the head of the strip, left of the title. */
  icon: ReactNode
  title: ReactNode
  /** The record the drawer is about, as a monospace pill. */
  idPill?: string | null
  /** Accessible name for the close control; drawers say which one they are. */
  closeLabel?: string
  /** Width when not expanded. */
  width?: string
  /**
   * Remembers the full-width choice under this `localStorage` key. A toggle is
   * offered exactly when a key is given — a choice that cannot be remembered is
   * not offered as one.
   */
  expandStorageKey?: string
  /** Extra styles for the body, e.g. the form cascade `FormDrawer` applies. */
  bodySx?: SxProps<Theme>
  children: ReactNode
}

export function readExpanded(key: string | undefined): boolean {
  if (!key) return false
  try {
    return window.localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

export function writeExpanded(key: string | undefined, expanded: boolean): void {
  if (!key) return
  try {
    window.localStorage.setItem(key, expanded ? '1' : '0')
  } catch {
    // Private mode or blocked site data: the choice lasts for this drawer only.
  }
}

/**
 * The right-hand panel, once.
 *
 * Split out of `FormDrawer`, which was mostly this plus a react-admin `sx`
 * cascade that only makes sense around an Edit or Create card. The log drawer
 * needs the panel and none of the cascade.
 */
export default function SideDrawer({
  open,
  onClose,
  icon,
  title,
  idPill,
  closeLabel = 'Close drawer',
  width = FORM_DRAWER_WIDTH,
  expandStorageKey,
  bodySx,
  children,
}: SideDrawerProps): JSX.Element {
  const theme = useTheme()
  const isSmall = useMediaQuery(theme.breakpoints.down('sm'))
  const isDark = theme.palette.mode === 'dark'
  // Same chrome tone the grid header uses, so the drawer reads as part of the
  // table surface rather than a foreign dialog.
  const chromeColor = isDark ? '#213345' : '#e9eef4'
  const expandable = expandStorageKey !== undefined
  const [expanded, setExpanded] = useState(() => readExpanded(expandStorageKey))

  const toggleExpanded = (): void => {
    const next = !expanded
    setExpanded(next)
    writeExpanded(expandStorageKey, next)
  }

  const paperWidth = isSmall ? '100%' : expanded ? EXPANDED_WIDTH : width
  const expandLabel = expanded ? 'Collapse to side panel' : 'Expand to full width'

  return (
    <Drawer
      anchor='right'
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: paperWidth, display: 'flex', flexDirection: 'column' } }}
    >
      <Box
        data-testid='side-drawer'
        data-expanded={expanded ? 'true' : 'false'}
        sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            px: 2,
            py: 1.25,
            borderBottom: '1px solid',
            borderColor: 'divider',
            bgcolor: chromeColor,
            flexShrink: 0,
          }}
        >
          {icon}
          <Typography sx={{ fontWeight: 600, fontSize: '0.95rem', textTransform: 'capitalize' }}>
            {title}
          </Typography>
          {idPill != null && (
            <Typography
              component='span'
              title={idPill}
              sx={{
                color: 'secondary.dark',
                backgroundColor: 'rgba(20,180,180,0.10)',
                px: 0.9,
                py: 0.2,
                borderRadius: '8px',
                fontSize: '0.75rem',
                fontFamily: '"Fira Code", "Roboto Mono", "Consolas", monospace',
                maxWidth: 220,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {idPill}
            </Typography>
          )}
          <Box sx={{ flex: 1 }} />
          {expandable && !isSmall && (
            <Tooltip title={expandLabel}>
              <IconButton
                aria-label={expandLabel}
                aria-pressed={expanded}
                onClick={toggleExpanded}
                size='small'
              >
                {expanded ? (
                  <CloseFullscreenIcon fontSize='small' />
                ) : (
                  <OpenInFullIcon fontSize='small' />
                )}
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title='Close'>
            <IconButton aria-label={closeLabel} onClick={onClose} size='small'>
              <CloseIcon fontSize='small' />
            </IconButton>
          </Tooltip>
        </Box>

        <Box
          sx={[
            { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' },
            ...(Array.isArray(bodySx) ? bodySx : [bodySx]),
          ]}
        >
          {children}
        </Box>
      </Box>
    </Drawer>
  )
}
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `./node_modules/.bin/vitest run src/components/SideDrawer`
Expected: 4 passed.

- [ ] **Step 6: Rebuild `FormDrawer` on it**

Replace the whole of `src/components/model-components/forms/FormDrawer.tsx` with the following. `FormDrawerProps` and the docstring are carried over word for word; the docstring gains one closing paragraph.

```tsx
import React, { JSX, ReactNode } from 'react'
import type { SxProps, Theme } from '@mui/material/styles'
import SideDrawer, { FORM_DRAWER_WIDTH } from '../../SideDrawer/SideDrawer'

export interface FormDrawerProps {
  open: boolean
  onClose: () => void
  /** Shown at the head of the strip, left of the title. */
  icon: ReactNode
  /** e.g. "Edit quarter" / "New quarter". */
  title: ReactNode
  /** The record id, for edit. Create has none, so it is optional. */
  idPill?: string | null
  /** Accessible name for the close control; drawers say which one they are. */
  closeLabel?: string
  children: ReactNode
}

/** Both class families, so one cascade serves Edit and Create. */
const cardChrome = (view: 'Edit' | 'Create') => ({
  [`& .${view.toLowerCase()}-page`]: {
    flex: 1,
    minHeight: 0,
    display: 'flex',
    flexDirection: 'column',
  },
  [`& .Ra${view}-main`]: {
    m: 0,
    mt: 0,
    flex: 1,
    minHeight: 0,
    display: 'flex',
    flexDirection: 'column',
  },
  [`& .Ra${view}-noActions`]: { mt: 0 },
  [`& .Ra${view}-card`]: {
    border: 'none',
    boxShadow: 'none',
    borderRadius: 0,
    bgcolor: 'transparent',
    transform: 'none !important',
    flex: 1,
    minHeight: 0,
    width: '100%',
    display: 'flex',
    flexDirection: 'column',
  },
  [`& .Ra${view}-card > form`]: {
    flex: 1,
    minHeight: 0,
    display: 'flex',
    flexDirection: 'column',
  },
  [`& .Ra${view}-card > form > .MuiCardContent-root`]: {
    flex: 1,
    minHeight: 0,
    overflowY: 'auto',
    px: 2.5,
    py: 2,
  },
})

/** The raw form renders react-admin's card and toolbar; inside the panel that
 *  chrome is redundant, so it is flattened and the save toolbar pinned to the
 *  bottom of the drawer. */
const FORM_BODY_SX: SxProps<Theme> = {
  ...cardChrome('Edit'),
  ...cardChrome('Create'),
  '& .MuiToolbar-root': {
    flexShrink: 0,
    borderTop: '1px solid',
    borderColor: 'divider',
    backgroundColor: 'background.paper',
  },
  '& .MuiToolbar-root button[type="submit"]': {
    backgroundColor: '#0d9e9e',
    color: '#fff',
    boxShadow: 'none',
    textTransform: 'none',
    fontWeight: 600,
    '&:hover': { backgroundColor: '#0b8a8a', boxShadow: 'none' },
  },
}

/**
 * The chrome a form drawer needs, once.
 *
 * Extracted from `EditDrawer`, most of which was this: the panel, the header
 * strip, and an intricate `sx` cascade that flattens react-admin's nested card
 * so the drawer IS the card and the save toolbar pins to the bottom. Copying
 * that into a second drawer would leave two versions of a fiddly cascade that
 * must agree forever — the kind of duplication that does not announce when it
 * diverges, because both halves keep rendering something plausible.
 *
 * THE NON-OBVIOUS PART: react-admin names these classes per view, so the shell
 * has to target both families. Verified against the installed
 * `ra-ui-materialui`:
 *
 *   Edit                | Create
 *   .edit-page          | .create-page
 *   .RaEdit-main        | .RaCreate-main
 *   .RaEdit-noActions   | .RaCreate-noActions
 *   .RaEdit-card        | .RaCreate-card
 *
 * The structure is otherwise identical, so one selector list covering both is
 * correct — this is not two divergent layouts pretending to be one.
 *
 * The panel and header now live in `SideDrawer`; what stays here is the
 * cascade, which only means something around a react-admin card.
 */
export default function FormDrawer({
  open,
  onClose,
  icon,
  title,
  idPill,
  closeLabel = 'Close drawer',
  children,
}: FormDrawerProps): JSX.Element {
  return (
    <SideDrawer
      open={open}
      onClose={onClose}
      icon={icon}
      title={title}
      idPill={idPill}
      closeLabel={closeLabel}
      width={FORM_DRAWER_WIDTH}
      bodySx={FORM_BODY_SX}
    >
      {children}
    </SideDrawer>
  )
}
```

- [ ] **Step 7: Run the form drawer's baseline — the gate for this task**

Run: `./node_modules/.bin/vitest run src/components/model-components/forms src/components/SideDrawer`
Expected: `EditDrawer.cluster.test.tsx` F5.60–F5.63 pass unchanged, plus the 4 new tests. Do not edit the F5 tests: the split must be invisible to `FormDrawer`'s callers.

- [ ] **Step 8: Format and commit**

```bash
./node_modules/.bin/prettier --write src/components/SideDrawer/SideDrawer.tsx src/components/SideDrawer/__test__/SideDrawer.test.tsx src/components/model-components/forms/FormDrawer.tsx
git add src/components/SideDrawer src/components/model-components/forms/FormDrawer.tsx
git commit -F - <<'MSG'
refactor(drawer): split the panel out of FormDrawer as SideDrawer

FormDrawer was mostly a right-hand panel plus a react-admin sx cascade that
only makes sense around an Edit or Create card. The calculation log drawer
needs the panel and none of the cascade, so the panel becomes SideDrawer and
FormDrawer becomes SideDrawer plus the cascade. Its props and callers are
unchanged and F5.60-F5.63 pass untouched.

SideDrawer can also expand to the full viewport width, remembered in
localStorage. A browser that refuses storage costs the memory, never the
drawer.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

---

### Task 5: What the drawer says before the log

**Files:**
- Create: `src/components/model-components/CalculateFunctionality/calculationRunHeadline.ts`
- Create: `src/components/model-components/CalculateFunctionality/__test__/calculationRunHeadline.test.ts`

**Interfaces:**
- Consumes: `CalculationStatusKind` from `./calculationStatus` (`'in_progress' | 'success' | 'error' | 'aborted' | 'cancelled' | 'idle'`).
- Produces:
  - `export interface RunHeadline { tone: 'error' | 'warning' | 'neutral'; title: string; note?: string; detail?: string }`
  - `export function recordedFailure(record: Record<string, unknown> | undefined): string | null`
  - `export function headlineFor(status: CalculationStatusKind, record?: Record<string, unknown>): RunHeadline | null`

- [ ] **Step 1: Write the failing tests**

Create `src/components/model-components/CalculateFunctionality/__test__/calculationRunHeadline.test.ts`:

```ts
/**
 * F12.75 — what the log drawer says before the log.
 *
 * The only durable traceback of a failed calculation is
 * `calculation_error_message` (then `error_message`), and only on models that
 * declare it: `update_calculation_status` broadcasts the stack trace over the
 * websocket and persists nothing. So the headline shows it where it exists and
 * says so where it does not — an empty panel reads as broken.
 *
 * @group unit
 */
import { describe, expect, it } from 'vitest'
import { headlineFor, recordedFailure } from '../calculationRunHeadline'

const FAILURE =
  'ValueError: quarter has no report date\n\nTraceback (most recent call last):\n  File "calc.py", line 12, in calculate'

describe('F12.75 — the run headline', () => {
  it('leads ERROR with the failure the model recorded', () => {
    expect(headlineFor('error', { calculation_error_message: FAILURE })).toEqual({
      tone: 'error',
      title: 'ValueError: quarter has no report date',
      detail: FAILURE,
    })
  })

  it('falls back to error_message, the framework’s second choice', () => {
    expect(headlineFor('error', { error_message: FAILURE })?.detail).toBe(FAILURE)
  })

  it('prefers calculation_error_message when a model has both', () => {
    expect(headlineFor('error', { calculation_error_message: 'A', error_message: 'B' })?.title).toBe(
      'A',
    )
  })

  it('says so when the model records no failure details', () => {
    const headline = headlineFor('error', {})
    expect(headline?.title).toBe('This calculation failed')
    expect(headline?.detail).toBeUndefined()
    expect(headline?.note).toMatch(/does not record failure details/)
  })

  it('treats a blank failure field as none', () => {
    expect(headlineFor('error', { calculation_error_message: '   ' })?.title).toBe(
      'This calculation failed',
    )
  })

  it('explains that ABORTED has no traceback', () => {
    expect(headlineFor('aborted', {})).toMatchObject({
      tone: 'warning',
      title: 'The worker stopped before this calculation finished',
    })
  })

  it('does not dress CANCELLED as an error', () => {
    expect(headlineFor('cancelled', {})).toMatchObject({ tone: 'neutral', title: 'Stopped by a person' })
  })

  it.each(['success', 'in_progress', 'idle'] as const)('says nothing for %s', (status) => {
    expect(headlineFor(status, { calculation_error_message: FAILURE })).toBeNull()
  })

  it('ignores a failure field that is not text', () => {
    expect(recordedFailure({ calculation_error_message: 42 })).toBeNull()
  })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `./node_modules/.bin/vitest run src/components/model-components/CalculateFunctionality/__test__/calculationRunHeadline.test.ts`
Expected: FAIL — `Failed to resolve import "../calculationRunHeadline"`.

- [ ] **Step 3: Write the module**

Create `src/components/model-components/CalculateFunctionality/calculationRunHeadline.ts`:

```ts
import type { CalculationStatusKind } from './calculationStatus'

/** What the log drawer says before the log. */
export interface RunHeadline {
  tone: 'error' | 'warning' | 'neutral'
  title: string
  /** A plain-language explanation, when there is something the reader should know. */
  note?: string
  /** The recorded failure in full — exception and stack trace — shown as code. */
  detail?: string
}

/**
 * The failure a model recorded, in the framework's own order of preference.
 *
 * `CalculationModel` writes `f"{exception_details}\n\n{stack_trace}"` to
 * `calculation_error_message` if the model declares it, otherwise to
 * `error_message`. Nothing else persists it — the websocket broadcast of the
 * stack trace is gone once no one is listening — so on a model with neither
 * field there is no traceback anywhere to show.
 */
export function recordedFailure(record: Record<string, unknown> | undefined): string | null {
  for (const key of ['calculation_error_message', 'error_message']) {
    const value = record?.[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

export function headlineFor(
  status: CalculationStatusKind,
  record?: Record<string, unknown>,
): RunHeadline | null {
  switch (status) {
    case 'error': {
      const failure = recordedFailure(record)
      if (failure) {
        return { tone: 'error', title: failure.split('\n')[0], detail: failure }
      }
      return {
        tone: 'error',
        title: 'This calculation failed',
        note: 'This model does not record failure details, so there is no traceback to show.',
      }
    }
    case 'aborted':
      return {
        tone: 'warning',
        title: 'The worker stopped before this calculation finished',
        note: 'Nothing raised an error, so there is no traceback. The log below ends where the run stopped.',
      }
    case 'cancelled':
      return {
        tone: 'neutral',
        title: 'Stopped by a person',
        note: 'The log below ends where it was cancelled.',
      }
    default:
      return null
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the Step 2 command. Expected: 11 passed.

- [ ] **Step 5: Format and commit**

```bash
./node_modules/.bin/prettier --write src/components/model-components/CalculateFunctionality/calculationRunHeadline.ts src/components/model-components/CalculateFunctionality/__test__/calculationRunHeadline.test.ts
git add src/components/model-components/CalculateFunctionality/calculationRunHeadline.ts src/components/model-components/CalculateFunctionality/__test__/calculationRunHeadline.test.ts
git commit -F - <<'MSG'
feat(calculation-log): say what happened before showing the log

A failed calculation's only durable traceback is calculation_error_message,
then error_message, and only on models that declare one: the stack trace is
otherwise broadcast over the websocket and lost. The headline leads ERROR
with it where it exists and says so plainly where it does not, explains that
ABORTED never has one, and does not dress CANCELLED as an error.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

---

### Task 6: `CalculationLogTree` can hide its title bar, and says when it failed to load

**Files:**
- Modify: `src/pages/CalculationLogTree/CalculationLogTree.tsx`
- Test: `src/pages/CalculationLogTree/__test__/CalculationLogTree.test.tsx`

**Interfaces:**
- Produces: `CalculationLogTreeProps.showTitle?: boolean` (default `true`); when the tree fetch fails, an error `Alert` reading `This log could not be loaded.` with a `Retry` button.

The spec requires that a log which fails to load shows an error inside the drawer, never a blank panel. Today the tree destructures only `{ data, isPending }` from `useGetTree`, so a failed fetch renders an empty tree. `useGetTree` returns a full react-query result (`UseQueryResult<…, Error>`), so `error` and `refetch` are available and typed.

- [ ] **Step 1: Write the failing tests**

Add inside the existing top-level `describe` block of `CalculationLogTree.test.tsx`, using its existing mocks:

```tsx
  it('F12.76 inside the log drawer it renders no title bar of its own', () => {
    // The drawer's header already names what this is; two "Calculation Log"
    // bars stacked is the seam this work exists to remove.
    render(<CalculationLogTree calculationId='orders_42_update_abc' embedded showTitle={false} />)

    expect(screen.queryByText('Calculation Log')).not.toBeInTheDocument()
    expect(screen.queryByText('orders_42_update_abc')).not.toBeInTheDocument()
  })

  it('F12.76 it keeps its title bar by default', () => {
    render(<CalculationLogTree calculationId='orders_42_update_abc' embedded />)

    expect(screen.getByText('Calculation Log')).toBeInTheDocument()
  })

  it('F12.76 a log that fails to load says so and offers a retry — never an empty panel', () => {
    mockTreeError = new Error('503 Service Unavailable')
    try {
      render(<CalculationLogTree calculationId='orders_42_update_abc' embedded showTitle={false} />)

      expect(screen.getByText(/This log could not be loaded/)).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
      expect(mockRefetch).toHaveBeenCalled()
    } finally {
      mockTreeError = null
      mockRefetch.mockClear()
    }
  })
```

Make the file's `useGetTree` mock able to fail. Directly after `let mockIsPending = false`, add:

```tsx
let mockTreeError: Error | null = null
const mockRefetch = vi.fn()
```

and in the `@react-admin/ra-tree` mock, replace:

```tsx
  useGetTree: (_resource: string, _options: any) => ({
    data: mockTreeData,
    isPending: mockIsPending,
  }),
```

with:

```tsx
  useGetTree: (_resource: string, _options: any) => ({
    data: mockTreeData,
    isPending: mockIsPending,
    error: mockTreeError,
    refetch: mockRefetch,
  }),
```

The factory reads these variables when `useGetTree` is *called*, not when the mock is hoisted — the same way it already reads `mockTreeData` — so they are initialised by then.

- [ ] **Step 2: Run them to verify they fail**

Run: `./node_modules/.bin/vitest run src/pages/CalculationLogTree`
Expected: the `showTitle={false}` test fails (the title renders) and the failed-load test fails (no error text). TypeScript also reports `Property 'showTitle' does not exist` under `tsc`.

- [ ] **Step 3: Add the prop and the error state**

Replace the MUI import:

```tsx
import { Box, IconButton, Tab, Tabs, Tooltip, Typography } from '@mui/material'
```

with:

```tsx
import { Alert, Box, Button, IconButton, Tab, Tabs, Tooltip, Typography } from '@mui/material'
```

Replace:

```tsx
  const { data, isPending } = useGetTree('calculationlog', {
```

with:

```tsx
  const { data, isPending, error, refetch } = useGetTree('calculationlog', {
```

In `CalculationLogTreeProps`, replace:

```tsx
  /** Hide page chrome (the Back control) that has no meaning inside a widget. */
  embedded?: boolean
}
```

with:

```tsx
  /** Hide page chrome (the Back control) that has no meaning inside a widget. */
  embedded?: boolean
  /** Render the title bar. The log drawer passes `false`: its own header names it. */
  showTitle?: boolean
}
```

In the component's parameter list, replace:

```tsx
  embedded = false,
}: CalculationLogTreeProps = {}): JSX.Element {
```

with:

```tsx
  embedded = false,
  showTitle = true,
}: CalculationLogTreeProps = {}): JSX.Element {
```

Wrap the page bar. Replace:

```tsx
      {/* Page bar — frameless context strip matching the list pages:
       * back navigation + title on the left, calculation id as a teal pill. */}
      <Box
```

with:

```tsx
      {/* Page bar — frameless context strip matching the list pages:
       * back navigation + title on the left, calculation id as a teal pill.
       * Hidden inside the log drawer, whose own header already names it. */}
      {showTitle && (
      <Box
```

and replace the page bar's closing, which is the first occurrence of:

```tsx
            {calculationId}
          </Typography>
        )}
      </Box>
```

with:

```tsx
            {calculationId}
          </Typography>
        )}
      </Box>
      )}

      {/* A failed fetch used to render an empty tree — indistinguishable from
          a run that logged nothing. Inside the drawer that reads as broken. */}
      {error && (
        <Alert
          severity='error'
          sx={{ mb: 1.25 }}
          action={
            <Button color='inherit' size='small' onClick={() => void refetch()}>
              Retry
            </Button>
          }
        >
          This log could not be loaded. {error.message}
        </Alert>
      )}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./node_modules/.bin/vitest run src/pages/CalculationLogTree`
Expected: every existing test plus the 3 new ones pass.

- [ ] **Step 5: Format and commit**

```bash
./node_modules/.bin/prettier --write src/pages/CalculationLogTree/CalculationLogTree.tsx src/pages/CalculationLogTree/__test__/CalculationLogTree.test.tsx
git add src/pages/CalculationLogTree
git commit -F - <<'MSG'
feat(calculation-log): the tree can leave its title to its host

showTitle (default true) lets the log drawer, whose header already names
the log, host the tree without a second "Calculation Log" bar stacked under
its own. The routed page is unchanged.

A failed tree fetch now says so, with a retry. It used to render an empty
tree, indistinguishable from a run that logged nothing.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

---

### Task 7: `CalculationLogDrawer`

**Files:**
- Create: `src/components/model-components/CalculateFunctionality/CalculationLogDrawer.tsx`
- Create: `src/components/model-components/CalculateFunctionality/__test__/CalculationLogDrawer.test.tsx`

**Interfaces:**
- Consumes: `SideDrawer` (Task 4), `headlineFor` / `RunHeadline` (Task 5), `CalculationLogTree` with `showTitle` (Task 6); existing `CalculationLogStream` (`src/components/widgets/CalculationLogStream.tsx`, props `{ model, pk, height?, calculationId? }`), existing `useResolvedCalculationId(model, pk, record?) => { calculationId: string | null; isResolving: boolean }` (`src/components/widgets/useResolvedCalculationId.ts`).
- Produces:
  - `export default function CalculationLogDrawer(props: CalculationLogDrawerProps): JSX.Element`
  - `export interface CalculationLogDrawerProps { open: boolean; onClose: () => void; model: string; pk: string | number; record: Record<string, unknown>; status: CalculationStatusKind }`
  - `export const LOG_DRAWER_WIDTH = 'min(1000px, 92vw)'`, `export const LOG_DRAWER_EXPAND_KEY = 'lex.calculationLogDrawer.expanded'`
  - DOM: headline `data-testid="run-headline"` with `data-tone`.

- [ ] **Step 1: Write the failing tests**

Create `src/components/model-components/CalculateFunctionality/__test__/CalculationLogDrawer.test.tsx`:

```tsx
/**
 * F12.77–F12.78 — the calculation log, opened from the row.
 *
 * While a run is live the drawer streams it; once it finishes, the drawer shows
 * the durable tree for the same run. The id comes from useResolvedCalculationId,
 * which keeps what it resolved across the moment a run completes — the live
 * entry is gone by then, and the grid may not have refetched the row yet.
 *
 * @group unit
 */
import React from 'react'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CalculationLogDrawer from '../CalculationLogDrawer'

const { resolver } = vi.hoisted(() => ({ resolver: vi.fn() }))

vi.mock('../../../widgets/useResolvedCalculationId', () => ({
  useResolvedCalculationId: (...args: unknown[]) => resolver(...args),
}))
vi.mock('../../../widgets/CalculationLogStream', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid='log-stream' data-id={p.calculationId} />,
}))
vi.mock('../../../../pages/CalculationLogTree/CalculationLogTree', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid='log-tree' data-id={p.calculationId} data-show-title={String(p.showTitle)} />
  ),
}))

const base = { open: true, onClose: () => {}, model: 'orders', pk: 42 }
const RUN = 'orders_42_update_abc'

beforeEach(() => {
  resolver.mockReset()
  resolver.mockReturnValue({ calculationId: RUN, isResolving: false })
})

describe('F12.77–F12.78 — CalculationLogDrawer', () => {
  it('F12.77 while running it streams, and shows no tree', () => {
    render(<CalculationLogDrawer {...base} record={{}} status='in_progress' />)

    expect(screen.getByTestId('log-stream')).toHaveAttribute('data-id', RUN)
    expect(screen.queryByTestId('log-tree')).not.toBeInTheDocument()
  })

  it('F12.77 once finished it shows the tree, without a second title bar', () => {
    render(
      <CalculationLogDrawer {...base} record={{ lex_reserved_calculation_id: RUN }} status='success' />,
    )

    const tree = screen.getByTestId('log-tree')
    expect(tree).toHaveAttribute('data-id', RUN)
    expect(tree).toHaveAttribute('data-show-title', 'false')
    expect(screen.queryByTestId('log-stream')).not.toBeInTheDocument()
  })

  it('F12.77 it hands the annotated id to useResolvedCalculationId', () => {
    render(
      <CalculationLogDrawer {...base} record={{ lex_reserved_calculation_id: RUN }} status='success' />,
    )

    expect(resolver).toHaveBeenCalledWith('orders', 42, { calculation_id: RUN })
  })

  it('F12.77 a finished run with no log says so', () => {
    resolver.mockReturnValue({ calculationId: null, isResolving: false })
    render(<CalculationLogDrawer {...base} record={{}} status='success' />)

    expect(screen.getByText('No log was recorded for this run.')).toBeInTheDocument()
  })

  it('F12.78 it swaps from stream to tree when its own run finishes', () => {
    const { rerender } = render(<CalculationLogDrawer {...base} record={{}} status='in_progress' />)
    expect(screen.getByTestId('log-stream')).toBeInTheDocument()

    rerender(<CalculationLogDrawer {...base} record={{}} status='success' />)

    expect(screen.getByTestId('log-tree')).toHaveAttribute('data-id', RUN)
    expect(screen.queryByTestId('log-stream')).not.toBeInTheDocument()
  })

  it('F12.78 on ERROR it leads with the recorded failure', () => {
    render(
      <CalculationLogDrawer
        {...base}
        record={{ calculation_error_message: 'ValueError: no date\n\nTraceback (most recent call last):' }}
        status='error'
      />,
    )

    const headline = screen.getByTestId('run-headline')
    expect(headline).toHaveAttribute('data-tone', 'error')
    expect(headline).toHaveTextContent('ValueError: no date')
  })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `./node_modules/.bin/vitest run src/components/model-components/CalculateFunctionality/__test__/CalculationLogDrawer.test.tsx`
Expected: FAIL — `Failed to resolve import "../CalculationLogDrawer"`.

- [ ] **Step 3: Write the drawer**

Create `src/components/model-components/CalculateFunctionality/CalculationLogDrawer.tsx`:

```tsx
import React, { JSX } from 'react'
import { Box, Typography } from '@mui/material'
import ArticleOutlinedIcon from '@mui/icons-material/ArticleOutlined'
import SideDrawer from '../../SideDrawer/SideDrawer'
import CalculationLogStream from '../../widgets/CalculationLogStream'
import CalculationLogTree from '../../../pages/CalculationLogTree/CalculationLogTree'
import { useResolvedCalculationId } from '../../widgets/useResolvedCalculationId'
import { headlineFor, type RunHeadline } from './calculationRunHeadline'
import type { CalculationStatusKind } from './calculationStatus'

/** Wider than a form: logs carry tables and code laid out for a wide dialog. */
export const LOG_DRAWER_WIDTH = 'min(1000px, 92vw)'
export const LOG_DRAWER_EXPAND_KEY = 'lex.calculationLogDrawer.expanded'

export interface CalculationLogDrawerProps {
  open: boolean
  onClose: () => void
  model: string
  pk: string | number
  /** The grid row: carries `lex_reserved_calculation_id` and any recorded failure. */
  record: Record<string, unknown>
  /** From the cell, which tracks the row's status live — so it cannot disagree with the pill. */
  status: CalculationStatusKind
}

/** The same four values the status pill uses, softened for a panel. */
const TONE: Record<RunHeadline['tone'], { fg: string; bg: string; border: string }> = {
  error: { fg: '#d32f2f', bg: 'rgba(211, 47, 47, 0.06)', border: 'rgba(211, 47, 47, 0.35)' },
  warning: { fg: '#ed6c02', bg: 'rgba(237, 108, 2, 0.06)', border: 'rgba(237, 108, 2, 0.35)' },
  neutral: { fg: '#607d8b', bg: 'rgba(96, 125, 139, 0.08)', border: 'rgba(96, 125, 139, 0.35)' },
}

function RunHeadlineBanner({ headline }: { headline: RunHeadline }): JSX.Element {
  const tone = TONE[headline.tone]
  return (
    <Box
      role='status'
      data-testid='run-headline'
      data-tone={headline.tone}
      sx={{
        mx: 2,
        mt: 1.5,
        p: 1.5,
        border: '1px solid',
        borderColor: tone.border,
        bgcolor: tone.bg,
        borderRadius: 1.5,
        flexShrink: 0,
      }}
    >
      <Typography sx={{ color: tone.fg, fontWeight: 600, fontSize: 14 }}>{headline.title}</Typography>
      {headline.note && (
        <Typography sx={{ color: 'text.secondary', fontSize: 13, mt: 0.5 }}>{headline.note}</Typography>
      )}
      {headline.detail && (
        <Box
          component='pre'
          sx={{
            mt: 1,
            mb: 0,
            maxHeight: 280,
            overflow: 'auto',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontSize: 12,
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {headline.detail}
        </Box>
      )}
    </Box>
  )
}

/**
 * The log of the run this row last started, without leaving the table.
 *
 * Running: `CalculationLogStream`, which takes its socket from the same
 * registry — under the same key — as the Actions column, so this never opens a
 * second one. Finished: `CalculationLogTree`, which reads the durable table,
 * not the cache that completion purges.
 */
export default function CalculationLogDrawer({
  open,
  onClose,
  model,
  pk,
  record,
  status,
}: CalculationLogDrawerProps): JSX.Element {
  // Live id while running, the annotated id once finished — and the id already
  // resolved, kept across the moment a run completes, when the live entry is
  // gone and the row may not have been refetched yet.
  const { calculationId, isResolving } = useResolvedCalculationId(model, pk, {
    calculation_id: record.lex_reserved_calculation_id,
  })
  const headline = headlineFor(status, record)
  const running = status === 'in_progress'

  return (
    <SideDrawer
      open={open}
      onClose={onClose}
      icon={<ArticleOutlinedIcon fontSize='small' />}
      title='Calculation log'
      idPill={`${model} #${String(pk)}`}
      closeLabel='Close calculation log'
      width={LOG_DRAWER_WIDTH}
      expandStorageKey={LOG_DRAWER_EXPAND_KEY}
    >
      {headline && <RunHeadlineBanner headline={headline} />}
      <Box sx={{ flex: 1, minHeight: 0, px: 2, py: 1.5, display: 'flex', flexDirection: 'column' }}>
        {running ? (
          <CalculationLogStream model={model} pk={pk} calculationId={calculationId} />
        ) : calculationId ? (
          <CalculationLogTree calculationId={calculationId} embedded showTitle={false} />
        ) : (
          <Typography sx={{ color: 'text.secondary', fontSize: 14 }}>
            {isResolving ? 'Finding this run’s log…' : 'No log was recorded for this run.'}
          </Typography>
        )}
      </Box>
    </SideDrawer>
  )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the Step 2 command. Expected: 6 passed.

- [ ] **Step 5: Format and commit**

```bash
./node_modules/.bin/prettier --write src/components/model-components/CalculateFunctionality/CalculationLogDrawer.tsx src/components/model-components/CalculateFunctionality/__test__/CalculationLogDrawer.test.tsx
git add src/components/model-components/CalculateFunctionality/CalculationLogDrawer.tsx src/components/model-components/CalculateFunctionality/__test__/CalculationLogDrawer.test.tsx
git commit -F - <<'MSG'
feat(calculation-log): a drawer for the run a row last started

Streams while running, shows the durable tree once finished, and leads with
what happened for ERROR, ABORTED and CANCELLED. The running body takes its
socket from the same registry as the Actions column, so it never opens a
second one; the finished body reads the table, not the cache completion
purges. The id comes from useResolvedCalculationId, which keeps it across
the moment a run completes.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

---

### Task 8: The cells, the spinner test, batches 7f and 12m, the PR

**Files:**
- Modify: `src/components/model-components/CalculateFunctionality/CalculateFunctionality.tsx`
- Modify: `src/components/model-components/__test__/CalculateFunctionality.test.tsx`
- Modify: `docs/test-plan/clusters/F07-calc_status/{allocation.yaml,batches.md}`, `docs/test-plan/clusters/F12-embed_streamlit/{allocation.yaml,batches.md}`

**Interfaces:**
- Consumes: `CalculationLogDrawer` (Task 7); `classifyCalculationStatus(value, isRunning)` from `./calculationStatus`; the grid fields from Task 3.
- Produces: log button named `Open calculation log` (`data-testid="log-door"`, `data-tone="error" | "default"`); its reserved slot (`data-testid="log-door-slot"`, `aria-hidden`); play button named `Run calculation`.

- [ ] **Step 1: Update the test helper and mocks**

In `CalculateFunctionality.test.tsx`:

Delete the `CalculationLogs` mock (the component no longer imports it):

```tsx
vi.mock('../../../web-sockets/CalculationLogs', () => ({
  __esModule: true,
  default: () => null,
}))
```

Keep the `react-loading` mock, and put this comment above it:

```tsx
// Kept although the component no longer imports react-loading: the mock is
// what makes "no spinner" assertable. If the spinner ever came back, this
// testid would render and F7.58 would fail.
```

Add below the existing mocks:

```tsx
vi.mock('../CalculateFunctionality/CalculationLogDrawer', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid='log-drawer' data-status={p.status} />,
}))
```

Replace the render helper's signature and record:

```tsx
const renderCalculateFunctionality = ({
  isCalculated = false,
  isHistoryView = false,
  dataProvider,
}: {
  isCalculated?: string | boolean
  isHistoryView?: boolean
  dataProvider?: any
} = {}) => {
```

with:

```tsx
const renderCalculateFunctionality = ({
  isCalculated = false,
  isHistoryView = false,
  dataProvider,
  variant,
  suppressLogViewer,
  extra = {},
}: {
  isCalculated?: string | boolean
  isHistoryView?: boolean
  dataProvider?: any
  variant?: 'full' | 'status' | 'action'
  suppressLogViewer?: boolean
  extra?: Record<string, unknown>
} = {}) => {
```

and inside its JSX replace:

```tsx
          record={{
            id: 42,
            is_calculated: isCalculated,
            lex_reserved_scopes: {
              edit: ['is_calculated'],
            },
          }}
          isHistoryView={isHistoryView}
```

with:

```tsx
          record={{
            id: 42,
            is_calculated: isCalculated,
            lex_reserved_scopes: {
              edit: ['is_calculated'],
            },
            ...extra,
          }}
          isHistoryView={isHistoryView}
          variant={variant}
          suppressLogViewer={suppressLogViewer}
```

- [ ] **Step 2: Rewrite the spinner test and add the door tests**

Replace the test named `'still shows the spinner for live in-progress records'` — whole `it(...)` block — with:

```tsx
  it('F7.58 a live run keeps its play button, disabled — THIS TEST USED TO PIN THE SPINNER that replaced it, and that spinner was the only door to the log', () => {
    renderCalculateFunctionality({ isCalculated: 'IN_PROGRESS' })

    expect(screen.getByText('IN_PROGRESS')).toBeInTheDocument()
    expect(screen.queryByTestId('calculation-spinner')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run calculation' })).toBeDisabled()
  })
```

Add at the end of the top-level `describe`:

```tsx
  // F7.55–F7.57 — the door to a finished run's log lives beside the pill.
  it('F7.55 a finished run with a log gets a door', () => {
    renderCalculateFunctionality({
      isCalculated: 'SUCCESS',
      extra: { lex_reserved_has_calculation_log: true },
    })
    expect(screen.getByRole('button', { name: 'Open calculation log' })).toBeInTheDocument()
  })

  it('F7.55 a finished run without a log gets none — the door follows the data, not the status', () => {
    renderCalculateFunctionality({ isCalculated: 'SUCCESS' })
    expect(screen.queryByRole('button', { name: 'Open calculation log' })).not.toBeInTheDocument()
  })

  it('F7.55 a live run gets a door before the row has been annotated', () => {
    renderCalculateFunctionality({ isCalculated: 'IN_PROGRESS' })
    expect(screen.getByRole('button', { name: 'Open calculation log' })).toBeInTheDocument()
  })

  it('F7.55 the door opens the log drawer with the row’s status', async () => {
    const user = userEvent.setup()
    renderCalculateFunctionality({
      isCalculated: 'SUCCESS',
      extra: { lex_reserved_has_calculation_log: true },
    })
    await user.click(screen.getByRole('button', { name: 'Open calculation log' }))
    expect(screen.getByTestId('log-drawer')).toHaveAttribute('data-status', 'success')
  })

  it('F7.56 there is no door in history view', () => {
    renderCalculateFunctionality({
      isCalculated: 'SUCCESS',
      isHistoryView: true,
      extra: { lex_reserved_has_calculation_log: true },
    })
    expect(screen.queryByRole('button', { name: 'Open calculation log' })).not.toBeInTheDocument()
  })

  it('F7.56 there is no door when the host shows its own log', () => {
    renderCalculateFunctionality({
      isCalculated: 'SUCCESS',
      suppressLogViewer: true,
      extra: { lex_reserved_has_calculation_log: true },
    })
    expect(screen.queryByRole('button', { name: 'Open calculation log' })).not.toBeInTheDocument()
  })

  it('F7.56 an empty door keeps its slot, so the column does not shift', () => {
    renderCalculateFunctionality({ isCalculated: 'NOT_CALCULATED', variant: 'status' })
    expect(screen.getByTestId('log-door-slot')).toHaveAttribute('aria-hidden', 'true')
  })

  it('F7.56 the Actions column carries no door', () => {
    renderCalculateFunctionality({
      isCalculated: 'SUCCESS',
      variant: 'action',
      extra: { lex_reserved_has_calculation_log: true },
    })
    expect(screen.queryByRole('button', { name: 'Open calculation log' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run calculation' })).toBeInTheDocument()
  })

  it('F7.57 the door is red when the run failed', () => {
    renderCalculateFunctionality({
      isCalculated: 'ERROR',
      extra: { lex_reserved_has_calculation_log: true },
    })
    expect(screen.getByTestId('log-door')).toHaveAttribute('data-tone', 'error')
  })
```

- [ ] **Step 3: Run the file to verify the new tests fail**

Run: `./node_modules/.bin/vitest run src/components/model-components/__test__/CalculateFunctionality.test.tsx`
Expected: F7.55–F7.58 fail (no button named `Open calculation log`; no button named `Run calculation`; spinner present). Every other test in the file still passes.

- [ ] **Step 4: Remove the old door**

In `CalculateFunctionality.tsx`, delete these imports (each has no other use after this task):

```tsx
import ReactLoading from 'react-loading'
import CalculationLogs from '../../../web-sockets/CalculationLogs'
import { useLazyGetInitCalculationLogsQuery } from '../../../store/calculation-logs'
import { normalizeCalculationLogText } from '../../../utils/normalizeCalculationLogText'
```

Keep `CustomNotification` and `Box`: the run handler's error notification still uses both.

Add:

```tsx
import ArticleOutlinedIcon from '@mui/icons-material/ArticleOutlined'
import CalculationLogDrawer from './CalculationLogDrawer'
import { classifyCalculationStatus } from './calculationStatus'
```

Delete the log state and the cache fetch:

```tsx
  const [logs, setLogs] = useState('')
```

```tsx
  const [getInitCalculationLogs] = useLazyGetInitCalculationLogsQuery()
```

Replace the whole `handleOpen` (from `const handleOpen = useCallback(async () => {` through its closing `}, [calcId, calculationRecord, getInitCalculationLogs, notify])`) with:

```tsx
  // The drawer's body fetches its own log — the stream backfills, the tree
  // reads the table — so opening it is only opening it.
  const handleOpen = useCallback(() => setOpen(true), [])
```

Replace:

```tsx
  const shouldShowSpinner = isRunning && !isHistoryView
```

with:

```tsx
  const isLiveRun = isRunning && !isHistoryView
  const status = classifyCalculationStatus(record.is_calculated, isRunning)
  // The door follows the data, not the status: a live run always has a log; a
  // finished one has one when the grid says so. Never in history view (a past
  // version has no live log and is not annotated), never when the host shows
  // its own stream beside the control.
  const logDoorVisible =
    !suppressLogViewer &&
    !isHistoryView &&
    (isLiveRun || record.lex_reserved_has_calculation_log === true)
```

- [ ] **Step 5: Add the door component**

Above `export default function CalculateFunctionality`, add:

```tsx
/**
 * The door to this row's calculation log.
 *
 * Always occupies its slot: when there is no log it renders the same button,
 * hidden and removed from the accessibility tree, so doors line up down the
 * column and a row without a log leaves a gap instead of shifting anything.
 */
function LogDoor({
  visible,
  isError,
  compact,
  onOpen,
}: {
  visible: boolean
  isError: boolean
  compact: boolean
  onOpen: () => void
}): JSX.Element {
  const size = compact ? 'small' : 'medium'
  if (!visible) {
    return (
      <IconButton
        aria-hidden='true'
        tabIndex={-1}
        disabled
        size={size}
        sx={{ visibility: 'hidden', flexShrink: 0 }}
        data-testid='log-door-slot'
      >
        <ArticleOutlinedIcon fontSize='small' />
      </IconButton>
    )
  }
  return (
    <Tooltip title={isError ? 'See why this calculation failed' : 'See the calculation log'}>
      <IconButton
        aria-label='Open calculation log'
        onClick={(event) => {
          // A grid cell click would otherwise select or open the row.
          event.stopPropagation()
          onOpen()
        }}
        size={size}
        data-testid='log-door'
        data-tone={isError ? 'error' : 'default'}
        sx={{ color: isError ? 'error.main' : 'text.secondary', flexShrink: 0 }}
      >
        <ArticleOutlinedIcon fontSize='small' />
      </IconButton>
    </Tooltip>
  )
}
```

If `JSX` is not already imported in this file, change `import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'` to include `JSX`.

- [ ] **Step 6: Replace the render**

Replace the entire `return (` … `)` of the component (from `// ── Render ──` to the end of the function) with:

```tsx
  // ── Render ────────────────────────────────────────────────
  // The Calculation column (variant 'status') puts the door at the trailing
  // edge, so doors form one straight line whatever the pill's width.
  return (
    <Flex align='center' justify={variant === 'status' ? 'space-between' : 'center'} gap={4}>
      {showPill && (
        <CalculationStatusPill
          value={record.is_calculated}
          isRunning={isRunning}
          compact={isCompactMode}
        />
      )}

      {showPill && (
        <LogDoor
          visible={logDoorVisible}
          isError={status === 'error'}
          compact={!!isCompactMode}
          onOpen={handleOpen}
        />
      )}

      {showButton && (
        <Tooltip
          title={
            isHistoryView
              ? 'Calculation is unavailable in history view'
              : canCalculate
                ? 'Start your custom calculation'
                : 'You do not have permission to calculate this record'
          }
          placement='bottom'
        >
          <span>
            <IconButton
              aria-label='Run calculation'
              disabled={isCalculateButtonDisabled}
              onClick={() => void onCalculateButtonClick()}
              size={isCompactMode ? 'small' : 'medium'}
            >
              <PlayArrowIcon
                color={!isCalculateButtonDisabled ? 'secondary' : 'disabled'}
                fontSize={isCompactMode ? 'small' : 'medium'}
              />
            </IconButton>
          </span>
        </Tooltip>
      )}

      {/* Mounted on `open`, not on the door: when a run finishes, the door can
          briefly hide (the row is not refetched yet) while the reader is
          mid-log — unmounting then would close the drawer under them. */}
      {open && (
        <CalculationLogDrawer
          open={open}
          onClose={handleClose}
          model={model}
          pk={recordId}
          record={record}
          status={status}
        />
      )}
    </Flex>
  )
}
```

Leave the websocket effect above the render untouched: the Actions column still opens the socket, and the drawer's stream reuses it through the registry.

- [ ] **Step 7: Run the file to verify everything passes**

Run the Step 3 command. Expected: every test in the file passes, including F7.55–F7.58.

- [ ] **Step 8: Type-check and run the whole suite against the baselines**

```bash
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep -c 'error TS'
./node_modules/.bin/vitest run 2>&1 | grep -E '^ Test Files|^      Tests'
```

Expected: `tsc` no higher than the Task 4 baseline. `vitest`: the baseline's failures unchanged, and the passed count up by exactly **33** — 4 (Task 4) + 11 (Task 5) + 3 (Task 6) + 6 (Task 7) + 9 (Task 8). The spinner test was rewritten, not added, so it does not count.

- [ ] **Step 9: Record batches 7f and 12m**

In `docs/test-plan/clusters/F07-calc_status/allocation.yaml`, change `max_scenario: 54` to `max_scenario: 58` and append to `letters:`:

```yaml
  f:
    title: The door to a finished run's log lives beside the pill
    scenarios: F7.55-F7.58
    tier: A
    status: complete
    tests: {pass: 10, skip: 0, xfail: 0}
    note: >-
      Reported as "once they are done, we lose the logs". In the grid the only door to a calculation's
      log was the spinner in the Actions column, which exists only while the run is live; when the run
      ended the play button replaced it and the door went with it. The Calculation column now carries a
      log button beside the pill, present when the grid says the row has a log (lex-app batch 15i) or
      the run is live, red on ERROR, absent in history view and under suppressLogViewer, and always
      holding its slot so doors line up. The spinner is gone. F7.58 rewrites the test that pinned it -
      that test asserted the bug, and its new name says so.
```

In `docs/test-plan/clusters/F12-embed_streamlit/allocation.yaml`, change `max_scenario: 72` to `max_scenario: 78` and append to `letters:`:

```yaml
  m:
    title: The calculation log, in a drawer, after the run
    scenarios: F12.73-F12.78
    tier: A
    status: complete
    tests: {pass: 24, skip: 0, xfail: 0}
    note: >-
      The drawer the status cell opens. SideDrawer is the panel split out of FormDrawer, with a
      full-width toggle remembered in localStorage (F12.73-F12.74; FormDrawer's F5.60-F5.63 pass
      untouched). The headline (F12.75) leads ERROR with calculation_error_message, then error_message -
      the only durable traceback, since update_calculation_status broadcasts the stack trace and
      persists nothing - and says plainly when a model records neither. CalculationLogTree gains
      showTitle so the drawer does not stack two title bars, and a failed tree fetch now says so with a
      retry instead of rendering empty (F12.76). The drawer streams while running
      and shows the durable tree once finished, swapping on its own row's transition, with the id from
      useResolvedCalculationId, which keeps it across the completion gap (F12.77-F12.78).
```

Append to `docs/test-plan/clusters/F07-calc_status/batches.md`:

```markdown
## Batch 7f — the door to a finished run's log (F7.55-F7.58)

| | |
| --- | --- |
| Scenario range | F7.55 – F7.58 |
| Tier | A |
| Files covered | `src/components/model-components/CalculateFunctionality/CalculateFunctionality.tsx` (`LogDoor`, render, `logDoorVisible`) |
| Test file | `src/components/model-components/__test__/CalculateFunctionality.test.tsx` (+9, 1 rewritten) |
| Tests landed | **10 pass / 0 fail** |
| Status | ✅ Complete |
| Paired with | lex-app batch `15i` (the two fields the door reads) and F12 batch `12m` (the drawer it opens) |

**The only door to a calculation's log was the spinner**, in the Actions column, and it existed only
while the run was live. When the run ended, the play button replaced it and the door went with it —
the report, literally. The Calculation column now carries a log button beside the pill, present when
the grid says the row has a log or the run is live, red on ERROR, absent in history view and under
`suppressLogViewer`. It always holds its slot, so doors line up down the column.

**F7.58 asserted the bug, and passed.** It pinned the spinner and the absence of a play button while
running. It is rewritten rather than deleted, and its name says what it used to assert.
```

Append to `docs/test-plan/clusters/F12-embed_streamlit/batches.md`:

```markdown
## Batch 12m — the calculation log, in a drawer, after the run (F12.73-F12.78)

| | |
| --- | --- |
| Scenario range | F12.73 – F12.78 |
| Tier | A |
| Files covered | `src/components/SideDrawer/SideDrawer.tsx` (new), `src/components/model-components/forms/FormDrawer.tsx`, `src/components/model-components/CalculateFunctionality/calculationRunHeadline.ts` (new), `src/components/model-components/CalculateFunctionality/CalculationLogDrawer.tsx` (new), `src/pages/CalculationLogTree/CalculationLogTree.tsx` |
| Test files | `SideDrawer.test.tsx` (4), `calculationRunHeadline.test.ts` (11), `CalculationLogTree.test.tsx` (+3), `CalculationLogDrawer.test.tsx` (6) |
| Tests landed | **24 pass / 0 fail** |
| Status | ✅ Complete |
| Paired with | lex-app batch `15i` and F07 batch `7f` |

**`SideDrawer` is the panel `FormDrawer` used to be.** The form cascade stays in `FormDrawer`, whose
props and callers are unchanged; F5.60–F5.63 pass untouched. The log drawer adds a full-width toggle
remembered in `localStorage`, and a browser that refuses storage costs the memory, never the drawer.

**The headline shows the only durable traceback there is.** `update_calculation_status` broadcasts
a failed run's stack trace over the websocket and persists nothing, so the drawer leads ERROR with
`calculation_error_message`, then `error_message` — and says plainly when a model records neither.

**While running the drawer streams, once finished it shows the tree.** The stream shares the Actions
column's socket through the registry; the tree reads the durable table. The id comes from
`useResolvedCalculationId`, which keeps it across the moment a run completes — the live entry is gone
by then and the grid may not have refetched the row. A tree that fails to load now says so, with a
retry, instead of rendering empty.
```

- [ ] **Step 10: Commit and open the PR**

```bash
./node_modules/.bin/prettier --write src/components/model-components/CalculateFunctionality/CalculateFunctionality.tsx src/components/model-components/__test__/CalculateFunctionality.test.tsx
git add src/components/model-components docs/test-plan/clusters/F07-calc_status docs/test-plan/clusters/F12-embed_streamlit
git commit -F - <<'MSG'
feat(calculation-log): the log is reachable after the run ends

Reported as "once they are done, we lose the logs and we would have to do a
lot of steps to get them". The only door to a calculation's log was the
spinner in the Actions column, which existed only while the run was live.
The Calculation column now carries a log button beside the pill, present
whenever the row has a log or is running, red on ERROR, opening the log
drawer. The play button stays, disabled while running; the spinner is gone.

The test that pinned the spinner is rewritten rather than deleted, with the
correction in its name: it asserted the bug.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
git push origin HEAD:feat/calculation-log-drawer
gh pr create --repo ExcellenceCloudGmbH/process-admin-general-client --base lex-app-v2-pac-latest \
  --head feat/calculation-log-drawer --title "feat(calculation-log): the log is reachable after the run ends"
```

Write the PR body from the spec's *The problem*, *The cells* and *The drawer* sections plus Step 8's baseline comparison. State that `Run linters` is red on `lex-app-v2-pac-latest` itself and was not run locally, and that the lex-app PR from Task 3 is the other half. End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
