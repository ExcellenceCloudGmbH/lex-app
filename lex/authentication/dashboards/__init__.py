"""The Django-anchored dashboard hand-off (plan "long-lived, secure sessions", §6.3 and §6.5).

A Streamlit dashboard signs in through the app, never through Keycloak directly:

1. The dashboard proxy, finding no ``__Host-lex_dash`` cookie, sets a nonce cookie and sends the
   frame to ``<app host>/oidc/handoff?aud=streamlit&return=<url>&state=<nonce>``.
2. The app (:class:`lex.authentication.views.handoff_views.HandoffView`) mints a single-use code
   for the browser's active sign-in and sends the frame back to ``<dashboard>/auth/handoff`` --
   or, with no sign-in, back with ``error=login_required`` (in a frame: Keycloak is never framed)
   or on to the normal sign-in (top level).
3. The proxy redeems the code in-process -- :func:`redeem`, same image and database -- into a
   :class:`~lex.authentication.models.DashboardBinding`, and sets the opaque handle cookie.
4. Every request and WebSocket asks the broker (:mod:`.broker`) for a fresh access token of the
   sign-in behind the binding; only the access token and identity headers reach Streamlit.

The MCP embed ticket (:mod:`.embed`) reuses the same codes and bindings (``aud="embed"``).

Modules: :mod:`.modes` (``LEX_STREAMLIT_AUTH``), :mod:`.cookies`, :mod:`.sites` (allowed origins,
same-site app host) -- standard library only, importable before Django is set up -- and
:mod:`.handoff`, :mod:`.broker`, :mod:`.embed` (Django ORM). The names below are imported lazily
for the same reason.
"""
from __future__ import annotations

from typing import Any

_HANDOFF_NAMES = (
    "AUD_EMBED",
    "AUD_STREAMLIT",
    "CODE_TTL_SECONDS",
    "HandoffError",
    "Redemption",
    "issue_code",
    "redeem",
    "resolve",
    "revoke",
)

__all__ = list(_HANDOFF_NAMES)


def __getattr__(name: str) -> Any:
    if name in _HANDOFF_NAMES:
        from lex.authentication.dashboards import handoff

        return getattr(handoff, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
