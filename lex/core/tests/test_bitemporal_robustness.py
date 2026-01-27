from unittest.mock import patch
from django.test import TransactionTestCase
from django.db import models, connection
from datetime import timedelta
from lex.core.models.base import LexModel
import datetime

class RobustnessTestModel(LexModel):
    name = models.CharField(max_length=100)
    class Meta:
        app_label = 'lex_app'

class BitemporalRobustnessTest(TransactionTestCase):
    
    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration
        mr = ModelRegistration()
        try: mr._register_standard_model(RobustnessTestModel, [])
        except Exception: pass
            
        self.HistoryModel = RobustnessTestModel.history.model
        
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(RobustnessTestModel)
            schema_editor.create_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel.meta_history.model)

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
            try: schema_editor.delete_model(self.HistoryModel.meta_history.model)
            except: pass
            try: schema_editor.delete_model(self.HistoryModel)
            except: pass
            try: schema_editor.delete_model(RobustnessTestModel)
            except: pass

    def test_future_insert_sync(self):
        """
        Test extracting a record that starts in the FUTURE.
        Main Table should NOT show it at T0.
        """
        T0 = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        T_Future = T0 + timedelta(hours=1)
        
        print("\n--- Test Future Insert ---")
        with patch('django.utils.timezone.now', return_value=T0):
            # Create object but force valid_from to Future
            # Note: Standard .create() sets valid_from=now(). 
            # We need to trick it or update it immediately.
            obj = RobustnessTestModel(name="future_obj")
            obj._history_date = T_Future
            obj.save()
            
            # At T0, this object is NOT valid. 
            # So Main Table should be empty?
            # Standard Django .save() WRITES to Main Table immediately.
            # Our synchronization logic triggers on POST_SAVE of HISTORY.
            # History is saved. Synchronization runs.
            # Sync should see "No valid record at T0".
            # Should it DELETE the Main Table row?
            
        # Verify Main Table at T0
        # If strict bitemporal: It should be gone.
        qs = RobustnessTestModel.objects.all()
        count = qs.count()
        print(f"Main Table Count at T0: {count}")
        if count > 0:
            print(f"Row: {qs.first().name}")
            
        self.assertEqual(count, 0, "Main Table should be empty if record is only valid in future")
        
    def test_gap_in_validity(self):
        """
        Test having a gap between two records.
        Main Table should disappear during gap.
        """
        T0 = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        T1 = T0 + timedelta(minutes=10) # Gap Start
        T2 = T0 + timedelta(minutes=20) # Gap End / Next Record Start
        
        print("\n--- Test Gap Validity ---")
        
        # 1. Create Record A (Valid T0 -> inf)
        with patch('django.utils.timezone.now', return_value=T0):
            obj = RobustnessTestModel.objects.create(name="MsgA")
            
        # 2. Delete Record A at T1. 
        original_id = obj.id
        
        with patch('django.utils.timezone.now', return_value=T1):
             obj.delete()
             
        # 3. Create Record B at T2.
        # Strict chaining will link A- (T1) -> B (T2).
        
        with patch('django.utils.timezone.now', return_value=T0): 
            # Resurrection with new name, reusing SAME ID
            obj = RobustnessTestModel(pk=original_id, name="MsgB")
            obj._history_date = T2
            obj.save()
            
        # Verify History Structure
        # hA: T0 -> T1
        # hB: T2 -> inf
        
        # Check at T_Gap (T0 + 15m)
        T_Gap = T0 + timedelta(minutes=15)
        
        # In a real app, 'time passes'. Main Table doesn't auto-update unless triggered.
        # BUT if we perform an ACTION at T_Gap (e.g. some update), it triggers sync.
        # Here we just want to verify state AFTER the operations above.
        # The last op was saving MsgB. Sync ran at T0 (simulated).
        # Wait, if we run sync at T0, what does it see?
        # At T0, MsgA is valid. So Main Table = MsgA.
        
        # But if we advance clock to T_Gap and trigger sync?
        # This simulates "Lazy Sync" or "Read Time View"?
        # No, Main Table is "Current Cache".
        # If time passes, the cache becomes stale regarding 'valid_to'.
        # This is a fundamental limitation of storing "Current State" in SQL Table.
        # It doesn't auto-expire at 'valid_to'.
        
        # HOWEVER, the User's Scenario was about "Correction".
        # Correction happens at T_System. 
        # The Sync logic runs at T_System.
        # It checks validity AT T_System.
        
        # So:
        # If I am at T0. I insert "Future Record valid at T2".
        # Sync runs at T0. logic: "Is there a valid record at T0?" -> No.
        # Main Table should be empty.
        
        # If I am at T3. I insert "Past Record valid at T2".
        # Sync runs at T3. logic: "Is there a valid record at T3?" -> Yes (MsgB).
        # Main Table = MsgB.
        
        # So "Test Gap" only makes sense if we are 'Living' in the gap?
        # Let's assume we are performing the operation AT T_Gap.
        
        # We want to verify that if a Sync triggers at T_Gap, it clears the table.
        # We can simulate a 'Read Repair' or just manual trigger.
        # Or we can update a history record without changing its start time.
        
        from lex.process_admin.utils.model_registration import ModelRegistration
        # We need to access the inner function or triggering it via signal? 
        # Easier to specific update on history model.
        
        with patch('django.utils.timezone.now', return_value=T_Gap):
             # Trigger sync by "touching" the history record of MsgB?
             # But touching it might not change anything.
             # Let's manually invoke the logic if possible, or perform a NO-OP save on history.
             h_b = self.HistoryModel.objects.filter(name="MsgB").latest('history_id')
             h_b.save() # Just saving history record triggers post_save -> synchronize_main_model
            
        # At T_Gap, no record is valid (h_a ends T1, h_b starts T2).
        qs = RobustnessTestModel.objects.all()
        print(f"Main Table Count at T_Gap: {qs.count()}")
        self.assertEqual(qs.count(), 0)
