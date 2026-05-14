"""
Tests for lex.api.views.model_entries.Many.ManyModelEntries
===========================================================

ManyModelEntries provides bulk GET / PATCH / DELETE on multiple records at once.
Its key behaviour is **per-object permission checking** — every entry in the
filtered queryset is individually validated via ``check_object_permissions``.

Tests verify:
- Bulk read returns serialised data for all matching objects
- Bulk patch validates, saves, and returns list of PKs
- Bulk delete calls perform_bulk_destroy and returns deleted IDs
- Permission enforcement iterates **every** object in the queryset
- PrimaryKeyListFilterBackend integration
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings")
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied

from lex.api.views.model_entries.Many import ManyModelEntries


# ── helpers ──────────────────────────────────────────────────────────────────

def _build_view(*, kwargs=None, request=None):
    view = ManyModelEntries()
    view.kwargs = kwargs or {}
    view.request = request or MagicMock()
    view.format_kwarg = None
    return view


def _mock_model_container(*, model_class=None, pk_name="id"):
    mc = MagicMock()
    mc.model_class = model_class or MagicMock
    mc.pk_name = pk_name
    return mc


def _make_request(*, data=None, user=None, query_params=None):
    req = MagicMock()
    req.data = data or {}
    req.user = user or MagicMock()
    req.query_params = query_params or {}
    return req


def _make_entries(n=3):
    """Create n fake model instances with sequential ids."""
    return [SimpleNamespace(id=i, name=f"entry-{i}") for i in range(1, n + 1)]


# ═══════════════════════════════════════════════════════════════════════════
# 1. get_filtered_query_set — object-level permission enforcement
# ═══════════════════════════════════════════════════════════════════════════
class GetFilteredQuerySetTests(SimpleTestCase):
    """Ensure check_object_permissions is called for every entry."""

    @patch.object(ManyModelEntries, "check_object_permissions")
    @patch.object(ManyModelEntries, "filter_queryset")
    @patch.object(ManyModelEntries, "get_queryset")
    def test_permissions_checked_for_every_entry(self, mock_qs, mock_filter, mock_check):
        """Each object must be validated individually — this is the security contract."""
        entries = _make_entries(4)
        mock_filter.return_value = entries
        request = _make_request()
        view = _build_view(request=request)

        result = view.get_filtered_query_set()

        self.assertEqual(mock_check.call_count, 4)
        for entry in entries:
            mock_check.assert_any_call(request, entry)
        self.assertEqual(result, entries)

    @patch.object(ManyModelEntries, "check_object_permissions", side_effect=PermissionDenied("denied"))
    @patch.object(ManyModelEntries, "filter_queryset")
    @patch.object(ManyModelEntries, "get_queryset")
    def test_permission_denied_propagates(self, mock_qs, mock_filter, mock_check):
        """If any entry fails permission check, the exception must propagate."""
        mock_filter.return_value = _make_entries(2)
        view = _build_view(request=_make_request())

        with self.assertRaises(PermissionDenied):
            view.get_filtered_query_set()

    @patch.object(ManyModelEntries, "check_object_permissions")
    @patch.object(ManyModelEntries, "filter_queryset")
    @patch.object(ManyModelEntries, "get_queryset")
    def test_empty_queryset_no_permission_calls(self, mock_qs, mock_filter, mock_check):
        """Empty queryset → zero permission checks (no iteration)."""
        mock_filter.return_value = []
        view = _build_view(request=_make_request())
        result = view.get_filtered_query_set()
        mock_check.assert_not_called()
        self.assertEqual(result, [])

    @patch.object(ManyModelEntries, "check_object_permissions")
    @patch.object(ManyModelEntries, "filter_queryset")
    @patch.object(ManyModelEntries, "get_queryset")
    def test_filter_queryset_receives_raw_queryset(self, mock_qs, mock_filter, mock_check):
        """filter_queryset is called with the result of get_queryset (filter backend integration)."""
        raw_qs = MagicMock()
        mock_qs.return_value = raw_qs
        mock_filter.return_value = []
        view = _build_view(request=_make_request())

        view.get_filtered_query_set()
        mock_filter.assert_called_once_with(raw_qs)


# ═══════════════════════════════════════════════════════════════════════════
# 2. GET — bulk read
# ═══════════════════════════════════════════════════════════════════════════
class BulkGetTests(SimpleTestCase):
    """GET returns serialised data for the filtered queryset."""

    @patch.object(ManyModelEntries, "get_serializer")
    @patch.object(ManyModelEntries, "get_filtered_query_set")
    def test_get_returns_serialized_list(self, mock_fqs, mock_ser):
        entries = _make_entries(3)
        mock_fqs.return_value = entries
        serializer = MagicMock()
        serializer.data = [{"id": 1}, {"id": 2}, {"id": 3}]
        mock_ser.return_value = serializer

        view = _build_view(request=_make_request())
        resp = view.get(view.request)

        mock_ser.assert_called_once_with(entries, many=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 3)

    @patch.object(ManyModelEntries, "get_serializer")
    @patch.object(ManyModelEntries, "get_filtered_query_set")
    def test_get_empty_queryset_returns_empty_list(self, mock_fqs, mock_ser):
        mock_fqs.return_value = []
        serializer = MagicMock()
        serializer.data = []
        mock_ser.return_value = serializer

        view = _build_view(request=_make_request())
        resp = view.get(view.request)
        self.assertEqual(resp.data, [])


# ═══════════════════════════════════════════════════════════════════════════
# 3. PATCH — bulk update
# ═══════════════════════════════════════════════════════════════════════════
class BulkPatchTests(SimpleTestCase):
    """PATCH validates, bulk-updates, and returns list of PKs."""

    @patch.object(ManyModelEntries, "perform_bulk_update")
    @patch.object(ManyModelEntries, "get_serializer")
    @patch.object(ManyModelEntries, "get_filtered_query_set")
    def test_patch_returns_list_of_pks(self, mock_fqs, mock_ser, mock_bulk):
        entries = _make_entries(2)
        mock_fqs.return_value = entries

        serializer = MagicMock()
        serializer.is_valid.return_value = True
        serializer.data = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        mock_ser.return_value = serializer

        mc = _mock_model_container(pk_name="id")
        request = _make_request(data=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
        view = _build_view(kwargs={"model_container": mc}, request=request)

        resp = view.patch(request)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [1, 2])

    @patch.object(ManyModelEntries, "perform_bulk_update")
    @patch.object(ManyModelEntries, "get_serializer")
    @patch.object(ManyModelEntries, "get_filtered_query_set")
    def test_patch_creates_serializer_with_partial_true(self, mock_fqs, mock_ser, mock_bulk):
        """PATCH always uses partial=True for partial updates."""
        mock_fqs.return_value = _make_entries(1)
        serializer = MagicMock()
        serializer.is_valid.return_value = True
        serializer.data = [{"id": 1}]
        mock_ser.return_value = serializer

        mc = _mock_model_container(pk_name="id")
        request = _make_request(data=[{"name": "updated"}])
        view = _build_view(kwargs={"model_container": mc}, request=request)

        view.patch(request)
        _, kwargs = mock_ser.call_args
        self.assertTrue(kwargs["partial"])
        self.assertTrue(kwargs["many"])

    @patch.object(ManyModelEntries, "get_serializer")
    @patch.object(ManyModelEntries, "get_filtered_query_set")
    def test_patch_invalid_data_raises(self, mock_fqs, mock_ser):
        """If serializer validation fails (raise_exception=True), error propagates."""
        from rest_framework.exceptions import ValidationError

        mock_fqs.return_value = _make_entries(1)
        serializer = MagicMock()
        serializer.is_valid.side_effect = ValidationError({"name": "required"})
        mock_ser.return_value = serializer

        mc = _mock_model_container()
        request = _make_request(data=[{}])
        view = _build_view(kwargs={"model_container": mc}, request=request)

        with self.assertRaises(ValidationError):
            view.patch(request)

    @patch.object(ManyModelEntries, "perform_bulk_update")
    @patch.object(ManyModelEntries, "get_serializer")
    @patch.object(ManyModelEntries, "get_filtered_query_set")
    def test_patch_calls_perform_bulk_update(self, mock_fqs, mock_ser, mock_bulk):
        """perform_bulk_update (from BulkAuditLogMixin) must be called with the validated serializer."""
        mock_fqs.return_value = _make_entries(1)
        serializer = MagicMock()
        serializer.is_valid.return_value = True
        serializer.data = [{"id": 1}]
        mock_ser.return_value = serializer

        mc = _mock_model_container(pk_name="id")
        view = _build_view(kwargs={"model_container": mc}, request=_make_request())
        view.patch(view.request)

        mock_bulk.assert_called_once_with(serializer)


# ═══════════════════════════════════════════════════════════════════════════
# 4. DELETE — bulk delete
# ═══════════════════════════════════════════════════════════════════════════
class BulkDeleteTests(SimpleTestCase):
    """DELETE bulk-deletes and returns IDs of deleted records."""

    @patch.object(ManyModelEntries, "perform_bulk_destroy")
    @patch.object(ManyModelEntries, "get_filtered_query_set")
    def test_delete_returns_deleted_ids(self, mock_fqs, mock_bulk):
        entries = _make_entries(3)
        mock_fqs.return_value = entries
        mock_bulk.return_value = [1, 2, 3]

        view = _build_view(request=_make_request())
        resp = view.delete(view.request)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [1, 2, 3])

    @patch.object(ManyModelEntries, "perform_bulk_destroy")
    @patch.object(ManyModelEntries, "get_filtered_query_set")
    def test_delete_passes_queryset_to_bulk_destroy(self, mock_fqs, mock_bulk):
        entries = _make_entries(2)
        mock_fqs.return_value = entries
        mock_bulk.return_value = [1, 2]

        view = _build_view(request=_make_request())
        view.delete(view.request)

        mock_bulk.assert_called_once_with(entries)

    @patch.object(ManyModelEntries, "perform_bulk_destroy")
    @patch.object(ManyModelEntries, "get_filtered_query_set")
    def test_delete_empty_queryset_returns_empty_list(self, mock_fqs, mock_bulk):
        mock_fqs.return_value = []
        mock_bulk.return_value = []

        view = _build_view(request=_make_request())
        resp = view.delete(view.request)
        self.assertEqual(resp.data, [])


# ═══════════════════════════════════════════════════════════════════════════
# 5. Permission class configuration
# ═══════════════════════════════════════════════════════════════════════════
class PermissionConfigTests(SimpleTestCase):
    """Verify that ManyModelEntries inherits the correct permission setup."""

    def test_filter_backends_includes_pk_list_filter(self):
        """PrimaryKeyListFilterBackend must be present for ?ids= filtering."""
        from lex.api.views.model_entries.filter_backends import PrimaryKeyListFilterBackend
        self.assertIn(PrimaryKeyListFilterBackend, ManyModelEntries.filter_backends)

    def test_inherits_from_bulk_audit_log_mixin(self):
        """BulkAuditLogMixin provides perform_bulk_update and perform_bulk_destroy."""
        from lex.audit_logging.mixins.BulkAuditLogMixin import BulkAuditLogMixin
        self.assertTrue(issubclass(ManyModelEntries, BulkAuditLogMixin))

    def test_inherits_from_model_entry_provider_mixin(self):
        """ModelEntryProviderMixin provides get_queryset and get_serializer_class."""
        from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import ModelEntryProviderMixin
        self.assertTrue(issubclass(ManyModelEntries, ModelEntryProviderMixin))


if __name__ == "__main__":
    unittest.main()
