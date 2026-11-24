"""lex.ui forms: a choice searched on the server -- one record among thousands, as the user types.

Intent: "which customer?" among thousands, or among people whose names must not all reach the
browser. A consultant reuses what a table already reads: `ui.field("customer",
options=OrdersState.customers)` names a `@ui.rows` handler, each row giving `id` (what the
handler receives) and `label` (the words the user reads). The user types; lex-app runs the rows
handler as the user, on the server, and sends the browser only a page of matches -- id and label,
nothing else of the row. The first page is there when the field opens. The value that comes back
is checked against what this tab was actually offered, so a value typed into the request by hand
is refused at the field. A regression is a list of every customer in the page, a row's other
fields (an e-mail address) in the browser, a forged id reaching the handler's body, or an empty
box until the user types.

Cluster 1ds -- scenarios 1.900-1.903, 1.912. Type: U.
Covers: lex/lex_app/reflex/ui/forms.py (Expect.search, check, clean, search, MATCHES),
lex/lex_app/reflex/ui/pages/forms.py (field, Field.check, Field.expect, Field.control),
lex/lex_app/reflex/ui/state.py (ui_search, ui_matches).
Run: python -m lex pytest lex/test_project/tests/init/test_1ds_ui_form_searched_choice.py -v
"""

from __future__ import annotations

import asyncio

import pytest
import reflex as rx
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui import forms, testing
from lex.lex_app.reflex.ui.pages.parts import Build, PageError
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

#: 120 customers; each row also holds an e-mail address, which must never reach the browser.
CUSTOMERS = [{"id": number, "label": f"Customer {number:03d}", "email": f"c{number}@example.test"}
             for number in range(1, 121)]
REGIONS = [{"id": "north", "label": "Nördlich"}, {"id": "south", "label": "Südlich"},
           {"id": "east", "label": "Östlich"}]
#: The search texts the customers handler was given, in order.
SEEN: list[str] = []


class OrdersState(ui.State):
    """Orders placed for a customer chosen among many, routed to a region."""

    got: dict = {}
    runs: int = 0

    @ui.rows
    def customers(self, search):
        SEEN.append(search)
        return [row for row in CUSTOMERS if search.casefold() in row["label"].casefold()]

    @ui.rows
    def regions(self):
        return REGIONS                     # no `search`: lex-app searches the labels itself

    @ui.rows
    def unlabelled(self, search):
        return [{"id": 1, "name": "Customer 001"}]

    @ui.rows
    def orders(self):
        return [{"id": 1, "name": "Order one"}]

    @ui.action
    def place(self, values):
        self.runs += 1
        self.got = {"customer": values["customer"], "type": type(values["customer"]).__name__}

    @ui.action
    def route(self, values):
        self.got = {"region": values["region"], "type": type(values["region"]).__name__}

    @ui.action
    def broken(self, values):
        return None

    @ui.action
    def plan(self, values):
        self.got = dict(values)

    @ui.action
    def assign(self, key, values):
        self.got = {"key": key, "customer": values["customer"]}

    @ui.action
    def wrong(self, values):
        return None


#: Holds the warehouses search while it is set: a slow search in another system.
SLOW = {"gate": None}


class DeliveryState(ui.State):
    """A delivery: a customer and a warehouse, each searched on the server, on one form."""

    got: dict = {}
    clicks: int = 0

    @ui.rows
    def customers(self, search):
        return [row for row in CUSTOMERS if search.casefold() in row["label"].casefold()]

    @ui.rows
    def warehouses(self, search):
        gate = SLOW["gate"]
        if gate is not None and search == "slow" and not gate.wait(5):
            raise RuntimeError("the gate was never opened")
        return [{"id": f"w{n}", "label": f"Warehouse {search} {n}"} for n in range(1, 3)]

    @ui.action
    def deliver(self, values):
        self.got = dict(values)

    @ui.action
    def click(self):
        self.clicks += 1


def deliver_order():
    return ui.form(title="Deliver", submit=DeliveryState.deliver,
                   fields=[ui.field("customer", options=DeliveryState.customers),
                           ui.field("warehouse", options=DeliveryState.warehouses)])


def place_order():
    return ui.form(title="Place order", submit=OrdersState.place,
                   fields=[ui.field("customer", options=OrdersState.customers, required=True)])


def route_order():
    return ui.form(title="Route order", submit=OrdersState.route,
                   fields=[ui.field("region", options=OrdersState.regions)])


def broken_order():
    return ui.form(title="Broken order", submit=OrdersState.broken,
                   fields=[ui.field("customer", options=OrdersState.unlabelled)])


def plan_order():
    return ui.form(title="Plan order", submit=OrdersState.plan, steps=[
        ui.step("Who", fields=[ui.field("customer", options=OrdersState.customers, required=True)]),
        ui.step("When", fields=[ui.field("year", kind="year", required=True)])])


def _assigning():
    return ui.embed(ui.table(OrdersState.orders, columns=["name"], row_actions=lambda row: [
        ui.button("Assign", OrdersState.assign, row["id"],
                  ask=[ui.field("customer", options=OrdersState.customers, required=True)])]), name="orders")


def _build() -> None:
    """What lex-app does when it registers the pages: each is built, which tells the handlers their fields."""
    for page in (place_order, route_order, broken_order, plan_order, deliver_order):
        page().build(Build(page=page.__name__, route=f"/{page.__name__}"))
    _assigning()


def _searched(handler) -> str:
    """The searched field of ``handler``'s form: where the server keeps what it offered."""
    return next(one.search for one in forms.of(handler.fn.__ui_fn__).fields if one.search)


def _text(component: rx.Component) -> str:
    """The built page as the browser gets it: its elements, and the functions its forms call."""
    return str(component) + "\n".join(component._get_all_hooks())


async def _session(steps):
    async with testing.Harness() as harness:
        await harness.open_tab()
        return await steps(harness)


async def _search(harness, handler, text: str) -> list:
    """The user typed ``text`` into the searched field of ``handler``'s form; the matches the browser got."""
    field = _searched(handler)
    await (await harness.send(State, "ui_search", field=field, text=text)).wait_all()
    return list(dict((await harness._state(State)).ui_matches).get(field, []))


class TestCluster01ds_SearchedChoice(SimpleTestCase):
    """1.900-1.903: what a search offers; what the server accepts; the field on a page; declared wrongly."""

    def setUp(self) -> None:
        _build()
        SEEN.clear()

    # -- 1.900 ---------------------------------------------------------
    def test_1_900_a_search_offers_a_page_of_matches_and_nothing_else_of_the_rows(self) -> None:
        """
        Scenario 1.900: the user types into a searched choice.
        Given: 120 customers whose rows also hold an e-mail address; regions from a rows
               handler that takes no search; a rows handler whose rows have no label.
        When:  the field opens (an empty search), then the user types.
        Then:  the rows handler is run with the text, tidied; the browser gets at most a page
               of matches, each its value (the row's id, as text) and its label -- nothing else
               of the row ever reaches the browser; without `search` lex-app matches the typed
               words against the labels, folding umlauts as RFDS does; rows without a label fail
               as a table's rows do -- logged with what to write and a reference, the field showing
               the reference; an unknown field is ignored.
        """

        async def steps(harness):
            first = await _search(harness, OrdersState.place, "")
            typed = await _search(harness, OrdersState.place, "  customer   07 ")
            region = await _search(harness, OrdersState.route, "nord")
            await _search(harness, OrdersState.broken, "")
            invalid = dict((await harness._state(State)).ui_invalid)
            await (await harness.send(State, "ui_search", field="nowhere.Nobody.nothing|customer", text="a"))
            return first, typed, region, invalid, list(harness.updates)

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as logs:
            first, typed, region, invalid, updates = asyncio.run(_session(steps))
        self.assertEqual(len(first), forms.MATCHES, "a page of matches when the field opens, not all 120")
        self.assertEqual(first[0], {"value": "1", "label": "Customer 001"}, "the id as the value, and the label")
        self.assertEqual(SEEN[:2], ["", "customer 07"], "the rows handler gets the text, its spaces tidied")
        self.assertEqual([one["label"] for one in typed], [f"Customer 07{digit}" for digit in range(10)],
                         "what the handler found for the text")
        self.assertTrue(all(set(one) == {"value", "label"} for one in first + typed), "a match is a value and a label")
        self.assertNotIn("example.test", repr(updates), "a row's other fields never reach the browser")
        self.assertEqual(region, [{"value": "north", "label": "Nördlich"}],
                         "without `search`, lex-app matches the labels: 'nord' finds 'Nördlich'")
        self.assertEqual(list(invalid), ["OrdersState.broken|customer"], "rows without a label: at the field")
        self.assertTrue(invalid["OrdersState.broken|customer"].startswith("Something went wrong. Reference: "),
                        "the user gets a reference, never the exception's text")
        self.assertIn("OrdersState.unlabelled: row 0 has no label. The rows of a choice give `id`, the value the "
                      "handler receives, and `label`, the words the user reads: "
                      "`{\"id\": order.id, \"label\": order.name}`.", "\n".join(logs.output),
                      "the log says what to write")
        self.assertFalse(any("nowhere" in repr(update) for update in updates), "an unknown field is ignored")

    # -- 1.901 ---------------------------------------------------------
    def test_1_901_the_server_accepts_only_a_value_it_offered_to_the_tab(self) -> None:
        """
        Scenario 1.901: the form is sent.
        Given: the place-order form with a required searched customer, opened (the first 50
               offered).
        When:  it is sent with an offered customer, a customer that exists but was not
               offered, an id never offered, and nothing; then after another search with a
               customer from the first page.
        Then:  an offered value reaches the body as the row's id gave it (a number stays a
               number); a value this tab was not offered -- also a real customer -- is refused
               at the field and the body does not run; nothing chosen says to choose; what an
               earlier search offered is still accepted after the user typed on.
        """

        async def steps(harness):
            await _search(harness, OrdersState.place, "")
            seen = []
            for sent in ("7", "100", "9999", ""):
                await (await harness.send(OrdersState, "place", values={"customer": sent}))
                fields = await harness.fields(OrdersState)
                seen.append((fields["ui_invalid"], fields["runs"], fields["got"]))
            await _search(harness, OrdersState.place, "customer 10")
            await (await harness.send(OrdersState, "place", values={"customer": "100"}))
            later = (await harness.fields(OrdersState))["got"]
            await (await harness.send(OrdersState, "place", values={"customer": "12"}))
            earlier = (await harness.fields(OrdersState))["got"]
            await _search(harness, OrdersState.route, "")
            await (await harness.send(OrdersState, "route", values={"region": "south"}))
            region = (await harness.fields(OrdersState))["got"]
            return seen, later, earlier, region

        seen, later, earlier, region = asyncio.run(_session(steps))
        offered, real, forged, empty = seen
        self.assertEqual(offered, ({}, 1, {"customer": 7, "type": "int"}), "the row's id, a number, reaches the body")
        refused = {"OrdersState.place|customer": "Choose one of the options."}
        self.assertEqual(real[:2], (refused, 1), "a real customer this tab was not offered is refused at the field")
        self.assertEqual(forged[:2], (refused, 1), "and so is an id never offered")
        self.assertEqual(empty[:2], ({"OrdersState.place|customer": "Choose one."}, 1), "required: choose one")
        self.assertEqual(later, {"customer": 100, "type": "int"}, "offered by a later search, then accepted")
        self.assertEqual(earlier, {"customer": 12, "type": "int"}, "what an earlier search offered stays accepted")
        self.assertEqual(region, {"region": "south", "type": "str"}, "a text id stays a text")
        self.assertEqual(forms.check(forms.of(OrdersState.place.fn.__ui_fn__), {"customer": "7"})[1],
                         {"customer": "Choose one of the options."}, "nothing offered: nothing accepted")

    # -- 1.902 ---------------------------------------------------------
    def test_1_902_the_field_is_rfds_combobox_searching_on_the_server(self) -> None:
        """
        Scenario 1.902: the field on a page, in a wizard's step and in a button's ask=.
        Given: the place-order form, the wizard and the row button that asks for a customer.
        When:  they are built, as the browser gets them; and the dialog is answered.
        Then:  the field is RFDS's combobox in its server-search mode: the list is the matches
               the server keeps for this field, not filtered in the browser; typing and opening
               the field ask the server; the value sent is the chosen id, under the field's key,
               and the box shows the chosen label; the field has its label, the required mark
               and the server's message; the page compiles with Reflex's page compiler. The
               step and the dialog draw it the same way, and the dialog hands the row's key
               and the chosen id to the handler.
        """
        page = place_order().build(Build(page="place_order", route="/orders/place"))
        text = _text(page)
        field = _searched(OrdersState.place)
        for needle, why in (('"data-rfds-component":"combobox"', "RFDS's combobox"),
                            ("shouldFilter:false", "the list is not filtered in the browser: the server searched"),
                            (f'ui_matches_rx_state_?.["{field}"]', "the matches the server keeps for this field"),
                            (f'ui_search", ({{ ["field"] : "{field}", ["text"] : _ev_0 }})', "typing asks the server"),
                            (f'ui_search", ({{ ["field"] : "{field}", ["text"] : "" }})', "so does opening the field"),
                            ('name:"customer"', "the chosen id is sent under the field's key"),
                            ('ui_invalid_rx_state_?.["OrdersState.place|customer"]', "the server's message at the field"),
                            ('rfdsI18n.t("Customer", "Customer"', "the label"),
                            ("rfds-field-required", "the required mark"),
                            ("?.label ??", "the box shows the chosen match's label")):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, why)
        _, module = compile_page("/orders/place", place_order().build(Build(page="place_order", route="/orders/place")))
        self.assertIn('import {Command} from "cmdk"', module, "compiled: the list is cmdk's, via RFDS")

        wizard = _text(plan_order().build(Build(page="plan_order", route="/plan_order")))
        self.assertIn(f'ui_matches_rx_state_?.["{_searched(OrdersState.plan)}"]', wizard, "a wizard's step draws it")
        dialog = _text(_assigning())
        self.assertIn(f'ui_matches_rx_state_?.["{_searched(OrdersState.assign)}"]', dialog, "a button's ask= draws it")

        async def steps(harness):
            await (await harness.send(State, "ui_ask", dialog="orders-ask-1", args=[1]))
            await _search(harness, OrdersState.assign, "customer 11")
            await (await harness.send(OrdersState, "assign", key=1, values={"customer": "113"}))
            return await harness.fields(OrdersState)

        self.assertEqual(asyncio.run(_session(steps))["got"], {"key": 1, "customer": 113},
                         "the dialog hands the row's key, then the chosen id")

    # -- 1.903 ---------------------------------------------------------
    def test_1_903_a_searched_choice_declared_wrongly(self) -> None:
        """
        Scenario 1.903: the build-time errors.
        Given: a searched source for several choices; a searched choice that starts with a
               value; an action given as the options; the same field of one handler searching
               two different rows handlers.
        When:  each is built.
        Then:  each error names the page and the slot and says what to write.
        """
        ctx = Build(page="orders", route="/orders")
        cases = {
            "several": ([ui.field("customers", kind="choices", options=OrdersState.customers)],
                        "Page `orders`, slot `fields`: field `customers` chooses several from `OrdersState.customers`, "
                        "a search.\nA search offers one choice: leave `kind=` out. Several are chosen from "
                        "`options=` a list or a field of the state."),
            "start": ([ui.field("customer", options=OrdersState.customers, value=7)],
                      "Page `orders`, slot `fields`: field `customer` is searched on the server and starts as `7`.\n"
                      "A searched choice starts empty -- the server accepts only what it offered: leave `value=` out."),
            "action": ([ui.field("customer", options=OrdersState.place)],
                       "Page `orders`, slot `fields`: field `customer` got `OrdersState.place`, an `@ui.action`, "
                       "as its options.\nOptions searched on the server come from a `@ui.rows` handler: "
                       "`options=OrdersState.customers`."),
        }
        for name, (fields, message) in cases.items():
            with self.subTest(case=name), self.assertRaises(PageError) as wrong:
                ui.form(title="Orders", submit=OrdersState.wrong, fields=fields).build(ctx)
            self.assertEqual(str(wrong.exception), message, name)
        with self.assertRaises(PageError) as twice:
            ui.form(title="Orders", submit=OrdersState.place,
                    fields=[ui.field("customer", options=OrdersState.regions, required=True)]).build(ctx)
        self.assertEqual(str(twice.exception),
                         "Page `orders`, slot `fields`: field `customer` of `OrdersState.place` searches "
                         "`OrdersState.regions`; elsewhere it searches `OrdersState.customers`.\n"
                         "A handler receives one set of fields: give this form or button a handler of its own.")

    # -- 1.912 ---------------------------------------------------------
    def test_1_912_two_searched_fields_on_one_form_never_cancel_each_other(self) -> None:
        """
        Scenario 1.912: two searched choices on one form, through Reflex's event processor.
        Given: a form with a searched customer and a searched warehouse; the warehouses search
               for "slow" is held (a slow system).
        When:  the user types "slow" into the warehouse, then, while it runs, types into the
               customer and presses another button; then types "fast" into the warehouse; then
               the slow search ends.
        Then:  the customer's matches arrive while the warehouse search still runs, and the
               button's action runs meanwhile (the search does not hold the tab); the customer's
               search does not cancel the warehouse's; the warehouse shows the matches of what
               was typed last ("fast"), never the slow, older search's that ended after it.
        """
        import threading

        customer, warehouse = (next(one.search for one in forms.of(DeliveryState.deliver.fn.__ui_fn__).fields
                                    if one.key == key) for key in ("customer", "warehouse"))
        SLOW["gate"] = threading.Event()
        self.addCleanup(SLOW.update, {"gate": None})

        async def matches(harness, field):
            return [one["label"] for one in dict((await harness._state(State)).ui_matches).get(field, [])]

        async def steps(harness):
            slow = await harness.send(State, "ui_search", field=warehouse, text="slow")
            await asyncio.sleep(0.05)
            await (await harness.send(State, "ui_search", field=customer, text="Customer 00")).wait_all()
            await asyncio.wait_for((await harness.send(DeliveryState, "click")).wait_all(), 2)
            during = (await matches(harness, customer), await matches(harness, warehouse),
                      (await harness.fields(DeliveryState))["clicks"])
            await (await harness.send(State, "ui_search", field=warehouse, text="fast")).wait_all()
            fast = await matches(harness, warehouse)
            SLOW["gate"].set()
            await asyncio.wait_for(slow.wait_all(), 5)
            await asyncio.sleep(0.05)
            return during, fast, await matches(harness, warehouse), await matches(harness, customer)

        during, fast, after, customers = asyncio.run(_session(steps))
        self.assertEqual(len(during[0]), 9, "the customer's matches came while the warehouse was searched")
        self.assertEqual(during[1], [], "the slow warehouse search was still running")
        self.assertEqual(during[2], 1, "another button worked meanwhile: the search does not hold the tab")
        self.assertEqual(fast, ["Warehouse fast 1", "Warehouse fast 2"])
        self.assertEqual(after, ["Warehouse fast 1", "Warehouse fast 2"],
                         "the older, slow search ending later does not replace what was typed last")
        self.assertEqual(len(customers), 9, "and nothing cancelled the customer's matches")
