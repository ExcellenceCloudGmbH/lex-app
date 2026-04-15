"""
Unit tests for the logging context-resolution subsystem.

**What this tests (customer-visible behaviour)**

When a calculation runs, every ``LexLogger.log()`` call is automatically
linked to:

- the **calculation ID** (from ``operation_context``)
- the **current model instance** being processed
- the **parent model** that triggered the child calculation
- the **root model** at the top of the calculation tree

This linkage is resolved by three collaborating classes:

1. ``ModelContext``  – a LIFO stack of model instances pushed/popped by
   ``model_logging_context()``.
2. ``ContextInfo``  – a frozen dataclass that carries the resolved
   calculation_id, audit_log, current/parent/root records, and their
   content-types.
3. ``ContextResolver.resolve()``  – the single entry-point that reads
   both ``operation_context`` (for ``calculation_id``) and
   ``ModelContext`` (for the model stack), queries the ``AuditLog``, and
   returns a ``ContextInfo``.

**Why it matters**

Logs that are not linked to the right calculation or model are invisible
in the frontend Calculation-Log panel, breaking the entire observability
story.  Regressions here affect every customer project.

**Methodology**

- ``ModelContext`` tests are pure in-memory stack manipulation — no mocks
  required.
- ``model_logging_context`` tests verify push/pop, nesting, and the
  ``TypeError`` guard for non-Django objects.
- ``ContextResolver`` tests mock ``operation_context``, ``_model_context``,
  ``AuditLog.objects.get``, and ``ContentType.objects.get_for_model`` to
  prove resolution logic without touching the database.

See also
--------
- ``docs/features/processing/logging.md``
- ``docs/reference/LexLogger API.md``
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from lex.audit_logging.utils.ModelContext import ModelContext, model_logging_context
from lex.audit_logging.utils.DataModels import (
    ContextInfo,
    CacheCleanupResult,
    CalculationLogError,
    CacheOperationError,
    ContextResolutionError,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1.  ModelContext stack
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestModelContextStack(SimpleTestCase):
    """Prove LIFO push/pop, ``current``, ``parent``, and ``get_root``."""

    def test_empty_stack_returns_none_for_current(self):
        """An empty ``ModelContext`` reports ``current`` as None."""
        ctx = ModelContext()
        self.assertIsNone(ctx.current)

    def test_empty_stack_returns_none_for_parent(self):
        """An empty ``ModelContext`` reports ``parent`` as None."""
        ctx = ModelContext()
        self.assertIsNone(ctx.parent)

    def test_empty_stack_returns_none_for_root(self):
        """An empty ``ModelContext`` reports ``get_root()`` as None."""
        ctx = ModelContext()
        self.assertIsNone(ctx.get_root())

    def test_single_push_sets_current(self):
        """After one push, ``current`` returns that instance."""
        ctx = ModelContext()
        obj = SimpleNamespace(_meta="fake_meta")
        ctx.push(obj)
        self.assertIs(ctx.current, obj)

    def test_single_push_parent_is_none(self):
        """With only one item, ``parent`` stays None."""
        ctx = ModelContext()
        ctx.push(SimpleNamespace(_meta="m"))
        self.assertIsNone(ctx.parent)

    def test_single_push_root_equals_current(self):
        """With one item the root **is** the current."""
        ctx = ModelContext()
        obj = SimpleNamespace(_meta="m")
        ctx.push(obj)
        self.assertIs(ctx.get_root(), obj)

    def test_two_pushes_updates_current_and_parent(self):
        """After two pushes: current = last, parent = first."""
        ctx = ModelContext()
        first = SimpleNamespace(_meta="m1")
        second = SimpleNamespace(_meta="m2")
        ctx.push(first)
        ctx.push(second)

        self.assertIs(ctx.current, second)
        self.assertIs(ctx.parent, first)

    def test_three_pushes_root_stays_first(self):
        """Root is always the bottom of the stack (index 0)."""
        ctx = ModelContext()
        a, b, c = (SimpleNamespace(_meta=f"m{i}") for i in range(3))
        for obj in (a, b, c):
            ctx.push(obj)

        self.assertIs(ctx.get_root(), a)
        self.assertIs(ctx.current, c)
        self.assertIs(ctx.parent, b)

    def test_pop_restores_previous_current(self):
        """Pop removes the top; ``current`` becomes the previous item."""
        ctx = ModelContext()
        first = SimpleNamespace(_meta="m1")
        second = SimpleNamespace(_meta="m2")
        ctx.push(first)
        ctx.push(second)

        popped = ctx.pop()
        self.assertIs(popped, second)
        self.assertIs(ctx.current, first)
        self.assertIsNone(ctx.parent)

    def test_pop_on_empty_returns_none(self):
        """Pop on an empty stack returns ``None`` instead of raising."""
        ctx = ModelContext()
        self.assertIsNone(ctx.pop())

    def test_repr_includes_depth(self):
        """``repr(ctx)`` includes the stack depth for debuggability."""
        ctx = ModelContext()
        ctx.push(SimpleNamespace(_meta="m"))
        self.assertIn("depth=1", repr(ctx))

    def test_init_with_existing_stack(self):
        """Constructing with a pre-built list uses it directly."""
        items = [SimpleNamespace(_meta=f"m{i}") for i in range(3)]
        ctx = ModelContext(stack=items)
        self.assertIs(ctx.current, items[-1])
        self.assertIs(ctx.get_root(), items[0])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2.  model_logging_context  context-manager
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _FakeModel:
    """Minimal stand-in for a Django model — just needs ``_meta``."""

    class _meta:
        model_name = "fakemodel"


class TestModelLoggingContext(SimpleTestCase):
    """Prove ``model_logging_context`` pushes/pops correctly."""

    def test_push_and_pop(self):
        """Entering the context pushes; exiting pops."""
        from lex.audit_logging.utils.ModelContext import _model_context

        model = _FakeModel()
        ctx_before = _model_context.get()["model_context"]
        depth_before = len(ctx_before._stack)

        with model_logging_context(model):
            ctx_inside = _model_context.get()["model_context"]
            self.assertIs(ctx_inside.current, model)
            self.assertEqual(len(ctx_inside._stack), depth_before + 1)

        ctx_after = _model_context.get()["model_context"]
        self.assertEqual(len(ctx_after._stack), depth_before)

    def test_nested_contexts(self):
        """Two nested ``model_logging_context`` blocks produce correct parent/current."""
        parent = _FakeModel()
        child = _FakeModel()

        from lex.audit_logging.utils.ModelContext import _model_context

        with model_logging_context(parent):
            ctx = _model_context.get()["model_context"]
            self.assertIs(ctx.current, parent)

            with model_logging_context(child):
                ctx = _model_context.get()["model_context"]
                self.assertIs(ctx.current, child)
                self.assertIs(ctx.parent, parent)

            ctx = _model_context.get()["model_context"]
            self.assertIs(ctx.current, parent)

    def test_rejects_non_django_object(self):
        """Passing an object without ``_meta`` raises ``TypeError``."""
        with self.assertRaises(TypeError):
            with model_logging_context("not a model"):
                pass  # pragma: no cover

    def test_accepts_none(self):
        """``None`` is allowed (used for anonymous/system contexts)."""
        with model_logging_context(None):
            pass  # should not raise


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3.  DataModels (ContextInfo, exception hierarchy)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestContextInfoDataclass(SimpleTestCase):
    """Prove ``ContextInfo`` construction and default values."""

    def test_required_fields_only(self):
        """Creating with just ``calculation_id`` and ``audit_log`` succeeds."""
        info = ContextInfo(calculation_id="abc-123", audit_log=MagicMock())
        self.assertEqual(info.calculation_id, "abc-123")
        self.assertIsNone(info.current_model)
        self.assertIsNone(info.parent_model)
        self.assertIsNone(info.root_record)

    def test_all_fields(self):
        """All optional fields are stored and accessible."""
        info = ContextInfo(
            calculation_id="x",
            audit_log=MagicMock(),
            current_model=MagicMock(),
            parent_model=MagicMock(),
            current_record="model_1",
            parent_record="parent_2",
            content_type=MagicMock(),
            parent_content_type=MagicMock(),
            root_record="root_0",
        )
        self.assertEqual(info.current_record, "model_1")
        self.assertEqual(info.parent_record, "parent_2")
        self.assertEqual(info.root_record, "root_0")


class TestCacheCleanupResult(SimpleTestCase):
    """Prove ``CacheCleanupResult`` normalises ``None`` lists."""

    def test_none_lists_become_empty(self):
        """Passing ``None`` for cleaned_keys / errors → empty lists."""
        result = CacheCleanupResult(success=True, cleaned_keys=None, errors=None)
        self.assertEqual(result.cleaned_keys, [])
        self.assertEqual(result.errors, [])

    def test_provided_lists_preserved(self):
        """Explicit lists are not overwritten."""
        result = CacheCleanupResult(
            success=False,
            cleaned_keys=["k1"],
            errors=["e1"],
        )
        self.assertEqual(result.cleaned_keys, ["k1"])
        self.assertEqual(result.errors, ["e1"])


class TestExceptionHierarchy(SimpleTestCase):
    """Prove the custom exception classes carry structured context."""

    def test_calculation_log_error_stores_calculation_id(self):
        """``CalculationLogError`` exposes ``calculation_id`` attribute."""
        exc = CalculationLogError("boom", calculation_id="calc-1")
        self.assertEqual(exc.calculation_id, "calc-1")
        self.assertIn("boom", str(exc))

    def test_cache_operation_error_stores_cache_key(self):
        """``CacheOperationError`` extends with ``cache_key``."""
        exc = CacheOperationError("timeout", cache_key="redis://foo")
        self.assertEqual(exc.cache_key, "redis://foo")

    def test_context_resolution_error_stores_stack_length(self):
        """``ContextResolutionError`` extends with ``stack_length``."""
        exc = ContextResolutionError("missing", stack_length=3)
        self.assertEqual(exc.stack_length, 3)

    def test_inheritance_chain(self):
        """All custom exceptions descend from ``CalculationLogError``."""
        self.assertTrue(issubclass(CacheOperationError, CalculationLogError))
        self.assertTrue(issubclass(ContextResolutionError, CalculationLogError))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.  ContextResolver.resolve()  (mocked integration)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestContextResolver(SimpleTestCase):
    """Prove ``ContextResolver.resolve()`` wires both context systems correctly."""

    def _make_fake_model(self, model_name="testmodel", pk=42):
        """Return a minimal stand-in whose ``_meta`` satisfies the resolver."""
        meta = SimpleNamespace(model_name=model_name, app_label="lex_app")
        return SimpleNamespace(_meta=meta, pk=pk)

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_resolve_happy_path_with_current_model(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """Full happy-path: calculation_id + current model → ContextInfo."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver

        audit_log = MagicMock(calculation_id="calc-99")
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-99",
            "audit_log_temp": audit_log,
        }

        current = self._make_fake_model("mymodel", pk=7)
        model_ctx = ModelContext()
        model_ctx.push(current)
        mock_model_ctx_var.get.return_value = {"model_context": model_ctx}
        mock_ct.return_value = MagicMock(spec_set=["pk"])

        info = ContextResolver.resolve()

        self.assertEqual(info.calculation_id, "calc-99")
        self.assertIs(info.audit_log, audit_log)
        self.assertEqual(info.current_record, "mymodel_7")
        self.assertIsNone(info.parent_record)

    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_resolve_raises_without_calculation_id(
        self, mock_op_ctx, mock_model_ctx_var
    ):
        """Missing ``calculation_id`` → ``ContextResolutionError``."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver

        mock_op_ctx.get.return_value = {"calculation_id": None}

        with self.assertRaises(ContextResolutionError):
            ContextResolver.resolve()

    @patch("lex.audit_logging.utils.ContextResolver._safe_get_content_type")
    @patch("lex.audit_logging.utils.ContextResolver._model_context")
    @patch("lex.audit_logging.utils.ContextResolver.operation_context")
    def test_resolve_with_parent_and_root(
        self, mock_op_ctx, mock_model_ctx_var, mock_ct
    ):
        """With two models in the stack: current, parent, and root are correct."""
        from lex.audit_logging.utils.ContextResolver import ContextResolver

        audit_log = MagicMock(calculation_id="calc-77")
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-77",
            "audit_log_temp": audit_log,
        }

        root_model = self._make_fake_model("root", pk=1)
        child_model = self._make_fake_model("child", pk=2)
        model_ctx = ModelContext()
        model_ctx.push(root_model)
        model_ctx.push(child_model)
        mock_model_ctx_var.get.return_value = {"model_context": model_ctx}
        mock_ct.return_value = MagicMock(spec_set=["pk"])

        info = ContextResolver.resolve()

        self.assertEqual(info.current_record, "child_2")
        self.assertEqual(info.parent_record, "root_1")
        self.assertEqual(info.root_record, "root_1")
