"""lex.ui: plain synchronous handlers for Reflex dashboards.

    from lex import ui

    class Tickets(ui.State):
        rows: list[dict] = []

        @ui.load
        def open(self):
            self.rows = self.api.list(Ticket)

        @ui.action
        def approve(self, ticket_id: int):
            self.api.update(Ticket, ticket_id, {"status": "approved"})

        @ui.job
        def export(self):
            for done, part in enumerate(parts(self.user), start=1):
                build(part)
                yield ui.progress(done / 10, "Building")

    @ui.page("/funds/[key]", requires=ui.read(Fund))
    def fund():
        return ui.record(
            title=FundState.name,
            load=FundState.open,
            facts=[ui.fact("Vintage", FundState.vintage)],
            sections=[ui.section("Notes", ui.each(FundState.notes, lambda note: ui.text(note)),
                                 load=FundState.load_notes)],
            actions=[ui.button("Export", FundState.export)],
        )

==========================  =====================================================
``ui.State``                base class of a page state: ``self.user`` (``.permissions``), ``self.api``,
                            ``self.tab_id`` (the browser tab, for a project's per-tab store),
                            ``self.params``, ``self.can()``, ``self.state()``; in a job
                            ``self.progress(fraction, message)``, also from a library's thread
                            (the one part of ``self`` safe there)
``@ui.load``                fills the page when it opens
``@ui.action``              a short handler, under about a second
``@ui.job``                 long work with progress; the user keeps working. ``limit=2``: at
                            most two runs at once in each server process, for all users (one
                            user may hold both); a further press is refused
``@ui.rows``                a table's rows, as a list of dicts; read-only, read as the user
``@ui.custom``              a raw Reflex handler, marked for review
``ui.goto/toast/download``  what a handler returns; ``ui.toast(link=ui.link(...))`` offers to
                            open something; ``ui.refresh(State.rows)`` makes the tables of
                            another state read again
``ui.progress``             what a job yields
``ui.private``              a state field kept in the server's memory only, for its user, for a
                            while: ``_report: dict = ui.private()``; a ``@ui.rows`` handler reads
                            it, a computed var may not
``ui.Refuse``               an expected refusal, shown in its own words (``title=`` heads it): in
                            a form above its submit button, elsewhere as a toast
``ui.ApiError``             lex-app's API refused a call (``.status``, ``.denied``,
                            ``.detail``); uncaught, it is shown to the user
``self.api``                lex-app's API as the user: ``list/get/create/update/delete``, and for
                            a calculation ``calculate(Model, pk)`` (as the Calculate button),
                            ``log(Model, pk)`` (its newest run's log); ``file(Model, pk, field)``
                            a record's file, downloaded as the user
``requires=``               ``ui.read/create/edit/delete(Model)``, ``ui.permission()``,
                            ``ui.signed_in``, a project's ``@ui.rule`` -- needed to query the
                            database directly; on a page and on every handler
``@ui.rule``                a project's own access rule: ``def rule(user, params)`` returns True,
                            False or the refusal's sentence; asked on the server once per event
``ui.running/failed/        a handler's status, for views; ``ui.cancel`` is the
progress_of/cancel``        event of a Cancel button
``ui.loaded``               whether a load has finished since the page opened
``ui.testing``              run a handler in a unit test
``@ui.page``                where a page lives: its route, its area of the navigation (``nav=``),
                            who may see it. lex-app's frame heads it with its breadcrumbs: the
                            area, the listed page that opens it, the page. The navigation lists
                            only what the user may open, decided on the server when a page opens
``ui.frame``                lex-app's frame for this project, set once in the dashboard module:
                            ``nav=[...]`` the areas' order, and sections of links:
                            ``ui.section("Help", ui.link(...))``, or ``ui.section("Favourites",
                            favourites)`` filled by a function of the user; ``nav_footer=`` a link
                            or text at the navigation's foot. A function of the user,
                            ``def waiting(user)``, stands for a value the frame shows (a badge's
                            count, a section's links): lex-app calls it on the server when a page
                            opens. ``search=`` a page taking one query parameter: RFDS's search box
                            in the header opens it with what was typed; ``actions=[...]`` links,
                            buttons, statuses, texts, custom blocks in the header band;
                            ``user_menu=[ui.link(...)]`` entries above "Sign out"; ``banner=``
                            one ``ui.message`` above every page while its sentence is not empty; ``denied=`` the sentence a refused
                            user reads, ``request_access=`` their way out (a page, https:// or
                            mailto:); ``opened=`` a function lex-app calls, not awaited, when a
                            page has opened: ``opened(user, page)``; ``sign_out=`` where the
                            user menu signs out (a page or https://; lex-app's /logout by default)
``@ui.dashboard``           a page that is not a route (the project's ``main()``)
``ui.record/list/form``     the page kinds: named slots, placed by lex-app (``ui.list`` is
                            left out of ``import *``, which would hide Python's ``list``)
``ui.form(keep=True)``      the result is still there when the user comes back: what its
                            state and its tables' states hold stays across visits
``ui.calculation``          the page of a lex-app calculation model: a form for a run's inputs
                            (created and started as the user), the run while it goes, its
                            result, the past runs; ``?run=<key>`` is one run. A field of the run
                            is written as the model's: ``RegionReport.orders``. The page's
                            ``requires=`` guards its handlers too
``ui.field/step``           a form's inputs, and a wizard's steps; the handler gets ``values``.
                            ``options=`` a ``@ui.rows`` handler searches on the server as the user
                            types. ``ui.button(confirm=, ask=)`` asks before its handler runs
``ui.section``              a titled part of a page; ``load=`` makes it load on its own;
                            ``collapsed=True`` closes it until the user opens it; ``tabs=True``
                            on ``ui.record`` or a ``ui.form``'s result shows one section at a time
``ui.text/fact/status/      what fills the slots; none takes a styling argument. In lex-app's
message/column/             frame a ``ui.link`` is an entry: ``icon=``, ``badge=`` (a count),
button/link``               ``requires=`` (drawn only for users who meet it, decided on the
                            server); ``@ui.page(nav=, badge=)`` too
``ui.each/switch``          repetition and choice; ``visible=`` on every component
                            decides what is shown, never what is allowed
``ui.table``                rows from a model (``where=``) or a ``@ui.rows`` handler; ``search=``,
                            ``filters=``, ``export=``, ``totals=`` (``totals_of_all=True``: of
                            every row, whatever the search leaves), ``row_actions=``,
                            ``choose_columns=``, ``page_size=`` (up to 200; the pager shows
                            only when the rows do not fit one page)
``ui.address``              a page's address, for a row that leads to a page of its own
``ui.reflex_page``          a page written in raw Reflex, as somewhere a link may lead;
                            ``nav=``, ``title=`` list it in an area of the navigation, ``requires=``
                            (a permission or a rule) hides that entry
``ui.custom_block``         a raw component, marked for review with its reason
``ui.embed``                the other way round: one lex.ui part inside a raw Reflex page,
                            for moving a project a table at a time
``ui.PageError``            a page declared wrongly: says the page, the slot, what to write
==========================  =====================================================

Import this module only in the Reflex process: it defines Reflex states.
"""

from lex.lex_app.reflex.data import LexApiError as ApiError
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.guard import create, delete, edit, permission, read, rule, signed_in
from lex.lex_app.reflex.ui.handlers import action, custom, job, load
from lex.lex_app.reflex.ui.pages.calculation import calculation
from lex.lex_app.reflex.ui.pages.declare import dashboard, embed, page
from lex.lex_app.reflex.ui.pages.forms import field, form, step
from lex.lex_app.reflex.ui.pages.frame import frame
from lex.lex_app.reflex.ui.pages.links import address, reflex_page
from lex.lex_app.reflex.ui.pages.kinds import list_ as list  # noqa: A001 - ui.list is the page kind
from lex.lex_app.reflex.ui.pages.kinds import record, section
from lex.lex_app.reflex.ui.pages.parts import PageError
from lex.lex_app.reflex.ui.pages.tables import column, table
from lex.lex_app.reflex.ui.pages.vocab import button, custom_block, each, fact, link, message, status, switch, text
from lex.lex_app.reflex.ui.private import private
from lex.lex_app.reflex.ui.results import Refuse, download, goto, progress, refresh, toast
from lex.lex_app.reflex.ui.rows import rows
from lex.lex_app.reflex.ui.runs import cancel, failed, loaded, progress_of, running
from lex.lex_app.reflex.ui.state import State

__all__ = [
    "ApiError", "PageError", "Refuse", "State", "action", "address", "button", "calculation", "cancel", "column",
    "create", "custom", "custom_block", "dashboard", "delete", "download", "each", "edit", "embed", "fact",
    "failed", "field", "form", "frame", "goto", "job", "link", "load", "loaded", "message", "page", "permission", "private", "progress",
    "progress_of", "read", "record", "reflex_page", "refresh", "rows", "rule", "running", "section", "signed_in", "status",
    "step", "switch", "table", "testing", "text", "toast",
]
