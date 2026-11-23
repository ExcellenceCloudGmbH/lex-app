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

==========================  =====================================================
``ui.State``                base class of a page state: ``self.user``, ``self.api``,
                            ``self.params``, ``self.can()``, ``self.state()``
``@ui.load``                fills the page when it opens
``@ui.action``              a short handler, under about a second
``@ui.job``                 long work with progress; the user keeps working
``@ui.custom``              a raw Reflex handler, marked for review
``ui.goto/toast/download``  what a handler returns
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
==========================  =====================================================

Import this module only in the Reflex process: it defines Reflex states.
"""

from lex.lex_app.reflex.data import LexApiError as ApiError
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.guard import create, delete, edit, permission, read, signed_in
from lex.lex_app.reflex.ui.handlers import action, custom, job, load
from lex.lex_app.reflex.ui.results import Refuse, download, goto, progress, toast
from lex.lex_app.reflex.ui.runs import cancel, failed, loaded, progress_of, running
from lex.lex_app.reflex.ui.state import State

__all__ = [
    "ApiError", "Refuse", "State", "action", "cancel", "create", "custom", "delete", "download", "edit",
    "failed", "goto", "job", "load", "loaded", "permission", "progress", "progress_of", "read", "running",
    "signed_in", "testing", "toast",
]
