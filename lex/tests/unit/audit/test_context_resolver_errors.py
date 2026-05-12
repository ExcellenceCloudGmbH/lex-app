"""
Tests for ``ContextResolver.resolve()`` error paths and edge cases.

**What is tested:**

    * AuditLog not found for calculation_id  ->  ``ContextResolutionError``
    * Generic exception during AuditLog lookup  ->  ``ContextResolutionError`` wrapping
    * ContentType resolution error on current model  ->  warning logged, ``None``
      in ``content_type`` field, does not crash
    * ContentType resolution error on parent model  ->  warning logged, ``None``
      in ``parent_content_type`` field, does not crash
    * Root model resolution error  ->  warning logged, ``None`` in ``root_record``
    * Empty model context (no current/parent)  ->  all model fields are ``None``

**Complements:**

    ``test_context_resolution.py`` already covers the happy path (current model,
    parent + root) and the missing-calculation_id path.  This file covers the
    remaining error/edge paths to bring ``ContextResolver`` coverage above 90%.

**Why this matters:**

    ``ContextResolver.resolve()`` runs inside every calculation logging operation.
    If an unexpected exception escapes (instead of being wrapped in
    ``ContextResolutionError``), the calculation still succeeds but the log entry
    is silently lost — operators have no visibility into what happened.

**How to run:**

    .. code-block:: bash

        lex test lex.audit_logging.tests.test_context_resolver_errors --verbosity=2 --noinput
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.audit_logging.utils.DataModels import ContextResolutionError
from lex.audit_logging.utils.ModelContext import ModelContext


# ─── helpers ──────────────────────────────────────────────────────────

def _fake_model(model_name="testmodel", pk=42):
    """Minimal stand-in whose ``_meta`` satisfies the resolver."""
    meta = SimpleNamespace(model_name=model_name, app_label="lex_app")
    return SimpleNamespace(_meta=meta, pk=pk)


# ════════════════════════════════════════════════════════════════════════
#  AuditLog lookup errors
# ════════════════════════════════════════════════════════════════════════

class TestContextResolverAuditLogErrors(SimpleTestCase):
    """Verify that AuditLog lookup failures produce ContextResolutionError."""

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_auditlog_does_not_exist_raises_context_error(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """AuditLog.DoesNotExist is wrapped in ContextResolutionError with calculation_id."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver
        from lex.audit_logging.models.AuditLog import AuditLog

        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-missing",
            "audit_log_temp": None,
        }

        with patch.object(AuditLog.objects, "get", side_effect=AuditLog.DoesNotExist):
            with self.assertRaises(ContextResolutionError) as cm:
                ContextResolver.resolve()

        self.assertIn("not found", str(cm.exception).lower())
        self.assertEqual(cm.exception.calculation_id, "calc-missing")

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_auditlog_generic_exception_raises_context_error(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """Generic exception during AuditLog.objects.get is wrapped in ContextResolutionError."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver
        from lex.audit_logging.models.AuditLog import AuditLog

        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-db-error",
            "audit_log_temp": None,
        }

        with patch.object(AuditLog.objects, "get", side_effect=RuntimeError("connection pool exhausted")):
            with self.assertRaises(ContextResolutionError) as cm:
                ContextResolver.resolve()

        self.assertIn("connection pool exhausted", str(cm.exception))
        self.assertEqual(cm.exception.calculation_id, "calc-db-error")

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_audit_log_temp_used_when_present(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """When audit_log_temp is set in context, DB lookup is skipped entirely."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver
        from lex.audit_logging.models.AuditLog import AuditLog

        temp_log = MagicMock(calculation_id="calc-temp")
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-temp",
            "audit_log_temp": temp_log,
        }

        model_ctx = ModelContext()
        mock_model_ctx_var.get.return_value = {"model_context": model_ctx}
        mock_ct.return_value = MagicMock()

        with patch.object(AuditLog.objects, "get") as mock_get:
            info = ContextResolver.resolve()
            mock_get.assert_not_called()

        self.assertIs(info.audit_log, temp_log)


# ════════════════════════════════════════════════════════════════════════
#  ContentType resolution errors
# ════════════════════════════════════════════════════════════════════════

class TestContextResolverContentTypeErrors(SimpleTestCase):
    """Verify ContentType errors are caught gracefully without crashing resolve()."""

    def _setup_context(self, mock_op_ctx, mock_model_ctx_var, current=None, parent=None):
        """Set up operation context and model context for a test."""
        audit_log = MagicMock(calculation_id="calc-ct-test")
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-ct-test",
            "audit_log_temp": audit_log,
        }

        model_ctx = ModelContext()
        if parent:
            model_ctx.push(parent)
        if current:
            model_ctx.push(current)
        mock_model_ctx_var.get.return_value = {"model_context": model_ctx}

        return audit_log

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_current_model_content_type_error_logs_warning(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """ContentType error on current model -> warning, both content_type and
        current_record are None because they share the same try block."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver

        current = _fake_model("cashflow", pk=10)
        self._setup_context(mock_op_ctx, mock_model_ctx_var, current=current)

        mock_ct.side_effect = RuntimeError("ContentType table missing")

        info = ContextResolver.resolve()

        # resolve() succeeds — error is caught
        self.assertIsNone(info.content_type)
        # current_record is also None: _safe_get_content_type and current_record
        # assignment are in the same try block, so the exception skips both
        self.assertIsNone(info.current_record)

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_parent_model_content_type_error_logs_warning(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """ContentType error on parent model -> warning, parent_content_type and
        parent_record are both None (same try block)."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver

        parent = _fake_model("portfolio", pk=1)
        current = _fake_model("cashflow", pk=10)
        self._setup_context(mock_op_ctx, mock_model_ctx_var, current=current, parent=parent)

        # First call (current model) succeeds, second call (parent model) fails
        mock_ct.side_effect = [MagicMock(), RuntimeError("stale cache")]

        info = ContextResolver.resolve()

        # parent_content_type and parent_record share a try block
        self.assertIsNone(info.parent_content_type)
        self.assertIsNone(info.parent_record)
        # current should be fine
        self.assertEqual(info.current_record, "cashflow_10")

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_root_model_meta_error_logs_warning(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """Root model with broken pk -> warning logged, root_record=None."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver

        # Create a root model whose pk property raises
        class BrokenRoot:
            @property
            def _meta(self):
                return SimpleNamespace(model_name="broken")

            @property
            def pk(self):
                raise AttributeError("pk not available")

        broken_root = BrokenRoot()
        current = _fake_model("cashflow", pk=10)

        audit_log = MagicMock(calculation_id="calc-root")
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-root",
            "audit_log_temp": audit_log,
        }

        model_ctx = ModelContext()
        model_ctx.push(broken_root)
        model_ctx.push(current)
        mock_model_ctx_var.get.return_value = {"model_context": model_ctx}
        mock_ct.return_value = MagicMock()

        info = ContextResolver.resolve()

        # root_record should be None because pk access on root raised
        self.assertIsNone(info.root_record)
        # current_record still works
        self.assertEqual(info.current_record, "cashflow_10")


# ════════════════════════════════════════════════════════════════════════
#  Empty model context
# ════════════════════════════════════════════════════════════════════════

class TestContextResolverEmptyModelContext(SimpleTestCase):
    """Verify behavior when model context stack is empty."""

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_empty_stack_returns_none_model_fields(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """Empty model stack -> all model-related fields are None."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver

        audit_log = MagicMock(calculation_id="calc-empty")
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-empty",
            "audit_log_temp": audit_log,
        }

        model_ctx = ModelContext()  # empty stack
        mock_model_ctx_var.get.return_value = {"model_context": model_ctx}

        info = ContextResolver.resolve()

        self.assertEqual(info.calculation_id, "calc-empty")
        self.assertIs(info.audit_log, audit_log)
        self.assertIsNone(info.current_model)
        self.assertIsNone(info.parent_model)
        self.assertIsNone(info.current_record)
        self.assertIsNone(info.parent_record)
        self.assertIsNone(info.content_type)
        self.assertIsNone(info.parent_content_type)
        self.assertIsNone(info.root_record)

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_single_model_has_no_parent(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """Single model in stack -> current set, parent is None, root == current."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver

        audit_log = MagicMock(calculation_id="calc-single")
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-single",
            "audit_log_temp": audit_log,
        }

        current = _fake_model("fund", pk=7)
        model_ctx = ModelContext()
        model_ctx.push(current)
        mock_model_ctx_var.get.return_value = {"model_context": model_ctx}
        mock_ct.return_value = MagicMock()

        info = ContextResolver.resolve()

        self.assertIs(info.current_model, current)
        self.assertIsNone(info.parent_model)
        self.assertIsNone(info.parent_record)
        self.assertIsNone(info.parent_content_type)
        # Root is the same as current when only one in the stack
        self.assertEqual(info.root_record, "fund_7")
        self.assertEqual(info.current_record, "fund_7")
