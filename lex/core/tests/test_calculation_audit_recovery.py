"""
Tests for calculation audit-log recovery after failures.

Why this matters
----------------
Customer-visible: when a user triggers a calculation that blows up, the
audit trail must show exactly one terminal record with the real traceback
so support can diagnose the failure. If the audit row is left in
``in_progress``, the UI shows a stuck calculation forever. These tests
pin the contract between ``CalculationModel`` error handling and the
``AuditLog`` / ``AuditLogStatus`` pair.

Why the sync path
-----------------
The failure recovery logic under test lives in
``CalculationModel.calculate_hook``'s ``except`` branch, which is reached
identically via the sync fallback or the Celery worker's in-process
execution. Testing via the sync path (``CELERY_ACTIVE=False``) gives us
a deterministic, broker-free run that still exercises the real error
handler. The Celery-dispatch path is covered separately in
``test_celery_callbacks`` which owns the broker boundary.
"""

import os
import traceback
from unittest.mock import patch

from django.db import connection, models
from django.test import TransactionTestCase

from lex.api.utils import OperationContext
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.core.models.CalculationModel import CalculationModel, CalculationModelException
from lex.core.models.LexModel import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task


class AuditRecoveryCalculationModel(CalculationModel):
    name = models.CharField(max_length=100)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        app_label = "lex_app"

    @lex_shared_task
    def calculate(self):
        raise ValueError("sync calculation exploded")

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class CalculationAuditRecoveryTests(TransactionTestCase):
    """
    Exercise the sync-path failure branch in ``CalculationModel.calculate_hook``
    against a real (temporary) model with a real database, asserting on the
    persisted ``AuditLog`` / ``AuditLogStatus`` rows.

    ``CELERY_ACTIVE`` is explicitly set to ``"False"`` for every test so the
    test is deterministic regardless of local ``.env`` state — when the env
    leaks ``CELERY_ACTIVE=True``, ``CalculationModel`` tries to dispatch via
    the broker and swallows our ``ValueError`` behind a Redis connection
    error. The release-gating CI runs with ``CELERY_ACTIVE=False`` so the
    patch here matches the contract the CI pipeline is validating.
    """

    # Models whose tables we created ourselves (and therefore must drop).
    _created_tables = []

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        existing = set(connection.introspection.table_names())
        cls._created_tables = []
        with connection.schema_editor() as schema_editor:
            for model in (AuditLog, AuditLogStatus, AuditRecoveryCalculationModel):
                if model._meta.db_table not in existing:
                    schema_editor.create_model(model)
                    cls._created_tables.append(model)

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._created_tables:
                with connection.schema_editor() as schema_editor:
                    for model in reversed(cls._created_tables):
                        schema_editor.delete_model(model)
        finally:
            super().tearDownClass()

    def _fixture_teardown(self):
        # SQLite enforces FK constraints during flush but Django doesn't
        # guarantee deletion order.  Temporarily disable for SQLite only.
        if connection.vendor == "sqlite":
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA foreign_keys = OFF")
        try:
            super()._fixture_teardown()
        finally:
            if connection.vendor == "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA foreign_keys = ON")

    def setUp(self):
        super().setUp()
        # Force the sync execution path. `clear=False` so we don't nuke the
        # rest of the test environment (DJANGO_SETTINGS_MODULE etc.).
        self._env_patch = patch.dict(
            os.environ, {"CELERY_ACTIVE": "False"}, clear=False
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_sync_atomic_failure_creates_terminal_audit_records(self):
        """
        When a sync calculation raises, the failure branch in
        ``calculate_hook`` must:

        1. Re-raise as ``CalculationModelException`` so callers see the error.
        2. Flip ``is_calculated`` on the instance to ``ERROR``.
        3. Write exactly one ``AuditLog`` row keyed by the calculation id,
           pointing at the real resource and object id.
        4. Attach an ``AuditLogStatus`` row with ``status="failure"`` whose
           ``error_traceback`` contains the original exception's message.

        Regression guard: the prior version of this test asserted on
        ``"sync calculation exploded"`` but was flaky because a leaked
        ``CELERY_ACTIVE=True`` pushed execution through the broker and
        the assertion matched a ``redis.ConnectionError`` traceback instead.
        """
        instance = AuditRecoveryCalculationModel.objects.create(name="initial")

        with self.assertRaises(CalculationModelException) as raised:
            with OperationContext({}, "calc-sync-failure"):
                with model_logging_context(instance):
                    instance.is_calculated = CalculationModel.IN_PROGRESS
                    instance.save()

        CalculationModel.persist_error_state(raised.exception.calc_obj)
        instance.refresh_from_db()
        audit_log = AuditLog.objects.get(calculation_id="calc-sync-failure")
        audit_status = AuditLogStatus.objects.get(audit_log=audit_log)

        self.assertEqual(instance.is_calculated, CalculationModel.ERROR)
        self.assertEqual(audit_log.resource, "auditrecoverycalculationmodel")
        self.assertEqual(audit_log.object_id, instance.pk)
        self.assertEqual(audit_status.status, "failure")
        self.assertIn("sync calculation exploded", audit_status.error_traceback)

    def test_nested_calculation_failure_keeps_inner_traceback_in_audit_status(self):
        """
        When the user's ``calculate`` method explicitly raises a
        ``CalculationModelException`` whose ``stack_trace`` points at an
        *inner* failure (e.g. a SharePoint 401), and then that raise is
        itself wrapped in a generic outer ``Exception``, the error-recovery
        logic must preserve the inner traceback on the audit record — so
        support sees the real root cause, not the wrapper.

        Concretely: ``error_traceback`` contains the inner exception text
        ("sharepoint 401 unauthorized") but *not* the outer wrapper
        ("wrapped at period level").
        """
        instance = AuditRecoveryCalculationModel.objects.create(name="nested")

        def wrapped_calculate(model_instance):
            try:
                raise ValueError("sharepoint 401 unauthorized")
            except ValueError:
                inner_traceback = traceback.format_exc()

            try:
                raise CalculationModelException(
                    calc_obj=[model_instance],
                    exception_details=[
                        "SharePoint Server cannot handle requests at the moment."
                    ],
                    stack_trace=[inner_traceback],
                )
            except CalculationModelException:
                raise Exception("wrapped at period level")

        with patch.object(
            AuditRecoveryCalculationModel,
            "calculate",
            new=wrapped_calculate,
        ):
            with self.assertRaises(CalculationModelException) as raised:
                with OperationContext({}, "calc-nested-failure"):
                    with model_logging_context(instance):
                        instance.is_calculated = CalculationModel.IN_PROGRESS
                        instance.save()

        CalculationModel.persist_error_state(raised.exception.calc_obj)
        audit_log = AuditLog.objects.get(calculation_id="calc-nested-failure")
        audit_status = AuditLogStatus.objects.get(audit_log=audit_log)

        self.assertEqual(
            raised.exception.exception_details[0],
            "SharePoint Server cannot handle requests at the moment.",
        )
        self.assertIn("sharepoint 401 unauthorized", raised.exception.stack_trace[0])
        self.assertIn("sharepoint 401 unauthorized", audit_status.error_traceback)
        self.assertNotIn("wrapped at period level", audit_status.error_traceback)
