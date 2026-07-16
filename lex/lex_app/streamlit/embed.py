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
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from typing import Any, Dict, Optional, Union

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
# Flow builder — ergonomic multi-step redirect definition
# ---------------------------------------------------------------------------


class Flow(dict):
    """
    A dict subclass for defining multi-step redirect workflows declaratively.

    Each key is ``"<resource>/<operation>"`` (operation: ``create`` or ``update``),
    and each value is the target path (supports ``{resource}`` and ``{id}`` tokens).

    Can be built via the constructor or the fluent builder methods::

        # Dict-style (passed directly to flow= param)
        Flow({
            "investor/create": "/cashflow/{id}/edit",
            "cashflow/update": "/investor",
        })

        # Fluent builder style
        (Flow()
            .after_create("investor", "/cashflow/{id}/edit")
            .after_update("cashflow", "/investor")
        )
    """

    def after_create(self, resource: str, target: str) -> "Flow":
        """Add a redirect rule: after creating a record of ``resource``, go to ``target``."""
        self[f"{resource}/create"] = target
        return self

    def after_update(self, resource: str, target: str) -> "Flow":
        """Add a redirect rule: after updating a record of ``resource``, go to ``target``."""
        self[f"{resource}/update"] = target
        return self


def _resolve_base_url() -> str:
    """
    Resolve the base URL of the React application.

    Priority:
    1. ``REACT_APP_URL`` environment variable (explicit override)
    2. ``LEX_FRONTEND_URL`` environment variable (framework convention)
    3. Falls back to ``http://localhost:8000``
    """
    return (
        os.getenv("REACT_APP_URL")
        or os.getenv("LEX_FRONTEND_URL")
        or "http://localhost:8000"
    ).rstrip("/")


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
    * **Plain iframe (legacy).** No ``on_*`` flag and no ``serializer``
      requesting event-bearing behaviour → renders ``components.iframe``
      and returns ``None``. Existing call sites are unchanged.
    * **Bidirectional component.** At least one of ``on_create``,
      ``on_update``, ``on_select``, ``on_navigate``, ``on_flow_step`` is
      ``True`` → renders the lex_view custom component which forwards
      ``postMessage`` events from the React app back to Python.
      ``lex_view(...)`` then returns the latest event envelope (or
      ``None`` until the first event arrives).

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
    resolved_base = base_url.rstrip("/") if base_url else _resolve_base_url()

    # Normalise path
    if path and not path.startswith("/"):
        path = f"/{path}"

    raw_url = f"{resolved_base}{path}"

    # ── Parse & build query params ──
    parsed = urllib.parse.urlparse(raw_url)
    params: Dict[str, Any] = urllib.parse.parse_qs(parsed.query)

    # Core embed flag
    params["embed"] = ["true"]

    # Visibility toggles
    if hide_toolbar:
        params["hide_toolbar"] = ["true"]
    if hide_actions:
        params["hide_actions"] = ["true"]

    # Post-operation redirect overrides
    if redirect_after:
        params["redirect_after"] = [redirect_after]
    if redirect_after_create:
        params["redirect_after_create"] = [redirect_after_create]
    if redirect_after_update:
        params["redirect_after_update"] = [redirect_after_update]

    # Flow routing table — JSON-encoded, takes priority over flat params
    if flow:
        params["lex_flow"] = [json.dumps(flow, separators=(",", ":"))]

    # Serializer override (per docs/features/access-and-ui/lex_view callbacks.md)
    if serializer:
        params["serializer"] = [serializer]

    # Theme handshake (host → iframe): boot fallback via query param; the
    # custom-component shim additionally posts a `theme` message on load.
    if theme not in ("light", "dark"):
        raise ValueError(f"lex_view(theme=...) must be 'light' or 'dark', got {theme!r}")
    params["theme"] = [theme]

    # Event opt-in flags forwarded to the React bridge. The React side
    # only attaches handlers / emits events for the flags it sees here,
    # so off-by-default cost stays zero for plain iframe embeds.
    if on_create:
        params["emit_create"] = ["true"]
    if on_update:
        params["emit_update"] = ["true"]
    if on_delete:
        params["emit_delete"] = ["true"]
    if on_select:
        params["emit_select"] = ["true"]
    if on_navigate:
        params["emit_navigate"] = ["true"]
    if on_flow_step:
        params["emit_flow_step"] = ["true"]

    # Extra user-supplied params
    if extra_params:
        for key_, value in extra_params.items():
            params[key_] = [str(value)]

    new_query = urllib.parse.urlencode(params, doseq=True)

    # ── Ensure #embed fragment ──
    fragment = parsed.fragment
    if "embed" not in (fragment or ""):
        fragment = f"{fragment}#embed" if fragment else "embed"

    final_url = urllib.parse.urlunparse(
        parsed._replace(query=new_query, fragment=fragment)
    )

    logger.debug("lex_view → %s", final_url)
    # ── Render ──
    callbacks_requested = any(
        (on_create, on_update, on_delete, on_select, on_navigate, on_flow_step)
    )

    if callbacks_requested:
        # Bidirectional path: custom component returns the latest event.
        # Origin used to gate inbound postMessage events to the resolved
        # frontend base. ``urlparse(resolved_base)`` keeps scheme+netloc
        # only — the React app's window.location.origin must match.
        parsed_base = urllib.parse.urlparse(resolved_base)
        expected_origin = (
            f"{parsed_base.scheme}://{parsed_base.netloc}"
            if parsed_base.scheme and parsed_base.netloc
            else None
        )
        return render_lex_view_component(
            url=final_url,
            height=height,
            expected_origin=expected_origin,
            key=key,
        )

    # Legacy iframe path. Streamlit's components.iframe accepts width as
    # int (pixels) or str (CSS value); pass it through directly.
    width_arg: Any = width
    if isinstance(width, str) and width.isdigit():
        width_arg = int(width)

    components.iframe(final_url, height=height, width=width_arg, scrolling=scrolling)
    return None
