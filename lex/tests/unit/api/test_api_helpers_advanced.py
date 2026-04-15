"""
Tests for ``lex.api.utils.helpers`` — model resolution, shadow instances, permission checks.

**What is tested:**

    * ``_get_field_map()`` — concrete-field cache for a model class
    * ``resolve_target_model()`` — content_type → model class, resource fallback
    * ``build_shadow_instance()`` — audit-log payload → unsaved model instance
    * ``can_read_from_payload()`` — permission check on shadow instance

**Why this matters:**

    These functions power the audit-log permission layer.  If ``resolve_target_model``
    returns the wrong class, users see other tenants' audit entries.  If
    ``build_shadow_instance`` fails silently, permission checks fall through to
    allow-by-default — a security gap.

**How to run:**

    .. code-block:: bash

        lex test lex.tests.test_api_helpers_advanced --verbosity=2 --noinput
"""

from unittest.mock import MagicMock, patch, PropertyMock

from django.db import models
from django.test import SimpleTestCase

from lex.api.utils.helpers import (
    _get_field_map,
    resolve_target_model,
    build_shadow_instance,
    can_read_from_payload,
)


# ─── helpers ──────────────────────────────────────────────────────────

def _make_audit_log(content_type_id=None, resource=None, payload=None):
    """Minimal audit log stub."""
    log = MagicMock()
    log.content_type_id = content_type_id
    log.resource = resource
    log.payload = payload
    log._state = MagicMock()
    log._state.db = "default"
    return log


# ════════════════════════════════════════════════════════════════════════
#  _get_field_map
# ════════════════════════════════════════════════════════════════════════

class TestGetFieldMap(SimpleTestCase):
    """Verify field map caches concrete fields by name."""

    def test_returns_concrete_fields(self):
        """Field map keys are concrete field names from _meta."""
        from django.contrib.auth.models import User
        field_map = _get_field_map(User)
        self.assertIn("id", field_map)
        self.assertIn("username", field_map)
        # ManyToMany fields are NOT concrete
        self.assertNotIn("groups", field_map)

    def test_caches_result(self):
        """Second call returns same dict object (cached)."""
        from django.contrib.auth.models import User
        first = _get_field_map(User)
        second = _get_field_map(User)
        self.assertIs(first, second)


# ════════════════════════════════════════════════════════════════════════
#  resolve_target_model
# ════════════════════════════════════════════════════════════════════════

class TestResolveTargetModel(SimpleTestCase):
    """Verify model resolution from audit log entries."""

    @patch("lex.api.utils.helpers.safe_get_content_type")
    def test_resolves_via_content_type(self, mock_ct):
        """When content_type_id is set, uses ContentType lookup."""
        mock_model_class = MagicMock()
        mock_ct_obj = MagicMock()
        mock_ct_obj.model_class.return_value = mock_model_class
        mock_ct.return_value = mock_ct_obj

        log = _make_audit_log(content_type_id=42)
        result = resolve_target_model(log)

        mock_ct.assert_called_once()
        self.assertEqual(result, mock_model_class)

    @patch("lex.api.utils.helpers.safe_get_content_type", side_effect=Exception("CT not found"))
    @patch("lex.api.utils.helpers._get_model_lookup")
    def test_falls_back_to_resource_name(self, mock_lookup, _mock_ct):
        """When content_type lookup fails, falls back to resource string."""
        mock_model = MagicMock()
        mock_lookup.return_value = {"mymodel": mock_model}

        log = _make_audit_log(content_type_id=99, resource="MyModel")
        result = resolve_target_model(log)

        self.assertEqual(result, mock_model)

    def test_returns_none_when_no_identifiers(self):
        """No content_type_id and no resource → None."""
        log = _make_audit_log(content_type_id=None, resource=None)
        result = resolve_target_model(log)
        self.assertIsNone(result)

    @patch("lex.api.utils.helpers._get_model_lookup")
    def test_resource_lookup_is_case_insensitive(self, mock_lookup):
        """Resource name lowered before lookup."""
        mock_model = MagicMock()
        mock_lookup.return_value = {"cashflow": mock_model}

        log = _make_audit_log(resource="CashFlow")
        result = resolve_target_model(log)
        self.assertEqual(result, mock_model)

    @patch("lex.api.utils.helpers._get_model_lookup")
    def test_unknown_resource_returns_none(self, mock_lookup):
        """Resource string not in lookup → None."""
        mock_lookup.return_value = {}
        log = _make_audit_log(resource="NonExistent")
        result = resolve_target_model(log)
        self.assertIsNone(result)


# ════════════════════════════════════════════════════════════════════════
#  build_shadow_instance
# ════════════════════════════════════════════════════════════════════════

class TestBuildShadowInstance(SimpleTestCase):
    """Verify shadow instance creation from audit-log payloads."""

    def test_empty_payload_returns_none(self):
        from django.contrib.auth.models import User
        result = build_shadow_instance(User, {})
        self.assertIsNone(result)

    def test_none_payload_returns_none(self):
        from django.contrib.auth.models import User
        result = build_shadow_instance(User, None)
        self.assertIsNone(result)

    def test_builds_instance_with_known_fields(self):
        """Payload with valid field names creates an unsaved instance."""
        from django.contrib.auth.models import User
        payload = {"username": "testuser", "email": "test@example.com"}
        result = build_shadow_instance(User, payload)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, User)
        self.assertEqual(result.username, "testuser")
        self.assertEqual(result.email, "test@example.com")

    def test_ignores_unknown_fields(self):
        """Payload keys not in _meta.concrete_fields are silently ignored."""
        from django.contrib.auth.models import User
        payload = {"username": "testuser", "bogus_field": "ignored"}
        result = build_shadow_instance(User, payload)
        self.assertIsNotNone(result)
        self.assertEqual(result.username, "testuser")
        self.assertFalse(hasattr(result, "bogus_field"))

    def test_includes_pk_from_payload(self):
        """If payload has pk field, shadow instance gets it."""
        from django.contrib.auth.models import User
        payload = {"id": 99, "username": "testuser"}
        result = build_shadow_instance(User, payload)
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, 99)

    def test_exception_returns_none(self):
        """If model constructor raises, returns None gracefully."""
        mock_model = MagicMock(side_effect=TypeError("bad constructor"))
        mock_model.__name__ = "BrokenModel"
        mock_model._meta = MagicMock()
        mock_model._meta.concrete_fields = []
        mock_model._meta.pk = MagicMock()
        mock_model._meta.pk.name = "id"

        result = build_shadow_instance(mock_model, {"id": 1})
        self.assertIsNone(result)


# ════════════════════════════════════════════════════════════════════════
#  can_read_from_payload
# ════════════════════════════════════════════════════════════════════════

class TestCanReadFromPayload(SimpleTestCase):
    """Verify permission check on shadow instances from audit logs."""

    @patch("lex.api.utils.helpers.resolve_target_model", return_value=None)
    def test_unresolvable_model_allows_by_default(self, _):
        """If model can't be resolved, allow (preserve default)."""
        request = MagicMock()
        log = _make_audit_log()
        result = can_read_from_payload(request, log)
        self.assertTrue(result)

    @patch("lex.api.utils.helpers.build_shadow_instance", return_value=None)
    @patch("lex.api.utils.helpers.resolve_target_model")
    def test_no_shadow_instance_allows_by_default(self, mock_resolve, _):
        """If shadow instance can't be built, allow (preserve default)."""
        mock_resolve.return_value = MagicMock()
        request = MagicMock()
        log = _make_audit_log(payload={})
        result = can_read_from_payload(request, log)
        self.assertTrue(result)

    @patch("lex.api.utils.helpers.build_shadow_instance")
    @patch("lex.api.utils.helpers.resolve_target_model")
    def test_permission_read_allowed(self, mock_resolve, mock_build):
        """permission_read returns allowed=True → can_read is True."""
        mock_instance = MagicMock()
        mock_instance.permission_read.return_value = MagicMock(allowed=True)
        mock_build.return_value = mock_instance
        mock_resolve.return_value = MagicMock()

        request = MagicMock()
        log = _make_audit_log(payload={"id": 1})
        result = can_read_from_payload(request, log)
        self.assertTrue(result)

    @patch("lex.api.utils.helpers.build_shadow_instance")
    @patch("lex.api.utils.helpers.resolve_target_model")
    def test_permission_read_denied(self, mock_resolve, mock_build):
        """permission_read returns allowed=False → can_read is False."""
        mock_instance = MagicMock()
        mock_instance.permission_read.return_value = MagicMock(allowed=False)
        mock_build.return_value = mock_instance
        mock_resolve.return_value = MagicMock()

        request = MagicMock()
        log = _make_audit_log(payload={"id": 1})
        result = can_read_from_payload(request, log)
        self.assertFalse(result)

    @patch("lex.api.utils.helpers.build_shadow_instance")
    @patch("lex.api.utils.helpers.resolve_target_model")
    def test_legacy_can_read_set_nonempty_allows(self, mock_resolve, mock_build):
        """Legacy can_read returns non-empty set → True."""
        mock_instance = MagicMock(spec=[])  # no permission_read
        mock_instance.can_read = MagicMock(return_value={"field1", "field2"})
        mock_build.return_value = mock_instance
        mock_resolve.return_value = MagicMock()

        request = MagicMock()
        log = _make_audit_log(payload={"id": 1})
        result = can_read_from_payload(request, log)
        self.assertTrue(result)

    @patch("lex.api.utils.helpers.build_shadow_instance")
    @patch("lex.api.utils.helpers.resolve_target_model")
    def test_legacy_can_read_empty_set_denies(self, mock_resolve, mock_build):
        """Legacy can_read returns empty set → False."""
        mock_instance = MagicMock(spec=[])  # no permission_read
        mock_instance.can_read = MagicMock(return_value=set())
        mock_build.return_value = mock_instance
        mock_resolve.return_value = MagicMock()

        request = MagicMock()
        log = _make_audit_log(payload={"id": 1})
        result = can_read_from_payload(request, log)
        self.assertFalse(result)

    @patch("lex.api.utils.helpers.build_shadow_instance")
    @patch("lex.api.utils.helpers.resolve_target_model")
    def test_no_permission_method_allows_by_default(self, mock_resolve, mock_build):
        """No permission_read or can_read → allow by default."""
        mock_instance = MagicMock(spec=[])  # no permission methods
        mock_build.return_value = mock_instance
        mock_resolve.return_value = MagicMock()

        request = MagicMock()
        log = _make_audit_log(payload={"id": 1})
        result = can_read_from_payload(request, log)
        self.assertTrue(result)

    @patch("lex.api.utils.helpers.build_shadow_instance")
    @patch("lex.api.utils.helpers.resolve_target_model")
    def test_permission_exception_allows_by_default(self, mock_resolve, mock_build):
        """Exception during permission check → allow (preserve default)."""
        mock_instance = MagicMock()
        mock_instance.permission_read.side_effect = RuntimeError("boom")
        mock_build.return_value = mock_instance
        mock_resolve.return_value = MagicMock()

        request = MagicMock()
        log = _make_audit_log(payload={"id": 1})
        result = can_read_from_payload(request, log)
        self.assertTrue(result)
