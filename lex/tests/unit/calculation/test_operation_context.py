"""
Unit tests for ``lex.api.utils.Context`` — OperationContext and helpers.

**What this tests (customer-visible behaviour)**

``OperationContext`` is the context manager that threads operation
metadata (request, calculation_id, audit_log) through the call stack
via ``ContextVar``.  Every CRUD operation wraps itself in an
``OperationContext`` so that downstream services (audit logging,
calculation signals, WebSocket broadcasts) can resolve "who triggered
this?" and "which calculation does this belong to?" without explicit
parameter passing.

**Why it matters**

If ``OperationContext`` fails to reset after an exception, subsequent
requests on the same thread inherit stale metadata — causing audit logs
to attribute actions to the wrong user or calculation.

**Methodology**

Pure logic + ContextVar manipulation.  No DB.  We test ``_build_context``,
``extract_info_request``, and the context-manager enter/exit lifecycle.

Run::

    python manage.py test lex.tests.test_operation_context
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from lex.api.utils.Context import (
    _build_context,
    OperationContext,
    operation_context,
)


class TestBuildContext(SimpleTestCase):
    """Prove ``_build_context`` constructs the expected dictionary."""

    def test_all_defaults(self):
        ctx = _build_context()
        self.assertEqual(ctx["operation_id"], "")
        self.assertEqual(ctx["request_obj"], "")
        self.assertEqual(ctx["calculation_id"], "")
        self.assertIsNone(ctx["audit_log_temp"])

    def test_all_explicit(self):
        ctx = _build_context(
            request="req",
            calculation_id="calc_1",
            audit_log="log",
            operation_id="op_1",
        )
        self.assertEqual(ctx["operation_id"], "op_1")
        self.assertEqual(ctx["request_obj"], "req")
        self.assertEqual(ctx["calculation_id"], "calc_1")
        self.assertEqual(ctx["audit_log_temp"], "log")


class TestExtractInfoRequest(SimpleTestCase):
    """Prove ``extract_info_request`` extracts user from a request."""

    def test_extracts_user_attribute(self):
        request = MagicMock()
        request.user = "admin"
        info = OperationContext.extract_info_request(request)
        self.assertIn("user", info)

    def test_missing_attribute_skipped(self):
        request = MagicMock(spec=[])  # no 'user' attribute
        info = OperationContext.extract_info_request(request)
        self.assertNotIn("user", info)


class TestOperationContextManager(SimpleTestCase):
    """Prove ``OperationContext`` sets and resets the ContextVar correctly."""

    def test_enter_sets_context(self):
        with OperationContext(calculation_id="calc_42") as ctx:
            self.assertEqual(ctx["calculation_id"], "calc_42")
            # Verify the ContextVar is set
            current = operation_context.get()
            self.assertEqual(current["calculation_id"], "calc_42")

    def test_exit_resets_context(self):
        original = operation_context.get()
        with OperationContext(calculation_id="calc_42"):
            pass
        after = operation_context.get()
        self.assertEqual(after.get("calculation_id", ""), original.get("calculation_id", ""))

    def test_resets_after_exception(self):
        original = operation_context.get()
        try:
            with OperationContext(calculation_id="fail"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        after = operation_context.get()
        self.assertEqual(after.get("calculation_id", ""), original.get("calculation_id", ""))

    def test_generates_operation_id(self):
        with OperationContext() as ctx:
            self.assertTrue(len(ctx["operation_id"]) > 0)

    def test_nested_context_preserves_outer_operation_id(self):
        with OperationContext(calculation_id="outer") as outer_ctx:
            outer_op_id = outer_ctx["operation_id"]
            with OperationContext(calculation_id="inner") as inner_ctx:
                # Inner should inherit the outer operation_id
                self.assertEqual(inner_ctx["operation_id"], outer_op_id)
                self.assertEqual(inner_ctx["calculation_id"], "inner")
            # After inner exits, outer should be restored
            restored = operation_context.get()
            self.assertEqual(restored["calculation_id"], "outer")

    def test_get_calc_id_returns_current(self):
        with OperationContext(calculation_id="test_calc"):
            self.assertEqual(OperationContext.get_calc_id(), "test_calc")

    def test_get_request_returns_current(self):
        mock_request = MagicMock()
        with OperationContext(request=mock_request):
            self.assertIs(OperationContext.get_request(), mock_request)
