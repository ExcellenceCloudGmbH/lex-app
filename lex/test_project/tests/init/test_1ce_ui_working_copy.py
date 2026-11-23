"""lex.ui: the working copy a load or job works on, and the snapshot a failed handler goes back to.

Intent: a job's body must not hold the user's lock, so it works on a copy of the state's fields;
only what it changed is written back, so what the user changed meanwhile survives. A failed
handler's changes are discarded by restoring a snapshot. A regression is a job overwriting the
user's input with stale values, or half-finished changes staying on screen after an error.

Cluster 1ce -- scenarios 1.753-1.755. Type: U.
Covers: lex/lex_app/reflex/ui/workcopy.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ce_ui_working_copy.py -v
"""

from __future__ import annotations

from typing import ClassVar
from unittest import TestCase

import pytest
import reflex as rx

from lex.lex_app.reflex.ui import workcopy

pytestmark = pytest.mark.init


class _Parent(rx.State):
    shared: int = 0


class _Tickets(_Parent):
    LIMIT: ClassVar[int] = 5
    rows: list[dict] = []
    note: str = ""
    progress: int = 0
    _cursor: int = 0

    @rx.var
    def total(self) -> int:
        return len(self.rows)

    def _double(self) -> int:
        return self.progress * 2

    @rx.event
    def refresh(self):
        pass


class _Fake:
    """Stands in for a live state: plain attributes, same class contract."""

    def __init__(self, **values):
        self.__dict__.update(values)


class TestCluster01ce_WorkingCopy(TestCase):
    """1.753-1.755."""

    def _live(self, **values):
        live = _Fake(rows=[], note="", progress=0, _cursor=0)
        live.__dict__.update(values)
        live.__class__ = type("Live", (_Fake,), {"base_vars": _Tickets.base_vars, "backend_vars": _Tickets.backend_vars,
                                                 "inherited_backend_vars": _Tickets.inherited_backend_vars,
                                                 "get_fields": _Tickets.get_fields})
        return live

    # -- 1.753 ---------------------------------------------------------
    def test_1_753_a_snapshot_holds_the_states_own_fields_and_restores_them(self) -> None:
        """
        Scenario 1.753: snapshot, restore and reset.
        Given: a state with its own fields (one of them a backend field) and an inherited one.
        When:  it is snapshotted, changed in place, restored; then reset with one field kept.
        Then:  the snapshot holds deep copies of the class's own fields only; restore puts every
               changed field back; reset sets fields to their class defaults except the kept one.
        """
        self.assertEqual(set(workcopy.fields_of(_Tickets)), {"rows", "note", "progress", "_cursor"},
                         "own fields, the backend one included, not the inherited one")
        live = self._live(rows=[{"id": 1}], note="x")
        snap = workcopy.snapshot(live)
        live.rows.append({"id": 2})
        live.note = "changed"
        self.assertEqual(snap["rows"], [{"id": 1}], "the snapshot is a deep copy")
        workcopy.restore(live, snap)
        self.assertEqual((live.rows, live.note), ([{"id": 1}], "x"), "restore puts changed fields back")
        live.progress = 9
        workcopy.reset(live, keep=("note",))
        self.assertEqual((live.rows, live.note, live.progress, live._cursor), ([], "x", 0, 0),
                         "reset to defaults, the kept field untouched")

    # -- 1.754 ---------------------------------------------------------
    def test_1_754_a_draft_reads_and_writes_fields_and_reaches_helpers(self) -> None:
        """
        Scenario 1.754: what a job body sees as self.
        Given: a draft of the state's fields.
        When:  the body reads and assigns fields, changes a list in place, reads a computed
               value, a class constant and an underscore helper, and misuses it.
        Then:  fields behave as on the state; the computed value and the helper work on the
               draft's values; assigning an undeclared field and calling another event handler
               are refused with a message that says what to do.
        """
        draft = workcopy.Draft(_Tickets, {"rows": [{"id": 1}], "note": "", "progress": 3, "_cursor": 0})
        draft.rows.append({"id": 2})
        draft.note = "typed"
        self.assertEqual((draft.total, draft._double(), draft.LIMIT), (2, 6, 5),
                         "computed values, helpers and constants work on the draft")
        self.assertEqual(set(draft.changed()), {"rows", "note"}, "in-place changes and assignments are both seen")
        with self.assertRaises(AttributeError) as undeclared:
            draft.nope = 1
        self.assertIn("declare it", str(undeclared.exception))
        with self.assertRaises(AttributeError) as handler:
            draft.refresh()
        self.assertIn("helper method", str(handler.exception))

    # -- 1.755 ---------------------------------------------------------
    def test_1_755_merge_writes_only_what_the_draft_changed(self) -> None:
        """
        Scenario 1.755: merging a job's changes.
        Given: a live state and a draft taken from it; then the user changes `note` on the
               live state while the job changes `progress` and `rows` on the draft.
        When:  the draft is merged, and merged again after both change `note`.
        Then:  the first merge writes progress and rows and leaves the user's note; the draft
               then sees the user's note; when both changed the same field, the job's value wins.
        """
        live = self._live(note="")
        draft = workcopy.Draft(_Tickets, workcopy.snapshot(live))
        live.note = "typed-by-user"
        draft.progress = 50
        draft.rows.append({"id": 1})
        self.assertEqual(sorted(workcopy.merge(live, draft)), ["progress", "rows"])
        self.assertEqual((live.note, live.progress, live.rows), ("typed-by-user", 50, [{"id": 1}]))
        self.assertEqual(draft.note, "typed-by-user", "after a merge the draft sees the user's change")
        self.assertEqual(draft.changed(), {}, "nothing is pending after a merge")
        live.note = "user-again"
        draft.note = "set-by-job"
        workcopy.merge(live, draft)
        self.assertEqual(live.note, "set-by-job", "the job wins a same-field conflict")
