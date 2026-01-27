from unittest.mock import patch
from django.test import TransactionTestCase
from django.db import models, connection
from datetime import timedelta
from lex.core.models.base import LexModel
import datetime

# Define a Test Model uniquely for this test to avoid collisions
class ScenarioTestModel(LexModel):
    name = models.CharField(max_length=100)
    class Meta:
        app_label = 'lex_app'

class BitemporalScenarioTest(TransactionTestCase):
    
    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration
        
        # Manually register the model
        mr = ModelRegistration()
        try:
            mr._register_standard_model(ScenarioTestModel, [])
        except Exception as e:
            pass
            
        self.HistoryModel = ScenarioTestModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model
        
        # Create Tables
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(ScenarioTestModel)
            schema_editor.create_model(self.HistoryModel)
            schema_editor.create_model(self.MetaModel)

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
            try: schema_editor.delete_model(self.MetaModel)
            except: pass
            try: schema_editor.delete_model(self.HistoryModel)
            except: pass
            try: schema_editor.delete_model(ScenarioTestModel)
            except: pass
            
    def format_dt(self, dt):
        if dt is None: return "inf"
        return dt.strftime("%H:%M")

    def test_user_scenario(self):
        # Base Time: 12:00
        # We assume specific date is not important, only time
        T12_00 = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        T12_05 = T12_00 + timedelta(minutes=5)
        T12_08 = T12_00 + timedelta(minutes=8)
        T13_00 = T12_00 + timedelta(hours=1)
        
        print("\n=== SCENARIO START ===")

        # 1) insert melih in Table at 12:00
        print("\n>>> STEP 1: Insert melih at 12:00")
        with patch('django.utils.timezone.now', return_value=T12_00):
            obj = ScenarioTestModel.objects.create(name="melih")
            
        # Verify
        h_rows = list(self.HistoryModel.objects.all().order_by('history_id'))
        hh_rows = list(self.MetaModel.objects.all().order_by('id'))
        
        print("HTable:")
        for h in h_rows:
            print(f"{h.name} {self.format_dt(h.valid_from)} {self.format_dt(h.valid_to)}")
            
        print("HHTable:")
        for m in hh_rows:
             # Fetch underlying history object values from the meta copy
             # Note uses meta copy fields
             print(f"{m.name} {self.format_dt(m.valid_from)} {self.format_dt(m.valid_to)} {self.format_dt(m.sys_from)} {self.format_dt(m.sys_to)}")

        # 2) We update melih to melih2 at 12:05
        print("\n>>> STEP 2: Update melih to melih2 at 12:05")
        with patch('django.utils.timezone.now', return_value=T12_05):
            obj.name = "melih2"
            obj.save()
            
        # Verify
        h_rows = list(self.HistoryModel.objects.all().order_by('valid_from'))
        hh_rows = list(self.MetaModel.objects.all().order_by('sys_from', 'valid_from')) # Ordering? User: melih, melih, melih2
        
        print("HTable:")
        for h in h_rows:
            print(f"{h.name} {self.format_dt(h.valid_from)} {self.format_dt(h.valid_to)}")
            
        print("HHTable:")
        for m in hh_rows:
             print(f"{m.name} {self.format_dt(m.valid_from)} {self.format_dt(m.valid_to)} {self.format_dt(m.sys_from)} {self.format_dt(m.sys_to)}")

        # 3) We change the history of the melih2 from 12:05 to 13:00 (Correction) at 12:08
        print("\n>>> STEP 3: Correction melih2 12:05 -> 13:00 at 12:08")
        with patch('django.utils.timezone.now', return_value=T12_08):
            # Find melih2 record
            h_melih2 = self.HistoryModel.objects.get(name="melih2")
            h_melih2.valid_from = T13_00
            h_melih2.save()
            
        # Verify HTable
        # Expected:
        # melih  12:00 13:00
        # melih2 13:00 inf
        h_rows = list(self.HistoryModel.objects.all().order_by('valid_from'))
        self.assertEqual(len(h_rows), 2)
        
        self.assertEqual(h_rows[0].name, "melih")
        self.assertEqual(h_rows[0].valid_from, T12_00)
        self.assertEqual(h_rows[0].valid_to, T13_00)
        
        self.assertEqual(h_rows[1].name, "melih2")
        self.assertEqual(h_rows[1].valid_from, T13_00)
        self.assertIsNone(h_rows[1].valid_to)

        # Verify HHTable (Meta)
        # Check specific record for 'melih' that was updated in-place
        # It should have sys_from=12:05 (creation of the close record) but valid_to=13:00
        m_melih_closed = self.MetaModel.objects.filter(name="melih", valid_to__isnull=False).latest('sys_from')
        self.assertEqual(m_melih_closed.valid_to, T13_00)
        self.assertEqual(m_melih_closed.sys_from, T12_05)
        self.assertIsNone(m_melih_closed.sys_to) # Still open/active knowledge

        # Verify Main Table State "As Of Now" (12:08)
        # Since melih2 is pushed to 13:00, and melih ends at 13:00.
        # At 12:08, "melih" is valid.
        current_obj = ScenarioTestModel.objects.get(pk=obj.pk)
        print(f"Main Table State at 12:08: {current_obj.name}")
        self.assertEqual(current_obj.name, "melih")

        print("Step 3 Verified.")
        
        # 4) Extra Case: "Re-closing" correction.
        # Change melih2 valid_from BACK to 12:30 at 12:10.
        # This is another refinement (Value -> Value on neighbour 'melih')
        print("\n>>> STEP 4: Correction melih2 13:00 -> 12:30 at 12:10")
        T12_10 = T12_00 + timedelta(minutes=10)
        T12_30 = T12_00 + timedelta(minutes=30)
        
        with patch('django.utils.timezone.now', return_value=T12_10):
            h_melih2.valid_from = T12_30
            h_melih2.save()
            
        # melih should now end at 12:30.
        # The SAME meta record (sys_from=12:05) should be updated again?
        # Yes, because it's still the active definition of "when melih ended".
        
        m_melih_closed.refresh_from_db()
        self.assertEqual(m_melih_closed.valid_to, T12_30)
        self.assertEqual(m_melih_closed.sys_from, T12_05) # Still 12:05
        
        print("Step 4 Verified.")

    def test_deletion(self):
        """
        Verify deletion logic:
        1. Insert at 12:00
        2. Delete at 12:10
        3. Verify Main Table is gone? Or marked deleted?
           Standard simple_history deletes the main table row.
           Bitemporal logic usually keeps it? 
           But our valid_to logic is on History Table.
           If we delete main row, we can't query it.
        """
        T12_00 = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        T12_10 = T12_00 + timedelta(minutes=10)
        
        with patch('django.utils.timezone.now', return_value=T12_00):
            obj = ScenarioTestModel.objects.create(name="to_be_deleted")
            
        # Verify Created
        self.assertEqual(ScenarioTestModel.objects.count(), 1)
        
        with patch('django.utils.timezone.now', return_value=T12_10):
            obj.delete()
            
        # Verify Main Table Deleted
        self.assertEqual(ScenarioTestModel.objects.count(), 0)
        
        # Verify History exists and is closed?
        # Simple History creates a '-' record at 12:10.
        # Strict chaining should see this '-' record and close the previous '+' record.
        
        h_rows = list(self.HistoryModel.objects.all().order_by('history_id'))
        # h1: +, 12:00
        # h2: -, 12:10
        self.assertEqual(len(h_rows), 2)
        self.assertEqual(h_rows[1].history_type, '-')
        self.assertEqual(h_rows[1].valid_from, T12_10)
        
        # Check chaining on h1
        self.assertEqual(h_rows[0].valid_to, T12_10)
        
    def test_retroactive_creation(self):
        """
        Verify inserting a record valid from the past.
        """
        T12_00 = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        T11_00 = T12_00 - timedelta(hours=1)
        
        # 1. Create at 12:00 but FORCE valid_from = 11:00
        # This is tricky with standard create() unless we manually set _history_date
        
        with patch('django.utils.timezone.now', return_value=T12_00):
            obj = ScenarioTestModel(name="retro")
            obj._history_date = T11_00
            obj.save()
            
        h1 = self.HistoryModel.objects.first()
        self.assertEqual(h1.valid_from, T11_00)
        self.assertIsNone(h1.valid_to)
        
        # Verify Meta
        # Meta sys_from should be 12:00 (Creation Time)
        m1 = self.MetaModel.objects.first()
        self.assertEqual(m1.sys_from, T12_00)
        self.assertIsNone(m1.sys_to)


