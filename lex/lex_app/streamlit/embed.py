"""
Built-in Streamlit helpers for embedding React application views.

Provides ``lex_view()`` — a single function that embeds any page from the
React frontend inside a Streamlit dashboard via an iframe, with full control
over which UI elements are shown.

Usage
-----
::

    import streamlit as st
    from lex.lex_app.streamlit.embed import lex_view

    st.set_page_config(layout="wide")
    st.title("My Dashboard")

    # Embed a table view
    lex_view("quarter")

    # Embed a create form, toolbar-free
    lex_view("quarter/create", hide_toolbar=True)

    # Side-by-side layout
    col1, col2 = st.columns(2)
    with col1:
        lex_view("fund", height=600)
    with col2:
        lex_view("investor", height=600)

    # Chain forms together: each step opens on the record the last one saved
    lex_view("investor/create", flow=(
        Flow().create("investor").create("vehicle").update("investor").table("investor")
    ))

    # Enter records one after another
    lex_view("investor/create", flow=Flow().create("investor").loop_last())
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Union

import streamlit.components.v1 as components

from lex.lex_app.streamlit._lex_view_component import render_lex_view_component

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_HEIGHT: int = 800
_DEFAULT_WIDTH: Union[int, str] = "100%"
_DEFAULT_SCROLLING: bool = True


# ---------------------------------------------------------------------------
# Flow builder and filters -- shared with the Reflex dashboards
# ---------------------------------------------------------------------------
#
# The contract with the React app lives in lex.lex_app.embed.core, free of
# Streamlit; these names stay importable from here, as they always were.
from lex.lex_app.embed.core import (  # noqa: E402,F401
    FLOW_WIRE_VERSION,
    MAX_URL_FILTERS_TOKEN,
    STAY,
    Flow,
    FlowError,
    Ref,
    _filter_scalar,
    build_lex_view_url,
    encode_filters_token,
    normalise_filters,
    ref,
)


#: Query parameter the lex-app frontend puts on the URL of a dashboard it frames: its own origin.
LEX_APP_ORIGIN_PARAM = "lex_app_origin"
_LEX_APP_ORIGIN_STATE_KEY = "lex_app_origin"


def _configured_frontends() -> List[str]:
    return [url for url in (os.getenv("REACT_APP_URL"), os.getenv("LEX_FRONTEND_URL")) if url]


def validated_lex_app_origin(raw: Any) -> Optional[str]:
    """``raw`` as an origin, when this instance lists it as one of its app origins.

    Accepted: an app host of ``LEX_INSTANCE_HOSTS`` (or its ``DOMAIN_HOSTED`` fallback), the
    configured frontend (``REACT_APP_URL`` / ``LEX_FRONTEND_URL``), and loopback origins on an
    instance that is not deployed. Anything else is ignored: the value arrives in the URL, and
    an origin taken from it unchecked would let a link embed a foreign site in the dashboard --
    and, with an ``on_*`` flag, accept its messages as events.
    """
    from lex.authentication.dashboards import sites

    origin = sites.normalize_origin(raw)
    if origin and sites.is_app_origin(origin, extra=_configured_frontends()):
        return origin
    return None


def remember_lex_app_origin(query_params: Any = None) -> Optional[str]:
    """Keep this page's ``lex_app_origin`` for the session, once validated; returns it.

    Kept in ``st.session_state`` because Streamlit's page navigation drops query parameters,
    while the session -- the frame the frontend opened -- keeps its frontend.
    """
    import streamlit as st

    params = st.query_params if query_params is None else query_params
    raw = params.get(LEX_APP_ORIGIN_PARAM)
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw:
        return None
    origin = validated_lex_app_origin(raw)
    if origin is None:
        logger.warning("Ignoring lex_app_origin %r: not an app origin of this instance", raw)
        return None
    st.session_state[_LEX_APP_ORIGIN_STATE_KEY] = origin
    return origin


def _session_lex_app_origin() -> Optional[str]:
    try:
        import streamlit as st

        value = st.session_state.get(_LEX_APP_ORIGIN_STATE_KEY)
    except Exception:  # outside a script run
        return None
    return value if isinstance(value, str) and value else None


def _resolve_base_url() -> str:
    """
    Resolve the base URL of the React application.

    Priority:
    1. The ``lex_app_origin`` the frontend framing this dashboard named, once validated
       (:func:`remember_lex_app_origin`) -- the origin the user is signed in on, same-site with
       the dashboard, so the embedded page carries the user's session.
    2. ``REACT_APP_URL`` environment variable (explicit override)
    3. ``LEX_FRONTEND_URL`` environment variable (framework convention)
    4. The application hostname on the same registrable domain as the hostname
       this dashboard is canonically served on
    5. Falls back to ``http://localhost:8000``

    Step 4 is what makes this work on a deployed instance without anything
    being set, and what keeps it working once a customer-owned domain is
    attached. This frame is the *reverse* of the one lex-app draws around a
    dashboard, and it has the same constraint: a frame whose origin is on a
    different registrable domain from the page around it is a third-party
    context, so a dashboard on ``streamlit.app.customer.com`` must frame
    ``app.customer.com`` and not the platform hostname.
    """
    from lex import hosts as lex_hosts

    named = _session_lex_app_origin()
    if named:
        return named.rstrip("/")

    explicit = (os.getenv("REACT_APP_URL") or os.getenv("LEX_FRONTEND_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    # The dashboard's own canonical hostname names the site this process serves;
    # there is no request here to read one from, and every hostname a dashboard
    # answers on is under the application host by construction.
    dashboard_host = lex_hosts.host_of(lex_hosts.canonical_url("streamlit"))
    app_url = lex_hosts.same_site_url("app", dashboard_host) if dashboard_host else ""
    if app_url and not lex_hosts.host_of(app_url).startswith("localhost"):
        return app_url.rstrip("/")

    return "http://localhost:8000"


def lex_view(
    path: str = "",
    *,
    height: int = _DEFAULT_HEIGHT,
    width: Union[int, str] = _DEFAULT_WIDTH,
    scrolling: bool = _DEFAULT_SCROLLING,
    hide_toolbar: bool = False,
    hide_actions: bool = False,
    redirect_after: Optional[str] = None,
    redirect_after_create: Optional[str] = None,
    redirect_after_update: Optional[str] = None,
    flow: Optional[Dict[str, str]] = None,
    serializer: Optional[str] = None,
    theme: str = "light",
    on_create: bool = False,
    on_update: bool = False,
    on_delete: bool = False,
    on_select: bool = False,
    on_navigate: bool = False,
    on_flow_step: bool = False,
    on_filter: bool = False,
    filters: Optional[Dict[str, Any]] = None,
    extra_params: Optional[Dict[str, str]] = None,
    base_url: Optional[str] = None,
    key: Optional[str] = None,
) -> Optional[dict]:
    """
    Embed a page from the React application inside the current Streamlit page.

    The React frontend detects embed mode via the ``embed=true`` query
    parameter **and** the ``#embed`` URL fragment, and renders the content
    without its own sidebar/appbar chrome.

    Two modes
    ---------
    * **Plain iframe (legacy).** No ``on_*`` flag and ``filters=None`` →
      renders ``components.iframe`` and returns ``None``. Existing call
      sites are unchanged.
    * **Bidirectional component.** At least one of ``on_create``,
      ``on_update``, ``on_delete``, ``on_select``, ``on_navigate``,
      ``on_flow_step``, ``on_filter`` is ``True``, or ``filters`` is a dict
      (that alone selects it) → renders the lex_view custom component,
      which forwards ``postMessage`` events from the React app back to
      Python and posts later filter changes into the frame.
      ``lex_view(...)`` then returns the latest event envelope (or
      ``None`` until the first event arrives).

    The two modes are different Streamlit elements: a page that switches an
    embed with no ``on_*`` flag between a ``filters`` dict and
    ``filters=None`` across reruns remounts the frame, which resets its grid,
    each time. To untick one field, keep passing a dict and give that field
    ``None`` (``{"region": None}``). Re-passing the same filters on a rerun
    posts nothing, so it does not override what the user changed inside the
    frame; filters that differ from the ones the frame last received are
    posted, and replace the ticked values of the fields they name.

    See ``docs/features/access-and-ui/lex_view callbacks.md`` for the
    event envelope schema and the per-type payload contracts.

    Parameters
    ----------
    path : str
        The React route to embed, e.g. ``"quarter"``, ``"fund/42"``,
        ``"investor/create"``.  A leading ``/`` is added automatically
        if missing.
    height : int
        Iframe height in pixels.  Default ``800``.
    width : int | str
        Iframe width — either an integer (pixels) or a CSS string like
        ``"100%"`` or ``"50vw"``.  Default ``"100%"``.  Ignored in
        bidirectional mode (the custom component is always 100% wide).
    scrolling : bool
        Whether the iframe should be scrollable.  Default ``True``.
        Ignored in bidirectional mode.
    hide_toolbar : bool
        If ``True``, hides the local toolbar row (History, Analytics,
        Density, Views, Sidebar toggle, etc.).  Maps to the
        ``?hide_toolbar=true`` query parameter read by ``CustomList``.
    hide_actions : bool
        If ``True``, hides the top actions bar (Create button, Refresh,
        Export).  Maps to the ``?hide_actions=true`` query parameter
        read by ``CustomListActions``.
    redirect_after : str, optional
        React route to navigate to after *any* successful create or update.
        Supports ``{resource}`` and ``{id}`` template tokens.
    redirect_after_create : str, optional
        Override ``redirect_after`` for create operations only.
    redirect_after_update : str, optional
        Override ``redirect_after`` for update operations only.
    flow : dict, optional
        Multi-step redirect routing table — see ``Flow``.
    serializer : str, optional
        Name of a registered DRF serializer on the model. Forwarded as
        ``?serializer=<name>`` so the embedded list/detail uses that
        serializer to shape its response. An unknown name surfaces as
        HTTP 400 (see cluster 12h). Resolves through
        ``ModelEntryProviderMixin.get_serializer_class``.
    on_create, on_update, on_delete, on_select, on_navigate, on_flow_step : bool
        Opt-in flags for the bidirectional event channel. Setting any
        of them switches ``lex_view`` to the custom-component path and
        causes the return value to carry event dicts.

        ``on_select`` additionally forwards as ``?emit_select=true`` so
        the React side wires the AG Grid ``onSelectionChanged`` callback
        only when explicitly requested (it is opt-in because driving
        Streamlit re-runs on every grid click is expensive).
    on_filter : bool
        Report filter changes as ``"filter"`` events. Payload: ``resource``,
        ``filters`` (field → ticked keys), ``filter_model`` (the grid's whole
        model) and ``row_count``. A value list applies on each tick, with no
        Apply button, so each change the user makes sends its own
        ``"filter"`` event and reruns the script. Pass a table's filters on
        to *another* table; never echo them back into the same table's
        ``filters=`` — an echo that arrives one rerun late can undo the
        user's latest tick (spec Amendment 15).
    filters : dict, optional
        Field name to stored values to tick: a list, one value, or ``None``
        to untick them all. An empty list also unticks them all, as ticking
        nothing does in the grid's value lists. Only the ticked values change:
        a condition typed in the field's filter stays. Applied on first load and,
        when changed on a later rerun, posted into the frame without reloading
        it. Filters on fields not named here, set by the user, stay. Filters
        whose token is longer than 2,000 characters (a long id list) are not
        put in the frame's URL; they are posted once the frame has loaded.
    theme : str
        Host colour scheme forwarded to the embedded app — ``"light"``
        (default) or ``"dark"``.  Sent as the ``?theme=`` query parameter
        (boot fallback) and re-announced over the host→iframe ``theme``
        postMessage in bidirectional mode.  See the *Theme handshake*
        section of ``lex_view callbacks.md``.
    extra_params : dict, optional
        Arbitrary extra query parameters forwarded to the React app.
    base_url : str, optional
        Override the React app base URL for this call only.
        By default uses ``REACT_APP_URL`` / ``LEX_FRONTEND_URL`` env vars.
    key : str, optional
        Streamlit component key — only used in bidirectional mode.
        Defaults to the resolved URL so different embeds in the same
        script get distinct component slots automatically.

    Returns
    -------
    None | dict
        ``None`` in plain-iframe mode (no callbacks requested), or in
        bidirectional mode before the first event has arrived.
        Otherwise the latest event envelope dict.

    Examples
    --------
    Basic table embed (plain iframe, no callbacks)::

        lex_view("quarter")

    React to AG Grid selection changes::

        event = lex_view("investor", on_select=True)
        if event and event["type"] == "select":
            st.write(event["payload"]["ids"])

    Request a specific serializer for the embedded list::

        lex_view("investor", serializer="InvestorWithFundSerializer")

    Multi-step workflow with creation hooks::

        event = lex_view(
            "investor",
            on_create=True,
            flow=Flow().after_create("investor", "/cashflow/{id}/edit"),
        )
        if event and event["type"] == "create":
            st.toast(f"Created investor #{event['payload']['id']}")
    """
    request = build_lex_view_url(
        base_url if base_url else _resolve_base_url(),
        path,
        hide_toolbar=hide_toolbar,
        hide_actions=hide_actions,
        redirect_after=redirect_after,
        redirect_after_create=redirect_after_create,
        redirect_after_update=redirect_after_update,
        flow=flow,
        serializer=serializer,
        theme=theme,
        filters=filters,
        extra_params=extra_params,
        on_create=on_create,
        on_update=on_update,
        on_delete=on_delete,
        on_select=on_select,
        on_navigate=on_navigate,
        on_flow_step=on_flow_step,
        on_filter=on_filter,
    )
    final_url = request.url

    logger.debug("lex_view → %s", final_url)
    # ── Render ──
    if request.bidirectional:
        # Bidirectional path: custom component returns the latest event.
        # Inbound postMessage events are gated to the resolved frontend base's
        # origin -- the React app's window.location.origin must match.
        return render_lex_view_component(
            url=final_url,
            height=height,
            expected_origin=request.expected_origin,
            key=key,
            filters=request.filters,
            filters_token=request.filters_token,
            url_filters_token=request.url_filters_token,
        )

    # Legacy iframe path. Streamlit's components.iframe accepts width as
    # int (pixels) or str (CSS value); pass it through directly.
    width_arg: Any = width
    if isinstance(width, str) and width.isdigit():
        width_arg = int(width)

    components.iframe(final_url, height=height, width=width_arg, scrolling=scrolling)
    return None
