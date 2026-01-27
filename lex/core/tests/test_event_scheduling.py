from django.test import TransactionTestCase
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.db import models, connection
from lex.core.models.base import LexModel
from lex.process_admin.utils.model_registration import ModelRegistration
import sys

# Define Test Model
class SchedTestModel(LexModel):
    name = models.CharField(max_length=100)
    class Meta:
        app_label = 'lex_app'

# MOCK django_celery_beat if not present
from django_celery_beat.models import PeriodicTask, ClockedSchedule


class EventSchedulingTest(TransactionTestCase):
    
    def setUp(self):
        # Register Model
        from simple_history.models import registered_models
        if SchedTestModel in registered_models: del registered_models[SchedTestModel]
        
        ModelRegistration._register_standard_model(SchedTestModel, [])
        self.HistoryModel = SchedTestModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model
        
        # Create Tables
        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            if SchedTestModel._meta.db_table in tables: schema_editor.delete_model(SchedTestModel)
            schema_editor.create_model(SchedTestModel)
            if self.HistoryModel._meta.db_table in tables: schema_editor.delete_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel)
            if self.MetaModel._meta.db_table in tables: schema_editor.delete_model(self.MetaModel)
            schema_editor.create_model(self.MetaModel)

    def tearDown(self):
         # Cleanup
         pass

    def test_future_insert_creates_schedule(self):
        """Test that inserting a record in the future creates a PeriodicTask."""
        import datetime
        T_Now = timezone.datetime(2025, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        T_Future = T_Now + timedelta(hours=2)
        
        # Reset Mock
        try:
            from django_celery_beat.models import PeriodicTask
            # Clear objects if it's our mock
            if hasattr(PeriodicTask.objects, 'items'):
                PeriodicTask.objects.items = []
            else:
                PeriodicTask.objects.all().delete()
        except: pass

        with patch('django.utils.timezone.now', return_value=T_Now):
            obj = SchedTestModel(name="FutureVal")
            obj._history_date = T_Future
            obj.save()
            
        # Verify PeriodicTask Created
        # In real DB:
        count = PeriodicTask.objects.filter(task="activate_history_version").count()
        self.assertEqual(count, 1, "Should have created 1 PeriodicTask")
        # In real DB:
        count = PeriodicTask.objects.filter(task="activate_history_version").count()
        self.assertEqual(count, 1, "Should have created 1 PeriodicTask")
        # print("✓ Scheduling Verified")

        # Verify Meta Status
        h1 = self.HistoryModel.objects.first()
        m1 = h1.meta_history.latest('sys_from')
        self.assertEqual(m1.meta_task_status, "SCHEDULED")
        
        
    def test_deletion_revokes_schedule(self):
        """Test that deleting the future record revokes the task."""
        # Setup (Same as above)
        import datetime
        T_Now = timezone.datetime(2025, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        T_Future = T_Now + timedelta(hours=2)
        
        with patch('django.utils.timezone.now', return_value=T_Now):
            obj = SchedTestModel(name="ToDelete")
            obj._history_date = T_Future
            obj.save()
            
        # Verify Created
        h1 = self.HistoryModel.objects.first()
        from django_celery_beat.models import PeriodicTask
        
        # DELETE
        h1.delete()
        
        # Verify Revoked
        self.assertEqual(PeriodicTask.objects.count(), 0)
             
        self.assertEqual(PeriodicTask.objects.count(), 0)
             
        # print("✓ Revocation Verified")
