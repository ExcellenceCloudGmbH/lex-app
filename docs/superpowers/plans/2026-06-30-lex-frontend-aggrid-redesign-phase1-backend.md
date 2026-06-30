# LEX Frontend AG Grid Redesign — Phase 1 (Backend Foundations) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **REQUIRED SUB-SKILL (per task):** Every task here adds framework source under `lex/` and a paired cluster test. Before writing each test, follow the `lex-testing` skill: Step 0 intent research, Step 1 read the test-plan, Step 3 allocate the **next free letter** for the named cluster, Step 5 print the allocation block and get confirmation, Step 7 sync the plan files. **This document recommends a cluster per task but does NOT pre-allocate letters/scenario IDs** — that happens at execution against the live `test-writing-plan.md`.

**Goal:** Add the backend metadata + serialization primitives the frontend redesign depends on — foreign-key human labels, per-field format specs, FK-display hints, and `preview`-serializer availability — without changing any existing response value (non-breaking).

**Architecture:** Three backend touch-points. (1) `LexModel` gains two opt-in class-level declarations (`lex_fk_label_field`, `lex_field_formats`). (2) The auto/wrapped serializers (`base_serializers.py`) emit a sibling `"<fk>_label"` string next to each existing FK primary key — the FK value itself stays the PK so filter/sort/edit/group/SSRM are untouched. (3) The `/fields/` metadata endpoint (`Fields.py`) gains a per-column `format` spec and, for FK columns, an `fk_label_field` + `fk_preview` hint. The `preview` serializer needs **no new wiring** — `api_serializers["preview"]` already flows through `get_serializer_map_for_model` and `?serializer=preview`; Phase 1 only *advertises* its availability.

**Tech Stack:** Django + DRF, `coverage.py`, lex cluster-testing (`python -m lex pytest`), pytest markers per cluster.

**Source of truth:** [`docs/superpowers/specs/2026-06-30-lex-frontend-aggrid-redesign-design.md`](../specs/2026-06-30-lex-frontend-aggrid-redesign-design.md) §4.2, §4.3, §5.

---

## Cross-cutting constraints (read before any task)

- **Non-breaking is the prime directive.** Every existing key in every response keeps its current value and type. We only *add* keys (`"<fk>_label"`, `info["format"]`, `info["fk_label_field"]`, `info["fk_preview"]`). The FK cell value stays the PK.
- **No theme/UI work here** — this phase is backend only.
- **Opt-in declarations.** A model with no `lex_fk_label_field` and no `lex_field_formats` must produce byte-identical metadata to today, except that FK columns gain `fk_label_field: None` and `fk_preview: False` and FK rows gain `"<fk>_label"` derived from `str(obj)`. Confirm this with an explicit "defaults" test in each task.
- **Test placement is strict** (lex-testing skill): files go in `lex/test_project/tests/<cluster_slug>/`. Recommended homes: serializer behaviour → **cluster 12** (`serializers/`); `/fields/` endpoint → **cluster 10** (`api_layer/`, alongside `test_10e_*`/`test_10i_*`). Prefer **U** (`SimpleTestCase`, no DB) using the `MagicMock(spec=...)` + `SimpleNamespace` style already established in `test_10i_fields_view_and_list_ui.py`.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `lex/core/models/LexModel.py` | Base model — declares the two opt-in hints + documents them | Modify (add 2 class attrs) |
| `lex/api/serializers/base_serializers.py` | Serializers — resolve FK label, inject `"<fk>_label"` sibling | Modify (add `resolve_fk_label`, `_inject_fk_labels`, `to_representation` hook) |
| `lex/api/views/model_info/Fields.py` | `/fields/` metadata — FK hint + `fk_preview` + per-field `format` | Modify (`create_field_info`, `Fields.get`, new `_target_has_preview_serializer`) |
| `lex/test_project/tests/serializers/test_<NN><L>_fk_label.py` | Tests for Task 1 + Task 2 | Create (cluster 12) |
| `lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py` | Tests for Task 3 + Task 4 | Create (cluster 10) |

---

## Task 1: FK label resolution helper + model hints

**Files:**
- Modify: `lex/core/models/LexModel.py` (add class attrs to the `LexModel` base)
- Modify: `lex/api/serializers/base_serializers.py` (add `resolve_fk_label` near the FK handling, ~after the imports block)
- Test: `lex/test_project/tests/serializers/test_<NN><L>_fk_label.py` (cluster 12, type **U**)

- [ ] **Step 1: Write the failing test**

```python
"""Foreign-key label resolution + model display hints.

Intent: FK cells must be able to show a human label (the target model's
declared ``lex_fk_label_field``) instead of a bare PK, while the FK value
itself stays the PK. A regression here means FK chips render blank or fall
back to opaque ids, which is exactly the UX the redesign removes.
Cluster 12<L> — scenarios 12.<X>–12.<Y>. Type: U.
Covers: lex/api/serializers/base_serializers.py (resolve_fk_label),
        lex/core/models/LexModel.py (lex_fk_label_field, lex_field_formats).
Run: python -m lex pytest lex/test_project/tests/serializers/test_<NN><L>_fk_label.py -v
"""

import pytest
from django.test import SimpleTestCase

from lex.api.serializers.base_serializers import resolve_fk_label

pytestmark = pytest.mark.serializers


class TestCluster12L_ResolveFkLabel(SimpleTestCase):
    """Cluster 12<L>: resolve_fk_label honours lex_fk_label_field, else str()."""

    def test_none_returns_none(self):
        """Scenario 12.X: a null relation resolves to None (no label)."""
        self.assertIsNone(resolve_fk_label(None), "None relation must yield no label")

    def test_falls_back_to_str_without_hint(self):
        """Scenario 12.X+1: with no lex_fk_label_field, label == str(obj)."""
        class Target:
            def __str__(self):
                return "STR-FORM"
        self.assertEqual(
            resolve_fk_label(Target()), "STR-FORM",
            "Without a hint the label must fall back to str(obj)",
        )

    def test_uses_declared_label_field(self):
        """Scenario 12.X+2: lex_fk_label_field selects the label column."""
        class Target:
            lex_fk_label_field = "name"
            name = "Fund Alpha"
            def __str__(self):
                return "wrong"
        self.assertEqual(
            resolve_fk_label(Target()), "Fund Alpha",
            "Declared label field must win over __str__",
        )

    def test_blank_label_field_value_falls_back_to_str(self):
        """Scenario 12.X+3: a None value on the label field falls back to str(obj)."""
        class Target:
            lex_fk_label_field = "name"
            name = None
            def __str__(self):
                return "STR-FALLBACK"
        self.assertEqual(
            resolve_fk_label(Target()), "STR-FALLBACK",
            "A null label-field value must fall back to str(obj)",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m lex pytest lex/test_project/tests/serializers/test_<NN><L>_fk_label.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_fk_label'`.

- [ ] **Step 3: Add the helper and model hints**

In `lex/api/serializers/base_serializers.py`, add near the top-level helpers (after the imports / constants block, before `model2serializer`):

```python
def resolve_fk_label(related_obj):
    """Human-readable label for a foreign-key target object.

    Uses the target model's declared ``lex_fk_label_field`` when set and the
    value is non-null; otherwise falls back to ``str(obj)``. Returns ``None``
    for a null relation. The FK cell value itself stays the PK — this is a
    *sibling* display string only.
    """
    if related_obj is None:
        return None
    label_field = getattr(type(related_obj), "lex_fk_label_field", None)
    if label_field:
        value = getattr(related_obj, label_field, None)
        if value is not None:
            return str(value)
    return str(related_obj)
```

In `lex/core/models/LexModel.py`, declare the two opt-in hints on the `LexModel` base class (find the `class LexModel(...)` body and add, with a docstring comment):

```python
    # --- Frontend redesign hints (opt-in, non-breaking) -------------------
    # Field name on THIS model used as its human label when it is the target
    # of a foreign key elsewhere. None → fall back to str(obj). Consumed by
    # ``resolve_fk_label`` (serializer) and ``/fields/`` FK metadata.
    lex_fk_label_field = None
    # Per-field display format specs surfaced in ``/fields/`` metadata, e.g.
    # {"revenue": {"format": "currency", "currency": "EUR", "decimals": 2}}.
    # Frontend builds an AG-Grid valueFormatter from these (hybrid defaults).
    lex_field_formats = {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m lex pytest lex/test_project/tests/serializers/test_<NN><L>_fk_label.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add lex/api/serializers/base_serializers.py lex/core/models/LexModel.py \
        lex/test_project/tests/serializers/test_<NN><L>_fk_label.py
git commit -m "feat(frontend-redesign): add FK label resolution + model display hints (phase 1)"
```

---

## Task 2: Serializer emits `"<fk>_label"` sibling

**Files:**
- Modify: `lex/api/serializers/base_serializers.py` — add `to_representation` + `_inject_fk_labels` on `RestApiModelSerializerTemplate` (class starts at line 617)
- Test: append to `lex/test_project/tests/serializers/test_<NN><L>_fk_label.py` (same cluster-12 file, type **U**)

- [ ] **Step 1: Write the failing test**

Append to the test file from Task 1:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock
from django.db.models import ForeignKey, IntegerField

from lex.api.serializers.base_serializers import RestApiModelSerializerTemplate


def _fk_field(name):
    f = MagicMock(spec=ForeignKey)
    f.name = name
    return f


def _plain_field(name):
    f = MagicMock(spec=IntegerField)
    f.name = name
    return f


class TestCluster12L_InjectFkLabels(SimpleTestCase):
    """Cluster 12<L>: to_representation adds '<fk>_label' next to each FK PK."""

    def _meta_with(self, fields):
        meta = MagicMock()
        meta.concrete_fields = fields
        return meta

    def test_label_added_for_present_fk(self):
        """Scenario 12.Y+1: a 'fund_label' sibling appears next to 'fund' PK."""
        related = SimpleNamespace(__str__=lambda self=None: "Fund Alpha")
        instance = SimpleNamespace(fund=related)
        # Bind the unbound helper to a lightweight object carrying a model meta.
        carrier = SimpleNamespace(
            Meta=SimpleNamespace(model=SimpleNamespace(
                _meta=self._meta_with([_fk_field("fund"), _plain_field("amount")]))))
        rep = {"fund": 7, "amount": 100}
        RestApiModelSerializerTemplate._inject_fk_labels(carrier, rep, instance)
        self.assertEqual(rep["fund"], 7, "FK value must stay the PK (non-breaking)")
        self.assertEqual(rep["fund_label"], "Fund Alpha", "Label sibling must be added")
        self.assertNotIn("amount_label", rep, "Non-FK columns get no label")

    def test_no_label_when_fk_absent_from_representation(self):
        """Scenario 12.Y+2: a filtered-out FK gets no label (respects visibility)."""
        instance = SimpleNamespace(fund=SimpleNamespace())
        carrier = SimpleNamespace(
            Meta=SimpleNamespace(model=SimpleNamespace(
                _meta=self._meta_with([_fk_field("fund")]))))
        rep = {"amount": 100}  # 'fund' was removed by permission filtering
        RestApiModelSerializerTemplate._inject_fk_labels(carrier, rep, instance)
        self.assertNotIn("fund_label", rep, "No label for a hidden FK column")

    def test_null_relation_label_is_none(self):
        """Scenario 12.Y+3: a null FK yields fund_label == None, PK stays None."""
        instance = SimpleNamespace(fund=None)
        carrier = SimpleNamespace(
            Meta=SimpleNamespace(model=SimpleNamespace(
                _meta=self._meta_with([_fk_field("fund")]))))
        rep = {"fund": None}
        RestApiModelSerializerTemplate._inject_fk_labels(carrier, rep, instance)
        self.assertIsNone(rep["fund_label"], "Null relation → null label")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m lex pytest lex/test_project/tests/serializers/test_<NN><L>_fk_label.py -v -k InjectFkLabels`
Expected: FAIL — `AttributeError: ... has no attribute '_inject_fk_labels'`.

- [ ] **Step 3: Add the injection to `RestApiModelSerializerTemplate`**

In `lex/api/serializers/base_serializers.py`, inside `class RestApiModelSerializerTemplate(LexSerializer):` (line 617), add (after `get_short_description`, ~line 625):

```python
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # Respect deny-all (LexSerializer returns {} when permission denies).
        if isinstance(representation, dict) and representation:
            self._inject_fk_labels(representation, instance)
        return representation

    def _inject_fk_labels(self, representation, instance):
        """Add a sibling ``"<fk>_label"`` string for every FK present in the
        representation. The FK value itself is left untouched (stays the PK),
        so filter/sort/edit/group/SSRM are unaffected. Skips FKs that were
        removed by visibility filtering (only adds a label when the FK key is
        still present) and never overwrites an explicitly declared label.
        """
        meta = getattr(getattr(self, "Meta", None), "model", None)
        model = meta if meta is not None else type(instance)
        for field in model._meta.concrete_fields:
            if not isinstance(field, ForeignKey):
                continue
            fk_name = field.name
            if fk_name not in representation:
                continue
            label_key = f"{fk_name}_label"
            if label_key in representation:
                continue
            representation[label_key] = resolve_fk_label(getattr(instance, fk_name, None))
```

> `ForeignKey` is already imported in this module (used at line 493). `resolve_fk_label` is defined in Task 1.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m lex pytest lex/test_project/tests/serializers/test_<NN><L>_fk_label.py -v`
Expected: PASS (7 passed total).

- [ ] **Step 5: Integration check — confirm a real serializer round-trip is non-breaking**

Run the existing serializer cluster to prove no regression:
Run: `python -m lex pytest lex/test_project/tests/serializers/ -v`
Expected: all previously-passing tests still PASS (FK keys unchanged; only `_label` siblings added).

- [ ] **Step 6: Commit**

```bash
git add lex/api/serializers/base_serializers.py \
        lex/test_project/tests/serializers/test_<NN><L>_fk_label.py
git commit -m "feat(frontend-redesign): emit '<fk>_label' sibling in serializer output (phase 1)"
```

---

## Task 3: `/fields/` FK display hint + `fk_preview` availability

**Files:**
- Modify: `lex/api/views/model_info/Fields.py` — `create_field_info` FK branch (lines 70-72) + new helper `_target_has_preview_serializer`
- Test: `lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py` (cluster 10, type **U**)

- [ ] **Step 1: Write the failing test**

```python
"""'/fields/' metadata: FK display hint, preview availability, format spec.

Intent: the React column builder reads /fields/ to decide how to render each
column. FK columns must advertise (a) which target field is the human label
and (b) whether the target has a curated 'preview' serializer for the hover
card; every column must be able to carry a display 'format' spec. A
regression silently reverts FK chips to raw ids or drops currency/percent
formatting. None of this needs a DB.
Cluster 10<L> — scenarios 10.<X>–10.<Y>. Type: U.
Covers: lex/api/views/model_info/Fields.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py -v
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from django.db.models import ForeignKey, FloatField
from django.test import SimpleTestCase

from lex.api.views.model_info.Fields import (
    create_field_info,
    _target_has_preview_serializer,
)

pytestmark = pytest.mark.api_layer


class TestCluster10L_FkDisplayHints(SimpleTestCase):
    """Cluster 10<L>: FK field info exposes fk_label_field + fk_preview."""

    def _fk(self, target):
        f = MagicMock(spec=ForeignKey)
        f.name = "fund"
        f.verbose_name = "fund"
        f.editable = True
        f.null = False
        f.primary_key = False
        f.get_default = MagicMock(return_value=None)
        f.remote_field = SimpleNamespace(model=target, limit_choices_to=None)
        return f

    def test_fk_exposes_declared_label_field_and_preview_true(self):
        """Scenario 10.X: target with hint + preview serializer surfaces both."""
        target = SimpleNamespace(
            _meta=SimpleNamespace(model_name="fund"),
            lex_fk_label_field="name",
            api_serializers={"preview": object()},
        )
        info = create_field_info(self._fk(target))
        self.assertEqual(info["fk_label_field"], "name")
        self.assertTrue(info["fk_preview"], "preview serializer present → fk_preview True")
        self.assertEqual(info["target"], "fund", "existing 'target' key preserved")

    def test_fk_defaults_when_target_has_no_hints(self):
        """Scenario 10.X+1: bare target → fk_label_field None, fk_preview False."""
        target = SimpleNamespace(_meta=SimpleNamespace(model_name="fund"))
        info = create_field_info(self._fk(target))
        self.assertIsNone(info["fk_label_field"])
        self.assertFalse(info["fk_preview"])

    def test_preview_helper_detects_registry(self):
        """Scenario 10.X+2: _target_has_preview_serializer reads api_serializers."""
        with_preview = SimpleNamespace(api_serializers={"preview": object()})
        without = SimpleNamespace(api_serializers={"default": object()})
        none_reg = SimpleNamespace()
        self.assertTrue(_target_has_preview_serializer(with_preview))
        self.assertFalse(_target_has_preview_serializer(without))
        self.assertFalse(_target_has_preview_serializer(none_reg))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m lex pytest lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py -v`
Expected: FAIL — `ImportError: cannot import name '_target_has_preview_serializer'`.

- [ ] **Step 3: Implement the helper + FK hint**

In `lex/api/views/model_info/Fields.py`, add the helper above `create_field_info` (after the type maps, ~line 57):

```python
def _target_has_preview_serializer(model):
    """True when the FK target model registers a ``preview`` serializer via
    ``api_serializers`` — the curated, permission-aware field set the FK hover
    card fetches with ``?serializer=preview``."""
    custom = getattr(model, "api_serializers", None)
    return bool(isinstance(custom, dict) and "preview" in custom)
```

Then extend the FK branch in `create_field_info` (replace lines 70-72):

```python
    if ftype == ForeignKey:
        target_model = field.remote_field.model
        additional_info['target'] = target_model._meta.model_name
        additional_info['limit_choices_to'] = field.remote_field.limit_choices_to
        # Frontend redesign: which target field is the human label, and whether
        # the target offers a curated 'preview' serializer for the hover card.
        additional_info['fk_label_field'] = getattr(target_model, "lex_fk_label_field", None)
        additional_info['fk_preview'] = _target_has_preview_serializer(target_model)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m lex pytest lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add lex/api/views/model_info/Fields.py \
        lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py
git commit -m "feat(frontend-redesign): expose FK label field + preview availability in /fields/ (phase 1)"
```

---

## Task 4: `/fields/` per-field `format` spec

**Files:**
- Modify: `lex/api/views/model_info/Fields.py` — `Fields.get` (attach format after each `info` is built, before `fields_info.append`)
- Test: append to `lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py` (cluster 10, type **U**)

- [ ] **Step 1: Write the failing test**

Append to the Task 3 test file:

```python
from rest_framework import serializers as drf_serializers
from lex.api.views.model_info.Fields import Fields


class TestCluster10L_FieldFormatSpec(SimpleTestCase):
    """Cluster 10<L>: model.lex_field_formats surfaces as info['format']."""

    def _request(self, serializer_name="default"):
        req = MagicMock()
        req.query_params = {"serializer": serializer_name} if serializer_name else {}
        return req

    def _container(self, model, serializer):
        return SimpleNamespace(
            model_class=model,
            serializers_map={"default": lambda: serializer},
            get_serializers_map=lambda: {"default": lambda: serializer},
        )

    def test_format_attached_from_model_declaration(self):
        """Scenario 10.Y: a column listed in lex_field_formats carries 'format'."""
        revenue = MagicMock(spec=FloatField)
        revenue.name = "revenue"
        revenue.verbose_name = "revenue"
        revenue.editable = True
        revenue.null = True
        revenue.primary_key = False
        revenue.get_default = MagicMock(return_value=None)

        fmt = {"format": "currency", "currency": "EUR", "decimals": 2}
        model = SimpleNamespace(
            _meta=SimpleNamespace(
                pk=SimpleNamespace(name="id"),
                get_field=lambda src: revenue if src == "revenue" else (_ for _ in ()).throw(Exception()),
            ),
            lex_field_formats={"revenue": fmt},
        )
        serializer = SimpleNamespace(
            fields={"revenue": SimpleNamespace(source="revenue")},
            Meta=SimpleNamespace(),
        )
        resp = Fields().get(self._request(), model_container=self._container(model, serializer))
        revenue_info = next(f for f in resp.data["fields"] if f["name"] == "revenue")
        self.assertEqual(revenue_info["format"], fmt, "Declared format must surface verbatim")

    def test_no_format_key_when_undeclared(self):
        """Scenario 10.Y+1: columns absent from lex_field_formats omit 'format'."""
        amount = MagicMock(spec=FloatField)
        amount.name = "amount"
        amount.verbose_name = "amount"
        amount.editable = True
        amount.null = True
        amount.primary_key = False
        amount.get_default = MagicMock(return_value=None)
        model = SimpleNamespace(
            _meta=SimpleNamespace(
                pk=SimpleNamespace(name="id"),
                get_field=lambda src: amount,
            ),
            lex_field_formats={},
        )
        serializer = SimpleNamespace(
            fields={"amount": SimpleNamespace(source="amount")},
            Meta=SimpleNamespace(),
        )
        resp = Fields().get(self._request(), model_container=self._container(model, serializer))
        amount_info = next(f for f in resp.data["fields"] if f["name"] == "amount")
        self.assertNotIn("format", amount_info, "Undeclared columns carry no format key")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m lex pytest lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py -v -k FieldFormatSpec`
Expected: FAIL — `KeyError: 'format'` in the first test.

- [ ] **Step 3: Attach the format spec in `Fields.get`**

In `lex/api/views/model_info/Fields.py`, inside `Fields.get`, read the model-level formats once (after `model = container.model_class`, ~line 108):

```python
        field_formats = getattr(model, "lex_field_formats", {}) or {}
```

Then, in the `for fname, drf_field in serializer.fields.items():` loop, just before `fields_info.append(info)` (line 190):

```python
            fmt = field_formats.get(info["name"])
            if fmt:
                info["format"] = fmt

            fields_info.append(info)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m lex pytest lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py -v`
Expected: PASS (5 passed total in the file).

- [ ] **Step 5: Regression sweep on the fields endpoint cluster**

Run: `python -m lex pytest lex/test_project/tests/api_layer/ -v -k "fields or Fields"`
Expected: existing `test_10e_*` / `test_10i_*` still PASS (defaults unchanged for models without `lex_field_formats`).

- [ ] **Step 6: Commit**

```bash
git add lex/api/views/model_info/Fields.py \
        lex/test_project/tests/api_layer/test_<NN><L>_fields_fk_and_format.py
git commit -m "feat(frontend-redesign): expose per-field format spec in /fields/ (phase 1)"
```

---

## Task 5: Plan sync + coverage gate (Definition of Done)

Per lex-testing Step 7, the phase is **not done** until the test-plan on disk matches the tests written. Do this in the same change as the tests.

- [ ] **Step 1: Append a `progress/session-log.md` row** for each new batch (one per PR), append-only.

- [ ] **Step 2: Document the two new batches in `test-writing-plan.md`** — under Cluster 12 (FK label serialization) and Cluster 10 (fields metadata), each matching the most-recent batch row shape (scenario range, type U, files covered, test file, test classes, fixtures = none, real pass/fail counts, status ✅).

- [ ] **Step 3: Bump cluster status / scenario ranges** in `test-clusters.md` and `progress/dashboard.md` for clusters 10 and 12.

- [ ] **Step 4: Run the full coverage gate** to confirm no drop:

```bash
coverage run --source=.venv/src/lex-app/lex --rcfile=.coveragerc -m lex pytest \
    lex/test_project/tests/serializers/ lex/test_project/tests/api_layer/
coverage report --rcfile=.coveragerc --fail-under=50
```
Expected: PASS, coverage ≥ current threshold (these are net-new covered lines).

- [ ] **Step 5: Commit the plan sync**

```bash
git add lex/test_project/test-plan/
git commit -m "docs(test-plan): record phase-1 backend FK-label + /fields/ format batches"
```

---

## Notes carried forward to later phases (not implemented here)

- **Reserved name "preview" (frontend).** `preview` is now a load-bearing serializer key consumed by the FK hover card. The frontend must block users from saving a **grid view** or **menu view** named `preview`. This is enforced in the frontend repo (Phase 2/5 view-naming validation) — **no backend code change** is needed because `?serializer=preview` already routes through `resolve_requested_serializer_name` unchanged. Record this constraint where view names are validated.
- **N+1 on FK labels.** `_inject_fk_labels` calls `getattr(instance, fk_name)`, which lazy-loads the relation if not already fetched. The List/SSRM endpoint should `select_related()` the FK columns it serializes to avoid per-row queries. Verify the existing list query already does this for FK columns during Phase 3; if not, add the `select_related` there (not here).
- **Export honoring formats.** Excel/CSV export honoring the `format` spec is a Phase 3 concern (frontend builds the formatter; export path may mirror it). Out of scope for Phase 1.

---

## Self-Review

- **Spec coverage:** §4.2 FK serialization (`<fk>_label`, `fk_display`/`fk_label_field` hint, target name) → Tasks 1-3. §4.3 per-field format spec in `/fields/` → Task 4. §5 Phase 1 "preview serializer support" → Task 3 (`fk_preview` advertise; routing already exists) + carried note. "reserved-name note" → carried note (frontend-enforced). ✅
- **Placeholder scan:** `<NN><L>` / `12.<X>` / `10.<X>` are intentional — the lex-testing skill allocates the real letter + scenario IDs at execution (Step 5 confirmation). All code blocks are complete. ✅
- **Type consistency:** `resolve_fk_label(related_obj)` defined in Task 1, used in Task 2 `_inject_fk_labels` and Task 3 path. `_target_has_preview_serializer(model)` defined + used in Task 3. `lex_fk_label_field` / `lex_field_formats` declared in Task 1, read in Tasks 2-4. Keys added: `"<fk>_label"`, `fk_label_field`, `fk_preview`, `format` — consistent across tasks and tests. ✅
