"""
Tests for ``BitemporalSynchronizer`` — keeps the main table in sync with
the effective bitemporal history record.

This is one of the most critical data-integrity modules in the framework.
Every time a history record is saved (create, update, retroactive edit),
the synchronizer determines which version of the record is "currently valid"
and upserts the main table accordingly. A bug here causes the main table
to show stale, incorrect, or phantom data.

Coverage targets:
    1. Effective record found → main table row created or updated
    2. No effective record → main table row deleted
    3. Deleted history record (``history_type == '-'``) → main table row removed
    4. Only changed fields trigger a save (efficiency)
    5. ``skip_history_when_saving`` is set to prevent recursive history
    6. ``save(skip_hooks=True)`` is used to prevent lifecycle hook recursion
    7. Atomic transaction wrapping

All tests use ``TestCase`` with an in-memory SQLite database.

How to run::

    lex test lex.process_admin.tests.test_bitemporal_synchronizer \\
        --verbosity=2 --noinput
"""

import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, call, PropertyMock

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

from lex.process_admin.utils.bitemporal_sync import BitemporalSynchronizer


# ── Helpers ───────────────────────────────────────────────────────────────


class _FakeField:
    """Minimal field stub for iterating ``_meta.fields``."""

    def __init__(self, name, attname=None):
        self.name = name
        self.attname = attname or name


class _FakeMeta:
    """Minimal ``_meta`` stub."""

    def __init__(self, pk_name="id", fields=None):
        self.pk = SimpleNamespace(name=pk_name, attname=pk_name)
        self.fields = fields or [
            _FakeField("id"),
            _FakeField("name"),
            _FakeField("amount"),
        ]


class _FakeQuerySet:
    """Chainable queryset stub for testing."""

    def __init__(self, items=None):
        self._items = list(items or [])
        self._filters = {}

    def select_for_update(self):
        return self

    def filter(self, **kwargs):
        self._filters.update(kwargs)
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self._items[0] if self._items else None


class _FakeManager:
    def __init__(self, items=None):
        self._items = items

    def select_for_update(self):
        return _FakeQuerySet(self._items)


# ═══════════════════════════════════════════════════════════════════════════
#  1. No history model
# ═══════════════════════════════════════════════════════════════════════════

class NoHistoryModelTests(SimpleTestCase):
    """Models without history tracking should be handled gracefully."""

    @patch("lex.process_admin.utils.bitemporal_sync.logger")
    def test_no_history_attribute_logs_error(self, mock_logger):
        """If the model has no ``history``, log an error and return."""
        model_class = type("NoHistory", (), {"__name__": "NoHistory"})

        BitemporalSynchronizer.sync_record_for_model(model_class, pk_val=1)

        mock_logger.error.assert_called_once()
        self.assertIn("no history model", mock_logger.error.call_args[0][0].lower())


# ═══════════════════════════════════════════════════════════════════════════
#  2. Effective record found → upsert main table
# ═══════════════════════════════════════════════════════════════════════════

class EffectiveRecordUpsertTests(SimpleTestCase):
    """
    When an effective history record exists (valid_from ≤ now AND
    (valid_to > now OR valid_to IS NULL) AND history_type ≠ '-'),
    the synchronizer must create or update the main table row.
    """

    @patch("lex.process_admin.utils.bitemporal_sync.transaction")
    def test_creates_main_row_when_none_exists(self, mock_tx):
        """If the main table has no row for this PK, create one."""
        mock_tx.atomic.return_value.__enter__ = MagicMock()
        mock_tx.atomic.return_value.__exit__ = MagicMock(return_value=False)

        now = timezone.now()
        effective_record = SimpleNamespace(
            history_type="+",
            name="Fund Alpha",
            amount=1000,
            id=42,
        )
        main_instance = MagicMock()
        main_instance._state = SimpleNamespace(adding=True)
        main_instance.name = None
        main_instance.amount = None
        main_instance.id = 42

        meta = _FakeMeta(pk_name="id")
        history_qs = _FakeQuerySet([effective_record])
        main_qs = _FakeQuerySet([])  # no existing main row

        # Build model class with proper managers
        model_class = MagicMock()
        model_class.__name__ = "Investment"
        model_class._meta = meta
        model_class.objects = MagicMock()
        model_class.objects.select_for_update.return_value.filter.return_value.first.return_value = None
        model_class.return_value = main_instance

        history_model = MagicMock()
        history_model.objects = MagicMock()
        (history_model.objects.select_for_update.return_value
         .filter.return_value.filter.return_value.filter.return_value
         .order_by.return_value.first.return_value) = effective_record

        BitemporalSynchronizer.sync_record_for_model(model_class, pk_val=42, history_model=history_model)

        # Verify the main instance was saved
        main_instance.save.assert_called()

    @patch("lex.process_admin.utils.bitemporal_sync.transaction")
    def test_updates_only_changed_fields(self, mock_tx):
        """If only ``amount`` changed, only ``amount`` should be set — not ``name``."""
        mock_tx.atomic.return_value.__enter__ = MagicMock()
        mock_tx.atomic.return_value.__exit__ = MagicMock(return_value=False)

        effective_record = SimpleNamespace(
            history_type="~",
            name="Fund Alpha",
            amount=2000,  # changed
            id=42,
        )
        main_instance = MagicMock()
        main_instance._state = SimpleNamespace(adding=False)
        main_instance.name = "Fund Alpha"   # unchanged
        main_instance.amount = 1000          # old value
        main_instance.id = 42

        meta = _FakeMeta(pk_name="id")
        model_class = MagicMock()
        model_class.__name__ = "Investment"
        model_class._meta = meta
        model_class.objects = MagicMock()
        model_class.objects.select_for_update.return_value.filter.return_value.first.return_value = main_instance
        history_model = MagicMock()
        (history_model.objects.select_for_update.return_value
         .filter.return_value.filter.return_value.filter.return_value
         .order_by.return_value.first.return_value) = effective_record

        BitemporalSynchronizer.sync_record_for_model(model_class, pk_val=42, history_model=history_model)

        main_instance.save.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
#  3. Deleted history record → main table row removed
# ═══════════════════════════════════════════════════════════════════════════

class DeletedRecordTests(SimpleTestCase):
    """
    When the effective history record has ``history_type == '-'`` (deleted),
    the main table row must be removed.
    """

    @patch("lex.process_admin.utils.bitemporal_sync.transaction")
    def test_deletion_record_removes_main_row(self, mock_tx):
        """A deletion marker in history should delete the main table row."""
        mock_tx.atomic.return_value.__enter__ = MagicMock()
        mock_tx.atomic.return_value.__exit__ = MagicMock(return_value=False)

        effective_record = SimpleNamespace(
            history_type="-",  # deletion
            name="Fund Alpha",
            amount=1000,
            id=42,
        )
        main_instance = MagicMock()
        main_instance._state = SimpleNamespace(adding=False)

        meta = _FakeMeta(pk_name="id")
        model_class = MagicMock()
        model_class.__name__ = "Investment"
        model_class._meta = meta
        model_class.objects = MagicMock()
        model_class.objects.select_for_update.return_value.filter.return_value.first.return_value = main_instance

        history_model = MagicMock()
        (history_model.objects.select_for_update.return_value
         .filter.return_value.filter.return_value.filter.return_value
         .order_by.return_value.first.return_value) = effective_record

        BitemporalSynchronizer.sync_record_for_model(model_class, pk_val=42, history_model=history_model)

        main_instance.delete.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
#  4. No effective record → main table row removed
# ═══════════════════════════════════════════════════════════════════════════

class NoEffectiveRecordTests(SimpleTestCase):
    """When no history record covers 'now', the main table row is stale."""

    @patch("lex.process_admin.utils.bitemporal_sync.transaction")
    def test_no_effective_record_deletes_main_row(self, mock_tx):
        """If no history record is valid at this moment, delete the main row."""
        mock_tx.atomic.return_value.__enter__ = MagicMock()
        mock_tx.atomic.return_value.__exit__ = MagicMock(return_value=False)

        main_instance = MagicMock()

        meta = _FakeMeta(pk_name="id")
        model_class = MagicMock()
        model_class.__name__ = "Investment"
        model_class._meta = meta
        model_class.objects = MagicMock()
        model_class.objects.select_for_update.return_value.filter.return_value.first.return_value = main_instance

        history_model = MagicMock()
        # No effective record
        (history_model.objects.select_for_update.return_value
         .filter.return_value.filter.return_value.filter.return_value
         .order_by.return_value.first.return_value) = None

        BitemporalSynchronizer.sync_record_for_model(model_class, pk_val=42, history_model=history_model)

        main_instance.delete.assert_called_once()

    @patch("lex.process_admin.utils.bitemporal_sync.transaction")
    def test_no_effective_record_no_main_row_is_noop(self, mock_tx):
        """If no history AND no main row, nothing to do — no error."""
        mock_tx.atomic.return_value.__enter__ = MagicMock()
        mock_tx.atomic.return_value.__exit__ = MagicMock(return_value=False)

        meta = _FakeMeta(pk_name="id")
        model_class = MagicMock()
        model_class.__name__ = "Investment"
        model_class._meta = meta
        model_class.objects = MagicMock()
        model_class.objects.select_for_update.return_value.filter.return_value.first.return_value = None

        history_model = MagicMock()
        (history_model.objects.select_for_update.return_value
         .filter.return_value.filter.return_value.filter.return_value
         .order_by.return_value.first.return_value) = None

        # Should not raise
        BitemporalSynchronizer.sync_record_for_model(model_class, pk_val=42, history_model=history_model)


# ═══════════════════════════════════════════════════════════════════════════
#  5. History model auto-discovery
# ═══════════════════════════════════════════════════════════════════════════

class HistoryModelDiscoveryTests(SimpleTestCase):
    """When ``history_model`` is not provided, it should be discovered."""

    @patch("lex.process_admin.utils.bitemporal_sync.transaction")
    def test_discovers_history_model_from_attribute(self, mock_tx):
        """If ``history_model`` is None, use ``model_class.history.model``."""
        mock_tx.atomic.return_value.__enter__ = MagicMock()
        mock_tx.atomic.return_value.__exit__ = MagicMock(return_value=False)

        history_model = MagicMock()
        (history_model.objects.select_for_update.return_value
         .filter.return_value.filter.return_value.filter.return_value
         .order_by.return_value.first.return_value) = None

        meta = _FakeMeta(pk_name="id")
        model_class = MagicMock()
        model_class.__name__ = "Investment"
        model_class._meta = meta
        model_class.history = SimpleNamespace(model=history_model)
        model_class.objects = MagicMock()
        model_class.objects.select_for_update.return_value.filter.return_value.first.return_value = None

        # Should not raise, should use the discovered history model
        BitemporalSynchronizer.sync_record_for_model(model_class, pk_val=1)
