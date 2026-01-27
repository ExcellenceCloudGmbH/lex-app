from unittest.mock import patch
from django.test import TransactionTestCase
from django.db import models, connection
from datetime import timedelta
import datetime
from django.utils import timezone
from lex.core.models.base import LexModel

class HistoryDeleteTestModel(LexModel):
    name = models.CharField(max_length=100)
    class Meta:
        app_label = 'lex_app'

class BitemporalHistoryDeletionTest(TransactionTestCase):
    
    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration
        mr = ModelRegistration()
        try: mr._register_standard_model(HistoryDeleteTestModel, [])
        except Exception: pass
        self.HistoryModel = HistoryDeleteTestModel.history.model
        
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(HistoryDeleteTestModel)
            schema_editor.create_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel.meta_history.model)

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
             try: schema_editor.delete_model(self.HistoryModel.meta_history.model)
             except: pass
             try: schema_editor.delete_model(self.HistoryModel)
             except: pass
             try: schema_editor.delete_model(HistoryDeleteTestModel)
             except: pass

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
        T0 = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
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
        
        print(f"Initial State Verified: A({h_a.valid_to}), B({h_b.valid_to}), C({h_c.valid_to})")
        
        # 2. Delete B
        # Trigger post_delete signal on history model
        h_b.delete()
        
        # 3. Verify A is extended
        h_a.refresh_from_db()
        print(f"Post-Delete State: A valid_to = {h_a.valid_to}")
        
        self.assertEqual(h_a.valid_to, T2, "Record A should be extended to cover the gap left by B")
        
        # 4. Verify C is untouched
        h_c.refresh_from_db()
        self.assertEqual(h_c.valid_to, None)
