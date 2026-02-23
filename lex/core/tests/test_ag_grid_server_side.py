from datetime import datetime

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase
from django.utils import timezone
from rest_framework import serializers

from lex.api.views.model_entries.List import ListModelEntries, apply_ordering, apply_query_param_filters


User = get_user_model()


class UserLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "is_staff")


class _AgGridListTestView(ListModelEntries):
    def get_serializer(self, *args, **kwargs):
        return UserLiteSerializer(*args, **kwargs)


class AgGridServerSideServiceTests(TestCase):
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
