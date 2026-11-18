"""lex-app's own pages and controls inside a Reflex dashboard -- ``lex_view`` and ``lex_widgets``.

The Reflex twins of ``lex.lex_app.streamlit``'s helpers, over the same contract
(:mod:`lex.lex_app.embed`): the same URL in front of lex-app's React app, the
same events back, the same widget-host manifest. What differs is what Reflex
makes possible -- a page compiles once, so every argument that names a record
may be a state var, read in the browser when it changes (decision D6), and an
event reaches an event handler instead of a script rerun::

    class Investors(rx.State):
        @rx.event
        def created(self, payload: dict):
            return rx.toast(f"Investor {payload['id']} created")

    lex_view("investor/create", flow=Flow().create("investor").table("investor"),
             on_create=Investors.created)

    lex_calculation("fund", FundDashboard.pk, show_log=True, on_status=FundDashboard.status)

========================  ====================================================
:func:`lex_view`          one lex-app page: a table, a form, a record
:func:`lex_widgets`       several lex-app controls in one frame
:func:`lex_calculation`   one calculation's status and button (``lex_widgets`` of one)
:func:`calculation`       a calculation widget for :func:`lex_widgets`
:func:`calculation_log`   a calculation's log (``tree=True``: its call tree)
========================  ====================================================

Write a page's chained forms with :class:`~lex.lex_app.embed.Flow` and
:func:`~lex.lex_app.embed.ref`, exactly as in Streamlit.

No ``from __future__ import annotations`` here: Reflex reads the component's
prop and event annotations at class creation, and string annotations naming a
function-local type would resolve to nothing -- dropping every event handler.
"""

import itertools
import os
from functools import cache
from typing import Any, Mapping, Sequence

import reflex as rx

from lex.lex_app.embed.core import EVENT_FLAGS, build_lex_view_url, origin_of
from lex.lex_app.embed.widgets import (
    WidgetSpecError,
    build_manifest,
    calculation_log_spec,
    calculation_spec,
)

#: The route of lex-app's widget host: the lightweight second entry of its build.
WIDGET_HOST_PATH = "/embed.html"

#: How tall the log a calculation opens is, as in Streamlit.
LOG_DIALOG_HEIGHT = 820

_PATH_MARK = "__lex_path__"
_IDS = itertools.count(1)


def lex_app_base_url() -> str:
    """Where lex-app's React app is, for the frames a dashboard opens.

    ``REACT_APP_URL`` or ``LEX_FRONTEND_URL`` when set; else the app hostname on
    the same registrable domain as the hostname the dashboards are canonically
    served on -- a frame on another registrable domain is a third-party context
    without the user's session; else a local ``lex start``. When lex-app itself
    frames the dashboard, the browser opens the page on the origin lex-app was
    reached on instead (``_js/lex_frame.jsx``), which covers attached domains.
    """
    from lex import hosts

    explicit = (os.getenv("REACT_APP_URL") or os.getenv("LEX_FRONTEND_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    dashboard_host = hosts.host_of(hosts.canonical_url("reflex"))
    app_url = hosts.same_site_url("app", dashboard_host) if dashboard_host else ""
    if app_url and not hosts.host_of(app_url).startswith("localhost"):
        return app_url.rstrip("/")
    return "http://localhost:8000"


@cache
def _frame_class() -> type:
    from reflex_base.components.component import field
    from reflex_base.vars.base import Var

    from lex.lex_app.reflex._js import asset_import

    passthrough = rx.event.passthrough_event_spec(dict)

    class LexFrame(rx.Component):
        """An iframe onto lex-app, speaking its postMessage contract (``_js/lex_frame.jsx``)."""

        library = asset_import("lex_frame.jsx")
        tag = "LexFrame"

        src: Var[str] = field(doc="The URL the frame opens.")
        expected_origin: Var[str] = field(doc="The only origin messages are believed from.")
        app_origins: Var[list[str]] = field(doc="lex-app's origins: a framing parent among them is used instead.")
        mode: Var[str] = field(doc='"view" (a lex-app page) or "widgets" (the widget host).')
        manifest: Var[dict] = field(doc="The widget-host manifest (widgets mode).")
        filters: Var[dict] = field(doc="Filters posted to the page (view mode).")
        url_filters_token: Var[str] = field(doc="The filters token the URL already carries.")
        height: Var[int] = field(doc="The frame's height in pixels (view mode).")
        min_height: Var[int] = field(doc="The least the widget host may shrink to.")
        title: Var[str] = field(doc="The frame's accessible title.")

        on_event: rx.EventHandler[passthrough] = field(doc="Every event envelope.")
        on_create: rx.EventHandler[passthrough] = field(doc="A record was created: {resource, id, data}.")
        on_update: rx.EventHandler[passthrough] = field(doc="A record was updated: {resource, id, data}.")
        on_delete: rx.EventHandler[passthrough] = field(doc="A record was deleted: {resource, id, data}.")
        on_select: rx.EventHandler[passthrough] = field(doc="The grid's selection changed: {resource, ids}.")
        on_navigate: rx.EventHandler[passthrough] = field(doc="The frame navigated: {from, to}.")
        on_filter: rx.EventHandler[passthrough] = field(doc="The grid's filters changed.")
        on_flow_step: rx.EventHandler[passthrough] = field(doc="A flow advanced.")
        on_status: rx.EventHandler[passthrough] = field(doc="A calculation's status: {widget_id, model, pk, status}.")
        on_open_log: rx.EventHandler[passthrough] = field(doc="A calculation's log was asked for.")

    # Defined in here to stay lazy, but named as if at module level: Reflex builds the names of
    # the JavaScript it generates from a component's qualified name, and "<locals>" is not
    # valid in one -- a page with a frame would not build.
    LexFrame.__qualname__ = LexFrame.__name__
    return LexFrame


def _frame(**props: Any) -> rx.Component:
    from lex.lex_app.reflex.design import lex_app_origins

    props.setdefault("app_origins", lex_app_origins())
    return _frame_class().create(**{k: v for k, v in props.items() if v is not None})


def _holds_var(value: Any) -> bool:
    if isinstance(value, rx.Var):
        return True
    if isinstance(value, Mapping):
        return any(_holds_var(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_holds_var(v) for v in value)
    return False


def _view_url(base: str, path: Any, **options: Any):
    """The embed request for ``path`` -- which may be a state var, joined in the browser."""
    if isinstance(path, str) and "reflex.Var" in path:
        # An f-string that embeds a var (``f"fund/{State.pk}/show"``) is a str
        # carrying the var's marker; made a Var, it is joined in the browser too.
        path = rx.Var.create(path)
    if isinstance(path, rx.Var):
        request = build_lex_view_url(base, _PATH_MARK, **options)
        prefix, suffix = request.url.split(_PATH_MARK, 1)
        return request, rx.Var.create(prefix) + path.to(str) + rx.Var.create(suffix)
    request = build_lex_view_url(base, path, **options)
    return request, request.url


def lex_view(
    path: Any = "",
    *,
    height: int = 800,
    hide_toolbar: bool = False,
    hide_actions: bool = False,
    redirect_after: str | None = None,
    redirect_after_create: str | None = None,
    redirect_after_update: str | None = None,
    flow: Mapping[str, Any] | None = None,
    serializer: str | None = None,
    filters: Any = None,
    extra_params: Mapping[str, str] | None = None,
    base_url: str | None = None,
    title: str | None = None,
    on_event: Any = None,
    on_create: Any = None,
    on_update: Any = None,
    on_delete: Any = None,
    on_select: Any = None,
    on_navigate: Any = None,
    on_filter: Any = None,
    on_flow_step: Any = None,
) -> rx.Component:
    """One lex-app page inside the dashboard: a table, a form, a record -- lex-app's own.

    Args:
        path: The page, as lex-app routes it: ``"investor"`` (the table),
            ``"investor/create"``, ``"investor/42/show"``. May be a state var
            (``f"investor/{State.pk}/show"``, no leading ``/``).
        height: The frame's height in pixels.
        hide_toolbar, hide_actions: Hide the list's toolbar, or its actions column.
        redirect_after, redirect_after_create, redirect_after_update: Where a
            form goes after saving (``{id}`` is the saved record's id).
        flow: Chained forms, a :class:`~lex.lex_app.embed.Flow`; it wins over the redirects.
        serializer: The serializer the embedded list reads with.
        filters: The list's filters, ``{field: value or [values]}``: a dict, a
            dict naming state vars, or a state var holding the dict; posted to
            the page whenever they change, without a reload.
        extra_params: More query parameters, applied last.
        base_url: lex-app's address, when :func:`lex_app_base_url` would pick the wrong one.
        title: The frame's accessible title.
        on_event: Gets every event envelope ``{type, id, ts, payload}`` -- and,
            passed, makes the page send every kind.
        on_create, on_update, on_delete, on_select, on_navigate, on_filter,
        on_flow_step: Get one kind of event's payload; passing one is what makes
            the page send that kind.
    """
    handlers = {
        "on_create": on_create, "on_update": on_update, "on_delete": on_delete,
        "on_select": on_select, "on_navigate": on_navigate, "on_filter": on_filter,
        "on_flow_step": on_flow_step,
    }
    # A handler is what asks the page for its kind of event; `on_event` alone asks for all.
    flags = {flag: handlers[flag] is not None or on_event is not None for flag in EVENT_FLAGS}
    if isinstance(filters, Mapping) and _holds_var(filters):
        # A dict naming a state var (``{"fund": State.pk}``) is read in the browser,
        # like a filters var: posted after each load and on change.
        filters = rx.Var.create(dict(filters))
    literal_filters = filters if isinstance(filters, Mapping) else None
    request, src = _view_url(
        base_url or lex_app_base_url(),
        path,
        hide_toolbar=hide_toolbar,
        hide_actions=hide_actions,
        redirect_after=redirect_after,
        redirect_after_create=redirect_after_create,
        redirect_after_update=redirect_after_update,
        flow=flow,
        serializer=serializer,
        filters=literal_filters,
        extra_params=dict(extra_params) if extra_params else None,
        **flags,
    )
    if request.url_filters_token and isinstance(src, str):
        # The first load carries the filters in the URL; later changes are posted.
        sep = "&" if "?" in src.split("#", 1)[0] else "?"
        base, _, fragment = src.partition("#")
        src = f"{base}{sep}filters={request.url_filters_token}" + (f"#{fragment}" if fragment else "")
    return _frame(
        src=src,
        expected_origin=request.expected_origin or "",
        mode="view",
        filters=request.filters if literal_filters is not None else filters,
        url_filters_token=request.url_filters_token or "",
        height=height,
        title=title,
        on_event=on_event,
        **handlers,
    )


# -- widgets ------------------------------------------------------------------


def _widget_id(kind: str, model: str, pk: Any, explicit: str | None) -> str:
    if explicit:
        return explicit
    if isinstance(pk, rx.Var):
        return f"{kind}_{model}_v{next(_IDS)}"
    return f"{kind}_{model}_{pk}"


def calculation(
    model: str,
    pk: Any,
    *,
    show_log: bool = False,
    log_height: int | None = None,
    on_status: bool = False,
    title: str | None = None,
    fields: Sequence[str] | None = None,
    variant: str = "full",
    show_log_button: bool = True,
    id: str | None = None,
) -> dict:
    """A calculation widget for :func:`lex_widgets`: its status and button (``variant``), its log.

    ``pk`` may be a state var. ``on_status=True`` makes it report its status to
    the ``on_status`` handler of :func:`lex_widgets`.
    """
    widget_id = _widget_id("calculation", model, pk, id)
    spec = calculation_spec(widget_id, model, "0" if isinstance(pk, rx.Var) else pk, show_log=show_log,
                            log_height=log_height, on_status=on_status, title=title,
                            fields=list(fields) if fields is not None else None, variant=variant,
                            show_log_button=show_log_button)
    if isinstance(pk, rx.Var):
        spec["pk"] = pk.to(str)
    return spec


def calculation_log(model: str, pk: Any, *, height: int | None = None, calculation_id: str | None = None,
                    tree: bool = False, id: str | None = None) -> dict:
    """A calculation's log for :func:`lex_widgets` (``tree=True``: its call tree). ``pk`` may be a state var."""
    widget_id = _widget_id("calculation_log_tree" if tree else "calculation_log", model, pk, id)
    spec = calculation_log_spec(widget_id, model, "0" if isinstance(pk, rx.Var) else pk, height=height,
                                calculation_id=calculation_id, tree=tree)
    if isinstance(pk, rx.Var):
        spec["pk"] = pk.to(str)
    return spec


class LexWidgetLogState(rx.State):
    """The log a calculation widget asked to see, in a dialog over the page."""

    log_open: bool = False
    log_manifest: dict = {}
    log_title: str = ""
    #: The lex-app the asking widget was framed from; its log opens there too.
    log_base: str = ""

    @rx.var
    def log_src(self) -> str:
        """The widget host the log dialog frames."""
        return f"{(self.log_base or lex_app_base_url()).rstrip('/')}{WIDGET_HOST_PATH}?embed=true"

    @rx.var
    def log_origin(self) -> str:
        """The only origin the log dialog's frame believes."""
        return origin_of(self.log_base or lex_app_base_url()) or ""

    @rx.event
    def open_log(self, payload: dict):
        """Show the log of the calculation ``payload`` names (``{model, pk, calculationId, base}``)."""
        model = str(payload.get("model") or "")
        pk = payload.get("pk")
        if not model or pk in (None, ""):
            return
        spec = calculation_log_spec("log", model, pk, height=LOG_DIALOG_HEIGHT - 60,
                                    calculation_id=payload.get("calculationId") or None)
        self.log_manifest = build_manifest([spec])
        self.log_title = f"Calculation log — {model} {pk}"
        self.log_base = str(payload.get("base") or "")
        self.log_open = True

    @rx.event
    def set_log_open(self, value: bool):
        """The dialog's open state, as the user closes it."""
        self.log_open = bool(value)


def log_dialog() -> rx.Component:
    """The one log dialog of a page, which every ``lex_widgets`` frame on it opens.

    ``lex_chrome`` draws it on every lex page, once. A page outside lex-app's
    chrome draws it itself -- or passes ``on_open_log=`` to ``lex_widgets``.
    """
    import rfds

    return rfds.dialog(
        _frame(src=LexWidgetLogState.log_src, expected_origin=LexWidgetLogState.log_origin,
               mode="widgets", manifest=LexWidgetLogState.log_manifest, min_height=LOG_DIALOG_HEIGHT - 60,
               title="Calculation log"),
        open=LexWidgetLogState.log_open,
        on_open_change=LexWidgetLogState.set_log_open,
        title=LexWidgetLogState.log_title,
        size="xl",
    )


def lex_widgets(
    *widgets: Mapping[str, Any],
    on_status: Any = None,
    on_open_log: Any = None,
    min_height: int = 48,
    base_url: str | None = None,
    title: str | None = None,
) -> rx.Component:
    """Several lex-app controls in one frame -- one React runtime for all of them.

    Widget count is free, frame count is not: each frame boots lex-app's whole
    widget host. Build the widgets with :func:`calculation` and
    :func:`calculation_log`.

    Args:
        on_status: Gets ``{widget_id, model, pk, status}`` from every widget made
            with ``on_status=True``, each time its status changes.
        on_open_log: Gets a widget's request to see its log. By default the log
            opens in the page's one log dialog (:func:`log_dialog`, which
            ``lex_chrome`` draws), as in Streamlit.
        min_height: The least the frame shrinks to.
    """
    if not widgets:
        raise WidgetSpecError("lex_widgets() needs at least one widget")
    manifest = build_manifest([dict(w) for w in widgets])
    base = (base_url or lex_app_base_url()).rstrip("/")
    frame = _frame(
        src=f"{base}{WIDGET_HOST_PATH}?embed=true",
        expected_origin=origin_of(base) or "",
        mode="widgets",
        manifest=manifest,
        min_height=min_height,
        title=title or "lex-app controls",
        on_status=on_status,
        on_open_log=on_open_log if on_open_log is not None else LexWidgetLogState.open_log,
    )
    return frame


def lex_calculation(model: str, pk: Any, *, on_status: Any = None, min_height: int = 48, **options: Any) -> rx.Component:
    """One calculation's status and button (and, with ``show_log=True``, its log).

    ``pk`` may be a state var; ``on_status`` gets the calculation's status as it
    changes. Options are :func:`calculation`'s. Several calculations on one page
    belong in one :func:`lex_widgets` frame instead.
    """
    return lex_widgets(calculation(model, pk, on_status=on_status is not None, **options),
                       on_status=on_status, min_height=min_height)


def lex_calculation_log(model: str, pk: Any, *, min_height: int = 48, **options: Any) -> rx.Component:
    """One calculation's log. ``pk`` may be a state var."""
    return lex_widgets(calculation_log(model, pk, **options), min_height=min_height)


def lex_calculation_log_tree(model: str, pk: Any, *, min_height: int = 48, **options: Any) -> rx.Component:
    """One calculation's log as a call tree. ``pk`` may be a state var."""
    return lex_widgets(calculation_log(model, pk, tree=True, **options), min_height=min_height)
