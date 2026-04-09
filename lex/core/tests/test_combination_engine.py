"""
Unit tests for the ``ModelCombinationGenerator`` and ``ModelClusterManager``.

**What this tests (customer-visible behaviour)**

``CalculatedModelMixin.create()`` follows a four-step pipeline:

1. **Combination generation** – ``ModelCombinationGenerator`` expands
   ``defining_fields × get_selected_key_list`` into the Cartesian product
   of model instances.
2. **Preparation / deduplication** – existing DB rows with the same key
   combination are reused (idempotent create).
3. **Clustering** – ``ModelClusterManager`` groups the models by
   ``parallelizable_fields`` so each group can be dispatched to a
   separate Celery worker.
4. **Dispatch** – groups are sent to Celery or processed synchronously.

This module focuses on steps 1 and 3 — the pure-logic parts that can be
tested without a database.

**Why it matters**

Batch calculations in every customer project (e.g., running a forecast
for *every investor × every quarter*) depend on the combination engine
producing the correct cross-product and the cluster manager grouping them
correctly for parallel dispatch.

**Methodology**

- Minimal concrete subclasses are defined in the test module with
  ``managed = False`` — no DB tables required.
- ``ModelCombinationGenerator`` static methods are tested directly with
  known inputs.
- ``ModelClusterManager`` is tested with pre-built model lists to
  verify grouping and flattening.
- Edge cases: empty fields, ``None`` override values, lazy iterables,
  and missing ``get_selected_key_list`` are all covered.

See also
--------
- ``docs/features/processing/batch calculations.md``
- ``docs/reference/CalculatedModelMixin Internals.md``
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.db import models
from django.test import SimpleTestCase

from lex.core.mixins.CalculatedModelMixin import (
    ModelCombinationGenerator,
    ModelClusterManager,
    _normalize_field_values,
)
from lex.core.exceptions import ModelCombinationError, ModelClusteringError


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Test model stubs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ComboModel:
    """Lightweight stand-in that supports attr access without Django ORM."""

    defining_fields = ["region", "product"]

    def __init__(self, region="", product=""):
        self.region = region
        self.product = product

    def get_selected_key_list(self, key):
        mapping = {
            "region": ["US", "EU"],
            "product": ["A", "B"],
        }
        return mapping.get(key, [])


class SingleFieldModel:
    """Model with one defining field."""

    defining_fields = ["quarter"]

    def __init__(self, quarter=""):
        self.quarter = quarter

    def get_selected_key_list(self, key):
        if key == "quarter":
            return ["Q1", "Q2", "Q3"]
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1.  _normalize_field_values
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNormalizeFieldValues(SimpleTestCase):
    """Prove ``_normalize_field_values`` handles every iterable type."""

    def test_list_returned_as_is(self):
        """A plain list is returned unchanged."""
        self.assertEqual(_normalize_field_values(["a", "b"]), ["a", "b"])

    def test_tuple_converted_to_list(self):
        """A tuple is converted to a list."""
        self.assertEqual(_normalize_field_values(("x", "y")), ["x", "y"])

    def test_string_wrapped_in_list(self):
        """A bare string becomes a single-element list (not char-split)."""
        self.assertEqual(_normalize_field_values("hello"), ["hello"])

    def test_bytes_wrapped_in_list(self):
        """Bytes are wrapped like strings — not iterated."""
        self.assertEqual(_normalize_field_values(b"raw"), [b"raw"])

    def test_dict_wrapped_in_list(self):
        """A dict is treated as a single value, not iterated."""
        d = {"key": "val"}
        self.assertEqual(_normalize_field_values(d), [d])

    def test_generator_materialised(self):
        """A generator is materialised into a list."""
        gen = (x for x in range(3))
        self.assertEqual(_normalize_field_values(gen), [0, 1, 2])

    def test_scalar_wrapped(self):
        """A plain integer becomes a single-element list."""
        self.assertEqual(_normalize_field_values(42), [42])

    def test_set_materialised(self):
        """A set is materialised (order may vary, but length is preserved)."""
        result = _normalize_field_values({1, 2, 3})
        self.assertEqual(len(result), 3)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2.  ModelCombinationGenerator — field values
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetFieldValues(SimpleTestCase):
    """Prove ``_get_field_values`` dispatches to overrides or model method."""

    def test_uses_override_when_present(self):
        """Override values take precedence over ``get_selected_key_list``."""
        model = ComboModel()
        values = ModelCombinationGenerator._get_field_values(
            model, "region", {"region": ["JP"]}
        )
        self.assertEqual(values, ["JP"])

    def test_uses_model_method_when_no_override(self):
        """Falls back to ``get_selected_key_list`` when no override."""
        model = ComboModel()
        values = ModelCombinationGenerator._get_field_values(
            model, "region", {}
        )
        self.assertEqual(values, ["US", "EU"])

    def test_none_override_raises(self):
        """``None`` as an override value raises ``ModelCombinationError``."""
        model = ComboModel()
        with self.assertRaises(ModelCombinationError):
            ModelCombinationGenerator._get_field_values(
                model, "region", {"region": None}
            )

    def test_missing_method_raises(self):
        """Model without ``get_selected_key_list`` raises immediately."""
        bare = SimpleNamespace(region="X")  # no method
        with self.assertRaises(ModelCombinationError):
            ModelCombinationGenerator._get_field_values(bare, "region", {})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3.  ModelCombinationGenerator — full expansion
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGenerateModelCombinations(SimpleTestCase):
    """Prove Cartesian-product expansion of defining fields."""

    def test_two_fields_produces_cartesian_product(self):
        """2 regions × 2 products → 4 combinations."""
        base = ComboModel()
        combos = ModelCombinationGenerator.generate_model_combinations(
            base, base.defining_fields, {}
        )
        self.assertEqual(len(combos), 4)

        pairs = [(m.region, m.product) for m in combos]
        self.assertIn(("US", "A"), pairs)
        self.assertIn(("US", "B"), pairs)
        self.assertIn(("EU", "A"), pairs)
        self.assertIn(("EU", "B"), pairs)

    def test_single_field_expansion(self):
        """One field with 3 values → 3 combinations."""
        base = SingleFieldModel()
        combos = ModelCombinationGenerator.generate_model_combinations(
            base, base.defining_fields, {}
        )
        self.assertEqual(len(combos), 3)
        quarters = [m.quarter for m in combos]
        self.assertEqual(sorted(quarters), ["Q1", "Q2", "Q3"])

    def test_override_reduces_combinations(self):
        """Overriding one field to a single value halves the product."""
        base = ComboModel()
        combos = ModelCombinationGenerator.generate_model_combinations(
            base, base.defining_fields, {"region": ["US"]}
        )
        self.assertEqual(len(combos), 2)
        self.assertTrue(all(m.region == "US" for m in combos))

    def test_empty_defining_fields_returns_base(self):
        """No defining_fields → the base model itself is returned."""
        base = ComboModel()
        combos = ModelCombinationGenerator.generate_model_combinations(
            base, [], {}
        )
        self.assertEqual(len(combos), 1)
        self.assertIs(combos[0], base)

    def test_none_base_model_raises(self):
        """``None`` as base model raises ``ModelCombinationError``."""
        with self.assertRaises(ModelCombinationError):
            ModelCombinationGenerator.generate_model_combinations(
                None, ["x"], {}
            )

    def test_empty_field_values_returns_empty(self):
        """If one field has no values, the entire expansion is empty."""

        class EmptyModel:
            defining_fields = ["color"]
            color = ""

            def get_selected_key_list(self, key):
                return []

        combos = ModelCombinationGenerator.generate_model_combinations(
            EmptyModel(), ["color"], {}
        )
        self.assertEqual(combos, [])

    def test_combinations_are_independent_copies(self):
        """Mutating one combination does not affect another."""
        base = ComboModel()
        combos = ModelCombinationGenerator.generate_model_combinations(
            base, base.defining_fields, {}
        )
        combos[0].region = "MODIFIED"
        self.assertNotEqual(combos[1].region, "MODIFIED")

    def test_dotted_field_name_takes_last_segment(self):
        """``parent.child`` is resolved as just ``child``."""

        class DottedModel:
            defining_fields = ["parent.color"]
            color = ""

            def get_selected_key_list(self, key):
                if key == "color":
                    return ["red", "blue"]
                return []

        base = DottedModel()
        combos = ModelCombinationGenerator.generate_model_combinations(
            base, base.defining_fields, {}
        )
        self.assertEqual(len(combos), 2)
        colors = [m.color for m in combos]
        self.assertIn("red", colors)
        self.assertIn("blue", colors)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.  ModelClusterManager — clustering
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCreateClusters(SimpleTestCase):
    """Prove ``create_clusters`` groups models by parallelizable fields."""

    def _make_model(self, **kwargs):
        return SimpleNamespace(**kwargs)

    def test_single_field_grouping(self):
        """Models grouped by one field → dict keyed by field value."""
        models = [
            self._make_model(region="US"),
            self._make_model(region="EU"),
            self._make_model(region="US"),
        ]
        clusters = ModelClusterManager.create_clusters(models, ["region"])

        self.assertIn("US", clusters)
        self.assertIn("EU", clusters)
        self.assertEqual(len(clusters["US"]), 2)
        self.assertEqual(len(clusters["EU"]), 1)

    def test_two_field_hierarchy(self):
        """Two fields create a nested dict: {region: {product: [models]}}."""
        models = [
            self._make_model(region="US", product="A"),
            self._make_model(region="US", product="B"),
            self._make_model(region="EU", product="A"),
        ]
        clusters = ModelClusterManager.create_clusters(
            models, ["region", "product"]
        )

        self.assertIsInstance(clusters["US"], dict)
        self.assertEqual(len(clusters["US"]["A"]), 1)
        self.assertEqual(len(clusters["US"]["B"]), 1)
        self.assertEqual(len(clusters["EU"]["A"]), 1)

    def test_no_parallelizable_fields_single_group(self):
        """No fields → all models in a single ``{None: [...]}`` group."""
        models = [self._make_model(x=1), self._make_model(x=2)]
        clusters = ModelClusterManager.create_clusters(models, [])

        self.assertIn(None, clusters)
        self.assertEqual(len(clusters[None]), 2)

    def test_empty_model_list_returns_empty(self):
        """No models → empty dict."""
        clusters = ModelClusterManager.create_clusters([], ["region"])
        self.assertEqual(clusters, {})

    def test_missing_field_raises(self):
        """Model without the parallelizable field → ``ModelClusteringError``."""
        models = [SimpleNamespace(x=1)]  # no 'region' attr
        with self.assertRaises(ModelClusteringError):
            ModelClusterManager.create_clusters(models, ["region"])

    def test_none_field_value_used_as_key(self):
        """``None`` values are used as cluster keys (not skipped)."""
        models = [self._make_model(region=None)]
        clusters = ModelClusterManager.create_clusters(models, ["region"])
        self.assertIn(None, clusters)
        self.assertEqual(len(clusters[None]), 1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5.  ModelClusterManager — flatten
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFlattenClustersToGroups(SimpleTestCase):
    """Prove ``flatten_clusters_to_groups`` extracts leaf lists correctly."""

    def test_flat_dict_with_lists(self):
        """Single-level dict with list values → one group per key."""
        cluster = {"US": ["m1", "m2"], "EU": ["m3"]}
        groups = ModelClusterManager.flatten_clusters_to_groups(cluster)
        self.assertEqual(len(groups), 2)

    def test_nested_dict_flattened(self):
        """Nested dict → leaf lists extracted recursively."""
        cluster = {
            "US": {"A": ["m1"], "B": ["m2"]},
            "EU": {"A": ["m3"]},
        }
        groups = ModelClusterManager.flatten_clusters_to_groups(cluster)
        self.assertEqual(len(groups), 3)

    def test_empty_dict_returns_empty(self):
        """Empty dict → empty list."""
        self.assertEqual(ModelClusterManager.flatten_clusters_to_groups({}), [])

    def test_empty_leaf_lists_skipped(self):
        """Empty leaf lists are not included in the output."""
        cluster = {"US": ["m1"], "EU": []}
        groups = ModelClusterManager.flatten_clusters_to_groups(cluster)
        self.assertEqual(len(groups), 1)

    def test_non_dict_input_raises(self):
        """Non-dict input → ``ModelClusteringError``."""
        with self.assertRaises(ModelClusteringError):
            ModelClusterManager.flatten_clusters_to_groups("not a dict")
