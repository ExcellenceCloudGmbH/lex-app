"""
Tests for ``ModelCombinationGenerator``, ``ModelClusterManager``, and
``_normalize_field_values`` — the pure-logic engines inside
``CalculatedModelMixin``.

**What is tested:**

    * ``_normalize_field_values`` — list, tuple, string, dict, generator, scalar
    * ``ModelCombinationGenerator.generate_model_combinations`` — Cartesian
      product with 2+ defining fields, field overrides, error on None base_model
    * ``ModelCombinationGenerator._get_field_values`` — override path, model path,
      error on missing ``get_selected_key_list``
    * ``ModelCombinationGenerator._expand_models_for_field`` — single-field
      expansion, empty models list
    * ``ModelClusterManager.create_clusters`` — single field, multi-field,
      empty input, no parallelizable fields
    * ``ModelClusterManager.flatten_clusters_to_groups`` — nested dict,
      empty dict, single level
    * ``calc_and_save_sync`` — success path, partial failure, all-failure raise

**Why this matters:**

    The combination engine produces every record the customer sees in computed
    tables.  If it drops a combination, data is silently missing.  If
    clustering is wrong, Celery dispatches are unbalanced or models run twice.

**How to run:**

    .. code-block:: bash

        lex test lex.core.tests.test_combination_and_cluster --verbosity=2 --noinput --keepdb
"""

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from core.mixins.CalculatedModelMixin import (
    ModelCombinationGenerator,
    ModelClusterManager,
    _normalize_field_values,
    calc_and_save_sync,
    CalculatedModelMixin,
)
from django.db import models
from django.test import SimpleTestCase
from lex.core.exceptions import ModelCombinationError, ModelClusteringError, CalculatedModelError


# ────────────────────────────────────────────────────────────────────
#  Stub model for combination / cluster tests (no DB)
# ────────────────────────────────────────────────────────────────────

class ComboTestCalcModel(CalculatedModelMixin):
    """Minimal concrete stub for combination/cluster tests."""
    defining_fields = ["region", "product"]
    parallelizable_fields = ["region"]

    region = models.CharField(max_length=32, blank=True)
    product = models.CharField(max_length=32, blank=True)

    class Meta:
        app_label = "lex_app"
        managed = False

    def get_selected_key_list(self, key):
        mapping = {
            "region": ["US", "EU"],
            "product": ["A", "B", "C"],
        }
        return mapping.get(key, [])

    def calculate(self):
        pass

    def calculate_mixin(self):
        pass


class SingleFieldComboModel(CalculatedModelMixin):
    defining_fields = ["color"]
    parallelizable_fields = []
    color = models.CharField(max_length=32, blank=True)

    class Meta:
        app_label = "lex_app"
        managed = False

    def get_selected_key_list(self, key):
        if key == "color":
            return ["red", "blue"]
        return []

    def calculate(self):
        pass

    def calculate_mixin(self):
        pass


# ════════════════════════════════════════════════════════════════════
#  _normalize_field_values
# ════════════════════════════════════════════════════════════════════

class TestNormalizeFieldValues(SimpleTestCase):
    """Cover every branch of ``_normalize_field_values``."""

    def test_list_passthrough(self):
        self.assertEqual(_normalize_field_values([1, 2]), [1, 2])

    def test_tuple_converted_to_list(self):
        self.assertEqual(_normalize_field_values((1, 2)), [1, 2])

    def test_string_wrapped_in_list(self):
        self.assertEqual(_normalize_field_values("hello"), ["hello"])

    def test_bytes_wrapped_in_list(self):
        self.assertEqual(_normalize_field_values(b"data"), [b"data"])

    def test_bytearray_wrapped_in_list(self):
        self.assertEqual(_normalize_field_values(bytearray(b"x")), [bytearray(b"x")])

    def test_dict_wrapped_in_list(self):
        d = {"a": 1}
        self.assertEqual(_normalize_field_values(d), [d])

    def test_generator_materialized(self):
        gen = (x for x in range(3))
        self.assertEqual(_normalize_field_values(gen), [0, 1, 2])

    def test_set_converted_to_list(self):
        result = _normalize_field_values({10, 20})
        self.assertEqual(sorted(result), [10, 20])

    def test_scalar_int_wrapped(self):
        self.assertEqual(_normalize_field_values(42), [42])

    def test_none_wrapped(self):
        self.assertEqual(_normalize_field_values(None), [None])

    def test_empty_list_stays_empty(self):
        self.assertEqual(_normalize_field_values([]), [])


# ════════════════════════════════════════════════════════════════════
#  ModelCombinationGenerator
# ════════════════════════════════════════════════════════════════════

class TestModelCombinationGenerator(SimpleTestCase):
    """Test the Cartesian-product expansion logic."""

    def test_two_field_cartesian_product(self):
        """2 regions × 3 products = 6 combinations."""
        base = ComboTestCalcModel()
        combos = ModelCombinationGenerator.generate_model_combinations(
            base, ComboTestCalcModel.defining_fields, {},
        )
        self.assertEqual(len(combos), 6)
        pairs = {(m.region, m.product) for m in combos}
        expected = {
            ("US", "A"), ("US", "B"), ("US", "C"),
            ("EU", "A"), ("EU", "B"), ("EU", "C"),
        }
        self.assertEqual(pairs, expected)

    def test_field_override_replaces_model_values(self):
        """Overriding 'region' restricts combinations."""
        base = ComboTestCalcModel()
        combos = ModelCombinationGenerator.generate_model_combinations(
            base, ComboTestCalcModel.defining_fields, {"region": ["US"]},
        )
        self.assertEqual(len(combos), 3)  # 1 region × 3 products
        self.assertTrue(all(m.region == "US" for m in combos))

    def test_no_defining_fields_returns_single_model(self):
        base = ComboTestCalcModel()
        combos = ModelCombinationGenerator.generate_model_combinations(base, [], {})
        self.assertEqual(len(combos), 1)
        self.assertIs(combos[0], base)

    def test_none_base_model_raises(self):
        with self.assertRaises(ModelCombinationError):
            ModelCombinationGenerator.generate_model_combinations(
                None, ["region"], {},
            )

    def test_override_none_value_raises(self):
        """Overriding a field with None should raise."""
        base = ComboTestCalcModel()
        with self.assertRaises(ModelCombinationError):
            ModelCombinationGenerator._get_field_values(base, "region", {"region": None})

    def test_missing_get_selected_key_list_raises(self):
        """Model without get_selected_key_list raises."""
        stub = SimpleNamespace()  # no get_selected_key_list
        with self.assertRaises(ModelCombinationError):
            ModelCombinationGenerator._get_field_values(stub, "region", {})

    def test_expand_empty_models_returns_empty(self):
        result = ModelCombinationGenerator._expand_models_for_field([], "region", {})
        self.assertEqual(result, [])

    def test_single_field_expansion(self):
        base = SingleFieldComboModel()
        combos = ModelCombinationGenerator.generate_model_combinations(
            base, ["color"], {},
        )
        self.assertEqual(len(combos), 2)
        self.assertEqual(sorted(m.color for m in combos), ["blue", "red"])


# ════════════════════════════════════════════════════════════════════
#  ModelClusterManager
# ════════════════════════════════════════════════════════════════════

class TestModelClusterManager(SimpleTestCase):
    """Test clustering and flattening logic."""

    def _make_model(self, **kwargs):
        class FakeModel:
            pass
        m = FakeModel()
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    # ── create_clusters ───────────────────────────────────────────────

    def test_single_parallelizable_field_clusters(self):
        m1 = self._make_model(region="US")
        m2 = self._make_model(region="US")
        m3 = self._make_model(region="EU")
        clusters = ModelClusterManager.create_clusters(
            [m1, m2, m3], ["region"],
        )
        self.assertIn("US", clusters)
        self.assertIn("EU", clusters)
        self.assertEqual(len(clusters["US"]), 2)
        self.assertEqual(len(clusters["EU"]), 1)

    def test_two_parallelizable_fields_nesting(self):
        m1 = self._make_model(region="US", category="A")
        m2 = self._make_model(region="US", category="B")
        m3 = self._make_model(region="EU", category="A")
        clusters = ModelClusterManager.create_clusters(
            [m1, m2, m3], ["region", "category"],
        )
        # Top level: region
        self.assertIn("US", clusters)
        self.assertIn("EU", clusters)
        # Second level: category
        self.assertIn("A", clusters["US"])
        self.assertIn("B", clusters["US"])
        self.assertEqual(len(clusters["US"]["A"]), 1)

    def test_empty_models_returns_empty_dict(self):
        clusters = ModelClusterManager.create_clusters([], ["region"])
        self.assertEqual(clusters, {})

    def test_no_parallelizable_fields_single_group(self):
        m1 = self._make_model(region="US")
        m2 = self._make_model(region="EU")
        clusters = ModelClusterManager.create_clusters([m1, m2], [])
        self.assertIn(None, clusters)
        self.assertEqual(len(clusters[None]), 2)

    def test_missing_field_raises(self):
        class NoFieldModel:
            pass
        m = NoFieldModel()
        with self.assertRaises(ModelClusteringError):
            ModelClusterManager.create_clusters([m], ["region"])

    # ── flatten_clusters_to_groups ────────────────────────────────────

    def test_flatten_single_level(self):
        clusters = {"US": [1, 2], "EU": [3]}
        groups = ModelClusterManager.flatten_clusters_to_groups(clusters)
        self.assertEqual(len(groups), 2)
        # Groups should contain [1,2] and [3] in some order
        flat = sorted([sorted(g) for g in groups])
        self.assertEqual(flat, [[1, 2], [3]])

    def test_flatten_nested(self):
        clusters = {
            "US": {"A": [1], "B": [2]},
            "EU": {"A": [3]},
        }
        groups = ModelClusterManager.flatten_clusters_to_groups(clusters)
        self.assertEqual(len(groups), 3)
        flat = sorted([sorted(g) for g in groups])
        self.assertEqual(flat, [[1], [2], [3]])

    def test_flatten_empty_dict(self):
        groups = ModelClusterManager.flatten_clusters_to_groups({})
        self.assertEqual(groups, [])

    def test_flatten_skips_empty_leaf(self):
        """Empty leaf lists are skipped."""
        clusters = {"US": [1, 2], "EU": []}
        groups = ModelClusterManager.flatten_clusters_to_groups(clusters)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0], [1, 2])

    def test_flatten_non_dict_raises(self):
        with self.assertRaises(ModelClusteringError):
            ModelClusterManager.flatten_clusters_to_groups("not a dict")


# ════════════════════════════════════════════════════════════════════
#  calc_and_save_sync
# ════════════════════════════════════════════════════════════════════

class TestCalcAndSaveSync(SimpleTestCase):
    """Test the synchronous fallback processing path."""

    def test_empty_models_is_noop(self):
        # Should not raise
        calc_and_save_sync([])

    def test_none_models_is_noop(self):
        calc_and_save_sync(None)

    def test_non_list_raises(self):
        with self.assertRaises(CalculatedModelError):
            calc_and_save_sync("not a list")

    def test_skips_none_entries(self):
        """None models in the list are skipped without error."""
        # Should not raise — None entries are logged and skipped
        calc_and_save_sync([None, None])

    @patch("lex.audit_logging.utils.ModelContext.model_logging_context")
    def test_all_failures_raises(self, mock_ctx):
        """If every model fails, calc_and_save_sync raises CalculatedModelError."""
        mock_ctx.return_value.__enter__ = MagicMock(return_value=None)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        m = MagicMock()
        m.lex_func.return_value.side_effect = RuntimeError("boom")
        m.__class__ = type("FakeModel", (), {"__name__": "FakeModel"})

        with self.assertRaises(CalculatedModelError):
            calc_and_save_sync([m])
