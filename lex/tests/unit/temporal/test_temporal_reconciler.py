"""
Tests for ``TemporalReconciler`` — the read-repair mechanism that detects
records whose ``valid_from`` timestamp has passed and triggers
synchronization.

In a bitemporal system, records can be created with a future ``valid_from``
date (e.g., a fund commitment scheduled to activate on 2025-01-01). When
that date passes, the main table must be updated to reflect the now-active
record. The ``TemporalReconciler`` scans for such records and delegates
to ``BitemporalSynchronizer``.

Coverage targets:
    1. ``reconcile_model_window`` — single-model reconciliation
    2. ``reconcile_changes_since`` — cross-model reconciliation
    3. Models without history are skipped
    4. No candidates → zero syncs

All tests use ``SimpleTestCase`` with mocked models and database.

How to run::

    lex test lex.process_admin.tests.test_temporal_reconciler \\
        --verbosity=2 --noinput
"""

import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase
from django.utils import timezone

from lex.process_admin.utils.temporal_reconciler import TemporalReconciler


# ═══════════════════════════════════════════════════════════════════════════
#  1. reconcile_model_window — single model
# ═══════════════════════════════════════════════════════════════════════════

class ReconcileModelWindowTests(SimpleTestCase):
    """
    ``reconcile_model_window`` scans one model's history for records that
    became valid in a given time window and syncs each one.
    """

    def test_model_without_history_returns_zero(self):
        """Models that don't track history are skipped with 0 syncs."""
        model = type("NoHistory", (), {})
        result = TemporalReconciler.reconcile_model_window(
            model,
            start_time=timezone.now() - timedelta(hours=1),
            end_time=timezone.now(),
        )
        self.assertEqual(result, 0)

    @patch("lex.process_admin.utils.temporal_reconciler.BitemporalSynchronizer")
    def test_syncs_each_candidate_pk(self, MockSync):
        """Each distinct PK with a valid_from in the window is synced."""
        now = timezone.now()
        history_model = MagicMock()
        history_model.objects.filter.return_value.values_list.return_value.distinct.return_value = [10, 20, 30]

        model = MagicMock()
        model._meta = SimpleNamespace(pk=SimpleNamespace(name="id"))
        model.history = SimpleNamespace(model=history_model)

        result = TemporalReconciler.reconcile_model_window(
            model,
            start_time=now - timedelta(hours=1),
            end_time=now,
        )

        self.assertEqual(result, 3)
        self.assertEqual(MockSync.sync_record_for_model.call_count, 3)
        # Verify each PK was synced with the correct arguments
        MockSync.sync_record_for_model.assert_any_call(model, 10, history_model)
        MockSync.sync_record_for_model.assert_any_call(model, 20, history_model)
        MockSync.sync_record_for_model.assert_any_call(model, 30, history_model)

    @patch("lex.process_admin.utils.temporal_reconciler.BitemporalSynchronizer")
    def test_no_candidates_returns_zero(self, MockSync):
        """If no records have valid_from in the window, nothing is synced."""
        history_model = MagicMock()
        history_model.objects.filter.return_value.values_list.return_value.distinct.return_value = []

        model = MagicMock()
        model._meta = SimpleNamespace(pk=SimpleNamespace(name="id"))
        model.history = SimpleNamespace(model=history_model)

        result = TemporalReconciler.reconcile_model_window(
            model,
            start_time=timezone.now() - timedelta(hours=1),
            end_time=timezone.now(),
        )

        self.assertEqual(result, 0)
        MockSync.sync_record_for_model.assert_not_called()

    @patch("lex.process_admin.utils.temporal_reconciler.BitemporalSynchronizer")
    def test_filters_by_time_window(self, MockSync):
        """The queryset must filter valid_from between start and end."""
        now = timezone.now()
        start = now - timedelta(hours=2)

        history_model = MagicMock()
        history_model.objects.filter.return_value.values_list.return_value.distinct.return_value = []

        model = MagicMock()
        model._meta = SimpleNamespace(pk=SimpleNamespace(name="id"))
        model.history = SimpleNamespace(model=history_model)

        TemporalReconciler.reconcile_model_window(model, start, now)

        # Verify filter was called with the time window
        filter_kwargs = history_model.objects.filter.call_args[1]
        self.assertEqual(filter_kwargs["valid_from__gte"], start)
        self.assertEqual(filter_kwargs["valid_from__lte"], now)


# ═══════════════════════════════════════════════════════════════════════════
#  2. reconcile_changes_since — cross-model scan
# ═══════════════════════════════════════════════════════════════════════════

class ReconcileChangesSinceTests(SimpleTestCase):
    """
    ``reconcile_changes_since`` scans ALL registered Django models and
    reconciles any that track bitemporal history.
    """

    @patch("lex.process_admin.utils.temporal_reconciler.BitemporalSynchronizer")
    @patch("lex.process_admin.utils.temporal_reconciler.apps")
    @patch("django.db.connection")
    def test_scans_all_models_with_history(self, mock_conn, mock_apps, MockSync):
        """All models with a ``history`` attribute are scanned."""
        now = timezone.now()
        start = now - timedelta(hours=1)

        # Model WITH history
        history_model = MagicMock()
        history_model._meta = SimpleNamespace(db_table="app_investment_history")
        history_model.objects.filter.return_value.values_list.return_value.distinct.return_value = [1]

        model_with_history = MagicMock()
        model_with_history.__name__ = "Investment"
        model_with_history._meta = SimpleNamespace(pk=SimpleNamespace(name="id"))
        model_with_history.history = SimpleNamespace(model=history_model)

        # Model WITHOUT history
        model_without_history = MagicMock(spec=[])
        model_without_history.__name__ = "Config"

        mock_apps.get_models.return_value = [model_with_history, model_without_history]
        mock_conn.introspection.table_names.return_value = ["app_investment_history"]

        TemporalReconciler.reconcile_changes_since(start, now)

        MockSync.sync_record_for_model.assert_called_once_with(model_with_history, 1, history_model)

    @patch("lex.process_admin.utils.temporal_reconciler.BitemporalSynchronizer")
    @patch("lex.process_admin.utils.temporal_reconciler.apps")
    @patch("django.db.connection")
    def test_skips_models_without_history_table(self, mock_conn, mock_apps, MockSync):
        """If the history table doesn't exist in the DB yet, skip the model."""
        history_model = MagicMock()
        history_model._meta = SimpleNamespace(db_table="missing_table")

        model = MagicMock()
        model.__name__ = "Phantom"
        model._meta = SimpleNamespace(pk=SimpleNamespace(name="id"))
        model.history = SimpleNamespace(model=history_model)

        mock_apps.get_models.return_value = [model]
        mock_conn.introspection.table_names.return_value = ["other_table"]

        TemporalReconciler.reconcile_changes_since(
            timezone.now() - timedelta(hours=1),
            timezone.now(),
        )

        MockSync.sync_record_for_model.assert_not_called()

    @patch("lex.process_admin.utils.temporal_reconciler.BitemporalSynchronizer")
    @patch("lex.process_admin.utils.temporal_reconciler.apps")
    @patch("django.db.connection")
    def test_defaults_end_time_to_now(self, mock_conn, mock_apps, MockSync):
        """If ``end_time`` is None, it defaults to ``timezone.now()``."""
        mock_apps.get_models.return_value = []
        mock_conn.introspection.table_names.return_value = []

        # Should not raise even with end_time=None
        TemporalReconciler.reconcile_changes_since(timezone.now() - timedelta(hours=1))

        MockSync.sync_record_for_model.assert_not_called()
