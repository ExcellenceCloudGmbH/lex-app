"""Per-call Keycloak requests made on behalf of one user: UMA permissions and userinfo.

Why this module exists
----------------------
python-keycloak's ``KeycloakOpenID.uma_permissions()`` and ``.userinfo()`` put the
caller's bearer token into the headers of a connection object
(``connection.add_param_headers("Authorization", ...)``) and only then send the
request. lex-app keeps one ``KeycloakManager`` -- and so one such connection -- per
process, and Django serves requests from several threads at once. Two users
asking at the same moment race on that one header: a request can leave with the
*other* user's token and come back with the other user's permissions or profile.

The functions here take the token as an argument and put it on that single
request only. Nothing about a user is stored between calls, so concurrent
callers cannot see each other's data. Each thread keeps its own
``requests.Session`` purely for connection reuse; it carries no auth state.

Errors are typed so callers can tell "Keycloak said no" from "Keycloak could not
be asked":

* :class:`KeycloakDenied` -- Keycloak answered and grants nothing (UMA
  ``access_denied``). This is a real, empty answer.
* :class:`KeycloakUnauthorized` -- the token was not accepted (expired,
  revoked, session ended, wrong audience).
* :class:`KeycloakUnavailable` -- no usable answer: network error, timeout,
  5xx, 429, an unexpected 4xx, a non-JSON body, or missing configuration.

No message raised or logged here contains a token.
"""

from __future__ import annotations

import logging
import os
import threading
from http.cookiejar import DefaultCookiePolicy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Optional, Union

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

#: ``(connect, read)`` seconds. A stuck Keycloak must not hold a request thread
#: for minutes; three seconds to connect, ten to answer.
DEFAULT_TIMEOUT: tuple[float, float] = (3.05, 10.0)

UMA_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:uma-ticket"

#: OAuth error codes that mean "this bearer token is not accepted".
_UNAUTHORIZED_ERRORS = frozenset({"invalid_grant", "invalid_token"})

Verify = Union[bool, str, None]
Timeout = Union[float, tuple[float, float]]

_local = threading.local()


class KeycloakCallError(Exception):
    """Keycloak did not return the requested data for this user.

    ``status`` is the HTTP status when Keycloak answered (``None`` when it could
    not be reached); ``error`` is the OAuth ``error`` code when the body had one.
    """

    def __init__(self, message: str, *, status: Optional[int] = None, error: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.error = error


class KeycloakDenied(KeycloakCallError):
    """Keycloak answered and the user holds no matching permission."""


class KeycloakUnauthorized(KeycloakCallError):
    """Keycloak did not accept the user's bearer token."""


class KeycloakUnavailable(KeycloakCallError):
    """Keycloak could not be asked, or did not give a usable answer."""


@dataclass(frozen=True)
class KeycloakEndpoints:
    """Where the per-user calls go.

    ``client_id`` is the UMA ``audience``; ``userinfo_url`` alone is enough for
    userinfo, so ``client_id`` may be empty when only that is needed.
    """

    token_url: str
    userinfo_url: str
    client_id: str = ""

    @classmethod
    def from_environment(cls) -> Optional["KeycloakEndpoints"]:
        """The endpoints ``KeycloakManager`` uses, from the same variables.

        ``KEYCLOAK_URL`` + ``KEYCLOAK_REALM`` (or ``KEYCLOAK_REALM_NAME``) locate
        the realm; ``OIDC_RP_CLIENT_ID`` is the audience of the UMA request.
        Returns ``None`` when the realm cannot be located.
        """
        server = (os.getenv("KEYCLOAK_URL") or "").strip().rstrip("/")
        realm = (os.getenv("KEYCLOAK_REALM") or os.getenv("KEYCLOAK_REALM_NAME") or "").strip()
        if not server or not realm:
            return None
        base = f"{server}/realms/{realm}/protocol/openid-connect"
        return cls(
            token_url=f"{base}/token",
            userinfo_url=f"{base}/userinfo",
            client_id=(os.getenv("OIDC_RP_CLIENT_ID") or "").strip(),
        )


def default_verify() -> Verify:
    """TLS verification for Keycloak calls: the ``KEYCLOAK_VERIFY_SSL`` setting.

    Same source and default as ``KeycloakManager`` so moving a call here does not
    change how its certificate is checked.
    """
    return getattr(settings, "KEYCLOAK_VERIFY_SSL", False)


def _session() -> requests.Session:
    """This thread's session. Used only for connection reuse; holds no auth state and no cookies."""
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        # Refuse every cookie: a value Keycloak or an ingress sets while answering one
        # user's call (a sticky-routing cookie, say) must never ride along with the next
        # user's call on this thread.
        session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        _local.session = session
    return session


def _permission_params(permissions: Any) -> list[str]:
    """``permission`` form values, as python-keycloak's ``build_permission_param`` builds them.

    Accepts ``None``/empty (every permission), ``"resource#scope"`` strings, an
    iterable of them, or ``{resource: scope | [scopes]}``.
    """
    if not permissions:
        return []
    if isinstance(permissions, str):
        return [permissions]
    if isinstance(permissions, Mapping):
        params: list[str] = []
        for resource, scopes in permissions.items():
            if scopes is None or scopes == "":
                params.append(str(resource))
            elif isinstance(scopes, str):
                params.append(f"{resource}#{scopes}")
            else:
                params.extend(f"{resource}#{scope}" for scope in scopes)
        return params
    if isinstance(permissions, Iterable):
        return [str(item) for item in permissions]
    raise TypeError(f"Unsupported permissions value of type {type(permissions).__name__}")


def _json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _oauth_error(body: Any) -> Optional[str]:
    if isinstance(body, Mapping):
        error = body.get("error")
        return error if isinstance(error, str) else None
    return None


def fetch_uma_permissions(
    access_token: str,
    *,
    permissions: Any = None,
    endpoints: Optional[KeycloakEndpoints] = None,
    timeout: Timeout = DEFAULT_TIMEOUT,
    verify: Verify = None,
) -> list[dict]:
    """The user's UMA permissions (``response_mode=permissions``) for ``access_token``.

    Raises :class:`KeycloakDenied` when Keycloak grants nothing,
    :class:`KeycloakUnauthorized` when it rejects the token, and
    :class:`KeycloakUnavailable` otherwise.
    """
    if not access_token:
        raise KeycloakUnauthorized("No access token to ask Keycloak with.")
    endpoints = endpoints or KeycloakEndpoints.from_environment()
    if endpoints is None:
        raise KeycloakUnavailable("Keycloak is not configured (KEYCLOAK_URL and KEYCLOAK_REALM).")
    if not endpoints.client_id:
        raise KeycloakUnavailable("Keycloak UMA needs OIDC_RP_CLIENT_ID as the audience.")

    data: list[tuple[str, str]] = [
        ("grant_type", UMA_GRANT_TYPE),
        ("response_mode", "permissions"),
        ("audience", endpoints.client_id),
    ]
    data.extend(("permission", value) for value in _permission_params(permissions))

    try:
        response = _session().post(
            endpoints.token_url,
            data=data,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=timeout,
            verify=default_verify() if verify is None else verify,
        )
    except requests.RequestException as exc:
        raise KeycloakUnavailable(f"UMA permissions request failed: {exc.__class__.__name__}") from exc

    body = _json(response)
    status = response.status_code
    error = _oauth_error(body)
    if status == 200:
        if isinstance(body, list):
            return body
        raise KeycloakUnavailable("UMA permissions response is not a list.", status=status)
    if status == 403 and error == "access_denied":
        raise KeycloakDenied("Keycloak grants no permission for this token.", status=status, error=error)
    if status == 401 or error in _UNAUTHORIZED_ERRORS:
        raise KeycloakUnauthorized("Keycloak rejected the access token.", status=status, error=error)
    raise KeycloakUnavailable(
        f"Keycloak answered the UMA request with HTTP {status}.", status=status, error=error
    )


def fetch_userinfo(
    access_token: str,
    *,
    endpoints: Optional[KeycloakEndpoints] = None,
    timeout: Timeout = DEFAULT_TIMEOUT,
    verify: Verify = None,
) -> dict:
    """The OIDC userinfo document for ``access_token``.

    Raises :class:`KeycloakUnauthorized` on 401/403 (token not accepted, or not
    allowed to read userinfo) and :class:`KeycloakUnavailable` otherwise.
    """
    if not access_token:
        raise KeycloakUnauthorized("No access token to ask Keycloak with.")
    endpoints = endpoints or KeycloakEndpoints.from_environment()
    if endpoints is None:
        raise KeycloakUnavailable("Keycloak is not configured (KEYCLOAK_URL and KEYCLOAK_REALM).")

    try:
        response = _session().get(
            endpoints.userinfo_url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=timeout,
            verify=default_verify() if verify is None else verify,
        )
    except requests.RequestException as exc:
        raise KeycloakUnavailable(f"Userinfo request failed: {exc.__class__.__name__}") from exc

    body = _json(response)
    status = response.status_code
    if status == 200:
        if isinstance(body, dict):
            return body
        raise KeycloakUnavailable("Userinfo response is not a JSON object.", status=status)
    if status in (401, 403):
        raise KeycloakUnauthorized(
            f"Keycloak rejected the access token for userinfo (HTTP {status}).",
            status=status,
            error=_oauth_error(body),
        )
    raise KeycloakUnavailable(
        f"Keycloak answered the userinfo request with HTTP {status}.", status=status, error=_oauth_error(body)
    )


__all__ = [
    "DEFAULT_TIMEOUT",
    "KeycloakCallError",
    "KeycloakDenied",
    "KeycloakEndpoints",
    "KeycloakUnauthorized",
    "KeycloakUnavailable",
    "default_verify",
    "fetch_uma_permissions",
    "fetch_userinfo",
]
