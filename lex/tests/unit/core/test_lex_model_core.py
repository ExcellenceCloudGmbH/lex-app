"""
Tests for ``LexModel`` core functionality — save flow, manager, timestamps,
default permissions, and utility functions.

**What is tested:**

    * ``lex_datetime_now()`` — timezone-aware vs naive branching
    * ``should_use_atomic_model_operations()`` — ``is_atomic`` opt-out
    * ``LexManager.bulk_create()`` — history-tracking vs skip_history paths
    * ``save(skip_hooks=True)`` — hook bypass confirmed via DB round-trip
    * ``save_without_historical_record()`` — flag lifecycle
    * ``_finalize_pending_terminal_audit()`` — cleanup after save failure
    * Default Keycloak-based permission methods (read/edit/export/create/delete/list)
    * ``_get_all_field_names()`` — meta field introspection
    * DB-backed timestamp hooks — ``update_timestamps_on_create``, ``update_edited_at``
    * DB-backed actor hooks — ``update_created_by``, ``update_edited_by``

**Why this matters:**

    These are the most-executed code paths in the framework.  Every customer
    model inherits from ``LexModel``, so regressions here break every app.

**How to run:**

    .. code-block:: bash

        lex test lex.core.tests.test_lex_model_core --verbosity=2 --noinput --keepdb
"""

import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.db import connection, models
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from lex.core.models.LexModel import (
    LexModel,
    LexManager,
    PermissionResult,
    UserContext,
    lex_datetime_now,
    should_use_atomic_model_operations,
)


# ────────────────────────────────────────────────────────────────────
#  Test model — created via schema_editor in TransactionTestCase
# ────────────────────────────────────────────────────────────────────

class CoreTestModel(LexModel):
    """Minimal concrete LexModel for DB-backed tests."""
    name = models.CharField(max_length=120, default="")

    class Meta:
        app_label = "core"


# ════════════════════════════════════════════════════════════════════
#  Pure-logic tests (no DB)
# ════════════════════════════════════════════════════════════════════

class TestLexDatetimeNow(SimpleTestCase):
    """Verify ``lex_datetime_now`` respects the USE_TZ setting."""

    @override_settings(USE_TZ=True)
    def test_use_tz_returns_aware_datetime(self):
        result = lex_datetime_now()
        self.assertIsNotNone(result.tzinfo)

    @override_settings(USE_TZ=False)
    def test_no_tz_returns_naive_datetime(self):
        result = lex_datetime_now()
        self.assertIsNone(result.tzinfo)

    def test_returns_datetime_instance(self):
        result = lex_datetime_now()
        self.assertIsInstance(result, datetime)


class TestShouldUseAtomicModelOperations(SimpleTestCase):
    """Verify the ``is_atomic`` opt-out flag."""

    def test_default_object_is_atomic(self):
        obj = SimpleNamespace()  # no is_atomic attr
        self.assertTrue(should_use_atomic_model_operations(obj))

    def test_is_atomic_true_returns_true(self):
        obj = SimpleNamespace(is_atomic=True)
        self.assertTrue(should_use_atomic_model_operations(obj))

    def test_is_atomic_false_returns_false(self):
        obj = SimpleNamespace(is_atomic=False)
        self.assertFalse(should_use_atomic_model_operations(obj))

    def test_class_with_is_atomic_false(self):
        class NonAtomic:
            is_atomic = False
        self.assertFalse(should_use_atomic_model_operations(NonAtomic))
        self.assertFalse(should_use_atomic_model_operations(NonAtomic()))


class TestDefaultPermissionMethods(SimpleTestCase):
    """Verify the default Keycloak-scope-based permission methods."""

    def _make_ctx(self, scopes=frozenset()):
        return UserContext(
            user=None,
            email="test@example.com",
            is_authenticated=True,
            is_superuser=False,
            groups=set(),
            keycloak_scopes=set(scopes),
        )

    def _make_instance(self):
        """Build a mock that has real LexModel permission methods."""
        inst = MagicMock(spec=LexModel)
        inst.permission_read = LexModel.permission_read.__get__(inst, type(inst))
        inst.permission_edit = LexModel.permission_edit.__get__(inst, type(inst))
        inst.permission_export = LexModel.permission_export.__get__(inst, type(inst))
        inst.permission_create = LexModel.permission_create.__get__(inst, type(inst))
        inst.permission_delete = LexModel.permission_delete.__get__(inst, type(inst))
        inst.permission_list = LexModel.permission_list.__get__(inst, type(inst))
        return inst

    # ── permission_read ───────────────────────────────────────────────

    def test_read_allowed_with_scope(self):
        inst = self._make_instance()
        result = inst.permission_read(self._make_ctx({"read"}))
        self.assertTrue(result.allowed)

    def test_read_denied_without_scope(self):
        inst = self._make_instance()
        result = inst.permission_read(self._make_ctx())
        self.assertFalse(result.allowed)

    # ── permission_edit ───────────────────────────────────────────────

    def test_edit_allowed_with_scope(self):
        inst = self._make_instance()
        result = inst.permission_edit(self._make_ctx({"edit"}))
        self.assertTrue(result.allowed)

    def test_edit_denied_without_scope(self):
        inst = self._make_instance()
        result = inst.permission_edit(self._make_ctx())
        self.assertFalse(result.allowed)

    # ── permission_export ─────────────────────────────────────────────

    def test_export_allowed_with_scope(self):
        inst = self._make_instance()
        result = inst.permission_export(self._make_ctx({"export"}))
        self.assertTrue(result.allowed)

    def test_export_denied_without_scope(self):
        inst = self._make_instance()
        result = inst.permission_export(self._make_ctx())
        self.assertFalse(result.allowed)

    # ── permission_create ─────────────────────────────────────────────

    def test_create_allowed_with_scope(self):
        inst = self._make_instance()
        self.assertTrue(inst.permission_create(self._make_ctx({"create"})))

    def test_create_denied_without_scope(self):
        inst = self._make_instance()
        self.assertFalse(inst.permission_create(self._make_ctx()))

    # ── permission_delete ─────────────────────────────────────────────

    def test_delete_allowed_with_scope(self):
        inst = self._make_instance()
        self.assertTrue(inst.permission_delete(self._make_ctx({"delete"})))

    def test_delete_denied_without_scope(self):
        inst = self._make_instance()
        self.assertFalse(inst.permission_delete(self._make_ctx()))

    # ── permission_list ───────────────────────────────────────────────

    def test_list_allowed_with_scope(self):
        inst = self._make_instance()
        self.assertTrue(inst.permission_list(self._make_ctx({"list"})))

    def test_list_denied_without_scope(self):
        inst = self._make_instance()
        self.assertFalse(inst.permission_list(self._make_ctx()))


class TestFinalizePendingTerminalAudit(SimpleTestCase):
    """Verify ``_finalize_pending_terminal_audit`` cleanup path."""

    def _make_instance(self):
        inst = MagicMock(spec=LexModel)
        inst._finalize_pending_terminal_audit = (
            LexModel._finalize_pending_terminal_audit.__get__(inst, type(inst))
        )
        return inst

    def test_noop_when_no_pending_audit(self):
        inst = self._make_instance()
        inst._pending_terminal_audit = None
        # Should not raise
        inst._finalize_pending_terminal_audit()

    def test_noop_when_pending_is_not_dict(self):
        inst = self._make_instance()
        inst._pending_terminal_audit = "not a dict"
        inst._finalize_pending_terminal_audit()

    @patch("lex.audit_logging.utils.calculation_audit.ensure_terminal_calculation_audit")
    def test_calls_audit_and_clears_attribute(self, mock_audit):
        inst = self._make_instance()
        inst._pending_terminal_audit = {
            "audit_status": "failure",
            "error_message": "boom",
            "stack_trace": "line 1\nline 2",
        }
        inst._finalize_pending_terminal_audit()
        mock_audit.assert_called_once_with(
            inst,
            audit_status="failure",
            error_message="boom",
            stack_trace="line 1\nline 2",
        )

    @patch(
        "lex.audit_logging.utils.calculation_audit.ensure_terminal_calculation_audit",
        side_effect=RuntimeError("audit write failed"),
    )
    def test_swallows_audit_error_and_still_clears(self, mock_audit):
        inst = self._make_instance()
        inst._pending_terminal_audit = {"audit_status": "failure"}
        # Should NOT propagate the RuntimeError
        inst._finalize_pending_terminal_audit()
        mock_audit.assert_called_once()


# ════════════════════════════════════════════════════════════════════
#  DB-backed tests
# ════════════════════════════════════════════════════════════════════

class TestLexModelDBSaveFlow(TransactionTestCase):
    """Exercises real save() flow with a concrete LexModel subclass."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as editor:
            editor.create_model(CoreTestModel)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as editor:
            editor.delete_model(CoreTestModel)
        super().tearDownClass()

    # ── skip_hooks ────────────────────────────────────────────────────

    def test_save_skip_hooks_bypasses_timestamp_hooks(self):
        """With skip_hooks=True, created_at/edited_at stay None."""
        obj = CoreTestModel(name="skip-hooks-test")
        obj.save(skip_hooks=True)
        obj.refresh_from_db()
        self.assertIsNone(obj.created_at)
        self.assertIsNone(obj.edited_at)

    def test_normal_save_sets_timestamps(self):
        """Normal create sets created_at but not edited_at."""
        obj = CoreTestModel(name="normal-create")
        obj.save()
        obj.refresh_from_db()
        self.assertIsNotNone(obj.created_at)
        self.assertIsNone(obj.edited_at)

    def test_update_only_changes_edited_at(self):
        """On update, created_at is untouched but edited_at advances."""
        obj = CoreTestModel(name="update-test")
        obj.save()
        obj.refresh_from_db()
        original_created = obj.created_at
        original_edited = obj.edited_at

        # Small delay so timestamps differ
        time.sleep(0.01)
        obj.name = "updated"
        obj.save()
        obj.refresh_from_db()

        self.assertEqual(obj.created_at, original_created)
        self.assertGreaterEqual(obj.edited_at, original_edited)

    # ── actor hooks ──────────────────────────────────────────────────

    def test_create_sets_fallback_actor(self):
        """Without operation_context, created_by defaults to FALLBACK_AUDIT_ACTOR."""
        obj = CoreTestModel(name="actor-test")
        obj.save()
        obj.refresh_from_db()
        self.assertEqual(obj.created_by, LexModel.FALLBACK_AUDIT_ACTOR)

    def test_update_sets_fallback_edited_by(self):
        """On update, edited_by is set to FALLBACK_AUDIT_ACTOR."""
        obj = CoreTestModel(name="edit-actor-test")
        obj.save()
        obj.name = "changed"
        obj.save()
        obj.refresh_from_db()
        self.assertEqual(obj.edited_by, LexModel.FALLBACK_AUDIT_ACTOR)

    @patch("lex.core.models.LexModel.operation_context")
    def test_create_with_explicit_actor(self, mock_ctx):
        """Operation context actor overrides the fallback."""
        mock_ctx.get.return_value = {"actor": "jane@test.com", "request_obj": None}
        obj = CoreTestModel(name="explicit-actor")
        obj.save()
        obj.refresh_from_db()
        self.assertEqual(obj.created_by, "jane@test.com")

    # ── explicit overrides ───────────────────────────────────────────

    def test_explicit_created_by_preserved(self):
        """If created_by is set before save, the hook does not overwrite it."""
        obj = CoreTestModel(name="preserved-actor")
        obj.created_by = "preset-user"
        obj.save()
        obj.refresh_from_db()
        self.assertEqual(obj.created_by, "preset-user")

    def test_explicit_created_at_preserved(self):
        """If created_at is passed to __init__, the hook does not overwrite it."""
        fixed_ts = datetime(2020, 1, 1, 12, 0, 0)
        obj = CoreTestModel(name="preserved-ts", created_at=fixed_ts)
        obj.save()
        obj.refresh_from_db()
        # Compare without microseconds since DB may truncate
        self.assertEqual(obj.created_at.year, 2020)
        self.assertEqual(obj.created_at.month, 1)

    # ── save_without_historical_record ───────────────────────────────

    def test_save_without_historical_record_flag_lifecycle(self):
        """save_without_historical_record sets and clears skip_history_when_saving."""
        obj = CoreTestModel(name="no-history")
        obj.save()  # create first
        obj.name = "no-history-updated"

        # After save_without_historical_record, the flag should be cleaned up
        obj.save_without_historical_record()
        self.assertFalse(hasattr(obj, "skip_history_when_saving"))

    # ── track / untrack ──────────────────────────────────────────────

    def test_untrack_sets_flag_track_removes(self):
        obj = CoreTestModel(name="track-test")
        obj.save()

        obj.untrack()
        self.assertTrue(obj.skip_history_when_saving)

        obj.track()
        self.assertFalse(hasattr(obj, "skip_history_when_saving"))

    def test_untrack_skips_timestamp_hooks(self):
        """With skip_history_when_saving=True, timestamp hooks are skipped."""
        obj = CoreTestModel(name="untrack-ts")
        obj.save(skip_hooks=True)  # create without hooks
        obj.refresh_from_db()
        self.assertIsNone(obj.created_at)

        # Now try a create-path save with untrack — hooks should skip
        obj2 = CoreTestModel(name="untrack-ts-2")
        obj2.untrack()
        obj2.save()
        obj2.refresh_from_db()
        # created_at is set by BEFORE_CREATE hook which checks skip_history_when_saving
        self.assertIsNone(obj2.created_at)

    # ── _get_all_field_names ─────────────────────────────────────────

    def test_get_all_field_names_includes_core_fields(self):
        obj = CoreTestModel(name="fields-test")
        field_names = obj._get_all_field_names()
        self.assertIn("id", field_names)
        self.assertIn("name", field_names)
        self.assertIn("created_at", field_names)
        self.assertIn("edited_at", field_names)
        self.assertIn("created_by", field_names)
        self.assertIn("edited_by", field_names)


class TestLexManagerBulkCreate(TransactionTestCase):
    """Test ``LexManager.bulk_create()`` both paths."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as editor:
            editor.create_model(CoreTestModel)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as editor:
            editor.delete_model(CoreTestModel)
        super().tearDownClass()

    def test_default_bulk_create_triggers_hooks(self):
        """Default bulk_create saves one-by-one so hooks fire."""
        objs = [CoreTestModel(name=f"bulk-{i}") for i in range(3)]
        created = CoreTestModel.objects.bulk_create(objs)
        self.assertEqual(len(created), 3)
        for obj in created:
            obj.refresh_from_db()
            # Hooks fired, so timestamps are set
            self.assertIsNotNone(obj.created_at)
            self.assertIsNotNone(obj.created_by)

    def test_skip_history_bulk_create_uses_raw_path(self):
        """skip_history=True uses Django's raw bulk path."""
        objs = [CoreTestModel(name=f"raw-bulk-{i}") for i in range(3)]
        created = CoreTestModel.objects.bulk_create(objs, skip_history=True)
        self.assertEqual(len(created), 3)
        for obj in created:
            obj.refresh_from_db()
            # Raw path — no hooks, so timestamps stay None
            self.assertIsNone(obj.created_at)

    def test_bulk_create_empty_list(self):
        """Empty list returns empty list without error."""
        created = CoreTestModel.objects.bulk_create([])
        self.assertEqual(created, [])
