from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.db import models
from django.test import SimpleTestCase

from lex.api.utils.helpers import can_read_from_payload
from lex.api.views.model_entries.filter_backends import UserReadRestrictionFilterBackend
from lex.core.models.LexModel import LexModel


class _DefaultLexModel(LexModel):
    name = models.CharField(max_length=20, default="")

    class Meta:
        app_label = "core_tests"
        managed = False


class _CustomPermissionLexModel(LexModel):
    name = models.CharField(max_length=20, default="")

    class Meta:
        app_label = "core_tests"
        managed = False

    def permission_read(self, user_context):
        return super().permission_read(user_context)


class UserReadRestrictionFilterBackendTests(SimpleTestCase):
    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()
        self.resource_name = f"{_DefaultLexModel._meta.app_label}.{_DefaultLexModel.__name__}"

    def _make_queryset(self):
        qs = Mock()
        qs.model = _DefaultLexModel
        qs.none.return_value = "NONE"
        qs.filter.return_value = "FILTERED"
        return qs

    def test_default_permission_global_scope_short_circuits(self):
        queryset = self._make_queryset()
        queryset.iterator.side_effect = AssertionError("iterator should not run in fast-path")

        request = SimpleNamespace(
            user_permissions=[
                {
                    "rsname": self.resource_name,
                    "resource_set_id": None,
                    "scopes": ["read"],
                }
            ]
        )

        result = self.backend._handle_lexmodel_default(request, queryset)

        self.assertIs(result, queryset)
        queryset.filter.assert_not_called()
        queryset.none.assert_not_called()

    def test_default_permission_filters_by_resource_ids(self):
        queryset = self._make_queryset()

        request = SimpleNamespace(
            user_permissions=[
                {
                    "rsname": self.resource_name,
                    "resource_set_id": "10",
                    "scopes": ["read"],
                },
                {
                    "rsname": self.resource_name,
                    "resource_set_id": "15",
                    "scopes": ["read"],
                },
            ]
        )

        result = self.backend._handle_lexmodel_default(request, queryset)

        self.assertEqual(result, "FILTERED")
        self.assertIn("id__in", queryset.filter.call_args.kwargs)
        self.assertEqual(set(queryset.filter.call_args.kwargs["id__in"]), {10, 15})
        queryset.none.assert_not_called()

    def test_default_permission_without_read_returns_none_queryset(self):
        queryset = self._make_queryset()
        request = SimpleNamespace(user_permissions=[])

        result = self.backend._handle_lexmodel_default(request, queryset)

        self.assertEqual(result, "NONE")
        queryset.none.assert_called_once()
        queryset.filter.assert_not_called()

    def test_custom_permission_model_is_not_fast_path_eligible(self):
        target_model, lookup_field = self.backend._get_default_permission_target(_CustomPermissionLexModel)
        self.assertIsNone(target_model)
        self.assertIsNone(lookup_field)

    def test_can_read_from_payload_uses_default_permission_fast_path(self):
        request = SimpleNamespace(
            user_permissions=[
                {
                    "rsname": self.resource_name,
                    "resource_set_id": "10",
                    "scopes": ["read"],
                }
            ]
        )
        audit_log = SimpleNamespace(
            content_type_id=123,
            payload={"id": 10},
            _state=SimpleNamespace(db="default"),
        )
        fake_content_type = SimpleNamespace(model_class=lambda: _DefaultLexModel)

        with patch(
            "lex.api.utils.helpers.safe_get_content_type",
            return_value=fake_content_type,
        ), patch(
            "lex.api.utils.helpers.build_shadow_instance",
            side_effect=AssertionError("shadow instance should not be built"),
        ):
            self.assertTrue(can_read_from_payload(request, audit_log))

    def test_auditlog_default_permission_rows_are_filtered_without_row_iteration(self):
        request = SimpleNamespace(
            user_permissions=[
                {
                    "rsname": self.resource_name,
                    "resource_set_id": "10",
                    "scopes": ["read"],
                }
            ]
        )
        residual_queryset = Mock()
        residual_queryset.only.return_value = residual_queryset
        residual_queryset.iterator.return_value = iter([])

        queryset = Mock()
        queryset.exclude.return_value = residual_queryset
        queryset.filter.return_value = "FINAL"

        with patch.object(
            UserReadRestrictionFilterBackend,
            "_get_auditlog_default_permission_resource_map",
            return_value={_DefaultLexModel.__name__.lower(): _DefaultLexModel},
        ), patch(
            "lex.api.views.model_entries.filter_backends.can_read_from_payload",
            side_effect=AssertionError("row iteration should not be needed"),
        ):
            result = self.backend._handle_auditlog(request, queryset)

        self.assertEqual(result, "FINAL")
        queryset.exclude.assert_called_once_with(resource__in=frozenset({_DefaultLexModel.__name__.lower()}))
        queryset.filter.assert_called_once()

    def test_auditlog_default_permission_db_filter_prefers_object_id_with_json_fallback(self):
        request = SimpleNamespace(
            user_permissions=[
                {
                    "rsname": self.resource_name,
                    "resource_set_id": "10",
                    "scopes": ["read"],
                }
            ]
        )

        with patch.object(
            UserReadRestrictionFilterBackend,
            "_get_auditlog_default_permission_resource_map",
            return_value={_DefaultLexModel.__name__.lower(): _DefaultLexModel},
        ):
            handled_resources, allowed_q = self.backend._build_auditlog_db_visibility_filters(request)

        self.assertEqual(handled_resources, frozenset({_DefaultLexModel.__name__.lower()}))
        self.assertIn("object_id__in", str(allowed_q))
        self.assertIn("payload__id__in", str(allowed_q))
