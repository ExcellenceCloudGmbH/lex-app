"""
Unit tests for bitemporal suppression context managers.

**What this tests (customer-visible behaviour)**

The context managers ``suppress_main_table_sync``,
``suppress_history_valid_to_chaining``, and ``suppress_meta_sys_to_chaining``
disable specific signal handlers during bulk operations like backfills.
They use ``ContextVar``s so they are safe for concurrent async handlers.

**Why it matters**

If a suppression guard doesn't properly reset, subsequent normal operations
silently skip history chaining — causing broken bitemporal invariants
(gaps in valid_to chains, missing MetaHistory rows).

**Methodology**

We test the ContextVar toggle behaviour: default is False, inside the
context manager it is True, after exiting (even via exception) it resets
to False.

Run::

    python manage.py test lex.tests.test_bitemporal_suppress_context_managers
"""

from django.test import SimpleTestCase

from lex.core.services.bitemporal_signals import (
    suppress_main_table_sync,
    suppress_history_valid_to_chaining,
    suppress_meta_sys_to_chaining,
    _suppress_main_table_sync,
    _suppress_history_valid_to_chaining,
    _suppress_meta_sys_to_chaining,
)


class TestSuppressMainTableSync(SimpleTestCase):
    """Prove ``suppress_main_table_sync`` toggles the ContextVar correctly."""

    def test_default_is_false(self):
        self.assertFalse(_suppress_main_table_sync.get())

    def test_inside_context_is_true(self):
        with suppress_main_table_sync():
            self.assertTrue(_suppress_main_table_sync.get())

    def test_resets_after_context(self):
        with suppress_main_table_sync():
            pass
        self.assertFalse(_suppress_main_table_sync.get())

    def test_resets_after_exception(self):
        try:
            with suppress_main_table_sync():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertFalse(_suppress_main_table_sync.get())

    def test_nesting(self):
        with suppress_main_table_sync():
            self.assertTrue(_suppress_main_table_sync.get())
            with suppress_main_table_sync():
                self.assertTrue(_suppress_main_table_sync.get())
            # After inner exits, outer still has its own token
            self.assertTrue(_suppress_main_table_sync.get())
        self.assertFalse(_suppress_main_table_sync.get())


class TestSuppressHistoryValidToChaining(SimpleTestCase):
    """Prove ``suppress_history_valid_to_chaining`` toggles correctly."""

    def test_default_is_false(self):
        self.assertFalse(_suppress_history_valid_to_chaining.get())

    def test_inside_context_is_true(self):
        with suppress_history_valid_to_chaining():
            self.assertTrue(_suppress_history_valid_to_chaining.get())

    def test_resets_after_context(self):
        with suppress_history_valid_to_chaining():
            pass
        self.assertFalse(_suppress_history_valid_to_chaining.get())

    def test_resets_after_exception(self):
        try:
            with suppress_history_valid_to_chaining():
                raise ValueError("test")
        except ValueError:
            pass
        self.assertFalse(_suppress_history_valid_to_chaining.get())


class TestSuppressMetaSysToChaining(SimpleTestCase):
    """Prove ``suppress_meta_sys_to_chaining`` toggles correctly."""

    def test_default_is_false(self):
        self.assertFalse(_suppress_meta_sys_to_chaining.get())

    def test_inside_context_is_true(self):
        with suppress_meta_sys_to_chaining():
            self.assertTrue(_suppress_meta_sys_to_chaining.get())

    def test_resets_after_context(self):
        with suppress_meta_sys_to_chaining():
            pass
        self.assertFalse(_suppress_meta_sys_to_chaining.get())

    def test_resets_after_exception(self):
        try:
            with suppress_meta_sys_to_chaining():
                raise TypeError("test")
        except TypeError:
            pass
        self.assertFalse(_suppress_meta_sys_to_chaining.get())
