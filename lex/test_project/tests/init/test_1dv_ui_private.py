"""lex.ui: a result held privately on the server -- ``ui.private``.

Intent: a tool's result often holds personal data (names of persons, amounts). A backend field
(``_name``) stays out of the browser but is part of the state, which Reflex writes to Redis in
production (to disk in development): the data lands there, outlives the page and has no idle
limit. ``_report: dict = ui.private()`` is read and written like a backend field, but its value
stays in this server process's memory only, bound to the tab and the user who wrote it, and leaves
after an idle time, a maximum age, and when the tab opens a page again (unless ``keep=True``).
What a reader sees then is a plain sentence that says to start again -- never stale data and
never another user's. A regression is personal data in a state pickle or in the browser, a value
one user wrote read by another, a value that outlives its limits, or a page that silently shows
nothing (or old data) where the value was.

Cluster 1dv -- scenarios 1.920-1.926, 1.911. Type: U.
Covers: lex/lex_app/reflex/ui/private.py, lex/lex_app/reflex/ui/workcopy.py (fields_of, snapshot,
restore, reset, Draft), lex/lex_app/reflex/ui/state.py (_ui_private, ui_open),
lex/lex_app/reflex/ui/context.py (user_id_of), lex/lex_app/reflex/ui/testing.py (GONE),
lex/lex_app/reflex/ui/runs.py (the clock's sweep).
Run: python -m lex pytest lex/test_project/tests/init/test_1dv_ui_private.py -v
"""

from __future__ import annotations

import asyncio
import os
import pickle
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import context, private, rows as rows_mod, runs
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

SECRET = "Ada Lovelace"
GONE_TEXT = "This is no longer available: it is kept only for a while. Start again."


class PayrollState(ui.State):
    """A tool whose result names persons and amounts."""

    shown: int = 0
    note: str = ""
    _report: dict = ui.private(idle_minutes=60, max_hours=4)
    _draft: list[dict] = ui.private(keep=True)

    @ui.action
    def build(self, name: str):
        self._report = {"name": name, "amount": "1250.50"}
        self.shown = len(self._report)

    @ui.job
    def build_later(self, name: str):
        yield ui.progress(0.5, "Reading")
        self._report = {"name": name, "amount": "99"}

    @ui.action
    def remember(self, name: str):
        self._draft = [{"name": name}]

    @ui.action
    def read(self):
        self.note = self._report.get("name", "(nothing)")

    @ui.job
    def read_later(self):
        self.note = self._report.get("name", "(nothing)")

    @ui.action
    def read_then_fail(self):
        self._report = {"name": "changed"}
        raise RuntimeError("the payroll system is away")

    @ui.rows
    def lines(self):
        return [self._report] if self._report else []

    @ui.load
    def open(self):
        self.note = f"report={self._report.get('name', '-')} draft={len(self._draft)}"


def _session(steps, user: str = "tester"):
    async def scenario():
        async with testing.Harness(user=user) as harness:
            await harness.open_tab()
            return await steps(harness)

    return asyncio.run(scenario())


async def _send(harness, handler: str, state=PayrollState, **arguments):
    await (await harness.send(state, handler, **arguments))
    await asyncio.sleep(0.05)
    return await harness.fields(PayrollState)


def _every_state(root):
    yield root
    for sub in root.substates.values():
        yield from _every_state(sub)


class TestCluster01dv_Private(SimpleTestCase):
    """1.920-1.926: memory only; bound to its user; its limits; a new visit; what is shown; tests; errors."""

    def setUp(self) -> None:
        private.clear()
        self.addCleanup(private.clear)

    # -- 1.920 ---------------------------------------------------------
    def test_1_920_the_value_stays_in_memory_never_in_a_pickle_or_the_browser(self) -> None:
        """
        Scenario 1.920: where the value lives.
        Given: a state with `_report: dict = ui.private(...)`.
        When:  an action writes a person's name into it; another action, a job and a table's
               rows read it in the same tab; the state is serialised the way Reflex's Redis and
               disk managers store it, and as the browser is sent it.
        Then:  every reader gets the value; no update the browser received, no state pickle and
               no state dictionary contains the name -- the state holds only a random stamp.
        """
        async def steps(harness):
            from reflex.istate.manager.token import BaseStateToken

            built = await _send(harness, "build", name=SECRET)
            owner = await harness._state(PayrollState)
            rows = await rows_mod.read(PayrollState.lines, owner)
            root = owner._get_root_state()
            blobs = [BaseStateToken.serialize(one) for one in _every_state(root)]
            blobs += [pickle.dumps(one) for one in _every_state(root)]
            stamps = dict((await harness._state(State))._ui_private)
            sent, updates = str(root.dict()), list(harness.updates)
            read = await _send(harness, "read")            # copies the name into a public field: after the checks
            later = await _send(harness, "read_later")
            return built, read, later, rows, blobs, sent, updates, stamps

        built, read, later, rows, blobs, as_sent, updates, stamps = _session(steps)
        self.assertEqual((read["note"], later["note"]), (SECRET, SECRET), "an action and a job read it")
        self.assertEqual(rows, [{"name": SECRET, "amount": "1250.50"}], "and a table's rows")
        self.assertEqual(built["shown"], 2, "it is read and written like a backend field")
        for blob in blobs:
            self.assertNotIn(SECRET.encode(), blob, "never in what Reflex stores (Redis, disk)")
            self.assertNotIn(b"1250.50", blob)
        self.assertNotIn(SECRET, as_sent, "never in the state as the browser gets it")
        self.assertNotIn("_report", str(updates), "never in an update sent to the browser")
        where = f"{PayrollState.get_full_name()}._report"
        self.assertEqual(list(stamps), [where], "the state holds a stamp only")
        self.assertRegex(stamps[where], r"^[0-9a-f]{32}$")
        self.assertEqual(ui.testing.run(PayrollState, "read").fields["note"], "(nothing)",
                         "nothing kept: the empty value of its type, not a failure")

    # -- 1.921 ---------------------------------------------------------
    def test_1_921_another_user_or_another_process_never_sees_it(self) -> None:
        """
        Scenario 1.921: bound to the user who wrote it, and to this process.
        Given: a value written by anna in a tab.
        When:  bob is signed in on the same tab; and, for anna again, the state is read by a
               process that never held the value (its memory is empty, the state's stamp is not).
        Then:  bob's handler is refused with the plain sentence and never gets anna's data; the
               other process says the same; the browser keeps what it had.
        """
        async def steps(harness):
            await _send(harness, "build", name=SECRET)
            previous = context.set_user_id_of(lambda state: "bob")     # bob signs in on the same tab
            try:
                bob = await _send(harness, "read")
                bob_outcomes = list(harness.outcomes)
            finally:
                context.set_user_id_of(previous)
            harness.outcomes.clear()
            anna = await _send(harness, "read")
            private.clear()                                           # this process never held it
            elsewhere = await _send(harness, "read")
            return bob, bob_outcomes, anna, elsewhere, list(harness.outcomes)

        bob, bob_outcomes, anna, elsewhere, outcomes = _session(steps, user="anna")
        self.assertEqual([(o.kind, o.message) for o in bob_outcomes], [("refused", GONE_TEXT)],
                         "another user is told plainly that it is not there")
        self.assertEqual(bob["note"], "", "and never gets anna's data")
        self.assertEqual(anna["note"], SECRET, "anna still reads it")
        self.assertEqual(elsewhere["note"], SECRET, "unchanged in the page: the read was refused")
        self.assertEqual([(o.kind, o.message) for o in outcomes], [("refused", GONE_TEXT)],
                         "another process says it plainly, too")

    # -- 1.922 ---------------------------------------------------------
    def test_1_922_it_leaves_after_its_idle_time_and_its_maximum_age(self) -> None:
        """
        Scenario 1.922: the limits, and the clock that enforces them.
        Given: `idle_minutes=60, max_hours=4`; a clock the test moves.
        When:  it is read after 59 minutes, 59 minutes later again, and so on; written anew;
               left alone for 61 minutes; and the lex.ui clock runs.
        Then:  each read within the idle time keeps it; past 4 hours since it was written it is
               gone even though it was read; written anew it counts from then; idle 61 minutes
               it is gone; the clock drops expired values from memory on its own, and memory
               keeps at most LEX_UI_PRIVATE_VALUES values (the least recently used goes first).
        """
        now = [1000.0]

        async def steps(harness):
            seen = []
            with patch.object(private, "_now", lambda: now[0]):
                await _send(harness, "build", name=SECRET)
                for _ in range(4):                                 # 4 x 59 min: within the idle time each time
                    now[0] += 59 * 60
                    seen.append((await _send(harness, "read"))["note"])
                now[0] += 5 * 60                                  # 4 h 01 since it was written
                harness.outcomes.clear()
                await _send(harness, "read")
                seen.append([o.message for o in harness.outcomes])
                await _send(harness, "build", name="Grace Hopper")
                now[0] += 61 * 60
                harness.outcomes.clear()
                await _send(harness, "read")
                seen.append([o.message for o in harness.outcomes])
                await _send(harness, "build", name="Grace Hopper")
                now[0] += 61 * 60
                swept = private.sweep()
            return seen, swept

        seen, swept = _session(steps)
        self.assertEqual(seen[:4], [SECRET] * 4, "every read within the idle time keeps it")
        self.assertEqual(seen[4], [GONE_TEXT], "past its maximum age it is gone, read or not")
        self.assertEqual(seen[5], [GONE_TEXT], "after its idle time it is gone")
        self.assertEqual((swept, len(private._STORE)), (1, 0), "the clock drops it from memory on its own")

        with patch.dict(os.environ, {"LEX_UI_PRIVATE_VALUES": "2"}):
            async def many(harness):
                await _send(harness, "build", name="first")
                await _send(harness, "remember", name="second")
                await _send(harness, "read")                          # the report was used last
                await _send(harness, "remember", name="third")        # a new value: the draft
                harness.outcomes.clear()
                await _send(harness, "read")
                return len(private._STORE), list(harness.outcomes)

            held, outcomes = _session(many)
        self.assertEqual((held, outcomes), (2, []), "memory holds at most the setting; the used value stays")
        import inspect

        self.assertIn("private.sweep()", inspect.getsource(runs.heartbeat), "the lex.ui clock runs the sweep")

    # -- 1.923 ---------------------------------------------------------
    def test_1_923_a_new_visit_drops_it_unless_kept(self) -> None:
        """
        Scenario 1.923: leaving the page.
        Given: `_report` (not kept) and `_draft` (keep=True), both written.
        When:  the tab opens a page (the opener's event); then the page's load runs; and once
               more after `_draft` went past its idle time.
        Then:  the opener drops `_report` from memory, so reading it says it is gone; `_draft`
               stays; the load resets `_report` like every field and keeps the kept `_draft`;
               a kept value that is gone is reset by the load, so the page starts clean
               instead of failing on it (no error page, no loop through "Try again").
        """
        now = [1000.0]

        async def steps(harness):
            with patch.object(private, "_now", lambda: now[0]):
                await _send(harness, "build", name=SECRET)
                await _send(harness, "remember", name="Grace Hopper")
                await (await harness.send(State, "ui_open", page="/payroll"))
                harness.outcomes.clear()
                after_leave = await _send(harness, "read")
                read_outcomes = [o.message for o in harness.outcomes]
                loaded = await _send(harness, "open")
                stamps = dict((await harness._state(State))._ui_private)
                now[0] += 61 * 60
                harness.outcomes.clear()
                again = await _send(harness, "open")
                return after_leave, read_outcomes, loaded, stamps, again, list(harness.outcomes)

        after_leave, read_outcomes, loaded, stamps, again, outcomes = _session(steps)
        self.assertEqual(read_outcomes, [GONE_TEXT], "the value not kept left with the visit, and says so")
        self.assertEqual(after_leave["_draft"], [{"name": "Grace Hopper"}], "keep=True stays across visits")
        self.assertEqual(loaded["note"], "report=- draft=1", "the load reset the report and kept the draft")
        self.assertEqual(list(stamps), [f"{PayrollState.get_full_name()}._draft"], "a reset value keeps nothing, not even a stamp")
        self.assertEqual((again["note"], outcomes), ("report=- draft=0", []),
                         "a kept value that went is reset by the load: the page opens clean")

    # -- 1.924 ---------------------------------------------------------
    def test_1_924_what_the_user_sees_when_it_is_gone(self) -> None:
        """
        Scenario 1.924: the sentence, wherever the value is read.
        Given: a value that is gone.
        When:  an action reads it, a job reads it, a table reads it, and an action that had
               changed it fails.
        Then:  the action and the job are refused with the plain sentence (a toast; the job's
               also in ui.failed); the table shows the same sentence in its message; nothing
               of the page changes; a failed action leaves the gone value gone, not stale.
        """
        action = ui.testing.run(PayrollState, "read", fields={"_report": ui.testing.GONE})
        self.assertEqual((action.refused, [t.message for t in action.toasts]), (GONE_TEXT, [GONE_TEXT]))
        job = ui.testing.run(PayrollState, "read_later", fields={"_report": ui.testing.GONE})
        self.assertEqual(job.failed, {"PayrollState.read_later": GONE_TEXT}, "a job's refusal stays in place")
        self.assertEqual((action.fields["note"], job.fields["note"]), ("", ""), "nothing else changed")
        with self.assertRaises(ui.Refuse) as raised:
            ui.testing.rows(PayrollState.lines, fields={"_report": ui.testing.GONE})
        self.assertEqual(raised.exception.message, GONE_TEXT)
        self.assertEqual(rows_mod.describe(raised.exception, "PayrollState.lines", ""), GONE_TEXT,
                         "the table's message is the sentence")
        failed = ui.testing.run(PayrollState, "read_then_fail", fields={"_report": ui.testing.GONE})
        self.assertIn("Reference:", failed.error)
        self.assertIs(failed.fields["_report"], ui.testing.GONE, "the failure's change was discarded: still gone")
        restored = ui.testing.run(PayrollState, "read_then_fail", fields={"_report": {"name": SECRET}})
        self.assertEqual(restored.fields["_report"], {"name": SECRET}, "a kept value is restored like any field")

    # -- 1.925 ---------------------------------------------------------
    def test_1_925_ui_testing_seeds_and_reads_it_like_any_field(self) -> None:
        """
        Scenario 1.925: a project's own tests.
        Given: `ui.testing.run` and `ui.testing.rows`.
        When:  a private field is seeded with `fields=`, or seeded as `ui.testing.GONE`.
        Then:  the handler reads the seeded value; `result.fields` holds what it wrote; a
               gone value is `ui.testing.GONE` there; GONE copies and pickles as itself.
        """
        result = ui.testing.run(PayrollState, "read", fields={"_report": {"name": SECRET}})
        self.assertEqual(result.fields["note"], SECRET)
        built = ui.testing.run(PayrollState, "build", name="Grace Hopper")
        self.assertEqual(built.fields["_report"], {"name": "Grace Hopper", "amount": "1250.50"})
        self.assertEqual(ui.testing.rows(PayrollState.lines, fields={"_report": {"name": SECRET}}), [{"name": SECRET}])
        gone = ui.testing.run(PayrollState, "build_later", name="Grace Hopper", fields={"_report": ui.testing.GONE})
        self.assertEqual(gone.fields["_report"], {"name": "Grace Hopper", "amount": "99"}, "a job writes it anew")
        self.assertIs(ui.testing.run(PayrollState, "read", fields={"_report": ui.testing.GONE}).fields["_report"],
                      ui.testing.GONE)
        import copy

        self.assertIs(copy.deepcopy(ui.testing.GONE), ui.testing.GONE)
        self.assertIs(pickle.loads(pickle.dumps(ui.testing.GONE)), ui.testing.GONE)
        self.assertIs(ui.testing.GONE, private.GONE)

    # -- 1.926 ---------------------------------------------------------
    def test_1_926_a_private_field_declared_wrongly_says_what_to_write(self) -> None:
        """
        Scenario 1.926: the declaration.
        Given: `ui.private(...)` on a state.
        When:  it has a public name, no type, a plain Reflex state, a limit of 0 or a keep= that is
               not True/False.
        Then:  each is refused when the class is defined, saying what to write; a field's
               empty value comes from its type.
        """
        with self.assertRaisesRegex(TypeError, "a private field's name starts with one '_'"):
            type("Wrong1", (ui.State,), {"__annotations__": {"report": dict}, "report": ui.private(),
                                         "__module__": __name__})
        with self.assertRaisesRegex(TypeError, "`_untyped: dict = ui.private\\(\\)`"):
            type("Wrong2", (ui.State,), {"_untyped": ui.private(), "__module__": __name__})
        import reflex as rx

        with self.assertRaisesRegex(TypeError, r"ui.private\(\) is a field of a ui.State"):
            type("Wrong3", (rx.State,), {"__annotations__": {"_report": dict}, "_report": ui.private(),
                                         "__module__": __name__})
        with self.assertRaisesRegex(ValueError, r"ui.private\(idle_minutes=\) is a number above 0; got 0"):
            ui.private(idle_minutes=0)
        with self.assertRaisesRegex(ValueError, r"max_hours=\) is a number above 0"):
            ui.private(max_hours=True)
        with self.assertRaisesRegex(TypeError, r"ui.private\(keep=\) is True or False"):
            ui.private(keep="yes")
        self.assertEqual((PayrollState.__dict__["_report"].empty(), PayrollState.__dict__["_draft"].empty()), ({}, []))
        self.assertIn("_report", PayrollState.__dict__)
        self.assertNotIn("_report", PayrollState.backend_vars, "not a backend var: Reflex never stores it")
        self.assertNotIn("_report", PayrollState.base_vars, "nor a var the browser gets")

    # -- 1.911 ---------------------------------------------------------
    def test_1_911_a_computed_var_reading_a_private_field_is_refused_when_the_class_is_defined(self) -> None:
        """
        Scenario 1.911: a computed var never reads a private field.
        Given: Reflex recomputes a computed var whenever it sends the state, and caches it by the
               fields it reads -- which a private field is not; read after the value is gone it
               raises in every later event of the tab.
        When:  a ui.State declares `@rx.var` reading a private field -- cached, `cache=False`,
               through a helper method -- and, beside it, one reading a backend field.
        Then:  each one reading the private field is refused when the class is defined, naming
               the var and the field and saying to read it in a `@ui.rows` handler or copy what
               the page shows into a field; the one reading a backend field is accepted.
        """
        import reflex as rx

        sentence = (r"ReadsPrivate\d?\.count reads the private field _lines.*a computed var is worked out "
                    r"whenever the page is sent.*@ui\.rows.*copy what the page shows into a field")
        with self.assertRaisesRegex(TypeError, sentence):
            class ReadsPrivate(ui.State):
                _lines: list = ui.private()

                @rx.var
                def count(self) -> int:
                    return len(self._lines)
        with self.assertRaisesRegex(TypeError, sentence):
            class ReadsPrivate2(ui.State):
                _lines: list = ui.private()

                @rx.var(cache=False)
                def count(self) -> int:
                    return len(self._lines)
        with self.assertRaisesRegex(TypeError, sentence):
            class ReadsPrivate3(ui.State):
                _lines: list = ui.private()

                def _helper(self) -> int:
                    return len(self._lines)

                @rx.var
                def count(self) -> int:
                    return self._helper()

        class ReadsBackend(ui.State):
            _plain: list = []

            @rx.var
            def count(self) -> int:
                return len(self._plain)

        fine = ReadsBackend
        self.assertIn("count", fine.computed_vars, "a computed var over a backend field is Reflex's own, and stays")
