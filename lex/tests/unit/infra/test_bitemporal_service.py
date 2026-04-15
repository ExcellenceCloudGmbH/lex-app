"""
Tests for ``lex.core.services.Bitemporal`` — time-travel query helpers.

Covers:
- ``get_queryset_as_of`` for main models (valid-time), history models
  (system-time), and error paths (no meta_history, no history at all).
- ``resurrect_object`` — creating a deleted object with a validity window.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from django.test import SimpleTestCase, TransactionTestCase
from django.db import models
from django.utils import timezone

from lex.core.services.Bitemporal import get_queryset_as_of, resurrect_object


# ── Unit tests (SimpleTestCase — no DB) ──────────────────────────────


class GetQuerysetAsOfErrorPathsTest(SimpleTestCase):
    """Exercise the ValueError branches that don't need real tables."""

    def test_model_without_history_raises(self):
        """A model with neither ``history`` nor ``history_id`` → ValueError."""
        PlainModel = type("PlainModel", (), {})  # no history attrs
        with self.assertRaises(ValueError) as cm:
            get_queryset_as_of(PlainModel, timezone.now())
        self.assertIn("neither a main model", str(cm.exception))

    def test_history_model_without_meta_history_raises(self):
        """A history model (has history_id) but no meta_history → ValueError."""
        HistModel = type("HistModel", (), {"history_id": 1})  # no meta_history
        with self.assertRaises(ValueError) as cm:
            get_queryset_as_of(HistModel, timezone.now())
        self.assertIn("no meta_history", str(cm.exception))

    def test_main_model_queries_valid_time(self):
        """Main model (has history, no history_id) → valid-time query."""
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.exclude.return_value = mock_qs

        mock_history_model = MagicMock()
        mock_history_model.objects.filter.return_value = mock_qs

        mock_history = MagicMock()
        mock_history.model = mock_history_model

        MainModel = type("MainModel", (), {"history": mock_history})

        as_of = timezone.now()
        result = get_queryset_as_of(MainModel, as_of)

        # Verify filter chain was called
        mock_history_model.objects.filter.assert_called_once()
        mock_qs.filter.assert_called_once()
        mock_qs.exclude.assert_called_once()
        self.assertEqual(result, mock_qs)

    def test_history_model_queries_system_time(self):
        """History model (has history_id + meta_history) → system-time query."""
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.exclude.return_value = mock_qs

        mock_meta_model = MagicMock()
        mock_meta_model.objects.filter.return_value = mock_qs

        mock_meta_history = MagicMock()
        mock_meta_history.model = mock_meta_model

        HistModel = type("HistModel", (), {
            "history_id": 1,
            "meta_history": mock_meta_history,
        })

        as_of = timezone.now()
        result = get_queryset_as_of(HistModel, as_of)

        mock_meta_model.objects.filter.assert_called_once()
        mock_qs.filter.assert_called_once()
        mock_qs.exclude.assert_called_once()
        self.assertEqual(result, mock_qs)


class ResurrectObjectTest(SimpleTestCase):
    """Exercise ``resurrect_object`` without a real database."""

    def test_resurrect_creates_instance_and_saves(self):
        """resurrect_object sets _history_date, saves, returns instance."""
        mock_instance = MagicMock()
        MockModel = MagicMock(return_value=mock_instance)
        # No history attr → deletion marker is skipped
        del MockModel.history

        valid_from = timezone.now()
        result = resurrect_object(
            MockModel, pk=42, valid_from=valid_from,
            attributes={"name": "Revived"},
        )

        MockModel.assert_called_once_with(pk=42, name="Revived")
        self.assertEqual(mock_instance._history_date, valid_from)
        mock_instance.save.assert_called_once()
        self.assertEqual(result, mock_instance)

    def test_resurrect_with_valid_to_creates_deletion_marker(self):
        """When valid_to is given and model has history, create a '-' record."""
        mock_instance = MagicMock()
        MockModel = MagicMock(return_value=mock_instance)

        mock_history_model = MagicMock()
        MockModel.history.model = mock_history_model

        valid_from = timezone.now()
        valid_to = valid_from + timedelta(days=30)

        result = resurrect_object(
            MockModel, pk=99, valid_from=valid_from,
            attributes={"name": "Temp"}, valid_to=valid_to,
        )

        mock_history_model.objects.create.assert_called_once()
        call_kwargs = mock_history_model.objects.create.call_args[1]
        self.assertEqual(call_kwargs["history_type"], "-")
        self.assertEqual(call_kwargs["valid_from"], valid_to)
        self.assertEqual(call_kwargs["name"], "Temp")

    def test_resurrect_without_valid_to_skips_deletion_marker(self):
        """Without valid_to, no deletion marker even if model has history."""
        mock_instance = MagicMock()
        MockModel = MagicMock(return_value=mock_instance)
        MockModel.history.model = MagicMock()

        result = resurrect_object(
            MockModel, pk=1, valid_from=timezone.now(),
        )

        MockModel.history.model.objects.create.assert_not_called()

    def test_resurrect_defaults_attributes_to_empty(self):
        """Calling without attributes uses empty dict."""
        mock_instance = MagicMock()
        MockModel = MagicMock(return_value=mock_instance)
        del MockModel.history

        resurrect_object(MockModel, pk=1, valid_from=timezone.now())
        MockModel.assert_called_once_with(pk=1)
