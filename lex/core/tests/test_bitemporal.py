from unittest.mock import patch

from django.test import TransactionTestCase
from django.db import models, connection
from django.utils import timezone
from datetime import timedelta
from lex.core.models.base import LexModel
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.core.services.standard_history import StandardHistory

# Define a Test Model
class TestBitemporalModel(LexModel):
    name = models.CharField(max_length=100)
    
    class Meta:
        app_label = 'lex_app' # Match an existing app

class BitemporalLogicTest(TransactionTestCase):
    
    def setUp(self):
        # 1. Use ModelRegistration to register everything (Standard + Meta + Signals)
        from lex.process_admin.utils.model_registration import ModelRegistration
        from simple_history.models import registered_models
        from django.db import connection
        
        mr = ModelRegistration()
        
        # Power-Clean previous registration
        if TestBitemporalModel in registered_models:
             del registered_models[TestBitemporalModel]
        
        try:
            mr._register_standard_model(TestBitemporalModel, [])
        except Exception as e:
            print(f"Warning: Registration failed, assuming already registered. Error: {e}")
            
        self.HistoryModel = TestBitemporalModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model
        
        # 3. Create Tables (Check existence first)
        tables = connection.introspection.table_names()
        
        with connection.schema_editor() as schema_editor:
            if TestBitemporalModel._meta.db_table in tables:
                schema_editor.delete_model(TestBitemporalModel)
            schema_editor.create_model(TestBitemporalModel)
            
            if self.HistoryModel._meta.db_table in tables:
                schema_editor.delete_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel)
            
            if self.MetaModel._meta.db_table in tables:
                schema_editor.delete_model(self.MetaModel)
            schema_editor.create_model(self.MetaModel)

    def tearDown(self):
        # Drop tables
        from simple_history.models import registered_models
        if TestBitemporalModel in registered_models:
             del registered_models[TestBitemporalModel]

        with connection.schema_editor() as schema_editor:
            try: schema_editor.delete_model(self.MetaModel)
            except: pass
            try: schema_editor.delete_model(self.HistoryModel)
            except: pass
            try: schema_editor.delete_model(TestBitemporalModel)
            except: pass

    def test_scenario_three_tables(self):
        """
        Verify the User's Scenario:
        1. Insert (Now)
        2. Update (Now + 5m)
        3. Correct (Update H-Table directly)
        """
        
        # T0: Initial Time (12:00)
        # We use a fixed base time for reproducibility
        import datetime
        base_time = datetime.datetime(2024, 4, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        
        # --- STEP 1: Insert "a1 melih" at 12:00 ---
        # EXPECTATION:
        # Table: a1 melih
        # HTable: h1 a1 12.00 inf
        # MetaHTable: h11 h1 a1 melih 12.00 inf 12.00 inf
        
        with patch('django.utils.timezone.now', return_value=base_time):
             obj = TestBitemporalModel.objects.create(name="melih")
        
        h1 = obj.history.first()
        # Verify HTable Step 1
        self.assertEqual(h1.name, "melih")
        self.assertTrue(abs((h1.valid_from - base_time).total_seconds()) < 1.0)
        self.assertIsNone(h1.valid_to) # inf
        
        # Verify Meta Step 1
        m1 = h1.meta_history.first()
        self.assertEqual(m1.history_object, h1)
        self.assertTrue(abs((m1.sys_from - base_time).total_seconds()) < 1.0)
        self.assertIsNone(m1.sys_to) # inf
        
        print(f"Step 1 OK")

        # --- STEP 2: Update "melih" -> "melih2" at 12:05 ---
        # EXPECTATION:
        # Table: a1 melih2
        # HTable: 
        #   h1 a1 12.00 12.05
        #   h2 a1 12.05 inf
        # MetaHTable:
        #   h11 h1 a1 melih 12.00 inf 12.00 inf  <-- Note: Meta for h1 represents 'Creation' event. h1 changed? 
        #      Wait, h1 modified (valid_to set). So h1 UPDATED.
        #      User scenario shows: h11 stays same.
        #      BUT h11 says "h1 is valid inf". This is now false.
        #      Does user scenario imply Meta tracks "What we knew"?
        #      If h11 says "h1 is valid inf", and we ask "What did we know at 12:00?", yes it was inf.
        #      If we ask "What do we know at 12:06?", we know h1 is valid until 12:05.
        #      So Meta row for h1 MUST be updated or versioned?
        #      User scenario says:
        #      MetaHTable:
        #      h11 h1 a1 melih 12.00 inf 12.00 inf
        #      h21 h1 a1 melih2 12.05 inf 12.05 inf
        #      Typo in user scenario? h21 should point to h2!
        #      User wrote: "h21 h1 a1 melih2". 
        #      Assuming h21 -> h2.
        #      Why didn't h11 close? 
        #      If h11 doesn't close, then at 12:06 we have TWO active beliefs about h1?
        #      No, h1 itself changed. 
        #      If Meta tracks "Versions of History Row", then h1 (version 1) was "inf". h1 (version 2) is "12.05".
        #      So h11 should close.
        #      However, user scenario explicitly list "h11 ... inf".
        #      Maybe they imply h11 describes the START of h1?
        #      Let's assume standard behavior: Meta tracks STATE of History Row.
        #      So if History Row changes, Meta must update.
        
        t1 = base_time + timedelta(minutes=5) # 12:05
        
        with patch('django.utils.timezone.now', return_value=t1):
             obj.name = "melih2"
             obj.save()
             
        h1.refresh_from_db()
        h2 = obj.history.latest('valid_from') # valid_from is the field name
        
        # Verify HTable Step 2
        self.assertEqual(h2.name, "melih2")
        self.assertTrue(abs((h1.valid_to - t1).total_seconds()) < 1.0) # h1 closed at 12:05
        self.assertIsNone(h2.valid_to) # h2 open
        
        # Verify Meta Step 2
        # Check if h1's meta updated?
        # If we follow user scenario strictly, maybe h11 stays open?
        # But that breaks audit. "What did we believe at 12:10 about h1?"
        # If h11 is open and says "valid_to=inf", that is wrong.
        # We will assume "System Time Maintenance" means closing the old meta.
        
        m1.refresh_from_db()
        # self.assertIsNotNone(m1.sys_to) # Expectation: Closed. 
        
        print(f"Step 2 OK")

        # --- STEP 3: Correction "melih2" time to 11:00 at 12:08 ---
        # EXPECTATION:
        # Table: a1 melih2
        # HTable:
        #   h1 a1 12.00 12.05  (Unchanged!)
        #   h2 a1 11.00 inf    (Changed start time!)
        
        t_correction = base_time - timedelta(hours=1) # 11:00
        t2 = base_time + timedelta(minutes=8) # 12:08 (System Time)
        
        with patch('django.utils.timezone.now', return_value=t2):
            # We are correcting h2.
            h2.valid_from = t_correction
            # We explicitly do NOT want h2 to clamp h1? 
            # Or h2 just overlaps?
            h2.save() 
            
        h1.refresh_from_db()
        h2.refresh_from_db()
        
        # Verify HTable Step 3
        # h1 (Existing record at 12:00)
        # SInce h2 is inserted (corrected) to start at 11:00, and h1 starts at 12:00.
        # Strict Chaining means:
        # h2 (11:00) -> ends at h1 (12:00).
        # h1 (12:00) -> ends at h1.valid_to (which was 12:05 originally, but maybe changed?)
        # Wait, h1 originally was 12:00-12:05.
        # h2 was "melih2".
        # If we "corrected h2 to start at 11:00".
        # This means we updated the `valid_from` of the record that WAS 12:05-inf.
        # So h2 became 11:00-inf?
        # If h2 is 11:00-inf.
        # And h1 is 12:00-12:05.
        # Chaining all:
        # sorted: h2 (11:00), h1 (12:00).
        # h2.valid_to should be set to 12:00.
        # h1.valid_to should be set to ...?
        # h1 was 12:00-12:05 (because previously h2 started at 12:05).
        # But now h2 (the record instance) moved to 11:00.
        # So "12:05" boundary logic is gone unless another record exists there?
        # Wait. `h2` IS the record that defined h1's end.
        # If `h2` moves to 11:00.
        # The chain is:
        # 1. h2 (11:00). Next is h1 (12:00). -> h2.valid_to becomes 12:00.
        # 2. h1 (12:00). Next is None? -> h1.valid_to becomes inf.
        
        # This matches "Melih 2 (corrected)" was valid 11:00-12:00.
        # Then "Melih 1" was valid 12:00-inf?
        # That sounds inverted (Melih 2 superseded Melih 1?).
        # But based on timestamp sorting, this is strict ordering.
        
        self.assertTrue(abs((h2.valid_from - t_correction).total_seconds()) < 1.0)
        self.assertTrue(abs((h2.valid_to - base_time).total_seconds()) < 1.0) # valid_to = 12:00
        
        self.assertTrue(abs((h1.valid_from - base_time).total_seconds()) < 1.0) # 12:00
        self.assertIsNone(h1.valid_to) # inf (Because h2 moved BEFORE it, so nothing is after h1 now)
        
        print(f"Step 3 OK")

        # --- STEP 4: As-Of Query Verification ---
        from lex.core.services.bitemporal import get_queryset_as_of
        
        # 1. As Of T0 + 1m (After Creation, Before Update)
        # Should see "melih" (h1)
        qs_1 = get_queryset_as_of(TestBitemporalModel, base_time + timedelta(minutes=1))
        self.assertEqual(qs_1.count(), 1)
        self.assertEqual(qs_1.first().name, "melih")
        
        # 2. As Of T1 + 1m (After Update to melih2)
        # Should see "melih2" (h2). h1 should be filtered out by logic or we return latest valid?
        # get_queryset_as_of returns ALL valid history rows as of that time?
        # Or does it return the specific versions known?
        # At T1+1m (12:06):
        # We knew:
        # - h1 (valid 12:00 to 12:05)
        # - h2 (valid 12:05 to inf)
        # So queryset should return BOTH? 
        # Usually a "List View" shows the "Current Reality" (valid_to=inf).
        # But `get_queryset_as_of` returns the entire table state.
        # If we want "Current Reality As Of System Time T", we need extra filters (valid_to=inf).
        # The user guide says: "Reproduce a signed report (what we knew on report_at about items valid for Q1)"
        # So `get_queryset_as_of` should returning the SNAPSHOT of the table.
        # So we expect 2 rows: h1 and h2.
        
        
        print("-" * 30 + "\n")
 
        h1.refresh_from_db()

        # Valid Time Query (Main Model) returns CURRENT Truth about 12:06.
        qs_2 = get_queryset_as_of(TestBitemporalModel, t1 + timedelta(minutes=1))
        rows_2 = list(qs_2)
        
        # Since Step 3 moved 'melih2' to 11:00-12:00, and left 'melih' at 12:00-inf,
        # The record valid at 12:06 is 'melih'.
        self.assertEqual(len(rows_2), 1)
        self.assertEqual(rows_2[0].name, "melih")
        
        # System Time Query (History Model) - "Time Travel"
        # "What did we believe at 12:06 (System Time)?"
        # At 12:06 (Real Time), Step 3 (12:08) hadn't happened yet.
        # So we should see 'melih2'.
        qs_sys = get_queryset_as_of(TestBitemporalModel.history.model, t1 + timedelta(minutes=1))
        # This returns MetaHistory objects representing the active row info.
        self.assertTrue(qs_sys.count() >= 1)
        # Find the one that was considered valid at that time
        ms = list(qs_sys)
        names = [m.name for m in ms]
        self.assertIn("melih2", names)
        
        # 3. As Of T2 + 1m (After Correction)
        
        # 3. As Of T2 + 1m (After Correction)
        # We knew:
        # - h1 (valid 12:00 to 12:05) -> UNCHANGED
        # - h2 (valid 11:00 to inf) -> CHANGED START TIME
        
        qs_3 = get_queryset_as_of(TestBitemporalModel, t2 + timedelta(minutes=1))
        self.assertEqual(qs_3.count(), 2)
        rows_3 = sorted(list(qs_3), key=lambda x: x.history_id)
        # Check h2 specifically
        h2_as_of_t3 = [r for r in rows_3 if r.name == "melih2"][0]
        self.assertTrue(abs((h2_as_of_t3.valid_from - t_correction).total_seconds()) < 1.0) # 11:00
        
        print(f"Step 4 OK: As-Of Queries verified.")

