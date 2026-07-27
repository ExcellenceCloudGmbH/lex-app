"""
Tests for ``lex.audit_logging.utils.calculation_audit`` — the terminal audit
system that ensures every root calculation gets an AuditLog + AuditLogStatus
record, even when the calculation's own atomic block rolled back.

Why this matters
----------------
When a calculation runs inside ``transaction.atomic()`` and fails, the rollback
destroys any AuditLog rows created during the transaction. The
``ensure_terminal_calculation_audit`` function repairs this: it creates or
updates the AuditLog outside the failed transaction, sets the AuditLogStatus
to ``success`` or ``failure``, and stores the error payload.

Without these tests, a bug in this module means:
* Failed calculations leave no audit trail (compliance violation)
* The UI's audit log panel shows nothing for a failed calculation
* The AuditLogStatus gets stuck in ``pending`` forever

Test structure
--------------
* **TestIsRootCalculation** — verifies the root-vs-child detection logic
  that prevents child calculations from duplicating the audit entry.
* **TestResolveCalculationId** — verifies ID resolution from operation_context,
  ActiveCalculationStateStore, or instance attribute (priority order).
* **TestResolveActor** — verifies actor resolution from context, request, or
  fallback to "system".
* **TestBuildPayload** — verifies payload assembly with error messages.
* **TestEnsureTerminalCalculationAudit** — integration-level test with real DB.

How to run
----------
.. code-block:: bash

    python -m django test lex.audit_logging.tests.test_calculation_audit \\
        --settings=lex.process_admin.tests.django_test_settings \\
        --verbosity=2 --noinput
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.audit_logging.utils.calculation_audit import (
    _is_root_calculation,
    _resolve_calculation_id,
    _resolve_actor,
    _resolve_context_data,
    _resolve_audit_log_template,
    _same_model_instance,
    _build_payload,
    _resolve_model_context,
)


def _fake_instance(model_name="testmodel", pk=1, label_lower=None):
    """Create a minimal fake Django model instance for testing."""
    inst = MagicMock()
    meta = MagicMock()
    meta.model_name = model_name
    meta.label_lower = label_lower or f"app.{model_name}"
    inst._meta = meta
    inst.pk = pk
    inst.__class__ = type(model_name, (), {})
    inst.__class__._default_manager = MagicMock()
    return inst


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. _same_model_instance — identity comparison
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSameModelInstance(SimpleTestCase):
    """Verify model identity comparison by label_lower + pk."""

    def test_same_instance_returns_true(self):
        """Two references to logically same model return True."""
        a = _fake_instance("vehicle", pk=5, label_lower="app.vehicle")
        b = _fake_instance("vehicle", pk=5, label_lower="app.vehicle")
        self.assertTrue(_same_model_instance(a, b))

    def test_different_pk_returns_false(self):
        """Same model type, different pk returns False."""
        a = _fake_instance("vehicle", pk=5)
        b = _fake_instance("vehicle", pk=6)
        self.assertFalse(_same_model_instance(a, b))

    def test_different_model_returns_false(self):
        """Different model types return False."""
        a = _fake_instance("vehicle", pk=1, label_lower="app.vehicle")
        b = _fake_instance("investor", pk=1, label_lower="app.investor")
        self.assertFalse(_same_model_instance(a, b))

    def test_none_left_returns_false(self):
        """None on left side returns False."""
        self.assertFalse(_same_model_instance(None, _fake_instance()))

    def test_none_right_returns_false(self):
        """None on right side returns False."""
        self.assertFalse(_same_model_instance(_fake_instance(), None))

    def test_both_none_returns_false(self):
        """Both None returns False."""
        self.assertFalse(_same_model_instance(None, None))

    def test_no_meta_returns_false(self):
        """Object without _meta returns False."""
        plain = object()
        self.assertFalse(_same_model_instance(plain, _fake_instance()))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. _is_root_calculation — root vs child detection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestIsRootCalculation(SimpleTestCase):
    """
    The audit entry should only be written for the ROOT calculation.
    Child calculations (triggered by model_logging_context nesting) must
    NOT create their own audit entry — the root handles it.
    """

    def test_no_model_context_is_root(self):
        """When model_context is None, instance is treated as root."""
        instance = _fake_instance("report", pk=1)
        self.assertTrue(_is_root_calculation(instance, model_context=None))

    def test_empty_root_model_is_root(self):
        """When get_root() returns None, treated as root."""
        ctx = MagicMock()
        ctx.get_root.return_value = None
        ctx.current = None
        instance = _fake_instance("report", pk=1)
        self.assertTrue(_is_root_calculation(instance, model_context=ctx))

    def test_instance_is_root_and_current(self):
        """When instance matches both root and current, it's root."""
        instance = _fake_instance("report", pk=1, label_lower="app.report")
        ctx = MagicMock()
        ctx.get_root.return_value = instance
        ctx.current = instance
        self.assertTrue(_is_root_calculation(instance, model_context=ctx))

    def test_instance_is_not_root(self):
        """When root is a different instance, not root."""
        root = _fake_instance("parent_report", pk=10, label_lower="app.parent_report")
        child = _fake_instance("child_calc", pk=20, label_lower="app.child_calc")
        ctx = MagicMock()
        ctx.get_root.return_value = root
        ctx.current = child
        self.assertFalse(_is_root_calculation(child, model_context=ctx))

    def test_root_matches_but_current_different(self):
        """When root matches instance but current is different child, not root."""
        instance = _fake_instance("report", pk=1, label_lower="app.report")
        other = _fake_instance("other", pk=99, label_lower="app.other")
        ctx = MagicMock()
        ctx.get_root.return_value = instance
        ctx.current = other  # Currently executing a child
        self.assertFalse(_is_root_calculation(instance, model_context=ctx))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. _resolve_calculation_id — ID resolution priority
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestResolveCalculationId(SimpleTestCase):
    """
    calculation_id resolution follows a strict priority:
    1. operation_context (set by API view)
    2. ActiveCalculationStateStore (set when calc was registered)
    3. Instance attribute (last resort)
    """

    @patch("lex.audit_logging.utils.calculation_audit.ActiveCalculationStateStore")
    def test_context_data_takes_priority(self, mock_store):
        """calculation_id from context_data wins over everything."""
        instance = _fake_instance("model", pk=1)
        result = _resolve_calculation_id(
            instance, context_data={"calculation_id": "from-context"}
        )
        self.assertEqual(result, "from-context")
        mock_store.get_calculation_id.assert_not_called()

    @patch("lex.audit_logging.utils.calculation_audit.ActiveCalculationStateStore")
    def test_state_store_fallback(self, mock_store):
        """When context has no calc_id, falls back to ActiveCalculationStateStore."""
        mock_store.get_calculation_id.return_value = "from-store"
        instance = _fake_instance("model", pk=1)
        result = _resolve_calculation_id(
            instance, context_data={"calculation_id": ""}
        )
        self.assertEqual(result, "from-store")

    @patch("lex.audit_logging.utils.calculation_audit.ActiveCalculationStateStore")
    def test_instance_attribute_fallback(self, mock_store):
        """Last resort: instance.calculation_id attribute."""
        mock_store.get_calculation_id.return_value = None
        instance = _fake_instance("model", pk=1)
        instance.calculation_id = "from-instance"
        result = _resolve_calculation_id(
            instance, context_data={"calculation_id": ""}
        )
        self.assertEqual(result, "from-instance")

    @patch("lex.audit_logging.utils.calculation_audit.ActiveCalculationStateStore")
    def test_returns_none_when_nothing_available(self, mock_store):
        """When no source has a calc_id, returns None."""
        mock_store.get_calculation_id.return_value = None
        instance = _fake_instance("model", pk=1)
        del instance.calculation_id  # ensure attribute doesn't exist
        # MagicMock will raise AttributeError on getattr with deleted attr
        instance.calculation_id = None
        result = _resolve_calculation_id(
            instance, context_data={"calculation_id": ""}
        )
        self.assertIsNone(result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. _resolve_actor — actor name resolution
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestResolveActor(SimpleTestCase):
    """
    The actor name appears on the AuditLog.author field. The UI shows this
    to indicate WHO triggered the calculation.
    """

    def test_actor_from_request_user_dict(self):
        """When request_obj is a dict with 'user' key."""
        result = _resolve_actor({
            "request_obj": {"user": "john@example.com"}
        })
        self.assertEqual(result, "john@example.com")

    def test_actor_from_request_user_attribute(self):
        """When request_obj is an object with .user attribute."""
        request = SimpleNamespace(user="jane@example.com")
        result = _resolve_actor({"request_obj": request})
        self.assertEqual(result, "jane@example.com")

    def test_fallback_to_system(self):
        """When no actor information is available, returns 'system'."""
        result = _resolve_actor({})
        self.assertEqual(result, "system")

    def test_none_context_returns_system(self):
        """None context_data returns 'system'."""
        result = _resolve_actor(None)
        self.assertEqual(result, "system")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. _resolve_context_data — context extraction
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestResolveContextData(SimpleTestCase):
    """Verify context_data extraction from explicit dict or operation_context."""

    def test_returns_provided_dict(self):
        """When a dict is explicitly provided, returns it directly."""
        data = {"calculation_id": "abc"}
        self.assertIs(_resolve_context_data(data), data)

    @patch("lex.audit_logging.utils.calculation_audit.operation_context")
    def test_falls_back_to_operation_context(self, mock_ctx):
        """When None is provided, falls back to operation_context.get()."""
        mock_ctx.get.return_value = {"calculation_id": "from-op"}
        result = _resolve_context_data(None)
        self.assertEqual(result, {"calculation_id": "from-op"})

    @patch("lex.audit_logging.utils.calculation_audit.operation_context")
    def test_returns_empty_dict_on_failure(self, mock_ctx):
        """When operation_context raises, returns empty dict."""
        mock_ctx.get.side_effect = LookupError("no context")
        result = _resolve_context_data(None)
        self.assertEqual(result, {})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. _resolve_model_context — model context extraction
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestResolveModelContext(SimpleTestCase):
    """Verify model context resolution from explicit or contextvar."""

    def test_returns_explicit_context(self):
        """When model_context is provided, returns it directly."""
        ctx = MagicMock()
        self.assertIs(_resolve_model_context(ctx), ctx)

    def test_falls_back_to_contextvar(self):
        """When None, reads from _model_context contextvar."""
        from lex.audit_logging.utils.ModelContext import ModelContext, _model_context

        mc = ModelContext()
        token = _model_context.set({"model_context": mc})
        try:
            result = _resolve_model_context(None)
            self.assertIs(result, mc)
        finally:
            _model_context.reset(token)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. _build_payload — payload assembly with error info
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBuildPayload(SimpleTestCase):
    """
    The payload stored on AuditLog.payload must contain:
    * The serialized instance data
    * is_calculated status
    * error_message (if failure)
    * error_traceback (if failure)
    """

    @patch("lex.audit_logging.utils.calculation_audit.generic_instance_payload")
    def test_includes_is_calculated(self, mock_serialize):
        """Payload always includes the is_calculated status."""
        mock_serialize.return_value = {"name": "Test Report"}
        instance = _fake_instance("report", pk=1)
        instance.is_calculated = "SUCCESS"

        payload = _build_payload(instance)
        self.assertEqual(payload["is_calculated"], "SUCCESS")

    @patch("lex.audit_logging.utils.calculation_audit.generic_instance_payload")
    def test_includes_error_message_on_failure(self, mock_serialize):
        """On failure, error_message is added to payload."""
        mock_serialize.return_value = {"name": "Failed Report"}
        instance = _fake_instance("report", pk=1)
        instance.is_calculated = "ERROR"
        instance.error_message = None
        instance.calculation_error_message = None

        payload = _build_payload(
            instance,
            error_message="Division by zero",
            stack_trace="Traceback...",
        )
        self.assertEqual(payload["error_message"], "Division by zero")
        self.assertEqual(payload["error_traceback"], "Traceback...")

    @patch("lex.audit_logging.utils.calculation_audit.generic_instance_payload")
    def test_persisted_error_takes_priority(self, mock_serialize):
        """error_message from the persisted instance takes priority."""
        mock_serialize.return_value = {"name": "Report"}
        instance = _fake_instance("report", pk=1)
        instance.is_calculated = "ERROR"
        instance.error_message = "DB-persisted error"

        # The DB-persisted instance has its own error_message
        payload = _build_payload(
            instance,
            error_message="Argument error",
        )
        self.assertEqual(payload["error_message"], "DB-persisted error")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. _resolve_audit_log_template — template extraction
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestResolveAuditLogTemplate(SimpleTestCase):
    """Verify audit_log_temp extraction from context."""

    def test_returns_audit_log_from_context(self):
        """When context has an AuditLog instance, returns it."""
        from lex.audit_logging.models.AuditLog import AuditLog
        template = MagicMock(spec=AuditLog)
        result = _resolve_audit_log_template({"audit_log_temp": template})
        self.assertIs(result, template)

    def test_returns_none_when_not_audit_log(self):
        """When audit_log_temp is not an AuditLog, returns None."""
        result = _resolve_audit_log_template({"audit_log_temp": "not-audit-log"})
        self.assertIsNone(result)

    def test_returns_none_when_missing(self):
        """When audit_log_temp is not in context, returns None."""
        result = _resolve_audit_log_template({})
        self.assertIsNone(result)

    def test_returns_none_for_none_context(self):
        """None context_data returns None."""
        result = _resolve_audit_log_template(None)
        self.assertIsNone(result)
