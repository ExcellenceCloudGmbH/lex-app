"""
Unit tests for ``lex.api.utils.temporal.parse_as_of_datetime``.

**What this tests (customer-visible behaviour)**

The ``as_of`` query parameter enables time-travel across the bitemporal
history.  ``parse_as_of_datetime`` normalizes incoming values (ISO strings,
Z-notation, naive/aware datetimes) to UTC so that the ORM filter is
consistent regardless of how the frontend encodes the date.

**Why it matters**

If the parser mishandles timezone offsets or Z-notation, the time-travel
query returns wrong historical data — a data-integrity issue.

**Methodology**

Pure function, no DB access.  We override ``settings.USE_TZ`` via
``@override_settings`` to test both branches (aware vs. naive return).

Run::

    python manage.py test lex.tests.test_temporal_utils
"""

from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase, override_settings

from lex.api.utils.temporal import parse_as_of_datetime


class TestParseAsOfDatetime(SimpleTestCase):
    """Prove ``parse_as_of_datetime`` normalizes timestamps correctly."""

    def test_none_returns_none(self):
        self.assertIsNone(parse_as_of_datetime(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_as_of_datetime(""))
        self.assertIsNone(parse_as_of_datetime("   "))

    def test_invalid_string_returns_none(self):
        self.assertIsNone(parse_as_of_datetime("not-a-date"))

    @override_settings(USE_TZ=True)
    def test_z_notation_returns_utc_aware(self):
        result = parse_as_of_datetime("2024-06-15T10:30:00Z")
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, dt_timezone.utc)
        self.assertEqual(result.hour, 10)
        self.assertEqual(result.minute, 30)

    @override_settings(USE_TZ=True)
    def test_offset_converted_to_utc(self):
        """A +02:00 offset should shift backward by 2 hours."""
        result = parse_as_of_datetime("2024-06-15T12:00:00+02:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.hour, 10)
        self.assertEqual(result.tzinfo, dt_timezone.utc)

    @override_settings(USE_TZ=True)
    def test_naive_input_interpreted_as_utc(self):
        result = parse_as_of_datetime("2024-06-15T08:00:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, dt_timezone.utc)
        self.assertEqual(result.hour, 8)

    @override_settings(USE_TZ=False)
    def test_use_tz_false_returns_naive(self):
        result = parse_as_of_datetime("2024-06-15T10:30:00Z")
        self.assertIsNotNone(result)
        self.assertIsNone(result.tzinfo)
        self.assertEqual(result.hour, 10)

    @override_settings(USE_TZ=False)
    def test_use_tz_false_offset_still_converts(self):
        result = parse_as_of_datetime("2024-06-15T12:00:00+02:00")
        self.assertIsNotNone(result)
        self.assertIsNone(result.tzinfo)
        self.assertEqual(result.hour, 10)

    def test_numeric_input_handled(self):
        """Non-string input should be stringified; invalid values return None."""
        result = parse_as_of_datetime(12345)
        self.assertIsNone(result)
