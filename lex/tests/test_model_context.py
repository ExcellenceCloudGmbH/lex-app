"""
Unit tests for ``lex.audit_logging.utils.ModelContext.ModelContext``.

**What this tests (customer-visible behaviour)**

``ModelContext`` is the stack that tracks which model instance is
currently being processed during calculations.  ``ContextResolver``
reads its ``current`` and ``parent`` properties to build
calculation-log entries.

**Why it matters**

If the stack doesn't pop correctly, nested calculations attribute log
entries to the wrong model — creating misleading audit trails.

**Methodology**

Pure data-structure — no DB.

Run::

    python manage.py test lex.tests.test_model_context
"""

from django.test import SimpleTestCase

from lex.audit_logging.utils.ModelContext import ModelContext


class TestModelContextStack(SimpleTestCase):
    """Prove ``ModelContext`` push/pop/current/parent semantics."""

    def test_empty_current_is_none(self):
        ctx = ModelContext()
        self.assertIsNone(ctx.current)

    def test_empty_parent_is_none(self):
        ctx = ModelContext()
        self.assertIsNone(ctx.parent)

    def test_push_sets_current(self):
        ctx = ModelContext()
        ctx.push("A")
        self.assertEqual(ctx.current, "A")

    def test_push_two_sets_current_and_parent(self):
        ctx = ModelContext()
        ctx.push("A")
        ctx.push("B")
        self.assertEqual(ctx.current, "B")
        self.assertEqual(ctx.parent, "A")

    def test_pop_returns_last(self):
        ctx = ModelContext()
        ctx.push("A")
        ctx.push("B")
        popped = ctx.pop()
        self.assertEqual(popped, "B")
        self.assertEqual(ctx.current, "A")

    def test_pop_empty_returns_none(self):
        ctx = ModelContext()
        self.assertIsNone(ctx.pop())

    def test_push_pop_lifecycle(self):
        ctx = ModelContext()
        ctx.push("A")
        ctx.push("B")
        ctx.push("C")
        self.assertEqual(ctx.current, "C")
        self.assertEqual(ctx.parent, "B")

        ctx.pop()
        self.assertEqual(ctx.current, "B")
        self.assertEqual(ctx.parent, "A")

        ctx.pop()
        self.assertEqual(ctx.current, "A")
        self.assertIsNone(ctx.parent)

        ctx.pop()
        self.assertIsNone(ctx.current)

    def test_get_root_returns_first(self):
        ctx = ModelContext()
        ctx.push("root")
        ctx.push("mid")
        ctx.push("leaf")
        self.assertEqual(ctx.get_root(), "root")

    def test_get_root_empty_returns_none(self):
        ctx = ModelContext()
        self.assertIsNone(ctx.get_root())

    def test_repr(self):
        ctx = ModelContext()
        ctx.push("A")
        r = repr(ctx)
        self.assertIn("A", r)
        self.assertIn("depth=1", r)

    def test_init_with_existing_stack(self):
        ctx = ModelContext(stack=["X", "Y"])
        self.assertEqual(ctx.current, "Y")
        self.assertEqual(ctx.parent, "X")

    def test_single_item_parent_is_none(self):
        ctx = ModelContext()
        ctx.push("only")
        self.assertIsNone(ctx.parent)
