"""
Unit tests for ``lex.api.utils.helpers._parse_value``.

**What this tests (customer-visible behaviour)**

``_parse_value`` converts raw audit-log payload values back into their
proper Python types (datetime, date, time, UUID, Decimal, FK dicts).
This is used when building "shadow instances" to check permissions
against historical/audit data.

**Why it matters**

If date strings aren't parsed correctly, permission checks against
historical records may fail or return wrong results.  ForeignKey
dictionaries that aren't unwrapped cause ``TypeError`` during
model instantiation.

**Methodology**

Pure function.  We pass mock field objects with the right type
and test value conversion.

Run::

    python manage.py test lex.tests.test_api_helpers
"""

from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID

from django.db.models.fields import DateTimeField, DateField, TimeField
from django.test import SimpleTestCase


from lex.api.utils.helpers import _parse_value


def _mock_field(field_class):
    """Create a mock that is an instance of the given field class."""
    mock = MagicMock(spec=field_class)
    mock.__class__ = field_class
    return mock


class TestParseValueDateTimes(SimpleTestCase):
    """Prove ``_parse_value`` handles temporal fields correctly."""

    def test_datetime_field_parses_iso(self):
        field = _mock_field(DateTimeField)
        result = _parse_value(field, "2024-06-15T10:30:00")
        self.assertIsInstance(result, datetime)
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.hour, 10)

    def test_datetime_field_invalid_returns_none(self):
        field = _mock_field(DateTimeField)
        self.assertIsNone(_parse_value(field, "not-a-date"))

    def test_date_field_parses_iso(self):
        field = _mock_field(DateField)
        result = _parse_value(field, "2024-06-15")
        self.assertIsInstance(result, date)
        self.assertEqual(result.month, 6)

    def test_date_field_invalid_returns_none(self):
        field = _mock_field(DateField)
        self.assertIsNone(_parse_value(field, "nope"))

    def test_time_field_parses_iso(self):
        field = _mock_field(TimeField)
        result = _parse_value(field, "10:30:00")
        self.assertIsInstance(result, time)
        self.assertEqual(result.hour, 10)

    def test_time_field_invalid_returns_none(self):
        field = _mock_field(TimeField)
        self.assertIsNone(_parse_value(field, "nope"))


class TestParseValueForeignKey(SimpleTestCase):
    """Prove ``_parse_value`` unwraps FK dict representations."""

    def test_fk_dict_extracts_id(self):
        from django.db.models import ForeignKey
        field = MagicMock(spec=ForeignKey)
        field.__class__ = ForeignKey
        result = _parse_value(field, {"id": 42, "name": "Period Q1"})
        self.assertEqual(result, 42)

    def test_fk_dict_missing_id_returns_none(self):
        from django.db.models import ForeignKey
        field = MagicMock(spec=ForeignKey)
        field.__class__ = ForeignKey
        self.assertIsNone(_parse_value(field, {"name": "Period Q1"}))


class TestParseValueScalars(SimpleTestCase):
    """Prove ``_parse_value`` handles UUID and Decimal strings."""

    def test_none_returns_none(self):
        field = MagicMock()
        self.assertIsNone(_parse_value(field, None))

    def test_uuid_string_parsed(self):
        field = MagicMock()
        result = _parse_value(field, "550e8400-e29b-41d4-a716-446655440000")
        self.assertIsInstance(result, UUID)

    def test_decimal_string_parsed(self):
        field = MagicMock()
        result = _parse_value(field, "123.45")
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal("123.45"))

    def test_non_special_string_returned_as_is(self):
        """Strings that aren't UUID or Decimal pass through unchanged."""
        field = MagicMock()
        result = _parse_value(field, "hello world")
        self.assertEqual(result, "hello world")

    def test_integer_returned_as_is(self):
        field = MagicMock()
        self.assertEqual(_parse_value(field, 42), 42)

    def test_boolean_returned_as_is(self):
        field = MagicMock()
        self.assertEqual(_parse_value(field, True), True)
