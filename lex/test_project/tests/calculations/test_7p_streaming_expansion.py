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

import os
import tracemalloc
from unittest import mock

import pytest
from django.test import SimpleTestCase
from lex.core.mixins.CalculatedModelMixin import (
    ModelCombinationGenerator,
    _sync_streaming_enabled,
)
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.calculations.models import ALL_MODELS, CombinatorialCalc

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
        assert len(streamed) == 6

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
        assert streamed[0] is base  # no-fields branch yields the base model itself, not a copy


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
