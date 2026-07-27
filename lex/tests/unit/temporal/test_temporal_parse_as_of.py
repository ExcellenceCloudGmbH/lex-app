"""
Tests for lex.api.utils.temporal.parse_as_of_datetime
=====================================================

parse_as_of_datetime is the entry point for bitemporal query parameter parsing.
It normalises incoming datetime strings to UTC, handling:
- Naive datetimes (interpreted as UTC)
- Offset-aware datetimes (converted to UTC)
- Z-suffix (ISO 8601)
- USE_TZ on/off
- Garbage input (returns None)
"""

import os
import sys
import unittest
from datetime import timezone as dt_tz
from pathlib import Path

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings")
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase, override_settings

from lex.api.utils.temporal import parse_as_of_datetime


class ParseAsOfDatetimeNoneInputTests(SimpleTestCase):
    """None and empty inputs must return None."""

    def test_none_returns_none(self):
        self.assertIsNone(parse_as_of_datetime(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_as_of_datetime(""))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(parse_as_of_datetime("   "))

    def test_garbage_returns_none(self):
        self.assertIsNone(parse_as_of_datetime("not-a-date"))

    def test_partial_date_returns_none(self):
        self.assertIsNone(parse_as_of_datetime("2025"))


class ParseAsOfDatetimeZSuffixTests(SimpleTestCase):
    """Z-suffix (ISO 8601 UTC) handling."""

    @override_settings(USE_TZ=True)
    def test_z_suffix_parsed_as_utc(self):
        result = parse_as_of_datetime("2025-06-15T10:30:00Z")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2025)
        self.assertEqual(result.month, 6)
        self.assertEqual(result.day, 15)
        self.assertEqual(result.hour, 10)
        self.assertEqual(result.minute, 30)
        self.assertEqual(result.tzinfo, dt_tz.utc)

    @override_settings(USE_TZ=True)
    def test_z_suffix_with_microseconds(self):
        result = parse_as_of_datetime("2025-01-01T00:00:00.123456Z")
        self.assertIsNotNone(result)
        self.assertEqual(result.microsecond, 123456)


class ParseAsOfDatetimeNaiveTests(SimpleTestCase):
    """Naive datetimes (no timezone) are interpreted as UTC."""

    @override_settings(USE_TZ=True)
    def test_naive_interpreted_as_utc(self):
        result = parse_as_of_datetime("2025-03-20T14:00:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, dt_tz.utc)
        self.assertEqual(result.hour, 14)


class ParseAsOfDatetimeOffsetAwareTests(SimpleTestCase):
    """Offset-aware datetimes are converted to UTC."""

    @override_settings(USE_TZ=True)
    def test_positive_offset_converted_to_utc(self):
        """2025-06-15T15:00:00+05:00 → 2025-06-15T10:00:00 UTC."""
        result = parse_as_of_datetime("2025-06-15T15:00:00+05:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.hour, 10)
        self.assertEqual(result.tzinfo, dt_tz.utc)

    @override_settings(USE_TZ=True)
    def test_negative_offset_converted_to_utc(self):
        """2025-06-15T08:00:00-04:00 → 2025-06-15T12:00:00 UTC."""
        result = parse_as_of_datetime("2025-06-15T08:00:00-04:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.hour, 12)


class ParseAsOfDatetimeUseTzOffTests(SimpleTestCase):
    """When USE_TZ=False, returns naive UTC datetime."""

    @override_settings(USE_TZ=False)
    def test_use_tz_false_returns_naive(self):
        result = parse_as_of_datetime("2025-06-15T10:00:00Z")
        self.assertIsNotNone(result)
        self.assertIsNone(result.tzinfo)
        self.assertEqual(result.hour, 10)

    @override_settings(USE_TZ=False)
    def test_use_tz_false_with_offset(self):
        """Even with offset, returns naive UTC equivalent."""
        result = parse_as_of_datetime("2025-06-15T15:00:00+05:00")
        self.assertIsNotNone(result)
        self.assertIsNone(result.tzinfo)
        self.assertEqual(result.hour, 10)


class ParseAsOfEdgeCasesTests(SimpleTestCase):
    """Edge cases for robustness."""

    @override_settings(USE_TZ=True)
    def test_date_only_parsed_as_midnight_utc(self):
        """Date-only string is parsed as midnight UTC."""
        result = parse_as_of_datetime("2025-06-15")
        self.assertIsNotNone(result)
        self.assertEqual(result.hour, 0)
        self.assertEqual(result.minute, 0)

    @override_settings(USE_TZ=True)
    def test_numeric_input_coerced_to_string(self):
        """Non-string input (e.g. int) is str()-converted."""
        result = parse_as_of_datetime(12345)
        self.assertIsNone(result)  # "12345" is not a valid datetime


if __name__ == "__main__":
    unittest.main()
