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

import logging
import os
import urllib.parse
from typing import Any, Dict, Optional, Union

import streamlit.components.v1 as components

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_HEIGHT: int = 800
_DEFAULT_WIDTH: Union[int, str] = "100%"
_DEFAULT_SCROLLING: bool = True


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
    extra_params: Optional[Dict[str, str]] = None,
    base_url: Optional[str] = None,
) -> None:
    """
    Embed a page from the React application inside the current Streamlit page.

    The React frontend detects embed mode via the ``embed=true`` query
    parameter **and** the ``#embed`` URL fragment, and renders the content
    without its own sidebar/appbar chrome.

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
        ``"100%"`` or ``"50vw"``.  Default ``"100%"``.
    scrolling : bool
        Whether the iframe should be scrollable.  Default ``True``.
    hide_toolbar : bool
        If ``True``, hides the local toolbar row (History, Analytics,
        Density, Views, Sidebar toggle, etc.).  Maps to the
        ``?hide_toolbar=true`` query parameter read by ``CustomList``.
    hide_actions : bool
        If ``True``, hides the top actions bar (Create button, Refresh,
        Export).  Maps to the ``?hide_actions=true`` query parameter
        read by ``CustomListActions``.
    extra_params : dict, optional
        Arbitrary extra query parameters forwarded to the React app.
        Useful for future extensions or custom frontend logic.
    base_url : str, optional
        Override the React app base URL for this call only.
        By default uses ``REACT_APP_URL`` / ``LEX_FRONTEND_URL`` env vars.

    Examples
    --------
    Basic table embed::

        lex_view("quarter")

    Create form without toolbar or actions::

        lex_view("quarter/create", hide_toolbar=True, hide_actions=True, height=600)

    Side-by-side layout using Streamlit columns::

        col1, col2 = st.columns(2)
        with col1:
            lex_view("fund", height=500)
        with col2:
            lex_view("investor", height=500)

    Custom width in pixels::

        lex_view("nav_overview", width=1200)
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

    # Extra user-supplied params
    if extra_params:
        for key, value in extra_params.items():
            params[key] = [str(value)]

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
    # Streamlit's components.iframe accepts width as int (pixels) or str
    # (CSS value).  We pass it through directly.
    width_arg: Any = width
    if isinstance(width, str) and width.isdigit():
        width_arg = int(width)

    components.iframe(final_url, height=height, width=width_arg, scrolling=scrolling)
