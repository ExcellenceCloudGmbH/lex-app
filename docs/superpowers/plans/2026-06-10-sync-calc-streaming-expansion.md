# Sync-Mode Calculation Streaming Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut peak memory of `CalculatedModelMixin.create()` in synchronous mode (`CELERY_ACTIVE=False`) from O(N models) to O(depth) by streaming the combinatorial expansion depth-first, with a memory benchmark and a byte-identical-output equivalence gate.

**Architecture:** Add a depth-first generator (`generate_model_combinations_streaming`) that yields one fully-expanded model at a time, reusing the existing `_get_field_values` / `_normalize_field_values` helpers so dependency-aware DFS semantics are preserved. Add a streaming sync consumer that prepares → calculates → saves → releases each yielded model. `create()` branches on mode at the top: Celery mode keeps the untouched four-stage list/cluster pipeline; sync mode routes to the streaming path, skipping stages 1–3. A `LEX_SYNC_STREAMING_EXPANSION` env var (default on) gives a one-line rollback to the legacy materialized path.

**Tech Stack:** Python 3.12, Django ORM, pytest (`python -m lex pytest`), `tracemalloc`, in-memory SQLite for the benchmark.

**Spec:** [docs/superpowers/specs/2026-06-10-sync-calc-streaming-expansion-design.md](../specs/2026-06-10-sync-calc-streaming-expansion-design.md)

**Test cluster:** 7p (next free letter after 7o), scenarios 7.178+. Confirm exact allocation via the lex-testing skill at execution time; this plan assumes 7p / 7.178–7.187.

**Run env (every test command below assumes this prefix is active):**
```bash
source project_example/.venv/bin/activate && set -a && source project_example/.env && set +a
```
Tests run via `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest <file> -v`.

---

## File Structure

| File | Responsibility |
|---|---|
| `lex/core/mixins/CalculatedModelMixin.py` (modify) | New `generate_model_combinations_streaming` generator on `ModelCombinationGenerator`; new `calc_and_save_streaming` consumer; `_sync_streaming_enabled()` flag reader; top-level mode branch in `create()`. Celery branch + legacy helpers untouched. |
| `lex/test_project/tests/calculations/test_7p_streaming_expansion.py` (create) | Cluster 7p paired tests: DFS equivalence vs legacy, dependency-aware semantics, overrides, pruning, no-defining-fields, flag valve, bounded-peak memory assertion. |
| `lex/test_project/tests/calculations/models.py` (reuse, no change) | The E2E equivalence test reuses the **existing** `CombinatorialCalc` fixture (`defining_fields = ["region", "category"]`, `get_selected_key_list` returns `_region_keys`/`_category_keys`, `calculate()` sets `name = f"{region}-{category}"`), already used by 7g. No new model is needed. |
| `scripts/bench_sync_expansion.py` (create) | Standalone N≈10k benchmark: tracemalloc peak + ordered fingerprint, legacy vs streaming, report to `docs/runs/`. |
| `docs/runs/2026-06-10-sync-expansion-benchmark.md` (create, by running the bench) | The old-vs-new numbers. |
| Plan-sync files (modify) | `dashboard.md`, `session-log.md`, `test-clusters.md`, `test-writing-plan.md`. |

---

## Task 1: Streaming DFS generator (pure logic)

Add a generator mirroring `_expand_models_for_field` value-for-value and copy-for-copy, but depth-first. This is pure logic — testable with the existing `_FakeCalcModel` pattern (no DB).

**Files:**
- Modify: `lex/core/mixins/CalculatedModelMixin.py` (add static method to `ModelCombinationGenerator`, after `_get_field_values` ends at line 499)
- Test: `lex/test_project/tests/calculations/test_7p_streaming_expansion.py`

- [ ] **Step 1: Write the failing test — streaming yields the SAME models in the SAME order as legacy**

Create `lex/test_project/tests/calculations/test_7p_streaming_expansion.py`:

```python
"""Cluster 7p: streaming DFS expansion equivalence + sync memory bound.

Intent: in sync mode (CELERY_ACTIVE=False) a calculation runs inside the
web process, so the combinatorial expansion's O(N-models) peak can OOM the
pod. The streaming DFS generator yields one fully-expanded model at a time
(O(depth) live) instead of materializing all N. These tests pin that the
streaming path produces a BYTE-IDENTICAL ordered set of models vs the legacy
breadth-first generator (the equivalence gate), preserves dependency-aware
DFS semantics, and keeps sync peak memory bounded.

Cluster 7p — scenarios 7.178-7.187. Type: U (+ I for the E2E sync path).
Covers: lex/core/mixins/CalculatedModelMixin.py.
Run: python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py -v
"""

from __future__ import annotations

import copy

import pytest
from django.test import SimpleTestCase
from lex.core.mixins.CalculatedModelMixin import ModelCombinationGenerator

pytestmark = pytest.mark.calculations


class _FakeCalcModel:
    """Dataclass-shaped stand-in for a CalculatedModelMixin (no DB).

    The combination engine only needs __class__.__name__, setattr/getattr,
    deepcopy, and get_selected_key_list(field) for non-overridden fields.
    """

    def __init__(self, key_lists=None, **attrs):
        # key_lists: dict field -> list, OR callable(model, field) -> list
        self._key_lists = key_lists or {}
        for k, v in attrs.items():
            setattr(self, k, v)

    def __deepcopy__(self, memo):
        new = _FakeCalcModel()
        new.__dict__.update({k: v for k, v in self.__dict__.items()})
        return new

    def get_selected_key_list(self, key):
        spec = self._key_lists.get(key)
        if callable(spec):
            return spec(self)
        return spec


def _tuples(models, fields):
    """Fingerprint: ordered list of defining-field value tuples."""
    return [tuple(getattr(m, f, None) for f in fields) for m in models]


class TestCluster07p_StreamingEquivalence(SimpleTestCase):
    """Streaming DFS == legacy BFS, same models, same order."""

    def test_7_178_streaming_matches_legacy_plain(self):
        """Scenario 7.178: independent fields — streaming order == legacy order."""
        fields = ["region", "product"]
        key_lists = {"region": ["US", "EU"], "product": ["A", "B", "C"]}

        base_legacy = _FakeCalcModel(key_lists=key_lists)
        legacy = ModelCombinationGenerator.generate_model_combinations(
            base_legacy, fields, {}
        )

        base_stream = _FakeCalcModel(key_lists=key_lists)
        streamed = list(
            ModelCombinationGenerator.generate_model_combinations_streaming(
                base_stream, fields, {}
            )
        )

        assert _tuples(streamed, fields) == _tuples(legacy, fields)
        assert len(streamed) == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py::TestCluster07p_StreamingEquivalence::test_7_178_streaming_matches_legacy_plain -v`
Expected: FAIL — `AttributeError: ... has no attribute 'generate_model_combinations_streaming'`

- [ ] **Step 3: Implement the streaming generator**

In `lex/core/mixins/CalculatedModelMixin.py`, add to `ModelCombinationGenerator` (after `_get_field_values`, ~line 499):

```python
    @staticmethod
    def generate_model_combinations_streaming(
        base_model: 'CalculatedModelMixin',
        defining_fields: List[str],
        field_overrides: Dict[str, Any],
    ):
        """Depth-first streaming twin of generate_model_combinations.

        Yields one fully-expanded model at a time. At most `depth` models
        (root-to-leaf path) are alive at once, vs the legacy generator's N.
        Mirrors _expand_models_for_field value-for-value and deepcopy-for-
        value, and computes field values from the PARTIALLY-built model via
        the same _get_field_values helper, so dependency-aware
        get_selected_key_list semantics are identical. Field ordering matches
        legacy: override fields first.
        """
        if not base_model:
            raise ModelCombinationError(
                "Base model cannot be None",
                model_class=base_model.__class__.__name__ if base_model else "Unknown",
            )

        if not defining_fields:
            yield base_model
            return

        ordered_defining_fields = sorted(
            defining_fields,
            key=lambda x: 0 if x in field_overrides.keys() else 1,
        )
        ordered_defining_fields = [
            f.__str__().split('.')[-1] for f in ordered_defining_fields
        ]

        def _expand(model, field_index):
            if field_index == len(ordered_defining_fields):
                yield model
                return
            field_name = ordered_defining_fields[field_index]
            try:
                field_values = ModelCombinationGenerator._get_field_values(
                    model, field_name, field_overrides
                )
            except ModelCombinationError:
                raise
            except Exception as field_error:
                raise ModelCombinationError(
                    f"Failed to expand defining field '{field_name}': {str(field_error)}",
                    field_name=field_name,
                    model_class=base_model.__class__.__name__,
                ) from field_error

            if not field_values:
                # Empty value list prunes this branch (matches legacy `continue`).
                return
            if not isinstance(field_values, list):
                raise ModelCombinationError(
                    f"Field values must normalize to a list, got {type(field_values).__name__}",
                    field_name=field_name,
                    model_class=model.__class__.__name__,
                )

            for value in field_values:
                model_copy = deepcopy(model)
                setattr(model_copy, field_name, value)
                yield from _expand(model_copy, field_index + 1)

        yield from _expand(base_model, 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py::TestCluster07p_StreamingEquivalence::test_7_178_streaming_matches_legacy_plain -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lex/core/mixins/CalculatedModelMixin.py lex/test_project/tests/calculations/test_7p_streaming_expansion.py
git commit -m "Add streaming DFS combination generator (7p: 7.178)"
```

---

## Task 2: DFS semantics, overrides, pruning, single-model edge cases

Pin the behaviors that make streaming equivalent to legacy in the tricky cases.

**Files:**
- Test: `lex/test_project/tests/calculations/test_7p_streaming_expansion.py`

- [ ] **Step 1: Write the failing tests**

Append to `TestCluster07p_StreamingEquivalence`:

```python
    def test_7_179_dependency_aware_fields_match_legacy(self):
        """Scenario 7.179: a field whose values depend on an earlier field.

        product values depend on the already-set region — exercises the DFS
        trap. Streaming must see the partially-built model exactly as legacy.
        """
        fields = ["region", "product"]
        key_lists = {
            "region": ["US", "EU"],
            "product": lambda m: (
                ["us-only"] if getattr(m, "region", None) == "US" else ["eu-1", "eu-2"]
            ),
        }
        base_legacy = _FakeCalcModel(key_lists=key_lists)
        legacy = ModelCombinationGenerator.generate_model_combinations(
            base_legacy, fields, {}
        )
        base_stream = _FakeCalcModel(key_lists=key_lists)
        streamed = list(
            ModelCombinationGenerator.generate_model_combinations_streaming(
                base_stream, fields, {}
            )
        )
        assert _tuples(streamed, fields) == _tuples(legacy, fields)
        # US -> 1 product, EU -> 2 products = 3 total
        assert len(streamed) == 3

    def test_7_180_field_overrides_match_legacy(self):
        """Scenario 7.180: override values + ordering (override fields first)."""
        fields = ["region", "product"]
        key_lists = {"region": ["US", "EU", "APAC"], "product": ["A"]}
        overrides = {"product": ["X", "Y"]}
        base_legacy = _FakeCalcModel(key_lists=key_lists)
        legacy = ModelCombinationGenerator.generate_model_combinations(
            base_legacy, fields, overrides
        )
        base_stream = _FakeCalcModel(key_lists=key_lists)
        streamed = list(
            ModelCombinationGenerator.generate_model_combinations_streaming(
                base_stream, fields, overrides
            )
        )
        assert _tuples(streamed, fields) == _tuples(legacy, fields)

    def test_7_181_empty_value_list_prunes_branch(self):
        """Scenario 7.181: a field returning [] for some parent prunes that branch."""
        fields = ["region", "product"]
        key_lists = {
            "region": ["US", "EU"],
            "product": lambda m: [] if getattr(m, "region", None) == "EU" else ["A", "B"],
        }
        base_legacy = _FakeCalcModel(key_lists=key_lists)
        legacy = ModelCombinationGenerator.generate_model_combinations(
            base_legacy, fields, {}
        )
        base_stream = _FakeCalcModel(key_lists=key_lists)
        streamed = list(
            ModelCombinationGenerator.generate_model_combinations_streaming(
                base_stream, fields, {}
            )
        )
        assert _tuples(streamed, fields) == _tuples(legacy, fields)
        assert len(streamed) == 2  # only US -> A, US -> B

    def test_7_182_no_defining_fields_yields_single_base(self):
        """Scenario 7.182: empty defining_fields yields exactly the base model."""
        base = _FakeCalcModel()
        streamed = list(
            ModelCombinationGenerator.generate_model_combinations_streaming(
                base, [], {}
            )
        )
        assert streamed == [base]
```

- [ ] **Step 2: Run tests to verify they fail or pass**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py::TestCluster07p_StreamingEquivalence -v`
Expected: 7.179–7.182 PASS if Task 1 implementation is correct. If any FAIL, the streaming generator diverges from legacy — fix the generator (do NOT weaken the test) until the fingerprint matches.

- [ ] **Step 3: Fix generator if any divergence (only if Step 2 showed a failure)**

If 7.181 fails because the branch wasn't pruned, confirm the `if not field_values: return` guard is present and placed before the `isinstance` check. If 7.180 fails on ordering, confirm `ordered_defining_fields` sorts override keys first. No code change if Step 2 was all green.

- [ ] **Step 4: Re-run to verify all green**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py::TestCluster07p_StreamingEquivalence -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add lex/test_project/tests/calculations/test_7p_streaming_expansion.py lex/core/mixins/CalculatedModelMixin.py
git commit -m "Pin DFS semantics/overrides/pruning equivalence (7p: 7.179-7.182)"
```

---

## Task 3: Flag reader + streaming sync consumer

Add the `LEX_SYNC_STREAMING_EXPANSION` flag reader and the per-model streaming consumer (prepare → calculate → save → release).

**Files:**
- Modify: `lex/core/mixins/CalculatedModelMixin.py` (module level near `calc_and_save_sync`, ~line 841)
- Test: `lex/test_project/tests/calculations/test_7p_streaming_expansion.py`

- [ ] **Step 1: Write the failing test for the flag reader**

Append a new class:

```python
import os
from unittest import mock
from lex.core.mixins.CalculatedModelMixin import _sync_streaming_enabled


class TestCluster07p_StreamingFlag(SimpleTestCase):
    """LEX_SYNC_STREAMING_EXPANSION valve: default on, opt-out to legacy."""

    def test_7_183_flag_default_on(self):
        """Scenario 7.183: unset env -> streaming enabled (default on)."""
        with mock.patch.dict(os.environ, {}, clear=True):
            assert _sync_streaming_enabled() is True

    def test_7_184_flag_false_disables(self):
        """Scenario 7.184: explicit 'false' -> legacy materialized path."""
        with mock.patch.dict(os.environ, {"LEX_SYNC_STREAMING_EXPANSION": "false"}):
            assert _sync_streaming_enabled() is False

    def test_7_185_flag_true_enables(self):
        """Scenario 7.185: explicit 'true' -> streaming."""
        with mock.patch.dict(os.environ, {"LEX_SYNC_STREAMING_EXPANSION": "true"}):
            assert _sync_streaming_enabled() is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py::TestCluster07p_StreamingFlag -v`
Expected: FAIL — `ImportError: cannot import name '_sync_streaming_enabled'`

- [ ] **Step 3: Implement flag reader + streaming consumer**

In `lex/core/mixins/CalculatedModelMixin.py`, add after `calc_and_save_sync` ends (~line 920):

```python
def _sync_streaming_enabled() -> bool:
    """Whether sync-mode expansion streams (default) or materializes (legacy).

    Default ON: streaming is the memory-safe path and the whole point of the
    fix. Set LEX_SYNC_STREAMING_EXPANSION=false for a one-line rollback to the
    legacy materialized path without a redeploy.
    """
    return os.getenv("LEX_SYNC_STREAMING_EXPANSION", "true").lower() != "false"


def calc_and_save_streaming(model_iter, *args):
    """Streaming sync consumer: prepare -> calculate -> save -> release, per model.

    Consumes the streaming combination generator one model at a time so peak
    memory is O(depth), not O(N). Per yielded model it runs the same stage-2
    prepare (delete_models_with_same_defining_fields) and the same
    calculate+save body as calc_and_save_sync, then drops the reference.
    """
    from lex.core.models.CalculationModel import calculation_execution_context

    processed = 0
    for i, model in enumerate(model_iter):
        if model is None:
            logger.warning(f"Streaming model {i + 1} is None, skipping")
            continue
        # Stage-2 prepare inline (dedup + pk reset), matching the legacy
        # _prepare_models_for_processing per-model step.
        prepared = model.delete_models_with_same_defining_fields()
        try:
            with calculation_execution_context():
                try:
                    prepared.lex_func()(*args)
                except Exception as calc_error:
                    raise CalculatedModelError(
                        f"Calculation failed for streaming model {i + 1}: {str(calc_error)}",
                        model_class=prepared.__class__.__name__,
                        model_index=i,
                    ) from calc_error
                prepared.save()
        except CalculatedModelError:
            raise
        except Exception as save_error:
            raise CalculatedModelError(
                f"Save failed for streaming model {i + 1}: {save_error}",
                model_class=prepared.__class__.__name__,
                model_index=i,
            ) from save_error
        processed += 1
    logger.info(f"Streaming sync processing completed: {processed} models processed")
```

- [ ] **Step 4: Run to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py::TestCluster07p_StreamingFlag -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add lex/core/mixins/CalculatedModelMixin.py lex/test_project/tests/calculations/test_7p_streaming_expansion.py
git commit -m "Add streaming sync consumer + flag reader (7p: 7.183-7.185)"
```

---

## Task 4: Wire `create()` to branch on mode

Branch `create()` at the top: Celery mode keeps the four-stage pipeline; sync mode (with streaming flag on) routes to the streaming consumer, skipping stages 1–3. Flag-off sync keeps the legacy pipeline.

**Files:**
- Modify: `lex/core/mixins/CalculatedModelMixin.py` (`create`, lines 1171-1292)
- Test: `lex/test_project/tests/calculations/test_7p_streaming_expansion.py`

- [ ] **Step 1: Write the failing E2E equivalence test**

Reuse the **existing** `CombinatorialCalc` fixture from `models.py` (verified: `defining_fields = ["region", "category"]`, `get_selected_key_list` returns the `_region_keys`/`_category_keys` class knobs, `calculate()` sets `self.name = f"{region}-{category}"`). It is already registered through `ALL_MODELS` and exercised by 7g under `CELERY_ACTIVE=False`. No new model — `E2ETestCase.setUp` already patches `CELERY_ACTIVE=False`, so `create()` takes the sync path. Reset the mutable class knobs in `setUp` exactly as `test_7g_create_pipeline.py` does, so one test can't leak into the next.

Add to the test file (the E2E base is `E2ETestCase` from `lex.test_project.tests._e2e_test_case`, verified against `test_7g_create_pipeline.py`):

```python
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.calculations.models import ALL_MODELS, CombinatorialCalc


class TestCluster07p_SyncCreateEquivalence(E2ETestCase):
    """create() in sync mode: streaming vs legacy produce identical saved rows."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        # Reset mutable class-level knobs between tests (same as 7g).
        CombinatorialCalc._region_keys = ["US", "EU", "APAC"]
        CombinatorialCalc._category_keys = ["A", "B"]
        CombinatorialCalc.fail_for_region = None

    def _run_and_fingerprint(self):
        CombinatorialCalc.create()
        rows = list(
            CombinatorialCalc.objects.order_by("region", "category")
            .values_list("region", "category", "name")
        )
        return rows

    def test_7_186_sync_streaming_saves_same_rows_as_legacy(self):
        """Scenario 7.186: streaming default vs flag-off legacy -> identical rows."""
        with mock.patch.dict(os.environ, {"LEX_SYNC_STREAMING_EXPANSION": "false"}):
            legacy_rows = self._run_and_fingerprint()
        CombinatorialCalc.objects.all().delete()
        with mock.patch.dict(os.environ, {"LEX_SYNC_STREAMING_EXPANSION": "true"}):
            streaming_rows = self._run_and_fingerprint()
        assert streaming_rows == legacy_rows
        # 3 regions x 2 categories = 6 rows.
        assert len(streaming_rows) == 6
```

- [ ] **Step 2: Run to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py::TestCluster07p_SyncCreateEquivalence -v`
Expected: FAIL — both runs currently take the legacy path (the flag isn't wired into `create()` yet), so either the assertion is trivially equal (not yet exercising streaming) OR errors on the new model. If it passes trivially, that's a false green — Step 3 wires the branch so the `true` run actually streams; re-confirm in Step 4.

- [ ] **Step 3: Wire the mode branch into `create()`**

In `lex/core/mixins/CalculatedModelMixin.py`, at the start of the `try` block in `create()` (after line 1204, before "Step 1"), insert the sync streaming short-circuit:

```python
            # Sync-mode streaming short-circuit: when Celery is not active and
            # streaming is enabled (default), expand depth-first and process one
            # model at a time so peak memory is O(depth), not O(N). Celery mode
            # and the flag-off legacy path fall through to the four-stage pipeline.
            celery_active = (
                os.getenv('CELERY_ACTIVE', "").lower() == 'true'
                and hasattr(cls.calculate, 'delay')
            )
            if not celery_active and _sync_streaming_enabled():
                logger.info(f"Sync streaming expansion for {cls.__name__}")
                base_model = cls()
                model_iter = ModelCombinationGenerator.generate_model_combinations_streaming(
                    base_model, cls.defining_fields, kwargs
                )
                calc_and_save_streaming(model_iter, *args)
                logger.info(f"Sync streaming expansion completed for {cls.__name__}")
                return
```

Note: `cls.defining_fields` may be empty — the generator already handles that by yielding the single base model. The `celery_active` detection mirrors `_dispatch_model_processing:1529` exactly so behavior is consistent.

- [ ] **Step 4: Run to verify it passes (and actually streams)**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py::TestCluster07p_SyncCreateEquivalence -v`
Expected: PASS — 6 identical rows from both the legacy (`false`) and streaming (`true`) runs.

- [ ] **Step 5: Run the full 7p file + regression on calculations**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py lex/test_project/tests/calculations/test_7g_create_pipeline.py lex/test_project/tests/calculations/test_7f_combination_engine.py -v`
Expected: all PASS (7g/7f are the existing create-pipeline + combination-engine suites; they must stay green — Celery path and legacy helpers are unchanged).

- [ ] **Step 6: Commit**

```bash
git add lex/core/mixins/CalculatedModelMixin.py lex/test_project/tests/calculations/test_7p_streaming_expansion.py
git commit -m "Route sync-mode create() to streaming expansion (7p: 7.186)"
```

---

## Task 5: Bounded-peak memory assertion (the regression gate)

A light, deterministic test that peak memory of the streaming path stays ~flat (O(depth)) rather than scaling with N. This is the CI-safe guard; the N≈10k bench (Task 6) is the manual report.

**Files:**
- Test: `lex/test_project/tests/calculations/test_7p_streaming_expansion.py`

- [ ] **Step 1: Write the failing memory test**

Append:

```python
import tracemalloc


class TestCluster07p_StreamingMemoryBound(SimpleTestCase):
    """Streaming generator holds O(depth) models, not O(N), at peak."""

    def test_7_187_streaming_peak_is_bounded_not_linear(self):
        """Scenario 7.187: peak live count stays small while N grows large.

        We instrument the generator: at no point are more than (depth + small
        constant) freshly-yielded models retained by the consumer. We prove it
        by consuming lazily and asserting the generator never materializes the
        full set — peak tracemalloc for a large N streaming consume stays far
        below materializing the same N via the legacy list generator.
        """
        fields = ["a", "b", "c"]
        # 25 * 25 * 25 = 15625 combinations
        key_lists = {"a": list(range(25)), "b": list(range(25)), "c": list(range(25))}

        base_legacy = _FakeCalcModel(key_lists=key_lists)
        tracemalloc.start()
        legacy = ModelCombinationGenerator.generate_model_combinations(
            base_legacy, fields, {}
        )
        _, legacy_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        legacy_count = len(legacy)
        del legacy

        base_stream = _FakeCalcModel(key_lists=key_lists)
        tracemalloc.start()
        stream_count = 0
        for _m in ModelCombinationGenerator.generate_model_combinations_streaming(
            base_stream, fields, {}
        ):
            stream_count += 1  # consume + immediately drop (mimics save+release)
        _, stream_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert stream_count == legacy_count == 15625
        # Streaming peak must be a small fraction of materializing all N.
        assert stream_peak < legacy_peak / 5, (
            f"streaming peak {stream_peak} not << legacy peak {legacy_peak}"
        )
```

- [ ] **Step 2: Run to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/test_7p_streaming_expansion.py::TestCluster07p_StreamingMemoryBound -v`
Expected: PASS. If `stream_peak` is not < legacy_peak/5, the generator is accidentally retaining models (e.g. a stray list) — inspect for accidental materialization. If the ratio is flaky near the boundary, relax to `/3` and note why in a comment, but it should comfortably clear `/5`.

- [ ] **Step 3: Commit**

```bash
git add lex/test_project/tests/calculations/test_7p_streaming_expansion.py
git commit -m "Add bounded-peak memory regression gate (7p: 7.187)"
```

---

## Task 6: Benchmark harness (N≈10k report)

Standalone old-vs-new memory + fingerprint report at the target scale.

**Files:**
- Create: `scripts/bench_sync_expansion.py`
- Create (by running): `docs/runs/2026-06-10-sync-expansion-benchmark.md`

- [ ] **Step 1: Write the benchmark script**

Create `scripts/bench_sync_expansion.py`:

```python
"""Benchmark: legacy vs streaming combinatorial expansion peak memory.

Sized to N ~= 10k (22^3 = 10648). Measures tracemalloc peak for each path and
asserts the produced fingerprint (ordered defining-field tuples) is identical.
Pure-logic (no DB) so it isolates the expansion engine's memory cost.

Run: DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex run scripts/bench_sync_expansion.py
(or invoke run_benchmark() from a pytest).
"""

from __future__ import annotations

import tracemalloc
from copy import deepcopy

from lex.core.mixins.CalculatedModelMixin import ModelCombinationGenerator


class _FakeCalcModel:
    def __init__(self, key_lists=None, **attrs):
        self._key_lists = key_lists or {}
        for k, v in attrs.items():
            setattr(self, k, v)

    def __deepcopy__(self, memo):
        new = _FakeCalcModel()
        new.__dict__.update(self.__dict__)
        return new

    def get_selected_key_list(self, key):
        spec = self._key_lists.get(key)
        return spec(self) if callable(spec) else spec


def _fields_and_keys():
    vals = [f"v{i}" for i in range(22)]
    fields = ["f0", "f1", "f2"]
    # f2 is dependency-aware: its values depend on f0, exercising the DFS path.
    key_lists = {
        "f0": vals,
        "f1": vals,
        "f2": lambda m: vals,
    }
    return fields, key_lists


def _tuples(models, fields):
    return [tuple(getattr(m, f, None) for f in fields) for m in models]


def run_benchmark():
    fields, key_lists = _fields_and_keys()

    base = _FakeCalcModel(key_lists=key_lists)
    tracemalloc.start()
    legacy = ModelCombinationGenerator.generate_model_combinations(base, fields, {})
    _, legacy_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    legacy_fp = _tuples(legacy, fields)
    legacy_n = len(legacy)
    del legacy

    base = _FakeCalcModel(key_lists=key_lists)
    tracemalloc.start()
    stream_fp = []
    n = 0
    for m in ModelCombinationGenerator.generate_model_combinations_streaming(
        base, fields, {}
    ):
        stream_fp.append(tuple(getattr(m, f, None) for f in fields))
        n += 1
    _, stream_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    identical = stream_fp == legacy_fp
    report = (
        f"# Sync Expansion Benchmark (2026-06-10)\n\n"
        f"N = {legacy_n} (legacy) / {n} (streaming)\n\n"
        f"| Path | Peak (MB) | Models |\n|---|---|---|\n"
        f"| legacy (materialized) | {legacy_peak / 1e6:.2f} | {legacy_n} |\n"
        f"| streaming (DFS) | {stream_peak / 1e6:.2f} | {n} |\n\n"
        f"Fingerprint identical: {identical}\n"
        f"Peak reduction: {legacy_peak / max(stream_peak, 1):.1f}x\n"
    )
    print(report)
    assert identical, "Fingerprint mismatch — streaming diverged from legacy!"
    return report


if __name__ == "__main__":
    run_benchmark()
```

- [ ] **Step 2: Run the benchmark**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex run scripts/bench_sync_expansion.py`
(If `lex run` is not a valid subcommand, run with the env active: `python scripts/bench_sync_expansion.py` after `DJANGO_SETTINGS_MODULE=lex_app.settings`.)
Expected: prints a table; `Fingerprint identical: True`; streaming peak materially lower than legacy (target ≥ 5x reduction).

- [ ] **Step 3: Save the report**

Copy the printed report into `docs/runs/2026-06-10-sync-expansion-benchmark.md`.

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_sync_expansion.py docs/runs/2026-06-10-sync-expansion-benchmark.md
git commit -m "Add sync-expansion memory benchmark + N=10k report"
```

---

## Task 7: Plan-file sync (lex-testing Step 7)

Record cluster 7p in the four test-plan files, matching the format of existing entries.

**Files:**
- Modify: `lex/test_project/test-plan/progress/dashboard.md`
- Modify: `lex/test_project/test-plan/progress/session-log.md`
- Modify: `lex/test_project/test-plan/test-clusters.md`
- Modify: `lex/test_project/test-plan/test-writing-plan.md`

- [ ] **Step 1: Add the dashboard row**

After the last cluster-7 row in `dashboard.md`, add (10 tests, all pass):

```
| 7p. Sync-mode streaming combinatorial expansion (OOM fix) | 10 | 10 | 10 | 0 | 0 | ✅ Complete — scenarios 7.178–7.187; depth-first generator yields one model at a time (O(depth) vs O(N)) so sync calcs (CELERY_ACTIVE=False) stop OOM-ing the web pod; byte-identical ordered output vs legacy; LEX_SYNC_STREAMING_EXPANSION=false valve |
```

- [ ] **Step 2: Add the session-log row** (use the actual date at execution time; convert any relative date to absolute)

```
| 2026-06-10 | <N> | **Sub-cluster 7p landed — sync-mode streaming combinatorial expansion (backend OOM fix).** When CELERY_ACTIVE=False a calc runs in the web process; create()'s four-stage pipeline materialized all N expanded models and held them for the whole run (peak O(N)), OOM-killing the pod. Fix: a depth-first `generate_model_combinations_streaming` generator + `calc_and_save_streaming` consumer process one model at a time (prepare→calculate→save→release), peak O(depth). `create()` branches on mode at the top: Celery path + legacy helpers untouched; sync path skips stages 1–3. `LEX_SYNC_STREAMING_EXPANSION=false` rolls back to legacy. Equivalence gate: ordered defining-field fingerprint identical legacy-vs-streaming (pure-logic + E2E saved rows). Benchmark at N≈10k in docs/runs/. Source: lex/core/mixins/CalculatedModelMixin.py. Test: test_7p_streaming_expansion.py. | 7p | 10 (7.178–7.187) + source in 1 file | 10 pass / 0 fail locally |
```

- [ ] **Step 3: Add the test-clusters.md batch block** under cluster 7, after the last 7x batch:

```
### Sub-cluster 7p — Sync-mode streaming combinatorial expansion (OOM fix)

In sync mode (`CELERY_ACTIVE=False`) a calculation runs inside the web/ASGI process. `CalculatedModelMixin.create()`'s four-stage pipeline (generate → prepare → cluster → dispatch) materialized **all N** expanded models and held them alive for the whole run, so peak memory was O(N) and a large fan-out OOM-killed the pod. 7p adds a depth-first `generate_model_combinations_streaming` generator that yields one fully-expanded model at a time (O(depth) live), and a `calc_and_save_streaming` consumer that prepares → calculates → saves → releases each one. `create()` branches on mode at the top: the Celery path and the legacy list/cluster helpers are untouched; the sync path skips stages 1–3. A `LEX_SYNC_STREAMING_EXPANSION=false` env var rolls back to the legacy materialized path. The expansion is dependency-aware (a field's values can depend on an earlier field), so the generator reuses `_get_field_values` and recurses depth-first to preserve identical semantics.

**Scenario range:** 7.178 – 7.187. **Test file:** `lex/test_project/tests/calculations/test_7p_streaming_expansion.py`. **Type:** U (+ I for the E2E sync create path). **Status:** ✅ Complete (2026-06-10). Covers `lex/core/mixins/CalculatedModelMixin.py`. Equivalence gate: ordered defining-field fingerprint identical legacy-vs-streaming; bounded-peak memory regression test; N≈10k benchmark in `docs/runs/`.
```

- [ ] **Step 4: Add the test-writing-plan.md batch block** under Cluster 7:

```
### Batch 7p — Sync-mode streaming combinatorial expansion (OOM fix) ✅

| Property | Value |
| --- | --- |
| Scenario range | 7.178 – 7.187 |
| Type | U (generator + flag, pure logic) + I (E2E sync create saved rows) |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py` (`generate_model_combinations_streaming`, `calc_and_save_streaming`, `_sync_streaming_enabled`, `create()` mode branch) |
| Test file | `lex/test_project/tests/calculations/test_7p_streaming_expansion.py` |
| Test classes | `TestCluster07p_StreamingEquivalence` (7.178–7.182), `TestCluster07p_StreamingFlag` (7.183–7.185), `TestCluster07p_SyncCreateEquivalence` (7.186), `TestCluster07p_StreamingMemoryBound` (7.187) |
| Fixtures | `_FakeCalcModel` (pure logic); existing `CombinatorialCalc` E2E model (reused, no new model) |
| Tests landed | 10 pass / 0 fail |
| Coverage gain | streaming generator + consumer + flag + create() sync branch |
| Status | ✅ Complete (2026-06-10) |
| Note | Backend OOM fix: sync-mode expansion now streams (O(depth)) instead of materializing all N. `LEX_SYNC_STREAMING_EXPANSION=false` valve. Benchmark in docs/runs/. |
```

- [ ] **Step 5: Commit**

```bash
git add lex/test_project/test-plan/
git commit -m "Plan sync: cluster 7p sync streaming expansion (7.178-7.187)"
```

---

## Task 8: Full regression + verification

- [ ] **Step 1: Run the whole calculations cluster**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/calculations/ -v`
Expected: all pass (new 7p + existing 7a–7o green). If any pre-existing test fails identically on a clean tree, note it; otherwise investigate.

- [ ] **Step 2: Run the celery_async cluster (guards the Celery path is unchanged)**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex pytest lex/test_project/tests/celery_async/ -v`
Expected: same pass/skip profile as before this change (Celery branch untouched).

- [ ] **Step 3: Verify the benchmark fingerprint gate one more time**

Run: `DJANGO_SETTINGS_MODULE=lex_app.settings python scripts/bench_sync_expansion.py`
Expected: `Fingerprint identical: True`, peak reduction ≥ 5x.

- [ ] **Step 4: Final commit if anything was adjusted**

```bash
git add -A
git commit -m "Sync streaming expansion: full regression green"
```

---

## Success Criteria (from spec §8)

1. Benchmark shows streaming peak materially lower than baseline at N≈10k, ~flat with N — **Task 6 + Task 5**.
2. Fingerprint identical legacy vs streaming for synthetic + E2E models — **Tasks 1–2 (pure), Task 4 (E2E)**.
3. Celery-mode path unchanged — **Task 8 Step 2** (celery_async regression) + Celery branch never edited.
4. `LEX_SYNC_STREAMING_EXPANSION=false` reproduces legacy exactly — **Task 3 + Task 4 (7.186 runs both)**.
