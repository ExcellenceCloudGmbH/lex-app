"""
Tests for ``HistoryModelEntry`` — the bitemporal history timeline API endpoint.

This endpoint is what the frontend calls when a user opens the "History" tab
on any record. It returns the full audit trail — every version of the record,
who changed it, when, and optionally the system-time (meta-history) snapshot.

Coverage targets:
    1. Model without history → 400 error
    2. Record serialization — ``_serialize_record`` field mapping
    3. User info extraction — ``_get_user_info`` with/without user
    4. Snapshot generation — with serializer / without (field iteration)
    5. System history — prefetched meta-history records
    6. Control field filtering — bitemporal control fields excluded from snapshot

All tests are pure-unit (no database, no HTTP) and use ``SimpleTestCase``.

How to run::

    lex test lex.process_admin.tests.test_history_endpoint \\
        --verbosity=2 --noinput
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase

from lex.api.views.model_entries.History import HistoryModelEntry


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_view():
    """Create a bare HistoryModelEntry for calling utility methods."""
    return HistoryModelEntry()


def _fake_field(name, value=None):
    """Build a minimal meta field."""
    return SimpleNamespace(name=name)


def _make_history_record(
    history_id=1,
    valid_from=None,
    valid_to=None,
    history_type="+",
    history_change_reason="Created",
    history_user_id=None,
    history_user=None,
    extra_fields=None,
):
    """Build a fake history record with configurable fields."""
    record = SimpleNamespace(
        history_id=history_id,
        valid_from=valid_from or datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc),
        valid_to=valid_to,
        history_type=history_type,
        history_change_reason=history_change_reason,
        history_user_id=history_user_id,
        history_user=history_user,
    )
    # Add extra data fields
    for k, v in (extra_fields or {}).items():
        setattr(record, k, v)
    return record


# ═══════════════════════════════════════════════════════════════════════════
#  1. _get_user_info
# ═══════════════════════════════════════════════════════════════════════════

class GetUserInfoTests(SimpleTestCase):
    """``_get_user_info`` extracts human-readable user information."""

    def test_no_user_id_returns_none(self):
        """If history_user_id is None, there's no user to show."""
        view = _make_view()
        record = _make_history_record(history_user_id=None)
        self.assertIsNone(view._get_user_info(record))

    def test_user_id_but_no_user_object(self):
        """Orphaned user reference — show the ID with fallback name."""
        view = _make_view()
        record = _make_history_record(history_user_id=42, history_user=None)
        result = view._get_user_info(record)
        self.assertEqual(result["id"], 42)
        self.assertEqual(result["name"], "Unknown User")

    def test_user_with_name(self):
        """Full user object with first/last name."""
        view = _make_view()
        user = SimpleNamespace(
            id=7,
            first_name="Jane",
            last_name="Doe",
            email="jane@fund.com",
            username="jdoe",
        )
        record = _make_history_record(history_user_id=7, history_user=user)
        result = view._get_user_info(record)
        self.assertEqual(result["id"], 7)
        self.assertEqual(result["name"], "Jane Doe")
        self.assertEqual(result["email"], "jane@fund.com")

    def test_user_without_name_falls_back_to_username(self):
        """If first_name and last_name are empty, use username."""
        view = _make_view()
        user = SimpleNamespace(
            id=8,
            first_name="",
            last_name="",
            email="",
            username="system_bot",
        )
        record = _make_history_record(history_user_id=8, history_user=user)
        result = view._get_user_info(record)
        self.assertEqual(result["name"], "system_bot")


# ═══════════════════════════════════════════════════════════════════════════
#  2. _serialize_record
# ═══════════════════════════════════════════════════════════════════════════

class SerializeRecordTests(SimpleTestCase):
    """``_serialize_record`` builds the history timeline entry dict."""

    def test_includes_all_required_keys(self):
        """Every history entry must have these keys."""
        view = _make_view()
        record = _make_history_record(
            history_id=5,
            history_type="+",
            history_change_reason="Initial creation",
        )
        # Mock out sub-methods to isolate
        view._get_user_info = MagicMock(return_value=None)
        view._get_snapshot = MagicMock(return_value={"name": "Fund A"})
        view._get_system_history = MagicMock(return_value=[])

        result = view._serialize_record(record)

        self.assertIn("history_id", result)
        self.assertIn("valid_from", result)
        self.assertIn("valid_to", result)
        self.assertIn("history_type", result)
        self.assertIn("change_reason", result)
        self.assertIn("user", result)
        self.assertIn("snapshot", result)
        self.assertIn("system_history", result)

    def test_maps_fields_correctly(self):
        view = _make_view()
        ts = datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc)
        record = _make_history_record(
            history_id=99,
            valid_from=ts,
            valid_to=None,
            history_type="~",
            history_change_reason="Updated amount",
        )
        view._get_user_info = MagicMock(return_value={"id": 1, "name": "Admin"})
        view._get_snapshot = MagicMock(return_value={})
        view._get_system_history = MagicMock(return_value=[])

        result = view._serialize_record(record)

        self.assertEqual(result["history_id"], 99)
        self.assertEqual(result["valid_from"], ts)
        self.assertIsNone(result["valid_to"])
        self.assertEqual(result["history_type"], "~")
        self.assertEqual(result["change_reason"], "Updated amount")


# ═══════════════════════════════════════════════════════════════════════════
#  3. _get_snapshot — without serializer
# ═══════════════════════════════════════════════════════════════════════════

def _make_record_with_meta(fields_spec, **field_values):
    """Create a record instance whose __class__._meta.fields is controllable.

    ``SimpleNamespace.__class__`` cannot be reassigned, so we dynamically
    create a real class with the required ``_meta`` and instantiate it.
    """
    meta_fields = [_fake_field(name) for name in fields_spec]
    cls = type("FakeHistory", (), {})
    cls._meta = SimpleNamespace(fields=meta_fields)
    instance = cls()
    for k, v in field_values.items():
        setattr(instance, k, v)
    return instance


class GetSnapshotTests(SimpleTestCase):
    """
    ``_get_snapshot`` generates the data payload for each history entry.
    When no serializer is available, it iterates model fields and
    excludes bitemporal control fields.
    """

    def test_excludes_control_fields(self):
        """Bitemporal control fields (history_id, valid_from, etc.) must not
        appear in the snapshot — they're already top-level."""
        view = _make_view()

        record = _make_record_with_meta(
            ["name", "amount", "history_id", "valid_from", "valid_to",
             "history_type", "history_change_reason"],
            name="Fund Alpha",
            amount=50000,
            history_id=1,
            valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
            valid_to=None,
            history_type="+",
            history_change_reason="created",
            history_user=None,
            history_user_id=None,
            history_relation=None,
        )

        result = view._get_snapshot(record)

        self.assertIn("name", result)
        self.assertIn("amount", result)
        self.assertNotIn("history_id", result)
        self.assertNotIn("valid_from", result)
        self.assertNotIn("history_type", result)

    def test_datetime_values_are_isoformatted(self):
        """Datetime values must be serialized as ISO strings."""
        view = _make_view()
        ts = datetime(2024, 6, 15, 12, 30, tzinfo=timezone.utc)
        record = _make_record_with_meta(["created_at"], created_at=ts)

        result = view._get_snapshot(record)

        self.assertIsInstance(result["created_at"], str)
        self.assertIn("2024-06-15", result["created_at"])

    def test_uses_serializer_when_provided(self):
        """If a serializer class is available, it takes precedence."""
        view = _make_view()
        record = SimpleNamespace(name="Fund A")

        mock_serializer = MagicMock()
        mock_serializer.return_value.data = {"name": "Fund A", "computed": True}

        result = view._get_snapshot(
            record,
            serializer_class=mock_serializer,
            serializer_context={"request": None},
        )

        self.assertEqual(result["name"], "Fund A")
        self.assertTrue(result["computed"])

    def test_non_primitive_values_stringified(self):
        """Non-serializable values (like model instances) become strings."""
        view = _make_view()
        record = _make_record_with_meta(
            ["owner"],
            owner=SimpleNamespace(name="Alice"),
        )

        result = view._get_snapshot(record)

        self.assertIsInstance(result["owner"], str)


# ═══════════════════════════════════════════════════════════════════════════
#  4. _get_system_history
# ═══════════════════════════════════════════════════════════════════════════

class GetSystemHistoryTests(SimpleTestCase):
    """``_get_system_history`` extracts Level 2 meta-history records."""

    def test_no_meta_history_returns_empty(self):
        """Records without meta-history tracking return []."""
        view = _make_view()
        record = SimpleNamespace()  # no meta_history attribute
        self.assertEqual(view._get_system_history(record), [])

    def test_extracts_meta_history_records(self):
        """Each meta-history entry maps to sys_from, sys_to, status, etc."""
        view = _make_view()
        meta_1 = SimpleNamespace(
            sys_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
            sys_to=datetime(2024, 6, 1, tzinfo=timezone.utc),
            meta_task_status="INITIAL",
            meta_task_name=None,
            meta_history_change_reason="Initial load",
        )
        meta_2 = SimpleNamespace(
            sys_from=datetime(2024, 6, 1, tzinfo=timezone.utc),
            sys_to=None,
            meta_task_status="CORRECTED",
            meta_task_name="recalculate_nav",
            meta_history_change_reason="Retroactive fix",
        )
        record = SimpleNamespace()
        record.meta_history = MagicMock()
        record.meta_history.all.return_value = [meta_1, meta_2]

        result = view._get_system_history(record)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["task_status"], "INITIAL")
        self.assertEqual(result[1]["task_status"], "CORRECTED")
        self.assertEqual(result[1]["task_name"], "recalculate_nav")
        self.assertEqual(result[1]["change_reason"], "Retroactive fix")


# ═══════════════════════════════════════════════════════════════════════════
#  5. list() — model without history
# ═══════════════════════════════════════════════════════════════════════════

class ListEndpointTests(SimpleTestCase):
    """Test the main ``list()`` method's validation and routing."""

    def test_model_without_history_returns_400(self):
        """If the model doesn't track history, return a clear error."""
        view = _make_view()
        request = MagicMock()

        model_class = type("NoHistory", (), {})
        model_container = MagicMock()
        model_container.model_class = model_class

        response = view.list(
            request,
            model_container=model_container,
            pk=1,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("does not track history", response.data["error"])
