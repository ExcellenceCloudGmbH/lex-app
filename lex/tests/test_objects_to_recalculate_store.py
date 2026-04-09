"""
Unit tests for ``lex.core.calculated_updates.ObjectsToRecalculateStore``.

**What this tests (customer-visible behaviour)**

``ObjectsToRecalculateStore`` accumulates model instances that need
recalculation after a dependent field changes, then batch-processes
them in ``do_recalculations``.

**Why it matters**

If ``insert`` doesn't de-duplicate by defining fields, the same
object gets recalculated multiple times — wasting computation.
If ``do_recalculations`` doesn't clear the store, stale objects are
re-processed on the next trigger.

**Methodology**

Mock model objects — no DB.

Run::

    python manage.py test lex.tests.test_objects_to_recalculate_store
"""

from unittest.mock import MagicMock, call

from django.test import SimpleTestCase

from lex.core.calculated_updates.ObjectsToRecalculateStore import (
    ObjectsToRecalculateStore,
)


class TestObjectsToRecalculateStore(SimpleTestCase):
    """Prove ``ObjectsToRecalculateStore`` insert / recalculate lifecycle."""

    def setUp(self):
        self.store = ObjectsToRecalculateStore()

    def _make_obj(self, model_name, defining_values):
        obj = MagicMock()
        obj._meta.model_name = model_name
        # defining_fields is a list of field objects with .name
        fields = []
        for name, val in defining_values.items():
            field = MagicMock()
            field.name = name
            fields.append(field)
            setattr(obj, name, val)
        obj.defining_fields = fields
        return obj

    def test_insert_adds_object(self):
        obj = self._make_obj("invoice", {"period": "Q1", "fund": "A"})
        ObjectsToRecalculateStore.insert(obj)
        self.assertIn("invoice", self.store.objects_to_recalculate)

    def test_insert_deduplicates(self):
        obj1 = self._make_obj("invoice", {"period": "Q1"})
        obj2 = self._make_obj("invoice", {"period": "Q1"})
        ObjectsToRecalculateStore.insert(obj1)
        ObjectsToRecalculateStore.insert(obj2)
        # Only one entry for the same defining fields
        self.assertEqual(len(self.store.objects_to_recalculate["invoice"]), 1)

    def test_insert_different_defining_fields(self):
        obj1 = self._make_obj("invoice", {"period": "Q1"})
        obj2 = self._make_obj("invoice", {"period": "Q2"})
        ObjectsToRecalculateStore.insert(obj1)
        ObjectsToRecalculateStore.insert(obj2)
        self.assertEqual(len(self.store.objects_to_recalculate["invoice"]), 2)

    def test_do_recalculations_calls_calculate_and_save(self):
        obj = self._make_obj("invoice", {"period": "Q1"})
        ObjectsToRecalculateStore.insert(obj)
        ObjectsToRecalculateStore.do_recalculations()
        obj.calculate.assert_called_once()
        obj.save.assert_called_once()

    def test_do_recalculations_clears_store(self):
        obj = self._make_obj("invoice", {"period": "Q1"})
        ObjectsToRecalculateStore.insert(obj)
        ObjectsToRecalculateStore.do_recalculations()
        self.assertEqual(self.store.objects_to_recalculate, {})

    def test_empty_store_recalculations_is_noop(self):
        ObjectsToRecalculateStore.do_recalculations()
        self.assertEqual(self.store.objects_to_recalculate, {})

    def test_multiple_models(self):
        inv = self._make_obj("invoice", {"period": "Q1"})
        cf = self._make_obj("cashflow", {"date": "2024-01"})
        ObjectsToRecalculateStore.insert(inv)
        ObjectsToRecalculateStore.insert(cf)
        self.assertIn("invoice", self.store.objects_to_recalculate)
        self.assertIn("cashflow", self.store.objects_to_recalculate)
        ObjectsToRecalculateStore.do_recalculations()
        inv.calculate.assert_called_once()
        cf.calculate.assert_called_once()
