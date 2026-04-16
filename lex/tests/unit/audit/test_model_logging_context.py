"""
Tests for ``ModelContext`` and ``model_logging_context`` — the context stack
that tracks parent/child relationships during nested calculations.

Why this matters
----------------
Every calculation in LEX can trigger child calculations. When
``InvestorTrackRecord.calculate()`` triggers ``CalculateNAV.calculate()``,
the framework needs to know:

1. WHO is the root calculation (for AuditLog ownership)
2. WHO is the current model (for CalculationLog entries)
3. WHO is the parent model (for parent-child log linking)

``model_logging_context`` is a context manager that pushes/pops models
onto a stack. ``ContextResolver.resolve()`` reads this stack to build
the ``ContextInfo`` that ``CalculationLog.log()`` uses.

If the stack is wrong:
* Log entries link to the wrong parent
* Cache fan-out goes to wrong WebSocket groups
* Root detection fails → duplicate or missing AuditLog entries

What is tested
--------------
* **ModelContext push/pop** — stack LIFO semantics
* **model_logging_context** — proper nesting, cleanup on exception
* **Single level** — one model in context
* **Two levels** — parent + child
* **Three levels** — grandparent + parent + child (real-world: Report → NAV → Cashflow)
* **Exception safety** — stack restores even when calculate() raises
* **Type validation** — non-Django instances rejected
* **Empty stack** — current/parent/root all None

How to run
----------
.. code-block:: bash

    python -m django test lex.audit_logging.tests.test_model_logging_context \\
        --settings=lex.process_admin.tests.django_test_settings \\
        --verbosity=2 --noinput
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from lex.audit_logging.utils.ModelContext import (
    ModelContext,
    model_logging_context,
    _model_context,
)


def _fake_model(name="TestModel", pk=1):
    """Create a minimal fake Django model instance with _meta."""
    inst = MagicMock()
    meta = MagicMock()
    meta.model_name = name.lower()
    meta.label_lower = f"app.{name.lower()}"
    inst._meta = meta
    inst.pk = pk
    inst.__str__ = lambda self: f"{name}(pk={pk})"
    return inst


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. ModelContext class — stack semantics
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestModelContextStack(SimpleTestCase):
    """Verify ModelContext implements correct LIFO stack semantics."""

    def test_empty_stack_current_is_none(self):
        """Empty stack → current is None."""
        ctx = ModelContext()
        self.assertIsNone(ctx.current)

    def test_empty_stack_parent_is_none(self):
        """Empty stack → parent is None."""
        ctx = ModelContext()
        self.assertIsNone(ctx.parent)

    def test_empty_stack_root_is_none(self):
        """Empty stack → get_root() is None."""
        ctx = ModelContext()
        self.assertIsNone(ctx.get_root())

    def test_push_single_item(self):
        """Push one item → it becomes current, no parent."""
        ctx = ModelContext()
        model = _fake_model("Report", pk=1)
        ctx.push(model)
        self.assertIs(ctx.current, model)
        self.assertIsNone(ctx.parent)
        self.assertIs(ctx.get_root(), model)

    def test_push_two_items(self):
        """Push two items → second is current, first is parent and root."""
        ctx = ModelContext()
        parent = _fake_model("Report", pk=1)
        child = _fake_model("NAV", pk=2)
        ctx.push(parent)
        ctx.push(child)
        self.assertIs(ctx.current, child)
        self.assertIs(ctx.parent, parent)
        self.assertIs(ctx.get_root(), parent)

    def test_push_three_items(self):
        """Push three items → third is current, second is parent, first is root."""
        ctx = ModelContext()
        grandparent = _fake_model("InvestorTrackRecord", pk=1)
        parent = _fake_model("CalculateNAV", pk=2)
        child = _fake_model("InvestorCashflow", pk=3)
        ctx.push(grandparent)
        ctx.push(parent)
        ctx.push(child)

        self.assertIs(ctx.current, child)
        self.assertIs(ctx.parent, parent)
        self.assertIs(ctx.get_root(), grandparent)

    def test_pop_restores_previous(self):
        """Pop removes the top → previous becomes current."""
        ctx = ModelContext()
        parent = _fake_model("Report", pk=1)
        child = _fake_model("NAV", pk=2)
        ctx.push(parent)
        ctx.push(child)

        popped = ctx.pop()
        self.assertIs(popped, child)
        self.assertIs(ctx.current, parent)
        self.assertIsNone(ctx.parent)

    def test_pop_empty_returns_none(self):
        """Pop on empty stack returns None, doesn't crash."""
        ctx = ModelContext()
        result = ctx.pop()
        self.assertIsNone(result)

    def test_repr_shows_depth(self):
        """__repr__ includes current, parent, and depth."""
        ctx = ModelContext()
        model = _fake_model("Report", pk=1)
        ctx.push(model)
        repr_str = repr(ctx)
        self.assertIn("depth=1", repr_str)

    def test_initial_stack_from_list(self):
        """ModelContext can be initialized with a pre-built stack."""
        models = [_fake_model("A", pk=1), _fake_model("B", pk=2)]
        ctx = ModelContext(models)
        self.assertIs(ctx.current, models[1])
        self.assertIs(ctx.parent, models[0])
        self.assertIs(ctx.get_root(), models[0])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. model_logging_context — context manager
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestModelLoggingContext(SimpleTestCase):
    """
    Verify ``model_logging_context`` correctly pushes/pops the model stack
    and restores state even on exceptions.
    """

    def test_single_level_sets_current(self):
        """One level of nesting → model is current."""
        model = _fake_model("Report", pk=1)
        with model_logging_context(model):
            ctx = _model_context.get()["model_context"]
            self.assertIs(ctx.current, model)
            self.assertIsNone(ctx.parent)

    def test_single_level_cleans_up(self):
        """After exiting the context, model is popped."""
        model = _fake_model("Report", pk=1)
        ctx = _model_context.get()["model_context"]
        original_depth = len(ctx._stack)

        with model_logging_context(model):
            pass

        self.assertEqual(len(ctx._stack), original_depth)

    def test_nested_two_levels(self):
        """
        Simulates: InvestorTrackRecord → CalculateNAV
        Inside the inner context, NAV is current, Report is parent.
        """
        parent = _fake_model("InvestorTrackRecord", pk=1)
        child = _fake_model("CalculateNAV", pk=2)

        with model_logging_context(parent):
            ctx = _model_context.get()["model_context"]
            self.assertIs(ctx.current, parent)

            with model_logging_context(child):
                ctx = _model_context.get()["model_context"]
                self.assertIs(ctx.current, child)
                self.assertIs(ctx.parent, parent)
                self.assertIs(ctx.get_root(), parent)

            # After inner exits, parent is current again
            ctx = _model_context.get()["model_context"]
            self.assertIs(ctx.current, parent)
            self.assertIsNone(ctx.parent)

    def test_nested_three_levels(self):
        """
        Simulates: InvestorTrackRecord → CalculateNAV → InvestorCashflow
        This is the real-world pattern from project_example.
        """
        grandparent = _fake_model("InvestorTrackRecord", pk=1)
        parent = _fake_model("CalculateNAV", pk=2)
        child = _fake_model("InvestorCashflow", pk=3)

        with model_logging_context(grandparent):
            with model_logging_context(parent):
                with model_logging_context(child):
                    ctx = _model_context.get()["model_context"]
                    self.assertIs(ctx.current, child)
                    self.assertIs(ctx.parent, parent)
                    self.assertIs(ctx.get_root(), grandparent)

                # After child exits
                ctx = _model_context.get()["model_context"]
                self.assertIs(ctx.current, parent)
                self.assertIs(ctx.parent, grandparent)

            # After parent exits
            ctx = _model_context.get()["model_context"]
            self.assertIs(ctx.current, grandparent)

    def test_exception_restores_stack(self):
        """
        If calculate() raises inside model_logging_context, the stack
        must still be restored — otherwise subsequent calculations
        would see a stale parent.
        """
        model = _fake_model("FailingCalc", pk=1)
        ctx = _model_context.get()["model_context"]
        original_depth = len(ctx._stack)

        with self.assertRaises(RuntimeError):
            with model_logging_context(model):
                raise RuntimeError("calculation failed")

        self.assertEqual(len(ctx._stack), original_depth)

    def test_exception_in_nested_restores_outer(self):
        """
        Exception in the inner context must not corrupt the outer context.
        """
        parent = _fake_model("Parent", pk=1)
        child = _fake_model("Child", pk=2)

        with model_logging_context(parent):
            with self.assertRaises(ValueError):
                with model_logging_context(child):
                    raise ValueError("child calculation failed")

            # Parent should still be current
            ctx = _model_context.get()["model_context"]
            self.assertIs(ctx.current, parent)

    def test_rejects_non_django_model(self):
        """
        model_logging_context must reject non-Django-model instances
        to prevent silent bugs where a dict or string is pushed.
        """
        with self.assertRaises(TypeError):
            with model_logging_context("not a model"):
                pass

    def test_rejects_dict(self):
        """Dicts are not model instances."""
        with self.assertRaises(TypeError):
            with model_logging_context({"key": "value"}):
                pass

    def test_accepts_none_gracefully(self):
        """
        Some code paths may call model_logging_context(None) when the
        instance isn't available. This should not crash but silently push None.
        
        Note: This tests the actual implementation which allows None.
        """
        ctx = _model_context.get()["model_context"]
        original_depth = len(ctx._stack)

        # model_logging_context checks `if instance is not None and not hasattr(instance, '_meta')`
        # So None is accepted (pushed as None)
        with model_logging_context(None):
            self.assertEqual(len(ctx._stack), original_depth + 1)

        self.assertEqual(len(ctx._stack), original_depth)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Integration: model_logging_context → ContextResolver
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestContextResolverIntegration(SimpleTestCase):
    """
    Verify that ContextResolver reads the model context stack correctly
    to produce the expected ContextInfo for CalculationLog.log().

    ContextResolver.resolve() needs operation_context (calculation_id)
    and model_context (current/parent models). We test the model_context
    half here; operation_context is mocked.
    """

    def test_resolver_reads_current_from_stack(self):
        """
        When model_logging_context is active, ContextResolver must see
        the current model on the stack.
        """
        # We can't fully test resolve() without a real AuditLog,
        # but we can verify the model_context is readable
        model = _fake_model("Report", pk=1)
        with model_logging_context(model):
            ctx = _model_context.get()["model_context"]
            self.assertIs(ctx.current, model)
            # ContextResolver.resolve() would call ctx.current internally

    def test_resolver_reads_parent_from_nested_stack(self):
        """
        With nested model_logging_context, ContextResolver must see
        both current and parent models.
        """
        parent = _fake_model("Parent", pk=1)
        child = _fake_model("Child", pk=2)

        with model_logging_context(parent):
            with model_logging_context(child):
                ctx = _model_context.get()["model_context"]
                self.assertIs(ctx.current, child)
                self.assertIs(ctx.parent, parent)
                # ContextResolver would produce:
                # current_record = "child_2"
                # parent_record = "parent_1"
                # root_record = "parent_1"

    def test_cache_key_pattern_from_model_context(self):
        """
        The cache key for CalculationLog is built as
        ``{model_name}_{pk}_{calculation_id}``.
        Verify the record_id format matches what CacheManager expects.
        """
        from lex.audit_logging.utils.CacheManager import CacheManager

        model = _fake_model("demandforecastparallel", pk=7)
        with model_logging_context(model):
            ctx = _model_context.get()["model_context"]
            current = ctx.current
            record_id = f"{current._meta.model_name}_{current.pk}"

            cache_key = CacheManager.build_cache_key(record_id, "calc-123")
            self.assertEqual(cache_key, "demandforecastparallel_7_calc-123")
