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
                            ``self.params``, ``self.can()``, ``self.state()``
``@ui.load``                fills the page when it opens
``@ui.action``              a short handler, under about a second
``@ui.job``                 long work with progress; the user keeps working
``@ui.rows``                a table's rows, as a list of dicts; read-only, read as the user
``@ui.custom``              a raw Reflex handler, marked for review
``ui.goto/toast/download``  what a handler returns; ``ui.refresh(State.rows)`` makes the
                            tables of another state read again
``ui.progress``             what a job yields
``ui.Refuse``               an expected refusal, shown in its own words
``ui.ApiError``             lex-app's API refused a call (``.status``, ``.denied``,
                            ``.detail``); uncaught, it is shown to the user
``requires=``               ``ui.read/create/edit/delete(Model)``, ``ui.permission()``,
                            ``ui.signed_in`` -- needed to query the database directly
``ui.running/failed/        a handler's status, for views; ``ui.cancel`` is the
progress_of/cancel``        event of a Cancel button
``ui.loaded``               whether a load has finished since the page opened
``ui.testing``              run a handler in a unit test
``@ui.page``                where a page lives: its route, navigation, who may see it
``@ui.dashboard``           a page that is not a route (the project's ``main()``)
``ui.record/list/form``     the page kinds: named slots, placed by lex-app (``ui.list`` is
                            left out of ``import *``, which would hide Python's ``list``)
``ui.field/step``           a form's inputs, and a wizard's steps; the handler gets ``values``.
                            ``ui.button(confirm=, ask=)`` asks before its handler runs
``ui.section``              a titled part of a page; ``load=`` makes it load on its own
``ui.text/fact/status/      what fills the slots; none takes a styling argument
message/column/
button/link``
``ui.each/switch``          repetition and choice; ``visible=`` on every component
                            decides what is shown, never what is allowed
``ui.table``                rows from a model (``where=``) or a ``@ui.rows`` handler; ``search=``,
                            ``filters=``, ``export=``, ``totals=``, ``row_actions=``,
                            ``choose_columns=``, ``page_size=`` (``None``: every row, up to 200)
``ui.address``              a page's address, for a row that leads to a page of its own
``ui.reflex_page``          a page written in raw Reflex, as somewhere a link may lead
``ui.custom_block``         a raw component, marked for review with its reason
``ui.embed``                the other way round: one lex.ui part inside a raw Reflex page,
                            for moving a project a table at a time
``ui.PageError``            a page declared wrongly: says the page, the slot, what to write
==========================  =====================================================

Import this module only in the Reflex process: it defines Reflex states.
"""

from lex.lex_app.reflex.data import LexApiError as ApiError
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.guard import create, delete, edit, permission, read, signed_in
from lex.lex_app.reflex.ui.handlers import action, custom, job, load
from lex.lex_app.reflex.ui.pages.declare import dashboard, embed, page
from lex.lex_app.reflex.ui.pages.forms import field, form, step
from lex.lex_app.reflex.ui.pages.links import address, reflex_page
from lex.lex_app.reflex.ui.pages.kinds import list_ as list  # noqa: A001 - ui.list is the page kind
from lex.lex_app.reflex.ui.pages.kinds import record, section
from lex.lex_app.reflex.ui.pages.parts import PageError
from lex.lex_app.reflex.ui.pages.tables import column, table
from lex.lex_app.reflex.ui.pages.vocab import button, custom_block, each, fact, link, message, status, switch, text
from lex.lex_app.reflex.ui.results import Refuse, download, goto, progress, refresh, toast
from lex.lex_app.reflex.ui.rows import rows
from lex.lex_app.reflex.ui.runs import cancel, failed, loaded, progress_of, running
from lex.lex_app.reflex.ui.state import State

__all__ = [
    "ApiError", "PageError", "Refuse", "State", "action", "address", "button", "cancel", "column",
    "create", "custom", "custom_block", "dashboard", "delete", "download", "each", "edit", "embed", "fact",
    "failed", "field", "form", "goto", "job", "link", "load", "loaded", "message", "page", "permission", "progress",
    "progress_of", "read", "record", "reflex_page", "refresh", "rows", "running", "section", "signed_in", "status",
    "step", "switch", "table", "testing", "text", "toast",
]
