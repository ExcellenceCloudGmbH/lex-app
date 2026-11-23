"""``ui.State``: the base class of every page state."""

from __future__ import annotations

import reflex as rx

from lex.lex_app.reflex.ui import context


class State(rx.State):
    """The base class of a page state: handler status for views, and the body's helpers.

    Inside a handler body: ``self.user``, ``self.api``, ``self.params``, ``self.can(Model, scope)``
    and ``self.state(Other)``. They are properties, so the browser cannot call them.
    """

    #: Handler key -> the first argument of each run in flight ('' without arguments).
    ui_running: dict[str, list[str]] = {}
    #: Handler key -> the failure message of its last run.
    ui_failed: dict[str, str] = {}
    ui_progress: dict[str, float] = {}
    ui_progress_message: dict[str, str] = {}
    #: Counts page openings in this tab; loads and jobs compare it with the visit they started in.
    ui_visit: int = 0
    #: What ``/ui-error`` shows: kind, title, message, path.
    ui_error: dict[str, str] = {}

    _ui_boots: dict[str, str] = {}
    _ui_pending_loads: list[str] = []
    _ui_optional_loads: list[str] = []

    user = property(lambda self: context.current().session.user)
    api = property(lambda self: context.api_of(context.current()))
    params = property(lambda self: context.current().session.params)
    can = property(lambda self: context.current().can)
    state = property(lambda self: context.current().state_of)

    @rx.event
    async def ui_open(self, required: list[str], optional: list[str], requires: str):
        """A page opened: a new visit. Task 8 makes it start the page's loads."""
        self.ui_visit += 1

    @rx.event
    def ui_cancel(self, key: str):
        """A Cancel button: ask the handler's runs in this tab to stop at their next yield."""
        from reflex_base.event.context import EventContext

        from lex.lex_app.reflex.ui import runs

        runs.request_cancel(EventContext.get().token, key)
