"""The token broker: a fresh access token for a dashboard binding, from the sign-in it rests on.

``access_token(binding)`` asks django-oauth2-codeflow for an access token of the binding's
``OIDCSession`` that stays valid for at least :data:`MIN_TTL_SECONDS`
(``sessions.access_token_for``): one primary-key read when the stored token is fresh, otherwise a
single-flight refresh shared with every other holder of the same sign-in. The library is the only
refresh-token holder; nothing here touches a refresh token.

* The sign-in is over (``OIDCSessionEnded``: ``invalid_grant``, logout, back-channel logout), the
  user was deactivated, or an embed's token has run out -- the binding is revoked and the
  exception propagates (the proxy answers 401 / closes 4401).
* Keycloak cannot be reached (``OIDCAuthUnavailable``) -- it propagates unchanged; the sign-in and
  the binding are intact (the library serves the still-valid token; then the proxy degrades to
  identity only).

:func:`standing_problem` answers the same question from the database alone, without asking
Keycloak (the proxy's ``/auth/status``).

Synchronous: call it off any event loop.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from django.utils import timezone
from oauth2_authcodeflow.exceptions import OIDCSessionEnded
from oauth2_authcodeflow.sessions import access_token_for

from lex.authentication.dashboards import handoff

#: The token handed out stays valid at least this long (the plan's ``min_ttl=60``).
MIN_TTL_SECONDS = 60


def standing_problem(binding: Any) -> Optional[str]:
    """Why ``binding`` can no longer sign anything in, from the database alone (no Keycloak call):
    revoked, past its expiry (embeds), its sign-in ended, or its user deactivated. ``None`` when
    none of these holds."""
    if binding.revoked_at is not None:
        return binding.revoke_reason or "binding_revoked"
    expires_at: Optional[datetime] = binding.expires_at
    if expires_at is not None and expires_at <= timezone.now():
        return "binding_expired"
    if binding.session.ended_at is not None:
        return f"ended:{binding.session.end_reason or 'ended'}"
    if not getattr(binding.session.user, "is_active", True):
        return "user_inactive"
    return None


def access_token(binding: Any) -> str:
    """A fresh access token of the sign-in behind ``binding`` (see the module docstring)."""
    if binding.revoked_at is not None:
        raise OIDCSessionEnded(binding.revoke_reason or "binding_revoked")
    problem = standing_problem(binding)
    if problem is not None:
        # an expired embed, an ended sign-in or a deactivated user: the binding goes with it
        handoff.revoke(binding, problem)
        raise OIDCSessionEnded(problem)
    try:
        return access_token_for(binding.session_id, min_ttl=MIN_TTL_SECONDS)
    except OIDCSessionEnded as exc:
        handoff.revoke(binding, f"ended:{exc.reason}")
        raise


__all__ = ["MIN_TTL_SECONDS", "access_token", "standing_problem"]
