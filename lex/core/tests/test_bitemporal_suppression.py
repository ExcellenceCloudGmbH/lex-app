"""
Unit tests for the bitemporal signal-suppression context managers.

**What this tests (customer-visible behaviour)**

The bitemporal 3-layer architecture (History → MetaHistory → Main Table)
uses Django post_save / pre_delete signals to keep layers in sync.  During
bulk operations (backfills, reconciles, migrations) these signals must be
suppressed to avoid:

- Recursive chain-repair loops (valid_to / sys_to re-chaining)
- Redundant main-table syncs during bulk history inserts
- Excessive MetaHistory rows from internal save() calls

Three ``ContextVar``-based context managers control suppression:

1. ``suppress_main_table_sync()``     — skips History→Main-Table sync
2. ``suppress_history_valid_to_chaining()`` — skips recursive valid_to repair
3. ``suppress_meta_sys_to_chaining()``      — skips MetaHistory sys_to chaining

**Why it matters**

If these context managers leak state (i.e. remain True after exiting),
normal user edits will silently stop synchronising — data loss.  If they
fail to suppress, backfill commands run exponentially slowly or recurse.

**Methodology**

- Each context manager is tested for: (a) default is ``False``,
  (b) inside the block it reads ``True``, (c) after exit it's ``False``
  again, (d) nesting works correctly, (e) exceptions don't leak state.

See also
--------
- ``docs/features/tracking/bitemporal history.md``
"""

from django.test import SimpleTestCase

from lex.core.services.bitemporal_signals import (
    _suppress_main_table_sync,
    _suppress_history_valid_to_chaining,
    _suppress_meta_sys_to_chaining,
    suppress_main_table_sync,
    suppress_history_valid_to_chaining,
    suppress_meta_sys_to_chaining,
)


class TestSuppressMainTableSync(SimpleTestCase):
    """Prove ``suppress_main_table_sync`` toggles the ContextVar correctly."""

    def test_default_is_false(self):
        """Outside any context manager the var is ``False``."""
        self.assertFalse(_suppress_main_table_sync.get())

    def test_true_inside_block(self):
        """Inside the ``with`` block the var reads ``True``."""
        with suppress_main_table_sync():
            self.assertTrue(_suppress_main_table_sync.get())

    def test_restored_after_exit(self):
        """After exiting the block the var is ``False`` again."""
        with suppress_main_table_sync():
            pass
        self.assertFalse(_suppress_main_table_sync.get())

    def test_restored_after_exception(self):
        """Even if an exception is raised, the var is restored."""
        try:
            with suppress_main_table_sync():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertFalse(_suppress_main_table_sync.get())

    def test_nesting(self):
        """Nested blocks both suppress; exiting inner restores outer's token."""
        with suppress_main_table_sync():
            self.assertTrue(_suppress_main_table_sync.get())
            with suppress_main_table_sync():
                self.assertTrue(_suppress_main_table_sync.get())
            # After inner exits, outer is still active
            self.assertTrue(_suppress_main_table_sync.get())
        self.assertFalse(_suppress_main_table_sync.get())


class TestSuppressHistoryValidToChaining(SimpleTestCase):
    """Prove ``suppress_history_valid_to_chaining`` toggles correctly."""

    def test_default_is_false(self):
        """Outside any context manager the var is ``False``."""
        self.assertFalse(_suppress_history_valid_to_chaining.get())

    def test_true_inside_block(self):
        """Inside the ``with`` block the var reads ``True``."""
        with suppress_history_valid_to_chaining():
            self.assertTrue(_suppress_history_valid_to_chaining.get())

    def test_restored_after_exit(self):
        """After exiting the block the var is ``False`` again."""
        with suppress_history_valid_to_chaining():
            pass
        self.assertFalse(_suppress_history_valid_to_chaining.get())

    def test_restored_after_exception(self):
        """Even if an exception is raised, the var is restored."""
        try:
            with suppress_history_valid_to_chaining():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertFalse(_suppress_history_valid_to_chaining.get())


class TestSuppressMetaSysToChaining(SimpleTestCase):
    """Prove ``suppress_meta_sys_to_chaining`` toggles correctly."""

    def test_default_is_false(self):
        """Outside any context manager the var is ``False``."""
        self.assertFalse(_suppress_meta_sys_to_chaining.get())

    def test_true_inside_block(self):
        """Inside the ``with`` block the var reads ``True``."""
        with suppress_meta_sys_to_chaining():
            self.assertTrue(_suppress_meta_sys_to_chaining.get())

    def test_restored_after_exit(self):
        """After exiting the block the var is ``False`` again."""
        with suppress_meta_sys_to_chaining():
            pass
        self.assertFalse(_suppress_meta_sys_to_chaining.get())

    def test_restored_after_exception(self):
        """Even if an exception is raised, the var is restored."""
        try:
            with suppress_meta_sys_to_chaining():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertFalse(_suppress_meta_sys_to_chaining.get())


class TestCombinedSuppression(SimpleTestCase):
    """Prove all three context managers compose correctly."""

    def test_all_three_active_simultaneously(self):
        """Nesting all three leaves all vars ``True`` inside."""
        with suppress_main_table_sync():
            with suppress_history_valid_to_chaining():
                with suppress_meta_sys_to_chaining():
                    self.assertTrue(_suppress_main_table_sync.get())
                    self.assertTrue(_suppress_history_valid_to_chaining.get())
                    self.assertTrue(_suppress_meta_sys_to_chaining.get())

    def test_all_restored_after_exit(self):
        """After all three exit, all vars are ``False``."""
        with suppress_main_table_sync():
            with suppress_history_valid_to_chaining():
                with suppress_meta_sys_to_chaining():
                    pass
        self.assertFalse(_suppress_main_table_sync.get())
        self.assertFalse(_suppress_history_valid_to_chaining.get())
        self.assertFalse(_suppress_meta_sys_to_chaining.get())

    def test_partial_exit_preserves_active_ones(self):
        """Exiting the inner manager doesn't affect the outer ones."""
        with suppress_main_table_sync():
            with suppress_meta_sys_to_chaining():
                pass
            # meta is restored, but main_table is still suppressed
            self.assertTrue(_suppress_main_table_sync.get())
            self.assertFalse(_suppress_meta_sys_to_chaining.get())
