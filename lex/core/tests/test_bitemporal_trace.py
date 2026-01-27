from django.test import TransactionTestCase
from django.db import models
from datetime import timedelta, datetime, timezone as dt_timezone
from unittest import mock
from lex.process_admin.utils.model_registration import ModelRegistration

# Define a dedicated test model for this trace
class TraceModel(models.Model):
    name = models.CharField(max_length=100)
    class Meta:
        app_label = 'lex_app'

class BitemporalTraceTest(TransactionTestCase):
    def setUp(self):
        # Register the model with history
        from simple_history.models import registered_models
        from django.db import connection
        
        # Clean up registration if exists from previous run
        if TraceModel in registered_models:
             del registered_models[TraceModel]

        ModelRegistration._register_standard_model(TraceModel, [])
        self.HistoryModel = TraceModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model
        
        # Create Tables
        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            if TraceModel._meta.db_table in tables:
                schema_editor.delete_model(TraceModel)
            schema_editor.create_model(TraceModel)
            
            if self.HistoryModel._meta.db_table in tables:
                schema_editor.delete_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel)
            
            if self.MetaModel._meta.db_table in tables:
                schema_editor.delete_model(self.MetaModel)
            schema_editor.create_model(self.MetaModel)

    def tearDown(self):
        from django.db import connection
        from simple_history.models import registered_models
        if TraceModel in registered_models:
             del registered_models[TraceModel]

        with connection.schema_editor() as schema_editor:
             try: schema_editor.delete_model(self.MetaModel)
             except: pass
             try: schema_editor.delete_model(self.HistoryModel)
             except: pass
             try: schema_editor.delete_model(TraceModel)
             except: pass

    def test_trace_scenario(self):
        """
        Verify the exact trace provided by the user.
        1) 12:00: Insert Melih (Valid 12:00-Inf)
        2) 12:05: Update to Melih2 (Valid 12:05-Inf)
        3) 12:08: Correct Melih2 valid_from to 13:00.
        """
        
        # --- STEP 1: Insert Melih at 12:00 ---
        t_1200 = datetime(2026, 1, 26, 12, 0, 0, tzinfo=dt_timezone.utc)
        
        with mock.patch('django.utils.timezone.now', return_value=t_1200):
            obj = TraceModel.objects.create(name="melih")
            
        # Verify Step 1
        # HTable: melih 12:00 inf
        # HHTable: melih 12:00 inf 12:00 inf (or similar)
        
        h_objs = list(self.HistoryModel.objects.filter(id=obj.id).order_by('valid_from'))
        self.assertEqual(len(h_objs), 1)
        self.assertEqual(h_objs[0].name, "melih")
        self.assertEqual(h_objs[0].valid_from, t_1200)
        self.assertIsNone(h_objs[0].valid_to)

        # --- STEP 2: Update to Melih2 at 12:05 ---
        t_1205 = datetime(2026, 1, 26, 12, 5, 0, tzinfo=dt_timezone.utc)
        
        with mock.patch('django.utils.timezone.now', return_value=t_1205):
            obj.name = "melih2"
            obj.save()
            
        # Verify Step 2
        # HTable: 
        # melih 12:00 12:05
        # melih2 12:05 inf
        
        h_objs = list(self.HistoryModel.objects.filter(id=obj.id).order_by('valid_from'))
        self.assertEqual(len(h_objs), 2)
        
        # Melih
        self.assertEqual(h_objs[0].name, "melih")
        self.assertEqual(h_objs[0].valid_to, t_1205)
        
        # Melih2
        self.assertEqual(h_objs[1].name, "melih2")
        self.assertEqual(h_objs[1].valid_from, t_1205)
        self.assertIsNone(h_objs[1].valid_to)


        # --- STEP 3: Correction at 12:08 (Melih2 starts at 13:00) ---
        t_1208 = datetime(2026, 1, 26, 12, 8, 0, tzinfo=dt_timezone.utc)
        t_1300 = datetime(2026, 1, 26, 13, 0, 0, tzinfo=dt_timezone.utc)
        
        with mock.patch('django.utils.timezone.now', return_value=t_1208):
            # We access the History Object for Melih2 (Active one)
            # and Update its valid_from
            h_melih2 = h_objs[1]
            h_melih2.valid_from = t_1300
            h_melih2.save()
            
        # Verify Step 3 (Crucial Check)
        # HTable Expectation:
        # melih 12:00 13:00
        # melih2 13:00 inf
        
        h_objs = list(self.HistoryModel.objects.filter(id=obj.id).order_by('valid_from'))
        self.assertEqual(len(h_objs), 2)
        
        # Verify Melih (should be extended to 13:00 automatically by strict chaining)
        self.assertEqual(h_objs[0].name, "melih")
        self.assertEqual(h_objs[0].valid_to, t_1300) 
        
        # Verify Melih2
        self.assertEqual(h_objs[1].name, "melih2")
        self.assertEqual(h_objs[1].valid_from, t_1300)
        self.assertIsNone(h_objs[1].valid_to)

        print("\n--- PASSED: HTable Logic matches User Trace ---\n")

        # Verify HHTable (Meta) Expectation
        # User expects:
        # Melih 12:00 inf 12:00 12:05
        # Melih 12:00 13:00 12:05 inf (Wait, User trace said 12:05, but this is the corrected version of Melih?)
        # Let's see what we actually have.
        
        print("\n--- DEBUG: Meta Trace ---")
        meta_objs = list(self.MetaModel.objects.order_by('sys_from', 'id'))
        for m in meta_objs:
             # Fetch the history object it refers to, to know WHICH history version it is
             # But history object attributes might be current? 
             # No, Meta stores a snapshot of attributes usually?
             # My implementation in ModelRegistration copies attributes:
             # attrs[field.attname] = getattr(instance, field.attname)
             # So 'm.name' should exist if we copied fields into meta model?
             # Wait, Simple History Level 2 usually adds fields to the model.
             # My `MetaLevelHistoricalRecords` defines `extra_fields` but does it include original fields?
             # Yes: `for field in self.fields_included(instance): attrs...` 
             
             # Let's print relevant fields
             print(f"MetaID={m.pk} HistID={m.history_object_id} Name={getattr(m, 'name', 'N/A')} " 
                   f"ValidFrom={getattr(m, 'valid_from', 'N/A')} ValidTo={getattr(m, 'valid_to', 'N/A')} "
                   f"SysFrom={m.sys_from} SysTo={m.sys_to}")
        print("-------------------------\n")
