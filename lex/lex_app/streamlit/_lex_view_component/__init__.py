"""
Streamlit Custom Component scaffolding for ``lex_view``.

This module declares the bidirectional Streamlit component that wraps the
lex-app React iframe. Its job is to bridge the browser-side ``postMessage``
events emitted by the React app back into Python so the Streamlit script
can re-run with each event as the return value of ``lex_view(...)``.

The protocol is documented in
``docs/features/access-and-ui/lex_view callbacks.md``.

Why a custom component instead of a polling REST endpoint:

* ``window.postMessage`` is the standard parent↔iframe channel.
* No backend round-trip, no new dependencies (Streamlit ships
  ``declare_component`` natively, the JS shim is a single vanilla file).
* Streamlit re-runs the script automatically when ``setComponentValue``
  fires — matches Streamlit's reactive model exactly.

This file only contains the *declaration*. The public ``lex_view`` helper in
``lex.lex_app.streamlit.embed`` decides per call whether to invoke this
component (callbacks opted in) or fall back to a plain
``components.iframe`` (legacy zero-arg form, returns ``None``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import streamlit.components.v1 as components


_COMPONENT_NAME = "lex_view"
_FRONTEND_DIR = Path(__file__).parent / "frontend"

# Lazy-initialised holder for the declared component. We deliberately do NOT
# call ``declare_component`` at import time. Streamlit (≥1.30) only registers
# a component if there is an active ``ScriptRunContext`` on the calling
# thread at the moment of ``declare_component`` (see
# ``streamlit/components/v1/component_registry.py`` → ``if ctx is not None:``).
# Importing this module from any thread without a ctx — the proxy thread,
# a worker, a unit test, even Streamlit's own import-scanning during boot —
# would silently no-op the registration; the module would then sit in
# ``sys.modules`` so the registration would never be retried on the next
# script run, and every request to
# ``/component/lex.lex_app.streamlit._lex_view_component.lex_view/index.html``
# would return 404 "Component not found".
#
# Declaring on first render guarantees a ctx is present (we're inside the
# user's script) and is the pattern used by mature Streamlit components.
_component_func: Optional[Any] = None


def _ensure_declared() -> Any:
    """Declare (or return the cached declaration of) the lex_view component."""
    global _component_func
    if _component_func is not None:
        return _component_func

    # In dev you can set LEX_VIEW_COMPONENT_URL to point at a Vite dev server
    # serving the JS shim. The default ships the static ``frontend/`` folder
    # inside the wheel — no build step, no extra deps.
    dev_url = os.getenv("LEX_VIEW_COMPONENT_URL")
    if dev_url:
        _component_func = components.declare_component(_COMPONENT_NAME, url=dev_url)
    else:
        _component_func = components.declare_component(
            _COMPONENT_NAME, path=str(_FRONTEND_DIR)
        )
    return _component_func


def render_lex_view_component(
    *,
    url: str,
    height: int = 800,
    expected_origin: Optional[str] = None,
    key: Optional[str] = None,
) -> Optional[dict]:
    """Render the bidirectional lex_view component.

    Parameters
    ----------
    url : str
        Fully resolved URL of the lex-app page to embed (already includes
        the ``embed=true`` query param, the ``#embed`` fragment, and any
        ``hide_toolbar`` / ``redirect_after_*`` / ``lex_flow`` /
        ``serializer`` / ``emit_*`` params).
    height : int
        Iframe height in pixels. Streamlit needs the height up-front;
        the component echoes it back via ``setFrameHeight``.
    expected_origin : str, optional
        Origin used to gate inbound ``postMessage`` events. When set,
        messages whose ``event.origin`` does not match are silently
        dropped. Pass ``None`` to accept any origin (useful for local
        ``file://`` testing only).
    key : str, optional
        Stable Streamlit key so the component state survives re-runs.
        Defaults to the URL itself — different embeds in the same script
        get their own component slot automatically.

    Returns
    -------
    dict | None
        The latest event envelope received from the React app, or
        ``None`` on the first render before any event has arrived.
    """
    if key is None:
        key = f"lex_view:{url}"

    return _ensure_declared()(
        url=url,
        height=height,
        expected_origin=expected_origin,
        key=key,
        default=None,
    )


__all__ = ["render_lex_view_component"]
