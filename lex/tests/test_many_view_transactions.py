from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.api.views.model_entries.Many import ManyModelEntries


class ManyViewAuditFailureTests(SimpleTestCase):
    def _make_view(self):
        view = ManyModelEntries()
        view.kwargs = {"model_container": SimpleNamespace(pk_name="id")}
        return view

    def test_patch_logs_failed_bulk_audit_for_validation_errors(self):
        view = self._make_view()
        request = SimpleNamespace(data={"name": "Broken"})
        instance_one = SimpleNamespace(pk=1)
        instance_two = SimpleNamespace(pk=2)
        serializer = MagicMock()
        serializer.is_valid.side_effect = RuntimeError("validation failed")

        with patch.object(view, "get_filtered_query_set", return_value=[instance_one, instance_two]), patch.object(
            view,
            "get_serializer",
            return_value=serializer,
        ), patch(
            "lex.api.views.model_entries.Many.resolve_exception_traceback",
            return_value="bulk traceback",
        ), patch.object(
            view,
            "log_failed_bulk_changes",
        ) as log_failed_bulk_changes_mock:
            with self.assertRaises(RuntimeError):
                view.patch(request)

        log_failed_bulk_changes_mock.assert_called_once_with(
            "update",
            [instance_one, instance_two],
            payloads={"name": "Broken"},
            error_traceback="bulk traceback",
        )

    def test_patch_flushes_pending_bulk_failure_audits(self):
        view = self._make_view()
        request = SimpleNamespace(data={"name": "Broken"})
        instance = SimpleNamespace(pk=1)
        serializer = MagicMock()
        serializer.validated_data = [{"name": "Broken"}]
        serializer.is_valid.return_value = None

        with patch.object(view, "get_filtered_query_set", return_value=[instance]), patch.object(
            view,
            "get_serializer",
            return_value=serializer,
        ), patch.object(
            view,
            "perform_bulk_update",
            side_effect=RuntimeError("save failed"),
        ), patch.object(
            view,
            "flush_pending_failed_audit_logs",
            return_value=[MagicMock()],
        ) as flush_mock, patch.object(
            view,
            "log_failed_bulk_changes",
        ) as log_failed_bulk_changes_mock:
            with self.assertRaises(RuntimeError):
                view.patch(request)

        flush_mock.assert_called_once_with()
        log_failed_bulk_changes_mock.assert_not_called()

    def test_delete_logs_failed_bulk_audit(self):
        view = self._make_view()
        request = SimpleNamespace()
        instance_one = SimpleNamespace(pk=1)
        instance_two = SimpleNamespace(pk=2)

        with patch.object(view, "get_filtered_query_set", return_value=[instance_one, instance_two]), patch.object(
            view,
            "get_serializer",
            side_effect=lambda instance: MagicMock(data={"id": instance.pk}),
        ), patch.object(
            view,
            "perform_bulk_destroy",
            side_effect=RuntimeError("delete failed"),
        ), patch(
            "lex.api.views.model_entries.Many.resolve_exception_traceback",
            return_value="delete traceback",
        ), patch.object(
            view,
            "log_failed_bulk_changes",
        ) as log_failed_bulk_changes_mock:
            with self.assertRaises(RuntimeError):
                view.delete(request)

        log_failed_bulk_changes_mock.assert_called_once_with(
            "delete",
            [instance_one, instance_two],
            payloads=[{"id": 1}, {"id": 2}],
            error_traceback="delete traceback",
        )