"""
Unit tests for ``UserReadRestrictionFilterBackend`` — per-row read permission
enforcement in the List/Many views.

**What this tests (customer-visible behaviour)**

``UserReadRestrictionFilterBackend`` sits in the filter chain for every
queryset returned to the frontend.  It enforces per-row read permissions
based on Keycloak UMA resource grants.  If it silently passes all rows,
users see data they should not.  If it over-filters, users lose access
to data they own.

**Methodology**

Pure mock-based tests — no real database queries.  The filter backend
receives a mock request, mock queryset, and mock view, and we verify
which queryset operations (``filter``, ``exclude``, ``none``) are called.

Run::

    lex test lex.process_admin.tests.test_user_read_restriction_filter --verbosity=2 --noinput --keepdb
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.api.views.model_entries.filter_backends import UserReadRestrictionFilterBackend
from lex.core.models.LexModel import LexModel, PermissionResult


class FilterStubLexModel(LexModel):
    """A minimal LexModel subclass for permission testing."""

    class Meta:
        app_label = "lex_app"
        managed = False


class FilterCustomPermLexModel(LexModel):
    """A LexModel with a custom ``permission_read`` override."""

    class Meta:
        app_label = "lex_app"
        managed = False

    def permission_read(self, user_context):
        return PermissionResult.allow_all("custom")


class TestFilterQuerysetBypass(SimpleTestCase):
    """Prove fast-path bypasses for known model types."""

    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()

    def test_auditlogstatus_bypassed(self):
        """AuditLogStatus rows are always readable — no per-row check."""
        model_class = type("AuditLogStatus", (), {"__name__": "AuditLogStatus"})
        view = MagicMock()
        view.kwargs = {"model_container": SimpleNamespace(model_class=model_class)}
        request = MagicMock()
        qs = MagicMock()

        result = self.backend.filter_queryset(request, qs, view)
        self.assertIs(result, qs)

    def test_calculationlog_bypassed(self):
        """CalculationLog rows are always readable — no per-row check."""
        model_class = type("CalculationLog", (), {"__name__": "CalculationLog"})
        view = MagicMock()
        view.kwargs = {"model_container": SimpleNamespace(model_class=model_class)}
        request = MagicMock()
        qs = MagicMock()

        result = self.backend.filter_queryset(request, qs, view)
        self.assertIs(result, qs)

    def test_auditlog_uses_handle_auditlog(self):
        """AuditLog rows go through ``_handle_auditlog``."""
        model_class = type("AuditLog", (), {"__name__": "AuditLog"})
        view = MagicMock()
        view.kwargs = {"model_container": SimpleNamespace(model_class=model_class)}
        request = MagicMock()
        qs = MagicMock()

        with patch.object(self.backend, "_handle_auditlog", return_value=qs) as mock_handle:
            result = self.backend.filter_queryset(request, qs, view)
        mock_handle.assert_called_once_with(request, qs)
        self.assertIs(result, qs)


class TestGetDefaultPermissionTarget(SimpleTestCase):
    """Prove ``_get_default_permission_target`` detects fast-path eligibility."""

    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()

    def test_lexmodel_subclass_returns_model_and_pk(self):
        """A LexModel subclass with default permission_read is eligible."""
        target, field = self.backend._get_default_permission_target(FilterStubLexModel)
        self.assertIs(target, FilterStubLexModel)
        self.assertIsNotNone(field)

    def test_custom_permission_read_returns_none(self):
        """A model with a custom ``permission_read`` override is NOT eligible."""
        target, field = self.backend._get_default_permission_target(FilterCustomPermLexModel)
        self.assertIsNone(target)
        self.assertIsNone(field)

    def test_non_lexmodel_returns_none(self):
        """A plain class returns (None, None)."""

        class PlainModel:
            pass

        target, field = self.backend._get_default_permission_target(PlainModel)
        self.assertIsNone(target)
        self.assertIsNone(field)

    def test_instance_type_attribute_fallback(self):
        """Falls back to ``model_class.instance_type`` if it's a LexModel."""

        class HistoryModel:
            instance_type = FilterStubLexModel

            class _meta:
                pk = FilterStubLexModel._meta.pk

                @staticmethod
                def get_field(name):
                    return True  # pretend the field exists

        target, field = self.backend._get_default_permission_target(HistoryModel)
        self.assertIs(target, FilterStubLexModel)


class TestApplyDefaultPermissionReadFilter(SimpleTestCase):
    """Prove ``_apply_default_permission_read_filter`` applies Keycloak scopes."""

    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()

    def test_global_read_returns_unfiltered(self):
        """A global ``read`` scope (no resource_set_id) passes all rows."""
        request = MagicMock()
        request.user_permissions = [
            {"rsname": "lex_app.FilterStubLexModel", "scopes": ["read"]},
        ]
        qs = MagicMock()

        result = self.backend._apply_default_permission_read_filter(
            request, qs, FilterStubLexModel, "id",
        )
        self.assertIs(result, qs)

    def test_no_matching_permission_returns_none(self):
        """No matching resource permission → ``queryset.none()``."""
        request = MagicMock()
        request.user_permissions = [
            {"rsname": "other_app.Other", "scopes": ["read"]},
        ]
        qs = MagicMock()
        qs.none.return_value = MagicMock()

        result = self.backend._apply_default_permission_read_filter(
            request, qs, FilterStubLexModel, "id",
        )
        qs.none.assert_called_once()

    def test_scoped_read_filters_by_ids(self):
        """Per-resource read scope filters queryset to allowed IDs."""
        request = MagicMock()
        request.user_permissions = [
            {"rsname": "lex_app.FilterStubLexModel", "scopes": ["read"], "resource_set_id": "1"},
            {"rsname": "lex_app.FilterStubLexModel", "scopes": ["read"], "resource_set_id": "3"},
        ]
        qs = MagicMock()
        qs.filter.return_value = MagicMock()

        result = self.backend._apply_default_permission_read_filter(
            request, qs, FilterStubLexModel, "id",
        )
        qs.filter.assert_called_once()
        call_kwargs = qs.filter.call_args[1]
        self.assertIn("id__in", call_kwargs)

    def test_no_read_scope_returns_none(self):
        """Permission without ``read`` scope → ``queryset.none()``."""
        request = MagicMock()
        request.user_permissions = [
            {"rsname": "lex_app.FilterStubLexModel", "scopes": ["write"]},
        ]
        qs = MagicMock()
        qs.none.return_value = MagicMock()

        result = self.backend._apply_default_permission_read_filter(
            request, qs, FilterStubLexModel, "id",
        )
        qs.none.assert_called_once()

    def test_empty_permissions_returns_none(self):
        """Empty ``user_permissions`` → ``queryset.none()``."""
        request = MagicMock()
        request.user_permissions = []
        qs = MagicMock()
        qs.none.return_value = MagicMock()

        result = self.backend._apply_default_permission_read_filter(
            request, qs, FilterStubLexModel, "id",
        )
        qs.none.assert_called_once()

    def test_none_permissions_returns_none(self):
        """``user_permissions = None`` → ``queryset.none()``."""
        request = MagicMock()
        request.user_permissions = None
        qs = MagicMock()
        qs.none.return_value = MagicMock()

        result = self.backend._apply_default_permission_read_filter(
            request, qs, FilterStubLexModel, "id",
        )
        qs.none.assert_called_once()

    def test_non_mapping_permissions_skipped(self):
        """Non-dict entries in user_permissions are silently skipped."""
        request = MagicMock()
        request.user_permissions = ["not-a-dict", 42, None]
        qs = MagicMock()
        qs.none.return_value = MagicMock()

        result = self.backend._apply_default_permission_read_filter(
            request, qs, FilterStubLexModel, "id",
        )
        qs.none.assert_called_once()
