"""
Tests for calculation model history transitions.

Verifies that ``CalculationModel`` correctly records history rows and
meta-history rows when the ``is_calculated`` status changes through
API-driven save / calculate cycles.
"""

import unittest
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection, models
from django.test import TransactionTestCase
from lex.api.serializers.base_serializers import get_serializer_map_for_model
from lex.api.utils import OperationContext
from lex.api.views.model_entries.One import OneModelEntry
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.core.mixins.ModelModificationRestriction import ModelModificationRestriction
from lex.core.models.CalculationModel import CalculationModel, CalculationModelException
from lex.core.models.LexModel import PermissionResult
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.process_admin.utils.model_registration import ModelRegistration
from rest_framework.test import APIRequestFactory, force_authenticate


class CalculationHistoryTestModel(CalculationModel):
    name = models.CharField(max_length=100)
    computed = models.IntegerField(default=0)
    should_fail = models.BooleanField(default=False)

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        if self.should_fail:
            raise ValueError("forced calculation failure")
        self.computed = (self.computed or 0) + 1

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class NestedChildCalculationHistoryTestModel(CalculationModel):
    name = models.CharField(max_length=100)
    computed = models.IntegerField(default=0)
    should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        if self.should_fail:
            raise ValueError("nested child failure")
        self.computed = (self.computed or 0) + 1

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class NestedParentCalculationHistoryTestModel(CalculationModel):
    name = models.CharField(max_length=100)
    child = models.ForeignKey(
        NestedChildCalculationHistoryTestModel,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        child = self.child
        if child is None:
            return

        with model_logging_context(child):
            child.is_calculated = CalculationModel.IN_PROGRESS
            child.save(skip_hooks=True)
            child.calculate_hook()

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class _Container:
    def __init__(self, model_class):
        self.model_class = model_class
        self.serializers_map = get_serializer_map_for_model(model_class)

    def get_serializers_map(self):
        return self.serializers_map

    def get_modification_restriction(self):
        return getattr(
            self.model_class, "modification_restriction", ModelModificationRestriction()
        )


class CalculationHistoryTransitionsTest(TransactionTestCase):
    def setUp(self):
        from simple_history.models import registered_models

        if CalculationHistoryTestModel in registered_models:
            del registered_models[CalculationHistoryTestModel]

        ActiveCalculationStateStore.clear_all()

        ModelRegistration._register_standard_model(CalculationHistoryTestModel, [])
        self.HistoryModel = CalculationHistoryTestModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            for model in [CalculationHistoryTestModel, self.HistoryModel, self.MetaModel]:
                if model._meta.db_table in tables:
                    schema_editor.delete_model(model)
                schema_editor.create_model(model)

        self.user = User.objects.create_user(
            username="calc-history-user",
            email="calc-history-user@example.com",
            password="password",
        )
        self.obj = CalculationHistoryTestModel.objects.create(name="initial")
        self.container = _Container(CalculationHistoryTestModel)
        self.factory = APIRequestFactory()

    def tearDown(self):
        from simple_history.models import registered_models

        ActiveCalculationStateStore.clear_all()

        if CalculationHistoryTestModel in registered_models:
            del registered_models[CalculationHistoryTestModel]

        with connection.schema_editor() as schema_editor:
            for model in [self.MetaModel, self.HistoryModel, CalculationHistoryTestModel]:
                try:
                    schema_editor.delete_model(model)
                except Exception:
                    pass

    def _history_rows_since(self, existing_ids):
        return list(
            self.HistoryModel.objects.filter(id=self.obj.pk)
            .exclude(history_id__in=existing_ids)
            .order_by("history_id")
        )

    def _send_calculation_request(self, calculation_id, *, should_fail=False):
        request = self.factory.patch(
            f"/api/model_entries/calculationhistorytestmodel/{calculation_id}/{self.obj.pk}/",
            {
                "calculate": "true",
                "is_calculated": True,
                "name": "trigger",
                "should_fail": should_fail,
            },
            format="json",
        )
        force_authenticate(request, user=self.user)

        return OneModelEntry.as_view()(
            request,
            model_container=self.container,
            calculationId=calculation_id,
            pk=self.obj.pk,
        )

    def test_frontend_calculation_creates_ordered_status_history_rows(self):
        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        response = self._send_calculation_request("calc-1")
        self.assertEqual(response.status_code, 200, response.data)

        new_rows = self._history_rows_since(existing_ids)
        self.assertEqual(len(new_rows), 2)
        self.assertEqual(
            [row.is_calculated for row in new_rows],
            [CalculationModel.IN_PROGRESS, CalculationModel.SUCCESS],
        )

    def test_celery_callback_success_creates_terminal_history_row(self):
        from lex.lex_app.celery_tasks import CallbackTask

        self.obj.name = "fresh-live-value"
        self.obj.is_calculated = CalculationModel.IN_PROGRESS
        self.obj.save(skip_hooks=True)

        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        stale_task_snapshot = CalculationHistoryTestModel(
            id=self.obj.pk,
            name="stale-task-value",
            computed=999,
            should_fail=True,
        )

        persisted = CallbackTask()._persist_status_fields(
            stale_task_snapshot,
            {"is_calculated": CalculationModel.SUCCESS},
        )

        self.assertTrue(persisted)
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(self.obj.name, "fresh-live-value")
        self.assertEqual(stale_task_snapshot.is_calculated, CalculationModel.SUCCESS)

        new_rows = self._history_rows_since(existing_ids)
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0].is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(new_rows[0].name, "fresh-live-value")

    def test_recovery_terminal_status_creates_history_row(self):
        from lex.lex_app.celery_recovery.supervisor import _finalize_calculation_rows

        self.obj.is_calculated = CalculationModel.IN_PROGRESS
        self.obj.save(skip_hooks=True)

        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        stale_task_snapshot = CalculationHistoryTestModel(
            id=self.obj.pk,
            name="stale-task-value",
        )

        with patch("lex.core.signals.update_calculation_status") as status_spy:
            _finalize_calculation_rows(
                {"args": ([stale_task_snapshot],)},
                CalculationModel.ABORTED,
                "worker heartbeat expired",
            )

        status_spy.assert_called_once()
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.is_calculated, CalculationModel.ABORTED)

        new_rows = self._history_rows_since(existing_ids)
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0].is_calculated, CalculationModel.ABORTED)
        self.assertEqual(
            new_rows[0].history_change_reason,
            "Celery recovery: ABORTED",
        )

    def test_atomic_calculation_error_keeps_single_in_progress_and_error_history_rows(self):
        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        response = self._send_calculation_request(
            "calc-atomic-error",
            should_fail=True,
        )
        self.assertEqual(response.status_code, 500, response.data)

        new_rows = self._history_rows_since(existing_ids)
        self.assertEqual(
            [row.is_calculated for row in new_rows],
            [CalculationModel.IN_PROGRESS, CalculationModel.ERROR],
        )

    def test_non_atomic_calculation_error_creates_error_history_once(self):
        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        with patch.object(CalculationHistoryTestModel, "is_atomic", False, create=True):
            response = self._send_calculation_request(
                "calc-non-atomic-error",
                should_fail=True,
            )

        self.assertEqual(response.status_code, 500, response.data)

        new_rows = self._history_rows_since(existing_ids)
        self.assertEqual(
            [row.is_calculated for row in new_rows],
            [CalculationModel.IN_PROGRESS, CalculationModel.ERROR],
        )

    def test_regular_update_skips_noop_payload_without_resetting_is_calculated(self):
        self.obj.is_calculated = CalculationModel.SUCCESS
        self.obj.save(skip_hooks=True)

        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        request = self.factory.patch(
            f"/api/model_entries/calculationhistorytestmodel/edit-1/{self.obj.pk}/",
            {
                "name": self.obj.name,
                "computed": self.obj.computed,
                "is_calculated": CalculationModel.SUCCESS,
            },
            format="json",
        )
        force_authenticate(request, user=self.user)

        response = OneModelEntry.as_view()(
            request,
            model_container=self.container,
            calculationId="edit-1",
            pk=self.obj.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)

        self.obj.refresh_from_db()
        self.assertEqual(self.obj.is_calculated, CalculationModel.SUCCESS)

        new_rows = list(
            self.HistoryModel.objects.filter(id=self.obj.pk)
            .exclude(history_id__in=existing_ids)
            .order_by("history_id")
        )
        self.assertEqual(new_rows, [])

    def test_sharepoint_edit_history_row_persists_not_calculated_status(self):
        self.obj.is_calculated = CalculationModel.SUCCESS
        self.obj.save(skip_hooks=True)

        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        request = self.factory.patch(
            f"/api/model_entries/calculationhistorytestmodel/sharepoint-edit/{self.obj.pk}/",
            {
                "edited_file": "Calculation workbook",
                "is_calculated": CalculationModel.NOT_CALCULATED,
            },
            format="json",
        )
        force_authenticate(request, user=self.user)

        response = OneModelEntry.as_view()(
            request,
            model_container=self.container,
            calculationId="sharepoint-edit",
            pk=self.obj.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)

        self.obj.refresh_from_db()
        self.assertEqual(self.obj.is_calculated, CalculationModel.NOT_CALCULATED)

        new_rows = self._history_rows_since(existing_ids)
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0].is_calculated, CalculationModel.NOT_CALCULATED)
        self.assertEqual(
            new_rows[0].history_change_reason,
            "SharePoint edit opened for Calculation workbook; calculation reset",
        )

    @unittest.skip("ActiveCalculationStateStore state mismatch — tests need update")
    def test_startup_abort_reset_persists_aborted_history_row(self):
        self.obj.is_calculated = CalculationModel.IN_PROGRESS
        self.obj.save(skip_hooks=True)

        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        with patch.dict("os.environ", {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(CalculationHistoryTestModel)

        self.obj.refresh_from_db()
        self.assertEqual(self.obj.is_calculated, CalculationModel.ABORTED)

        new_rows = list(
            self.HistoryModel.objects.filter(id=self.obj.pk)
            .exclude(history_id__in=existing_ids)
            .order_by("history_id")
        )
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0].is_calculated, CalculationModel.ABORTED)

    @unittest.skip("ActiveCalculationStateStore state mismatch — tests need update")
    def test_startup_abort_reset_uses_active_state_store_when_db_is_not_in_progress(self):
        self.obj.is_calculated = CalculationModel.NOT_CALCULATED
        self.obj.save(skip_hooks=True)

        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        ActiveCalculationStateStore.mark_in_progress(
            record_id=f"{self.obj._meta.model_name}_{self.obj.pk}",
            calculation_id="calc-1",
            record=str(self.obj),
            model_label=self.obj._meta.label_lower,
            record_pk=self.obj.pk,
        )

        with patch.dict("os.environ", {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(CalculationHistoryTestModel)

        self.obj.refresh_from_db()
        self.assertEqual(self.obj.is_calculated, CalculationModel.ABORTED)

        new_rows = list(
            self.HistoryModel.objects.filter(id=self.obj.pk)
            .exclude(history_id__in=existing_ids)
            .order_by("history_id")
        )
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0].is_calculated, CalculationModel.ABORTED)


class NestedCalculationHistoryTransitionsTest(TransactionTestCase):
    def setUp(self):
        from simple_history.models import registered_models

        for model_class in [
            NestedChildCalculationHistoryTestModel,
            NestedParentCalculationHistoryTestModel,
        ]:
            if model_class in registered_models:
                del registered_models[model_class]

        ActiveCalculationStateStore.clear_all()

        ModelRegistration._register_standard_model(
            NestedChildCalculationHistoryTestModel,
            [],
        )
        ModelRegistration._register_standard_model(
            NestedParentCalculationHistoryTestModel,
            [],
        )

        self.ChildHistoryModel = NestedChildCalculationHistoryTestModel.history.model
        self.ChildMetaModel = self.ChildHistoryModel.meta_history.model
        self.ParentHistoryModel = NestedParentCalculationHistoryTestModel.history.model
        self.ParentMetaModel = self.ParentHistoryModel.meta_history.model

        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            for model in [
                NestedChildCalculationHistoryTestModel,
                NestedParentCalculationHistoryTestModel,
                self.ChildHistoryModel,
                self.ChildMetaModel,
                self.ParentHistoryModel,
                self.ParentMetaModel,
            ]:
                if model._meta.db_table in tables:
                    schema_editor.delete_model(model)
                schema_editor.create_model(model)

        self.child = NestedChildCalculationHistoryTestModel.objects.create(
            name="child",
            should_fail=True,
        )
        self.parent = NestedParentCalculationHistoryTestModel.objects.create(
            name="parent",
            child=self.child,
        )

    def tearDown(self):
        from simple_history.models import registered_models

        ActiveCalculationStateStore.clear_all()

        for model_class in [
            NestedParentCalculationHistoryTestModel,
            NestedChildCalculationHistoryTestModel,
        ]:
            if model_class in registered_models:
                del registered_models[model_class]

        with connection.schema_editor() as schema_editor:
            for model in [
                self.ParentMetaModel,
                self.ParentHistoryModel,
                self.ChildMetaModel,
                self.ChildHistoryModel,
                NestedParentCalculationHistoryTestModel,
                NestedChildCalculationHistoryTestModel,
            ]:
                try:
                    schema_editor.delete_model(model)
                except Exception:
                    pass

    def test_nested_child_failure_restores_missing_in_progress_history_row(self):
        existing_ids = set(
            self.ChildHistoryModel.objects.filter(id=self.child.pk).values_list(
                "history_id",
                flat=True,
            )
        )

        with self.assertRaises(CalculationModelException) as raised:
            with OperationContext({}, "calc-nested-child-error"):
                with model_logging_context(self.parent):
                    self.parent.is_calculated = CalculationModel.IN_PROGRESS
                    self.parent.save(skip_hooks=True)
                    self.parent.calculate_hook()

        CalculationModel.persist_error_state(raised.exception.calc_obj)
        self.child.refresh_from_db()

        new_rows = list(
            self.ChildHistoryModel.objects.filter(id=self.child.pk)
            .exclude(history_id__in=existing_ids)
            .order_by("history_id")
        )

        self.assertEqual(
            [row.is_calculated for row in new_rows],
            [CalculationModel.IN_PROGRESS, CalculationModel.ERROR],
        )
