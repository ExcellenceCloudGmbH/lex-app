from django.test import TransactionTestCase
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch
from io import StringIO
from django.db import models, connection
from lex.core.models.base import LexModel
from lex.process_admin.utils.model_registration import ModelRegistration

# Define Test Model
class TimeCmdTestModel(LexModel):
    name = models.CharField(max_length=100)
    class Meta:
        app_label = 'lex_app'

class ReconcileCommandTest(TransactionTestCase):
    
    def setUp(self):
        # Clean registration
        from simple_history.models import registered_models
        if TimeCmdTestModel in registered_models:
             del registered_models[TimeCmdTestModel]
             
        ModelRegistration._register_standard_model(TimeCmdTestModel, [])
        self.HistoryModel = TimeCmdTestModel.history.model
        
        # Create Tables
        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            if TimeCmdTestModel._meta.db_table in tables:
                schema_editor.delete_model(TimeCmdTestModel)
            schema_editor.create_model(TimeCmdTestModel)
            
            if self.HistoryModel._meta.db_table in tables:
                schema_editor.delete_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel)
            
            if self.HistoryModel.meta_history.model._meta.db_table in tables:
                schema_editor.delete_model(self.HistoryModel.meta_history.model)
            schema_editor.create_model(self.HistoryModel.meta_history.model)

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
            try: schema_editor.delete_model(self.HistoryModel.meta_history.model)
            except: pass
            try: schema_editor.delete_model(self.HistoryModel)
            except: pass
            try: schema_editor.delete_model(TimeCmdTestModel)
            except: pass

    def test_command_reconciles_stale_record(self):
        """
        Verify that the management command wakes up a record that became valid.
        """
        import datetime
        T0 = timezone.datetime(2025, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        T_Future = T0 + timedelta(hours=1) # 11:00
        
        # 1. Insert Future Record
        with patch('django.utils.timezone.now', return_value=T0):
            obj = TimeCmdTestModel(name="FutureCmd")
            obj._history_date = T_Future
            obj.save()
            
        # Verify Main Table is Empty
        with patch('django.utils.timezone.now', return_value=T0):
             self.assertEqual(TimeCmdTestModel.objects.count(), 0)
             
        # 2. Advance Time to 11:05 (Record became valid at 11:00)
        T_Now = T_Future + timedelta(minutes=5)
        
        with patch('django.utils.timezone.now', return_value=T_Now):
             # Verify Stale State
             self.assertEqual(TimeCmdTestModel.objects.count(), 0)
             
             # 3. Run Command (Window 10 mins covers 11:00)
             out = StringIO()
             call_command('reconcile_temporal', minutes=10, stdout=out)
             
             output = out.getvalue()
             print(output)
             
             # 4. Verify Sync
             self.assertIn("Synced 1 records", output)
             self.assertEqual(TimeCmdTestModel.objects.count(), 1)
             self.assertEqual(TimeCmdTestModel.objects.first().name, "FutureCmd")
             
