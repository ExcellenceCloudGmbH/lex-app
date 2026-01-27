from unittest.mock import patch
from django.test import TransactionTestCase
from django.db import models, connection
from datetime import timedelta
from lex.core.models.base import LexModel
import datetime
from django.utils import timezone
from lex.core.services.bitemporal import get_queryset_as_of

class AsOfTestModel(LexModel):
    name = models.CharField(max_length=100)
    class Meta:
        app_label = 'lex_app'

class BitemporalAsOfTest(TransactionTestCase):
    
    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration
        mr = ModelRegistration()
        try: mr._register_standard_model(AsOfTestModel, [])
        except Exception: pass
        self.HistoryModel = AsOfTestModel.history.model
        
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(AsOfTestModel)
            schema_editor.create_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel.meta_history.model)

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
             try: schema_editor.delete_model(self.HistoryModel.meta_history.model)
             except: pass
             try: schema_editor.delete_model(self.HistoryModel)
             except: pass
             try: schema_editor.delete_model(AsOfTestModel)
             except: pass

    def test_as_of_validity(self):
        """
        Verify as_of query filters based on Valid Time.
        """
        T0 = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        T_Future = T0 + timedelta(hours=1) # 13:00
        
        # 1. Insert Future Record (Valid from 13:00)
        with patch('django.utils.timezone.now', return_value=T0):
            obj = AsOfTestModel(name="FutureVal")
            obj._history_date = T_Future
            obj.save()
            
        with patch('django.utils.timezone.now', return_value=T0):
            # 2. Main Table Check (Should be Empty)
            self.assertEqual(AsOfTestModel.objects.count(), 0)
            
            # 3. As-Of Query Check (Should match Main Table)
            qs_now = get_queryset_as_of(AsOfTestModel, T0)
            print(f"As-Of T0 Count: {qs_now.count()}")
            self.assertEqual(qs_now.count(), 0, "as_of=Now should exclude Future records")
            
            # 4. As-Of Future Query
            qs_future = get_queryset_as_of(AsOfTestModel, T_Future)
            print(f"As-Of Future Count: {qs_future.count()}")
            self.assertEqual(qs_future.count(), 1, "as_of=Future should show the record")
            self.assertEqual(qs_future.first().name, "FutureVal")

    def test_as_of_historical(self):
        """
        Verify as_of retrieves past valid records.
        """
        T0 = datetime.datetime(2025, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        T1 = T0 + timedelta(hours=1)
        T2 = T0 + timedelta(hours=2)
        
        with patch('django.utils.timezone.now', return_value=T0):
             obj = AsOfTestModel(name="OldVal")
             obj._history_date = T0
             obj.save()
             
        # Update at T1
        with patch('django.utils.timezone.now', return_value=T1):
             obj.name = "NewVal"
             obj._history_date = T1
             obj.save()
             
        # At T2, check history
        with patch('django.utils.timezone.now', return_value=T2):
             qs_past = get_queryset_as_of(AsOfTestModel, T0 + timedelta(minutes=30))
             print(f"As-Of Past (10:30) Name: {qs_past.first().name}")
             self.assertEqual(qs_past.first().name, "OldVal")
             
             qs_current = get_queryset_as_of(AsOfTestModel, T1 + timedelta(minutes=30))
             print(f"As-Of Later (11:30) Name: {qs_current.first().name}")
             self.assertEqual(qs_current.first().name, "NewVal")

    def test_as_of_with_history_model_class(self):
        """
        Verify that passing the Historical Model class uses System Time (Meta-History).
        """
        # T0: Create Record
        T0 = datetime.datetime(2025, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        
        with patch('django.utils.timezone.now', return_value=T0):
             obj = AsOfTestModel.objects.create(name="Original")
             
        # T1: Update Record (Correction) - This creates a new Meta Version for the SAME History ID?
        # Actually Strict Chaining updates the record in place.
        # Let's simple update:
        T1 = T0 + timedelta(hours=1)
        with patch('django.utils.timezone.now', return_value=T1):
             obj.name = "Changed"
             obj.save()
             
        # Now querying History Model 'as_of' T0 should return "Original"
        # Querying 'as_of' T1 should return "Changed" (if we track system time updates correctly)
        
        # Test Main Model As-Of (Valid Time)
        qs_valid = get_queryset_as_of(AsOfTestModel, T1)
        self.assertEqual(qs_valid.first().name, "Changed")
        
        # Test History Model As-Of (System Time) for the FIRST history record?
        # The first history record (Original) exists from T0.
        # Wait, if we updated at T1, we created a NEW history record for "Changed" (Valid T1->inf).
        # "Original" is now Valid T0->T1.
        
        # Let's use get_queryset_as_of(HistoryModel, T0).
        # At T0, we knew about "Original".
        qs_sys_t0 = get_queryset_as_of(self.HistoryModel, T0)
        self.assertEqual(qs_sys_t0.count(), 1)
        # It returns a META record.
        self.assertTrue(hasattr(qs_sys_t0.first(), 'meta_history_id'), "Should return MetaHistory")
        self.assertEqual(qs_sys_t0.first().name, "Original")
        
        # At T0.5 (Before change), we still know "Original".
        
        # At T1.5 (After change). We know "Original" AND "Changed".
        qs_sys_t1 = get_queryset_as_of(self.HistoryModel, T1 + timedelta(minutes=1))
        # We should see BOTH history records as known to the system?
        # Yes, History Table contains both rows. Both are known.
        self.assertEqual(qs_sys_t1.count(), 2)
