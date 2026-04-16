"""
Tests for the AG Grid server-side row model integration.

Verifies that the ``ListModelEntries`` view correctly translates AG Grid
server-side requests into Django ORM queries, covering:
    • Column-level filtering (text, number, date, set)
    • Row grouping and aggregation
    • Pivot mode layout
    • Sort-model translation
    • Query-param datetime filters
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase
from django.utils import timezone
from rest_framework import serializers

from lex.api.views.model_entries.List import ListModelEntries, apply_ordering, apply_query_param_filters
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus


User = get_user_model()


class UserLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "is_staff")


class _AgGridListTestView(ListModelEntries):
    def get_serializer(self, *args, **kwargs):
        return UserLiteSerializer(*args, **kwargs)


class AuditLogLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ("id", "resource", "object_id")


class _AgGridAuditLogTestView(ListModelEntries):
    def get_serializer(self, *args, **kwargs):
        return AuditLogLiteSerializer(*args, **kwargs)


class AgGridServerSideServiceTests(TestCase):
    """Prove AG Grid server-side filtering, grouping, and pivot logic."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="x", first_name="Alice", is_staff=False)
        cls.alex = User.objects.create_user(username="alex", password="x", first_name="Alex", is_staff=True)
        cls.bob = User.objects.create_user(username="bob", password="x", first_name="Bob", is_staff=False)

        cls.alice.date_joined = timezone.make_aware(datetime(2026, 2, 1, 9, 30, 15))
        cls.alice.save(update_fields=["date_joined"])
        cls.alex.date_joined = timezone.make_aware(datetime(2026, 2, 2, 12, 0, 0))
        cls.alex.save(update_fields=["date_joined"])
        cls.bob.date_joined = timezone.make_aware(datetime(2026, 2, 3, 18, 45, 30))
        cls.bob.save(update_fields=["date_joined"])

    def _view(self):
        view = _AgGridListTestView()
        view._ag_model_class = User
        view._ag_field_validity_cache = {}
        view._ag_model_field_cache = {}
        return view

    def _auditlog_view(self):
        view = _AgGridAuditLogTestView()
        view._ag_model_class = AuditLog
        view._ag_field_validity_cache = {}
        view._ag_model_field_cache = {}
        return view

    def test_query_param_filtering_and_ordering(self):
        params = QueryDict("first_name__icontains=al&ordering=-id")
        qs = apply_query_param_filters(User.objects.all(), params, User)
        qs = apply_ordering(qs, params.get("ordering"), User)

        names = list(qs.values_list("first_name", flat=True))
        self.assertEqual(names, ["Alex", "Alice"])

    def test_group_aggregation(self):
        payload = {
            "startRow": 0,
            "endRow": 100,
            "rowGroupCols": [{"field": "is_staff", "id": "is_staff"}],
            "groupKeys": [],
            "valueCols": [{"field": "id", "id": "id_count", "aggFunc": "count"}],
            "sortModel": [{"colId": "is_staff", "sort": "asc"}],
            "filterModel": {},
            "pivotMode": False,
        }

        result = self._view()._execute_ag_grid_request(User.objects.all(), payload)
        self.assertEqual(result["rowCount"], 2)

        by_staff = {row["is_staff"]: row for row in result["rowData"]}
        self.assertEqual(by_staff[False]["__childCount"], 2)
        self.assertEqual(by_staff[False]["id_count"], 2)
        self.assertEqual(by_staff[True]["__childCount"], 1)
        self.assertEqual(by_staff[True]["id_count"], 1)

    def test_pivot_aggregation(self):
        payload = {
            "startRow": 0,
            "endRow": 100,
            "rowGroupCols": [],
            "groupKeys": [],
            "pivotCols": [{"field": "is_staff", "id": "is_staff"}],
            "valueCols": [{"field": "id", "id": "id_sum", "aggFunc": "sum"}],
            "sortModel": [],
            "filterModel": {},
            "pivotMode": True,
        }

        result = self._view()._execute_ag_grid_request(User.objects.all(), payload)

        self.assertEqual(result["rowCount"], 1)
        self.assertIn("pivotResultFields", result)
        self.assertEqual(len(result["pivotResultFields"]), 2)

        pivot_row = result["rowData"][0]
        self.assertIn("false__id_sum__sum", pivot_row)
        self.assertIn("true__id_sum__sum", pivot_row)

    def test_leaf_sort_and_filter_model(self):
        payload = {
            "startRow": 0,
            "endRow": 10,
            "rowGroupCols": [],
            "groupKeys": [],
            "valueCols": [],
            "sortModel": [{"colId": "id", "sort": "desc"}],
            "filterModel": {
                "first_name": {
                    "filterType": "text",
                    "type": "contains",
                    "filter": "al",
                }
            },
            "pivotMode": False,
        }

        result = self._view()._execute_ag_grid_request(User.objects.all(), payload)
        self.assertEqual(result["rowCount"], 2)

        returned = [row["first_name"] for row in result["rowData"]]
        self.assertEqual(returned, ["Alex", "Alice"])

    def test_leaf_filter_model_scalar_value(self):
        payload = {
            "startRow": 0,
            "endRow": 10,
            "rowGroupCols": [],
            "groupKeys": [],
            "valueCols": [],
            "sortModel": [{"colId": "id", "sort": "asc"}],
            "filterModel": {
                "id": self.alex.id,
            },
            "pivotMode": False,
        }

        result = self._view()._execute_ag_grid_request(User.objects.all(), payload)
        self.assertEqual(result["rowCount"], 1)
        self.assertEqual(result["rowData"][0]["username"], "alex")

    def test_group_key_filters_leaf_rows(self):
        payload = {
            "startRow": 0,
            "endRow": 10,
            "rowGroupCols": [{"field": "is_staff", "id": "is_staff"}],
            "groupKeys": [False],
            "valueCols": [],
            "sortModel": [{"colId": "id", "sort": "asc"}],
            "filterModel": {},
            "pivotMode": False,
        }

        result = self._view()._execute_ag_grid_request(User.objects.all(), payload)
        self.assertEqual(result["rowCount"], 2)
        self.assertTrue(all(not row["is_staff"] for row in result["rowData"]))

    def test_datetime_filter_custom_operator_iso_string(self):
        payload = {
            "startRow": 0,
            "endRow": 10,
            "rowGroupCols": [],
            "groupKeys": [],
            "valueCols": [],
            "sortModel": [{"colId": "id", "sort": "asc"}],
            "filterModel": {
                "date_joined": {
                    "operator": "equals",
                    "dateFrom": "2026-02-02T12:00:00",
                }
            },
            "pivotMode": False,
        }

        result = self._view()._execute_ag_grid_request(User.objects.all(), payload)
        self.assertEqual(result["rowCount"], 1)
        self.assertEqual(result["rowData"][0]["username"], "alex")

    def test_datetime_filter_in_range_with_time(self):
        payload = {
            "startRow": 0,
            "endRow": 10,
            "rowGroupCols": [],
            "groupKeys": [],
            "valueCols": [],
            "sortModel": [{"colId": "id", "sort": "asc"}],
            "filterModel": {
                "date_joined": {
                    "filterType": "date",
                    "type": "inRange",
                    "dateFrom": "2026-02-02T00:00:00",
                    "dateTo": "2026-02-03T00:00:00",
                }
            },
            "pivotMode": False,
        }

        result = self._view()._execute_ag_grid_request(User.objects.all(), payload)
        self.assertEqual(result["rowCount"], 1)
        self.assertEqual(result["rowData"][0]["username"], "alex")

    def test_query_param_filtering_by_json_payload_key(self):
        matching_log = AuditLog.objects.create(
            author="tester",
            resource="fund",
            action="update",
            payload={"id": 101, "name": "Alpha"},
        )
        AuditLog.objects.create(
            author="tester",
            resource="fund",
            action="update",
            payload={"id": 202, "name": "Beta"},
        )

        params = QueryDict("payload.id=101")
        qs = apply_query_param_filters(AuditLog.objects.all(), params, AuditLog)

        self.assertEqual(list(qs.values_list("id", flat=True)), [matching_log.id])

    def test_query_param_filtering_by_related_json_payload_key(self):
        matching_log = AuditLog.objects.create(
            author="tester",
            resource="fund",
            action="update",
            payload={"id": 303},
        )
        AuditLogStatus.objects.create(audit_log=matching_log, status="success")

        other_log = AuditLog.objects.create(
            author="tester",
            resource="fund",
            action="update",
            payload={"id": 404},
        )
        AuditLogStatus.objects.create(audit_log=other_log, status="failure")

        params = QueryDict("audit_log.resource=fund&audit_log.payload.id=303")
        qs = apply_query_param_filters(AuditLogStatus.objects.all(), params, AuditLogStatus)

        self.assertEqual(list(qs.values_list("audit_log_id", flat=True)), [matching_log.id])

    def test_auditlog_leaf_rows_avoid_exact_count_until_last_page(self):
        for index in range(3):
            AuditLog.objects.create(
                author=f"tester-{index}",
                resource="fund",
                action="update",
                object_id=index + 1,
                payload={"id": index + 1},
            )

        payload = {
            "startRow": 0,
            "endRow": 2,
            "rowGroupCols": [],
            "groupKeys": [],
            "valueCols": [],
            "sortModel": [{"colId": "id", "sort": "asc"}],
            "filterModel": {},
            "pivotMode": False,
        }

        result = self._auditlog_view()._execute_ag_grid_request(AuditLog.objects.all(), payload)
        self.assertEqual(len(result["rowData"]), 2)
        self.assertIsNone(result["rowCount"])

        payload["startRow"] = 2
        payload["endRow"] = 4
        result = self._auditlog_view()._execute_ag_grid_request(AuditLog.objects.all(), payload)
        self.assertEqual(len(result["rowData"]), 1)
        self.assertEqual(result["rowCount"], 3)

    def test_auditlog_deferred_permissions_stop_after_requested_window(self):
        for index in range(50):
            AuditLog.objects.create(
                author=f"tester-{index}",
                resource="custom-resource",
                action="update",
                object_id=index + 1,
                payload={"id": index + 1},
            )

        view = self._auditlog_view()
        view.request = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=False),
            user_permissions=[],
        )
        payload = {
            "startRow": 0,
            "endRow": 2,
            "rowGroupCols": [],
            "groupKeys": [],
            "valueCols": [],
            "sortModel": [{"colId": "id", "sort": "asc"}],
            "filterModel": {},
            "pivotMode": False,
        }

        with patch(
            "lex.api.views.model_entries.List.UserReadRestrictionFilterBackend._build_auditlog_db_visibility_filters",
            return_value=(frozenset(), None),
        ), patch(
            "lex.api.views.model_entries.List.can_read_from_payload",
            return_value=True,
        ) as can_read_mock:
            result = view._execute_ag_grid_request(
                AuditLog.objects.all(),
                payload,
                defer_auditlog_permissions=True,
            )

        self.assertEqual([row["object_id"] for row in result["rowData"]], [1, 2])
        self.assertIsNone(result["rowCount"])
        self.assertEqual(can_read_mock.call_count, 3)

    def test_auditlog_post_uses_deferred_permissions_for_flat_leaf_requests(self):
        AuditLog.objects.create(
            author="tester",
            resource="fund",
            action="update",
            object_id=1,
            payload={"id": 1},
        )

        view = self._auditlog_view()
        view.kwargs = {"model_container": SimpleNamespace(model_class=AuditLog)}
        request = SimpleNamespace(
            data={
                "request": {
                    "startRow": 0,
                    "endRow": 2,
                    "rowGroupCols": [],
                    "groupKeys": [],
                    "valueCols": [],
                    "sortModel": [{"colId": "id", "sort": "asc"}],
                    "filterModel": {},
                    "pivotMode": False,
                }
            },
            query_params=QueryDict(""),
            user=SimpleNamespace(is_authenticated=False),
            user_permissions=[],
        )
        view.request = request
        view.filter_queryset = lambda queryset: (_ for _ in ()).throw(
            AssertionError("filter_queryset should not run for deferred AuditLog leaf requests")
        )

        with patch.object(
            view,
            "_execute_ag_grid_request",
            return_value={"rowData": [], "rowCount": 0},
        ) as execute_mock:
            response = view.post(request)

        self.assertEqual(response.data, {"rowData": [], "rowCount": 0})
        self.assertTrue(execute_mock.call_args.kwargs["defer_auditlog_permissions"])
