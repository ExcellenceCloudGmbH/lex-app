"""
Tests for bitemporal history record deletion.

Verifies:
    • Deleting a mid-chain history row extends the predecessor’s
      ``valid_to`` to cover the gap
    • History descriptor reuses its resolved manager class
    • Deleting the main record creates a ``history_type = '-'`` row
      and a corresponding meta-history entry
"""

import datetime
from datetime import timedelta
from unittest.mock import patch

from django.db import models, connection
from django.test import TransactionTestCase
from lex.core.models.LexModel import LexModel


class HistoryDeleteTestModel(LexModel):
    name = models.CharField(max_length=100)
    class Meta:
        app_label = 'lex_app'

class BitemporalHistoryDeletionTest(TransactionTestCase):
    """Prove history deletion gap-closing and delete-marker creation."""
    
    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration
        mr = ModelRegistration()
        try:
            mr._register_standard_model(HistoryDeleteTestModel, [])
        except Exception:
            pass  # already registered
        self.HistoryModel = HistoryDeleteTestModel.history.model
        
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(HistoryDeleteTestModel)
            schema_editor.create_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel.meta_history.model)

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
            try:
                schema_editor.delete_model(self.HistoryModel.meta_history.model)
            except Exception:
                pass
            try:
                schema_editor.delete_model(self.HistoryModel)
            except Exception:
                pass
            try:
                schema_editor.delete_model(HistoryDeleteTestModel)
            except Exception:
                pass

    def test_deletion_extends_previous_record(self):
        """
        Verify that deleting a history record extends the valid_to of the previous record.
        Scenario:
        1. A: 12:00 -> 12:05
        2. B: 12:05 -> 13:00
        3. C: 13:00 -> inf
        
        Delete B.
        Expectation:
        1. A: 12:00 -> 13:00 (Extended)
        3. C: 13:00 -> inf (Unchanged)
        """
        T0 = datetime.datetime(2025, 1, 1, 12, 0, 0)
        T1 = T0 + timedelta(minutes=5)   # 12:05
        T2 = T0 + timedelta(hours=1)     # 13:00
        
        # 1. Create Chain
        with patch('django.utils.timezone.now', return_value=T0):
            obj = HistoryDeleteTestModel.objects.create(name="A")
            
        with patch('django.utils.timezone.now', return_value=T1):
            obj.name = "B"
            obj.save()
            
        with patch('django.utils.timezone.now', return_value=T2):
            obj.name = "C"
            obj.save()
            
        # Verify Initial State
        h_a = self.HistoryModel.objects.get(name="A")
        h_b = self.HistoryModel.objects.get(name="B")
        h_c = self.HistoryModel.objects.get(name="C")
        
        self.assertEqual(h_a.valid_to, T1)
        self.assertEqual(h_b.valid_to, T2)
        self.assertEqual(h_c.valid_to, None)
        
        # 2. Delete B
        h_b.delete()
        
        # 3. Verify A is extended
        h_a.refresh_from_db()
        
        self.assertEqual(h_a.valid_to, T2, "Record A should be extended to cover the gap left by B")
        
        # 4. Verify C is untouched
        h_c.refresh_from_db()
        self.assertEqual(h_c.valid_to, None)

    def test_history_descriptor_reuses_manager_class(self):
        t0 = datetime.datetime(2025, 1, 1, 12, 0, 0)

        with patch('django.utils.timezone.now', return_value=t0):
            obj = HistoryDeleteTestModel.objects.create(name="A")

        manager1 = obj.history
        manager2 = obj.history

        self.assertIs(
            manager1.__class__,
            manager2.__class__,
            "History descriptor should reuse the resolved manager class.",
        )

    def test_delete_creates_delete_history_and_meta_history(self):
        t0 = datetime.datetime(2025, 1, 1, 12, 0, 0)

        with patch('django.utils.timezone.now', return_value=t0):
            obj = HistoryDeleteTestModel.objects.create(name="A")

        history_count_before = self.HistoryModel.objects.count()
        meta_count_before = self.HistoryModel.meta_history.model.objects.count()

        obj.delete()

        self.assertEqual(self.HistoryModel.objects.count(), history_count_before + 1)
        self.assertGreaterEqual(
            self.HistoryModel.meta_history.model.objects.count(),
            meta_count_before + 1,
        )
        self.assertEqual(
            self.HistoryModel.objects.order_by("-valid_from", "-history_id").first().history_type,
            "-",
        )
