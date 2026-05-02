"""
Cluster 7f: Combination engine + cluster manager (pure-logic helpers).

Targets the pure-Python helpers in
``lex/core/mixins/CalculatedModelMixin.py`` that drive the
combinatorial expansion engine and clustering hierarchy behind every
non-atomic ``CalculatedModel``:

* ``_normalize_field_values`` — coerces the result of
  ``get_selected_key_list`` into a concrete list (every iterable
  shape, plus ``None`` / scalar / mapping / set / range).
* ``_flatten`` — list-of-lists → flat list utility used by the
  expansion engine.
* ``ModelCombinationGenerator``
    - ``generate_model_combinations`` (public) — cartesian product
      across defining fields.
    - ``_get_field_values`` (private) — override-vs-model lookup
      with full error wrapping.
    - ``_expand_models_for_field`` (private) — per-model expansion
      including deepcopy/setattr failure paths.
* ``ModelClusterManager``
    - ``create_clusters`` — empty / single-field / multi-field /
      missing-field validation / ``None``-key handling.
    - ``_build_cluster_hierarchy`` (private) — direct guards.
    - ``flatten_clusters_to_groups`` — empty/non-dict/invalid-leaf
      validation, empty-leaf skipping, tuple-leaf acceptance,
      arbitrary nesting depth.

Why unit-level: these helpers take plain Python lists + dicts and
return plain Python lists + dicts. They do **not** touch the DB,
Celery, or the ``LexModel`` lifecycle. The end-to-end
``CalculatedModelMixin.create()`` orchestrator is covered by 7g; the
Celery dispatch path by 7h.

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
    _flatten,
    _normalize_field_values,
)


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

    # If set on the instance, controls what get_selected_key_list returns
    # per-field. Falsy means "fall back to default: empty list".
    _key_list_map: dict | None = None
    # If set, get_selected_key_list raises this exception (to exercise
    # the model-side failure path in _get_field_values).
    _key_list_raises: BaseException | None = None
    # If True, deepcopy raises — exercises the deepcopy-failure branch in
    # _expand_models_for_field.
    _deepcopy_blows_up: bool = False

    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)

    def __copy__(self):
        new = _FakeCalcModel()
        new.__dict__.update(self.__dict__)
        return new

    def __deepcopy__(self, memo):
        if getattr(self, "_deepcopy_blows_up", False):
            raise RuntimeError("simulated deepcopy failure")
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
    def get_selected_key_list(self, field_name):
        if self._key_list_raises is not None:
            raise self._key_list_raises
        if self._key_list_map is not None:
            # Returning whatever the test wired up — including None,
            # tuples, generators, etc., so we can also exercise
            # _normalize_field_values via this path.
            return self._key_list_map.get(field_name, [])
        return []


class _NoKeyListModel:
    """Model variant deliberately *missing* ``get_selected_key_list``.

    Exercises the ``hasattr(model, 'get_selected_key_list')`` guard in
    ``ModelCombinationGenerator._get_field_values``.
    """

    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)

    def __deepcopy__(self, memo):
        new = _NoKeyListModel()
        new.__dict__.update(self.__dict__)
        memo[id(self)] = new
        return new


# --------------------------------------------------------------------
# 7.15 — `_normalize_field_values` contract
# --------------------------------------------------------------------
class TestCluster07f_NormalizeFieldValues(SimpleTestCase):
    """7.15: Every shape ``get_selected_key_list`` might return must
    end up as a plain Python list the engine can iterate."""

    def test_7_15_normalize_every_iterable_shape(self) -> None:
        """One scenario, many sub-assertions for each branch of
        ``_normalize_field_values``."""
        gen = (x for x in [1, 2])
        cases = [
            (["a", "b"],          ["a", "b"],          "plain list passthrough"),
            (("a", "b"),          ["a", "b"],          "tuple → list"),
            ("single",            ["single"],          "string wrapped (not iterated char-by-char)"),
            (b"bytes",            [b"bytes"],          "bytes wrapped"),
            (bytearray(b"ba"),    [bytearray(b"ba")],  "bytearray wrapped"),
            ({"k": "v"},          [{"k": "v"}],        "dict wrapped (mapping branch)"),
            (gen,                 [1, 2],              "generator → list"),
            (range(3),            [0, 1, 2],           "range → list (iterable branch)"),
            (42,                  [42],                "scalar wrapped"),
            (None,                [None],              "None wrapped (non-iterable fallback)"),
            ([],                  [],                  "empty list stays empty"),
        ]
        for raw, expected, why in cases:
            with self.subTest(why=why):
                self.assertEqual(_normalize_field_values(raw), expected, msg=why)

    def test_7_15b_normalize_set_and_frozenset_are_listified(self) -> None:
        """Sets/frozensets are unordered iterables — they must round-trip
        to a list with identical membership (order is not guaranteed)."""
        for raw in ({1, 2, 3}, frozenset({"a", "b"})):
            with self.subTest(input_type=type(raw).__name__):
                result = _normalize_field_values(raw)
                self.assertIsInstance(result, list)
                self.assertEqual(set(result), set(raw))


# --------------------------------------------------------------------
# 7.15c — `_flatten` utility
# --------------------------------------------------------------------
class TestCluster07f_FlattenHelper(SimpleTestCase):
    """``_flatten`` collapses a list-of-lists to a single list — used by
    ``_expand_models_for_field`` to merge per-model expanded groups."""

    def test_7_15c_flatten_basic(self) -> None:
        self.assertEqual(_flatten([[1, 2], [3], [4, 5]]), [1, 2, 3, 4, 5])

    def test_7_15c_flatten_empty_outer_and_inner(self) -> None:
        self.assertEqual(_flatten([]), [])
        self.assertEqual(_flatten([[], [], []]), [])
        self.assertEqual(_flatten([[1], [], [2]]), [1, 2])


# --------------------------------------------------------------------
# 7.16 – 7.18 — Combination generator (public API)
# --------------------------------------------------------------------
class TestCluster07f_CombinationGenerator(SimpleTestCase):
    """``ModelCombinationGenerator.generate_model_combinations``."""

    # -- 7.16 ----------------------------------------------------------
    def test_7_16_cartesian_product_over_two_defining_fields(self) -> None:
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
        self.assertEqual(combos, {("US", "A"), ("US", "B"), ("EU", "A"), ("EU", "B")})
        self.assertEqual(len(result), 4)

    # -- 7.17 ----------------------------------------------------------
    def test_7_17_empty_defining_fields_returns_single_model(self) -> None:
        base = _FakeCalcModel(name="simple")
        result = ModelCombinationGenerator.generate_model_combinations(
            base_model=base, defining_fields=[], field_overrides={},
        )
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], base)

    # -- 7.18 ----------------------------------------------------------
    def test_7_18_none_base_model_raises_combination_error(self) -> None:
        with self.assertRaises(ModelCombinationError) as ctx:
            ModelCombinationGenerator.generate_model_combinations(
                base_model=None, defining_fields=["x"], field_overrides={},
            )
        self.assertIn("cannot be none", str(ctx.exception).lower())

    # -- 7.18a ---------------------------------------------------------
    def test_7_18a_single_defining_field_expansion(self) -> None:
        """Single field expansion is the simplest non-trivial case —
        guarantees the loop body runs exactly once and produces N copies."""
        base = _FakeCalcModel(region="X")
        result = ModelCombinationGenerator.generate_model_combinations(
            base_model=base,
            defining_fields=["region"],
            field_overrides={"region": ["US", "EU", "APAC"]},
        )
        self.assertEqual(sorted(m.region for m in result), ["APAC", "EU", "US"])

    # -- 7.18b ---------------------------------------------------------
    def test_7_18b_overrides_processed_before_non_overridden_fields(self) -> None:
        """Fields that have an override are processed first (sort key
        ``0 if x in overrides else 1``). We verify ordering by capturing
        the call sequence into ``_get_field_values``."""
        seen: list[str] = []
        original = ModelCombinationGenerator._get_field_values

        def spy(model, field_name, overrides):
            seen.append(field_name)
            return original(model, field_name, overrides)

        ModelCombinationGenerator._get_field_values = staticmethod(spy)
        try:
            base = _FakeCalcModel()
            base._key_list_map = {"a": ["a1", "a2"], "b": ["b1"]}
            ModelCombinationGenerator.generate_model_combinations(
                base_model=base,
                defining_fields=["a", "b"],   # 'a' has no override; 'b' does
                field_overrides={"b": ["b1"]},
            )
        finally:
            ModelCombinationGenerator._get_field_values = staticmethod(original)

        # 'b' (overridden) is queried at least once strictly before 'a'.
        first_b = seen.index("b") if "b" in seen else -1
        first_a = seen.index("a") if "a" in seen else -1
        self.assertGreaterEqual(first_b, 0)
        self.assertGreaterEqual(first_a, 0)
        self.assertLess(first_b, first_a, f"call order was {seen!r}")

    # -- 7.18c ---------------------------------------------------------
    def test_7_18c_dotted_field_names_use_last_segment(self) -> None:
        """``parent.child`` should set ``child`` on each model copy."""
        base = _FakeCalcModel()
        result = ModelCombinationGenerator.generate_model_combinations(
            base_model=base,
            defining_fields=["parent.child"],
            field_overrides={"child": ["x", "y"]},
        )
        self.assertEqual(sorted(getattr(m, "child") for m in result), ["x", "y"])

    # -- 7.18d ---------------------------------------------------------
    def test_7_18d_falls_back_to_get_selected_key_list_when_no_override(self) -> None:
        base = _FakeCalcModel()
        base._key_list_map = {"region": ["US", "EU"]}
        result = ModelCombinationGenerator.generate_model_combinations(
            base_model=base,
            defining_fields=["region"],
            field_overrides={},
        )
        self.assertEqual(sorted(m.region for m in result), ["EU", "US"])

    # -- 7.18e ---------------------------------------------------------
    def test_7_18e_empty_field_values_short_circuits_to_zero(self) -> None:
        """An empty value list for any field prunes the whole branch and
        the engine returns ``[]`` (not an error)."""
        base = _FakeCalcModel()
        result = ModelCombinationGenerator.generate_model_combinations(
            base_model=base,
            defining_fields=["region", "product"],
            field_overrides={"region": ["US"], "product": []},
        )
        self.assertEqual(result, [])

    # -- 7.18f ---------------------------------------------------------
    def test_7_18f_none_override_raises_typed_error(self) -> None:
        with self.assertRaises(ModelCombinationError) as ctx:
            ModelCombinationGenerator.generate_model_combinations(
                base_model=_FakeCalcModel(),
                defining_fields=["region"],
                field_overrides={"region": None},
            )
        self.assertIn("region", str(ctx.exception))

    # -- 7.18g ---------------------------------------------------------
    def test_7_18g_get_selected_key_list_returning_none_raises(self) -> None:
        base = _FakeCalcModel()
        base._key_list_map = {"region": None}
        with self.assertRaises(ModelCombinationError) as ctx:
            ModelCombinationGenerator.generate_model_combinations(
                base_model=base,
                defining_fields=["region"],
                field_overrides={},
            )
        self.assertIn("region", str(ctx.exception))

    # -- 7.18h ---------------------------------------------------------
    def test_7_18h_get_selected_key_list_raising_is_wrapped(self) -> None:
        base = _FakeCalcModel()
        base._key_list_raises = ValueError("boom")
        with self.assertRaises(ModelCombinationError) as ctx:
            ModelCombinationGenerator.generate_model_combinations(
                base_model=base,
                defining_fields=["region"],
                field_overrides={},
            )
        # `from get_values_error` chain preserves the original cause.
        self.assertIsInstance(ctx.exception.__cause__, (ValueError, Exception))

    # -- 7.18i ---------------------------------------------------------
    def test_7_18i_model_without_get_selected_key_list_raises(self) -> None:
        base = _NoKeyListModel(region="US")
        with self.assertRaises(ModelCombinationError) as ctx:
            ModelCombinationGenerator.generate_model_combinations(
                base_model=base,
                defining_fields=["region"],
                field_overrides={},
            )
        self.assertIn("get_selected_key_list", str(ctx.exception))

    # -- 7.18j ---------------------------------------------------------
    def test_7_18j_deepcopy_failure_is_wrapped(self) -> None:
        base = _FakeCalcModel(region="X")
        base._deepcopy_blows_up = True
        with self.assertRaises(ModelCombinationError) as ctx:
            ModelCombinationGenerator.generate_model_combinations(
                base_model=base,
                defining_fields=["region"],
                field_overrides={"region": ["US", "EU"]},
            )
        msg = str(ctx.exception).lower()
        # The wrapper either reports the field-expansion failure or the
        # specific copy failure depending on which `try` catches first.
        self.assertTrue("region" in msg)


# --------------------------------------------------------------------
# 7.18k – 7.18m — `_get_field_values` directly (private helper)
# --------------------------------------------------------------------
class TestCluster07f_GetFieldValues(SimpleTestCase):
    """Direct tests of ``ModelCombinationGenerator._get_field_values`` to
    cover branches not easily reachable via the public API."""

    def test_7_18k_override_path_normalizes_tuple(self) -> None:
        m = _FakeCalcModel()
        result = ModelCombinationGenerator._get_field_values(
            m, "region", {"region": ("US", "EU")},
        )
        self.assertEqual(result, ["US", "EU"])

    def test_7_18l_no_override_uses_model_method(self) -> None:
        m = _FakeCalcModel()
        m._key_list_map = {"region": ["US"]}
        result = ModelCombinationGenerator._get_field_values(m, "region", {})
        self.assertEqual(result, ["US"])

    def test_7_18m_unexpected_error_is_wrapped(self) -> None:
        """Trigger the catch-all ``except Exception`` by passing an object
        that raises on ``__contains__`` (i.e., on ``in field_overrides``)."""

        class HostileDict(dict):
            def __contains__(self, item):
                raise RuntimeError("hostile lookup")

        with self.assertRaises(ModelCombinationError):
            ModelCombinationGenerator._get_field_values(
                _FakeCalcModel(), "region", HostileDict(),
            )


# --------------------------------------------------------------------
# 7.18n – 7.18p — `_expand_models_for_field` directly
# --------------------------------------------------------------------
class TestCluster07f_ExpandModelsForField(SimpleTestCase):
    """Direct tests of ``ModelCombinationGenerator._expand_models_for_field``."""

    def test_7_18n_empty_models_returns_empty(self) -> None:
        self.assertEqual(
            ModelCombinationGenerator._expand_models_for_field([], "x", {"x": [1]}),
            [],
        )

    def test_7_18o_per_model_empty_values_prunes_only_that_model(self) -> None:
        """One model has values, another doesn't — only the second is
        pruned; the first still expands normally."""
        m_with = _FakeCalcModel(name="with")
        m_with._key_list_map = {"region": ["US", "EU"]}
        m_without = _FakeCalcModel(name="without")
        m_without._key_list_map = {"region": []}

        result = ModelCombinationGenerator._expand_models_for_field(
            [m_with, m_without], "region", {},
        )
        self.assertEqual(len(result), 2)
        self.assertEqual({m.region for m in result}, {"US", "EU"})

    def test_7_18p_setattr_failure_is_wrapped(self) -> None:
        """If ``setattr`` raises (e.g., ``__slots__`` model with no slot
        for the field), the engine wraps it in ``ModelCombinationError``."""

        class SlottedModel:
            __slots__ = ("region",)

            def __deepcopy__(self, memo):
                new = SlottedModel()
                memo[id(self)] = new
                return new

            def get_selected_key_list(self, key):  # pragma: no cover
                return []

        m = SlottedModel()
        with self.assertRaises(ModelCombinationError) as ctx:
            ModelCombinationGenerator._expand_models_for_field(
                [m], "not_a_slot", {"not_a_slot": ["v"]},
            )
        self.assertIn("not_a_slot", str(ctx.exception))


# --------------------------------------------------------------------
# 7.19 – 7.22 — Cluster manager — hierarchy + flatten
# --------------------------------------------------------------------
class TestCluster07f_ClusterManager(SimpleTestCase):
    """``ModelClusterManager`` hierarchy + flatten."""

    # -- 7.19 ----------------------------------------------------------
    def test_7_19_clusters_group_by_parallelizable_field_values(self) -> None:
        m1 = _FakeCalcModel(region="US", category="A", id=1)
        m2 = _FakeCalcModel(region="US", category="B", id=2)
        m3 = _FakeCalcModel(region="EU", category="A", id=3)

        cluster = ModelClusterManager.create_clusters(
            models=[m1, m2, m3],
            parallelizable_fields=["region", "category"],
        )

        self.assertEqual(set(cluster.keys()), {"US", "EU"})
        self.assertEqual(set(cluster["US"].keys()), {"A", "B"})
        self.assertEqual(cluster["US"]["A"], [m1])
        self.assertEqual(cluster["US"]["B"], [m2])
        self.assertEqual(cluster["EU"]["A"], [m3])

    # -- 7.20 ----------------------------------------------------------
    def test_7_20_empty_parallelizable_fields_returns_single_group(self) -> None:
        models = [_FakeCalcModel(id=i) for i in range(3)]
        cluster = ModelClusterManager.create_clusters(
            models=models, parallelizable_fields=[],
        )
        self.assertEqual(cluster, {None: models})
        groups = ModelClusterManager.flatten_clusters_to_groups(cluster)
        self.assertEqual(groups, [models])

    # -- 7.21 ----------------------------------------------------------
    def test_7_21_missing_parallelizable_field_raises_typed_error(self) -> None:
        m = _FakeCalcModel(region="US")  # no `category`
        with self.assertRaises(ModelClusteringError) as ctx:
            ModelClusterManager.create_clusters(
                models=[m], parallelizable_fields=["region", "category"],
            )
        self.assertIn("category", str(ctx.exception))

    # -- 7.22 ----------------------------------------------------------
    def test_7_22_flatten_preserves_all_models_no_duplicates(self) -> None:
        models = [
            _FakeCalcModel(region="US", category="A", id=i) for i in range(2)
        ] + [
            _FakeCalcModel(region="EU", category="B", id=i) for i in range(2, 5)
        ]
        cluster = ModelClusterManager.create_clusters(
            models=models, parallelizable_fields=["region", "category"],
        )
        groups = ModelClusterManager.flatten_clusters_to_groups(cluster)

        flattened = [m for g in groups for m in g]
        self.assertEqual(
            sorted(m.id for m in flattened),
            sorted(m.id for m in models),
        )

    # -- 7.23 ----------------------------------------------------------
    def test_7_23_create_clusters_empty_models_returns_empty_dict(self) -> None:
        self.assertEqual(
            ModelClusterManager.create_clusters([], ["region"]),
            {},
        )

    # -- 7.24 ----------------------------------------------------------
    def test_7_24_create_clusters_single_field(self) -> None:
        """With a single parallelizable field the leaf level holds lists
        of models keyed by that single value."""
        m1 = _FakeCalcModel(region="US", id=1)
        m2 = _FakeCalcModel(region="US", id=2)
        m3 = _FakeCalcModel(region="EU", id=3)
        cluster = ModelClusterManager.create_clusters(
            [m1, m2, m3], ["region"],
        )
        self.assertEqual(cluster, {"US": [m1, m2], "EU": [m3]})

    # -- 7.24a ---------------------------------------------------------
    def test_7_24a_none_field_value_is_used_as_cluster_key(self) -> None:
        """Models with ``None`` for a parallelizable field cluster under
        the ``None`` key (with a warning log) instead of crashing."""
        m1 = _FakeCalcModel(region=None, category="A", id=1)
        m2 = _FakeCalcModel(region=None, category=None, id=2)
        cluster = ModelClusterManager.create_clusters(
            [m1, m2], ["region", "category"],
        )
        self.assertIn(None, cluster)
        self.assertEqual(cluster[None]["A"], [m1])
        self.assertEqual(cluster[None][None], [m2])

    # -- 7.24b ---------------------------------------------------------
    def test_7_24b_three_level_hierarchy(self) -> None:
        m = _FakeCalcModel(a="1", b="2", c="3", id=1)
        cluster = ModelClusterManager.create_clusters([m], ["a", "b", "c"])
        self.assertEqual(cluster, {"1": {"2": {"3": [m]}}})

        groups = ModelClusterManager.flatten_clusters_to_groups(cluster)
        self.assertEqual(groups, [[m]])

    # -- 7.24c ---------------------------------------------------------
    def test_7_24c_build_cluster_hierarchy_requires_fields(self) -> None:
        """``_build_cluster_hierarchy`` is private but documented to
        require ``parallelizable_fields`` when given non-empty models."""
        with self.assertRaises(ModelClusteringError):
            ModelClusterManager._build_cluster_hierarchy(
                [_FakeCalcModel(id=1)], parallelizable_fields=[],
            )

    # -- 7.24d ---------------------------------------------------------
    def test_7_24d_build_cluster_hierarchy_empty_models(self) -> None:
        self.assertEqual(
            ModelClusterManager._build_cluster_hierarchy([], ["region"]),
            {},
        )

    # -- 7.24e ---------------------------------------------------------
    def test_7_24e_flatten_empty_dict_returns_empty_list(self) -> None:
        self.assertEqual(ModelClusterManager.flatten_clusters_to_groups({}), [])

    # -- 7.24f ---------------------------------------------------------
    def test_7_24f_flatten_non_dict_raises_typed_error(self) -> None:
        with self.assertRaises(ModelClusteringError):
            ModelClusterManager.flatten_clusters_to_groups([1, 2, 3])  # type: ignore[arg-type]

    # -- 7.24g ---------------------------------------------------------
    def test_7_24g_flatten_invalid_leaf_value_raises(self) -> None:
        """Leaves must be dicts (sub-cluster) or lists/tuples (model
        groups). Anything else is a malformed cluster."""
        with self.assertRaises(ModelClusteringError):
            ModelClusterManager.flatten_clusters_to_groups({"k": 42})

    # -- 7.24h ---------------------------------------------------------
    def test_7_24h_flatten_skips_empty_leaf_groups(self) -> None:
        """Empty leaf groups must not surface as empty lists in the
        output (the dispatcher would otherwise schedule a no-op task)."""
        m = _FakeCalcModel(id=1)
        groups = ModelClusterManager.flatten_clusters_to_groups({
            "US": [m],
            "EU": [],          # empty group → skipped
        })
        self.assertEqual(groups, [[m]])

    # -- 7.24i ---------------------------------------------------------
    def test_7_24i_flatten_accepts_tuple_leaves(self) -> None:
        m1, m2 = _FakeCalcModel(id=1), _FakeCalcModel(id=2)
        groups = ModelClusterManager.flatten_clusters_to_groups({
            "US": (m1, m2),
        })
        self.assertEqual(groups, [[m1, m2]])


# --------------------------------------------------------------------
# 7.25 — Defensive / hard-to-reach exception branches
#
# These tests deliberately stub *internal* helpers (or use models with
# attribute-access side effects) to drive exception branches that are
# guarded but unreachable through normal public-API misuse. Each one
# corresponds to a specific ``except``/``raise`` block in
# ``CalculatedModelMixin.py`` we'd otherwise miss in coverage reports.
#
# Note: a small number of branches are *truly* unreachable from
# anywhere outside the closure they live in (the recursive
# ``_add_to_group`` non-dict guard, the post-flatten group-type
# validation, and the outer ``except Exception`` of
# ``flatten_clusters_to_groups``). Those are pure defense-in-depth and
# documented inline in the source — not testable without rewriting
# them.
# --------------------------------------------------------------------
class TestCluster07f_DefensiveBranches(SimpleTestCase):

    # -- 7.25a ---------------------------------------------------------
    def test_7_25a_expand_models_non_list_field_values_raises(self) -> None:
        """Covers the ``if not isinstance(field_values, list): raise``
        guard at ``_expand_models_for_field`` (line ~364). Reachable
        only by monkey-patching ``_get_field_values`` to return a
        non-list (``_normalize_field_values`` always returns a list,
        so the public API can't get here).
        """
        original = ModelCombinationGenerator._get_field_values
        ModelCombinationGenerator._get_field_values = staticmethod(
            lambda model, field_name, overrides: ("US", "EU")  # truthy tuple
        )
        try:
            with self.assertRaises(ModelCombinationError) as ctx:
                ModelCombinationGenerator._expand_models_for_field(
                    [_FakeCalcModel()], "region", {},
                )
            self.assertIn("normalize to a list", str(ctx.exception))
        finally:
            ModelCombinationGenerator._get_field_values = staticmethod(original)

    # -- 7.25b ---------------------------------------------------------
    def test_7_25b_expand_models_outer_per_model_exception_is_wrapped(self) -> None:
        """Covers the per-model outer ``except Exception as model_error``
        at ``_expand_models_for_field`` (line ~406). Triggered by a
        non-``ModelCombinationError`` raised from ``_get_field_values``
        — which the inner ``try`` doesn't catch.

        Note: the engine's normal failure modes already wrap themselves
        in ``ModelCombinationError``; the only way to surface a *bare*
        exception here is to monkey-patch the helper to raise a
        plain Python error.
        """
        original = ModelCombinationGenerator._get_field_values

        def hostile(model, field_name, overrides):
            raise RuntimeError("bare error from helper")

        ModelCombinationGenerator._get_field_values = staticmethod(hostile)
        try:
            with self.assertRaises(ModelCombinationError) as ctx:
                ModelCombinationGenerator._expand_models_for_field(
                    [_FakeCalcModel()], "region", {},
                )
            self.assertIn("region", str(ctx.exception))
            # Original cause preserved via ``from model_error``.
            self.assertIsInstance(ctx.exception.__cause__, RuntimeError)
        finally:
            ModelCombinationGenerator._get_field_values = staticmethod(original)

    # -- 7.25c ---------------------------------------------------------
    def test_7_25c_create_clusters_invalid_hierarchy_result_raises(self) -> None:
        """Covers ``if not isinstance(cluster_result, dict): raise``
        at ``create_clusters`` (line ~578). Only reachable by
        monkey-patching ``_build_cluster_hierarchy`` to return a
        non-dict.
        """
        original = ModelClusterManager._build_cluster_hierarchy
        ModelClusterManager._build_cluster_hierarchy = staticmethod(
            lambda models, parallelizable_fields: ["not", "a", "dict"]
        )
        try:
            with self.assertRaises(ModelClusteringError) as ctx:
                ModelClusterManager.create_clusters(
                    [_FakeCalcModel(region="US")], ["region"],
                )
            self.assertIn("invalid type", str(ctx.exception))
        finally:
            ModelClusterManager._build_cluster_hierarchy = staticmethod(original)

    # -- 7.25d ---------------------------------------------------------
    def test_7_25d_create_clusters_unexpected_error_is_wrapped(self) -> None:
        """Covers the outer ``except Exception as e`` of
        ``create_clusters`` (line ~591). Triggered by a non-typed
        exception raised from ``_build_cluster_hierarchy``.
        """
        original = ModelClusterManager._build_cluster_hierarchy

        def hostile(models, parallelizable_fields):
            raise RuntimeError("bare hierarchy error")

        ModelClusterManager._build_cluster_hierarchy = staticmethod(hostile)
        try:
            with self.assertRaises(ModelClusteringError) as ctx:
                ModelClusterManager.create_clusters(
                    [_FakeCalcModel(region="US")], ["region"],
                )
            self.assertIn("Unexpected error during clustering", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, RuntimeError)
        finally:
            ModelClusterManager._build_cluster_hierarchy = staticmethod(original)

    # -- 7.25e ---------------------------------------------------------
    def test_7_25e_build_hierarchy_intermediate_field_access_raises(self) -> None:
        """Covers the intermediate-field ``except Exception as field_error``
        in ``_build_cluster_hierarchy`` (line ~773). The
        ``hasattr`` validation in ``create_clusters`` lets us through
        because the attribute *exists*; only the access raises (a
        property whose getter blows up).
        """

        class ExplodingProperty:
            category = "A"  # second field is fine

            @property
            def region(self):
                raise RuntimeError("getter exploded")

            def __deepcopy__(self, memo):  # pragma: no cover
                return self

        # Bypass create_clusters' hasattr validation by calling
        # _build_cluster_hierarchy directly — that's also where the
        # branch lives.
        with self.assertRaises(ModelClusteringError) as ctx:
            ModelClusterManager._build_cluster_hierarchy(
                [ExplodingProperty()], ["region", "category"],
            )
        self.assertIn("region", str(ctx.exception))
        # The raise chain should preserve the RuntimeError cause.
        self.assertIsNotNone(ctx.exception.__cause__)

    # -- 7.25f ---------------------------------------------------------
    def test_7_25f_build_hierarchy_last_field_access_raises(self) -> None:
        """Covers the last-field ``except Exception as last_field_error``
        in ``_build_cluster_hierarchy`` (line ~807). Same pattern as
        7.25e but the exploding attribute is the *final* field in
        the parallelizable list (separate code path: the last field
        builds a model list, not a sub-dict)."""

        class ExplodingLast:
            region = "US"

            @property
            def category(self):
                raise RuntimeError("last-field getter exploded")

            def __deepcopy__(self, memo):  # pragma: no cover
                return self

        with self.assertRaises(ModelClusteringError) as ctx:
            ModelClusterManager._build_cluster_hierarchy(
                [ExplodingLast()], ["region", "category"],
            )
        self.assertIn("category", str(ctx.exception))

    # -- 7.25g ---------------------------------------------------------
    def test_7_25g_build_hierarchy_outer_unexpected_error_is_wrapped(self) -> None:
        """Covers the outer ``except Exception as e`` of
        ``_build_cluster_hierarchy`` (line ~835). Reachable by
        passing a model whose iteration over the for-loop raises
        outside the inner per-field try (e.g., ``len()`` of the
        models list raised inside the logger). The cleanest
        deterministic trigger is a list subclass whose ``__iter__``
        raises after the length has been read.
        """

        class HostileList(list):
            def __iter__(self):
                raise RuntimeError("hostile iteration")

        models = HostileList([_FakeCalcModel(region="US")])
        with self.assertRaises(ModelClusteringError) as ctx:
            ModelClusterManager._build_cluster_hierarchy(models, ["region"])
        self.assertIn("Unexpected error", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

