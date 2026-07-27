"""
Tests for future-record event scheduling via ``django-celery-beat``.

Verifies:
    • Inserting a future-valid record creates a ``PeriodicTask``
    • Deleting the future record revokes the scheduled task
    • Meta-history tracks the ``SCHEDULED`` status
"""

from datetime import timedelta
from unittest.mock import patch

from django.db import models, connection
from django.test import TransactionTestCase
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from lex.core.models.LexModel import LexModel
from lex.process_admin.utils.model_registration import ModelRegistration


class SchedTestModel(LexModel):
    """Test model used by both scheduling and history-API tests."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class EventSchedulingTest(TransactionTestCase):
    """Prove future-record scheduling and revocation via ``django-celery-beat``."""
    
    def setUp(self):
        from simple_history.models import registered_models
        if SchedTestModel in registered_models:
            del registered_models[SchedTestModel]

        ModelRegistration._register_standard_model(SchedTestModel, [])
        self.HistoryModel = SchedTestModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            if SchedTestModel._meta.db_table in tables:
                schema_editor.delete_model(SchedTestModel)
            schema_editor.create_model(SchedTestModel)
            if self.HistoryModel._meta.db_table in tables:
                schema_editor.delete_model(self.HistoryModel)
            schema_editor.create_model(self.HistoryModel)
            if self.MetaModel._meta.db_table in tables:
                schema_editor.delete_model(self.MetaModel)
            schema_editor.create_model(self.MetaModel)

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
            try:
                schema_editor.delete_model(self.MetaModel)
            except Exception:
                pass
            try:
                schema_editor.delete_model(self.HistoryModel)
            except Exception:
                pass
            try:
                schema_editor.delete_model(SchedTestModel)
            except Exception:
                pass

    def test_future_insert_creates_schedule(self):
        """Inserting a future-valid record creates a ``PeriodicTask``."""
        T_Now = timezone.datetime(2025, 1, 1, 10, 0, 0)
        T_Future = T_Now + timedelta(hours=2)

        try:
            PeriodicTask.objects.all().delete()
        except Exception:
            pass

        with patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False):
            with patch('django.utils.timezone.now', return_value=T_Now):
                obj = SchedTestModel(name="FutureVal")
                obj._history_date = T_Future
                obj.save()

        count = PeriodicTask.objects.filter(task="activate_history_version").count()
        self.assertEqual(count, 1, "Should have created 1 PeriodicTask")

        h1 = self.HistoryModel.objects.first()
        m1 = h1.meta_history.latest('sys_from')
        self.assertEqual(m1.meta_task_status, "SCHEDULED")
        
        
    def test_deletion_revokes_schedule(self):
        """Deleting a future-valid history record revokes the scheduled task."""
        T_Now = timezone.datetime(2025, 1, 1, 10, 0, 0)
        T_Future = T_Now + timedelta(hours=2)

        with patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False):
            with patch('django.utils.timezone.now', return_value=T_Now):
                obj = SchedTestModel(name="ToDelete")
                obj._history_date = T_Future
                obj.save()

        h1 = self.HistoryModel.objects.first()
        h1.delete()

        self.assertEqual(PeriodicTask.objects.count(), 0)
