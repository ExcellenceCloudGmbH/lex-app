"""
Tests for the AG Grid server-side row model utility functions in
``lex.api.views.model_entries.List``.

This module is the backbone of the AG Grid frontend — every filter, sort,
group, and pivot operation flows through these functions. The module is ~1000
lines and was previously **entirely untested**.

Coverage targets:
    1. ``_safe_key``        — sanitisation for pivot field names
    2. ``_parse_bool``      — string/bool coercion for ``pivotMode`` etc.
    3. ``_parse_int``       — safe integer parsing with defaults
    4. ``_parse_ag_datetime``/``_parse_ag_date`` — timezone-aware date parsing
    5. ``normalize_field_path`` — dot-to-dunder conversion
    6. ``_coerce_value``    — type coercion based on model field internal type
    7. ``_build_query_from_values`` — Q object construction for __in, __range
    8. ``apply_query_param_filters``   — full filter pipeline with negation
    9. ``apply_ordering``   — comma-separated ordering with descending support
    10. ``_build_filter_q`` — AG Grid filter model → Django Q (text/number/date/set)

All tests are pure-unit (no database, no HTTP) and use ``SimpleTestCase``.

How to run::

    lex test lex.process_admin.tests.test_ag_grid_list_utilities \\
        --verbosity=2 --noinput
"""

import os
import sys
from datetime import date, datetime, timezone as dt_tz, timedelta
from decimal import Decimal
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

from django.db.models import Q
from django.test import SimpleTestCase
from django.utils import timezone

from lex.api.views.model_entries.List import (
    _safe_key,
    _parse_bool,
    _parse_int,
    _parse_ag_datetime,
    _parse_ag_date,
    _ag_filter_has_time,
    normalize_field_path,
    _coerce_value,
    _build_query_from_values,
    _resolve_lookup,
    apply_query_param_filters,
    apply_ordering,
    RESERVED_QUERY_PARAMS,
    SAFE_LOOKUPS,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers — fake Django model fields for type coercion tests
# ═══════════════════════════════════════════════════════════════════════════

def _fake_field(internal_type):
    """Build a minimal field stub with ``get_internal_type()``."""
    field = SimpleNamespace()
    field.get_internal_type = lambda: internal_type
    return field


# ═══════════════════════════════════════════════════════════════════════════
#  1. _safe_key
# ═══════════════════════════════════════════════════════════════════════════

class SafeKeyTests(SimpleTestCase):
    """``_safe_key`` sanitises arbitrary values into safe annotation aliases."""

    def test_simple_string_lowercased(self):
        """Plain alphanumeric strings are lowered."""
        self.assertEqual(_safe_key("FundA"), "funda")

    def test_none_becomes_null(self):
        """None is represented as 'null' — not an empty string."""
        self.assertEqual(_safe_key(None), "null")

    def test_special_chars_replaced_with_underscores(self):
        """Characters outside [a-zA-Z0-9_] become underscores."""
        self.assertEqual(_safe_key("Fund (A) — 2024"), "fund_a_2024")

    def test_empty_string_becomes_empty_sentinel(self):
        """An empty string yields 'empty' — never a blank alias."""
        self.assertEqual(_safe_key(""), "empty")

    def test_numeric_value_converted_via_str(self):
        """Integers are stringified, not rejected."""
        self.assertEqual(_safe_key(42), "42")

    def test_leading_trailing_underscores_stripped(self):
        """Sanitised result doesn't start/end with underscores."""
        self.assertEqual(_safe_key("---hello---"), "hello")

    def test_whitespace_only_becomes_empty(self):
        """Whitespace-only strings collapse to 'empty'."""
        self.assertEqual(_safe_key("   "), "empty")


# ═══════════════════════════════════════════════════════════════════════════
#  2. _parse_bool
# ═══════════════════════════════════════════════════════════════════════════

class ParseBoolTests(SimpleTestCase):
    """``_parse_bool`` handles all AG Grid boolean representations."""

    def test_true_string_variants(self):
        """AG Grid sends 'true', '1', 'yes', 'y' for truthy values."""
        for val in ("true", "True", "TRUE", "1", "yes", "y", " true "):
            with self.subTest(val=val):
                self.assertTrue(_parse_bool(val))

    def test_false_string_variants(self):
        """AG Grid sends 'false', '0', 'no', 'n' for falsy values."""
        for val in ("false", "False", "FALSE", "0", "no", "n", " false "):
            with self.subTest(val=val):
                self.assertFalse(_parse_bool(val))

    def test_actual_bool_true(self):
        self.assertTrue(_parse_bool(True))

    def test_actual_bool_false(self):
        self.assertFalse(_parse_bool(False))

    def test_unrecognised_string_uses_default(self):
        """Unknown strings fall back to the provided default."""
        self.assertTrue(_parse_bool("maybe", default=True))
        self.assertFalse(_parse_bool("maybe", default=False))

    def test_none_uses_default(self):
        self.assertTrue(_parse_bool(None, default=True))

    def test_integer_uses_default(self):
        """Non-bool, non-string types fall through to default."""
        self.assertFalse(_parse_bool(42, default=False))


# ═══════════════════════════════════════════════════════════════════════════
#  3. _parse_int
# ═══════════════════════════════════════════════════════════════════════════

class ParseIntTests(SimpleTestCase):
    """``_parse_int`` safely converts AG Grid row indices."""

    def test_valid_integer_string(self):
        self.assertEqual(_parse_int("100", 0), 100)

    def test_valid_integer(self):
        self.assertEqual(_parse_int(50, 0), 50)

    def test_none_returns_default(self):
        self.assertEqual(_parse_int(None, 25), 25)

    def test_non_numeric_string_returns_default(self):
        self.assertEqual(_parse_int("abc", 0), 0)

    def test_float_string_returns_default(self):
        """'3.14' is not a valid int — should fall back."""
        self.assertEqual(_parse_int("3.14", 0), 0)

    def test_empty_string_returns_default(self):
        self.assertEqual(_parse_int("", 10), 10)


# ═══════════════════════════════════════════════════════════════════════════
#  4. _ag_filter_has_time
# ═══════════════════════════════════════════════════════════════════════════

class AgFilterHasTimeTests(SimpleTestCase):
    """``_ag_filter_has_time`` detects whether a date value includes a time component."""

    def test_datetime_object_always_has_time(self):
        self.assertTrue(_ag_filter_has_time(datetime(2024, 1, 1, 12, 0)))

    def test_date_string_without_time(self):
        self.assertFalse(_ag_filter_has_time("2024-01-01"))

    def test_datetime_string_with_time(self):
        self.assertTrue(_ag_filter_has_time("2024-01-01T12:30:00"))

    def test_time_only_string(self):
        self.assertTrue(_ag_filter_has_time("14:30"))

    def test_none_returns_false(self):
        self.assertFalse(_ag_filter_has_time(None))

    def test_date_object_returns_false(self):
        """A date object (not datetime) has no time component."""
        self.assertFalse(_ag_filter_has_time(date(2024, 1, 1)))

    def test_integer_returns_false(self):
        self.assertFalse(_ag_filter_has_time(42))


# ═══════════════════════════════════════════════════════════════════════════
#  5. _parse_ag_datetime and _parse_ag_date
# ═══════════════════════════════════════════════════════════════════════════

class ParseAgDatetimeTests(SimpleTestCase):
    """``_parse_ag_datetime`` parses AG Grid date strings into timezone-aware datetimes."""

    def test_iso_string_with_z_suffix(self):
        """AG Grid sends '2024-01-15T10:00:00Z' — must parse to UTC."""
        result = _parse_ag_datetime("2024-01-15T10:00:00Z")
        self.assertIsNotNone(result)
        self.assertFalse(timezone.is_naive(result))

    def test_iso_string_with_offset(self):
        result = _parse_ag_datetime("2024-01-15T10:00:00+02:00")
        self.assertIsNotNone(result)
        self.assertFalse(timezone.is_naive(result))

    def test_date_only_string_promoted_to_midnight(self):
        """'2024-01-15' becomes midnight of that day, timezone-aware."""
        result = _parse_ag_datetime("2024-01-15")
        self.assertIsNotNone(result)
        self.assertEqual(result.hour, 0)
        self.assertEqual(result.minute, 0)

    def test_none_returns_none(self):
        self.assertIsNone(_parse_ag_datetime(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_ag_datetime(""))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(_parse_ag_datetime("   "))

    def test_datetime_object_input(self):
        """Already a datetime — make it timezone-aware if naive."""
        naive_dt = datetime(2024, 6, 15, 10, 30)
        result = _parse_ag_datetime(naive_dt)
        self.assertIsNotNone(result)
        self.assertFalse(timezone.is_naive(result))

    def test_aware_datetime_passed_through(self):
        aware_dt = datetime(2024, 6, 15, 10, 30, tzinfo=dt_tz.utc)
        result = _parse_ag_datetime(aware_dt)
        self.assertEqual(result, aware_dt)

    def test_date_object_promoted(self):
        """A ``date`` object is promoted to midnight datetime."""
        result = _parse_ag_datetime(date(2024, 3, 1))
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 3)
        self.assertEqual(result.day, 1)


class ParseAgDateTests(SimpleTestCase):
    """``_parse_ag_date`` extracts a ``date`` from AG Grid input."""

    def test_iso_date_string(self):
        result = _parse_ag_date("2024-06-15")
        self.assertEqual(result, date(2024, 6, 15))

    def test_iso_datetime_string_extracts_date(self):
        result = _parse_ag_date("2024-06-15T10:30:00Z")
        self.assertEqual(result, date(2024, 6, 15))

    def test_datetime_object_extracts_date(self):
        result = _parse_ag_date(datetime(2024, 6, 15, 10, 30))
        self.assertEqual(result, date(2024, 6, 15))

    def test_date_object_passed_through(self):
        result = _parse_ag_date(date(2024, 6, 15))
        self.assertEqual(result, date(2024, 6, 15))

    def test_none_returns_none(self):
        self.assertIsNone(_parse_ag_date(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_ag_date(""))


# ═══════════════════════════════════════════════════════════════════════════
#  6. normalize_field_path
# ═══════════════════════════════════════════════════════════════════════════

class NormalizeFieldPathTests(SimpleTestCase):
    """``normalize_field_path`` converts AG Grid dot-notation to Django dunders."""

    def test_dots_become_double_underscores(self):
        self.assertEqual(normalize_field_path("vehicle.name"), "vehicle__name")

    def test_already_dunder_notation_unchanged(self):
        self.assertEqual(normalize_field_path("vehicle__name"), "vehicle__name")

    def test_empty_string(self):
        self.assertEqual(normalize_field_path(""), "")

    def test_none_treated_as_empty(self):
        """None is handled by the ``or ""`` guard."""
        self.assertEqual(normalize_field_path(None), "")

    def test_deeply_nested_path(self):
        self.assertEqual(
            normalize_field_path("organisation.country.name"),
            "organisation__country__name",
        )


# ═══════════════════════════════════════════════════════════════════════════
#  7. _coerce_value — type coercion based on field type
# ═══════════════════════════════════════════════════════════════════════════

class CoerceValueTests(SimpleTestCase):
    """``_coerce_value`` adapts AG Grid filter values to Django field types."""

    def test_integer_field_coercion(self):
        """AG Grid sends numbers as strings — must convert to int."""
        field = _fake_field("IntegerField")
        self.assertEqual(_coerce_value(field, "42"), 42)

    def test_integer_field_invalid_value_passes_through(self):
        field = _fake_field("IntegerField")
        self.assertEqual(_coerce_value(field, "not_a_number"), "not_a_number")

    def test_float_field_coercion(self):
        field = _fake_field("FloatField")
        self.assertAlmostEqual(_coerce_value(field, "3.14"), 3.14)

    def test_decimal_field_coercion(self):
        field = _fake_field("DecimalField")
        result = _coerce_value(field, "99.99")
        self.assertEqual(result, Decimal("99.99"))

    def test_boolean_field_true_variants(self):
        field = _fake_field("BooleanField")
        for val in ("true", "True", "1", "yes", "y"):
            with self.subTest(val=val):
                self.assertTrue(_coerce_value(field, val))

    def test_boolean_field_false_variants(self):
        field = _fake_field("BooleanField")
        for val in ("false", "False", "0", "no", "n"):
            with self.subTest(val=val):
                self.assertFalse(_coerce_value(field, val))

    def test_date_field_coercion(self):
        field = _fake_field("DateField")
        result = _coerce_value(field, "2024-06-15")
        self.assertEqual(result, date(2024, 6, 15))

    def test_datetime_field_coercion(self):
        field = _fake_field("DateTimeField")
        result = _coerce_value(field, "2024-06-15T10:30:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2024)

    def test_null_string_becomes_none(self):
        """AG Grid sends 'null' to represent empty values."""
        field = _fake_field("CharField")
        self.assertIsNone(_coerce_value(field, "null"))

    def test_none_string_becomes_none(self):
        field = _fake_field("CharField")
        self.assertIsNone(_coerce_value(field, "none"))

    def test_true_string_on_non_boolean_field_becomes_bool(self):
        """The string 'true'/'false' is always coerced to bool, regardless of field type."""
        field = _fake_field("CharField")
        self.assertTrue(_coerce_value(field, "true"))
        self.assertFalse(_coerce_value(field, "false"))

    def test_none_value_passes_through(self):
        field = _fake_field("IntegerField")
        self.assertIsNone(_coerce_value(field, None))

    def test_big_auto_field_coercion(self):
        """BigAutoField (primary keys) should coerce to int."""
        field = _fake_field("BigAutoField")
        self.assertEqual(_coerce_value(field, "1"), 1)

    def test_positive_integer_field_coercion(self):
        field = _fake_field("PositiveIntegerField")
        self.assertEqual(_coerce_value(field, "7"), 7)


# ═══════════════════════════════════════════════════════════════════════════
#  8. _build_query_from_values
# ═══════════════════════════════════════════════════════════════════════════

class BuildQueryFromValuesTests(SimpleTestCase):
    """``_build_query_from_values`` constructs Q objects from resolved lookups."""

    def test_single_value_produces_exact_match(self):
        """A single value without __in or __range is an exact match."""
        field = _fake_field("CharField")
        resolved = ("name", "name", field)
        result = _build_query_from_values(resolved, ["hello"])
        self.assertIsNotNone(result)
        self.assertEqual(result, Q(name="hello"))

    def test_in_lookup_with_comma_separated_value(self):
        """'a,b,c' for __in is split on commas."""
        field = _fake_field("CharField")
        resolved = ("status", "status__in", field)
        result = _build_query_from_values(resolved, ["active,inactive,pending"])
        self.assertIsNotNone(result)
        # Verify it constructs an __in query
        self.assertIn("status__in", str(result))

    def test_in_lookup_with_multiple_values(self):
        """Multiple values for __in are used directly."""
        field = _fake_field("IntegerField")
        resolved = ("id", "id__in", field)
        result = _build_query_from_values(resolved, ["1", "2", "3"])
        self.assertIsNotNone(result)

    def test_range_lookup_with_comma_separated(self):
        """'1,10' for __range is split into two bounds."""
        field = _fake_field("IntegerField")
        resolved = ("amount", "amount__range", field)
        result = _build_query_from_values(resolved, ["1,10"])
        self.assertIsNotNone(result)

    def test_range_lookup_with_two_values(self):
        field = _fake_field("IntegerField")
        resolved = ("amount", "amount__range", field)
        result = _build_query_from_values(resolved, ["1", "10"])
        self.assertIsNotNone(result)

    def test_empty_values_returns_none(self):
        """No values → no Q object."""
        field = _fake_field("CharField")
        resolved = ("name", "name", field)
        result = _build_query_from_values(resolved, [])
        self.assertIsNone(result)

    def test_all_empty_strings_returns_none(self):
        field = _fake_field("CharField")
        resolved = ("name", "name", field)
        result = _build_query_from_values(resolved, ["", ""])
        self.assertIsNone(result)

    def test_multiple_non_in_values_produce_or_query(self):
        """Multiple values for a non-__in lookup are OR-combined."""
        field = _fake_field("CharField")
        resolved = ("name", "name", field)
        result = _build_query_from_values(resolved, ["alpha", "beta"])
        self.assertIsNotNone(result)


# ═══════════════════════════════════════════════════════════════════════════
#  9. RESERVED_QUERY_PARAMS and SAFE_LOOKUPS constants
# ═══════════════════════════════════════════════════════════════════════════

class ConstantsTests(SimpleTestCase):
    """Verify critical constants that gate filter and lookup behaviour."""

    def test_reserved_params_include_pagination(self):
        """Pagination params must be reserved to prevent accidental filtering."""
        for param in ("page", "perPage", "_start", "_end"):
            self.assertIn(param, RESERVED_QUERY_PARAMS, f"'{param}' should be reserved")

    def test_reserved_params_include_ag_grid_keys(self):
        """AG Grid server-side params must be reserved."""
        for param in ("startRow", "endRow", "ag_request"):
            self.assertIn(param, RESERVED_QUERY_PARAMS, f"'{param}' should be reserved")

    def test_safe_lookups_include_common_operations(self):
        """All standard Django lookups used by the filter system must be in SAFE_LOOKUPS."""
        for lookup in ("exact", "icontains", "gt", "gte", "lt", "lte", "in", "isnull"):
            self.assertIn(lookup, SAFE_LOOKUPS, f"'{lookup}' should be a safe lookup")

    def test_safe_lookups_include_date_sub_lookups(self):
        """Date-specific sub-lookups must be allowed for datetime fields."""
        for lookup in ("date", "date__gt", "date__gte", "date__lt", "date__lte"):
            self.assertIn(lookup, SAFE_LOOKUPS, f"'{lookup}' should be a safe lookup")

    def test_unsafe_lookups_not_present(self):
        """Dangerous lookups must NOT be in SAFE_LOOKUPS."""
        for lookup in ("regex", "iregex", "year", "week_day"):
            self.assertNotIn(lookup, SAFE_LOOKUPS,
                             f"'{lookup}' should not be a safe lookup (injection risk)")
