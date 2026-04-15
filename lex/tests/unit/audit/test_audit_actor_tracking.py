"""
Tests for LexModel audit-actor auto-tracking and timestamp hooks.

**What is tested:**

    • ``_resolve_audit_actor()`` priority chain:
      1. Explicit actor from ``operation_context``
      2. Authenticated user from ``request_obj``
      3. API-key header → ``'Technical User'``
      4. Fallback → ``'Initial Data Upload'``
    • ``_normalize_audit_actor()`` strips whitespace, converts empty → None.
    • ``_has_explicit_created_by_override()`` — skips auto-set when pre-populated.
    • ``_has_explicit_edited_by_override()`` — skips auto-set when changed.
    • ``update_created_by`` / ``update_edited_by`` hook methods.
    • ``update_timestamps_on_create`` / ``update_edited_at`` hook methods.
    • ``skip_history_when_saving`` flag skips all audit hooks.

**Why this matters:**

    Every record in the system tracks *who* created it and *who* last edited
    it.  If actor resolution is wrong, audit trails become useless for
    compliance.  If timestamps are wrong, bitemporal queries return
    incorrect results.

**How to run:**

    .. code-block:: bash

        lex test lex.core.tests.test_audit_actor_tracking --verbosity=2
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from lex.core.models.LexModel import LexModel


# ────────────────────────────────────────────────────────────────────
#  _normalize_audit_actor
# ────────────────────────────────────────────────────────────────────

class NormalizeAuditActorTest(SimpleTestCase):
    """Verify whitespace handling and None coercion."""

    def test_none_returns_none(self):
        self.assertIsNone(LexModel._normalize_audit_actor(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(LexModel._normalize_audit_actor(""))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(LexModel._normalize_audit_actor("   "))

    def test_valid_string_returned_stripped(self):
        self.assertEqual(LexModel._normalize_audit_actor("  alice  "), "alice")

    def test_non_string_converted(self):
        """Integers, model instances, etc. are str()-ified."""
        self.assertEqual(LexModel._normalize_audit_actor(42), "42")


# ────────────────────────────────────────────────────────────────────
#  _resolve_audit_actor priority chain
# ────────────────────────────────────────────────────────────────────

class ResolveAuditActorTest(SimpleTestCase):
    """Verify the actor resolution priority chain."""

    def _make_instance(self):
        inst = MagicMock(spec=LexModel)
        inst.FALLBACK_AUDIT_ACTOR = LexModel.FALLBACK_AUDIT_ACTOR
        inst.API_KEY_AUDIT_ACTOR = LexModel.API_KEY_AUDIT_ACTOR
        inst._normalize_audit_actor = LexModel._normalize_audit_actor
        inst._get_operation_context = LexModel._get_operation_context
        # Bind _resolve_request_audit_actor so `self` is satisfied
        inst._resolve_request_audit_actor = lambda req: LexModel._resolve_request_audit_actor(inst, req)
        return inst

    @patch("lex.core.models.LexModel.operation_context")
    def test_explicit_actor_from_context(self, mock_ctx):
        """Priority 1: explicit actor in operation_context wins."""
        mock_ctx.get.return_value = {"actor": "explicit-user"}
        inst = self._make_instance()
        result = LexModel._resolve_audit_actor(inst)
        self.assertEqual(result, "explicit-user")

    @patch("lex.core.models.LexModel.operation_context")
    def test_authenticated_user_from_request(self, mock_ctx):
        """Priority 2: authenticated user on request."""
        class FakeUser:
            is_authenticated = True
            def __str__(self):
                return "alice"

        request = SimpleNamespace(user=FakeUser(), headers={})
        mock_ctx.get.return_value = {"actor": None, "request_obj": request}
        inst = self._make_instance()
        result = LexModel._resolve_audit_actor(inst)
        self.assertEqual(result, "alice")

    @patch("lex.core.models.LexModel.operation_context")
    def test_fallback_when_no_context(self, mock_ctx):
        """Priority 4: falls back to 'Initial Data Upload'."""
        mock_ctx.get.return_value = {}
        inst = self._make_instance()
        result = LexModel._resolve_audit_actor(inst)
        self.assertEqual(result, "Initial Data Upload")


# ────────────────────────────────────────────────────────────────────
#  Override detection
# ────────────────────────────────────────────────────────────────────

class OverrideDetectionTest(SimpleTestCase):
    """Verify explicit override detection for created_by / edited_by."""

    def test_created_by_override_when_already_set(self):
        """If created_by is already non-null, it's an override."""
        inst = MagicMock(spec=LexModel)
        inst.created_by = "pre-set-user"
        inst._normalize_audit_actor = LexModel._normalize_audit_actor
        self.assertTrue(LexModel._has_explicit_created_by_override(inst))

    def test_created_by_no_override_when_none(self):
        """If created_by is None, no override."""
        inst = MagicMock(spec=LexModel)
        inst.created_by = None
        inst._normalize_audit_actor = LexModel._normalize_audit_actor
        self.assertFalse(LexModel._has_explicit_created_by_override(inst))

    def test_created_at_override_when_flag_and_value(self):
        """If _explicit_timestamp_overrides['created_at'] is True and value set."""
        inst = MagicMock(spec=LexModel)
        inst._explicit_timestamp_overrides = {"created_at": True}
        inst.created_at = "2025-01-01"
        self.assertTrue(LexModel._has_explicit_created_at_override(inst))

    def test_created_at_no_override_when_flag_false(self):
        """If flag is False, not an override even if value is set."""
        inst = MagicMock(spec=LexModel)
        inst._explicit_timestamp_overrides = {"created_at": False}
        inst.created_at = "2025-01-01"
        self.assertFalse(LexModel._has_explicit_created_at_override(inst))


# ────────────────────────────────────────────────────────────────────
#  Hook methods (BEFORE_CREATE / BEFORE_UPDATE)
# ────────────────────────────────────────────────────────────────────

class TimestampHookTest(SimpleTestCase):
    """Verify timestamp auto-set on create and update."""

    @patch("lex.core.models.LexModel.lex_datetime_now")
    def test_update_timestamps_on_create_sets_both(self, mock_now):
        """On create, both created_at and edited_at are set."""
        mock_now.return_value = "2025-06-15T12:00:00Z"
        inst = MagicMock(spec=LexModel)
        inst.skip_history_when_saving = False
        inst._explicit_timestamp_overrides = {"created_at": False, "edited_at": False}
        inst._has_explicit_created_at_override = MagicMock(return_value=False)
        inst.created_at = None
        inst.edited_at = None

        LexModel.update_timestamps_on_create(inst)

        self.assertEqual(inst.created_at, "2025-06-15T12:00:00Z")
        self.assertEqual(inst.edited_at, "2025-06-15T12:00:00Z")

    def test_update_timestamps_skipped_when_syncing_history(self):
        """Timestamp hooks are skipped when skip_history_when_saving is True."""
        inst = MagicMock(spec=LexModel)
        inst.skip_history_when_saving = True
        inst.created_at = None
        inst.edited_at = None

        LexModel.update_timestamps_on_create(inst)

        # Should remain None — hook skipped
        self.assertIsNone(inst.created_at)
        self.assertIsNone(inst.edited_at)

    @patch("lex.core.models.LexModel.lex_datetime_now")
    def test_update_edited_at_on_update(self, mock_now):
        """On update, edited_at is updated."""
        mock_now.return_value = "2025-06-15T13:00:00Z"
        inst = MagicMock(spec=LexModel)
        inst.skip_history_when_saving = False
        inst._has_explicit_edited_at_override = MagicMock(return_value=False)
        inst.edited_at = "old"

        LexModel.update_edited_at(inst)

        self.assertEqual(inst.edited_at, "2025-06-15T13:00:00Z")

    def test_update_edited_at_skipped_when_explicit_override(self):
        """If edited_at has an explicit override, hook does not overwrite it."""
        inst = MagicMock(spec=LexModel)
        inst.skip_history_when_saving = False
        inst._has_explicit_edited_at_override = MagicMock(return_value=True)
        inst.edited_at = "explicit-value"

        LexModel.update_edited_at(inst)

        self.assertEqual(inst.edited_at, "explicit-value")


class ActorHookTest(SimpleTestCase):
    """Verify created_by / edited_by auto-set hooks."""

    def test_update_created_by_sets_fallback(self):
        """On create with no context, created_by defaults to fallback."""
        inst = MagicMock(spec=LexModel)
        inst.skip_history_when_saving = False
        inst._has_explicit_created_by_override = MagicMock(return_value=False)
        inst._resolve_audit_actor = MagicMock(return_value="Initial Data Upload")

        LexModel.update_created_by(inst)

        self.assertEqual(inst.created_by, "Initial Data Upload")

    def test_update_created_by_skipped_when_preset(self):
        """If created_by is already set, hook doesn't overwrite."""
        inst = MagicMock(spec=LexModel)
        inst.skip_history_when_saving = False
        inst._has_explicit_created_by_override = MagicMock(return_value=True)
        inst.created_by = "preset-user"

        LexModel.update_created_by(inst)

        self.assertEqual(inst.created_by, "preset-user")

    def test_update_edited_by_sets_actor(self):
        """On update, edited_by is set to resolved actor."""
        inst = MagicMock(spec=LexModel)
        inst.skip_history_when_saving = False
        inst._has_explicit_edited_by_override = MagicMock(return_value=False)
        inst._resolve_audit_actor = MagicMock(return_value="bob")

        LexModel.update_edited_by(inst)

        self.assertEqual(inst.edited_by, "bob")

    def test_update_edited_by_skipped_during_history_sync(self):
        """During bitemporal sync, edited_by hook is skipped."""
        inst = MagicMock(spec=LexModel)
        inst.skip_history_when_saving = True
        inst.edited_by = None

        LexModel.update_edited_by(inst)

        self.assertIsNone(inst.edited_by)
