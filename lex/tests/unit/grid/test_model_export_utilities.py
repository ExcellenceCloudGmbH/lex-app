"""
Tests for ``ModelExportView`` utility methods — the Excel/CSV export layer
that serves AG Grid's "Export to Excel" and the framework's standalone
export endpoint.

This is a 683-line view with complex logic for:
- Per-object field-level permission masking during export
- AG Grid column layout application (reordering + renaming)
- Foreign key display name resolution
- Base64-encoded selection filter parsing
- Group key path selection from AG Grid pivot/group exports
- Hierarchy label refresh with readable FK values

These tests cover the **utility functions** without needing a running
server or database — they verify the data transformation logic that sits
between the raw queryset and the final Excel file.

Coverage targets:
    1. ``get_exportable_fields_for_object``  — new API / legacy / fallback
    2. ``_normalize_ag_request``             — defaults and normalisation
    3. ``_extract_selected_ids_for_export``  — base64 decoding
    4. ``_extract_selected_group_key_paths`` — AG Grid selection parsing
    5. ``_apply_ag_column_layout``           — column reorder + rename
    6. ``_coerce_group_key``                 — type coercion for group keys
    7. ``_safe_bool`` (module-level)         — bool parsing

All tests are pure-unit (no database, no HTTP) and use ``SimpleTestCase``.

How to run::

    lex test lex.process_admin.tests.test_model_export_utilities \\
        --verbosity=2 --noinput
"""

import base64
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

import pandas as pd
from django.test import SimpleTestCase

from lex.api.views.file_operations.ModelExport import (
    ModelExportView,
    _safe_bool,
    MAX_AG_EXPORT_ROWS,
    AG_GROUP_HIERARCHY_COLUMN,
    AG_GROUP_HIERARCHY_DEPTH_COLUMN,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_export_view():
    """Create a bare ModelExportView for calling utility methods."""
    view = ModelExportView.__new__(ModelExportView)
    view.kwargs = {}
    return view


def _fake_field(name, internal_type, is_relation=False, related_model=None):
    """Build a minimal field stub."""
    field = SimpleNamespace(
        name=name,
        attname=name,
        get_internal_type=lambda: internal_type,
        is_relation=is_relation,
        related_model=related_model,
    )
    return field


# ═══════════════════════════════════════════════════════════════════════════
#  1. _safe_bool (module-level utility)
# ═══════════════════════════════════════════════════════════════════════════

class SafeBoolTests(SimpleTestCase):
    """``_safe_bool`` is used to parse ``pivotMode`` and other booleans."""

    def test_true_variants(self):
        for val in (True, "true", "True", "1", "yes", "y"):
            with self.subTest(val=val):
                self.assertTrue(_safe_bool(val))

    def test_false_variants(self):
        for val in (False, "false", "False", "0", "no", "n"):
            with self.subTest(val=val):
                self.assertFalse(_safe_bool(val))

    def test_default_on_unrecognized(self):
        self.assertTrue(_safe_bool("maybe", default=True))
        self.assertFalse(_safe_bool("maybe", default=False))


# ═══════════════════════════════════════════════════════════════════════════
#  2. get_exportable_fields_for_object
# ═══════════════════════════════════════════════════════════════════════════

class GetExportableFieldsTests(SimpleTestCase):
    """
    ``get_exportable_fields_for_object`` determines which fields a user
    can see in the exported Excel file. It must respect the permission API.
    """

    def test_new_permission_api_allow_all(self):
        """When ``permission_export`` returns allow_all, all fields are exportable."""
        view = _make_export_view()

        from lex.core.models.LexModel import PermissionResult
        obj = MagicMock()
        obj._meta.fields = [
            _fake_field("id", "BigAutoField"),
            _fake_field("name", "CharField"),
            _fake_field("amount", "DecimalField"),
        ]
        obj.permission_export = MagicMock(
            return_value=PermissionResult.allow_all()
        )

        request = MagicMock()
        result = view.get_exportable_fields_for_object(obj, request)

        # All model fields + always-included fields
        self.assertIn("name", result)
        self.assertIn("amount", result)
        self.assertIn("id", result)
        self.assertIn("created_by", result)
        self.assertIn("edited_by", result)

    def test_new_permission_api_deny(self):
        """When ``permission_export`` denies, only base fields remain."""
        view = _make_export_view()

        from lex.core.models.LexModel import PermissionResult
        obj = MagicMock()
        obj._meta.fields = [_fake_field("id", "BigAutoField"), _fake_field("secret", "CharField")]
        obj.permission_export = MagicMock(
            return_value=PermissionResult.deny()
        )

        request = MagicMock()
        result = view.get_exportable_fields_for_object(obj, request)

        # Denied → empty set, but union with {id, created_by, edited_by}
        self.assertIn("id", result)
        self.assertIn("created_by", result)
        self.assertNotIn("secret", result)

    def test_legacy_can_export_returns_set(self):
        """Legacy ``can_export`` returning a set of field names."""
        view = _make_export_view()
        obj = MagicMock(spec=[])
        obj._meta = MagicMock()
        obj._meta.fields = [_fake_field("id", "BigAutoField"), _fake_field("name", "CharField")]
        obj.can_export = MagicMock(return_value={"name"})

        request = MagicMock()
        result = view.get_exportable_fields_for_object(obj, request)

        self.assertIn("name", result)
        self.assertIn("id", result)  # always included

    def test_no_permission_method_allows_all_fields(self):
        """If neither permission API is defined, all fields are exportable."""
        view = _make_export_view()
        obj = MagicMock(spec=[])
        obj._meta = MagicMock()
        obj._meta.fields = [
            _fake_field("id", "BigAutoField"),
            _fake_field("name", "CharField"),
        ]

        request = MagicMock()
        result = view.get_exportable_fields_for_object(obj, request)

        self.assertIn("name", result)
        self.assertIn("id", result)

    def test_permission_method_exception_falls_back_to_all(self):
        """If the permission method raises, fall back to all fields."""
        view = _make_export_view()
        obj = MagicMock()
        obj._meta.fields = [_fake_field("id", "BigAutoField"), _fake_field("name", "CharField")]
        obj.permission_export = MagicMock(side_effect=RuntimeError("broken"))

        request = MagicMock()
        result = view.get_exportable_fields_for_object(obj, request)

        self.assertIn("name", result)


# ═══════════════════════════════════════════════════════════════════════════
#  3. _normalize_ag_request
# ═══════════════════════════════════════════════════════════════════════════

class NormalizeAgRequestTests(SimpleTestCase):
    """``_normalize_ag_request`` sets safe defaults for all AG Grid params."""

    def test_sets_start_row_to_zero(self):
        view = _make_export_view()
        result = view._normalize_ag_request({"startRow": 50})
        self.assertEqual(result["startRow"], 0)

    def test_sets_end_row_to_max(self):
        view = _make_export_view()
        result = view._normalize_ag_request({})
        self.assertEqual(result["endRow"], MAX_AG_EXPORT_ROWS)

    def test_normalizes_lists_from_none(self):
        """Missing keys default to empty lists."""
        view = _make_export_view()
        result = view._normalize_ag_request({})
        self.assertEqual(result["groupKeys"], [])
        self.assertEqual(result["rowGroupCols"], [])
        self.assertEqual(result["pivotCols"], [])
        self.assertEqual(result["valueCols"], [])
        self.assertEqual(result["sortModel"], [])

    def test_preserves_existing_filter_model(self):
        view = _make_export_view()
        filters = {"name": {"filterType": "text", "filter": "Fund", "type": "contains"}}
        result = view._normalize_ag_request({"filterModel": filters})
        self.assertEqual(result["filterModel"], filters)

    def test_pivot_mode_default_false(self):
        view = _make_export_view()
        result = view._normalize_ag_request({})
        self.assertFalse(result["pivotMode"])

    def test_non_dict_input_treated_as_empty(self):
        view = _make_export_view()
        result = view._normalize_ag_request(None)
        self.assertEqual(result["startRow"], 0)


# ═══════════════════════════════════════════════════════════════════════════
#  4. _extract_selected_ids_for_export
# ═══════════════════════════════════════════════════════════════════════════

class ExtractSelectedIdsTests(SimpleTestCase):
    """``_extract_selected_ids_for_export`` decodes base64-encoded ID filters."""

    def test_decodes_base64_ids(self):
        """IDs are base64-encoded in the query string format ``ids=1&ids=2``."""
        view = _make_export_view()
        raw = base64.b64encode(b"ids=10&ids=20&ids=30").decode()
        result = view._extract_selected_ids_for_export({"filtered_export": raw})
        self.assertEqual(result, ["10", "20", "30"])

    def test_empty_string_returns_empty_list(self):
        view = _make_export_view()
        result = view._extract_selected_ids_for_export({"filtered_export": ""})
        self.assertEqual(result, [])

    def test_missing_key_returns_empty_list(self):
        view = _make_export_view()
        result = view._extract_selected_ids_for_export({})
        self.assertEqual(result, [])

    def test_invalid_base64_returns_empty_list(self):
        """Garbage base64 should not crash."""
        view = _make_export_view()
        result = view._extract_selected_ids_for_export({"filtered_export": "!!!invalid!!!"})
        self.assertEqual(result, [])

    def test_no_ids_in_query_string_returns_empty(self):
        """Base64 content without 'ids' key returns empty."""
        view = _make_export_view()
        raw = base64.b64encode(b"name=Fund").decode()
        result = view._extract_selected_ids_for_export({"filtered_export": raw})
        self.assertEqual(result, [])


# ═══════════════════════════════════════════════════════════════════════════
#  5. _extract_selected_group_key_paths
# ═══════════════════════════════════════════════════════════════════════════

class ExtractGroupKeyPathsTests(SimpleTestCase):
    """
    ``_extract_selected_group_key_paths`` parses AG Grid's selection payload
    for grouped exports — users can select specific group branches to export.
    """

    def test_parses_valid_paths(self):
        view = _make_export_view()
        payload = {
            "selection": {
                "groupKeyPaths": [
                    [{"field": "vehicle.name", "key": "Fund A"}],
                    [{"field": "vehicle.name", "key": "Fund B"}, {"field": "target", "key": "T1"}],
                ]
            }
        }
        result = view._extract_selected_group_key_paths(payload)

        self.assertEqual(len(result), 2)
        # First path has one step
        self.assertEqual(result[0][0]["field"], "vehicle__name")  # dots → dunders
        self.assertEqual(result[0][0]["key"], "Fund A")
        # Second path has two steps
        self.assertEqual(len(result[1]), 2)

    def test_non_dict_payload_returns_empty(self):
        view = _make_export_view()
        self.assertEqual(view._extract_selected_group_key_paths("not a dict"), [])

    def test_missing_selection_returns_empty(self):
        view = _make_export_view()
        self.assertEqual(view._extract_selected_group_key_paths({}), [])

    def test_empty_paths_list_returns_empty(self):
        view = _make_export_view()
        self.assertEqual(
            view._extract_selected_group_key_paths({"selection": {"groupKeyPaths": []}}),
            [],
        )

    def test_invalid_path_entries_skipped(self):
        """Non-dict steps in a path are skipped."""
        view = _make_export_view()
        payload = {
            "selection": {
                "groupKeyPaths": [
                    [{"field": "name", "key": "X"}, "invalid_step"],
                ]
            }
        }
        result = view._extract_selected_group_key_paths(payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 1)  # invalid step was skipped

    def test_steps_without_field_are_skipped(self):
        view = _make_export_view()
        payload = {
            "selection": {
                "groupKeyPaths": [
                    [{"key": "X"}],  # missing 'field'
                ]
            }
        }
        result = view._extract_selected_group_key_paths(payload)
        self.assertEqual(result, [])


# ═══════════════════════════════════════════════════════════════════════════
#  6. _apply_ag_column_layout
# ═══════════════════════════════════════════════════════════════════════════

class ApplyAgColumnLayoutTests(SimpleTestCase):
    """
    ``_apply_ag_column_layout`` reorders and renames DataFrame columns
    to match the AG Grid column layout the user sees in the UI.
    """

    def test_reorders_columns(self):
        """Columns appear in the order specified by the AG Grid layout."""
        view = _make_export_view()
        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        ag_export = {
            "columns": [
                {"dataKey": "c", "headerName": "Column C"},
                {"dataKey": "a", "headerName": "Column A"},
            ],
            "includeColumnLabels": True,
        }

        result = view._apply_ag_column_layout(df, ag_export)

        self.assertEqual(list(result.columns), ["Column C", "Column A"])

    def test_renames_columns_with_header_names(self):
        """Columns are renamed to their ``headerName`` for human-readable exports."""
        view = _make_export_view()
        df = pd.DataFrame({"investment_name": [1], "total_amount": [2]})
        ag_export = {
            "columns": [
                {"dataKey": "investment_name", "headerName": "Investment Name"},
                {"dataKey": "total_amount", "headerName": "Total Amount (€)"},
            ],
            "includeColumnLabels": True,
        }

        result = view._apply_ag_column_layout(df, ag_export)

        self.assertIn("Investment Name", result.columns)
        self.assertIn("Total Amount (€)", result.columns)

    def test_deduplicates_header_names(self):
        """If two columns share a headerName, suffixes (2), (3) etc. are added."""
        view = _make_export_view()
        df = pd.DataFrame({"a": [1], "b": [2]})
        ag_export = {
            "columns": [
                {"dataKey": "a", "headerName": "Amount"},
                {"dataKey": "b", "headerName": "Amount"},
            ],
            "includeColumnLabels": True,
        }

        result = view._apply_ag_column_layout(df, ag_export)

        self.assertIn("Amount", result.columns)
        self.assertIn("Amount (2)", result.columns)

    def test_no_columns_spec_returns_original(self):
        """If no column layout is provided, return the DataFrame as-is."""
        view = _make_export_view()
        df = pd.DataFrame({"a": [1], "b": [2]})

        result = view._apply_ag_column_layout(df, {})

        self.assertEqual(list(result.columns), ["a", "b"])

    def test_empty_dataframe_returns_empty(self):
        view = _make_export_view()
        df = pd.DataFrame()

        result = view._apply_ag_column_layout(df, {"columns": [{"dataKey": "a"}]})

        self.assertTrue(result.empty)

    def test_fallback_key_uses_field_or_colid(self):
        """If ``dataKey`` is missing, fall back to ``field`` then ``colId``."""
        view = _make_export_view()
        df = pd.DataFrame({"name": [1]})
        ag_export = {
            "columns": [{"field": "name", "headerName": "Name"}],
            "includeColumnLabels": True,
        }

        result = view._apply_ag_column_layout(df, ag_export)

        self.assertIn("Name", result.columns)

    def test_without_column_labels_no_rename(self):
        """When ``includeColumnLabels`` is False, columns keep data keys."""
        view = _make_export_view()
        df = pd.DataFrame({"a": [1], "b": [2]})
        ag_export = {
            "columns": [
                {"dataKey": "b", "headerName": "Column B"},
                {"dataKey": "a", "headerName": "Column A"},
            ],
            "includeColumnLabels": False,
        }

        result = view._apply_ag_column_layout(df, ag_export)

        # Columns reordered but NOT renamed
        self.assertEqual(list(result.columns), ["b", "a"])


# ═══════════════════════════════════════════════════════════════════════════
#  7. _coerce_group_key
# ═══════════════════════════════════════════════════════════════════════════

class CoerceGroupKeyTests(SimpleTestCase):
    """``_coerce_group_key`` converts AG Grid group key strings to proper types."""

    def test_integer_field_coercion(self):
        view = _make_export_view()
        field = _fake_field("id", "IntegerField")
        self.assertEqual(view._coerce_group_key(field, "42"), 42)

    def test_boolean_field_true(self):
        view = _make_export_view()
        field = _fake_field("active", "BooleanField")
        self.assertTrue(view._coerce_group_key(field, "true"))

    def test_boolean_field_false(self):
        view = _make_export_view()
        field = _fake_field("active", "BooleanField")
        self.assertFalse(view._coerce_group_key(field, "false"))

    def test_float_field_coercion(self):
        view = _make_export_view()
        field = _fake_field("rate", "FloatField")
        self.assertAlmostEqual(view._coerce_group_key(field, "3.14"), 3.14)

    def test_null_string_becomes_none(self):
        view = _make_export_view()
        field = _fake_field("name", "CharField")
        self.assertIsNone(view._coerce_group_key(field, "null"))

    def test_none_passes_through(self):
        view = _make_export_view()
        field = _fake_field("name", "CharField")
        self.assertIsNone(view._coerce_group_key(field, None))

    def test_no_model_field_returns_string(self):
        """If field is None (unknown), return the raw string."""
        view = _make_export_view()
        self.assertEqual(view._coerce_group_key(None, "hello"), "hello")

    def test_non_string_passes_through(self):
        view = _make_export_view()
        field = _fake_field("id", "IntegerField")
        self.assertEqual(view._coerce_group_key(field, 42), 42)


# ═══════════════════════════════════════════════════════════════════════════
#  8. Constants
# ═══════════════════════════════════════════════════════════════════════════

class ExportConstantsTests(SimpleTestCase):
    """Verify critical export constants."""

    def test_max_export_rows_is_reasonable(self):
        """The max export limit should be large but bounded (prevent OOM)."""
        self.assertEqual(MAX_AG_EXPORT_ROWS, 1_000_000)

    def test_hierarchy_column_names_are_strings(self):
        self.assertIsInstance(AG_GROUP_HIERARCHY_COLUMN, str)
        self.assertIsInstance(AG_GROUP_HIERARCHY_DEPTH_COLUMN, str)
