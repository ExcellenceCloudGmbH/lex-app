from types import SimpleNamespace
from unittest.mock import Mock

from django.db import models
from django.test import SimpleTestCase

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
