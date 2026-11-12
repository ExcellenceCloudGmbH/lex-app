"""What this server offers its browser clients, as ``api/auth/session`` reports it.

Clients read ``capabilities`` to choose a code path, so a capability is advertised only once
the server really serves it:

* ``auth_contract`` -- the version of the HTTP auth contract (``lex/contracts/auth/v2``).
* ``dashboard_handoff`` -- the dashboard surfaces that sign in through the Django-anchored
  hand-off (``oidc/handoff``) instead of a token in the iframe URL.
* ``ws_close_codes`` -- whether WebSockets close with 4401 / 4403 / 4503 (after ``accept()``).

The last two are switched on by the Streamlit hand-off and WebSocket work; each reads its own
rollout flag in the function below, so this module stays the one place that decides.
"""
from __future__ import annotations

from typing import Any, Dict, List

#: The HTTP auth contract this server implements.
AUTH_CONTRACT_VERSION = 2


def dashboard_handoff_surfaces() -> List[str]:
    """Dashboard surfaces served by the hand-off: ``["streamlit"]`` when ``LEX_STREAMLIT_AUTH`` is ``dual`` or ``handoff``."""
    from lex.authentication.dashboards.modes import handoff_enabled, streamlit_auth_mode

    return ["streamlit"] if handoff_enabled(streamlit_auth_mode()) else []


def ws_close_codes() -> bool:
    """Whether WebSockets close with 4401 / 4403 / 4503: exactly when ``LEX_WS_AUTH`` is ``enforce``."""
    from lex.api.consumers.auth import WS_AUTH_ENFORCE, ws_auth_mode

    return ws_auth_mode() == WS_AUTH_ENFORCE


def auth_capabilities() -> Dict[str, Any]:
    return {
        "auth_contract": AUTH_CONTRACT_VERSION,
        "dashboard_handoff": dashboard_handoff_surfaces(),
        "ws_close_codes": ws_close_codes(),
    }


__all__ = ["AUTH_CONTRACT_VERSION", "auth_capabilities", "dashboard_handoff_surfaces", "ws_close_codes"]
