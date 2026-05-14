"""
Tests for lex.api.views.model_entries.filter_backends
=====================================================

PrimaryKeyListFilterBackend provides ?ids= filtering for the Many/One views.
It filters the queryset to only include records whose PK is in the ``ids``
query parameter list.  Also supports ``filter_for_export`` via base64-encoded
query strings.
"""

import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings")
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase

from lex.api.views.model_entries.filter_backends import PrimaryKeyListFilterBackend


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_request(ids=None):
    req = MagicMock()
    req.query_params = MagicMock()
    req.query_params.getlist = MagicMock(return_value=ids or [])
    return req


def _make_view(pk_name="id"):
    view = MagicMock()
    view.kwargs = {"model_container": MagicMock(pk_name=pk_name)}
    return view


def _make_queryset():
    qs = MagicMock()
    qs.filter.return_value = qs
    return qs


# ═══════════════════════════════════════════════════════════════════════════
# 1. filter_queryset — ?ids= filtering
# ═══════════════════════════════════════════════════════════════════════════
class FilterQuerysetTests(SimpleTestCase):
    """Test the primary ?ids= query parameter filter."""

    def test_no_ids_returns_unfiltered_queryset(self):
        """Without ?ids=, queryset passes through unchanged."""
        backend = PrimaryKeyListFilterBackend()
        qs = _make_queryset()
        request = _make_request(ids=[])
        view = _make_view()

        result = backend.filter_queryset(request, qs, view)

        self.assertIs(result, qs)
        qs.filter.assert_not_called()

    def test_with_ids_filters_queryset(self):
        """?ids=1&ids=2&ids=3 → filter(pk_name__in=[1,2,3])."""
        backend = PrimaryKeyListFilterBackend()
        qs = _make_queryset()
        request = _make_request(ids=["1", "2", "3"])
        view = _make_view(pk_name="id")

        backend.filter_queryset(request, qs, view)

        qs.filter.assert_called_once_with(id__in=["1", "2", "3"])

    def test_custom_pk_name(self):
        """Uses the model_container's pk_name for the filter lookup."""
        backend = PrimaryKeyListFilterBackend()
        qs = _make_queryset()
        request = _make_request(ids=["abc"])
        view = _make_view(pk_name="custom_pk")

        backend.filter_queryset(request, qs, view)

        qs.filter.assert_called_once_with(custom_pk__in=["abc"])

    def test_empty_string_ids_cleaned(self):
        """Empty strings in the ids list are removed."""
        backend = PrimaryKeyListFilterBackend()
        qs = _make_queryset()
        request = _make_request(ids=["1", "", "3", ""])
        view = _make_view()

        backend.filter_queryset(request, qs, view)

        qs.filter.assert_called_once_with(id__in=["1", "3"])

    def test_single_id(self):
        """Single ?ids=42 still works."""
        backend = PrimaryKeyListFilterBackend()
        qs = _make_queryset()
        request = _make_request(ids=["42"])
        view = _make_view()

        backend.filter_queryset(request, qs, view)

        qs.filter.assert_called_once_with(id__in=["42"])


# ═══════════════════════════════════════════════════════════════════════════
# 2. filter_for_export — base64-encoded query string
# ═══════════════════════════════════════════════════════════════════════════
class FilterForExportTests(SimpleTestCase):
    """Test export filtering via base64-encoded query parameters."""

    def _encode(self, query_string):
        return base64.b64encode(query_string.encode("utf-8")).decode("utf-8")

    def test_base64_ids_decoded_and_filtered(self):
        """Base64-encoded 'ids=1&ids=2' correctly filters queryset."""
        backend = PrimaryKeyListFilterBackend()
        qs = _make_queryset()
        view = _make_view(pk_name="pk")
        encoded = self._encode("ids=1&ids=2")

        backend.filter_for_export({"filtered_export": encoded}, qs, view)

        qs.filter.assert_called_once_with(pk__in=["1", "2"])

    def test_no_ids_in_export_returns_unfiltered(self):
        """If base64 data has no ids= parameter, queryset passes through."""
        backend = PrimaryKeyListFilterBackend()
        qs = _make_queryset()
        view = _make_view()
        encoded = self._encode("foo=bar")

        result = backend.filter_for_export({"filtered_export": encoded}, qs, view)

        self.assertIs(result, qs)
        qs.filter.assert_not_called()

    def test_empty_ids_in_export_cleaned(self):
        """Empty ids in base64 query are filtered out."""
        backend = PrimaryKeyListFilterBackend()
        qs = _make_queryset()
        view = _make_view(pk_name="id")
        encoded = self._encode("ids=5&ids=&ids=10")

        backend.filter_for_export({"filtered_export": encoded}, qs, view)

        qs.filter.assert_called_once_with(id__in=["5", "10"])


if __name__ == "__main__":
    unittest.main()
