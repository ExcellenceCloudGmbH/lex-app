"""
Tests for Celery calculation context propagation.

Verifies:
    • ``CeleryCalculationContext`` snapshots and restores
      ``operation_context`` and ``_model_context`` across task boundaries
    • ``lex_shared_task`` wraps task functions with context propagation
    • Synchronous (non-worker) fallback preserves caller context
"""

from unittest.mock import patch

from django.db import models
from django.test import SimpleTestCase

from lex.api.utils import operation_context
from lex.audit_logging.utils.ModelContext import ModelContext, _model_context
from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery_tasks import CeleryCalculationContext, lex_shared_task


class DummyContextCalculationModel(CalculationModel):
    name = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):
        return None


class CeleryCalculationContextTests(SimpleTestCase):
    def setUp(self):
        self._original_operation_context = dict(operation_context.get() or {})
        self._original_model_context = _model_context.get()["model_context"]
        operation_context.set(
            {
                "operation_id": "",
                "request_obj": "",
                "calculation_id": "",
                "audit_log_temp": None,
                "actor": None,
            }
        )
        _model_context.get()["model_context"] = ModelContext()

    def tearDown(self):
        operation_context.set(self._original_operation_context)
        _model_context.get()["model_context"] = self._original_model_context
        super().tearDown()

    def test_context_manager_sets_worker_metadata_and_restores_operation_context(self):
        original_context = {
            "operation_id": "request-op",
            "request_obj": {"path": "/api/calculate"},
            "calculation_id": "calc-123",
            "audit_log_temp": "audit-entry",
            "actor": "tester",
        }
        operation_context.set(original_context)

        with CeleryCalculationContext(original_context, None):
            current_context = operation_context.get()
            self.assertEqual(current_context["calculation_id"], "calc-123")
            self.assertEqual(current_context["request_obj"], {"path": "/api/calculate"})
            self.assertEqual(current_context["audit_log_temp"], "audit-entry")
            self.assertEqual(current_context["actor"], "tester")
            self.assertNotEqual(current_context["operation_id"], "request-op")
            self.assertTrue(current_context["celery_task"])
            self.assertEqual(current_context["task_name"], "calc_and_save")

            current_context["request_obj"]["path"] = "/worker-only"
            self.assertEqual(original_context["request_obj"], {"path": "/api/calculate"})

        self.assertEqual(operation_context.get(), original_context)

    def test_context_manager_replaces_and_restores_model_context(self):
        original_model = DummyContextCalculationModel(id=1, name="parent")
        child_model = DummyContextCalculationModel(id=2, name="child")
        original_context = ModelContext([original_model])
        replacement_context = ModelContext([original_model, child_model])
        _model_context.get()["model_context"] = original_context

        with CeleryCalculationContext(None, replacement_context):
            active_context = _model_context.get()["model_context"]
            self.assertIs(active_context, replacement_context)
            self.assertEqual(active_context.current, child_model)
            self.assertEqual(active_context.parent, original_model)

        self.assertIs(_model_context.get()["model_context"], original_context)


class LexSharedTaskContextTests(SimpleTestCase):
    def test_lex_shared_task_skips_celery_context_when_no_worker_context_is_passed(self):
        @lex_shared_task(name="test_task_without_context")
        def sample_task():
            return "done"

        with patch("lex.lex_app.celery_tasks._celery_is_active", return_value=False), patch(
            "lex.lex_app.celery_tasks.CeleryCalculationContext"
        ) as celery_context_mock:
            result = sample_task()

        self.assertEqual(result[0], "done")
        celery_context_mock.assert_not_called()

    def test_lex_shared_task_runs_inside_celery_context_when_worker_context_is_passed(self):
        observed = {}
        model_context = ModelContext([DummyContextCalculationModel(id=7, name="worker")])
        worker_context = {
            "operation_id": "request-op",
            "request_obj": {"path": "/api/calculate"},
            "calculation_id": "calc-789",
            "audit_log_temp": None,
            "actor": "tester",
        }

        @lex_shared_task(name="test_task_with_context")
        def sample_task():
            observed["operation_context"] = dict(operation_context.get())
            observed["model_context"] = _model_context.get()["model_context"]
            return "done"

        with patch("lex.lex_app.celery_tasks._celery_is_active", return_value=False):
            result = sample_task(context=worker_context, model_context=model_context)

        self.assertEqual(result[0], "done")
        self.assertEqual(observed["operation_context"]["calculation_id"], "calc-789")
        self.assertTrue(observed["operation_context"]["celery_task"])
        self.assertEqual(observed["operation_context"]["task_name"], "calc_and_save")
        self.assertIs(observed["model_context"], model_context)
