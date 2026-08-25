"""Streamlit custom component wrapping the widget-host iframe.

One declared component per page hosts a single iframe pointed at the lex-app
widget host route. The manifest travels as a component arg rather than on the
URL because it can exceed URL length; the shim relays it to the iframe once the
host reports ready.

``declare_component`` is called lazily rather than at import time: Streamlit
registers the static path against the script-run context live at the moment of
declaration, so declaring during module import can bind it to the wrong thread.
The same reasoning governs ``_lex_view_component``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit.components.v1 as components

_COMPONENT_NAME = "lex_widget_host"
_FRONTEND_DIR = Path(__file__).parent / "frontend"

_component_func: Optional[Any] = None


def _ensure_declared() -> Any:
    """Declare the component on first use and memoise it."""
    global _component_func
    if _component_func is not None:
        return _component_func

    dev_url = os.getenv("LEX_WIDGET_HOST_DEV_URL")
    if dev_url:
        _component_func = components.declare_component(_COMPONENT_NAME, url=dev_url)
    else:
        _component_func = components.declare_component(
            _COMPONENT_NAME, path=str(_FRONTEND_DIR)
        )
    return _component_func


def render_widget_host(
    *,
    url: str,
    manifest: Dict[str, Any],
    expected_origin: Optional[str],
    min_height: int = 200,
    theme_storage_key: Optional[str] = None,
    key: Optional[str] = None,
) -> Any:
    """Render the host iframe and return the latest event value, if any.

    Returns ``None`` until a widget emits an opted-in envelope; thereafter the
    most recent one. The shim dedupes by envelope id, so a rerun does not
    re-deliver an event the page has already seen.

    ``theme_storage_key`` travels as an arg rather than being baked into the
    static shim so the key keeps a single definition in ``lex/streamlit_theme.py``.
    A theme envelope is handled entirely inside the shim -- it writes the key on
    this origin, where the page's theme follower is listening -- so it never
    reaches Python and never costs a rerun.
    """
    component = _ensure_declared()
    return component(
        url=url,
        manifest=manifest,
        expected_origin=expected_origin,
        min_height=min_height,
        theme_storage_key=theme_storage_key,
        key=key,
        default=None,
    )


__all__ = ["render_widget_host"]
