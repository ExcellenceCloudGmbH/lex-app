from datetime import timedelta
from unittest import TestCase

from django.test import override_settings

from lex.api.utils.temporal import parse_as_of_datetime


class ParseAsOfDatetimeTest(TestCase):
    @override_settings(USE_TZ=True)
    def test_naive_input_is_interpreted_as_utc(self):
        parsed = parse_as_of_datetime("2025-01-01T12:00:00")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertEqual(parsed.isoformat(), "2025-01-01T12:00:00+00:00")

    @override_settings(USE_TZ=True)
    def test_offset_input_is_normalized_to_utc(self):
        parsed = parse_as_of_datetime("2025-01-01T12:00:00+02:00")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertEqual(parsed.isoformat(), "2025-01-01T10:00:00+00:00")

    @override_settings(USE_TZ=False)
    def test_use_tz_false_returns_naive_datetime(self):
        parsed = parse_as_of_datetime("2025-01-01T12:00:00Z")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertIsNone(parsed.tzinfo)
        self.assertEqual(parsed.isoformat(), "2025-01-01T12:00:00")

    def test_invalid_input_returns_none(self):
        self.assertIsNone(parse_as_of_datetime("not-a-date"))
        self.assertIsNone(parse_as_of_datetime(""))
        self.assertIsNone(parse_as_of_datetime(None))
