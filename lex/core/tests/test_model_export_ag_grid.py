from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.http import QueryDict
from django.test import TestCase
from rest_framework import serializers

from lex.core.models.LexModel import PermissionResult
from lex.api.views.file_operations.ModelExport import (
    AG_GROUP_HIERARCHY_COLUMN,
    AG_GROUP_HIERARCHY_DEPTH_COLUMN,
    ModelExportView,
)
from lex.api.views.model_entries.List import ListModelEntries


User = get_user_model()


class UserLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "is_staff", "is_superuser")


class PermissionLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ("id", "content_type", "codename", "name")


class _Container:
    model_class = User
    pk_name = "id"
    serializers_map = {"default": UserLiteSerializer}

    def get_serializers_map(self):
        return self.serializers_map


class ModelExportAgGridTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create_user(username="alice", password="x", is_staff=False)
        User.objects.create_user(username="alex", password="x", is_staff=True)
        User.objects.create_user(username="bob", password="x", is_staff=False)

    def _view(self) -> ModelExportView:
        view = ModelExportView()
        view.kwargs = {"model_container": _Container()}
        return view

    def _request(self):
        return SimpleNamespace(
            query_params=QueryDict(""),
            user=SimpleNamespace(
                is_authenticated=True,
                is_superuser=False,
                email="viewer@example.com",
                groups=SimpleNamespace(values_list=lambda *args, **kwargs: []),
            ),
            user_permissions=[],
            userinfo={},
            client_roles=[],
            session={},
        )

    def _permission_view(self) -> ModelExportView:
        class PermissionContainer:
            model_class = Permission
            pk_name = "id"
            serializers_map = {"default": PermissionLiteSerializer}

            def get_serializers_map(self):
                return self.serializers_map

        view = ModelExportView()
        view.kwargs = {"model_container": PermissionContainer()}
        return view

    def test_build_ag_grid_dataframe_for_pivot_respects_layout_and_labels(self):
        ag_export = {
            "request": {
                "startRow": 0,
                "endRow": 100,
                "rowGroupCols": [],
                "groupKeys": [],
                "pivotCols": [{"field": "is_staff", "id": "is_staff"}],
                "valueCols": [{"field": "id", "id": "id_sum", "aggFunc": "sum"}],
                "sortModel": [],
                "filterModel": {},
                "pivotMode": True,
            },
            "columns": [
                {"colId": "false__id_sum__sum", "dataKey": "false__id_sum__sum", "headerName": "Not Staff"},
                {"colId": "true__id_sum__sum", "dataKey": "true__id_sum__sum", "headerName": "Staff"},
            ],
            "includeColumnLabels": True,
        }

        df = self._view()._build_ag_grid_dataframe(
            queryset=User.objects.all(),
            request=self._request(),
            model=User,
            ag_export=ag_export,
        )

        self.assertEqual(list(df.columns), ["Not Staff", "Staff"])
        self.assertEqual(len(df.index), 1)

    def test_build_ag_grid_dataframe_for_grouping_uses_visible_column_order(self):
        ag_export = {
            "request": {
                "startRow": 0,
                "endRow": 100,
                "rowGroupCols": [{"field": "is_staff", "id": "is_staff"}],
                "groupKeys": [],
                "pivotCols": [],
                "valueCols": [{"field": "id", "id": "id_count", "aggFunc": "count"}],
                "sortModel": [{"colId": "is_staff", "sort": "asc"}],
                "filterModel": {},
                "pivotMode": False,
            },
            "columns": [
                {"colId": "ag-Grid-AutoColumn", "dataKey": "is_staff", "field": "is_staff", "headerName": "Group"},
                {"colId": "id_count", "dataKey": "id_count", "headerName": "Count"},
            ],
            "includeColumnLabels": True,
        }

        df = self._view()._build_ag_grid_dataframe(
            queryset=User.objects.all(),
            request=self._request(),
            model=User,
            ag_export=ag_export,
        )

        self.assertEqual(list(df.columns), ["Group", "Count"])
        self.assertEqual(len(df.index), 2)

    def test_apply_ag_selection_filters_with_group_key_path(self):
        filtered = self._view()._apply_ag_selection_filters(
            queryset=User.objects.all(),
            model=User,
            selected_ids=[],
            selected_group_key_paths=[[{"field": "is_staff", "key": False}]],
        )

        self.assertEqual(filtered.count(), 2)
        self.assertTrue(all(not user.is_staff for user in filtered))

    def test_apply_ag_selection_filters_with_ids_and_group_paths_unions_results(self):
        staff_user = User.objects.filter(is_staff=True).first()
        assert staff_user is not None

        filtered = self._view()._apply_ag_selection_filters(
            queryset=User.objects.all(),
            model=User,
            selected_ids=[str(staff_user.pk)],
            selected_group_key_paths=[[{"field": "is_staff", "key": False}]],
        )

        self.assertEqual(filtered.count(), 3)

    def test_collect_ag_export_rows_expands_nested_groups(self):
        view = self._view()
        ag_export = {
            "request": {
                "startRow": 0,
                "endRow": 100,
                "rowGroupCols": [
                    {"field": "is_staff", "id": "is_staff"},
                    {"field": "is_superuser", "id": "is_superuser"},
                ],
                "groupKeys": [],
                "pivotCols": [],
                "valueCols": [{"field": "id", "id": "id_count", "aggFunc": "count"}],
                "sortModel": [{"colId": "is_staff", "sort": "asc"}],
                "filterModel": {},
                "pivotMode": False,
            },
            "columns": [],
            "includeColumnLabels": True,
        }

        request_payload = view._normalize_ag_request(ag_export["request"])

        ag_view = ListModelEntries()
        ag_view.request = self._request()
        ag_view.args = ()
        ag_view.kwargs = {"model_container": _Container()}
        ag_view.format_kwarg = None
        ag_view._ag_model_class = User
        ag_view._ag_field_validity_cache = {}
        ag_view._ag_model_field_cache = {}

        rows = view._collect_ag_export_rows(
            ag_view=ag_view,
            queryset=User.objects.all(),
            normalized_request=request_payload,
        )

        # At least top-level + nested-level rows should be present.
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(any(row.get("is_staff") is False for row in rows))
        self.assertTrue(any(row.get("is_staff") is True for row in rows))
        self.assertTrue(any("__ag_group_hierarchy_label" in row for row in rows))
        self.assertTrue(any(AG_GROUP_HIERARCHY_DEPTH_COLUMN in row for row in rows))
        self.assertTrue(any(row.get(AG_GROUP_HIERARCHY_DEPTH_COLUMN) == 0 for row in rows))
        self.assertTrue(any((row.get(AG_GROUP_HIERARCHY_DEPTH_COLUMN) or 0) > 0 for row in rows))
        self.assertTrue(any(str(row.get("__ag_group_hierarchy_label", "")).startswith("  ") for row in rows))

    def test_refresh_hierarchy_labels_uses_fk_readable_values(self):
        permission = Permission.objects.order_by("id").first()
        assert permission is not None

        view = self._permission_view()
        df = pd.DataFrame(
            [
                {
                    "content_type": permission.content_type_id,
                    "codename": permission.codename,
                    AG_GROUP_HIERARCHY_COLUMN: str(permission.content_type_id),
                    AG_GROUP_HIERARCHY_DEPTH_COLUMN: 0,
                },
                {
                    "content_type": permission.content_type_id,
                    "codename": permission.codename,
                    AG_GROUP_HIERARCHY_COLUMN: f"  {permission.codename}",
                    AG_GROUP_HIERARCHY_DEPTH_COLUMN: 1,
                },
            ]
        )

        df = view._apply_foreign_key_display_names(df, Permission)
        refreshed = view._refresh_hierarchy_labels_with_readable_values(
            df=df,
            normalized_request={
                "rowGroupCols": [
                    {"field": "content_type", "id": "content_type"},
                    {"field": "codename", "id": "codename"},
                ]
            },
        )

        self.assertEqual(refreshed.iloc[0]["content_type"], str(permission.content_type))
        self.assertEqual(refreshed.iloc[0][AG_GROUP_HIERARCHY_COLUMN], str(permission.content_type))
        self.assertEqual(refreshed.iloc[1][AG_GROUP_HIERARCHY_COLUMN], f"  {permission.codename}")

    def test_filter_and_mask_data_for_export_keeps_only_allowed_permission_fields(self):
        request = self._request()

        with patch.object(
            User,
            "permission_export",
            new=lambda _self, _user_context: PermissionResult.allow_fields({"username"}, "username only"),
            create=True,
        ):
            df = self._view().filter_and_mask_data_for_export(
                queryset=User.objects.filter(username="alice"),
                request=request,
            )

        self.assertEqual(len(df.index), 1)
        self.assertEqual(df.iloc[0]["username"], "alice")
        self.assertIsNotNone(df.iloc[0]["id"])
        self.assertIsNone(df.iloc[0]["email"])
        self.assertIsNone(df.iloc[0]["password"])
        self.assertIsNone(df.iloc[0]["is_staff"])

    def test_filter_and_mask_data_for_export_honors_excluded_fields(self):
        request = self._request()

        with patch.object(
            User,
            "permission_export",
            new=lambda _self, _user_context: PermissionResult.allow_all_except(
                {"password", "email"},
                "hide sensitive fields",
            ),
            create=True,
        ):
            df = self._view().filter_and_mask_data_for_export(
                queryset=User.objects.filter(username="alex"),
                request=request,
            )

        self.assertEqual(len(df.index), 1)
        self.assertEqual(df.iloc[0]["username"], "alex")
        self.assertTrue(df.iloc[0]["is_staff"])
        self.assertIsNotNone(df.iloc[0]["id"])
        self.assertIsNone(df.iloc[0]["email"])
        self.assertIsNone(df.iloc[0]["password"])
