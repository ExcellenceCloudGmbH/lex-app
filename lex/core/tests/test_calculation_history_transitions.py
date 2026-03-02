from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection, models
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from lex.api.serializers.base_serializers import get_serializer_map_for_model
from lex.api.views.model_entries.One import OneModelEntry
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult
from lex.core.mixins.ModelModificationRestriction import ModelModificationRestriction
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.process_admin.utils.model_registration import ModelRegistration


class CalculationHistoryTestModel(CalculationModel):
    name = models.CharField(max_length=100)
    computed = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        self.computed = (self.computed or 0) + 1

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

    def test_frontend_calculation_creates_ordered_status_history_rows(self):
        existing_ids = set(
            self.HistoryModel.objects.filter(id=self.obj.pk).values_list(
                "history_id", flat=True
            )
        )

        request = self.factory.patch(
            "/api/model_entries/calculationhistorytestmodel/calc-1/1/",
            {"calculate": "true", "is_calculated": True, "name": "trigger"},
            format="json",
        )
        force_authenticate(request, user=self.user)

        response = OneModelEntry.as_view()(
            request,
            model_container=self.container,
            calculationId="calc-1",
            pk=self.obj.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)

        new_rows = list(
            self.HistoryModel.objects.filter(id=self.obj.pk)
            .exclude(history_id__in=existing_ids)
            .order_by("history_id")
        )
        self.assertEqual(len(new_rows), 2)
        self.assertEqual(
            [row.is_calculated for row in new_rows],
            [CalculationModel.IN_PROGRESS, CalculationModel.SUCCESS],
        )

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
