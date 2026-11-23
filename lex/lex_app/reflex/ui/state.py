"""``ui.State``: the base class of every page state."""

from __future__ import annotations

from typing import Any

import reflex as rx

from lex.lex_app.reflex.ui import context

#: The page a failed required load sends the browser to.
ERROR_ROUTE = "/ui-error"


def _page_permitted(state: Any, requires: str) -> bool:
    """Whether the signed-in user holds the page's permission string ('' = no requirement)."""
    if not requires:
        return True
    from rfds.auth import current_identity

    from lex.lex_app.reflex.access import lex_permission_resolver

    return requires in lex_permission_resolver(current_identity(state))


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
    #: The route of this visit, and the states a load of this visit has already reset.
    _ui_page: str = ""
    _ui_reset: list[str] = []
    #: The ids of the loads the opener started that have not begun yet: a load not listed here
    #: was started on mount.
    _ui_started: list[str] = []
    #: Load id -> the token of its latest run in this tab; an older run on mount is stale.
    _ui_load_runs: dict[str, str] = {}

    user = property(lambda self: context.current().session.user)
    api = property(lambda self: context.api_of(context.current()))
    params = property(lambda self: context.current().session.params)
    can = property(lambda self: context.current().can)
    state = property(lambda self: context.current().state_of)

    @rx.event
    async def ui_open(self, page: str):
        """A page opened: a new visit, then the loads registered for ``page``.

        ``lex_page`` puts this last in ``on_load``, after the user's grants are loaded. Reflex
        starts background handlers at once, so the loads are started from here, not listed in
        ``on_load`` themselves. Only the route comes from the browser: the loads and the
        permission they need are looked up in the server's page registry. Optional loads start
        when the required ones have succeeded.
        """
        from lex.lex_app.reflex.ui import handlers

        self.ui_visit += 1
        from lex.lex_app.reflex.ui import runs

        await runs.sweep(self)
        self._ui_page = page
        self._ui_reset = []
        entry = handlers.page_of(page)
        if entry is None or not _page_permitted(self, entry.requires):
            self._ui_pending_loads, self._ui_optional_loads, self._ui_started = [], [], []
            return None
        self._ui_pending_loads = [handlers.load_id(h) for h in entry.required]
        self._ui_optional_loads = [handlers.load_id(h) for h in entry.optional] if entry.required else []
        starting = list(entry.required or entry.optional)
        self._ui_started = [handlers.load_id(h) for h in starting]
        return starting

    @rx.event
    def ui_cancel(self, key: str):
        """A Cancel button: ask the handler's runs in this tab to stop at their next yield."""
        from reflex_base.event.context import EventContext

        from lex.lex_app.reflex.ui import runs

        runs.request_cancel(EventContext.get().token, key)


def error_page() -> rx.Component:
    """What ``/ui-error`` shows: why the page could not be opened, a reference, and a way back."""
    import rfds

    from lex.lex_app.reflex.shell import lex_chrome

    retry = rfds.link("Try again", href=State.ui_error.get("path", "/"))
    title = State.ui_error.get("title", "This page could not be opened")
    message = State.ui_error.get("message", "Try again.")
    return lex_chrome(rfds.page(
        rfds.page_header(title="This page could not be opened", path=ERROR_ROUTE),
        rx.cond(
            State.ui_error.get("kind", "error") == "denied",
            rfds.empty_state(kind="no_permission", title=title, description=message),
            rfds.empty_state(kind="error", title=title, description=message, primary_action=retry),
        ),
    ))
