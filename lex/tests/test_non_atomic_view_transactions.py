from contextlib import nullcontext
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import TemporaryUploadedFile
from django.http import QueryDict

from lex.api.views.model_entries.One import OneModelEntry
from lex.api.views.process_flow.CreateOrUpdate import CreateOrUpdate
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import should_use_atomic_model_operations


class AtomicModel:
    pass


class NonAtomicModel:
    is_atomic = False


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
