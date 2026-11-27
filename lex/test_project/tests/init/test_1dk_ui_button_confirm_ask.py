"""lex.ui: a button that asks first -- confirm=, ask= -- and that shows its handler running.

Intent: closing, deleting and declining need a question or a reason first. Written by hand that
is a dialog, an open flag, a field in the state and two handlers, on every page. On a `ui.button`
it is one argument: `confirm="Close this order?"` asks, `ask=[ui.field("reason", required=True)]`
asks for values and hands them to the handler, checked like a form's. A dangerous action must
ask. And every button shows its handler running, so no page keeps a busy flag. A regression is a
dangerous action that runs on one click, a dialog per table row, a reason that reaches the
handler unchecked, or a button that can be pressed again while its handler runs.

Cluster 1dk -- scenarios 1.847-1.851. Type: U.
Covers: lex/lex_app/reflex/ui/pages/vocab.py (button confirm=, ask=, Button), lex/lex_app/reflex/ui/pages/forms.py
(dialog), lex/lex_app/reflex/ui/pages/parts.py (with_overlays), lex/lex_app/reflex/ui/state.py (ui_ask,
ui_ask_close).
Run: python -m lex pytest lex/test_project/tests/init/test_1dk_ui_button_confirm_ask.py -v
"""

from __future__ import annotations

import asyncio

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.parts import Build, PageError
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init


class TicketsState(ui.State):
    """Tickets that can be closed, declined and exported."""

    name: str = "Ticket one"
    declined: dict = {}

    @ui.rows
    def tickets(self):
        return [{"id": 1, "name": "Ticket one"}, {"id": 2, "name": "Ticket two"}]

    @ui.action
    def close(self):
        return None

    @ui.action
    def remove(self, key: str):
        return None

    @ui.action
    def decline(self, key, values):
        self.declined = {"key": key, **values}

    @ui.action
    def reject(self, key, values):
        return None

    @ui.action
    def note(self, values):
        return None

    @ui.job
    def export(self):
        yield ui.progress(0.5, "Half")

    @ui.custom
    def raw(self):
        return None


def tickets_page():
    """Stands for a page declared with @ui.page("/tickets")."""


tickets_page.__ui_page__ = links.info_of("/tickets", "tickets_page")


def _text(component: rx.Component) -> str:
    return str(component) + "\n".join(component._get_all_hooks())


def _record(**slots):
    return ui.record(title=TicketsState.name, sections=[ui.section("Details", ui.text("Hello"))], **slots)


CTX = Build(page="ticket", route="/tickets/[key]")


class TestCluster01dk_ButtonAsks(SimpleTestCase):
    """1.847-1.851: confirm=; danger must confirm; ask=; the button shows its handler; the dialog's events."""

    # -- 1.847 ---------------------------------------------------------
    def test_1_847_confirm_asks_before_the_handler_runs(self) -> None:
        """
        Scenario 1.847: ui.button(confirm=).
        Given: a record page whose only action is a dangerous button with a question; and a
               question that names nothing.
        When:  the page is built.
        Then:  pressing the button opens its dialog instead of running the handler; the dialog
               is RFDS's confirmation, beside the page, open while it is the one asked for, with
               the question as its title and the button's label on the button that agrees, in
               the danger tone; agreeing closes it and runs the handler; "Are you sure?" and a
               sentence that is no question are refused with what to write; confirm= with to=
               is refused.
        """
        ctx = Build(page="ticket", route="/tickets/[key]")
        text = _text(_record(actions=[ui.button("Close ticket", TicketsState.close, tone="danger",
                                                confirm="Close this ticket?")]).build(ctx))
        self.assertEqual(ctx.overlays, [], "the dialogs were placed, once")
        opened = text[:text.index("rfds-confirm-dialog")]
        self.assertIn('ui_ask", ({ ["dialog"] : "ticket-ask-', opened, "the button opens its dialog")
        self.assertNotIn("tickets_state.close", opened, "and does not run the handler itself")
        dialog = text[text.index("AlertDialog"):]
        for needle, why in (('ui_asking_rx_state_?.valueOf?.() === "ticket-ask-', "open while it is the one asked for"),
                            ('rfdsI18n.t("Close this ticket?", "Close this ticket?"', "the question is the title"),
                            ('rfdsI18n.t("Close ticket", "Close ticket"', "the button that agrees repeats the label"),
                            ('"data-rfds-tone":"danger"', "a dangerous action's confirmation looks it"),
                            ("ui_ask_close", "agreeing or dismissing closes it"),
                            ('tickets_state.close", ({  })', "agreeing runs the handler")):
            with self.subTest(needle=needle):
                self.assertIn(needle, dialog, why)
        self.assertLess(dialog.index('ui_ask_close", ({ ["open"] : false })'), dialog.index("tickets_state.close"),
                        "the dialog closes first: the button on the page then shows the handler running")
        for question in ("Are you sure?", "Close the ticket"):
            with self.subTest(question=question), self.assertRaises(PageError) as vague:
                ui.button("Close ticket", TicketsState.close, confirm=question).build(Build(page="ticket", slot="actions"))
            self.assertEqual(str(vague.exception),
                             f"Page `ticket`, slot `actions`: `confirm=` is `{question}`.\nAsk what is about to "
                             "happen, naming the action and its object, as a question: `confirm=\"Close this order?\"`. "
                             "The button to agree repeats the button's label.")
        with self.assertRaises(ValueError, msg="a to= button opens a page; there is nothing to confirm"):
            ui.button("Tickets", to=tickets_page, confirm="Open the tickets?")
        with self.assertRaises(TypeError):
            ui.button("Close", TicketsState.close, confirm=True)

    # -- 1.848 ---------------------------------------------------------
    def test_1_848_a_dangerous_button_must_ask(self) -> None:
        """
        Scenario 1.848: tone="danger" without confirm=.
        Given: a dangerous button without a question -- as a button, as an entry of the page
               header's menu, and on each row of a table.
        When:  each is built.
        Then:  each is refused, naming the page, the slot and the button, with what to write;
               with a question each builds; a dangerous button with ask= builds too (its
               dialog is the question); a table's rows share one dialog.
        """
        message = ("the button `Remove` has `tone=\"danger\"` and no `confirm=`.\nAsk before it runs, naming the "
                   "action and its object: `ui.button(\"Close order\", State.close, tone=\"danger\", "
                   "confirm=\"Close this order?\")`.")
        bare = ui.button("Remove", TicketsState.close, tone="danger")
        with self.assertRaises(PageError) as alone:
            bare.build(Build(page="ticket", slot="sections[0]"))
        self.assertEqual(str(alone.exception), f"Page `ticket`, slot `sections[0]`: {message}")
        with self.assertRaises(PageError) as in_menu:
            _record(actions=[ui.button("Edit", TicketsState.close), ui.button("Export", TicketsState.export), bare]).build(CTX)
        self.assertEqual(str(in_menu.exception), f"Page `ticket`, slot `actions`: {message}")
        with self.assertRaises(PageError) as in_rows:
            ui.embed(ui.table(TicketsState.tickets, columns=["name"], row_actions=lambda row: [
                ui.button("Remove", TicketsState.remove, row["id"], tone="danger")]), name="tickets")
        self.assertEqual(str(in_rows.exception), f"Page `tickets`, slot `row_actions`: {message}")
        table = _text(ui.embed(ui.table(TicketsState.tickets, columns=["name"], row_actions=lambda row: [
            ui.button("Remove", TicketsState.remove, row["id"], tone="danger", confirm="Remove this ticket?")]),
            name="tickets"))
        self.assertEqual(table.count('"data-slot":"rfds-confirm-dialog"'), 1, "one dialog for the table, not one per row")
        self.assertIn('["args"] : [r_rx_state_?.["id"]]', table, "the row's key is kept with the open dialog")
        self.assertIn('tickets_state.remove", ({ ["key"] : reflex___state____state__lex___lex_app___reflex___ui___state'
                      '____state.ui_asking_args_rx_state_?.at?.(0) })', table, "and passed to the handler on agreeing")
        asking = ui.button("Reject", TicketsState.reject, "7", tone="danger", ask=[ui.field("reason")])
        asking.build(Build(page="ticket"))

    # -- 1.849 ---------------------------------------------------------
    def test_1_849_ask_opens_a_small_dialog_and_hands_its_values_to_the_handler(self) -> None:
        """
        Scenario 1.849: ui.button(ask=[ui.field(...)]).
        Given: a button on each row that asks for a required reason, with a question as well.
        When:  the table is built; and the dialog's form is sent empty, then with a reason,
               while the dialog is open.
        Then:  the button opens RFDS's dialog, titled with the button's label and described by
               the question, holding RFDS's form with the field and a Cancel button; sending
               it passes the row's key and then the values to the handler. Sent empty, the
               body does not run, the field has its message and the dialog stays open; with a
               reason the dialog closes and the body receives the key and the checked values.
               A handler that does not take the values, and fields declared wrongly, are
               refused with what to write.
        """
        table = _text(ui.embed(ui.table(TicketsState.tickets, columns=["name"], row_actions=lambda row: [
            ui.button("Decline", TicketsState.decline, row["id"], confirm="Decline this ticket?",
                      ask=[ui.field("reason", required=True, help="The requester reads this.")])]), name="tickets"))
        for needle, why in (('"data-slot":"rfds-dialog"', "RFDS's dialog"),
                            ('"rfds-dialog-title"},rfdsI18n.t("Decline", "Decline"', "titled with the button's label"),
                            ('rfdsI18n.t("Decline this ticket?", "Decline this ticket?"', "described by the question"),
                            ('"data-slot":"rfds-form"', "RFDS's form"),
                            ('rfdsI18n.t("Reason", "Reason"', "the field"),
                            ('rfdsI18n.t("The requester reads this.", "The requester reads this."', "its help"),
                            ('ui_invalid_rx_state_?.["TicketsState.decline|reason"]', "the server's message at the field"),
                            ('rfdsI18n.t("Cancel", "Cancel"', "a way out"),
                            ('ui_running_rx_state_?.["TicketsState.decline"]', "its button shows the handler running"),
                            ('tickets_state.decline", ({ ["key"] : reflex___state____state__lex___lex_app___reflex___ui'
                             '___state____state.ui_asking_args_rx_state_?.at?.(0), ["values"] : form_data })',
                             "sending passes the row's key, then the values")):
            with self.subTest(needle=needle):
                self.assertIn(needle, table, why)
        self.assertEqual(table.count('"data-slot":"rfds-dialog"'), 1, "one dialog for the table")

        async def answered(reason: str):
            async with testing.Harness() as harness:
                await harness.open_tab()
                await (await harness.send(State, "ui_ask", dialog="tickets-ask-1", args=[2]))
                await (await harness.send(TicketsState, "decline", key=2, values={"reason": reason}))
                return await harness.fields(TicketsState)

        empty = asyncio.run(answered(" "))
        self.assertEqual((empty["declined"], empty["ui_asking"]), ({}, "tickets-ask-1"), "the dialog stays open")
        self.assertEqual(empty["ui_invalid"], {"TicketsState.decline|reason": "Enter a value."})
        given = asyncio.run(answered("Out of scope"))
        self.assertEqual(given["declined"], {"key": 2, "reason": "Out of scope"}, "the key, then the checked values")
        self.assertEqual((given["ui_asking"], given["ui_invalid"]), ("", {}), "the dialog closes")

        ctx = Build(page="ticket", slot="actions")
        with self.assertRaises(PageError) as no_values:
            ui.button("Decline", TicketsState.remove, "7", ask=[ui.field("reason")]).build(ctx)
        self.assertEqual(str(no_values.exception),
                         "Page `ticket`, slot `actions`: `TicketsState.remove` takes (self, key).\nIt receives what was "
                         "entered as its last argument, called `values`: `def remove(self, key, values): ...`; "
                         "`values[\"name\"]` is the field `ui.field(\"name\")`.")
        with self.assertRaises(PageError) as twice:
            ui.button("Note", TicketsState.note, ask=[ui.field("text"), ui.field("text")]).build(ctx)
        self.assertEqual(str(twice.exception),
                         "Page `ticket`, slot `actions.ask`: two fields have the key `text`.\n"
                         "The handler finds each value under its field's key: give each field its own.")
        with self.assertRaises(PageError) as not_a_field:
            ui.button("Note", TicketsState.note, ask=[ui.text("Why?")]).build(ctx)
        self.assertIn("slot `actions.ask`: got `ui.text`.\n`ask=` holds `ui.field(", str(not_a_field.exception))

    # -- 1.850 ---------------------------------------------------------
    def test_1_850_a_button_shows_its_handler_running(self) -> None:
        """
        Scenario 1.850: the button knows its handler.
        Given: buttons for an action, for a job, for a handler with a row's key, for a raw
               Reflex handler and for a page.
        When:  they are built.
        Then:  an action's and a job's button show the handler's runs; a row's button only the
               run for its own key; a raw handler's button and a button that opens a page show
               nothing, as lex-app keeps no status for them.
        """
        ctx = Build(page="ticket")
        for name, handler in (("action", TicketsState.close), ("job", TicketsState.export)):
            with self.subTest(handler=name):
                built = str(ui.button("Go", handler).build(ctx))
                self.assertIn(f'ui_running_rx_state_?.["{handler.fn.__ui_key__}"] : []).length > 0),onClick', built,
                              "loading while the handler has a run")
        keyed = str(ui.button("Remove", TicketsState.remove, "7").build(ctx))
        self.assertIn('ui_running_rx_state_?.["TicketsState.remove"] : []).includes("7"),onClick', keyed,
                      "a row's button: loading while there is a run for its key")
        self.assertIn("loading:false", str(ui.button("Raw", TicketsState.raw).build(ctx)), "no status is kept for a raw handler")
        self.assertIn("loading:false", str(ui.button("Tickets", to=tickets_page).build(ctx)), "nor for opening a page")

    # -- 1.851 ---------------------------------------------------------
    def test_1_851_the_dialog_is_opened_and_closed_on_the_server(self) -> None:
        """
        Scenario 1.851: which dialog is open is the tab's state.
        Given: a tab with a message left at a field.
        When:  a button asks; the dialog reports that it stays open; and that it was dismissed.
        Then:  asking records the dialog and the button's values and clears old messages;
               staying open changes nothing; dismissing forgets the dialog and the values.
        """
        async def scenario():
            async with testing.Harness() as harness:
                await harness.open_tab()
                await harness.set_fields(State, ui_invalid={"TicketsState.decline|reason": "Enter a value."})
                seen = []
                for handler, arguments in (("ui_ask", {"dialog": "tickets-ask-1", "args": [2]}),
                                           ("ui_ask_close", {"open": True}), ("ui_ask_close", {"open": False})):
                    await (await harness.send(State, handler, **arguments))
                    state = await harness._state(State)
                    seen.append((state.ui_asking, list(state.ui_asking_args), dict(state.ui_invalid)))
                return seen

        self.assertEqual(asyncio.run(scenario()),
                         [("tickets-ask-1", [2], {}), ("tickets-ask-1", [2], {}), ("", [], {})])
