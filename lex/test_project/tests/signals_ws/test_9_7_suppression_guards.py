"""
Cluster 9.7 – 9.10: Bitemporal suppression guards — contract of the
three ``ContextVar``-backed context managers in
``lex/core/services/bitemporal_signals.py``.

These guards are what the signal handlers themselves consult to
decide whether to run or early-return:

    * ``suppress_main_table_sync``         → consulted at line 274
      inside ``on_history_saved__sync_main_table``
    * ``suppress_history_valid_to_chaining`` → consulted at line 118
      inside ``on_history_saved__chain_valid_to``
    * ``suppress_meta_sys_to_chaining``    → consulted in the Level-2
      meta-chaining handler

The handlers also wrap their own internal ``record.save(...)`` calls
inside ``with suppress_*()``. If the guard contract drifts (e.g. the
context-manager stops resetting on exit, or one guard's state leaks
into another), the bitemporal chain recomputes recursively and the
~14-min BUG-011 bottleneck gets 10× worse — i.e. customer-visible
export latency regresses silently.

Baseline coverage before this sub-cluster: ``bitemporal_signals.py``
sat at **46.60%** with lines 170–340 largely untouched. Direct
integration tests on the signal handlers require a full history
fixture (which clusters 5 + 9 already cover at the happy-path
level); this file locks down the **suppression primitives** that
every handler relies on.

Scenario numbering matches
docs/test-plan/test-clusters.md § Planned Expansions → 9.7–9.10.
"""

from __future__ import annotations

import threading
import unittest

from django.test import SimpleTestCase
from lex.core.services.bitemporal_signals import (
    _suppress_history_valid_to_chaining,
    _suppress_main_table_sync,
    _suppress_meta_sys_to_chaining,
    suppress_history_valid_to_chaining,
    suppress_main_table_sync,
    suppress_meta_sys_to_chaining,
)

import pytest

pytestmark = pytest.mark.signals_ws


class TestCluster09_SuppressionGuards(SimpleTestCase):
    """Unit contract for the three bitemporal suppression guards."""

    # -- 9.7 -----------------------------------------------------------
    def test_9_7_guard_lifecycle_before_inside_after(self) -> None:
        """
        Scenario 9.7: Each guard flips the ContextVar to True on enter
        and resets to False on exit.

        If this contract drifts (exit doesn't reset), a single
        ``BitemporalSynchronizer.sync_record_for_model`` call leaks the
        suppression into the rest of the request — subsequent saves
        silently skip chaining and the bitemporal timeline goes
        inconsistent.
        """
        guards = [
            ("main_table_sync",           _suppress_main_table_sync,           suppress_main_table_sync),
            ("history_valid_to_chaining", _suppress_history_valid_to_chaining, suppress_history_valid_to_chaining),
            ("meta_sys_to_chaining",      _suppress_meta_sys_to_chaining,      suppress_meta_sys_to_chaining),
        ]
        for name, var, cm in guards:
            with self.subTest(guard=name):
                self.assertFalse(
                    var.get(),
                    f"{name} must default to False before any context is "
                    "entered — otherwise the very first save of a request "
                    "silently skips its bitemporal maintenance",
                )
                with cm():
                    self.assertTrue(
                        var.get(),
                        f"{name} must be True inside the `with` block",
                    )
                self.assertFalse(
                    var.get(),
                    f"{name} must reset to False on exit — a leaked True "
                    "is how the chaining bug at BUG-011 would compound",
                )

    # -- 9.8 -----------------------------------------------------------
    def test_9_8_nested_suspension_stacks_and_unwinds_correctly(self) -> None:
        """
        Scenario 9.8: The signal handlers themselves call
        ``with suppress_history_valid_to_chaining(), suppress_main_table_sync():``
        inside their body — so nested suspension has to work correctly.

        Contract: inner-exit must **not** deactivate the outer context.
        Only the outermost exit resets to False. This is the core
        property ``ContextVar.set()`` / ``reset(token)`` provides, but
        we lock it in with an explicit test so a future refactor (e.g.
        swapping to a plain module-level boolean) is caught.
        """
        var = _suppress_history_valid_to_chaining
        self.assertFalse(var.get(), "precondition: guard is off")

        with suppress_history_valid_to_chaining():          # outer
            self.assertTrue(var.get())
            with suppress_history_valid_to_chaining():      # inner
                self.assertTrue(var.get())
            # Inner exited — but outer is still active.
            self.assertTrue(
                var.get(),
                "Inner `with` exit must NOT switch off the outer "
                "suspension; otherwise the signal handler's internal "
                "`record.save(...)` re-triggers the very signal it is "
                "inside, causing unbounded recursion",
            )
        self.assertFalse(
            var.get(),
            "Outer exit must finally clear the guard — "
            "precondition for the next request on this thread",
        )

    # -- 9.9 -----------------------------------------------------------
    def test_9_9_three_guards_are_independent(self) -> None:
        """
        Scenario 9.9: Suspending one guard must not silently suspend
        another. The signal handlers rely on asymmetric combinations
        (e.g. ``sync_main_table`` enters ``suppress_main_table_sync``
        only), so a cross-contaminating implementation would break the
        chain maintenance while leaving the main-table sync disabled.
        """
        with suppress_main_table_sync():
            self.assertTrue(_suppress_main_table_sync.get())
            self.assertFalse(
                _suppress_history_valid_to_chaining.get(),
                "main_table_sync guard must NOT implicitly suspend "
                "history chaining — they serve orthogonal invariants",
            )
            self.assertFalse(
                _suppress_meta_sys_to_chaining.get(),
                "main_table_sync guard must NOT implicitly suspend "
                "meta-history chaining",
            )

        # And cross-check the symmetric case.
        with suppress_meta_sys_to_chaining():
            self.assertFalse(_suppress_main_table_sync.get())
            self.assertFalse(_suppress_history_valid_to_chaining.get())
            self.assertTrue(_suppress_meta_sys_to_chaining.get())

    # -- 9.10 ----------------------------------------------------------
    def test_9_10_suspension_does_not_leak_across_threads(self) -> None:
        """
        Scenario 9.10: ``ContextVar`` state is per-thread. A background
        thread that starts while another thread holds a suspension
        must see the guard as False — otherwise parallel requests in a
        Celery worker would silently share suspension state and a
        bitemporal-maintenance run on request A would corrupt request B.
        """
        observed_in_other_thread: dict = {}

        def _probe():
            # This thread inherits no ContextVar state from the parent
            # (that's the contract). If the guard were implemented
            # with a module-level global or a threading.local without
            # isolation, this would flip to True.
            observed_in_other_thread["value"] = _suppress_history_valid_to_chaining.get()

        with suppress_history_valid_to_chaining():
            self.assertTrue(
                _suppress_history_valid_to_chaining.get(),
                "precondition: this thread sees the guard active",
            )
            t = threading.Thread(target=_probe)
            t.start()
            t.join(timeout=1)

        self.assertIn(
            "value", observed_in_other_thread,
            "Probe thread did not complete within 1s — something is "
            "blocking on the guard state itself",
        )
        self.assertFalse(
            observed_in_other_thread["value"],
            "Guard leaked across threads — a Celery worker running "
            "parallel calculations would share suspension state and "
            "bitemporal chains would silently desync",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

