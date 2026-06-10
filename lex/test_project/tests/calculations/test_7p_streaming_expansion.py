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
