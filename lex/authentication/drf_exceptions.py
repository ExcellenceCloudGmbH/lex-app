"""The HTTP auth contract v2 for lex's REST views (``REST_FRAMEWORK['EXCEPTION_HANDLER']``).

What a client sees when authentication or authorization is the problem
(``lex/contracts/auth/v2/README.md``):

* **401** ``{"error": "not_authenticated" | "session_expired", "login_url": "/oidc/authenticate"}``
  with ``WWW-Authenticate`` -- the session is gone; the client signs in again (silently if it
  can). DRF's own "not authenticated" answers also carry ``code: not_authenticated``.
* **403** ``{"detail": ..., "code": "permission_denied" | "csrf_failed"}`` -- the user may not do
  this, or the CSRF token was missing; the user stays signed in.
* **503** ``{"error": "auth_unavailable", "retry_after": n}`` with ``Retry-After`` -- Keycloak (the
  token endpoint or UMA) could not answer. Never a denial and never a sign-out: the client
  retries.

Every one of them carries ``Cache-Control: no-store``.

The handler builds on ``oauth2_authcodeflow.contrib.drf.oidc_exception_handler`` (which adds the
codes and maps the library's ``OIDCSessionEnded`` / ``OIDCAuthUnavailable``) and adds lex's own
two outcomes of a permission lookup: :class:`AuthUnavailable` and :class:`SessionRejected`.
Code that turns exceptions into denials must let both through -- see
:func:`lex.api.security.is_auth_outage`.
"""
from __future__ import annotations

from typing import Any, Optional

from django.http import HttpRequest, HttpResponse
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from oauth2_authcodeflow import responses as oidc_responses
from oauth2_authcodeflow.conf import settings as oidc_settings
from oauth2_authcodeflow.contrib.drf import oidc_exception_handler
from oauth2_authcodeflow.exceptions import OIDCAuthUnavailable

#: Seconds a client is told to wait when nothing more specific is known.
DEFAULT_RETRY_AFTER = 5


class AuthUnavailable(APIException):
    """An auth backend could not answer right now (Keycloak UMA, a breaker that is open).

    Answered 503 ``auth_unavailable`` with ``Retry-After``. It is never a "no": code that
    denies on exceptions (``LEX_SEC_FAIL_CLOSED``) re-raises it.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Authorization is temporarily unavailable; please retry."
    default_code = "auth_unavailable"

    def __init__(self, retry_after: Optional[int] = None, *, category: str = "transient") -> None:
        super().__init__()
        self.retry_after = max(1, int(retry_after or DEFAULT_RETRY_AFTER))
        self.category = category


class SessionRejected(APIException):
    """Keycloak no longer accepts the access token of this sign-in.

    Answered 401 ``session_expired``: the client signs in again. The Django session is left
    alone -- a fresh sign-in replaces it, and a misconfigured Keycloak must not sign everybody
    out.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "The sign-in is no longer accepted; please sign in again."
    default_code = "session_expired"


def _no_store(response: Any) -> Any:
    response["Cache-Control"] = "no-store"
    return response


def _session_realm() -> str:
    return f'Session realm="{oidc_settings.OIDC_RP_CLIENT_ID}"'


def auth_unavailable_response(retry_after: Optional[int] = None) -> Response:
    """The contract's 503 for a REST view."""
    retry = max(1, int(retry_after or DEFAULT_RETRY_AFTER))
    response = Response({"error": "auth_unavailable", "retry_after": retry}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    response["Retry-After"] = str(retry)
    return _no_store(response)


def session_expired_response() -> Response:
    """The contract's 401 ``session_expired`` for a REST view."""
    response = Response(
        {"error": "session_expired", "login_url": oidc_responses.login_path()},
        status=status.HTTP_401_UNAUTHORIZED,
    )
    response["WWW-Authenticate"] = _session_realm()
    return _no_store(response)


def django_response(request: HttpRequest, exc: Exception) -> Optional[HttpResponse]:
    """The contract's answer to :class:`AuthUnavailable` / :class:`SessionRejected` outside DRF.

    Plain Django views get the library's request-kind aware answers (JSON for API requests,
    HTML for page and frame navigations). ``None`` for any other exception.
    """
    exc = _translate(exc)
    if isinstance(exc, AuthUnavailable):
        return oidc_responses.auth_unavailable(request, OIDCAuthUnavailable(exc.category, exc.retry_after))
    if isinstance(exc, SessionRejected):
        return oidc_responses.unauthenticated(request, "session_expired")
    return None


def _translate(exc: Exception) -> Exception:
    """lex's Keycloak client errors, raised straight out of a view, mean the same as ours."""
    from lex.api.keycloak.uma_client import KeycloakUnauthorized, KeycloakUnavailable

    if isinstance(exc, KeycloakUnavailable):
        return AuthUnavailable(category="uma")
    if isinstance(exc, KeycloakUnauthorized):
        return SessionRejected()
    return exc


def refusal_response(request: Any, message: str) -> Response:
    """A permission refusal decided inside a view: 401 for an anonymous caller, else 403.

    Keeps the documented ``{"message": ...}`` body and adds the contract's keys, so a client
    can tell a refusal (403 ``permission_denied``: stay signed in) from a lost session (401).
    """
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        response = Response(
            {
                "message": message,
                "detail": message,
                "code": "not_authenticated",
                "error": "not_authenticated",
                "login_url": oidc_responses.login_path(),
            },
            status=status.HTTP_401_UNAUTHORIZED,
        )
        response["WWW-Authenticate"] = _session_realm()
        return _no_store(response)
    return _no_store(
        Response({"message": message, "detail": message, "code": "permission_denied"}, status=status.HTTP_403_FORBIDDEN)
    )


def exception_handler(exc: Exception, context: dict) -> Optional[Response]:
    """``REST_FRAMEWORK['EXCEPTION_HANDLER']``: DRF's handler plus the auth contract."""
    exc = _translate(exc)
    if isinstance(exc, AuthUnavailable):
        return auth_unavailable_response(exc.retry_after)
    if isinstance(exc, SessionRejected):
        return session_expired_response()
    return oidc_exception_handler(exc, context)


__all__ = [
    "AuthUnavailable",
    "DEFAULT_RETRY_AFTER",
    "SessionRejected",
    "auth_unavailable_response",
    "django_response",
    "exception_handler",
    "refusal_response",
    "session_expired_response",
]
