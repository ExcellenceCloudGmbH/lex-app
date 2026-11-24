"""``ui.State``: the base class of every page state."""

from __future__ import annotations

from typing import Any

import reflex as rx

from lex.lex_app.reflex.ui import context
from lex.lex_app.reflex.ui.context import RESERVED

#: The page a failed required load sends the browser to.
ERROR_ROUTE = "/ui-error"


def check_route(route: str) -> None:
    """Refuse a route whose dynamic argument is named like a ``ui.State`` helper (``ValueError``)."""
    from reflex.route import get_route_args

    for name in sorted(set(get_route_args(route)) & set(RESERVED)):
        raise ValueError(
            f"route {route!r}: the dynamic argument [{name}] has the name of the ui.State helper "
            f"self.{name}. Reflex makes a route argument a field of every state, which hides the helper "
            f"in every handler. Rename the argument, e.g. [{name}_id]."
        )


def check_app_routes(app: Any) -> None:
    """Refuse an app that has a page route with a dynamic argument named like a ``ui.State`` helper.

    ``create_app`` calls it once the project's pages are registered: it sees the pages added to
    the app and those declared with ``@rx.page`` or ``@lex_page`` that the app adds when it compiles.
    """
    from reflex_base.registry import RegistrationContext

    routes = list(getattr(app, "_unevaluated_pages", {}) or {})
    routes += [kwargs["route"] for _, kwargs in RegistrationContext.ensure_context().decorated_pages
               if kwargs.get("route")]
    for route in routes:
        check_route(route)
    from lex.lex_app.reflex.ui.pages import links

    links.check_reflex_pages(routes)   # a link to a raw page leads to a page the app has


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
    and ``self.state(Other)``. They are properties, so the browser cannot call them. Their names are
    reserved: a subclass declaring a field, computed var or method with one of them is refused
    when the class is defined.
    """

    def __init_subclass__(cls, **kwargs: Any):
        own = {*cls.__dict__, *cls.__dict__.get("__annotations__", {})}
        for name in sorted(own & set(RESERVED)):
            raise TypeError(
                f"{cls.__name__}.{name}: '{name}' is reserved on a ui.State -- self.{name} is the "
                f"handler's helper, and a field, computed var or method of that name would hide it. "
                f"Rename it, e.g. {name}_value or _{name}."
            )
        super().__init_subclass__(**kwargs)
        from lex.lex_app.reflex.ui import handlers

        handlers.own_handlers(cls)          # a handler from a mixin keeps its status under this state's name

    #: Handler key -> the first argument of each run in flight ('' without arguments).
    ui_running: dict[str, list[str]] = {}
    #: Handler key -> the failure message of its last run.
    ui_failed: dict[str, str] = {}
    ui_progress: dict[str, float] = {}
    #: Load key -> whether its last run finished; a page shows its skeleton until then.
    ui_loaded: dict[str, bool] = {}
    ui_progress_message: dict[str, str] = {}
    #: Counts page openings in this tab; loads and jobs compare it with the visit they started in.
    ui_visit: int = 0
    #: What ``/ui-error`` shows: kind, title, message, path.
    ui_error: dict[str, str] = {}
    #: "Handler key|field key" -> what is wrong with the value last sent for that field.
    ui_invalid: dict[str, str] = {}
    #: Handler key -> the step a wizard shows (0 is the first).
    ui_step: dict[str, int] = {}
    #: Handler key -> whether its form's last submit succeeded: the form's result shows while it is true.
    ui_done: dict[str, bool] = {}
    #: The dialog of a button with ``confirm=`` or ``ask=`` that is open ('' when none is), and the
    #: values that button passes to its handler.
    ui_asking: str = ""
    ui_asking_args: list = []
    #: Searched field id -> the matches the user may choose from now: ``{"value", "label"}``, a page of them.
    ui_matches: dict[str, list[dict[str, str]]] = {}

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
    #: Searched field id -> every value offered to this tab (as text) -> the row's id: what a form accepts.
    _ui_offered: dict[str, dict] = {}

    user = property(lambda self: context.current().session.user)
    api = property(lambda self: context.api_of(context.current()))
    params = property(lambda self: context.current().session.params)
    can = property(lambda self: context.current().can)
    state = property(lambda self: context.current().state_of)
    #: The browser tab the handler runs for (Reflex's client token): for a project's own per-tab store.
    tab_id = property(lambda self: context.current().session.tab)

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
        self.ui_invalid, self.ui_step, self.ui_done = {}, {}, {}      # a form starts over on a new visit
        self.ui_asking, self.ui_asking_args = "", []
        self._ui_page = page
        self._ui_reset = []
        entry = handlers.page_of(page)
        from lex.lex_app.reflex.ui.pages import rows_table

        tables = rows_table.reopen_events(page)     # a table already on screen: the new record's rows
        if entry is None or not _page_permitted(self, entry.requires):
            self._ui_pending_loads, self._ui_optional_loads, self._ui_started = [], [], []
            return None
        from lex.lex_app.reflex.ui import runs as ui_runs

        # A page shows its skeleton until its loads have finished in this visit.
        self.ui_loaded = {**dict(self.ui_loaded),
                          **{ui_runs.key_of(h): False for h in (*entry.required, *entry.optional)}}
        self._ui_pending_loads = [handlers.load_id(h) for h in entry.required]
        self._ui_optional_loads = [handlers.load_id(h) for h in entry.optional] if entry.required else []
        starting = list(entry.required or entry.optional)
        self._ui_started = [handlers.load_id(h) for h in starting]
        return [*starting, *tables]

    @rx.event
    def ui_cancel(self, key: str):
        """A Cancel button: ask the handler's runs in this tab to stop at their next yield."""
        from reflex_base.event.context import EventContext

        from lex.lex_app.reflex.ui import runs

        runs.request_cancel(EventContext.get().token, key)

    @rx.event
    def ui_ask(self, dialog: str, args: list):
        """A button with ``confirm=`` or ``ask=`` was pressed: its dialog opens, holding the button's values."""
        self.ui_asking, self.ui_asking_args = dialog, list(args)
        self.ui_invalid = {}

    @rx.event
    def ui_ask_close(self, open: bool = False):  # noqa: A002 - the name Radix gives the value
        """The open dialog was answered or dismissed."""
        if not open:
            self.ui_asking, self.ui_asking_args = "", []

    @rx.event(supersedes=True)
    async def ui_search(self, field: str, text: str = ""):
        """A searched choice opened (``text`` is ``""``), or the user typed into it: offer the matches.

        Only the field's id comes from the browser: the rows handler that searches is looked up in
        the server's registry, and runs as the user.
        """
        from lex.lex_app.reflex.ui import forms

        await forms.search(self, field, text)

    @rx.event
    def ui_step_back(self, key: str):
        """A wizard's Back button: the step before, with what was entered kept."""
        self.ui_step = {**dict(self.ui_step), key: max(int(dict(self.ui_step).get(key, 0)) - 1, 0)}


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