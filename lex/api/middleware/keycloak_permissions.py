"""The caller's Keycloak permissions, profile and roles, attached to every request -- lazily.

``KeycloakPermissionsMiddleware`` gives each request three attributes that the permission
layer (``UserContext``, filter backends, serializers, ``api/user_permissions/``) reads:

* ``request.user_permissions`` -- the user's Keycloak UMA permissions
  (``[{"rsname", "resource_set_id", "scopes"}, ...]``);
* ``request.userinfo`` -- the profile of the sign-in (``sub``, ``email``, ``name``, ...);
* ``request.client_roles`` -- the user's roles on this instance's Keycloak client.

They are lazy (``SimpleLazyObject``): nothing is computed until something reads them, and a
request that never checks a permission never reaches Keycloak. The profile and the roles come
from the validated sign-in (``request.oidc``, the access token's ``resource_access``) and never
cost a Keycloak call. The permissions are one UMA request through the stateless
``lex.api.keycloak.uma_client`` (the token travels on that request only), and:

* **cached** per access-token digest for at most 60 s and never past the token's expiry -- a
  token belongs to one user, so the cache can never answer for anyone else;
* **single-flight** per digest: concurrent requests with one token make one call and share its
  outcome;
* behind **their own circuit breaker** (``keycloak_uma``, configured by ``OIDC_CIRCUIT_BREAKER``):
  while Keycloak keeps failing, nobody waits on it.

What Keycloak's answer means:

=================================  ==========================================================
Keycloak answers                   the request
=================================  ==========================================================
the permissions                    ``user_permissions`` is that list
403 ``access_denied``              ``[]`` -- a real "nothing granted"; views answer 403/404
401 (token not accepted)           401 ``session_expired`` (``SessionRejected``): sign in again
no usable answer (5xx, timeout,    503 ``auth_unavailable`` (``AuthUnavailable``). Never ``[]``:
breaker open)                      an outage must not read as "no permission"
=================================  ==========================================================

For unsafe methods (POST, PUT, PATCH, DELETE) of a signed-in user the permissions are fetched
here, before the view, so a 503 always comes before anything was written -- the contract that
lets clients retry those requests. Code that turns exceptions into denials must let these
outcomes through (``lex.api.security.is_auth_outage``).

Requests without an OIDC sign-in (anonymous, API keys, non-OIDC logins) get ``[]``, ``{}`` and
``[]``, as before.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from django.utils.functional import SimpleLazyObject

from oauth2_authcodeflow import sessions as oidc_sessions
from oauth2_authcodeflow.breaker import get_breaker
from oauth2_authcodeflow.exceptions import OIDCAuthUnavailable, OIDCSessionEnded
from oauth2_authcodeflow.jwt_validation import unverified_claims
from oauth2_authcodeflow.tokens import get_access_token

from lex.api.keycloak import uma_client
from lex.authentication import identity
from lex.authentication.drf_exceptions import AuthUnavailable, SessionRejected, django_response

logger = logging.getLogger(__name__)

#: A UMA answer is reused for at most this many seconds (and never past the token's expiry).
UMA_CACHE_MAX_TTL = 60
#: How long a request waits for the one already asking Keycloak with the same token.
UMA_WAIT_SECONDS = 15
#: Upper bound on cached answers per process (expired ones go first).
UMA_CACHE_MAX_ENTRIES = 4096
#: Name of the UMA circuit breaker (the library's breaker registry, separate from its own).
UMA_BREAKER = "keycloak_uma"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


# ---------------------------------------------------------------------------
# Cache and single flight, keyed by the SHA-256 of the access token
# ---------------------------------------------------------------------------
class _UmaCache:
    def __init__(self, max_entries: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_entries = max_entries
        self.clock = clock
        self._data: dict[str, tuple[float, list]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[list]:
        now = self.clock()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry[0] <= now:
                del self._data[key]
                return None
            return copy.deepcopy(entry[1])

    def put(self, key: str, value: list, ttl: float) -> None:
        now = self.clock()
        with self._lock:
            if key not in self._data and len(self._data) >= self.max_entries:
                for stale in [k for k, (expires, _) in self._data.items() if expires <= now]:
                    del self._data[stale]
                while len(self._data) >= self.max_entries:
                    del self._data[next(iter(self._data))]
            self._data[key] = (now + ttl, copy.deepcopy(value))

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class _Flight:
    __slots__ = ("done", "result", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Optional[list] = None
        self.error: Optional[BaseException] = None


_cache = _UmaCache(UMA_CACHE_MAX_ENTRIES)
_flights: dict[str, _Flight] = {}
_flights_lock = threading.Lock()


def reset_uma_cache() -> None:
    """Forget every cached answer (tests; a Keycloak policy change that must apply at once)."""
    _cache.clear()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _seconds_left(token: str, expires_at: Optional[datetime]) -> float:
    if expires_at is None:
        exp = unverified_claims(token).get("exp")
        if not isinstance(exp, (int, float)):
            return 0.0
        return float(exp) - time.time()
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return (expires_at - datetime.now(timezone.utc)).total_seconds()


def _again(error: BaseException) -> BaseException:
    """A fresh exception for a request that shared a failed flight."""
    if isinstance(error, AuthUnavailable):
        return AuthUnavailable(error.retry_after, category=error.category)
    if isinstance(error, SessionRejected):
        return SessionRejected()
    return error


def _single_flight(key: str, fetch: Callable[[], list]) -> list:
    with _flights_lock:
        flight = _flights.get(key)
        leader = flight is None
        if leader:
            flight = _Flight()
            _flights[key] = flight
    if not leader:
        if not flight.done.wait(UMA_WAIT_SECONDS):
            raise AuthUnavailable(category="uma_wait")
        if flight.error is not None:
            raise _again(flight.error)
        return copy.deepcopy(flight.result or [])
    try:
        flight.result = fetch()
        return copy.deepcopy(flight.result)
    except BaseException as error:
        flight.error = error
        raise
    finally:
        with _flights_lock:
            _flights.pop(key, None)
        flight.done.set()


def _ask_keycloak(token: str, endpoints: uma_client.KeycloakEndpoints) -> list:
    breaker = get_breaker(UMA_BREAKER)
    if not breaker.try_acquire():
        raise AuthUnavailable(breaker.retry_after(), category="uma_breaker_open")
    try:
        permissions = uma_client.fetch_uma_permissions(token, endpoints=endpoints)
    except uma_client.KeycloakDenied:
        breaker.success()
        return []
    except uma_client.KeycloakUnauthorized:
        breaker.success()  # Keycloak answered: it is up, it refused this token
        logger.info("Keycloak did not accept an access token for UMA; answering 401 session_expired")
        raise SessionRejected()
    except uma_client.KeycloakUnavailable as exc:
        category = getattr(exc, "category", "transient")
        breaker.failure("config" if category == "config" else "transient")
        logger.warning("Keycloak UMA permissions unavailable (%s): %s", category, exc)
        raise AuthUnavailable(breaker.retry_after(), category="uma") from None
    except BaseException:
        breaker.release_probe()  # not an answer from Keycloak: never leave a half-open probe pending
        raise
    breaker.success()
    return permissions


def uma_permissions(token: str, *, expires_at: Optional[datetime] = None) -> list:
    """The UMA permissions of ``token``: cached, single-flight and behind the UMA breaker.

    Raises :class:`AuthUnavailable` (503) or :class:`SessionRejected` (401); returns ``[]``
    when Keycloak grants nothing -- or when no Keycloak authorization server is configured at
    all (no ``KEYCLOAK_URL``/``KEYCLOAK_REALM``/``OIDC_RP_CLIENT_ID``): there is nobody to ask.
    """
    endpoints = uma_client.KeycloakEndpoints.from_environment()
    if endpoints is None or not endpoints.client_id:
        return []
    key = _digest(token)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    def fetch() -> list:
        again = _cache.get(key)  # a flight that finished while this one queued
        if again is not None:
            return again
        permissions = _ask_keycloak(token, endpoints)
        ttl = min(float(UMA_CACHE_MAX_TTL), _seconds_left(token, expires_at))
        if ttl > 0:
            _cache.put(key, permissions, ttl)
        return permissions

    return _single_flight(key, fetch)


# ---------------------------------------------------------------------------
# The middleware
# ---------------------------------------------------------------------------
#: What a permission lookup can raise instead of answering (all mapped to the contract).
LOOKUP_OUTCOMES = (AuthUnavailable, SessionRejected, OIDCAuthUnavailable, OIDCSessionEnded)


class _RequestIdentity:
    """Computes the three attributes of one request, on first use."""

    def __init__(self, request: Any) -> None:
        self.request = request

    @property
    def context(self):
        return getattr(self.request, "oidc", None)

    def permissions(self) -> list:
        context = self.context
        if context is None:
            return []
        token = get_access_token(self.request)  # the library refreshes it if it must
        if not token:
            return []
        return uma_permissions(token, expires_at=context.access_expires_at)

    def userinfo(self) -> dict:
        context = self.context
        return identity.profile_claims(context) if context is not None else {}

    def client_roles(self) -> list:
        context = self.context
        return list(context.client_roles()) if context is not None else []


class KeycloakPermissionsMiddleware:
    """Attach lazy ``user_permissions`` / ``userinfo`` / ``client_roles`` to every request.

    Place it after ``oauth2_authcodeflow``'s ``BearerAuthMiddleware`` and
    ``OIDCSessionMiddleware`` (which set ``request.oidc``) and after the login middleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        state = _RequestIdentity(request)
        request.user_permissions = SimpleLazyObject(state.permissions)
        request.userinfo = SimpleLazyObject(state.userinfo)
        request.client_roles = SimpleLazyObject(state.client_roles)
        if self._decide_before_view(request, state):
            # Writes are decided before the view: a 503 never follows a half-done write.
            try:
                bool(request.user_permissions)
            except LOOKUP_OUTCOMES as exc:
                return self._contract_answer(request, exc)
        return self.get_response(request)

    @staticmethod
    def _decide_before_view(request, state) -> bool:
        """A signed-in write to anything but the sign-in machinery itself.

        The OIDC endpoints (logout, back-channel logout, ...) and the paths the session
        middleware leaves alone (health, admin) never read UMA permissions; signing out
        must work while Keycloak is down.
        """
        if request.method in SAFE_METHODS or state.context is None:
            return False
        from oauth2_authcodeflow.conf import settings as oidc_settings
        from oauth2_authcodeflow.middleware import oidc_endpoint_patterns

        path = request.path_info
        skipped = oidc_endpoint_patterns() + tuple(str(p) for p in oidc_settings.OIDC_SESSION_EXEMPT_URL_PATTERNS)
        return not any(re.search(pattern, path) for pattern in skipped)

    def process_exception(self, request, exception):
        """Plain Django views: map a lookup outcome raised while the view ran to the contract."""
        if isinstance(exception, LOOKUP_OUTCOMES):
            return self._contract_answer(request, exception)
        return None

    @staticmethod
    def _contract_answer(request, exc):
        from oauth2_authcodeflow import responses as oidc_responses

        if isinstance(exc, OIDCSessionEnded):
            oidc_sessions.logout_request(request, exc.reason)
            return oidc_responses.unauthenticated(request, "session_expired")
        if isinstance(exc, OIDCAuthUnavailable):
            return oidc_responses.auth_unavailable(request, exc)
        return django_response(request, exc)


__all__ = [
    "KeycloakPermissionsMiddleware",
    "LOOKUP_OUTCOMES",
    "UMA_BREAKER",
    "UMA_CACHE_MAX_TTL",
    "reset_uma_cache",
    "uma_permissions",
]
