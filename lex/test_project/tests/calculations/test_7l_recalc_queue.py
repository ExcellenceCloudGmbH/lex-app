import pytest

pytestmark = pytest.mark.calculations

# """
# Cluster 7l: ``ObjectsToRecalculateStore`` + ``CalculatedModelUpdateHandler``.
#
# Intent
# ------
#
# Two singleton stores at the heart of the framework's recalculation
# plumbing — both ~50 lines, both completely dark before this batch.
#
# * ``lex/core/calculated_updates/ObjectsToRecalculateStore.py`` — the
#   in-memory queue every signal handler appends to when an object
#   needs recalculation. Keyed by ``(model_name, defining_fields)`` so
#   multiple touches of the same row collapse to one recalc — losing
#   this dedupe would silently turn a bulk update into N recalcs of
#   the same rows.
#
# * ``lex/core/calculated_updates/update_handler.py`` — the
#   customer-facing post-save dispatcher. ``register_save(entry)``
#   walks ``entry.get_dependent_entries()`` and dispatches each
#   ``CalculatedModelMixin`` dependent through ``post_save_behaviour``
#   (default: ``calc_and_save`` — calls ``.calculate()`` + ``.save()``
#   inline). The ``set_post_save_behaviour`` / ``reset_…`` pair is the
#   documented hook for tests + customer overrides to defer/batch
#   the dispatch.
#
# All scenarios are pure-Python `SimpleTestCase` — the singletons
# manage their own state via class-level ``instance`` attributes, so
# no DB. Scenario range picks up at **7.143** (7k ended at 7.142).
#
# The bare singleton state is global — every test sets up a fresh
# instance in ``setUp`` and resets it in ``tearDown`` so cross-test
# contamination cannot happen.
# """
#
# from __future__ import annotations
#
# import unittest
# from types import SimpleNamespace
# from unittest import TestCase
#
# from lex.core.calculated_updates.ObjectsToRecalculateStore import (
#     ObjectsToRecalculateStore,
# )
# from lex.core.calculated_updates.update_handler import (
#     CalculatedModelUpdateHandler,
#     calc_and_save,
# )
#
#
# # ---------------------------------------------------------------------
# # Helpers — fake objects that look enough like a CalculatedModelMixin
# # instance for the store/handler code to introspect.
# # ---------------------------------------------------------------------
# def _make_calc_obj(model_name, defining_field_values, **field_overrides):
#     """Build an object that looks like a calculated row.
#
#     ``defining_field_values`` is a dict of ``{field_name: value}``.
#     ``defining_fields`` then becomes a list of ``Field``-like
#     namespaces with a ``.name`` attribute (the only attribute the
#     store actually reads).
#     """
#     obj = SimpleNamespace()
#     obj._meta = SimpleNamespace(model_name=model_name)
#     obj.defining_fields = [
#         SimpleNamespace(name=field_name)
#         for field_name in defining_field_values
#     ]
#     for field_name, value in defining_field_values.items():
#         setattr(obj, field_name, value)
#     for k, v in field_overrides.items():
#         setattr(obj, k, v)
#     obj._calc_count = 0
#     obj._save_count = 0
#
#     def _calculate():
#         obj._calc_count += 1
#
#     def _save():
#         obj._save_count += 1
#
#     obj.calculate = _calculate
#     obj.save = _save
#     return obj
#
#
# # ---------------------------------------------------------------------
# # 7.143–7.147 — ObjectsToRecalculateStore
# # ---------------------------------------------------------------------
# class TestCluster07l_ObjectsToRecalculateStore(TestCase):
#     """Singleton in-memory dedupe queue for objects awaiting
#     recalculation.
#
#     The (model_name, defining_fields) key is what makes a bulk
#     update O(N) rows instead of O(N²) recalcs.
#     """
#
#     def setUp(self):
#         # Fresh singleton per test — the constructor wires
#         # ``ObjectsToRecalculateStore.instance = self`` so simply
#         # constructing one is enough.
#         self._previous_instance = ObjectsToRecalculateStore.instance
#         ObjectsToRecalculateStore()
#         self.addCleanup(self._restore_instance)
#
#     def _restore_instance(self):
#         ObjectsToRecalculateStore.instance = self._previous_instance
#
#     def test_7_143_insert_creates_first_entry_for_a_model(self):
#         """7.143: ``insert(obj)`` on an empty store creates the
#         per-model bucket and registers the object under its
#         ``defining_fields`` tuple.
#
#         Caller (signal handler) appends to the store directly via
#         the static ``insert`` — no per-call construction.
#         """
#         obj = _make_calc_obj("invoice", {"counterparty": "X", "year": 2025})
#
#         ObjectsToRecalculateStore.insert(obj)
#
#         store = ObjectsToRecalculateStore.instance.objects_to_recalculate
#         self.assertIn("invoice", store, "model bucket must exist")
#         self.assertIn(
#             ("X", 2025), store["invoice"],
#             "row must be keyed by the (counterparty, year) tuple",
#         )
#         self.assertIs(store["invoice"][("X", 2025)], obj)
#
#     def test_7_144_insert_dedupes_by_defining_fields_tuple(self):
#         """7.144: a second ``insert`` with the same defining-field
#         tuple is a no-op — the first object stays registered.
#
#         This is the dedupe contract that keeps a bulk update from
#         spawning N recalcs of the same row. A regression here would
#         re-run ``.calculate()`` once per signal, which on a 20k-row
#         table is the difference between 1 minute and 7 hours.
#         """
#         first = _make_calc_obj("invoice", {"counterparty": "X", "year": 2025})
#         second = _make_calc_obj("invoice", {"counterparty": "X", "year": 2025})
#
#         ObjectsToRecalculateStore.insert(first)
#         ObjectsToRecalculateStore.insert(second)
#
#         store = ObjectsToRecalculateStore.instance.objects_to_recalculate
#         self.assertEqual(
#             len(store["invoice"]), 1,
#             "duplicate insert must not create a second entry",
#         )
#         self.assertIs(
#             store["invoice"][("X", 2025)], first,
#             "first-write-wins — second insert is a no-op",
#         )
#
#     def test_7_145_insert_keeps_distinct_rows_separate(self):
#         """7.145: rows differing in any defining field stay distinct.
#
#         Pin: the tuple comparison must consider every field — a
#         regression that compared only the first field would silently
#         overwrite distinct rows with the same counterparty but
#         different years.
#         """
#         a = _make_calc_obj("invoice", {"counterparty": "X", "year": 2024})
#         b = _make_calc_obj("invoice", {"counterparty": "X", "year": 2025})
#
#         ObjectsToRecalculateStore.insert(a)
#         ObjectsToRecalculateStore.insert(b)
#
#         store = ObjectsToRecalculateStore.instance.objects_to_recalculate["invoice"]
#         self.assertEqual(len(store), 2)
#         self.assertIs(store[("X", 2024)], a)
#         self.assertIs(store[("X", 2025)], b)
#
#     def test_7_146_insert_isolates_by_model_name(self):
#         """7.146: rows from different models live in separate buckets
#         even when their defining-field tuples collide.
#
#         Without this, an Invoice with `(X, 2025)` and a Counterparty
#         with `(X, 2025)` would clobber each other.
#         """
#         invoice = _make_calc_obj("invoice", {"key": "X"})
#         counterparty = _make_calc_obj("counterparty", {"key": "X"})
#
#         ObjectsToRecalculateStore.insert(invoice)
#         ObjectsToRecalculateStore.insert(counterparty)
#
#         store = ObjectsToRecalculateStore.instance.objects_to_recalculate
#         self.assertIs(store["invoice"][("X",)], invoice)
#         self.assertIs(store["counterparty"][("X",)], counterparty)
#
#     def test_7_147_do_recalculations_runs_calculate_then_save_then_clears(self):
#         """7.147: ``do_recalculations()`` walks the queue, calls
#         ``.calculate()`` then ``.save()`` on each entry, then empties
#         the queue.
#
#         Three contracts pinned in one scenario:
#          (a) ``calculate()`` is called exactly once per row;
#          (b) ``save()`` is called exactly once per row, AFTER
#              calculate (out-of-order would silently persist
#              pre-recalc state);
#          (c) the queue is cleared on exit so a second call is a
#              no-op (no double-recalc on next signal).
#         """
#         a = _make_calc_obj("invoice", {"key": "A"})
#         b = _make_calc_obj("invoice", {"key": "B"})
#         c = _make_calc_obj("counterparty", {"key": "C"})
#         for obj in (a, b, c):
#             ObjectsToRecalculateStore.insert(obj)
#
#         ObjectsToRecalculateStore.do_recalculations()
#
#         for obj in (a, b, c):
#             with self.subTest(obj=obj._meta.model_name):
#                 self.assertEqual(obj._calc_count, 1)
#                 self.assertEqual(obj._save_count, 1)
#
#         # Queue cleared
#         store = ObjectsToRecalculateStore.instance.objects_to_recalculate
#         self.assertEqual(
#             store, {},
#             "queue must be empty after do_recalculations — otherwise "
#             "next signal triggers re-recalc",
#         )
#
#         # Second call is a no-op (no extra calculate/save)
#         ObjectsToRecalculateStore.do_recalculations()
#         for obj in (a, b, c):
#             self.assertEqual(obj._calc_count, 1, "no double-recalc")
#             self.assertEqual(obj._save_count, 1)
#
#
# # ---------------------------------------------------------------------
# # 7.148–7.154 — CalculatedModelUpdateHandler
# # ---------------------------------------------------------------------
# class TestCluster07l_CalculatedModelUpdateHandler(TestCase):
#     """Singleton dispatcher that fans out a save into recalcs of
#     every dependent CalculatedModelMixin entry.
#
#     The default ``post_save_behaviour`` is ``calc_and_save`` (inline
#     `.calculate() + .save()`). Customers / tests override via
#     ``set_post_save_behaviour`` to batch, defer, or instrument.
#     """
#
#     def setUp(self):
#         self._previous_instance = CalculatedModelUpdateHandler.instance
#         CalculatedModelUpdateHandler()
#         self.addCleanup(self._restore_instance)
#
#     def _restore_instance(self):
#         CalculatedModelUpdateHandler.instance = self._previous_instance
#
#     def _make_calculated_dependent(self, label="dep"):
#         """Build a fake dependent that DOES inherit from
#         ``CalculatedModelMixin`` (the ``register_save`` filter
#         requires it).
#         """
#         from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin
#
#         class _FakeCalcDep(CalculatedModelMixin):
#             class Meta:
#                 abstract = True
#                 app_label = "test_project"
#
#             def __init__(self):
#                 self.label = label
#                 self.calc_count = 0
#                 self.save_count = 0
#
#             def calculate(self):
#                 self.calc_count += 1
#
#             def save(self, *args, **kwargs):  # noqa: D401
#                 self.save_count += 1
#
#         return _FakeCalcDep()
#
#     def _make_plain_dependent(self, label="plain"):
#         """Build a dependent that does NOT inherit from
#         ``CalculatedModelMixin`` — must be filtered out.
#         """
#         obj = SimpleNamespace(label=label, calc_count=0, save_count=0)
#
#         def _calc():
#             obj.calc_count += 1
#
#         def _save(*a, **kw):
#             obj.save_count += 1
#
#         obj.calculate = _calc
#         obj.save = _save
#         return obj
#
#     def test_7_148_default_post_save_behaviour_is_calc_and_save(self):
#         """7.148: a freshly-constructed handler exposes the documented
#         ``calc_and_save`` default.
#
#         Pin: the function identity, not just a name match — a future
#         replacement that kept the name but changed semantics would
#         slip past a name-only check.
#         """
#         self.assertIs(
#             CalculatedModelUpdateHandler.instance.post_save_behaviour,
#             calc_and_save,
#         )
#
#     def test_7_149_calc_and_save_runs_calculate_then_save_on_entry(self):
#         """7.149: ``calc_and_save(entry)`` calls ``.calculate()``
#         then ``.save()``.
#
#         Order matters — save-before-calculate would persist
#         pre-recalc state and silently produce stale rows.
#         """
#         order: list = []
#
#         class _Probe(SimpleNamespace):
#             def calculate(self):
#                 order.append("calc")
#
#             def save(self):
#                 order.append("save")
#
#         calc_and_save(_Probe())
#         self.assertEqual(order, ["calc", "save"])
#
#     def test_7_150_set_post_save_behaviour_overrides_dispatcher(self):
#         """7.150: ``set_post_save_behaviour(func)`` replaces the
#         dispatch function for every subsequent ``register_save``.
#
#         Documented hook for tests / customer-side batching.
#         """
#         recorded: list = []
#
#         def _record(entry):
#             recorded.append(entry.label)
#
#         CalculatedModelUpdateHandler.set_post_save_behaviour(_record)
#         self.assertIs(
#             CalculatedModelUpdateHandler.instance.post_save_behaviour,
#             _record,
#         )
#
#     def test_7_151_reset_post_save_behaviour_restores_default(self):
#         """7.151: ``reset_post_save_behaviour()`` puts ``calc_and_save``
#         back on the singleton — the cleanup hook every test relies on
#         to avoid leaking overrides across runs.
#         """
#         CalculatedModelUpdateHandler.set_post_save_behaviour(lambda x: None)
#         self.assertIsNot(
#             CalculatedModelUpdateHandler.instance.post_save_behaviour,
#             calc_and_save,
#         )
#
#         CalculatedModelUpdateHandler.reset_post_save_behaviour()
#         self.assertIs(
#             CalculatedModelUpdateHandler.instance.post_save_behaviour,
#             calc_and_save,
#         )
#
#     def test_7_152_register_save_skips_entries_without_get_dependent_entries(self):
#         """7.152: an updated entry that does NOT expose
#         ``get_dependent_entries`` is silently skipped — no error.
#
#         Pin: defensive guard at the top of ``register_save``. A
#         regression that called the missing method would crash every
#         signal handler that touches a non-dependency-tracked model.
#         """
#         bare = SimpleNamespace()  # no get_dependent_entries
#
#         # Replace the dispatch so we can detect any unintended call
#         called: list = []
#         CalculatedModelUpdateHandler.set_post_save_behaviour(
#             lambda x: called.append(x)
#         )
#
#         # Must not raise, must not call dispatcher
#         CalculatedModelUpdateHandler.register_save(bare)
#         self.assertEqual(called, [])
#
#     def test_7_153_register_save_filters_dependents_by_calculated_mixin(self):
#         """7.153: only dependents that inherit from
#         ``CalculatedModelMixin`` get dispatched. Plain Django rows
#         are returned by ``get_dependent_entries`` but pass through
#         the filter unchanged.
#
#         Without this filter, every signal would re-trigger every
#         listening Django model — including the ones that do not
#         actually need a recalc.
#         """
#         calc_dep = self._make_calculated_dependent(label="calc")
#         plain_dep = self._make_plain_dependent(label="plain")
#
#         # The framework reads `.keys()` on the returned dict, so the
#         # values are arbitrary.
#         updated = SimpleNamespace(
#             get_dependent_entries=lambda: {calc_dep: 1, plain_dep: 1}
#         )
#
#         dispatched: list = []
#         CalculatedModelUpdateHandler.set_post_save_behaviour(
#             lambda entry: dispatched.append(entry)
#         )
#
#         CalculatedModelUpdateHandler.register_save(updated)
#
#         self.assertIn(calc_dep, dispatched, "CalculatedModelMixin dep dispatched")
#         self.assertNotIn(plain_dep, dispatched, "plain dep filtered out")
#         self.assertEqual(len(dispatched), 1)
#
#     def test_7_154_register_save_invokes_dispatcher_per_calc_dependent(self):
#         """7.154: each calculated dependent receives its OWN dispatch
#         call — no batching at this layer.
#
#         End-to-end with the default ``calc_and_save``: every
#         calculated dependent gets ``.calculate()`` + ``.save()``
#         once. A regression here would drop or re-run dispatches
#         and silently desync derived rows.
#         """
#         a = self._make_calculated_dependent(label="A")
#         b = self._make_calculated_dependent(label="B")
#
#         updated = SimpleNamespace(
#             get_dependent_entries=lambda: {a: 1, b: 1}
#         )
#
#         CalculatedModelUpdateHandler.register_save(updated)
#
#         for dep in (a, b):
#             with self.subTest(label=dep.label):
#                 self.assertEqual(dep.calc_count, 1)
#                 self.assertEqual(dep.save_count, 1)
#
#
# if __name__ == "__main__":  # pragma: no cover
#     unittest.main()
#
