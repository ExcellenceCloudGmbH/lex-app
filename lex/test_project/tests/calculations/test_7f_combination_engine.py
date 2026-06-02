"""
Cluster 7f: Combination engine + cluster manager (pure-logic helpers).

Targets the static helpers in
``lex/core/mixins/CalculatedModelMixin.py`` that drive the
combinatorial expansion engine behind every non-atomic
``CalculatedModel``:

* ``_normalize_field_values`` (line 152) — coerces the result of
  ``get_selected_key_list`` into a concrete list.
* ``ModelCombinationGenerator.generate_model_combinations`` + the
  private ``_expand_models_for_field`` / ``_get_field_values``
  helpers — take a base model, a list of defining fields, and a
  per-field override dict; return one cloned model per cartesian
  combination.
* ``ModelClusterManager.create_clusters`` + ``_build_cluster_hierarchy``
  + ``flatten_clusters_to_groups`` — organize a flat list of
  models into a nested dict keyed by parallelizable-field values,
  then flatten the nested dict back into groups ready for Celery
  dispatch.

Baseline: ``CalculatedModelMixin.py`` sat at **9.29%** with 484
missing statements. The combination engine alone is ~300 lines of
pure logic.

Why unit-level: these helpers take plain Python lists + dicts and
return plain Python lists + dicts. They do **not** touch the DB,
Celery, or the ``LexModel`` lifecycle. Driving them end-to-end
through the full ``calculate_mixin`` path would add 10× setup cost
for 0× extra assertion value — the cartesian-expansion contract is
the entire public surface we care about. The end-to-end calculation
path is already covered by 7a / 7b / 7c.

``base_model.__class__`` is probed via ``__name__`` only (for log
messages and error payloads). That lets us use a dataclass-shaped
fake — no Django model + ``Meta`` + migration dance required.

Scenario numbering matches
docs/test-plan/test-clusters.md § Planned Expansions → 7f.
"""

from __future__ import annotations

import unittest

from django.test import SimpleTestCase
from lex.core.exceptions import ModelClusteringError, ModelCombinationError
from lex.core.mixins.CalculatedModelMixin import (
    ModelClusterManager,
    ModelCombinationGenerator,
    _normalize_field_values,
)

import pytest

pytestmark = pytest.mark.calculations


# --------------------------------------------------------------------
# Lightweight fakes
# --------------------------------------------------------------------
class _FakeCalcModel:
    """Dataclass-shaped stand-in for a ``CalculatedModelMixin``.

    The combination engine only needs:

      * ``__class__.__name__`` (log + error messages)
      * attribute assignment via ``setattr`` (``_expand_models_for_field``
        writes the value for each expanded field)
      * attribute read via ``getattr`` (``_build_cluster_hierarchy``
        reads parallelizable-field values)
      * ``get_selected_key_list(field_name)`` if the field is not in
        the overrides dict

    Everything else (save, validate, Django signals) is untouched.
    """

    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)

    def __copy__(self):
        new = _FakeCalcModel()
        new.__dict__.update(self.__dict__)
        return new

    def __deepcopy__(self, memo):
        # The engine uses `copy.deepcopy` to clone. Our attrs are
        # primitives; a shallow dict copy is equivalent and cheap.
        new = _FakeCalcModel()
        new.__dict__.update(self.__dict__)
        memo[id(self)] = new
        return new

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Fake {self.__dict__}>"

    # `_get_field_values` falls back to this when the field is NOT
    # overridden. The engine asserts on its presence.
    def get_selected_key_list(self, field_name):  # pragma: no cover
        # Never invoked in the tests below because every field we
        # exercise is provided via field_overrides. We still need the
        # attribute to exist so the `hasattr` guard in
        # `_get_field_values` passes.
        return []


# --------------------------------------------------------------------
# 7.15 — `_normalize_field_values` contract
# --------------------------------------------------------------------
class TestCluster07f_NormalizeFieldValues(SimpleTestCase):
    """7.15: Every shape ``get_selected_key_list`` might return must
    end up as a plain Python list the engine can iterate."""

    # -- 7.15 ----------------------------------------------------------
    def test_7_15_normalize_every_iterable_shape(self) -> None:
        """
        One scenario, seven sub-assertions with named messages so a
        regression in any single coercion branch surfaces the
        offending input type, not a generic diff.

        Customer-facing risk: ``get_selected_key_list`` is a
        subclass-overridable method. Real code returns anything from
        a plain list to a Django ``QuerySet`` to a generator. If
        normalization drops one of these, every combination for
        that subclass silently collapses to zero.
        """
        cases = [
            # (input, expected_result, why)
            (["a", "b"],           ["a", "b"],      "plain list passthrough"),
            (("a", "b"),           ["a", "b"],      "tuple → list"),
            ("single",             ["single"],      "string wrapped as single value (not iterated char-by-char)"),
            (b"bytes",             [b"bytes"],      "bytes wrapped as single value"),
            ({"k": "v"},           [{"k": "v"}],    "dict wrapped as single value (not iterated over keys)"),
            ((x for x in [1, 2]),  [1, 2],          "generator → list"),
            (42,                   [42],            "scalar wrapped as single value"),
            ([],                   [],              "empty list stays empty (signals no combinations)"),
        ]
        for raw, expected, why in cases:
            with self.subTest(why=why):
                self.assertEqual(
                    _normalize_field_values(raw), expected,
                    msg=(
                        f"_normalize_field_values({type(raw).__name__} → "
                        f"{raw!r}) expected {expected!r} ({why}); "
                        "drift here silently collapses every combinatorial "
                        "expansion for that subclass"
                    ),
                )


# --------------------------------------------------------------------
# 7.16 — Combination generator cartesian product
# --------------------------------------------------------------------
class TestCluster07f_CombinationGenerator(SimpleTestCase):
    """7.16 – 7.18: ``ModelCombinationGenerator.generate_model_combinations``."""

    # -- 7.16 ----------------------------------------------------------
    def test_7_16_cartesian_product_over_two_defining_fields(self) -> None:
        """
        Scenario 7.16: ``defining_fields=['region', 'product']`` with
        2 × 2 override values produces exactly 4 models, one per
        combination, with each model carrying the correct field values.

        This is the base promise of ``@defining_fields`` — the user
        writes a model once and the engine fans it out.
        """
        base = _FakeCalcModel(name="base", region="X", product="X")

        result = ModelCombinationGenerator.generate_model_combinations(
            base_model=base,
            defining_fields=["region", "product"],
            field_overrides={
                "region": ["US", "EU"],
                "product": ["A", "B"],
            },
        )

        combos = {(m.region, m.product) for m in result}
        self.assertEqual(
            combos,
            {("US", "A"), ("US", "B"), ("EU", "A"), ("EU", "B")},
            msg=(
                "Cartesian product over ['US','EU'] × ['A','B'] must "
                "produce exactly the four (region, product) pairs; "
                "drift here means entire periods or scenarios silently "
                "skip calculation in production"
            ),
        )
        self.assertEqual(
            len(result), 4,
            msg=f"Expected 4 combinations, got {len(result)}",
        )

    # -- 7.17 ----------------------------------------------------------
    def test_7_17_empty_defining_fields_returns_single_model(self) -> None:
        """
        Scenario 7.17: A ``CalculatedModel`` with no ``defining_fields``
        (i.e., a "simple" non-atomic calc) must produce exactly one
        model — the base — from the combination generator, not zero
        and not error.

        This is the degenerate base case. Drift means atomic-style
        calcs declared on a non-atomic class silently stop executing.
        """
        base = _FakeCalcModel(name="simple")

        result = ModelCombinationGenerator.generate_model_combinations(
            base_model=base,
            defining_fields=[],
            field_overrides={},
        )

        self.assertEqual(
            len(result), 1,
            msg="Empty defining_fields must yield exactly one (the base) model",
        )
        self.assertIs(
            result[0], base,
            msg="With no fields to expand, the base model must pass through identity-equal",
        )

    # -- 7.18 ----------------------------------------------------------
    def test_7_18_none_base_model_raises_combination_error(self) -> None:
        """
        Scenario 7.18: Passing ``None`` as the base model must raise
        ``ModelCombinationError`` — **not** ``AttributeError`` or a
        plain ``TypeError`` — so the upstream ``calculate_mixin``
        handler can report a typed failure instead of a generic
        traceback.
        """
        with self.assertRaises(ModelCombinationError) as ctx:
            ModelCombinationGenerator.generate_model_combinations(
                base_model=None,
                defining_fields=["x"],
                field_overrides={},
            )
        self.assertIn(
            "cannot be none", str(ctx.exception).lower(),
            msg="Error message must name the precondition failure",
        )


# --------------------------------------------------------------------
# 7.19 — Cluster manager — hierarchy + flatten round-trip
# --------------------------------------------------------------------
class TestCluster07f_ClusterManager(SimpleTestCase):
    """7.19 – 7.22: ``ModelClusterManager`` hierarchy + flatten."""

    # -- 7.19 ----------------------------------------------------------
    def test_7_19_clusters_group_by_parallelizable_field_values(self) -> None:
        """
        Scenario 7.19: Three models with (region, category) values
        ``[US/A, US/B, EU/A]`` must cluster as:

            { 'US': {'A': [m1], 'B': [m2]},
              'EU': {'A': [m3]} }

        This is the structure the Celery dispatcher walks. A drift
        here either:
          * fires too many parallel tasks (sub-groups scatter) or
          * fires too few (groups merge) — causing ordered calcs to
            race with their own dependencies.
        """
        m1 = _FakeCalcModel(region="US", category="A", id=1)
        m2 = _FakeCalcModel(region="US", category="B", id=2)
        m3 = _FakeCalcModel(region="EU", category="A", id=3)

        cluster = ModelClusterManager.create_clusters(
            models=[m1, m2, m3],
            parallelizable_fields=["region", "category"],
        )

        self.assertEqual(
            set(cluster.keys()), {"US", "EU"},
            msg="First-level keys must be the distinct values of the first parallelizable field",
        )
        self.assertEqual(
            set(cluster["US"].keys()), {"A", "B"},
            msg="Second-level keys under US must be the distinct categories within US",
        )
        self.assertEqual(cluster["US"]["A"], [m1])
        self.assertEqual(cluster["US"]["B"], [m2])
        self.assertEqual(cluster["EU"]["A"], [m3])

    # -- 7.20 ----------------------------------------------------------
    def test_7_20_empty_parallelizable_fields_returns_single_group(self) -> None:
        """
        Scenario 7.20: ``parallelizable_fields=[]`` means "run
        everything together" — the cluster must collapse to a single
        ``{None: [all models]}`` entry so ``flatten_clusters_to_groups``
        produces one group, not many.
        """
        models = [_FakeCalcModel(id=i) for i in range(3)]

        cluster = ModelClusterManager.create_clusters(
            models=models,
            parallelizable_fields=[],
        )
        self.assertEqual(cluster, {None: models})

        groups = ModelClusterManager.flatten_clusters_to_groups(cluster)
        self.assertEqual(
            groups, [models],
            msg=(
                "Empty parallelizable_fields must flatten to exactly "
                "one group containing every input model"
            ),
        )

    # -- 7.21 ----------------------------------------------------------
    def test_7_21_missing_parallelizable_field_raises_typed_error(self) -> None:
        """
        Scenario 7.21: A model that doesn't have the requested
        parallelizable attribute must trigger
        ``ModelClusteringError`` **before** the hierarchy build
        starts — otherwise the engine silently groups everything
        under ``None`` and the user's "parallelize by region"
        annotation does nothing.
        """
        m = _FakeCalcModel(region="US")  # no `category` attribute
        with self.assertRaises(ModelClusteringError) as ctx:
            ModelClusterManager.create_clusters(
                models=[m],
                parallelizable_fields=["region", "category"],
            )
        self.assertIn(
            "category", str(ctx.exception),
            msg="Error must name the missing field so the user can fix the declaration",
        )

    # -- 7.22 ----------------------------------------------------------
    def test_7_22_flatten_preserves_all_models_no_duplicates(self) -> None:
        """
        Scenario 7.22: ``create_clusters`` → ``flatten_clusters_to_groups``
        is a round-trip over the model set — every input model must
        appear exactly once in exactly one output group.

        Drift: a duplicated model means the same calc runs twice;
        a dropped model means a row silently never recalculates.
        Both have been real production incidents in similar engines.
        """
        models = [
            _FakeCalcModel(region="US", category="A", id=i)
            for i in range(2)
        ] + [
            _FakeCalcModel(region="EU", category="B", id=i)
            for i in range(2, 5)
        ]
        cluster = ModelClusterManager.create_clusters(
            models=models,
            parallelizable_fields=["region", "category"],
        )
        groups = ModelClusterManager.flatten_clusters_to_groups(cluster)

        flattened = [m for g in groups for m in g]
        self.assertEqual(
            sorted(m.id for m in flattened),
            sorted(m.id for m in models),
            msg=(
                "Every input model must appear exactly once across "
                "all groups after the create→flatten round-trip. "
                "Missing ids → drift in cluster flattening; "
                "duplicate ids → ambiguous Celery dispatch"
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

