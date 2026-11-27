"""ui.testing: drive a searched field, seed a private value as gone, and test against lex-app's real API.

Intent: a project tests its handlers with `ui.testing` in one line. Three things needed lex.ui's
internals: what a searched choice offers when the user types (`ui.testing.search`, and
`Harness.search` inside a longer test); a `ui.private` field seeded as gone (`fields={"_x": GONE}`),
which went through Reflex's typed setter and logged a type error; and a handler's `self.api` against
lex-app's own API without a server (`ui.testing.lex_api(client)`: every call answered in-process by
lex-app's views through Django's test client, so permissions, validation and hooks are the real
ones). A regression is a project test that has to reach into lex.ui's private modules, a noisy log,
or a stand-in API that tests something lex-app does not do.

Cluster 1fb -- scenarios 1.1038-1.1040, 1.1113. Types: U (1.1038, 1.1039), E (1.1040).
Covers: lex/lex_app/reflex/ui/testing.py (search, Harness.search, Harness.set_fields, lex_api,
LexViews).
Run: python -m lex pytest lex/test_project/tests/init/test_1fb_ui_testing_search_gone_api.py -v
"""

from __future__ import annotations

import logging

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import private
from lex.lex_app.reflex.ui.pages.parts import Build
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.init.models import UI_FK_MODELS, UiCustomer

pytestmark = pytest.mark.init

GONE_TEXT = "This is no longer available: it is kept only for a while. Start again."


class ShipState(ui.State):
    """Shipments to a customer chosen among many, a private result, and customers read from lex-app."""

    note: str = ""
    names: list[str] = []
    _found: dict = ui.private()

    @ui.rows
    def customers(self, search):
        return [{"id": n, "label": f"Customer {n:03d}"} for n in range(1, 60) if search in f"Customer {n:03d}"]

    @ui.action
    def ship(self, values):
        return None

    @ui.action
    def read_found(self):
        self.note = str(self._found.get("name", "-"))

    @ui.action
    def list_customers(self):
        self.names = [row["name"] for row in self.api.list(UiCustomer, search="Ltd", ordering="name")]


def _ship_form():
    return ui.form(title="Ship", submit=ShipState.ship, fields=[ui.field("customer", options=ShipState.customers)])


class TestCluster01fb_TestingHelpers(SimpleTestCase):
    """1.1038-1.1039."""

    def setUp(self) -> None:
        private.clear()
        self.addCleanup(private.clear)

    # -- 1.1038 --------------------------------------------------------
    def test_1_1038_a_test_drives_a_searched_field(self) -> None:
        """
        Scenario 1.1038: ui.testing.search(ShipState.ship, "customer", "00").
        Given: a form whose customer field is searched by a @ui.rows handler; its page built.
        When:  a test types "00" into it, then names a field that is not searched.
        Then:  it gets the matches the browser would be offered (value and label only); the wrong
               field is refused with what a searched field is.
        """
        _ship_form().build(Build(page="ship", route="/ship"))
        offered = ui.testing.search(ShipState.ship, "customer", "00")
        self.assertEqual([one["label"] for one in offered], [f"Customer 00{n}" for n in range(1, 10)])
        self.assertEqual(set(offered[0]), {"value", "label"}, "as the browser gets them")
        with self.assertRaises(ValueError) as wrong:
            ui.testing.search(ShipState.ship, "nothing", "a")
        self.assertIn("has no searched field 'nothing'", str(wrong.exception))

    # -- 1.1039 --------------------------------------------------------
    def test_1_1039_a_private_value_is_seeded_as_gone_without_a_logged_error(self) -> None:
        """
        Scenario 1.1039: ui.testing.run(..., fields={"_found": ui.testing.GONE}), then a value.
        Given: a state with a private field.
        When:  a test seeds it as gone and runs a handler that reads it; then seeds a value.
        Then:  the handler is refused in the plain sentence; nothing is logged at warning or above
               while seeding; a seeded value is read as written.
        """
        with self.assertNoLogs(level=logging.WARNING):
            gone = ui.testing.run(ShipState, "read_found", fields={"_found": ui.testing.GONE})
        self.assertEqual(gone.refused, GONE_TEXT)
        seeded = ui.testing.run(ShipState, "read_found", fields={"_found": {"name": "Ada"}})
        self.assertEqual(seeded.fields["note"], "Ada")


class TestCluster01fb_LexApiInProcess(E2ETestCase):
    """1.1040: self.api answered by lex-app's views."""

    e2e_models = UI_FK_MODELS

    # -- 1.1040 --------------------------------------------------------
    def test_1_1040_self_api_is_answered_by_lex_apps_views_in_process(self) -> None:
        """
        Scenario 1.1040: ui.testing.run(..., api=ui.testing.lex_api(self.client)).
        Given: three customers in the database; the test's signed-in client.
        When:  a handler lists the customers whose name holds "Ltd" through self.api.
        Then:  it gets the real rows, searched by lex-app's list view (the model's own name field),
               with no server; the requests went to lex-app's list route.
        """
        for name in ("Acme Ltd", "Beta plc", "Gamma Ltd"):
            UiCustomer.objects.create(name=name)
        api = ui.testing.lex_api(self.client)
        ran = ui.testing.run(ShipState, "list_customers", user="e2e_user", api=api)   # the client's user
        self.assertEqual(ran.error, "")
        self.assertEqual(ran.fields["names"], ["Acme Ltd", "Gamma Ltd"])
        self.assertEqual(api.calls, [("GET", "/api/model_entries/uicustomer/list")])


    # -- 1.1113 --------------------------------------------------------
    def test_1_1113_the_handlers_user_is_the_user_the_api_acts_as(self) -> None:
        """
        Scenario 1.1113: ui.testing.lex_api(client) with a handler run as another user (review 2).
        Given: the test's client, signed in as e2e_user; a handler run as "tester".
        When:  the handler calls self.api.
        Then:  the call is refused before lex-app's views answer, saying the API acts as the client's user
               and what to write (user="e2e_user"); run as e2e_user it is answered.
        """
        UiCustomer.objects.create(name="Acme Ltd")
        api = ui.testing.lex_api(self.client)
        ran = ui.testing.run(ShipState, "list_customers", user="tester", api=api)
        self.assertEqual(api.calls, [("GET", "/api/model_entries/uicustomer/list")])
        self.assertEqual(ran.fields["names"], [], "nothing was answered for the wrong user")
        self.assertTrue(ran.error, "the handler failed")
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as logged:
            ui.testing.run(ShipState, "list_customers", user="tester", api=ui.testing.lex_api(self.client))
        said = "\n".join(logged.output)
        self.assertIn("acts as the client's user 'e2e_user'", said)
        self.assertIn("user='e2e_user'", said, "it says what to write")
        self.assertEqual(ui.testing.run(ShipState, "list_customers", user="e2e_user", api=api).fields["names"],
                         ["Acme Ltd"])
