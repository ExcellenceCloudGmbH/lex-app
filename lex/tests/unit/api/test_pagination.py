"""
Unit tests for ``lex.api.views.pagination`` custom pagination classes.

**What this tests (customer-visible behaviour)**

``CustomPageNumberPagination`` and ``CustomLimitOffsetPagination``
configure the query parameter names that the frontend uses when
requesting paginated data from the API (``?size=25``, ``?limit=50``).

**Why it matters**

If the ``page_size_query_param`` or ``limit_query_param`` values change,
every frontend grid breaks silently — the API returns default-sized
pages while the grid expects the size it requested, leading to blank
pages or missing rows.

**Methodology**

Class attribute inspection — no DB, no HTTP.

Run::

    python manage.py test lex.tests.test_pagination
"""

from django.test import SimpleTestCase
from lex.api.views.pagination import (
    CustomPageNumberPagination,
    CustomLimitOffsetPagination,
)
from rest_framework.pagination import PageNumberPagination, LimitOffsetPagination


class TestCustomPageNumberPagination(SimpleTestCase):
    """Prove ``CustomPageNumberPagination`` has the correct config."""

    def test_page_size_query_param(self):
        self.assertEqual(
            CustomPageNumberPagination.page_size_query_param, "size"
        )

    def test_inherits_page_number_pagination(self):
        self.assertTrue(
            issubclass(CustomPageNumberPagination, PageNumberPagination)
        )


class TestCustomLimitOffsetPagination(SimpleTestCase):
    """Prove ``CustomLimitOffsetPagination`` has the correct config."""

    def test_limit_query_param(self):
        self.assertEqual(
            CustomLimitOffsetPagination.limit_query_param, "limit"
        )

    def test_offset_query_param(self):
        self.assertEqual(
            CustomLimitOffsetPagination.offset_query_param, "offset"
        )

    def test_inherits_limit_offset_pagination(self):
        self.assertTrue(
            issubclass(CustomLimitOffsetPagination, LimitOffsetPagination)
        )
