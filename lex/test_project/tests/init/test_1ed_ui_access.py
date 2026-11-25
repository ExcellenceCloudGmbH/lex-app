"""lex.ui's frame: who may see a page -- a project's no-access sentence and way out, and its own rules.

Intent: a user who opens a page they may not see is told so in the project's own words and offered
the project's way out ("Ask your administrator for access", a request page, signing in with another
account), on every refused page -- not lex-app's generic sentence with no way forward. And a project
guards pages and handlers with rules of its own ("only the region's team"), checked on the server
like lex-app's permissions, refused with the rule's own sentence. A regression is a refused page that
strands the user, a sentence that is lex-app's where the project said its own, a rule that the browser
decides, a rule asked more than once per event, or a rule that fails open.

Cluster 1ed -- scenarios 1.968-1.973, 1.1090, 1.1104. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (denied=, request_access=), lex/lex_app/reflex/shell.py
(denied_notice, lex_page), lex/lex_app/reflex/ui/state.py (error_page, ui_open),
lex/lex_app/reflex/ui/guard.py (rule), lex/lex_app/reflex/ui/handlers.py (_session),
lex/lex_app/reflex/ui/pages/declare.py (page requires=).
Run: python -m lex pytest lex/test_project/tests/init/test_1ed_ui_access.py -v
"""

from __future__ import annotations

import pytest
import reflex as rx
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.state import error_page
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init


def _about(title):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


class TestCluster01ed_NoAccess(FrameCase):
    """1.968-1.969: the project's sentence and way out, on a refused page and on the page a refused load goes to."""

    # -- 1.968 ---------------------------------------------------------
    def test_1_968_a_refused_page_says_the_projects_sentence_and_offers_its_way_out(self) -> None:
        """
        Scenario 1.968: the no-access notice.
        Given: a page that requires a permission, with lex-app's defaults, then with
               ui.frame(denied=, request_access=, sign_out=).
        When:  the page is built in lex-app's frame and compiled.
        Then:  the page falls back to RFDS's access-denied notice: by default RFDS's sentence, no
               request link, "Switch account" to lex-app's sign-out; configured, the project's sentence,
               its request-access address and "Switch account" to its sign-out; a request page that
               needs values, and a route written as text, are refused with what to write.
        """
        @ui.page("/reports", nav="Reports", requires=ui.permission("lex_app.Report", "read"))
        def reports():
            return _about("Reports")

        declare.finalize()
        _, module = compile_page("/reports", self.page_named("/reports")())
        for expected in ("rfds-access-denied", "does not have permission", '"/logout"', "lex_app.Report:read"):
            self.assertIn(expected, module, f"by default, RFDS's notice: {expected}")
        self.assertNotIn("rfds-access-denied-request", module, "no request link the project did not give")

        @ui.page("/access")
        def access_request():
            return _about("Request access")

        ui.frame(denied="Ask your administrator for access.", request_access=access_request,
                 sign_out="https://portal.example.com/sign-out")
        _, module = compile_page("/reports", self.page_named("/reports")())
        for expected in ("Ask your administrator for access.", "rfds-access-denied-request", '"/access"',
                         "https://portal.example.com/sign-out"):
            self.assertIn(expected, module, f"the project's notice: {expected}")
        ui.frame(request_access="mailto:access@example.com")
        self.assertIn("mailto:access@example.com", str(shell.denied_notice()), "a mail address is a way out too")

        @ui.page("/reports/[region]")
        def region_report():
            return _about("Region")

        for wrong, says in ((region_report, "needs region"), ("/access", "not written as text")):
            with self.assertRaises(ValueError) as caught:
                ui.frame(request_access=wrong)
            self.assertIn(says, str(caught.exception))
        with self.assertRaises(TypeError) as caught:
            ui.frame(denied=["Ask your administrator."])
        self.assertIn("denied=", str(caught.exception))

    # -- 1.969 ---------------------------------------------------------
    def test_1_969_the_page_a_refused_load_goes_to_offers_the_same_way_out(self) -> None:
        """
        Scenario 1.969: a load was refused.
        Given: ui.frame(request_access=) and the page a failed load goes to (/ui-error).
        When:  it is built and compiled.
        Then:  a refusal there is the same notice -- the refusal's own sentence, the project's request
               link, "Switch account" -- while any other failure keeps its "Try again".
        """
        ui.frame(request_access="mailto:access@example.com")
        _, module = compile_page("/ui-error", error_page())
        for expected in ("rfds-access-denied", "mailto:access@example.com", "Try again", '"denied"'):
            self.assertIn(expected, module, f"the error page: {expected}")


ASKED: list = []


@ui.rule
def region_team(user, params):
    """Only the team of the page's region; north belongs to anna."""
    import threading

    ASKED.append((user.id, params.get("region"), threading.current_thread().name))
    if params.get("region") == "broken":
        raise RuntimeError("the team directory is away")
    if params.get("region") == "odd":
        return 7
    if user.id == "anna" and params.get("region") == "north":
        return True
    if params.get("region") == "south":
        return False
    return "Only the region's team sees its report."


class RegionState(ui.State):
    """A region's report: a load and two actions guarded by the project's rule."""

    figures: list[str] = []
    done: str = ""

    @ui.load
    def open(self):
        self.figures = ["12"]

    @ui.action(requires=region_team)
    def close(self):
        self.done = "closed"

    @ui.action(requires=[ui.signed_in, region_team, region_team])
    def reopen(self):
        self.done = "reopened"


@ui.rule
def report_reader(user, params):
    """Granted to a reader who holds the report read permission, read from user.permissions."""
    held = {(p.get("rsname"), scope) for p in user.permissions for scope in p.get("scopes", [])}
    return True if ("init.Report", "read") in held else "Only report readers."


class PermittedState(ui.State):
    """An action guarded by a rule that reads the user's permissions."""

    done: str = ""

    @ui.action(requires=report_reader)
    def close(self):
        self.done = "closed"


def _report():
    return ui.record(title="Region report", load=RegionState.open,
                     sections=[ui.section("Figures", ui.text("The figures."))])


class TestCluster01ed_Rules(FrameCase):
    """1.970-1.973: a project's rule on handlers, once per event, on pages, and its mistakes."""

    def setUp(self) -> None:
        super().setUp()
        ASKED.clear()

    def run_events(self, *, user: str, region: str, steps):
        import asyncio

        from lex.lex_app.reflex.ui import testing

        async def scenario():
            async with testing.Harness(user=user, params={"region": region}) as harness:
                await harness.open_tab(f"/regions/{region}")
                await steps(harness)
                return await harness.fields(RegionState), list(harness.outcomes)

        return asyncio.run(scenario())

    # -- 1.970 ---------------------------------------------------------
    def test_1_970_a_handler_guarded_by_a_rule_runs_only_when_it_grants(self) -> None:
        """
        Scenario 1.970: requires= a project's rule, on a handler.
        Given: an action that requires the rule `region_team`, which grants anna the north, refuses
               the south with lex-app's sentence and everything else with its own.
        When:  anna presses it on the north, on the south and on the east, through Reflex's own event
               processor.
        Then:  it runs only on the north; on the south the user reads lex-app's refusal, on the east the
               rule's sentence; the rule was given the user and the page's values; the rule ran off the
               event loop, on a worker thread (it may query the database).
        """
        async def press(harness):
            await (await harness.send(RegionState, "close"))

        fields, outcomes = self.run_events(user="anna", region="north", steps=press)
        self.assertEqual((fields["done"], outcomes), ("closed", []), "granted: the handler ran")
        self.assertEqual(ASKED[0][:2], ("anna", "north"), "the rule got the user and the page's values")
        self.assertTrue(ASKED[0][2].startswith("lex-ui-"), f"on a worker thread, not the loop: {ASKED[0][2]}")
        fields, outcomes = self.run_events(user="anna", region="south", steps=press)
        self.assertEqual(fields["done"], "", "refused: the body did not run")
        self.assertEqual([(o.kind, o.message) for o in outcomes], [("denied", "You are not permitted to do this.")])
        fields, outcomes = self.run_events(user="anna", region="east", steps=press)
        self.assertEqual([(o.kind, o.message) for o in outcomes],
                         [("denied", "Only the region's team sees its report.")], "the rule's own sentence")

    # -- 1.971 ---------------------------------------------------------
    def test_1_971_a_rule_is_asked_once_per_event_and_fails_closed(self) -> None:
        """
        Scenario 1.971: once per event; never open by accident.
        Given: an action that names the rule twice beside ui.signed_in; a rule that raises; one that
               answers something that is neither yes, no nor a sentence.
        When:  the action is pressed twice, then on the broken regions.
        Then:  the rule is asked once per press, not once per mention and not once for all presses;
               a rule that raises or answers wrongly refuses -- the body does not run -- and the user
               gets the standard error with a reference, as for any unexpected failure.
        """
        async def twice(harness):
            await (await harness.send(RegionState, "reopen"))
            await (await harness.send(RegionState, "reopen"))

        fields, outcomes = self.run_events(user="anna", region="north", steps=twice)
        self.assertEqual((fields["done"], outcomes), ("reopened", []))
        self.assertEqual(len(ASKED), 2, "asked once per event, however often it is named")
        for region in ("broken", "odd"):
            fields, outcomes = self.run_events(user="anna", region=region,
                                               steps=lambda h: _send(h, "close"))
            self.assertEqual(fields["done"], "", f"{region}: the body did not run")
            self.assertEqual([o.kind for o in outcomes], ["error"], f"{region}: an unexpected failure")
            self.assertIn("Reference", outcomes[0].message)

    # -- 1.972 ---------------------------------------------------------
    def test_1_972_a_page_guarded_by_a_rule_is_decided_by_the_opener(self) -> None:
        """
        Scenario 1.972: requires= a project's rule, on a page.
        Given: a page /regions/[region] that requires the rule, with a load; ui.frame(denied=).
        When:  lex-app registers it, the page is compiled, and anna opens it on the north and the east.
        Then:  the compiled page shows its content only once the opener granted it, else RFDS's notice
               with the rule's sentence (or, for a plain no, the project's); on the north the page's
               load starts; on the east none starts and the refusal is kept for the page; a page's
               requires= takes a list -- a permission and a rule -- whose permission still hides it.
        """
        ui.frame(denied="Ask your administrator for access.")

        @ui.page("/regions/[region]", requires=region_team)
        def region_report():
            return _report()

        @ui.page("/regions", nav="Reports", requires=[ui.permission("lex_app.Report", "read"), region_team])
        def regions():
            return _about("Regions")

        declare.finalize()
        _, module = compile_page("/regions/[region]", self.page_named("/regions/[region]")())
        for expected in ("ui_access", "rfds-access-denied", "Ask your administrator for access."):
            self.assertIn(expected, module, f"compiled: {expected}")
        self.assertEqual(shell.nav_entries()[0].permission, "lex_app.Report:read", "the permission hides the entry")
        self.assertIn("lex_app.Report:read", compile_page("/regions", self.page_named("/regions")())[1])

        async def open_page(harness):
            from lex.lex_app.reflex.ui.state import State

            await (await harness.send(State, "ui_open", page="/regions/[region]"))
            import asyncio

            await asyncio.sleep(0.05)

        import asyncio

        from lex.lex_app.reflex.ui import testing
        from lex.lex_app.reflex.ui.state import State

        async def visit(region):
            async with testing.Harness(user="anna", params={"region": region}) as harness:
                await harness.open_tab(f"/regions/{region}")
                await open_page(harness)
                return await harness.fields(RegionState), (await harness._state(State)).ui_access

        fields, access = asyncio.run(visit("north"))
        self.assertEqual(fields["figures"], ["12"], "granted: the page's load ran")
        self.assertEqual(access["/regions/[region]"]["granted"], "yes")
        fields, access = asyncio.run(visit("east"))
        self.assertEqual(fields["figures"], [], "refused: no load started")
        self.assertEqual(dict(access["/regions/[region]"]),
                         {"granted": "no", "message": "Only the region's team sees its report."})

    # -- 1.973 ---------------------------------------------------------
    def test_1_973_a_rule_written_wrongly_is_refused_with_what_to_write(self) -> None:
        """
        Scenario 1.973: mistakes.
        Given: a rule taking the wrong arguments, a plain function given as requires=, a rule given as
               visible=, and a raw page given a plain function as requires=.
        When:  each is declared or built.
        Then:  each is refused with what to write; a rule stays a function the project can call itself.
               (A raw page's requires= takes a rule since the navigation is decided on the server: 1.1001.)
        """
        with self.assertRaises(TypeError) as caught:
            @ui.rule
            def wrong(user):
                return True
        self.assertIn("(user, params)", str(caught.exception))

        def plain(user, params):
            return True

        with self.assertRaises(TypeError) as caught:
            ui.action(requires=plain)(lambda self: None)
        self.assertIn("@ui.rule", str(caught.exception))
        with self.assertRaises(TypeError) as caught:
            ui.page("/x", requires=plain)
        self.assertIn("@ui.rule", str(caught.exception))
        from lex.lex_app.reflex.ui.pages.parts import Build, PageError

        with self.assertRaises(PageError) as caught:
            ui.text("Only for the team.", visible=region_team).build(Build(page="p", route="/p"))
        self.assertIn("self.params", str(caught.exception), "it says how to show something by a rule")
        with self.assertRaises(ValueError):
            ui.reflex_page("/legacy", nav="Tools", title="Legacy", requires=plain)
        from lex.lex_app.reflex.ui.context import Params, User

        self.assertIs(region_team(User(id="anna"), Params({"region": "north"})), True, "still callable")


    # -- 1.1090 --------------------------------------------------------
    def test_1_1090_a_rule_reads_the_users_permissions(self) -> None:
        """
        Scenario 1.1090: a rule that decides by the user's Keycloak permissions.
        Given: a rule granting a reader who holds `init.Report:read` among `user.permissions`.
        When:  a reader with that grant and one without press an action that requires the rule.
        Then:  the rule reads `user.permissions` on its worker thread (it is not a handler body, so
               lex-app gives the user its permissions before the rule runs): the reader with the
               grant is granted, the other refused with the rule's sentence - never a failure.
        """
        import asyncio

        from lex.lex_app.reflex.ui import testing

        async def scenario(grants):
            async with testing.Harness(user="anna", grants=grants, params={"region": "north"}) as harness:
                await harness.open_tab("/regions/north")
                await (await harness.send(PermittedState, "close"))
                return await harness.fields(PermittedState), list(harness.outcomes)

        fields, outcomes = asyncio.run(scenario(["init.Report:read"]))
        self.assertEqual((fields["done"], outcomes), ("closed", []), "granted by a permission the rule read")
        fields, outcomes = asyncio.run(scenario([]))
        self.assertEqual([(o.kind, o.message) for o in outcomes], [("denied", "Only report readers.")],
                         "refused by the rule's own sentence, not a failure")


async def _send(harness, name):
    await (await harness.send(RegionState, name))


class RegionRowsState(ui.State):
    """A region's customers, read by a rows handler that itself requires nothing."""

    @ui.rows
    def customers(self):
        READ.append("customers")
        return [{"id": 1, "name": "Acme"}]


READ: list = []


class FakeApi:
    """lex-app's API as a model table reads it: one page of groups."""

    async def list(self, resource, **kwargs):
        READ.append(resource)
        return {"results": [{"id": 1, "name": "North team"}], "count": 1}


class TestCluster01ed_TablesFollowThePage(FrameCase):
    """1.1104: a table on a rule-guarded page reads only for users the page grants."""

    def setUp(self) -> None:
        super().setUp()
        ASKED.clear()
        READ.clear()

    # -- 1.1104 --------------------------------------------------------
    def test_1_1104_a_refused_user_reads_no_rows_by_sending_a_tables_events(self) -> None:
        """
        Scenario 1.1104: the events of a page's tables sent by name.
        Given: a region page guarded by the project's rule, holding a model table and a table over a
               rows handler that requires nothing of its own; a calculation page guarded by the rule,
               with its past runs.
        When:  a user the rule refuses sends each table's `open` and `set_page` by name; then the
               region's team does.
        Then:  the refused user reads nothing -- lex-app's API and the rows handler are never called --
               and each table shows the rule's sentence; the team's user reads the rows.
        """
        import asyncio
        from unittest.mock import patch

        from django.contrib.auth.models import Group

        from lex.lex_app.reflex import data
        from lex.lex_app.reflex.ui import testing
        from lex.lex_app.reflex.ui.pages import rows_table
        from lex.test_project.tests.init.models import UiRegionReport

        groups = ui.table(Group, columns=["name"])
        customers = ui.table(RegionRowsState.customers, columns=["name"])

        @ui.page("/regions/[region]", requires=region_team)
        def region():
            return ui.record(title="Region", sections=[ui.section("Teams", groups), ui.section("Customers", customers)])

        @ui.page("/reports/regions", nav="Reports", requires=region_team)
        def reports():
            return ui.calculation(UiRegionReport, title="Region report",
                                  fields=[ui.field("region"), ui.field("year", kind="year")],
                                  history=ui.table(UiRegionReport, columns=["region", "year"]))

        declare.finalize()
        tables = [rows_table._CACHE_BY_TABLE[groups], rows_table._CACHE_BY_TABLE[customers],
                  rows_table._CACHE_BY_TABLE[declare._DECLARED["/reports/regions"].kind.built_history]]

        async def fake_api(state):
            return FakeApi()

        def read_as(user, region_name):
            async def scenario():
                async with testing.Harness(user=user, params={"region": region_name}) as harness:
                    await harness.open_tab(f"/regions/{region_name}")
                    shown = []
                    for state in tables:
                        opening = {"scope": {}} if issubclass(state, data.LexTableState) else {}
                        await (await harness.send(state, "open", **opening))
                        await (await harness.send(state, "set_page", value=1))
                        live = await harness._state(state)
                        shown.append((live.error, len(live.page_rows)))
                    return shown

            with patch.object(data, "lex_api", fake_api):
                return asyncio.run(scenario())

        refused = read_as("ben", "east")
        self.assertEqual(READ, [], "nothing was read for a user the page refuses")
        self.assertEqual(refused, [("Only the region's team sees its report.", 0)] * 3,
                         "each table shows the rule's sentence")
        granted = read_as("anna", "north")
        self.assertEqual([error for error, _ in granted], ["", "", ""], "the region's team reads")
        self.assertEqual(sorted(set(READ)), ["customers", "group", "uiregionreport"])
