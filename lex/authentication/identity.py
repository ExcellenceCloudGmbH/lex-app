"""Who the request's user is, answered from the validated sign-in -- never by asking Keycloak.

``oauth2_authcodeflow`` validated the ID token at sign-in (or the access token of a bearer
request) and keeps the profile claims on ``request.oidc``. Everything lex used to fetch from
Keycloak's userinfo endpoint on every page load is there already:

* :func:`profile_claims` -- the userinfo-shaped profile (``sub``, ``email``, ``name``, ...);
* :func:`client_roles` -- the user's roles on this instance's Keycloak client, from the access
  token's ``resource_access[<client id>].roles`` (falling back to a ``client_roles`` claim);
* :func:`user_payload` -- what ``api/user/`` and ``api/auth/session`` answer.

A signed-in Django user without an OIDC sign-in (``createsuperuser`` + admin login, local runs
without Keycloak) is described from the user record, with no roles.
"""
from __future__ import annotations

from typing import Any, Dict, List

#: Token bookkeeping that is not part of a user's profile (and never belongs in an answer).
PROTOCOL_CLAIMS = frozenset({
    "iss", "aud", "azp", "sid", "auth_time", "acr", "amr", "exp", "iat", "nbf", "nonce", "typ",
    "at_hash", "c_hash", "s_hash", "jti", "session_state", "scope", "resource_access",
    "realm_access", "allowed-origins", "client_role", "realm_role", "client_roles",
})


def oidc_context(request: Any):
    """The request's ``OIDCContext`` (``None`` for anonymous, API-key and non-OIDC logins)."""
    django_request = getattr(request, "_request", request)
    return getattr(django_request, "oidc", None)


def client_roles(request: Any) -> List[str]:
    """Roles of the signed-in user on this instance's Keycloak client ([] without a sign-in)."""
    context = oidc_context(request)
    if context is None:
        return []
    return list(context.client_roles())


def profile_claims(context: Any) -> Dict[str, Any]:
    """The profile claims of a sign-in, shaped like Keycloak's userinfo answer, plus ``client_roles``."""
    claims = {key: value for key, value in dict(context.claims or {}).items() if key not in PROTOCOL_CLAIMS}
    claims["client_roles"] = list(context.client_roles())
    return claims


def _from_user_record(user: Any) -> Dict[str, Any]:
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    username = user.get_username() if hasattr(user, "get_username") else getattr(user, "username", "")
    return {
        "sub": str(getattr(user, "pk", "") or ""),
        "email": getattr(user, "email", "") or "",
        "preferred_username": username or "",
        "given_name": first,
        "family_name": last,
        "name": " ".join(part for part in (first, last) if part) or username or "",
        "client_roles": [],
    }


def user_payload(request: Any) -> Dict[str, Any]:
    """What lex answers about the signed-in user (``api/user/``, ``api/auth/session``)."""
    context = oidc_context(request)
    if context is not None:
        return profile_claims(context)
    django_request = getattr(request, "_request", request)
    return _from_user_record(getattr(django_request, "user", None))


__all__ = ["PROTOCOL_CLAIMS", "client_roles", "oidc_context", "profile_claims", "user_payload"]
