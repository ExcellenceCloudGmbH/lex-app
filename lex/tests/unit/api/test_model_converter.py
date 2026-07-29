"""
Unit tests for ``lex.api.utils.converters.create_model_converter``.

**What this tests (customer-visible behaviour)**

``create_model_converter`` builds a Django URL path converter that maps
URL path segments to model containers.  The generated ``regex``
attribute is used by Django's URL routing to validate which model names
are accepted in API endpoints like ``/api/<model_name>/``.

**Why it matters**

If the regex is wrong, API requests for valid models return 404.
If ``to_python`` returns the wrong container, CRUD operations
target the wrong database table.

**Methodology**

Mock model collection — no DB.

Run::

    python manage.py test lex.tests.test_model_converter
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase
from lex.api.utils.converters import create_model_converter


class TestCreateModelConverter(SimpleTestCase):
    """Prove ``create_model_converter`` builds a working URL converter."""

    def _make_collection(self, model_ids):
        collection = MagicMock()
        collection.all_model_ids = model_ids
        return collection

    def test_regex_matches_model_ids(self):
        collection = self._make_collection(["fund", "period", "cashflow"])
        converter_cls = create_model_converter(collection)
        self.assertIn("fund", converter_cls.regex)
        self.assertIn("period", converter_cls.regex)
        self.assertIn("cashflow", converter_cls.regex)

    def test_regex_pipe_separated(self):
        collection = self._make_collection(["fund", "period"])
        converter_cls = create_model_converter(collection)
        self.assertEqual(converter_cls.regex, "fund|period")

    def test_to_python_calls_get_container(self):
        collection = self._make_collection(["fund"])
        container = MagicMock()
        collection.get_container.return_value = container
        converter_cls = create_model_converter(collection)

        instance = converter_cls()
        result = converter_cls.to_python(instance, "fund")
        collection.get_container.assert_called_once_with("fund")
        self.assertIs(result, container)

    def test_to_url_returns_id(self):
        collection = self._make_collection(["fund"])
        converter_cls = create_model_converter(collection)

        value = MagicMock()
        value.id = "fund"
        instance = converter_cls()
        result = converter_cls.to_url(instance, value)
        self.assertEqual(result, "fund")

    def test_single_model_id(self):
        collection = self._make_collection(["solo"])
        converter_cls = create_model_converter(collection)
        self.assertEqual(converter_cls.regex, "solo")

    def test_empty_model_ids(self):
        collection = self._make_collection([])
        converter_cls = create_model_converter(collection)
        self.assertEqual(converter_cls.regex, "")
