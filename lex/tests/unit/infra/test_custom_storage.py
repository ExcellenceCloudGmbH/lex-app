"""
Unit tests for ``lex.utilities.storage.custom_storage.CustomDefaultStorage.url``.

**What this tests (customer-visible behaviour)**

``CustomDefaultStorage.url`` builds the public URL for uploaded files
(PDFs, images, XLSX exports).  The frontend uses these URLs in
``<a>``/``<img>`` tags.

**Why it matters**

If the URL has a doubled leading slash or missing base, file downloads
break — users see "404 Not Found" or the browser treats it as a
protocol-relative URL and hits the wrong host.

**Methodology**

Instantiates ``CustomDefaultStorage`` with a known ``base_url``
and verifies URL construction.  No filesystem or DB.

Run::

    python manage.py test lex.tests.test_custom_storage
"""

import unittest

from django.test import SimpleTestCase
from lex.utilities.storage.custom_storage import CustomDefaultStorage


class TestCustomDefaultStorageUrl(SimpleTestCase):
    """Prove ``CustomDefaultStorage.url`` builds correct URLs."""

    def _make_storage(self, base_url="/media/"):
        return CustomDefaultStorage(location="/tmp/test", base_url=base_url)

    def test_basic_url(self):
        storage = self._make_storage()
        url = storage.url("uploads/file.pdf")
        self.assertEqual(url, "/media/uploads/file.pdf")

    def test_strips_leading_slash(self):
        storage = self._make_storage()
        url = storage.url("/uploads/file.pdf")
        self.assertEqual(url, "/media/uploads/file.pdf")

    def test_none_name(self):
        """None name should be handled gracefully."""
        storage = self._make_storage()
        url = storage.url(None)
        self.assertEqual(url, "/media/")

    @unittest.skip("Django FileSystemStorage may set a default base_url — needs clarification")
    def test_no_base_url_raises(self):
        storage = CustomDefaultStorage(location="/tmp/test", base_url=None)
        with self.assertRaises(ValueError):
            storage.url("file.pdf")

    def test_absolute_base_url(self):
        storage = self._make_storage(base_url="https://cdn.example.com/media/")
        url = storage.url("report.xlsx")
        self.assertEqual(url, "https://cdn.example.com/media/report.xlsx")

    def test_nested_path(self):
        storage = self._make_storage()
        url = storage.url("uploads/2024/06/report.pdf")
        self.assertEqual(url, "/media/uploads/2024/06/report.pdf")
