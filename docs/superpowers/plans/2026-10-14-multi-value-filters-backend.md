# Multi-value filters — backend and Streamlit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The lex-app side of value-list filters: Set and Multi filter models translated correctly, a
`filter_values` endpoint that lists a column's values with counts under the other filters, a
`filter_bar` list option, and `lex_view(filters=, filter_bar=, on_filter=)` with a component that
updates an embed's filters without reloading it.

**Architecture:** `ListModelEntries._build_filter_q` stays the one server-side filter translator; it
learns Set key conversion, blanks, empty selections and Multi. `FilterValues` subclasses
`ListModelEntries`, so it inherits the queryset (`as_of`), the read filter and the translator, and
adds grouping, labels and per-row field permissions. `lex_view` passes filters to its Streamlit
component as arguments (never in the component's key), and a small, separately tested script decides
when the component posts a changed filter into the frame.

**Tech Stack:** Django 6, DRF, pytest via `python -m lex pytest`, Streamlit custom component (plain
HTML/JS), node (for one shim test).

**Spec:** `docs/superpowers/specs/2026-10-14-multi-value-filters-and-cross-filtering-design.md`
(read its *Amendments* first).

**Companion plan:** `docs/superpowers/plans/2026-10-14-multi-value-filters-frontend.md` (the grid,
the filter bar and the bridge, in `process-admin-general-client`).

## Global Constraints

- **No customer data** in any file, test, commit, PR or issue. Examples use the test models
  (`QueryItem`, `RowFieldItem`, …) or invented names (invoice, region, order).
- **Branch:** `feat/multi-value-filters` in a worktree named `lex_*` (settings refuse other names),
  based on `origin/lex-app-v2`. Keep issue IDs out of branch names, PR titles and bodies.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body ends with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. The user merges; never run
  `gh pr merge`.
- **Tests** follow the lex-testing skill: cluster tests under `lex/test_project/tests/<cluster>/`,
  module docstring with Intent, `pytestmark`, Given/When/Then docstrings. Plan shards are updated in
  the same commit as the tests.
- **Allocations** (verified against `origin/lex-app-v2` on 2026-10-14):
  - Cluster 14 (queries): letters `f` (14.36–14.41) and `g` (14.42–14.51).
  - Cluster 4 (permissions): letter `o`, 4.80–4.83.
  - Cluster 10 (api_layer): letter `o`, 10.83–10.85.
  - Cluster 1 (init): letter `aw`, 1.393–1.399. `av` is held by open PR #810, so if #810 has merged
    when this lands, `aw` is still free; re-check before committing.
- **Run tests** from the worktree root:
  `DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd)/lex/test_project /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest <paths>`.
  One run at a time: every worktree shares one test database.
- **Wire constants**, used by both repos:
  - key of a stored value: `None` → `null`; `True` / `False` → `"true"` / `"false"`; dates →
    ISO `YYYY-MM-DD`; everything else → `str(value)`.
  - labels: blank → `"(Blank)"`; booleans → `"Yes"` / `"No"`.
  - `filter_values` default `limit` 200, maximum 1,000.
  - `LEX_FILTER_VALUES_SCAN_LIMIT` = 5000; `LEX_FILTER_VALUES_ROW_SCAN_LIMIT` = 50000.

---

### Task 1: Set and Multi filter translation

**Files:**
- Modify: `lex/api/views/model_entries/List.py` (imports, a module logger, `_bool_from_key`,
  `ListModelEntries._set_filter_values`, the `set` branch of `_build_filter_q`, a new `multi` branch,
  logging at the final `return None`)
- Test: `lex/test_project/tests/queries/test_14f_value_list_filters.py` (new)
- Plan: `lex/test_project/test-plan/clusters/14-queries/{allocation.yaml,batches.md,cluster.md}`

**Interfaces:**
- Produces: `ListModelEntries._set_filter_values(field: str, values: list) -> tuple[list, bool]`;
  `_build_filter_q` accepting `{filterType: 'multi', filterModels: [...]}`. A key that cannot be
  converted raises `rest_framework.exceptions.ValidationError({"filterModel": {field: msg}})`, a 400.

- [ ] **Step 1: Write the failing tests**

Create `lex/test_project/tests/queries/test_14f_value_list_filters.py`:

```python
"""Value-list filters: Set keys are converted, blanks match, nothing ticked means no rows.

Intent: the grid's value-list (Set) filter sends each ticked value as a string key,
a blank as ``null``, and a Multi filter as a condition plus a value list. The list
endpoint must turn exactly those into SQL: keys converted to the column's type,
``null`` matching blank cells (SQL ``IN`` never does), an empty selection matching
no rows, and both halves of a Multi filter applied. A regression silently widens or
empties the grid: the user sees rows they did not ask for, or none, and export and
"Select All Records" inherit the same answer.
Cluster 14f — scenarios 14.36–14.41. Type: E.
Covers: lex/api/views/model_entries/List.py (``_build_filter_q``, ``_set_filter_values``).
Run: python -m lex pytest lex/test_project/tests/queries/test_14f_value_list_filters.py -v
"""

from __future__ import annotations

from datetime import date

import pytest
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    ITEM,
    QUERY_STATUS_ACTIVE,
    QUERY_STATUS_ARCHIVED,
    QUERY_STATUS_DRAFT,
    QueryCategory,
    QueryItem,
)

pytestmark = pytest.mark.queries

EVERY_NAME = {"alpha-small", "alpha-mid", "beta-mid", "loose"}


def _request(filter_model: dict) -> dict:
    return {
        "startRow": 0,
        "endRow": 100,
        "rowGroupCols": [],
        "groupKeys": [],
        "pivotCols": [],
        "pivotMode": False,
        "valueCols": [],
        "sortModel": [],
        "filterModel": filter_model,
    }


class TestCluster14f_ValueListFilters(E2ETestCase):
    """Cluster 14f: Set and Multi filter models on the AG Grid list endpoint."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        self.alpha = QueryCategory.objects.create(name="alpha")
        self.beta = QueryCategory.objects.create(name="beta")
        QueryItem.objects.create(
            name="alpha-small", count=1, is_active=True, status=QUERY_STATUS_ACTIVE,
            created_on=date(2026, 1, 15), category=self.alpha,
        )
        QueryItem.objects.create(
            name="alpha-mid", count=5, is_active=False, status=QUERY_STATUS_ARCHIVED,
            created_on=date(2026, 3, 10), category=self.alpha,
        )
        QueryItem.objects.create(
            name="beta-mid", count=0, is_active=True, status=QUERY_STATUS_DRAFT,
            created_on=date(2026, 2, 20), category=self.beta,
        )
        QueryItem.objects.create(
            name="loose", count=7, is_active=True, status=QUERY_STATUS_ACTIVE,
            created_on=None, category=None,
        )

    def _post(self, filter_model: dict):
        return self.client.post(
            self.url_list(ITEM), data={"request": _request(filter_model)}, format="json",
        )

    def _names(self, filter_model: dict) -> set[str]:
        resp = self._post(filter_model)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return {row["name"] for row in resp.data["rowData"]}

    # -- 14.36 ---------------------------------------------------------
    def test_14_36_keys_are_converted_to_the_column_type(self):
        """
        Scenario 14.36: a ticked value arrives as a string and still matches
        Given: rows with integer, boolean, date and foreign-key columns
        When: Set filters send their keys as strings
        Then: each key matches the rows holding that value
        """
        self.assertEqual(
            self._names({"count": {"filterType": "set", "values": ["1", "5"]}}),
            {"alpha-small", "alpha-mid"}, "integer keys arrive as strings",
        )
        self.assertEqual(
            self._names({"is_active": {"filterType": "set", "values": ["false"]}}),
            {"alpha-mid"}, '"false" is the wire key for False; Django would reject it as is',
        )
        self.assertEqual(
            self._names({"created_on": {"filterType": "set", "values": ["2026-02-20"]}}),
            {"beta-mid"}, "a date key is ISO",
        )
        self.assertEqual(
            self._names({"category": {"filterType": "set", "values": [str(self.beta.pk)]}}),
            {"beta-mid"}, "a foreign key's key is the related primary key",
        )

    # -- 14.37 ---------------------------------------------------------
    def test_14_37_null_matches_blank_cells(self):
        """
        Scenario 14.37: "(Blank)" in a value list finds the empty cells
        Given: a row with no date and no category
        When: a Set filter carries null, alone or beside a value
        Then: the blank row matches — SQL IN alone never matches NULL
        """
        self.assertEqual(
            self._names({"created_on": {"filterType": "set", "values": [None]}}), {"loose"},
            "null alone must find the blank row",
        )
        self.assertEqual(
            self._names({"category": {"filterType": "set", "values": [str(self.alpha.pk), None]}}),
            {"alpha-small", "alpha-mid", "loose"}, "a value and null together OR their matches",
        )

    # -- 14.38 ---------------------------------------------------------
    def test_14_38_nothing_ticked_means_no_rows(self):
        """
        Scenario 14.38: an empty selection is "match nothing", as AG Grid means it
        Given: four rows
        When: a Set filter arrives with an empty values list
        Then: no row matches, rather than every row
        """
        self.assertEqual(
            self._names({"status": {"filterType": "set", "values": []}}), set(),
            "an empty selection used to be dropped, which returned every row",
        )

    # -- 14.39 ---------------------------------------------------------
    def test_14_39_both_halves_of_a_multi_filter_apply(self):
        """
        Scenario 14.39: a condition and a value list on one column are ANDed
        Given: a text column with a Multi filter
        When: the condition says "contains mid" and the list ticks two names
        Then: only rows passing both match; an empty half is ignored
        """
        both = {"name": {"filterType": "multi", "filterModels": [
            {"filterType": "text", "type": "contains", "filter": "mid"},
            {"filterType": "set", "values": ["alpha-mid", "loose"]},
        ]}}
        self.assertEqual(self._names(both), {"alpha-mid"}, "both halves must apply")
        list_only = {"name": {"filterType": "multi", "filterModels": [
            None, {"filterType": "set", "values": ["loose"]},
        ]}}
        self.assertEqual(self._names(list_only), {"loose"}, "a null half is skipped")

    # -- 14.40 ---------------------------------------------------------
    def test_14_40_an_unconvertible_key_is_a_400_naming_the_field(self):
        """
        Scenario 14.40: a bad key is refused, not silently widened
        Given: an integer column
        When: a Set filter sends a key that is not an integer
        Then: 400, and the error names the column
        """
        resp = self._post({"count": {"filterType": "set", "values": ["many"]}})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("count", str(resp.data), "the error must name the field")

    # -- 14.41 ---------------------------------------------------------
    def test_14_41_an_unknown_filter_type_is_ignored_and_logged(self):
        """
        Scenario 14.41: compatibility for shapes the server does not know
        Given: a filter model with an unknown filterType
        When: the list is requested
        Then: the filter is ignored as before, and the server logs the field and type
        """
        with self.assertLogs("lex.api.views.model_entries.List", level="INFO") as logs:
            names = self._names({"name": {"filterType": "fancy", "filter": "x"}})
        self.assertEqual(names, EVERY_NAME, "unknown types keep their old meaning: no filter")
        self.assertTrue(
            any("fancy" in line and "name" in line for line in logs.output),
            f"the log must name the field and the type; got {logs.output!r}",
        )
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `… -m lex pytest lex/test_project/tests/queries/test_14f_value_list_filters.py -v`
Expected: 14.36 fails (`"false"` rejected, 500 or ValidationError), 14.37 returns no rows, 14.38
returns all four rows, 14.39 returns all four rows, 14.40 is a 500, 14.41 has no log. Read each
failure and confirm it is one of these, not an import or fixture error.

- [ ] **Step 3: Implement the translation**

In `lex/api/views/model_entries/List.py`:

Add to the imports (keep the existing ones):

```python
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import BooleanField
from rest_framework.exceptions import ValidationError
```

Below the imports, beside `MAX_PIVOT_COMBINATIONS`:

```python
logger = logging.getLogger(__name__)

_TRUE_KEYS = {"true", "1", "t", "yes"}
_FALSE_KEYS = {"false", "0", "f", "no"}


def _bool_from_key(raw: Any) -> bool:
    """A value-list key for a boolean column. Django's own ``to_python`` rejects
    ``"true"`` and ``"false"``, which are exactly what the grid sends."""
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE_KEYS:
        return True
    if text in _FALSE_KEYS:
        return False
    raise ValueError(f"{raw!r} is not a boolean")
```

Add this method to `ListModelEntries`, just above `_build_filter_q`:

```python
    def _set_filter_values(self, field: str, values: list) -> Tuple[list, bool]:
        """Convert a Set filter's keys to the column's Python values.

        A key is the stored value as a string -- ``"5"``, ``"true"``, an ISO
        date, a related primary key. ``None`` stands for blank, which SQL ``IN``
        never matches, so it is reported separately. A key that cannot be
        converted is a 400 naming the field: answering with more rows than the
        user asked for is worse than refusing.
        """
        model_field = self._get_model_field(field)
        target_field = getattr(model_field, "target_field", None)
        if getattr(model_field, "is_relation", False) and target_field is not None:
            convert = target_field.to_python
        elif isinstance(model_field, BooleanField):
            convert = _bool_from_key
        else:
            convert = model_field.to_python

        converted: list = []
        include_blank = False
        for raw in values:
            if raw is None:
                include_blank = True
                continue
            try:
                converted.append(convert(raw))
            except (DjangoValidationError, TypeError, ValueError) as exc:
                raise ValidationError(
                    {"filterModel": {field: f"{raw!r} is not a valid value for {field}."}}
                ) from exc
        return converted, include_blank
```

Replace the `set` branch of `_build_filter_q`:

```python
        if filter_type == "set":
            values = model.get("values") or []
            if not isinstance(values, list) or not values:
                return None
            return Q(**{f"{field}__in": values})
```

with:

```python
        if filter_type == "set":
            values = model.get("values")
            if not isinstance(values, list):
                return None
            if not values:
                # AG Grid's Set filter with nothing ticked means "no rows".
                return Q(pk__in=[])
            converted, include_blank = self._set_filter_values(field, values)
            condition: Optional[Q] = Q(**{f"{field}__in": converted}) if converted else None
            if include_blank:
                blank = Q(**{f"{field}__isnull": True})
                condition = blank if condition is None else (condition | blank)
            return condition

        if filter_type == "multi":
            children = model.get("filterModels")
            if not isinstance(children, list):
                return None
            conditions = [
                self._build_filter_q(field, child)
                for child in children
                if child not in (None, "")
            ]
            conditions = [condition for condition in conditions if condition is not None]
            if not conditions:
                return None
            combined = conditions[0]
            for condition in conditions[1:]:
                combined &= condition
            return combined
```

Replace the final `return None` of `_build_filter_q` (after the `date` branch) with:

```python
        logger.info("Ignoring unknown filterType %r for field %r", filter_type, field)
        return None
```

- [ ] **Step 4: Run the tests to see them pass, plus the existing cluster**

Run: `… -m lex pytest lex/test_project/tests/queries -v`
Expected: 14f 6 passed, and every pre-existing cluster 14 test still passes (14.13 sends string keys
on a text column and is unaffected).

- [ ] **Step 5: Update the plan shards**

Append to `lex/test_project/test-plan/clusters/14-queries/allocation.yaml` under `letters:` and set
`max_scenario: 41`:

```yaml
  f:
    title: Value-list filters — Set keys converted, blanks, empty selections, Multi
    scenarios: 14.36-14.41
    status: complete
    tests:
      pass: 6
      skip: 0
      xfail: 0
    note: >-
      The grid's value-list filter sends string keys, null for blank and a Multi model for a
      condition plus a list. _build_filter_q converted nothing (a "false" key 500ed on a boolean),
      matched no blank (SQL IN skips NULL), treated an empty selection as "no filter", and dropped
      Multi entirely, widening the result. 14.36-14.39 pin each answer, 14.40 that a bad key is a
      400 naming the field, 14.41 that an unknown filterType still means "no filter" and is logged.
```

Replace `(no batches recorded yet)` in `clusters/14-queries/batches.md` with:

```markdown
## Batch 14f — Value-list filters on the AG Grid list endpoint (14.36-14.41)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/List.py`
- **Test file:** `lex/test_project/tests/queries/test_14f_value_list_filters.py`
- **Test classes:** `TestCluster14f_ValueListFilters`
- **Fixtures:** `QueryItem`, `QueryCategory`
- **Status:** complete — 6 pass
```

Append to `clusters/14-queries/cluster.md`:

```markdown
### 14f. Value-list filters

| # | Scenario | What We Assert | Status |
|---|----------|----------------|--------|
| 14.36 | Set keys arrive as strings | Integer, `"false"`, ISO date and related-pk keys each match their rows | ✅ |
| 14.37 | Blank in a value list | `null` matches empty cells, alone or beside a value | ✅ |
| 14.38 | Nothing ticked | An empty `values` list matches no rows | ✅ |
| 14.39 | Multi filter | Condition and list on one column are ANDed; a null half is skipped | ✅ |
| 14.40 | Unconvertible key | 400 naming the field | ✅ |
| 14.41 | Unknown filterType | Ignored as before, and logged with field and type | ✅ |
```

- [ ] **Step 6: Commit**

```bash
git add lex/api/views/model_entries/List.py lex/test_project/tests/queries/test_14f_value_list_filters.py lex/test_project/test-plan/clusters/14-queries
git commit -m "fix(filters): value-list keys are converted, blanks match, nothing ticked means no rows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The `filter_values` endpoint

**Files:**
- Create: `lex/api/utils/filterable_fields.py`
- Create: `lex/api/views/model_entries/FilterValues.py`
- Modify: `lex/process_admin/sites/process_admin_site.py` (import + route)
- Modify: `lex/test_project/tests/_e2e_test_case.py` (`url_filter_values`)
- Test: `lex/test_project/tests/queries/test_14g_filter_values_endpoint.py` (new)
- Plan: cluster 14 shards

**Interfaces:**
- Consumes: Task 1's `_set_filter_values` (through `_apply_filter_model`).
- Produces:
  - `is_value_list_field(model_field) -> bool` in `lex.api.utils.filterable_fields`.
  - `value_key(value) -> Optional[str]` and `uses_default_read_permission(model_class) -> bool` in
    `lex.api.views.model_entries.FilterValues`.
  - URL name `model-entries-filter-values`: `POST api/model_entries/<model>/filter_values?as_of=&serializer=`.
    - Body: `{field, filterModel, selected, search, limit}`.
    - Response: `{"values": [{"key", "label", "count"}], "truncated": bool}`.

- [ ] **Step 1: Add the URL helper to the E2E test case**

In `lex/test_project/tests/_e2e_test_case.py`, after `url_many`:

```python
    def url_filter_values(self, model_name):
        """POST endpoint for ``FilterValues`` — a column's values with counts."""
        return self._url(
            "model-entries-filter-values",
            model_container=_MockContainer(model_name),
        )
```

- [ ] **Step 2: Write the failing tests**

Create `lex/test_project/tests/queries/test_14g_filter_values_endpoint.py`:

```python
"""A column's values with counts, under every filter except the column's own.

Intent: a value-list filter must offer what the grid would show — the same rows
(``as_of`` and read permissions included) under the other active filters — with a
count per value and a readable label. A column's own filter is left out, so a
ticked value can be unticked and more added; a ticked value the other filters
removed stays listed at 0. A regression offers values that lead to empty grids,
hides values the user needs, or shows ids where names belong.
Cluster 14g — scenarios 14.42–14.51. Type: E.
Covers: lex/api/views/model_entries/FilterValues.py, lex/api/utils/filterable_fields.py,
lex/process_admin/sites/process_admin_site.py (route).
Run: python -m lex pytest lex/test_project/tests/queries/test_14g_filter_values_endpoint.py -v
"""

from __future__ import annotations

import time
import urllib.parse
from datetime import date
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    ITEM,
    QUERY_STATUS_ACTIVE,
    QUERY_STATUS_ARCHIVED,
    QUERY_STATUS_DRAFT,
    QueryCategory,
    QueryItem,
)

pytestmark = pytest.mark.queries


class TestCluster14g_FilterValuesEndpoint(E2ETestCase):
    """Cluster 14g: POST api/model_entries/<model>/filter_values."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        self.alpha = QueryCategory.objects.create(name="alpha")
        self.beta = QueryCategory.objects.create(name="beta")
        rows = [
            ("alpha-small", QUERY_STATUS_ACTIVE, True, 1, date(2026, 1, 15), self.alpha),
            ("alpha-mid", QUERY_STATUS_ARCHIVED, False, 5, date(2026, 3, 10), self.alpha),
            ("beta-mid", QUERY_STATUS_DRAFT, True, 0, date(2026, 2, 20), self.beta),
            ("beta-big", QUERY_STATUS_ACTIVE, True, 9, date(2026, 2, 20), self.beta),
            ("loose", QUERY_STATUS_ACTIVE, True, 7, None, None),
        ]
        for name, state, active, count, created_on, category in rows:
            QueryItem.objects.create(
                name=name, status=state, is_active=active, count=count,
                created_on=created_on, category=category,
            )

    def _post(self, body: dict, query: str = ""):
        return self.client.post(self.url_filter_values(ITEM) + query, data=body, format="json")

    def _values(self, field: str, filter_model=None, query: str = "", **extra) -> dict:
        resp = self._post({"field": field, "filterModel": filter_model or {}, **extra}, query)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return resp.data

    def _counts(self, field: str, filter_model=None, query: str = "", **extra) -> dict:
        data = self._values(field, filter_model, query, **extra)
        return {item["key"]: item["count"] for item in data["values"]}

    # -- 14.42 ---------------------------------------------------------
    def test_14_42_counts_follow_the_other_filters_not_the_columns_own(self):
        """
        Scenario 14.42: narrowing
        Given: five rows across three statuses and two categories
        When: status values are asked for under a status filter, then under a category filter
        Then: the status filter is ignored for its own list; the category filter narrows it
        """
        own = {"status": {"filterType": "set", "values": [QUERY_STATUS_ARCHIVED]}}
        self.assertEqual(
            self._counts("status", own),
            {QUERY_STATUS_ACTIVE: 3, QUERY_STATUS_ARCHIVED: 1, QUERY_STATUS_DRAFT: 1},
            "a column must not narrow its own list",
        )
        other = {"category": {"filterType": "set", "values": [str(self.beta.pk)]}}
        self.assertEqual(
            self._counts("status", other),
            {QUERY_STATUS_ACTIVE: 1, QUERY_STATUS_DRAFT: 1},
            "another column's filter must narrow the list",
        )

    # -- 14.43 ---------------------------------------------------------
    def test_14_43_keys_and_labels_per_type(self):
        """
        Scenario 14.43: what a value list shows
        Given: choice, foreign-key, boolean, date and integer columns, one blank row
        When: each column's values are asked for
        Then: keys are the wire form and labels are readable
        """
        status_items = {i["key"]: i["label"] for i in self._values("status")["values"]}
        self.assertEqual(status_items[QUERY_STATUS_ACTIVE], "Active", "choices use their labels")

        categories = {i["key"]: (i["label"], i["count"]) for i in self._values("category")["values"]}
        self.assertEqual(categories[str(self.alpha.pk)], ("QCat<alpha>", 2), "a foreign key shows str(related)")
        self.assertEqual(categories[None], ("(Blank)", 1), "blank is listed with its own label")

        flags = {i["key"]: i["label"] for i in self._values("is_active")["values"]}
        self.assertEqual(flags, {"true": "Yes", "false": "No"}, "booleans are true/false keys, Yes/No labels")

        self.assertEqual(self._counts("created_on")["2026-02-20"], 2, "a date key is ISO")
        self.assertEqual(self._counts("count")["5"], 1, "an integer key is its string form")

    # -- 14.44 ---------------------------------------------------------
    def test_14_44_a_ticked_value_the_other_filters_removed_stays_at_zero(self):
        """
        Scenario 14.44: a ticked value can still be unticked
        Given: a category filter that leaves no archived row
        When: status values are asked for with "archived" ticked
        Then: archived is listed with count 0
        """
        other = {"category": {"filterType": "set", "values": [str(self.beta.pk)]}}
        counts = self._counts("status", other, selected=[QUERY_STATUS_ARCHIVED])
        self.assertEqual(counts.get(QUERY_STATUS_ARCHIVED), 0, "a ticked value must stay listed")

    # -- 14.45 ---------------------------------------------------------
    def test_14_45_search_matches_labels(self):
        """
        Scenario 14.45: searching a long list
        Given: text and foreign-key columns
        When: values are searched
        Then: text matches its values; a foreign key matches its labels
        """
        self.assertEqual(set(self._counts("name", search="MID")), {"alpha-mid", "beta-mid"}, "case-insensitive")
        self.assertEqual(set(self._counts("category", search="bet")), {str(self.beta.pk)}, "matched on str(related)")

    # -- 14.46 ---------------------------------------------------------
    def test_14_46_limit_cuts_the_list_and_says_so(self):
        """
        Scenario 14.46: a long list is cut, and a ticked value survives the cut
        Given: five distinct names
        When: two are asked for, then two with "loose" ticked
        Then: two come back with truncated=true; the ticked one is added after the cut
        """
        data = self._values("name", limit=2)
        self.assertEqual(len(data["values"]), 2, "the limit applies")
        self.assertTrue(data["truncated"], "a cut list must say so")
        ticked = {i["key"] for i in self._values("name", limit=2, selected=["loose"])["values"]}
        self.assertIn("loose", ticked, "a ticked value is never cut away")

    # -- 14.47 ---------------------------------------------------------
    def test_14_47_values_are_sorted_by_label_with_blank_last(self):
        """
        Scenario 14.47: a predictable order
        Given: two categories and one blank
        When: category values are asked for
        Then: labels ascend, case-insensitively, and blank comes last
        """
        labels = [i["label"] for i in self._values("category")["values"]]
        self.assertEqual(labels, ["QCat<alpha>", "QCat<beta>", "(Blank)"])

    # -- 14.48 ---------------------------------------------------------
    def test_14_48_only_the_lists_own_columns_can_be_listed(self):
        """
        Scenario 14.48: what is not filterable
        Given: the item model
        When: an unknown field, a JSON field, a related path or a serializer-only field is asked for
        Then: each is a 400 "is not filterable"
        """
        for field in ("nope", "metadata", "category__name", "lex_reserved_scopes", ""):
            resp = self._post({"field": field, "filterModel": {}})
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"{field!r}: {resp.data}")
            self.assertIn("not filterable", str(resp.data), field)

    # -- 14.49 ---------------------------------------------------------
    def test_14_49_as_of_lists_the_values_of_that_moment(self):
        """
        Scenario 14.49: time travel
        Given: a row whose status changed after a recorded instant
        When: status values are asked for as of that instant
        Then: the earlier status is counted, not the current one
        """
        time.sleep(0.05)
        moment = timezone.now()
        time.sleep(0.05)
        row = QueryItem.objects.get(name="beta-mid")
        row.status = QUERY_STATUS_ARCHIVED
        row.save()

        self.assertEqual(
            self._counts("status"),
            {QUERY_STATUS_ACTIVE: 3, QUERY_STATUS_ARCHIVED: 2},
            "now: the draft row is archived",
        )
        query = "?" + urllib.parse.urlencode({"as_of": moment.isoformat()})
        self.assertEqual(
            self._counts("status", query=query),
            {QUERY_STATUS_ACTIVE: 3, QUERY_STATUS_ARCHIVED: 1, QUERY_STATUS_DRAFT: 1},
            "as of the instant: still a draft",
        )

    # -- 14.50 ---------------------------------------------------------
    def test_14_50_a_bad_key_in_another_filter_is_a_400(self):
        """
        Scenario 14.50: the other filters are translated like the list's
        Given: an integer filter with a key that is not an integer
        When: another column's values are asked for under it
        Then: 400 naming the integer field
        """
        resp = self._post({"field": "status", "filterModel": {"count": {"filterType": "set", "values": ["x"]}}})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("count", str(resp.data))

    # -- 14.51 ---------------------------------------------------------
    def test_14_51_the_database_count_and_the_per_row_count_agree(self):
        """
        Scenario 14.51: two ways to count, one answer
        Given: a model whose read permission allows every field
        When: values are counted per row (overridden permission) and in the database
        Then: the answers are identical
        """
        other = {"is_active": {"filterType": "set", "values": ["true"]}}
        per_row = self._values("category", other)
        with patch(
            "lex.api.views.model_entries.FilterValues.uses_default_read_permission",
            return_value=True,
        ):
            in_database = self._values("category", other)
        self.assertEqual(per_row, in_database)
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `… -m lex pytest lex/test_project/tests/queries/test_14g_filter_values_endpoint.py -v`
Expected: every test errors with `NoReverseMatch` for `model-entries-filter-values`.

- [ ] **Step 4: Create the shared field predicate**

Create `lex/api/utils/filterable_fields.py`:

```python
"""Which model fields a value-list filter can list.

One answer for the endpoint that lists values and for the ``filter_bar`` list
option, so the bar can never name a column the endpoint would refuse.
"""

from __future__ import annotations

from django.db.models import FileField, JSONField


def is_value_list_field(model_field) -> bool:
    """A real column or a foreign key: not many-to-many, not JSON, not a file."""
    return (
        bool(getattr(model_field, "concrete", False))
        and not getattr(model_field, "many_to_many", False)
        and not isinstance(model_field, (JSONField, FileField))
    )
```

- [ ] **Step 5: Create the view**

Create `lex/api/views/model_entries/FilterValues.py`:

```python
"""A list column's distinct values with row counts, for value-list filters.

The grid's value-list filters ask this endpoint what a column holds. The answer
must match what the grid itself would show, so the view inherits the list view's
queryset (``as_of`` included), its read filter and its filter translation, and
applies every filter EXCEPT the column's own: a column's list shows what the
other filters leave, so a ticked value can always be unticked and others added.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import BooleanField, CharField, Count, Model, QuerySet, TextField
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from lex.api.serializers.base_serializers import LexSerializer
from lex.api.utils.filterable_fields import is_value_list_field
from lex.api.views.model_entries.List import ListModelEntries, normalize_field_path
from lex.core.models.LexModel import LexModel, UserContext

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
BLANK_LABEL = "(Blank)"
TRUE_LABEL = "Yes"
FALSE_LABEL = "No"
ROW_CHUNK_SIZE = 2000


def scan_limit() -> int:
    """Distinct values aggregated before a list is cut."""
    return int(getattr(settings, "LEX_FILTER_VALUES_SCAN_LIMIT", 5000))


def row_scan_limit() -> int:
    """Rows a per-row permission count may read before it is cut."""
    return int(getattr(settings, "LEX_FILTER_VALUES_ROW_SCAN_LIMIT", 50000))


def value_key(value: Any) -> Optional[str]:
    """The wire key for a stored value: what a value-list filter sends back."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def uses_default_read_permission(model_class: type[Model]) -> bool:
    """True when reads are all-or-nothing per row, so a database count is exact."""
    target = model_class
    if not (isinstance(target, type) and issubclass(target, LexModel)):
        target = getattr(model_class, "instance_type", None)
    if not (isinstance(target, type) and issubclass(target, LexModel)):
        return False
    return getattr(target, "permission_read", None) is LexModel.permission_read


def _bounded_limit(raw: Any) -> int:
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


class FilterValues(ListModelEntries):
    """``POST api/model_entries/<model>/filter_values``."""

    # The list view's GET would otherwise answer here with rows.
    http_method_names = ["post", "options"]

    def post(self, request, *args, **kwargs):
        self._ag_model_class = self.kwargs["model_container"].model_class
        self._ag_field_validity_cache: Dict[str, bool] = {}
        self._ag_model_field_cache: Dict[str, Any] = {}

        body = request.data if isinstance(request.data, dict) else {}
        field = normalize_field_path(str(body.get("field") or "")).strip()
        model_field = self._filterable_field(field)

        filter_model = body.get("filterModel") or {}
        if not isinstance(filter_model, dict):
            raise ValidationError({"filterModel": "must be an object"})
        other_filters = {
            key: value
            for key, value in filter_model.items()
            if normalize_field_path(str(key or "")).strip() != field
        }
        selected = body.get("selected") or []
        if not isinstance(selected, list):
            raise ValidationError({"selected": "must be a list"})
        selected_keys: List[Optional[str]] = [None if key is None else str(key) for key in selected]
        search = str(body.get("search") or "").strip()
        limit = _bounded_limit(body.get("limit", DEFAULT_LIMIT))

        queryset = self.filter_queryset(self.get_queryset())
        queryset = self._apply_filter_model(queryset, other_filters)
        searched_in_database = bool(search) and self._is_plain_text(model_field)
        if searched_in_database:
            queryset = queryset.filter(**{f"{field}__icontains": search})

        if uses_default_read_permission(queryset.model):
            counts, truncated = self._count_in_database(queryset, field)
        else:
            counts, truncated = self._count_per_row(request, queryset, model_field)

        labels = self._labels(model_field, set(counts) | set(selected_keys))
        items = [
            {"key": key, "label": labels.get(key, key or BLANK_LABEL), "count": count}
            for key, count in counts.items()
        ]
        if search and not searched_in_database:
            needle = search.casefold()
            items = [item for item in items if needle in item["label"].casefold()]
        items.sort(key=lambda item: (item["key"] is None, item["label"].casefold()))

        truncated = truncated or len(items) > limit
        items = items[:limit]
        shown = {item["key"] for item in items}
        for key in selected_keys:
            if key not in shown:
                items.append(
                    {"key": key, "label": labels.get(key, key or BLANK_LABEL), "count": counts.get(key, 0)}
                )
                shown.add(key)

        return Response({"values": items, "truncated": truncated})

    # -- which column ------------------------------------------------------

    def _filterable_field(self, field: str):
        """The model field behind ``field``, or a 400.

        It must be a field of the requested serializer -- a column the user can
        see in this list -- and a real column or foreign key on the model. One
        answer for every refusal, so the response never says which fields exist.
        """
        refusal = ValidationError({"field": f"{field!r} is not filterable."})
        if not field or "__" in field:
            raise refusal
        if field not in self.get_serializer().fields:
            raise refusal
        try:
            model_field = self._ag_model_class._meta.get_field(field)
        except FieldDoesNotExist:
            raise refusal
        if not is_value_list_field(model_field):
            raise refusal
        return model_field

    @staticmethod
    def _is_plain_text(model_field) -> bool:
        return isinstance(model_field, (CharField, TextField)) and not model_field.choices

    # -- counting ----------------------------------------------------------

    def _count_in_database(
        self, queryset: QuerySet, field: str
    ) -> Tuple[Dict[Optional[str], int], bool]:
        limit = scan_limit()
        grouped = (
            queryset.order_by()
            .values(field)
            .annotate(value_count=Count("pk"))
            .order_by("-value_count", field)
        )
        rows = list(grouped[: limit + 1])
        counts: Dict[Optional[str], int] = {}
        for row in rows[:limit]:
            key = value_key(row[field])
            counts[key] = counts.get(key, 0) + row["value_count"]
        return counts, len(rows) > limit

    def _count_per_row(
        self, request, queryset: QuerySet, model_field
    ) -> Tuple[Dict[Optional[str], int], bool]:
        """Count only rows whose ``permission_read`` lets this user read the field."""
        base_context = UserContext.from_request_base(request)
        name = model_field.name
        attname = model_field.attname
        max_rows = row_scan_limit()
        counts: Counter = Counter()
        truncated = False

        for scanned, row in enumerate(queryset.order_by().iterator(chunk_size=ROW_CHUNK_SIZE), start=1):
            if scanned > max_rows:
                truncated = True
                break
            target = row if isinstance(row, LexModel) else LexSerializer._unwrap_instance(row)
            try:
                result = target.permission_read(base_context.with_instance(request, target))
            except Exception:
                logger.warning("permission_read raised while listing %s values; row skipped", name, exc_info=True)
                continue
            if not result.allowed or name not in result.get_fields({name}):
                continue
            counts[value_key(getattr(row, attname))] += 1

        limit = scan_limit()
        if len(counts) > limit:
            truncated = True
            counts = Counter(dict(counts.most_common(limit)))
        return dict(counts), truncated

    # -- labels ------------------------------------------------------------

    def _labels(self, model_field, keys: Iterable[Optional[str]]) -> Dict[Optional[str], str]:
        keys = set(keys)
        labels: Dict[Optional[str], str] = {}
        if None in keys:
            labels[None] = BLANK_LABEL
        present = [key for key in keys if key is not None]

        if getattr(model_field, "is_relation", False) and getattr(model_field, "related_model", None):
            primary_keys = []
            for key in present:
                try:
                    primary_keys.append(model_field.target_field.to_python(key))
                except (DjangoValidationError, TypeError, ValueError):
                    continue
            related = model_field.related_model._default_manager.filter(pk__in=primary_keys)
            for obj in related:
                labels[value_key(obj.pk)] = str(obj)
        elif isinstance(model_field, BooleanField):
            for key in present:
                labels[key] = TRUE_LABEL if key == "true" else FALSE_LABEL
        else:
            choices = {value_key(value): str(label) for value, label in (model_field.flatchoices or [])}
            for key in present:
                labels[key] = choices.get(key, key)

        for key in present:
            labels.setdefault(key, key)
        return labels
```

- [ ] **Step 6: Register the route**

In `lex/process_admin/sites/process_admin_site.py`, add the import beside the other model_entries
views:

```python
from lex.api.views.model_entries.FilterValues import FilterValues
```

and add this route right after the `model-entries-list` route in `url_patterns_for_react_admin`:

```python
            path(
                "api/model_entries/<model:model_container>/filter_values",
                FilterValues.as_view(),
                name="model-entries-filter-values",
            ),
```

- [ ] **Step 7: Run the tests to see them pass**

Run: `… -m lex pytest lex/test_project/tests/queries/test_14g_filter_values_endpoint.py -v`
Expected: 10 passed. If 14.49 fails on timing, check that `QueryItem` writes history in E2E tests
(`QueryItem.history` exists); do not loosen the assertion.

- [ ] **Step 8: Update the plan shards**

Add letter `g` to `clusters/14-queries/allocation.yaml` (`max_scenario: 51`):

```yaml
  g:
    title: filter_values — a column's values with counts under the other filters
    scenarios: 14.42-14.51
    status: complete
    tests:
      pass: 10
      skip: 0
      xfail: 0
    note: >-
      New endpoint for the grid's value-list filters and filter bar. Subclasses ListModelEntries, so
      as_of, the read filter and the 14f translation are the list's own. 14.42 pins that a column's
      own filter is left out and the others narrow; 14.43 keys and labels per type; 14.44 a ticked
      value at 0; 14.45 search (database for text, labels for foreign keys); 14.46 limit and
      truncated, with ticked values kept past the cut; 14.47 order; 14.48 refusals; 14.49 as_of;
      14.50 bad keys in other filters; 14.51 the database and per-row counts agree. Field-level
      permissions are cluster 4o.
```

Append to `clusters/14-queries/batches.md`:

```markdown
## Batch 14g — filter_values: a column's values with counts (14.42-14.51)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/FilterValues.py`, `lex/api/utils/filterable_fields.py`,
  `lex/process_admin/sites/process_admin_site.py`
- **Test file:** `lex/test_project/tests/queries/test_14g_filter_values_endpoint.py`
- **Test classes:** `TestCluster14g_FilterValuesEndpoint`
- **Fixtures:** `QueryItem`, `QueryCategory`
- **Status:** complete — 10 pass
```

Append to `clusters/14-queries/cluster.md`:

```markdown
### 14g. filter_values

| # | Scenario | What We Assert | Status |
|---|----------|----------------|--------|
| 14.42 | Narrowing | A column's own filter is ignored for its list; another column's filter narrows it | ✅ |
| 14.43 | Keys and labels | Choice labels, `str(related)`, Yes/No, ISO dates, "(Blank)" | ✅ |
| 14.44 | Ticked value removed by other filters | Listed with count 0 | ✅ |
| 14.45 | Search | Text matches values in the database; foreign keys match labels | ✅ |
| 14.46 | Limit | Cut to `limit` with `truncated`; ticked values kept past the cut | ✅ |
| 14.47 | Order | Labels ascend case-insensitively; blank last | ✅ |
| 14.48 | Not filterable | Unknown, JSON, related path, serializer-only and empty fields are 400 | ✅ |
| 14.49 | as_of | Values of that instant, not of now | ✅ |
| 14.50 | Bad key in another filter | 400 naming that field | ✅ |
| 14.51 | Two counting paths | Database and per-row counts give identical answers | ✅ |
```

- [ ] **Step 9: Commit**

```bash
git add lex/api/utils/filterable_fields.py lex/api/views/model_entries/FilterValues.py lex/process_admin/sites/process_admin_site.py lex/test_project/tests/_e2e_test_case.py lex/test_project/tests/queries/test_14g_filter_values_endpoint.py lex/test_project/test-plan/clusters/14-queries
git commit -m "feat(filters): a filter_values endpoint lists a column's values with counts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Field-level read permissions on `filter_values`

**Files:**
- Modify: `lex/test_project/tests/permissions/models.py` (add `RowFieldItem`)
- Test: `lex/test_project/tests/permissions/test_4o_filter_values_permissions.py` (new)
- Plan: `lex/test_project/test-plan/clusters/04-permissions/*`
- Source: none expected. The behaviour ships in Task 2; this task proves it. If a test fails,
  fix `FilterValues._count_per_row` and say so in the commit.

**Interfaces:**
- Consumes: `url_filter_values`, the `FilterValues` view.

- [ ] **Step 1: Add the fixture model**

Append to `lex/test_project/tests/permissions/models.py`:

```python
class RowFieldItem(LexModel):
    """Cluster 4o fixture: a field hidden on some rows, and some rows hidden entirely."""

    title = models.CharField(max_length=200)
    region = models.CharField(max_length=64, blank=True, default="")
    is_private = models.BooleanField(default=False)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.title

    def permission_read(self, uc):
        if self.title.startswith("hidden"):
            return PermissionResult.deny("cluster 4o: hidden row")
        if self.is_private:
            return PermissionResult.allow_all_except(
                {"region"}, "cluster 4o: a private row hides its region",
            )
        return PermissionResult.allow_all("cluster 4o: open row")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 4o: edit-open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 2: Write the tests**

Create `lex/test_project/tests/permissions/test_4o_filter_values_permissions.py`:

```python
"""A value list never shows what the user could not read in the grid.

Intent: value lists come from a new endpoint, outside the serializer that hides
fields per row in the grid. A model's permission_read can hide a row, or one
field on some rows; a value list must count neither, and a field the user can
read on no row must give an empty list rather than an error. A field the
serializer does not expose must be refused even though the model has it. A
regression leaks values of hidden fields to anyone who opens a filter.
Cluster 4o — scenarios 4.80–4.83. Type: E.
Covers: lex/api/views/model_entries/FilterValues.py (``_filterable_field``, ``_count_per_row``).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4o_filter_values_permissions.py -v
"""

from __future__ import annotations

import pytest
from rest_framework import serializers as drf_serializers
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import FieldLevelItem, RowFieldItem

pytestmark = pytest.mark.permissions

ROWS = "rowfielditem"
FIELD_LEVEL = "fieldlevelitem"


class TestCluster04o_FilterValuesPermissions(E2ETestCase):
    """Cluster 4o: filter_values honours row and field read permissions."""

    e2e_models = [RowFieldItem, FieldLevelItem]

    def setUp(self):
        super().setUp()
        RowFieldItem.objects.create(title="open-1", region="north")
        RowFieldItem.objects.create(title="open-2", region="north")
        RowFieldItem.objects.create(title="private-1", region="north", is_private=True)
        RowFieldItem.objects.create(title="private-2", region="south", is_private=True)
        RowFieldItem.objects.create(title="hidden-1", region="west")
        FieldLevelItem.objects.create(public_name="a", sensitive_salary=10)
        FieldLevelItem.objects.create(public_name="b", sensitive_salary=20)

    def _post(self, model: str, body: dict, query: str = ""):
        return self.client.post(self.url_filter_values(model) + query, data=body, format="json")

    def _counts(self, model: str, field: str, query: str = "") -> dict:
        resp = self._post(model, {"field": field, "filterModel": {}}, query)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return {item["key"]: item["count"] for item in resp.data["values"]}

    # -- 4.80 ----------------------------------------------------------
    def test_04_80_a_field_hidden_on_some_rows_is_counted_only_where_readable(self):
        """
        Scenario 4.80: per-row field hiding
        Given: two open rows in north, two private rows (north, south) that hide region
        When: region values are asked for
        Then: only the open rows count: north 2; south never appears
        """
        self.assertEqual(self._counts(ROWS, "region"), {"north": 2})

    # -- 4.81 ----------------------------------------------------------
    def test_04_81_rows_the_user_cannot_see_are_never_counted(self):
        """
        Scenario 4.81: hidden rows stay hidden
        Given: a row whose permission_read denies it
        When: title and is_private values are asked for
        Then: the hidden row is in neither list
        """
        self.assertNotIn("hidden-1", self._counts(ROWS, "title"))
        self.assertEqual(self._counts(ROWS, "is_private"), {"false": 2, "true": 2})

    # -- 4.82 ----------------------------------------------------------
    def test_04_82_a_field_readable_on_no_row_gives_an_empty_list(self):
        """
        Scenario 4.82: nothing to show is not an error
        Given: a regular user, who may read only id and public_name
        When: sensitive_salary values are asked for
        Then: 200 with no values
        """
        resp = self._post(FIELD_LEVEL, {"field": "sensitive_salary", "filterModel": {}})
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["values"], [], "no salary may leak")

    # -- 4.83 ----------------------------------------------------------
    def test_04_83_a_field_the_serializer_does_not_expose_is_refused(self):
        """
        Scenario 4.83: the serializer decides which columns exist
        Given: a named serializer exposing only title
        When: region values are asked for through it
        Then: 400 "is not filterable", although the model has region
        """

        class TitleOnly(drf_serializers.ModelSerializer):
            class Meta:
                model = RowFieldItem
                fields = ["id", "title"]

        had = hasattr(RowFieldItem, "api_serializers")
        original = getattr(RowFieldItem, "api_serializers", None)

        def _restore():
            if had:
                RowFieldItem.api_serializers = original
            elif hasattr(RowFieldItem, "api_serializers"):
                delattr(RowFieldItem, "api_serializers")

        self.addCleanup(_restore)
        RowFieldItem.api_serializers = {"title_only": TitleOnly}

        resp = self._post(ROWS, {"field": "region", "filterModel": {}}, "?serializer=title_only")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("not filterable", str(resp.data))
```

- [ ] **Step 3: Run them**

Run: `… -m lex pytest lex/test_project/tests/permissions/test_4o_filter_values_permissions.py -v`
Expected: 4 passed. A failure in 4.80–4.82 is a leak: fix `_count_per_row`, never the test. For 4.83,
if the container does not pick up the patched `api_serializers`, read
`ModelContainer.get_serializers_map` (it refreshes on a changed signature) before changing the test.

- [ ] **Step 4: Mutation check**

Temporarily change `if not result.allowed or name not in result.get_fields({name}):` to
`if not result.allowed:`. Run 4.80 and 4.82: both must fail. Restore the line and run again.

- [ ] **Step 5: Plan shards and commit**

Append under `letters:` in `clusters/04-permissions/allocation.yaml` and set `max_scenario: 83`:

```yaml
  o:
    title: filter_values honours row and field read permissions
    scenarios: 4.80-4.83
    status: complete
    tests:
      pass: 4
      skip: 0
      xfail: 0
    note: >-
      Value lists come from filter_values, outside the serializer that hides fields per row in the
      grid. RowFieldItem hides region on private rows and denies hidden rows. 4.80 counts region
      only where readable, 4.81 never counts hidden rows, 4.82 gives an empty list for a field
      readable on no row, 4.83 refuses a field the requested serializer does not expose. Mutation:
      dropping the get_fields check from _count_per_row fails 4.80 and 4.82.
```

Append to `clusters/04-permissions/batches.md`:

```markdown
## Batch 4o — filter_values honours row and field read permissions (4.80-4.83)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/FilterValues.py`
- **Test file:** `lex/test_project/tests/permissions/test_4o_filter_values_permissions.py`
- **Test classes:** `TestCluster04o_FilterValuesPermissions`
- **Fixtures:** new `RowFieldItem`; `FieldLevelItem`
- **Status:** complete — 4 pass
```

Append to `clusters/04-permissions/cluster.md`:

```markdown
### 4o. filter_values and read permissions

| # | Scenario | What We Assert | Status |
|---|----------|----------------|--------|
| 4.80 | Field hidden on some rows | Counted only on rows that allow it | ✅ |
| 4.81 | Rows hidden by permission_read | Never counted, in any column | ✅ |
| 4.82 | Field readable on no row | 200 with an empty list | ✅ |
| 4.83 | Field the serializer does not expose | 400 "is not filterable" | ✅ |
```

```bash
git add lex/test_project/tests/permissions lex/test_project/test-plan/clusters/04-permissions
git commit -m "test(permissions): value lists never count rows or fields the user cannot read

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `filter_bar` in `list_ui`

**Files:**
- Modify: `lex/api/serializers/base_serializers.py` (`get_list_ui_options`, new `_resolve_filter_bar`)
- Modify: `lex/api/views/model_info/Fields.py` (`create_list_ui_info` fallback branch)
- Test: `lex/test_project/tests/api_layer/test_10o_list_ui_filter_bar.py` (new)
- Plan: `clusters/10-api_layer/*`

**Interfaces:**
- Consumes: `is_value_list_field` (Task 2).
- Produces: `list_ui.filter_bar: list[str]` in `GET api/model_info/<model>/fields`.

- [ ] **Step 1: Write the failing tests**

Create `lex/test_project/tests/api_layer/test_10o_list_ui_filter_bar.py`:

```python
"""The filter bar's default columns travel with the list's other options.

Intent: a developer names the columns a list shows in its filter bar, on the
serializer's Meta or, for a list without a custom serializer, on the model. The
frontend reads them from list_ui. A name that is not a value-list column must be
dropped with a warning, never sent, so the bar cannot offer a filter the
filter_values endpoint would refuse.
Cluster 10o — scenarios 10.83–10.85. Type: U.
Covers: lex/api/serializers/base_serializers.py (``get_list_ui_options``,
``_resolve_filter_bar``), lex/api/views/model_info/Fields.py (``create_list_ui_info``).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_list_ui_filter_bar.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase

from lex.api.serializers.base_serializers import LexSerializer
from lex.api.views.model_info.Fields import create_list_ui_info

from .models import SchemaItem

pytestmark = pytest.mark.api_layer


class TestCluster10o_ListUiFilterBar(SimpleTestCase):
    """Cluster 10o: list_ui.filter_bar."""

    def tearDown(self):
        if "filter_bar" in SchemaItem.__dict__:
            del SchemaItem.filter_bar
        super().tearDown()

    # -- 10.83 ---------------------------------------------------------
    def test_10_83_the_serializer_meta_names_the_bar(self):
        """
        Scenario 10.83: declared on the serializer
        Given: a serializer whose Meta sets filter_bar
        When: its list options are built
        Then: list_ui.filter_bar carries those columns, in order
        """

        class WithBar(LexSerializer):
            class Meta:
                model = SchemaItem
                fields = ["id", "name", "active"]
                filter_bar = ["active", "name"]

        self.assertEqual(create_list_ui_info(WithBar)["filter_bar"], ["active", "name"])

    # -- 10.84 ---------------------------------------------------------
    def test_10_84_a_model_attribute_is_the_fallback(self):
        """
        Scenario 10.84: declared on the model
        Given: no filter_bar on the serializer, filter_bar on the model
        When: list options are built
        Then: the model's columns are used; with neither, the bar is empty
        """

        class Plain(LexSerializer):
            class Meta:
                model = SchemaItem
                fields = ["id", "name", "day"]

        self.assertEqual(create_list_ui_info(Plain)["filter_bar"], [], "no declaration, no bar")
        SchemaItem.filter_bar = ["day"]
        self.assertEqual(create_list_ui_info(Plain)["filter_bar"], ["day"])

    # -- 10.85 ---------------------------------------------------------
    def test_10_85_names_that_are_not_value_list_columns_are_dropped(self):
        """
        Scenario 10.85: only columns filter_values accepts
        Given: filter_bar naming an unknown field, a JSON field, a related path and a real column twice
        When: list options are built
        Then: only the real column is kept, once, and each drop is logged
        """

        class Messy(LexSerializer):
            class Meta:
                model = SchemaItem
                fields = ["id", "name", "payload"]
                filter_bar = ["nope", "payload", "name", "fk__label", "name"]

        with self.assertLogs("lex.api.serializers.base_serializers", level="WARNING") as logs:
            bar = create_list_ui_info(Messy)["filter_bar"]
        self.assertEqual(bar, ["name"])
        self.assertEqual(len(logs.output), 3, f"one warning per dropped name: {logs.output!r}")
```

- [ ] **Step 2: Run them to see them fail**

Run: `… -m lex pytest lex/test_project/tests/api_layer/test_10o_list_ui_filter_bar.py -v`
Expected: `KeyError: 'filter_bar'`.

- [ ] **Step 3: Implement**

In `lex/api/serializers/base_serializers.py`, add the import (the module already has `import logging`;
add `logger = logging.getLogger(__name__)` below the imports if it is not already defined):

```python
from lex.api.utils.filterable_fields import is_value_list_field
```

Add this function at module level, above `class LexSerializer`:

```python
def _resolve_filter_bar(meta) -> list:
    """The columns a list shows in its filter bar by default.

    ``Meta.filter_bar`` on the serializer, else a ``filter_bar`` attribute on the
    model, so a list without a custom serializer can declare one. Names that are
    not value-list columns of the model are dropped with a warning.
    """
    model = getattr(meta, "model", None)
    raw = getattr(meta, "filter_bar", None)
    if raw is None and model is not None:
        raw = getattr(model, "filter_bar", None)
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]

    names: list = []
    for name in raw:
        model_field = None
        if model is not None and isinstance(name, str) and name and "__" not in name:
            try:
                model_field = model._meta.get_field(name)
            except Exception:
                model_field = None
        if model_field is None or not is_value_list_field(model_field):
            logger.warning(
                "filter_bar: %r is not a value-list column of %s; dropped",
                name, getattr(model, "__name__", model),
            )
            continue
        if name not in names:
            names.append(name)
    return names
```

Change `get_list_ui_options` to:

```python
    @classmethod
    def get_list_ui_options(cls) -> dict:
        """Expose serializer-level frontend list options via ``Meta``."""
        meta = getattr(cls, "Meta", None)
        return {
            "hide_actions_column": bool(getattr(meta, "hide_actions_column", False)),
            "filter_bar": _resolve_filter_bar(meta),
        }
```

In `lex/api/views/model_info/Fields.py`, the fallback branch of `create_list_ui_info` (serializers that
are not `LexSerializer`s, such as the user list) returns:

```python
    return {
        "hide_actions_column": bool(getattr(meta, "hide_actions_column", False)),
        "filter_bar": [],
    }
```

Note: the duplicate `"name"` in 10.85 is dropped silently (no warning), so the expected three
warnings are `nope`, `payload` and `fk__label`.

- [ ] **Step 4: Run cluster 10o and the existing list_ui tests**

Run: `… -m lex pytest lex/test_project/tests/api_layer/test_10o_list_ui_filter_bar.py lex/test_project/tests/api_layer/test_10i_fields_view_and_list_ui.py -v`
Expected: 10o 3 passed. 10i may assert `list_ui == {"hide_actions_column": ...}` exactly; if so,
update those assertions to include `"filter_bar": []`, and record that in the 10o batch note.

- [ ] **Step 5: Plan shards and commit**

Append under `letters:` in `clusters/10-api_layer/allocation.yaml` and set `max_scenario: 85`:

```yaml
  o:
    title: list_ui.filter_bar — a list's default filter-bar columns
    scenarios: 10.83-10.85
    status: complete
    tests:
      pass: 3
      skip: 0
      xfail: 0
    note: >-
      The filter bar's default columns travel in list_ui. 10.83 reads Meta.filter_bar, 10.84 falls
      back to a model attribute and defaults to an empty bar, 10.85 drops names that are not
      value-list columns (unknown, JSON, related paths) with one warning each and de-duplicates.
      The predicate is shared with filter_values (lex/api/utils/filterable_fields.py), so the bar
      cannot name a column the endpoint refuses.
```

Append to `clusters/10-api_layer/batches.md`:

```markdown
## Batch 10o — list_ui.filter_bar (10.83-10.85)

- **Type:** U
- **Files covered:** `lex/api/serializers/base_serializers.py`, `lex/api/views/model_info/Fields.py`
- **Test file:** `lex/test_project/tests/api_layer/test_10o_list_ui_filter_bar.py`
- **Test classes:** `TestCluster10o_ListUiFilterBar`
- **Fixtures:** `SchemaItem`
- **Status:** complete — 3 pass
```

Append to `clusters/10-api_layer/cluster.md`:

```markdown
### 10o. list_ui.filter_bar

| # | Scenario | What We Assert | Status |
|---|----------|----------------|--------|
| 10.83 | Declared on the serializer | `Meta.filter_bar` reaches `list_ui.filter_bar` in order | ✅ |
| 10.84 | Declared on the model | Model attribute is the fallback; none means `[]` | ✅ |
| 10.85 | Not a value-list column | Unknown, JSON and related-path names dropped with a warning each | ✅ |
```

```bash
git add lex/api/serializers/base_serializers.py lex/api/views/model_info/Fields.py lex/test_project/tests/api_layer lex/test_project/test-plan/clusters/10-api_layer
git commit -m "feat(list-ui): a list names its default filter-bar columns

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `lex_view(filters=, filter_bar=, on_filter=)` and the component

**Files:**
- Modify: `lex/lex_app/streamlit/embed.py` (imports, helpers, signature, docstring, body)
- Modify: `lex/lex_app/streamlit/_lex_view_component/__init__.py` (`render_lex_view_component`)
- Create: `lex/lex_app/streamlit/_lex_view_component/frontend/filter_sync.js`
- Modify: `lex/lex_app/streamlit/_lex_view_component/frontend/index.html`
- Test: `lex/test_project/tests/init/test_1aw_lex_view_filters.py` (new)
- Plan: `clusters/01-init/*`

**Interfaces:**
- Produces:
  - `encode_filters_token(filters: dict) -> str` in `embed.py`: base64url of compact, key-sorted JSON,
    no padding. The frontend decodes this token.
  - `normalise_filters(filters) -> dict` in `embed.py`.
  - `render_lex_view_component(..., filters=None, filters_token=None)`.
  - URL params `filter_bar=<a,b>` (empty string = hide) and `emit_filter=true`.
  - Host message `{source: "lex-app-host", version: 1, type: "filter", payload: {filters}}`.

- [ ] **Step 1: Write the failing tests**

Create `lex/test_project/tests/init/test_1aw_lex_view_filters.py`:

```python
"""lex_view sets an embed's filters, names its filter bar and hears its filter changes.

Intent: a Streamlit page passes filters to an embedded table and can change them
on a later rerun without reloading the frame, and two embeds can be linked by
passing one's selection or filters into the other. The filters therefore ride
the component's arguments, never its URL or key: the URL is the component's
identity, and changing it remounts the frame and wipes the user's grid state.
The first load carries them in the iframe URL; later changes are posted, once
the frame has loaded, only when they differ from what the frame has.
Cluster 01-init, batch 1aw — scenarios 1.393–1.399. Type: U.
Covers: lex/lex_app/streamlit/embed.py (``lex_view``, ``normalise_filters``,
``encode_filters_token``), lex/lex_app/streamlit/_lex_view_component/__init__.py,
lex/lex_app/streamlit/_lex_view_component/frontend/filter_sync.js and index.html.
Run: python -m lex pytest lex/test_project/tests/init/test_1aw_lex_view_filters.py -v
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import urllib.parse
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.init

FRONTEND = Path(__file__).resolve().parents[3] / "lex_app/streamlit/_lex_view_component/frontend"


def _decode(token: str) -> dict:
    padded = token + "=" * (-len(token) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())


def _capture_component(monkeypatch) -> dict:
    from lex.lex_app.streamlit import embed as embed_mod

    captured: dict = {}
    monkeypatch.setattr(
        embed_mod, "render_lex_view_component",
        lambda **kwargs: captured.update(kwargs) or None,
    )
    return captured


def _query(url: str) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True)


class TestCluster1aw_LexViewFilters:
    """Cluster 1aw: lex_view filters, filter_bar and on_filter."""

    def test_01_393_filters_go_to_the_component_not_the_url(self, monkeypatch):
        """
        Scenario 1.393: the component's identity does not change with the filters
        Given: lex_view with filters
        When: it renders
        Then: the URL carries no filters; the component gets them normalised and as a token
        """
        from lex.lex_app.streamlit.embed import lex_view

        captured = _capture_component(monkeypatch)
        lex_view("invoice", filters={"status": ["open", "closed"], "issued": date(2026, 3, 1), "region": None})

        assert "filters" not in _query(captured["url"]), "filters in the URL would remount the frame"
        assert captured["filters"] == {"status": ["open", "closed"], "issued": "2026-03-01", "region": None}
        assert _decode(captured["filters_token"]) == captured["filters"], "the token is the same filters"

    def test_01_394_filters_alone_select_the_component(self, monkeypatch):
        """
        Scenario 1.394: only the component can post a later change
        Given: lex_view with filters and no on_* flag
        When: it renders
        Then: the component renders, not a plain iframe
        """
        from lex.lex_app.streamlit import embed as embed_mod

        captured = _capture_component(monkeypatch)
        monkeypatch.setattr(embed_mod.components, "iframe", lambda *a, **k: pytest.fail("plain iframe used"))
        embed_mod.lex_view("invoice", filters={"status": "open"})
        assert captured["filters"] == {"status": "open"}

    def test_01_395_filter_bar_rides_the_url(self, monkeypatch):
        """
        Scenario 1.395: the bar's columns
        Given: filter_bar as a list, as an empty list, and absent
        When: lex_view renders
        Then: filter_bar=a,b; filter_bar= (hide); no parameter at all
        """
        from lex.lex_app.streamlit import embed as embed_mod

        captured = _capture_component(monkeypatch)
        embed_mod.lex_view("invoice", filter_bar=["status", "region"], on_select=True)
        assert _query(captured["url"])["filter_bar"] == ["status,region"]
        embed_mod.lex_view("invoice", filter_bar=[], on_select=True)
        assert _query(captured["url"])["filter_bar"] == [""], "an empty bar is sent, and means hide"
        embed_mod.lex_view("invoice", on_select=True)
        assert "filter_bar" not in _query(captured["url"])

    def test_01_396_on_filter_opts_in_to_filter_events(self, monkeypatch):
        """
        Scenario 1.396: the event is opt-in, like every other
        Given: on_filter=True
        When: lex_view renders
        Then: emit_filter=true and the component renders
        """
        from lex.lex_app.streamlit.embed import lex_view

        captured = _capture_component(monkeypatch)
        lex_view("invoice", on_filter=True)
        assert _query(captured["url"])["emit_filter"] == ["true"]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"filters": ["status"]},
            {"filters": {1: ["open"]}},
            {"filters": {"status": [object()]}},
            {"filter_bar": "status"},
            {"filter_bar": ["status", 3]},
        ],
    )
    def test_01_397_a_malformed_argument_is_refused_at_the_call(self, monkeypatch, kwargs):
        """
        Scenario 1.397: mistakes surface in the line that made them
        Given: filters that are not a dict of names to values, or a filter_bar that is not a list of names
        When: lex_view is called
        Then: TypeError, before anything renders
        """
        from lex.lex_app.streamlit.embed import lex_view

        _capture_component(monkeypatch)
        with pytest.raises(TypeError):
            lex_view("invoice", **kwargs)

    def test_01_398_changing_filters_keeps_the_same_url(self, monkeypatch):
        """
        Scenario 1.398: a rerun with new filters is not a new frame
        Given: two renders that differ only in their filters
        When: both render
        Then: the URL handed to the component is identical; the tokens differ
        """
        from lex.lex_app.streamlit.embed import lex_view

        captured = _capture_component(monkeypatch)
        lex_view("invoice", filters={"status": ["open"]})
        first_url, first_token = captured["url"], captured["filters_token"]
        lex_view("invoice", filters={"status": ["closed"]})
        assert captured["url"] == first_url
        assert captured["filters_token"] != first_token

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_01_399_the_shim_posts_a_changed_filter_only_after_load(self):
        """
        Scenario 1.399: when the component posts
        Given: the shim's decision function
        When: it is asked with the frame loading, loaded, unchanged, cleared and changed
        Then: it posts only for a loaded frame whose named filters differ from the frame's
        """
        script = FRONTEND / "filter_sync.js"
        program = (
            f"const s = require({json.dumps(str(script))});"
            "const cases = ["
            "[{loaded: false, desired: 'a', current: null}, false],"
            "[{loaded: true, desired: 'a', current: null}, true],"
            "[{loaded: true, desired: 'a', current: 'a'}, false],"
            "[{loaded: true, desired: null, current: 'a'}, false],"
            "[{loaded: true, desired: 'b', current: 'a'}, true],"
            "];"
            "console.log(JSON.stringify(cases.map(([state, want]) => s.shouldPost(state) === want)));"
        )
        out = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout
        assert json.loads(out) == [True] * 5, out

        html = (FRONTEND / "index.html").read_text()
        assert '<script src="filter_sync.js"></script>' in html, "the shim loads the decision function"
        assert 'type: "filter"' in html, "the shim posts filter messages"
        assert "LexViewFilterSync.shouldPost" in html, "and uses the tested function to decide"
```

- [ ] **Step 2: Run them to see them fail**

Run: `… -m lex pytest lex/test_project/tests/init/test_1aw_lex_view_filters.py -v`
Expected: `TypeError: lex_view() got an unexpected keyword argument 'filters'` (and `filter_bar`,
`on_filter`); 1.399 fails because `filter_sync.js` does not exist.

- [ ] **Step 3: The decision function**

Create `lex/lex_app/streamlit/_lex_view_component/frontend/filter_sync.js`:

```js
/*
 * When the lex_view shim posts the host's filters into the embedded app.
 *
 * Kept apart from index.html so it can be run and tested on its own. The first
 * load carries the filters in the iframe URL; after that a change is POSTED, so
 * the frame keeps its state instead of reloading. A post happens only once the
 * frame has loaded, only when the host named filters, and only when they differ
 * from the ones the frame already has: feeding an embed's own filter event back
 * into lex_view(filters=...) must settle, not loop.
 */
(function (root) {
  function shouldPost(state) {
    if (!state || !state.loaded) return false;
    if (state.desired === null || state.desired === undefined) return false;
    var current = state.current === undefined ? null : state.current;
    return state.desired !== current;
  }

  var api = { shouldPost: shouldPost };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.LexViewFilterSync = api;
  }
})(this);
```

- [ ] **Step 4: The shim**

In `index.html`, add `<script src="filter_sync.js"></script>` on the line before the existing
`<script>`. Inside the existing script, after `let _rendered = false;`, add:

```js
    // ── Host filters (see filter_sync.js) ────────────────────────────
    let _frameLoaded = false;
    let _desiredFilters = null;
    let _desiredFiltersToken = null;
    let _frameFiltersToken = null;

    function withFiltersParam(url, token) {
      if (!token) return url;
      try {
        const u = new URL(url, window.location.href);
        u.searchParams.set("filters", token);
        return u.toString();
      } catch (e) {
        return url;
      }
    }

    function postFiltersToFrame(frame) {
      let childOrigin = null;
      try {
        childOrigin = new URL(frame.dataset.url, window.location.href).origin;
      } catch (e) {
        /* relative/opaque URL — never post with "*" */
      }
      if (!frame.contentWindow || !childOrigin) return;
      frame.contentWindow.postMessage(
        {
          source: HOST_SOURCE_TAG,
          version: PROTOCOL_VERSION,
          type: "filter",
          payload: { filters: _desiredFilters },
        },
        childOrigin
      );
      _frameFiltersToken = _desiredFiltersToken;
    }

    function syncFilters(frame) {
      const post = LexViewFilterSync.shouldPost({
        loaded: _frameLoaded,
        desired: _desiredFiltersToken,
        current: _frameFiltersToken,
      });
      if (post) postFiltersToFrame(frame);
    }
```

Replace the navigation block in the render handler:

```js
      if (!_rendered || frame.dataset.url !== url) {
        frame.src = url;
        frame.dataset.url = url;
        _rendered = true;
        // Announce the host theme once the embedded app is loaded.
        frame.addEventListener("load", () => postThemeToFrame(frame), { once: true });
      }
```

with:

```js
      _desiredFilters = args.filters || null;
      _desiredFiltersToken = args.filters_token || null;

      if (!_rendered || frame.dataset.url !== url) {
        // The first load carries the filters in the URL; `dataset.url` keeps
        // the URL WITHOUT them, so a filter change is never a navigation.
        frame.src = withFiltersParam(url, _desiredFiltersToken);
        frame.dataset.url = url;
        _frameFiltersToken = _desiredFiltersToken;
        _frameLoaded = false;
        _rendered = true;
        frame.addEventListener("load", () => {
          _frameLoaded = true;
          postThemeToFrame(frame);
          syncFilters(frame);
        }, { once: true });
      } else {
        syncFilters(frame);
      }
```

- [ ] **Step 5: The Python side**

In `lex/lex_app/streamlit/_lex_view_component/__init__.py`, give `render_lex_view_component` two more
keyword arguments and pass them through:

```python
def render_lex_view_component(
    *,
    url: str,
    height: int = 800,
    expected_origin: Optional[str] = None,
    key: Optional[str] = None,
    filters: Optional[dict] = None,
    filters_token: Optional[str] = None,
) -> Optional[dict]:
```

Add to its docstring's Parameters:

```
    filters : dict, optional
        The host's filters, already normalised by ``lex_view``. Posted into the
        frame when they change after it loaded.
    filters_token : str, optional
        The same filters as a URL-safe token; added to the iframe URL on first
        load, and compared to decide whether a change must be posted.
```

and call:

```python
    return _ensure_declared()(
        url=url,
        height=height,
        expected_origin=expected_origin,
        filters=filters,
        filters_token=filters_token,
        key=key,
        default=None,
    )
```

The default `key` stays `f"lex_view:{url}"`: `url` never contains the filters.

In `lex/lex_app/streamlit/embed.py`:

Add imports:

```python
import base64
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from collections.abc import Mapping
```

Add these helpers above `def lex_view`:

```python
def _filter_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    raise TypeError(
        f"lex_view(filters=...) values must be strings, numbers, booleans, dates or None; got {value!r}"
    )


def normalise_filters(filters: Any) -> Dict[str, Any]:
    """``filters`` as JSON-ready data: field name to a list of values, one value, or None."""
    if not isinstance(filters, Mapping):
        raise TypeError("lex_view(filters=...) must be a dict of field name to values")
    normalised: Dict[str, Any] = {}
    for field, value in filters.items():
        if not isinstance(field, str) or not field:
            raise TypeError(f"lex_view(filters=...) keys must be field names; got {field!r}")
        if isinstance(value, (list, tuple)):
            normalised[field] = [_filter_scalar(item) for item in value]
        elif isinstance(value, (set, frozenset)):
            normalised[field] = sorted((_filter_scalar(item) for item in value), key=repr)
        else:
            normalised[field] = _filter_scalar(value)
    return normalised


def encode_filters_token(filters: Dict[str, Any]) -> str:
    """Base64url of compact, key-sorted JSON: one URL-safe token per filter state."""
    raw = json.dumps(filters, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _filter_bar_param(filter_bar: Any) -> str:
    if isinstance(filter_bar, str) or not isinstance(filter_bar, (list, tuple)):
        raise TypeError("lex_view(filter_bar=...) must be a list of field names")
    if not all(isinstance(name, str) and name for name in filter_bar):
        raise TypeError("lex_view(filter_bar=...) must be a list of field names")
    return ",".join(filter_bar)
```

Add three keyword parameters to `lex_view`, after `on_flow_step: bool = False,`:

```python
    on_filter: bool = False,
    filters: Optional[Dict[str, Any]] = None,
    filter_bar: Optional[List[str]] = None,
```

Document them in the docstring's Parameters, after the `on_*` block:

```
    on_filter : bool
        Report filter changes as ``"filter"`` events. Payload: ``resource``,
        ``filters`` (field → ticked keys), ``filter_model`` (the grid's whole
        model) and ``row_count``.
    filters : dict, optional
        Field name to stored values: a list, one value, or ``None`` to clear
        the field. An empty list matches no rows. Applied on first load and,
        when changed on a later rerun, posted into the frame without reloading
        it. Filters on fields not named here, set by the user, stay.
    filter_bar : list of str, optional
        The columns shown in the embed's filter bar; ``[]`` hides the bar.
```

In the body, validate early (before `params` is built) so a bad call fails before rendering:

```python
    filters_payload = normalise_filters(filters) if filters is not None else None
    filter_bar_value = _filter_bar_param(filter_bar) if filter_bar is not None else None
```

After the `serializer` block, add:

```python
    if filter_bar_value is not None:
        params["filter_bar"] = [filter_bar_value]
```

After `if on_flow_step:` add:

```python
    if on_filter:
        params["emit_filter"] = ["true"]
```

Change the render decision and the component call:

```python
    callbacks_requested = any(
        (on_create, on_update, on_delete, on_select, on_navigate, on_flow_step, on_filter)
    )

    if callbacks_requested or filters_payload is not None:
        ...
        return render_lex_view_component(
            url=final_url,
            height=height,
            expected_origin=expected_origin,
            key=key,
            filters=filters_payload,
            filters_token=encode_filters_token(filters_payload) if filters_payload is not None else None,
        )
```

`urlencode(params, doseq=True)` writes `filter_bar=` for an empty bar, which is the "hide" signal.

- [ ] **Step 6: Run the tests to see them pass**

Run: `… -m lex pytest lex/test_project/tests/init/test_1aw_lex_view_filters.py lex/test_project/tests/init/test_1ao_flow_composable_steps.py lex/test_project/tests/init/test_1af_theme_switch_preserves_brand.py -v`
Expected: 1aw all pass (1.399 needs `node`; it is installed here), and 1ao and 1af still pass.

- [ ] **Step 7: Plan shards and commit**

Append under `letters:` in `clusters/01-init/allocation.yaml` and set `max_scenario: 399`:

```yaml
  aw:
    title: lex_view filters, filter_bar and on_filter
    scenarios: 1.393-1.399
    status: complete
    tests:
      pass: 11
      skip: 0
      xfail: 0
    note: >-
      lex_view(filters=, filter_bar=, on_filter=). Filters ride the component's arguments, never its
      URL or key, because the URL is the component's identity and a changed URL remounts the frame.
      1.393-1.398 pin that, the component choice, the filter_bar and emit_filter parameters, and
      refusals at the call. 1.399 runs the shim's decision function under node (skipped without
      node): post only to a loaded frame, only named filters, only when they differ.
```

(`pass: 11` counts the five parametrised cases of 1.397. Write the number pytest reports.)

Append to `clusters/01-init/batches.md`:

```markdown
## Batch 1aw — lex_view filters, filter_bar and on_filter (1.393-1.399)

- **Type:** U
- **Files covered:** `lex/lex_app/streamlit/embed.py`, `lex/lex_app/streamlit/_lex_view_component/__init__.py`,
  `lex/lex_app/streamlit/_lex_view_component/frontend/filter_sync.js`, `…/frontend/index.html`
- **Test file:** `lex/test_project/tests/init/test_1aw_lex_view_filters.py`
- **Test classes:** `TestCluster1aw_LexViewFilters`
- **Fixtures:** none
- **Status:** complete
```

Append to `clusters/01-init/cluster.md`:

```markdown
### 1aw. lex_view filters

| # | Scenario | What We Assert | Status |
|---|----------|----------------|--------|
| 1.393 | Filters reach the component | Not in the URL; normalised dict and matching token as arguments | ✅ |
| 1.394 | Filters select the component | No plain iframe when `filters` is given | ✅ |
| 1.395 | filter_bar | `a,b`; empty means hide; absent sends nothing | ✅ |
| 1.396 | on_filter | `emit_filter=true` | ✅ |
| 1.397 | Malformed arguments | TypeError at the call | ✅ |
| 1.398 | A filter change is not a new frame | Same URL, different token | ✅ |
| 1.399 | When the shim posts | Loaded, named and changed only; index.html uses the function | ✅ |
```

If #810 has merged and taken `aw`, use the next free letter, and renumber the scenarios here and in
the test file.

```bash
git add lex/lex_app/streamlit lex/test_project/tests/init/test_1aw_lex_view_filters.py lex/test_project/test-plan/clusters/01-init
git commit -m "feat(streamlit): lex_view sets an embed's filters and hears them change

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Session fragment, dashboard, full regression, PR

**Files:**
- Create: `lex/test_project/test-plan/progress/sessions/2026-10-14-multi-value-filters.md`
- Regenerate: `lex/test_project/test-plan/progress/dashboard.md`

- [ ] **Step 1: Regression**

1. Run the four touched clusters: `… -m lex pytest lex/test_project/tests/queries lex/test_project/tests/permissions lex/test_project/tests/api_layer lex/test_project/tests/init -q`.
2. Then the full suite: `… -m lex pytest lex/test_project/tests -q -p no:cacheprovider`.
3. Compare its failures with a run on `origin/lex-app-v2`.
   - The known baseline: cluster-15 calculation_logging, Reflex init without the package, stress
     timing, and the gate self-test.
   - Anything new is yours to fix.
4. Note the four counts (pass / fail / skip / xfail) of both runs.

- [ ] **Step 2: Session fragment, with the counts from Step 1**

Create `lex/test_project/test-plan/progress/sessions/2026-10-14-multi-value-filters.md`.
- `suite_tally` is one line: the five batches' pass counts as pytest reported them, then the full
  suite's pass / fail / skip / xfail, then a statement that every failure is in the baseline
  categories, which you checked in Step 1.
- `tests_added` is the total number of new test cases pytest collected.

```markdown
---
date: 2026-10-14
clusters: [1, 4, 10, 14]
tests_added: 34
suite_tally: "14f 6 pass, 14g 10 pass, 4o 4 pass, 10o 3 pass, 1aw 11 pass; full suite 1712 pass / 37 fail / 27 skip / 6 xfail, every failure in the baseline categories"
---

# Multi-value filters — backend and Streamlit

Value-list filters for the grid and its embeds. [14f](../../clusters/14-queries/batches.md) fixes how
Set and Multi models are translated; [14g](../../clusters/14-queries/batches.md) adds `filter_values`;
[4o](../../clusters/04-permissions/batches.md) proves it never counts what a user cannot read;
[10o](../../clusters/10-api_layer/batches.md) adds `list_ui.filter_bar`;
[1aw](../../clusters/01-init/batches.md) adds `lex_view(filters=, filter_bar=, on_filter=)`.
```

The numbers in that example show the shape; write the ones Step 1 measured.

- [ ] **Step 3: Dashboard**

Run: `/home/syscall/Documents/lex/.venv-test/bin/python .github/scripts/test_plan_aggregates.py build`
then `… test_plan_aggregates.py check` and `… validate`. `validate` has a known baseline of 79
problems; the count must not grow.

- [ ] **Step 4: Commit and open the PR**

```bash
git add lex/test_project/test-plan/progress
git commit -m "docs(test-plan): multi-value filters session and dashboard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/multi-value-filters
```

Open a PR against `lex-app-v2`, titled "feat(filters): value-list filters, filter_values and lex_view filters". The body:
- covers what changed and why, the endpoint contract, the `lex_view` parameters and the test
  numbers;
- links the spec;
- ends with the Claude Code line.

---

### Task 7: Documentation in `lex-app-docs`

**Files (in a clone of `ExcellenceCloudGmbH/lex-app-docs`, branch `docs/multi-value-filters`):**
- `content/using-the-app/the-grid/filtering and sorting.md`
- `content/access-and-dashboards/streamlit/embedding app pages.md`
- `content/model-your-data/serializers.md`

Do this after the two code PRs are open, so the docs describe what ships. lex-app-docs rejects a
second push to an open PR's branch, so finish all three pages before the one push.

- [ ] **Step 1: Filtering and sorting**
  - Add a *Value lists* section: ticking several values per column, the row counts, how the lists
    narrow under the other filters, and that a ticked value stays at 0.
  - Add a *Filter bar* section: where it sits, adding a column from the column menu, removing a
    column (the filter stays), and saved views remembering the bar.
  - Correct two current claims:
    - The foreign-key filter becomes a value list; it was single-value.
    - Filters wait for **Apply**; they do not filter as you type.
  - Use generic examples only (invoices, regions).

- [ ] **Step 2: Embedding app pages**
  - Add `filters`, `filter_bar` and `on_filter` to the parameter tables.
  - Add `filter` to the event table, with its payload: `resource`, `filters`, `filter_model`,
    `row_count`.
  - Add a *Linking two tables* section with the order / order-line example from the spec, and
    explain that a changed `filters` updates the embed without a reload.

- [ ] **Step 3: Serializers**
  - Document `Meta.filter_bar`, the model-attribute fallback, and that names which are not value-list
    columns are dropped with a warning.

- [ ] **Step 4: Verify against the source and open the PR**
  - Check every parameter name, default and payload key against the merged code (`embed.py`,
    `FilterValues.py`, `base_serializers.py`).
  - Push once, then open the PR. The mirror sync brings the pages into lex-app's `docs/`.
