"""
Unit tests for ``LexSerializer._parse_value_for_field``.

**What this tests (customer-visible behaviour)**

``_parse_value_for_field`` converts raw audit-log payload values (JSON)
back into proper Python objects that can be assigned to model fields.
This is used when building "shadow instances" from audit payloads
to check per-record permissions on historical/audit data.

**Why it matters**

If datetime strings aren't parsed, ForeignKey dicts aren't unwrapped,
or types aren't converted, the shadow instance has wrong field values
and permission checks may return incorrect results — either hiding
data users should see or exposing data they shouldn't.

**Methodology**

Pure static method — no DB, no serializer instantiation.

Run::

    python manage.py test lex.tests.test_serializer_parse_value
"""

from datetime import datetime, date, time
from unittest.mock import MagicMock

from django.db.models import ForeignKey
from django.db.models.fields import DateTimeField, DateField, TimeField
from django.test import SimpleTestCase
from lex.api.serializers.base_serializers import LexSerializer


def _mock_field(field_class):
    """Create a mock that isinstance() recognizes as the given field class."""
    mock = MagicMock(spec=field_class)
    mock.__class__ = field_class
    return mock


class TestParseValueForFieldDateTimes(SimpleTestCase):
    """Prove temporal field parsing works correctly."""

    def test_datetime_iso(self):
        field = _mock_field(DateTimeField)
        result = LexSerializer._parse_value_for_field(field, "2024-06-15T10:30:00")
        self.assertIsInstance(result, datetime)
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.hour, 10)

    def test_datetime_invalid_returns_none(self):
        field = _mock_field(DateTimeField)
        self.assertIsNone(LexSerializer._parse_value_for_field(field, "bad"))

    def test_date_iso(self):
        field = _mock_field(DateField)
        result = LexSerializer._parse_value_for_field(field, "2024-06-15")
        self.assertIsInstance(result, date)
        self.assertEqual(result.day, 15)

    def test_date_invalid_returns_none(self):
        field = _mock_field(DateField)
        self.assertIsNone(LexSerializer._parse_value_for_field(field, "bad"))

    def test_time_iso(self):
        field = _mock_field(TimeField)
        result = LexSerializer._parse_value_for_field(field, "14:30:00")
        self.assertIsInstance(result, time)
        self.assertEqual(result.minute, 30)

    def test_time_invalid_returns_none(self):
        field = _mock_field(TimeField)
        self.assertIsNone(LexSerializer._parse_value_for_field(field, "bad"))


class TestParseValueForFieldForeignKey(SimpleTestCase):
    """Prove FK dict payloads are unwrapped to their ID."""

    def test_fk_dict_extracts_id(self):
        field = _mock_field(ForeignKey)
        result = LexSerializer._parse_value_for_field(field, {"id": 42, "name": "Q1"})
        self.assertEqual(result, 42)

    def test_fk_dict_missing_id_returns_none(self):
        field = _mock_field(ForeignKey)
        self.assertIsNone(LexSerializer._parse_value_for_field(field, {"name": "Q1"}))

    def test_fk_non_dict_passes_through(self):
        """FK fields with simple int values should pass through."""
        field = _mock_field(ForeignKey)
        # Not a dict, so not a FK dict representation
        result = LexSerializer._parse_value_for_field(field, 42)
        self.assertEqual(result, 42)


class TestParseValueForFieldScalars(SimpleTestCase):
    """Prove non-temporal, non-FK values pass through unchanged."""

    def test_none_returns_none(self):
        field = MagicMock()
        self.assertIsNone(LexSerializer._parse_value_for_field(field, None))

    def test_string_passes_through(self):
        field = MagicMock()
        self.assertEqual(LexSerializer._parse_value_for_field(field, "hello"), "hello")

    def test_integer_passes_through(self):
        field = MagicMock()
        self.assertEqual(LexSerializer._parse_value_for_field(field, 42), 42)

    def test_boolean_passes_through(self):
        field = MagicMock()
        self.assertTrue(LexSerializer._parse_value_for_field(field, True))

    def test_float_passes_through(self):
        field = MagicMock()
        self.assertEqual(LexSerializer._parse_value_for_field(field, 3.14), 3.14)
