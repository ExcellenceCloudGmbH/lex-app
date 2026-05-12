"""
Tests for ``lex.api.views.model_entries.filter_backends`` — AG Grid filter backends.

**What is tested:**

    * ``PrimaryKeyListFilterBackend`` — ids from query_params, base64 export decoding
    * ``UserReadRestrictionFilterBackend`` — model-name dispatch, AuditLogStatus/
      CalculationLog fast-path, default permission target resolution, Keycloak
      scope-based filtering (global read, id-restricted, no read)

**Why this matters:**

    These backends control which rows a user sees in every AG Grid view.  If the
    PK filter misparses ids, the frontend selection doesn't match the response.
    If the read-restriction backend skips the permission check, users see data
    they shouldn't.

**How to run:**

    .. code-block:: bash

        lex test lex.tests.test_filter_backends --verbosity=2 --noinput
"""

import base64
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.api.views.model_entries.filter_backends import (
    PrimaryKeyListFilterBackend,
    UserReadRestrictionFilterBackend,
)
from lex.core.models.LexModel import LexModel


# ─── helpers ──────────────────────────────────────────────────────────

def _mock_view(model_class=None, pk_name="pk"):
    """View stub with model_container in kwargs."""
    mc = MagicMock()
    mc.pk_name = pk_name
    mc.model_class = model_class or MagicMock()
    mc.model_class.__name__ = "TestModel"
    view = MagicMock()
    view.kwargs = {"model_container": mc}
    return view


def _mock_request(ids=None, user_permissions=None):
    """DRF request stub."""
    request = MagicMock()
    request.query_params = MagicMock()
    request.query_params.getlist.return_value = ids or []
    request.user_permissions = user_permissions
    return request


# ════════════════════════════════════════════════════════════════════════
#  PrimaryKeyListFilterBackend
# ════════════════════════════════════════════════════════════════════════

class TestPrimaryKeyListFilterBackendQueryParams(SimpleTestCase):
    """Test PK list filtering from query parameters."""

    def test_no_ids_returns_queryset_unchanged(self):
        backend = PrimaryKeyListFilterBackend()
        view = _mock_view(pk_name="id")
        request = _mock_request(ids=[])
        qs = MagicMock()

        result = backend.filter_queryset(request, qs, view)
        self.assertEqual(result, qs)
        qs.filter.assert_not_called()

    def test_ids_filter_applied(self):
        backend = PrimaryKeyListFilterBackend()
        view = _mock_view(pk_name="id")
        request = _mock_request(ids=["1", "2", "3"])
        qs = MagicMock()
        filtered_qs = MagicMock()
        qs.filter.return_value = filtered_qs

        result = backend.filter_queryset(request, qs, view)

        qs.filter.assert_called_once_with(id__in=["1", "2", "3"])
        self.assertEqual(result, filtered_qs)

    def test_empty_strings_filtered_out(self):
        """Empty string ids are removed before filtering."""
        backend = PrimaryKeyListFilterBackend()
        view = _mock_view(pk_name="pk")
        request = _mock_request(ids=["1", "", "3"])
        qs = MagicMock()
        qs.filter.return_value = MagicMock()

        backend.filter_queryset(request, qs, view)
        qs.filter.assert_called_once_with(pk__in=["1", "3"])


class TestPrimaryKeyListFilterBackendExport(SimpleTestCase):
    """Test PK list filtering from base64-encoded export data."""

    def test_filter_for_export_decodes_and_filters(self):
        backend = PrimaryKeyListFilterBackend()
        view = _mock_view(pk_name="id")

        # Encode "ids=10&ids=20" as base64
        raw = "ids=10&ids=20"
        encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        json_data = {"filtered_export": encoded}

        qs = MagicMock()
        filtered_qs = MagicMock()
        qs.filter.return_value = filtered_qs

        result = backend.filter_for_export(json_data, qs, view)

        qs.filter.assert_called_once_with(id__in=["10", "20"])
        self.assertEqual(result, filtered_qs)

    def test_filter_for_export_no_ids_returns_queryset(self):
        backend = PrimaryKeyListFilterBackend()
        view = _mock_view(pk_name="pk")

        raw = "other=value"
        encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        json_data = {"filtered_export": encoded}

        qs = MagicMock()
        result = backend.filter_for_export(json_data, qs, view)
        self.assertEqual(result, qs)


# ════════════════════════════════════════════════════════════════════════
#  UserReadRestrictionFilterBackend — dispatch
# ════════════════════════════════════════════════════════════════════════

class TestUserReadRestrictionDispatch(SimpleTestCase):
    """Test model-name dispatch in filter_queryset."""

    def test_auditlogstatus_passes_through(self):
        """AuditLogStatus → queryset returned unchanged."""
        backend = UserReadRestrictionFilterBackend()
        mc = MagicMock()
        mc.model_class.__name__ = "AuditLogStatus"
        view = MagicMock()
        view.kwargs = {"model_container": mc}
        qs = MagicMock()

        result = backend.filter_queryset(MagicMock(), qs, view)
        self.assertEqual(result, qs)

    def test_calculationlog_passes_through(self):
        """CalculationLog → queryset returned unchanged."""
        backend = UserReadRestrictionFilterBackend()
        mc = MagicMock()
        mc.model_class.__name__ = "CalculationLog"
        view = MagicMock()
        view.kwargs = {"model_container": mc}
        qs = MagicMock()

        result = backend.filter_queryset(MagicMock(), qs, view)
        self.assertEqual(result, qs)

    def test_auditlog_dispatches_to_handler(self):
        """AuditLog → _handle_auditlog called."""
        backend = UserReadRestrictionFilterBackend()
        mc = MagicMock()
        mc.model_class.__name__ = "AuditLog"
        view = MagicMock()
        view.kwargs = {"model_container": mc}

        with patch.object(backend, "_handle_auditlog", return_value="filtered") as mock_handler:
            result = backend.filter_queryset(MagicMock(), MagicMock(), view)
            mock_handler.assert_called_once()
            self.assertEqual(result, "filtered")

    def test_other_model_dispatches_to_default(self):
        """Regular model → _handle_lexmodel_default called."""
        backend = UserReadRestrictionFilterBackend()
        mc = MagicMock()
        mc.model_class.__name__ = "CashFlow"
        view = MagicMock()
        view.kwargs = {"model_container": mc}

        with patch.object(backend, "_handle_lexmodel_default", return_value="filtered") as mock_handler:
            result = backend.filter_queryset(MagicMock(), MagicMock(), view)
            mock_handler.assert_called_once()
            self.assertEqual(result, "filtered")


# ════════════════════════════════════════════════════════════════════════
#  _get_default_permission_target
# ════════════════════════════════════════════════════════════════════════

class TestGetDefaultPermissionTarget(SimpleTestCase):
    """Test fast-path permission target resolution."""

    def test_model_with_custom_permission_read_returns_none(self):
        """LexModel subclass that overrides permission_read → (None, None).

        The fast-path is only valid when permission_read is the default.
        """
        backend = UserReadRestrictionFilterBackend()

        class CustomPermModel(LexModel):
            class Meta:
                app_label = "core"
                managed = False

            def permission_read(self, user_context):
                return MagicMock(allowed=True)

        target, field = backend._get_default_permission_target(CustomPermModel)
        self.assertIsNone(target)

    def test_non_model_returns_none(self):
        """Non-model class without instance_type → (None, None)."""
        backend = UserReadRestrictionFilterBackend()

        class PlainClass:
            pass

        result = backend._get_default_permission_target(PlainClass)
        self.assertEqual(result, (None, None))

    def test_non_type_returns_none(self):
        """Instance (not a class) → (None, None) because isinstance(x, type) is False."""
        backend = UserReadRestrictionFilterBackend()
        result = backend._get_default_permission_target("not a class")
        self.assertEqual(result, (None, None))


# ════════════════════════════════════════════════════════════════════════
#  _apply_default_permission_read_filter
# ════════════════════════════════════════════════════════════════════════

class TestApplyDefaultPermissionReadFilter(SimpleTestCase):
    """Test Keycloak scope-based queryset filtering."""

    def _make_target(self, app_label="core", name="CashFlow", pk_name="id"):
        target = MagicMock()
        target._meta.app_label = app_label
        target.__name__ = name
        target._meta.pk = MagicMock()
        target._meta.pk.name = pk_name
        target._meta.pk.to_python = lambda self, x: int(x)
        return target

    def test_global_read_returns_full_queryset(self):
        """Permission with no resource_set_id = global read → full queryset."""
        backend = UserReadRestrictionFilterBackend()
        target = self._make_target()
        qs = MagicMock()
        request = _mock_request(user_permissions=[
            {"rsname": "core.CashFlow", "scopes": ["read"]}
        ])

        result = backend._apply_default_permission_read_filter(
            request, qs, target, "id"
        )
        self.assertEqual(result, qs)

    def test_no_matching_permissions_returns_empty(self):
        """No permissions for this resource → queryset.none()."""
        backend = UserReadRestrictionFilterBackend()
        target = self._make_target()
        qs = MagicMock()
        empty_qs = MagicMock()
        qs.none.return_value = empty_qs
        request = _mock_request(user_permissions=[
            {"rsname": "other.Model", "scopes": ["read"]}
        ])

        result = backend._apply_default_permission_read_filter(
            request, qs, target, "id"
        )
        self.assertEqual(result, empty_qs)

    def test_id_restricted_permission_filters_by_ids(self):
        """Permission with resource_set_id → filter by those ids only."""
        backend = UserReadRestrictionFilterBackend()
        target = self._make_target()
        qs = MagicMock()
        filtered_qs = MagicMock()
        qs.filter.return_value = filtered_qs
        request = _mock_request(user_permissions=[
            {"rsname": "core.CashFlow", "scopes": ["read"], "resource_set_id": "42"},
            {"rsname": "core.CashFlow", "scopes": ["read"], "resource_set_id": "99"},
        ])

        result = backend._apply_default_permission_read_filter(
            request, qs, target, "id"
        )
        self.assertEqual(result, filtered_qs)
        # Should filter by the two IDs
        call_kwargs = qs.filter.call_args[1]
        self.assertIn("id__in", call_kwargs)

    def test_read_scope_missing_skips_permission(self):
        """Permission without 'read' scope is ignored."""
        backend = UserReadRestrictionFilterBackend()
        target = self._make_target()
        qs = MagicMock()
        empty_qs = MagicMock()
        qs.none.return_value = empty_qs
        request = _mock_request(user_permissions=[
            {"rsname": "core.CashFlow", "scopes": ["edit"]}
        ])

        result = backend._apply_default_permission_read_filter(
            request, qs, target, "id"
        )
        self.assertEqual(result, empty_qs)

    def test_none_permissions_returns_empty(self):
        """user_permissions is None → empty queryset."""
        backend = UserReadRestrictionFilterBackend()
        target = self._make_target()
        qs = MagicMock()
        empty_qs = MagicMock()
        qs.none.return_value = empty_qs
        request = _mock_request(user_permissions=None)

        result = backend._apply_default_permission_read_filter(
            request, qs, target, "id"
        )
        self.assertEqual(result, empty_qs)

    def test_non_mapping_permission_ignored(self):
        """Non-dict entries in user_permissions are skipped."""
        backend = UserReadRestrictionFilterBackend()
        target = self._make_target()
        qs = MagicMock()
        empty_qs = MagicMock()
        qs.none.return_value = empty_qs
        request = _mock_request(user_permissions=["not-a-dict", 42])

        result = backend._apply_default_permission_read_filter(
            request, qs, target, "id"
        )
        self.assertEqual(result, empty_qs)
