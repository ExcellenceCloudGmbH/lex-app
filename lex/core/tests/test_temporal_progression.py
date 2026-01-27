from unittest.mock import patch
from django.test import TransactionTestCase
from django.db import models, connection
from datetime import timedelta
from lex.core.models.base import LexModel
import datetime
from django.utils import timezone

class TemporalTestModel(LexModel):
    name = models.CharField(max_length=100)
    class Meta:
        app_label = 'lex_app'

class BitemporalProgressionTest(TransactionTestCase):
    
    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration
        mr = ModelRegistration()
        try: mr._register_standard_model(TemporalTestModel, [])
        except Exception: pass
        self.HistoryModel = TemporalTestModel.history.model
        
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(TemporalTestModel)
            schema_editor.create_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel.meta_history.model)

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
             try: schema_editor.delete_model(self.HistoryModel.meta_history.model)
             except: pass
             try: schema_editor.delete_model(self.HistoryModel)
             except: pass
             try: schema_editor.delete_model(TemporalTestModel)
             except: pass

    def test_passage_of_time(self):
        """
        Verify what happens when time passes into a Future Record's validity period.
        """
        T0 = datetime.datetime(2025, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        T_Future = T0 + timedelta(hours=1) # 11:00
        
        print("\n--- Test Passage of Time ---")
        
        # 1. At T0, insert a record valid from T_Future (11:00)
        with patch('django.utils.timezone.now', return_value=T0):
            obj = TemporalTestModel(name="FutureVal")
            obj._history_date = T_Future
            obj.save()
            
        # At T0, Main Table should be empty (Verified by previous robust tests)
        self.assertEqual(TemporalTestModel.objects.count(), 0, "Should be empty at T0")
        
        # 2. Advance Time to T_Future + 1 min (11:01)
        T_Active = T_Future + timedelta(minutes=1)
        print(f"Advancing time to {T_Active}")
        
        with patch('django.utils.timezone.now', return_value=T_Active):
             # Run Reconciliation Logic
             from lex.process_admin.utils.temporal_reconciler import TemporalReconciler
             
             # Reconcile window: From T0 to T_Active
             # Ideally we run this periodically.
             # We look for records that became valid between T0 and T_Active.
             count_reconciled = TemporalReconciler.reconcile_changes_since(T0, T_Active)
             print(f"Reconciled {count_reconciled} records.")
             
             qs = TemporalTestModel.objects.all()
             count = qs.count()
             
             print(f"Main Table Count at T_Active: {count}")
             
             # EXPECTATION: 
             # Now that we ran reconciliation, the Main Table should be updated!
             if count == 1:
                 print("Result: Main Table Updated Successfully via Reconciliation!")
             else:
                 print("Result: Main Table still STALE.")
                 
             self.assertEqual(count, 1, "Main Table should reflect the active record after reconciliation")
