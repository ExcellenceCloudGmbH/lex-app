from contextlib import nullcontext
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import TemporaryUploadedFile
from django.http import QueryDict
from rest_framework.exceptions import APIException

from lex.api.views.model_entries.One import OneModelEntry
from lex.audit_logging.mixins.AuditLogMixin import AuditLogMixin
from lex.api.views.process_flow.CreateOrUpdate import CreateOrUpdate
from lex.core.models.CalculationModel import CalculationModel, CalculationModelException
from lex.core.models.LexModel import should_use_atomic_model_operations


class AtomicModel:
    pass


class NonAtomicModel:
    is_atomic = False


class ConcreteCalculationModel(CalculationModel):
    class Meta:
        app_label = "lex"


class AtomicViewTransactionTests(TestCase):
    def test_atomic_helper_matches_legacy_flag_semantics(self):
        self.assertTrue(should_use_atomic_model_operations(AtomicModel))
        self.assertTrue(should_use_atomic_model_operations(AtomicModel()))
        self.assertFalse(should_use_atomic_model_operations(NonAtomicModel))
        self.assertFalse(should_use_atomic_model_operations(NonAtomicModel()))

    def test_one_create_skips_outer_atomic_for_non_atomic_models(self):
        view = OneModelEntry()
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=NonAtomicModel),
            "calculationId": "calc-1",
        }
        request = SimpleNamespace(data={})
        atomic_mock = Mock(return_value=nullcontext())

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.transaction.atomic",
            atomic_mock,
        ), patch(
            "lex.api.views.model_entries.One.CreateModelMixin.create",
            return_value="created",
        ):
            response = view.create(request)

        self.assertEqual(response, "created")
        atomic_mock.assert_not_called()

    def test_one_create_keeps_outer_atomic_for_atomic_models(self):
        view = OneModelEntry()
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=AtomicModel),
            "calculationId": "calc-2",
        }
        request = SimpleNamespace(data={})
        atomic_mock = Mock(return_value=nullcontext())

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.transaction.atomic",
            atomic_mock,
        ), patch(
            "lex.api.views.model_entries.One.CreateModelMixin.create",
            return_value="created",
        ):
            response = view.create(request)

        self.assertEqual(response, "created")
        atomic_mock.assert_called_once_with()

    def test_one_create_logs_failed_audit_for_atomic_models(self):
        view = OneModelEntry()
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=AtomicModel),
            "calculationId": "calc-create-atomic",
        }
        request = SimpleNamespace(data={"name": "Broken"})

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.transaction.atomic",
            return_value=nullcontext(),
        ), patch(
            "lex.api.views.model_entries.One.CreateModelMixin.create",
            side_effect=RuntimeError("create failed"),
        ), patch(
            "lex.api.views.model_entries.One.resolve_exception_traceback",
            return_value="create traceback",
        ), patch.object(
            view,
            "log_failed_change",
        ) as log_failed_change_mock:
            with self.assertRaises(APIException):
                view.create(request)

        log_failed_change_mock.assert_called_once_with(
            "create",
            AtomicModel,
            payload={"name": "Broken"},
            error_traceback="create traceback",
            related_instance=None,
        )

    def test_one_create_logs_failed_audit_for_non_atomic_models(self):
        view = OneModelEntry()
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=NonAtomicModel),
            "calculationId": "calc-create-non-atomic",
        }
        request = SimpleNamespace(data={"name": "Broken"})

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.CreateModelMixin.create",
            side_effect=RuntimeError("create failed"),
        ), patch(
            "lex.api.views.model_entries.One.resolve_exception_traceback",
            return_value="create traceback",
        ), patch.object(
            view,
            "log_failed_change",
        ) as log_failed_change_mock:
            with self.assertRaises(APIException):
                view.create(request)

        log_failed_change_mock.assert_called_once_with(
            "create",
            NonAtomicModel,
            payload={"name": "Broken"},
            error_traceback="create traceback",
            related_instance=None,
        )

    def test_prepare_update_request_handles_uploaded_files_without_deepcopy(self):
        view = OneModelEntry()
        upload = TemporaryUploadedFile("payload.txt", "text/plain", 3, "utf-8")
        upload.write(b"abc")
        upload.seek(0)
        request_data = QueryDict("", mutable=True)
        request_data["calculate"] = "true"
        request_data.setlist("attachment", [upload])
        request = SimpleNamespace(data=request_data)

        try:
            prepared_request = view._prepare_update_request(
                request,
                reset_is_calculated=True,
            )

            self.assertIs(prepared_request, request)
            self.assertEqual(request.data["calculate"], "true")
            self.assertNotIn("calculate", prepared_request._data)
            self.assertEqual(
                prepared_request._data["is_calculated"],
                CalculationModel.NOT_CALCULATED,
            )
            self.assertIs(prepared_request._data.getlist("attachment")[0], upload)
            self.assertIs(prepared_request._full_data, prepared_request._data)
        finally:
            upload.close()

    def test_sharepoint_edit_update_passes_skip_history_to_reset(self):
        view = OneModelEntry()
        instance = ConcreteCalculationModel()
        instance.pk = 123
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=ConcreteCalculationModel),
            "calculationId": "calc-sharepoint",
        }
        view.get_object = Mock(return_value=instance)
        request_data = QueryDict("", mutable=True)
        request_data["edited_file"] = "Document"
        request = SimpleNamespace(data=request_data)
        response = object()
        reset_kwargs = {}

        def fake_reset(response_obj, **kwargs):
            reset_kwargs.update(kwargs)
            return response_obj

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.model_logging_context",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.UpdateModelMixin.update",
            return_value=response,
        ) as update_mock, patch.object(
            view,
            "_reset_instance_is_calculated",
            side_effect=fake_reset,
        ) as reset_mock:
            result = view.update(request)

        self.assertIs(result, response)
        update_mock.assert_called_once()
        reset_mock.assert_called_once()
        self.assertTrue(reset_kwargs["skip_history"])
        self.assertIn("SharePoint edit opened for Document", reset_kwargs["history_change_reason"])

    def test_update_skips_noop_payload_before_model_update(self):
        view = OneModelEntry()
        instance = SimpleNamespace(
            pk=17,
            name="Stable",
            _meta=SimpleNamespace(model_name="atomicmodel", label_lower="lex.atomicmodel"),
        )
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=AtomicModel),
            "calculationId": "calc-update-noop",
        }
        view.get_object = Mock(return_value=instance)
        request = SimpleNamespace(data={"name": "Stable"})

        validation_serializer = Mock()
        validation_serializer.instance = instance
        validation_serializer.validated_data = {"name": "Stable"}
        validation_serializer.is_valid.return_value = True

        response_serializer = Mock()
        response_serializer.data = {"id": 17, "name": "Stable"}

        view.get_serializer = Mock(side_effect=[validation_serializer, response_serializer])

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.model_logging_context",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.UpdateModelMixin.update",
        ) as update_mock:
            response = view.update(request, partial=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"id": 17, "name": "Stable"})
        update_mock.assert_not_called()

    def test_perform_update_sets_sharepoint_history_change_reason(self):
        view = OneModelEntry()
        instance = ConcreteCalculationModel()
        instance.is_calculated = CalculationModel.SUCCESS
        serializer = Mock()
        serializer.instance = instance
        request_data = QueryDict("", mutable=True)
        request_data["edited_file"] = "Document"
        view.request = SimpleNamespace(data=request_data)
        view._calculate_requested = False

        with patch.object(AuditLogMixin, "perform_update", return_value="updated") as perform_update_mock:
            result = view.perform_update(serializer)

        self.assertEqual(result, "updated")
        self.assertEqual(instance.is_calculated, CalculationModel.NOT_CALCULATED)
        self.assertEqual(
            instance._history_change_reason,
            "SharePoint edit opened for Document; calculation reset",
        )
        perform_update_mock.assert_called_once_with(serializer)

    def test_reset_instance_is_calculated_can_skip_history_and_preserve_reason(self):
        view = OneModelEntry()
        instance = ConcreteCalculationModel()
        instance.is_calculated = CalculationModel.SUCCESS
        instance.save = Mock()
        instance.save_without_historical_record = Mock()
        view.get_object = Mock(return_value=instance)
        response = SimpleNamespace(data={})

        result = view._reset_instance_is_calculated(
            response,
            skip_history=True,
            history_change_reason="SharePoint edit opened for Document; calculation reset",
        )

        self.assertIs(result, response)
        self.assertEqual(instance.is_calculated, CalculationModel.NOT_CALCULATED)
        self.assertEqual(
            instance._history_change_reason,
            "SharePoint edit opened for Document; calculation reset",
        )
        instance.save_without_historical_record.assert_called_once_with(skip_hooks=True)
        instance.save.assert_not_called()
        self.assertEqual(response.data["is_calculated"], CalculationModel.NOT_CALCULATED)

    def test_one_update_logs_failed_audit_for_atomic_models(self):
        view = OneModelEntry()
        instance = AtomicModel()
        instance.pk = 17
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=AtomicModel),
            "calculationId": "calc-update-atomic",
        }
        view.get_object = Mock(return_value=instance)
        request = SimpleNamespace(data={"name": "Broken"})

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.model_logging_context",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.UpdateModelMixin.update",
            side_effect=RuntimeError("update failed"),
        ), patch(
            "lex.api.views.model_entries.One.resolve_exception_traceback",
            return_value="update traceback",
        ), patch.object(
            view,
            "log_failed_change",
        ) as log_failed_change_mock:
            with self.assertRaises(APIException):
                view.update(request)

        log_failed_change_mock.assert_called_once_with(
            "update",
            AtomicModel,
            payload={"name": "Broken"},
            error_traceback="update traceback",
            related_instance=instance,
        )

    def test_one_update_logs_failed_audit_for_non_atomic_models(self):
        view = OneModelEntry()
        instance = NonAtomicModel()
        instance.pk = 23
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=NonAtomicModel),
            "calculationId": "calc-update-non-atomic",
        }
        view.get_object = Mock(return_value=instance)
        request = SimpleNamespace(data={"name": "Broken"})

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.model_logging_context",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.UpdateModelMixin.update",
            side_effect=RuntimeError("update failed"),
        ), patch(
            "lex.api.views.model_entries.One.resolve_exception_traceback",
            return_value="update traceback",
        ), patch.object(
            view,
            "log_failed_change",
        ) as log_failed_change_mock:
            with self.assertRaises(APIException):
                view.update(request)

        log_failed_change_mock.assert_called_once_with(
            "update",
            NonAtomicModel,
            payload={"name": "Broken"},
            error_traceback="update traceback",
            related_instance=instance,
        )

    def test_one_update_logs_failed_audit_for_calculation_model_exception(self):
        view = OneModelEntry()
        instance = ConcreteCalculationModel()
        instance.pk = 31
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=ConcreteCalculationModel),
            "calculationId": "calc-update-calc-exc",
        }
        view.get_object = Mock(return_value=instance)
        request = SimpleNamespace(data={"name": "Broken"})
        calculation_exception = CalculationModelException(
            calc_obj=instance,
            exception_details="calc failed",
            stack_trace="calc traceback",
        )

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.model_logging_context",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.UpdateModelMixin.update",
            side_effect=calculation_exception,
        ), patch(
            "lex.api.views.model_entries.One.resolve_exception_traceback",
            return_value="resolved calc traceback",
        ), patch(
            "lex.api.views.model_entries.One.resolve_exception_detail",
            return_value="resolved calc detail",
        ), patch(
            "lex.api.views.model_entries.One.CalculationModel.persist_error_state",
        ) as persist_error_state_mock, patch.object(
            view,
            "log_failed_change",
        ) as log_failed_change_mock:
            with self.assertRaises(APIException):
                view.update(request)

        persist_error_state_mock.assert_called_once_with(instance)
        log_failed_change_mock.assert_called_once_with(
            "update",
            ConcreteCalculationModel,
            payload={"name": "Broken"},
            error_traceback="resolved calc traceback",
            related_instance=instance,
        )

    def test_one_destroy_logs_failed_audit_for_atomic_models(self):
        view = OneModelEntry()
        instance = AtomicModel()
        instance.pk = 41
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=AtomicModel),
            "calculationId": "calc-destroy-atomic",
        }
        view.get_object = Mock(return_value=instance)
        view.get_serializer = Mock(return_value=Mock(data={"id": 41}))
        request = SimpleNamespace()

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.model_logging_context",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin.DestroyOneWithPayloadMixin.destroy",
            side_effect=RuntimeError("delete failed"),
        ), patch(
            "lex.api.views.model_entries.One.resolve_exception_traceback",
            return_value="delete traceback",
        ), patch.object(
            view,
            "log_failed_change",
        ) as log_failed_change_mock:
            with self.assertRaises(APIException):
                view.destroy(request)

        log_failed_change_mock.assert_called_once_with(
            "delete",
            instance,
            payload={"id": 41},
            error_traceback="delete traceback",
            related_instance=instance,
        )

    def test_one_destroy_logs_failed_audit_for_non_atomic_models(self):
        view = OneModelEntry()
        instance = NonAtomicModel()
        instance.pk = 42
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=NonAtomicModel),
            "calculationId": "calc-destroy-non-atomic",
        }
        view.get_object = Mock(return_value=instance)
        view.get_serializer = Mock(return_value=Mock(data={"id": 42}))
        request = SimpleNamespace()

        with patch(
            "lex.api.views.model_entries.One.OperationContext",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.One.model_logging_context",
            side_effect=lambda *args, **kwargs: nullcontext("ctx"),
        ), patch(
            "lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin.DestroyOneWithPayloadMixin.destroy",
            side_effect=RuntimeError("delete failed"),
        ), patch(
            "lex.api.views.model_entries.One.resolve_exception_traceback",
            return_value="delete traceback",
        ), patch.object(
            view,
            "log_failed_change",
        ) as log_failed_change_mock:
            with self.assertRaises(APIException):
                view.destroy(request)

        log_failed_change_mock.assert_called_once_with(
            "delete",
            instance,
            payload={"id": 42},
            error_traceback="delete traceback",
            related_instance=instance,
        )

    def test_process_flow_update_skips_outer_atomic_for_non_atomic_models(self):
        instance = NonAtomicModel()
        NonAtomicModel.objects = SimpleNamespace(
            filter=Mock(return_value=SimpleNamespace(first=Mock(return_value=instance)))
        )
        view = CreateOrUpdate()
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=NonAtomicModel),
            "pk": 1,
        }
        request = SimpleNamespace(data={})
        atomic_mock = Mock(return_value=nullcontext())

        with patch(
            "lex.api.views.process_flow.CreateOrUpdate.transaction.atomic",
            atomic_mock,
        ), patch(
            "lex.api.views.process_flow.CreateOrUpdate.UpdateModelMixin.update",
            return_value="updated",
        ), patch(
            "lex.api.views.process_flow.CreateOrUpdate.post_save.disconnect",
        ), patch(
            "lex.api.views.process_flow.CreateOrUpdate.post_save.connect",
        ):
            response = view.update(request)

        self.assertEqual(response, "updated")
        atomic_mock.assert_not_called()

    def test_process_flow_update_keeps_outer_atomic_for_atomic_models(self):
        instance = AtomicModel()
        AtomicModel.objects = SimpleNamespace(
            filter=Mock(return_value=SimpleNamespace(first=Mock(return_value=instance)))
        )
        view = CreateOrUpdate()
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=AtomicModel),
            "pk": 2,
        }
        request = SimpleNamespace(data={})
        atomic_mock = Mock(return_value=nullcontext())

        with patch(
            "lex.api.views.process_flow.CreateOrUpdate.transaction.atomic",
            atomic_mock,
        ), patch(
            "lex.api.views.process_flow.CreateOrUpdate.UpdateModelMixin.update",
            return_value="updated",
        ), patch(
            "lex.api.views.process_flow.CreateOrUpdate.post_save.disconnect",
        ), patch(
            "lex.api.views.process_flow.CreateOrUpdate.post_save.connect",
        ):
            response = view.update(request)

        self.assertEqual(response, "updated")
        atomic_mock.assert_called_once_with()
