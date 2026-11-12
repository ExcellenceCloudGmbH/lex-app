import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from inspect import signature
from typing import Any, Dict, Optional, Tuple, List
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit

import httpx
import jwt  # PyJWT
from authlib.integrations.starlette_client import OAuth
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

# Standard library only (no Django): read before anything else, and all that legacy mode needs.
from lex.authentication.dashboards import cookies as dash_cookies
from lex.authentication.dashboards import modes as dash_modes
from lex.authentication.dashboards import sites as dash_sites

# The hostnames this instance is served on, per role. Deliberately a top-level
# `lex` module: it imports no Django and triggers no package side effects, so
# it is safe in this standalone proxy, which runs in its own uvicorn thread.
from lex import hosts as lex_hosts

# Respect X-Forwarded-* when behind ingress/LB
try:
    from starlette.middleware.proxy_headers import ProxyHeadersMiddleware  # type: ignore
except Exception:  # pragma: no cover
    try:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  # type: ignore
    except Exception:  # pragma: no cover
        ProxyHeadersMiddleware = None  # type: ignore

# Optional Redis token store for production replicas
try:
    import redis.asyncio as redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None  # type: ignore

# Must precede the `websockets` import below -- the library reads its
# header-line limit from the environment as `websockets.http11` is imported, and
# the access token this proxy forwards upstream is one header line. `lex
# streamlit` already applies this before it starts the server; this covers a
# deployment that runs `proxy:app` under its own uvicorn. See
# lex/ws_header_limits.py.
from lex import ws_header_limits  # noqa: F401  (imported for its side effect)

try:
    from websockets.asyncio.client import connect as ws_connect  # websockets >= 12
except Exception:
    try:
        from websockets.client import connect as ws_connect  # websockets 10/11
    except Exception:
        from websockets import connect as ws_connect  # type: ignore

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Config helpers
# -----------------------------------------------------------------------------
def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def _now() -> int:
    return int(time.time())


def _decode_exp_no_verify(token: Any) -> int:
    if not isinstance(token, str) or not token:
        return 0
    try:
        payload = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
        return int(payload.get("exp") or 0)
    except Exception:
        return 0


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
PUBLIC_URL = (os.getenv("STREAMLIT_URL") or os.getenv("BASE_URL") or "").rstrip("/")
PUBLIC_URL_OBJ = httpx.URL(PUBLIC_URL) if PUBLIC_URL else None
PUBLIC_IS_HTTPS = (PUBLIC_URL_OBJ.scheme == "https") if PUBLIC_URL_OBJ else False

UPSTREAM = (os.environ.get("UPSTREAM") or os.environ.get("STREAMLIT_UPSTREAM") or "http://localhost:8080").rstrip("/")

#: The published fallback in ``lex/lex_app/settings.py``. Deriving a
#: cookie-signing key from *this* would give every lex-app instance in the world
#: the same one, so it is explicitly excluded -- worse than a random value, not
#: better.
_PUBLISHED_DJANGO_SECRET_DEFAULT = "pjlulvaa77lteno-_y6!oxb%63xqiaw4%n%1or&77a!x9@nkd+"


def _resolve_session_secret() -> Tuple[str, str]:
    """The key used to sign session cookies, and where it came from.

    Order matters, and each step exists for a reason:

    1. ``SESSION_SECRET`` (or its aliases) -- an explicit choice always wins.
    2. Derived from ``DJANGO_SECRET_KEY``. Every deployed instance already has
       one: terraform generates it with ``random_password`` and keeps it in
       state, so unlike a per-process value it is stable across restarts and
       identical on every replica -- which is exactly the property session
       cookies need. Derived rather than used directly, so that a stolen
       session cookie key does not hand over Django's secret and vice versa.
       The published settings.py default is excluded: sharing *that* would give
       every instance the same signing key.
    3. A random per-process value, with a warning. Sessions then do not survive
       a restart -- degraded, but a Streamlit dashboard that starts and loses
       sessions on redeploy beats one that refuses to start at all.

    An earlier version of this raised on step 3 when the public URL was https.
    That was wrong: it bricked instances that had been running fine, and the
    single-process ``lex streamlit`` case is genuinely only *degraded* by a
    per-process key, not broken. The durable fix was to stop needing a new
    variable, not to demand one.
    """
    explicit = (
        os.environ.get("SESSION_SECRET")
        or os.environ.get("SESSION_KEY")
        or os.environ.get("SESSION_SECRET_KEY")
    )
    if explicit:
        return explicit, "SESSION_SECRET"

    django_secret = (os.getenv("DJANGO_SECRET_KEY") or "").strip()
    if django_secret and django_secret != _PUBLISHED_DJANGO_SECRET_DEFAULT:
        derived = hmac.new(
            django_secret.encode("utf-8"),
            b"lex-proxy-session-cookie-v1",
            hashlib.sha256,
        ).hexdigest()
        return derived, "DJANGO_SECRET_KEY"

    logger.warning(
        "Neither SESSION_SECRET nor a non-default DJANGO_SECRET_KEY is set, so session "
        "cookies will be signed with a random per-process value. Dashboard sessions will "
        "not survive a restart and cannot be shared across replicas. Set SESSION_SECRET "
        "to a fixed value, or DJANGO_SECRET_KEY (which every deployed instance has)."
    )
    return "CHANGE_ME_IN_PRODUCTION_" + secrets.token_urlsafe(32), "ephemeral"


SESSION_SECRET, SESSION_SECRET_SOURCE = _resolve_session_secret()

SESSION_HTTPS_ONLY = _env_bool("SESSION_HTTPS_ONLY", PUBLIC_IS_HTTPS)

# The Streamlit dashboard is loaded in an <iframe> owned by the React shell. In
# a cross-site frame the browser compares a cookie's site against the TOP-LEVEL
# site, so `SameSite=Lax` cookies are withheld from the frame's own requests to
# its own origin -- every asset, every XHR, the WebSocket handshake. The session
# and `st_access` cookies then simply are not there, which reaches the user as
# the 401 flood and, once the handshake credential expires, a dead dashboard.
#
# It happens to work today only because the shell and Streamlit sit under one
# registrable domain (`*.excellence-cloud.de`, and `localhost` in dev). Any
# customer on their own domain, or any split of the two hosts, breaks it. So
# default to `none` when we are on HTTPS and can therefore satisfy the `Secure`
# requirement that `SameSite=None` carries.
_SESSION_SAMESITE_DEFAULT = "none" if PUBLIC_IS_HTTPS else "lax"
SESSION_SAMESITE = (os.getenv("SESSION_SAMESITE") or _SESSION_SAMESITE_DEFAULT).lower()

if SESSION_SAMESITE not in {"lax", "strict", "none"}:
    raise RuntimeError(
        f"SESSION_SAMESITE must be one of lax|strict|none, got {SESSION_SAMESITE!r}"
    )

if SESSION_SAMESITE == "none" and not SESSION_HTTPS_ONLY:
    # Browsers reject `SameSite=None` without `Secure` outright, so this
    # combination does not degrade -- it silently drops every cookie the proxy
    # sets, and the dashboard never authenticates at all. Fail where it can be
    # read rather than in a browser console nobody is watching.
    raise RuntimeError(
        "SESSION_SAMESITE=none requires Secure cookies, but SESSION_HTTPS_ONLY is false. "
        "Browsers discard such cookies, so no session would ever be established. "
        "Serve the proxy over HTTPS (set STREAMLIT_URL/BASE_URL to an https:// URL, or "
        "SESSION_HTTPS_ONLY=true behind a TLS-terminating ingress), or set "
        "SESSION_SAMESITE=lax and keep the shell and Streamlit on one registrable domain."
    )

if SESSION_SAMESITE == "strict":
    logger.warning(
        "SESSION_SAMESITE=strict withholds the proxy's cookies from the embedded "
        "dashboard iframe entirely; use 'none' (HTTPS) or 'lax' (same registrable domain)."
    )

OIDC_VERIFY_SSL = _env_bool("OIDC_VERIFY_SSL", True)

# -----------------------------------------------------------------------------
# How dashboards sign in: LEX_STREAMLIT_AUTH = legacy | dual | handoff
# -----------------------------------------------------------------------------
# legacy  -- everything in this file outside the hand-off section, exactly as before 2.5:
#            `auth_token`, the proxy's own Keycloak sign-in, its token store, `/auth/adopt`.
# dual    -- the Django-anchored hand-off (`__Host-lex_dash`) first; every legacy credential is
#            still accepted, so frontends that predate the hand-off keep working.
# handoff -- only the hand-off. The proxy holds no Keycloak token of its own.
# See lex/authentication/dashboards/modes.py. An unknown value refuses to start, like the cookie
# rules above: silently picking a mode is worse than a readable error.
try:
    STREAMLIT_AUTH_MODE = dash_modes.parse_streamlit_auth(os.getenv(dash_modes.ENV_VAR))
except dash_modes.InvalidStreamlitAuthMode as _mode_error:
    raise RuntimeError(str(_mode_error)) from None
HANDOFF_ENABLED = dash_modes.handoff_enabled(STREAMLIT_AUTH_MODE)
LEGACY_ENABLED = dash_modes.legacy_enabled(STREAMLIT_AUTH_MODE)

TOKEN_REDIS_URL = os.getenv("TOKEN_REDIS_URL") or os.getenv("REDIS_URL") or ""
TOKEN_IDLE_TTL_SECONDS = int(os.getenv("TOKEN_IDLE_TTL_SECONDS", str(60 * 60 * 8)))  # 8 hours idle
TOKEN_EXPIRY_SKEW_SECONDS = int(os.getenv("TOKEN_EXPIRY_SKEW_SECONDS", "30"))

# NEW: issue cookies when auth_token is seen (recommended for embed)
# - Enables persistence for Streamlit follow-up requests that don't carry auth_token
PERSIST_JWT_AUTH_TO_SESSION = _env_bool("PERSIST_JWT_AUTH_TO_SESSION", True)

# Optional: set short-lived access token cookie too (helps WS when query param not repeated)
SET_ST_ACCESS_COOKIE = _env_bool("SET_ST_ACCESS_COOKIE", True)
ST_ACCESS_COOKIE_MAX_AGE = int(os.getenv("ST_ACCESS_COOKIE_MAX_AGE", "600"))  # 10 minutes

# -----------------------------------------------------------------------------
# Internal token-pull channel
# -----------------------------------------------------------------------------
# Everything this proxy injects into the upstream request -- access token,
# refresh token, identity -- reaches Streamlit only through the *WebSocket
# handshake*, and Streamlit snapshots those headers once per connection
# (``st.context.headers`` reads the session's client request). The socket then
# stays open for hours, so a running dashboard has no way to learn about a token
# this proxy refreshed five minutes ago: it is stuck with the credential it was
# handed at connect time, and dies when that one expires.
#
# ``/auth/token`` is the missing pull direction. The co-located Streamlit process
# presents the browser's own session cookie and gets back a freshly refreshed
# access token. Refreshing stays exclusively this proxy's job, so the refresh
# token is never spent from two places -- the rotation race that made local
# refresh unusable in the first place.
#
# The endpoint is guarded by a shared secret rather than the session cookie
# alone: the cookie is HttpOnly, but page script could still ``fetch`` it with
# ``credentials: 'include'`` and read the raw access token out of the response.
# The secret is process-local by default, which is exactly right when the proxy
# and Streamlit share a process (see ``lex streamlit``); set
# ``LEX_INTERNAL_AUTH_SECRET`` when they do not.
INTERNAL_AUTH_HEADER = "x-lex-internal-auth"
INTERNAL_AUTH_SECRET = os.getenv("LEX_INTERNAL_AUTH_SECRET") or ""
if not INTERNAL_AUTH_SECRET:
    INTERNAL_AUTH_SECRET = secrets.token_urlsafe(32)
    os.environ["LEX_INTERNAL_AUTH_SECRET"] = INTERNAL_AUTH_SECRET

# The proxy talks to an internal Streamlit upstream (typically localhost), so
# avoid sending those hops through system proxy settings unless explicitly
# requested.
UPSTREAM_USE_SYSTEM_PROXY = _env_bool(
    "UPSTREAM_USE_SYSTEM_PROXY",
    _env_bool("WS_UPSTREAM_USE_SYSTEM_PROXY", False),
)

JWT_ALG = os.getenv("JWT_ALG", "RS256")
JWT_VERIFY_ISSUER = _env_bool("JWT_VERIFY_ISSUER", False)
JWT_LEEWAY_SECONDS = int(os.getenv("JWT_LEEWAY_SECONDS", "2"))

KEYCLOAK_URL = (os.getenv("KEYCLOAK_URL") or "").rstrip("/")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM") or ""
EXPECTED_ISSUER = os.getenv("OIDC_ISSUER") or (
    f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}" if KEYCLOAK_URL and KEYCLOAK_REALM else ""
)

# -----------------------------------------------------------------------------
# OAuth client
# -----------------------------------------------------------------------------
oauth = OAuth()
oauth.register(
    name="oidc",
    client_id=os.getenv("OIDC_RP_CLIENT_ID"),
    client_secret=os.getenv("OIDC_RP_CLIENT_SECRET"),
    server_metadata_url=f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration",
    # PKCE (S256) on top of the client secret: a stolen authorization code is
    # useless without the verifier kept in this browser's session, and every
    # relying party on the shared Keycloak client sends PKCE, so the client can
    # later be set to require it.
    client_kwargs={"scope": "openid email profile", "verify": OIDC_VERIFY_SSL, "code_challenge_method": "S256"},
)

# -----------------------------------------------------------------------------
# Token store
# -----------------------------------------------------------------------------
def _compute_expires_at(token: Dict[str, Any]) -> int:
    if token.get("expires_at"):
        with suppress(Exception):
            return int(token["expires_at"])
    if token.get("expires_in"):
        with suppress(Exception):
            return _now() + int(token["expires_in"])
    # Some refresh responses omit expires_in; fall back to JWT exp when available.
    exp_from_access = _decode_exp_no_verify(token.get("access_token"))
    if exp_from_access:
        return exp_from_access
    exp_from_id = _decode_exp_no_verify(token.get("id_token"))
    if exp_from_id:
        return exp_from_id
    return 0


def _trim_token(token: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "access_token": token.get("access_token"),
        "id_token": token.get("id_token"),
        "refresh_token": token.get("refresh_token"),
        "expires_at": _compute_expires_at(token),
    }


class TokenStore:
    async def get(self, sid: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def set(self, sid: str, value: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def delete(self, sid: str) -> None:
        raise NotImplementedError

    async def touch(self, sid: str) -> None:
        raise NotImplementedError


class MemoryTokenStore(TokenStore):
    def __init__(self) -> None:
        self._tokens: Dict[str, Dict[str, Any]] = {}

    def _gc(self) -> None:
        cutoff = _now() - TOKEN_IDLE_TTL_SECONDS
        stale = [k for k, v in self._tokens.items() if int(v.get("last_seen", 0)) < cutoff]
        for k in stale:
            self._tokens.pop(k, None)

    async def get(self, sid: str) -> Optional[Dict[str, Any]]:
        self._gc()
        t = self._tokens.get(sid)
        if t:
            t["last_seen"] = _now()
        return t

    async def set(self, sid: str, value: Dict[str, Any]) -> None:
        self._gc()
        self._tokens[sid] = value

    async def delete(self, sid: str) -> None:
        self._tokens.pop(sid, None)

    async def touch(self, sid: str) -> None:
        t = self._tokens.get(sid)
        if t:
            t["last_seen"] = _now()


class RedisTokenStore(TokenStore):
    def __init__(self, redis_url: str) -> None:
        if redis is None:
            raise RuntimeError("redis.asyncio is not installed but TOKEN_REDIS_URL/REDIS_URL is set")
        self._r = redis.from_url(redis_url, decode_responses=True)
        self._prefix = os.getenv("TOKEN_REDIS_PREFIX", "st_proxy_tokens:")

    def _key(self, sid: str) -> str:
        return f"{self._prefix}{sid}"

    async def get(self, sid: str) -> Optional[Dict[str, Any]]:
        raw = await self._r.get(self._key(sid))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        await self.touch(sid)
        return data

    async def set(self, sid: str, value: Dict[str, Any]) -> None:
        await self._r.set(self._key(sid), json.dumps(value), ex=TOKEN_IDLE_TTL_SECONDS)

    async def delete(self, sid: str) -> None:
        await self._r.delete(self._key(sid))

    async def touch(self, sid: str) -> None:
        await self._r.expire(self._key(sid), TOKEN_IDLE_TTL_SECONDS)


#: Replica count, when the deployment knows it. >1 makes an in-memory token
#: store incorrect rather than merely fragile.
def _env_int(name: str, default: int) -> int:
    """An int from the environment, or ``default`` with a warning.

    Not a bare ``int()``: a typo in a deployment variable must not raise at
    import, because this module is imported inside the uvicorn worker thread --
    where the traceback kills only the proxy and leaves Streamlit serving, which
    reads as "the dashboard is broken" rather than "fix your env".
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default


PROXY_REPLICAS = _env_int("LEX_PROXY_REPLICAS", 1)


def _build_token_store() -> TokenStore:
    """Redis when configured, else in-memory -- which is only safe unreplicated.

    The store holds the refresh tokens that keep dashboards alive. In-memory it
    is process-local, so a restart drops every one, and with more than one
    replica a request routed elsewhere finds no ``sid`` and 401s. Both surface
    to the user as a session that expired for no reason.

    ``LEX_STREAMLIT_AUTH=handoff`` needs no store at all: dashboards rest on
    Django sign-ins (``DashboardBinding`` rows in the shared database), so any
    replica serves any dashboard and nothing here needs Redis.
    """
    if not LEGACY_ENABLED:
        return MemoryTokenStore()
    if TOKEN_REDIS_URL:
        try:
            return RedisTokenStore(TOKEN_REDIS_URL)
        except Exception as e:
            if PROXY_REPLICAS > 1:
                raise RuntimeError(
                    f"Redis token store could not be initialised ({e}) and "
                    f"LEX_PROXY_REPLICAS={PROXY_REPLICAS}. An in-memory store is "
                    "process-local, so sessions established on one replica would 401 "
                    "on every other. Fix TOKEN_REDIS_URL/REDIS_URL, or run one replica."
                ) from e
            logger.warning(
                "Redis token store disabled: %s. Falling back to in-memory: sessions "
                "will not survive a restart.", e
            )
    elif PROXY_REPLICAS > 1:
        raise RuntimeError(
            f"LEX_PROXY_REPLICAS={PROXY_REPLICAS} but no TOKEN_REDIS_URL/REDIS_URL is "
            "set. The token store would be process-local, so a request routed to "
            "another replica would find no session and return 401. Set a shared Redis "
            "URL, or run one replica with session affinity."
        )
    else:
        logger.warning(
            "No TOKEN_REDIS_URL/REDIS_URL set; using an in-memory token store. "
            "Sessions will not survive a proxy restart and cannot be replicated."
        )
    return MemoryTokenStore()


TOKEN_STORE: TokenStore = _build_token_store()


async def _put_tokens(sid: str, email: str, token: Dict[str, Any]) -> None:
    payload = {**_trim_token(token), "email": email, "last_seen": _now()}
    await TOKEN_STORE.set(sid, payload)


async def _get_tokens(sid: str) -> Optional[Dict[str, Any]]:
    return await TOKEN_STORE.get(sid)


async def _drop_tokens(sid: str) -> None:
    await TOKEN_STORE.delete(sid)


# -----------------------------------------------------------------------------
# OIDC endpoints + refresh
# -----------------------------------------------------------------------------
_OIDC_META: Optional[Dict[str, Any]] = None


async def _get_oidc_endpoints() -> Dict[str, str]:
    global _OIDC_META
    if _OIDC_META is None:
        meta_url = getattr(oauth.oidc, "server_metadata_url", None) or \
                   f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=10.0, verify=OIDC_VERIFY_SSL) as client:
            try:
                r = await client.get(meta_url)
                r.raise_for_status()
                _OIDC_META = r.json()
            except Exception:
                _OIDC_META = {}

        issuer = _OIDC_META.get("issuer")
        if not issuer:
            base = httpx.URL(meta_url)
            issuer = str(base.copy_with(path=base.path.replace("/.well-known/openid-configuration", "")))
            _OIDC_META["issuer"] = issuer

        base = httpx.URL(_OIDC_META["issuer"])
        _OIDC_META.setdefault(
            "token_endpoint",
            str(base.copy_with(path=base.path.rstrip("/") + "/protocol/openid-connect/token")),
        )
        _OIDC_META.setdefault(
            "end_session_endpoint",
            str(base.copy_with(path=base.path.rstrip("/") + "/protocol/openid-connect/logout")),
        )

    return {
        "issuer": str(_OIDC_META.get("issuer") or ""),
        "token_endpoint": str(_OIDC_META.get("token_endpoint") or ""),
        "end_session_endpoint": str(_OIDC_META.get("end_session_endpoint") or ""),
    }


async def _refresh_access_token(sid: str) -> bool:
    t = await _get_tokens(sid)
    if not t or not t.get("refresh_token"):
        return False

    endpoints = await _get_oidc_endpoints()
    token_url = endpoints["token_endpoint"]
    if not token_url:
        return False

    data = {
        "grant_type": "refresh_token",
        "refresh_token": t["refresh_token"],
        "client_id": os.getenv("OIDC_RP_CLIENT_ID") or "",
    }
    client_secret = os.getenv("OIDC_RP_CLIENT_SECRET")
    if client_secret:
        data["client_secret"] = client_secret
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient(timeout=10.0, verify=OIDC_VERIFY_SSL) as client:
        try:
            resp = await client.post(token_url, data=data, headers=headers)
            if resp.status_code >= 400:
                return False
            new_token = resp.json()
        except Exception:
            return False

    if not new_token.get("access_token"):
        return False
    # Preserve values that some providers omit on refresh responses.
    if not new_token.get("refresh_token") and t.get("refresh_token"):
        new_token["refresh_token"] = t["refresh_token"]
    if not new_token.get("id_token") and t.get("id_token"):
        new_token["id_token"] = t["id_token"]

    await _put_tokens(sid, t.get("email", ""), new_token)
    return True


async def _ensure_valid_access_token(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = (session.get("user") or {}).get("sid")
    if not sid:
        return None

    t = await _get_tokens(sid)
    if not t or not t.get("access_token"):
        return None

    expires_at = int(t.get("expires_at") or 0)
    if _now() < (expires_at - TOKEN_EXPIRY_SKEW_SECONDS):
        return t

    ok = await _refresh_access_token(sid)
    if ok:
        return await _get_tokens(sid)

    # Refresh can fail transiently; keep using token until it is truly expired.
    latest = await _get_tokens(sid) or t
    latest_exp = int(latest.get("expires_at") or 0)
    if latest_exp and _now() < latest_exp:
        return latest

    # Expired + not refreshable: drop stale store entry.
    await _drop_tokens(sid)
    return None


# -----------------------------------------------------------------------------
# JWKS + JWT validation
# -----------------------------------------------------------------------------
_JWKS_CACHE: Optional[Dict[str, Any]] = None
_JWKS_CACHE_TIME: float = 0.0
_JWKS_CACHE_TTL: int = int(os.getenv("JWKS_CACHE_TTL", "3600"))


def _jwks_lock() -> asyncio.Lock:
    """Per-loop, for the reason given at ``_loop_lock``."""
    return _loop_lock("jwks")

#: How long to wait before retrying after a failed refresh, when stale keys
#: are still being served.
_JWKS_RETRY_BACKOFF_SECONDS = int(os.getenv("JWKS_RETRY_BACKOFF_SECONDS", "30"))


def _jwks_url() -> str:
    return f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"


def _jwks_is_fresh() -> bool:
    return bool(_JWKS_CACHE) and (time.time() - _JWKS_CACHE_TIME) < _JWKS_CACHE_TTL


def _fetch_jwks_blocking() -> None:
    """Refresh the JWKS cache. Blocking -- only ever called in a worker thread.

    On failure the previous keys are **kept**. Returning nothing here used to
    mean returning ``None`` to ``_get_signing_key``, which made
    ``validate_jwt_token`` reject every token it was handed -- so a Keycloak
    blip that happened to land after the TTL lapsed would 401 every request in
    the cluster, including all 365 asset chunks, from keys that were still
    perfectly valid. Signing keys rotate on the order of months; stale ones are
    overwhelmingly better than none.
    """
    global _JWKS_CACHE, _JWKS_CACHE_TIME

    if not KEYCLOAK_URL or not KEYCLOAK_REALM:
        # Stamp the clock anyway. Without it `_jwks_is_fresh()` stays False
        # forever, so every single request pays a lock round-trip, an
        # asyncio.to_thread hop and a log line -- on the dev default, forever.
        _JWKS_CACHE_TIME = time.time() - _JWKS_CACHE_TTL + _JWKS_RETRY_BACKOFF_SECONDS
        logger.warning("JWKS fetch skipped: KEYCLOAK_URL or KEYCLOAK_REALM not set")
        return

    try:
        with httpx.Client(timeout=10.0, verify=OIDC_VERIFY_SSL) as client:
            resp = client.get(_jwks_url())
            resp.raise_for_status()
            _JWKS_CACHE = resp.json()
            _JWKS_CACHE_TIME = time.time()
    except Exception as exc:
        # Back off in BOTH cases. Doing it only when keys were cached meant a
        # cold cache re-ran the whole 10s fetch for every queued request: the
        # single-flight lock serialises them, each re-checks `_jwks_is_fresh()`,
        # still False, so N waiting requests cost N x 10s against a Keycloak
        # that is blackholed rather than refusing.
        _JWKS_CACHE_TIME = time.time() - _JWKS_CACHE_TTL + _JWKS_RETRY_BACKOFF_SECONDS
        if _JWKS_CACHE:
            logger.warning("JWKS refresh from %s failed (%s); keeping cached keys", _jwks_url(), exc)
        else:
            logger.error("JWKS fetch from %s failed and no keys are cached: %s", _jwks_url(), exc)


async def _ensure_jwks_ready() -> None:
    """Warm/refresh the JWKS cache without blocking the event loop.

    ``validate_jwt_token`` is synchronous and called from async handlers, and
    it used to fetch the JWKS inline with a **sync** ``httpx.Client``. That is
    a blocking call on the event loop: for up to 10 seconds nothing else in
    this process could make progress -- not the other in-flight asset
    requests, and not the WebSocket pumps, so a badly timed refresh could drop
    a live dashboard's connection and lose its session state. It fired on the
    first request after boot and again every time ``JWKS_CACHE_TTL`` (1h)
    lapsed.

    Awaiting this first keeps the fetch on a worker thread and keeps
    ``validate_jwt_token`` a pure, synchronous, patchable function.
    """
    if _jwks_is_fresh():
        return
    async with _jwks_lock():
        # Single-flight: 107 concurrent chunk requests must not become 107
        # concurrent fetches. The re-check covers the ones that queued here.
        if _jwks_is_fresh():
            return
        await asyncio.to_thread(_fetch_jwks_blocking)


def _get_jwks() -> Optional[Dict[str, Any]]:
    """The cached JWKS. Never fetches -- see ``_ensure_jwks_ready``."""
    return _JWKS_CACHE


def _get_signing_key(token: str):
    jwks_data = _get_jwks()
    if not jwks_data:
        return None
    from jwt import PyJWKSet

    jwks = PyJWKSet.from_dict(jwks_data)
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")

    for key in jwks.keys:
        if key.key_id == kid:
            return key.key

    for key in jwks.keys:
        if getattr(key, "key_type", None) == "RSA":
            return key.key
    return None


def validate_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        signing_key = _get_signing_key(token)
        if not signing_key:
            logger.warning("JWT validation failed: no signing key available")
            return None

        client_id = os.getenv("OIDC_RP_CLIENT_ID", "")
        audiences = [aud for aud in (client_id, "broker", "account") if aud]

        kwargs: Dict[str, Any] = {}
        if JWT_VERIFY_ISSUER and EXPECTED_ISSUER:
            kwargs["issuer"] = EXPECTED_ISSUER

        payload = jwt.decode(
            token,
            signing_key,
            algorithms=[JWT_ALG],
            options={"require": ["exp"], "verify_signature": True},
            leeway=JWT_LEEWAY_SECONDS,
            audience=audiences,
            **kwargs,
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.info("JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("JWT validation failed: %s", e)
        return None


def _claims_from_token_set(tokens: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("id_token", "access_token"):
        tok = tokens.get(key)
        if isinstance(tok, str) and tok:
            payload = validate_jwt_token(tok)
            if payload:
                return payload
    return {}


def _jwt_user_payload(token: str, claims: Dict[str, Any]) -> Dict[str, Any]:
    """Identity for a bare access token. Carries no refresh token, by design."""
    return {
        "sub": claims.get("sub"),
        "email": claims.get("email"),
        "preferred_username": claims.get("preferred_username"),
        "access_token": token,
    }


def _session_user_payload(session: Dict[str, Any], tokens: Dict[str, Any]) -> Dict[str, Any]:
    """Identity for a server-side session, including its renewable token set."""
    claims = _claims_from_token_set(tokens)
    session_email = (session.get("user") or {}).get("email") or ""
    return {
        "sub": claims.get("sub") or "",
        "email": claims.get("email") or session_email or tokens.get("email") or "",
        "preferred_username": claims.get("preferred_username") or claims.get("name") or "",
        "access_token": tokens.get("access_token"),
        "id_token": tokens.get("id_token"),
        "refresh_token": tokens.get("refresh_token"),
    }


# -----------------------------------------------------------------------------
# URL helpers
# -----------------------------------------------------------------------------
#: Session key holding where to return after the OIDC round trip.
_NEXT_SESSION_KEY = "lex_post_login_next"

#: Strip a consumed ``auth_token`` out of the address bar with a redirect.
STRIP_AUTH_TOKEN_FROM_URL = _env_bool("STRIP_AUTH_TOKEN_FROM_URL", True)


#: Paths that exist for machinery, never for a person to look at.
#:
#: Every one of them is fetched by script or loaded into a hidden frame: the
#: theme relay is framed invisibly and zero-sized, ``/auth/token`` and
#: ``/auth/adopt`` are ``fetch`` targets, ``/_stcore/*`` is Streamlit's own
#: bootstrap. None renders anything a user could act on.
#:
#: They are listed because returning a *browser* to one of them is how a
#: utility request turns into a dead end. That is LEX-1477: the frontend's
#: hidden ``/_lex/theme-relay`` frame reached a proxy that did not yet serve
#: that route, fell into the authenticated catch-all, and got the frame
#: breakout -- whose job is to send the TOP window to a login. The OIDC round
#: trip then honoured ``next=/_lex/theme-relay``, so the whole tab finished on
#: a blank storage shim with no way back to the app.
#:
#: The route exists now, so that particular 401 cannot happen again. This is
#: the guard that makes the *consequence* impossible regardless: whatever
#: 401s, a browser is never sent somewhere it has nothing to do.
_NON_LANDING_PREFIXES = ("/_lex/", "/_stcore/")
_NON_LANDING_PATHS = frozenset({"/auth/token", "/auth/adopt"})


def _is_landing_path(path: str) -> bool:
    """Whether *path* is somewhere a browser can usefully be returned to."""
    if path in _NON_LANDING_PATHS:
        return False
    return not path.startswith(_NON_LANDING_PREFIXES)


def _safe_next_path(raw: Optional[str]) -> Optional[str]:
    """A same-origin, path-only, *human-visitable* redirect target, or ``None``.

    Deliberately strict: only a single-leading-slash path (plus query and
    fragment) survives. Anything carrying a scheme, an authority, or a
    protocol-relative ``//host`` prefix is discarded, because this value ends
    up in a ``Location`` header after an OIDC round trip -- exactly the shape
    of an open redirect, and a login flow is the most valuable place to have
    one.

    Same-origin is not sufficient on its own, though. ``/_lex/theme-relay`` is
    as same-origin as it gets and still the wrong place to leave a browser --
    see :data:`_NON_LANDING_PREFIXES`. Rejecting here covers ``login``'s
    ``?next=`` on the way in *and* ``auth_callback``'s re-validation on the way
    out, so a value that was stashed by an older build, or by a frame that had
    no business asking, cannot strand the tab either.
    """
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    # Backslashes are treated as slashes by some browsers, so "/\evil.com" and
    # "/\\evil.com" would escape the origin.
    if candidate[1:2] == "\\" or "\\" in candidate[:2]:
        return None
    if not _is_landing_path(urlparse(candidate).path):
        return None
    return candidate


def _current_relative_path(request: Request) -> str:
    """This request's path and query, with any ``auth_token`` removed.

    Always a path on this origin: a request for ``/%2Fevil.example/`` has the path
    ``//evil.example/``, which as a ``Location`` is scheme-relative -- another host.
    """
    query = _query_without_auth_token(request)
    return dash_sites.local_path(request.url.path) + (f"?{query}" if query else "")


def _query_without_auth_token(request: Request) -> str:
    """The request query string minus ``auth_token``, preserving the rest."""
    kept = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "auth_token"
    ]
    return urlencode(kept)


#: Every hostname this dashboard is served on, from the environment the
#: lex-instance terraform module writes: the canonical one first, then any
#: customer-owned domain attached to the instance. Falls back to the canonical
#: host alone, which is what an instance with no custom domain has.
#:
#: This is an ALLOW-LIST, and must stay one. `_external_base_url` builds the
#: OIDC `redirect_uri` and the post-logout URL from the request's own host, so
#: trusting the `Host` header as it arrives would let anyone who can set it
#: have Keycloak redirect to a host of their choosing, carrying the
#: authorization code with it.
PUBLIC_HOSTS = frozenset(lex_hosts.public_hosts("streamlit"))


def _requested_host(request: Request) -> str:
    """The hostname the browser used, if it is one we are served on.

    `request.url` already reflects `X-Forwarded-Host`/`X-Forwarded-Proto` where
    ProxyHeadersMiddleware is installed, and the ingress sets both.
    """
    host = (request.url.hostname or "").strip().lower()
    return host if host in PUBLIC_HOSTS else ""


def _external_base_url(request: Request) -> str:
    """The origin to build absolute URLs back to this proxy with.

    The request's own host wins over the canonical ``STREAMLIT_URL`` whenever
    we recognise it. An instance with a customer-owned domain attached serves
    its dashboard on several hostnames at once, and a user who arrives on one
    of the others must come back to *that* one: the session cookie this proxy
    sets is host-only, so completing the sign-in on the canonical host would
    leave the host the user is actually on with no cookie at all -- and a
    sign-in loop that never terminates.
    """
    host = _requested_host(request)
    if host:
        # Only the HOST comes from the request. The scheme and port come from
        # the canonical URL, because every hostname a role is served on shares
        # them -- and because the request's own scheme is only trustworthy
        # where ProxyHeadersMiddleware could be imported. Reading it anyway
        # would put `http://` into the OIDC redirect_uri the moment that import
        # failed, which Keycloak refuses and which no log here would explain.
        scheme = PUBLIC_URL_OBJ.scheme if PUBLIC_URL_OBJ else (request.url.scheme or "https")
        try:
            port = PUBLIC_URL_OBJ.port if PUBLIC_URL_OBJ else request.url.port
        except ValueError:
            # Reading a port out of the REQUEST parses whatever the Host header
            # carried, and that header is attacker-influenced. `h:99999` and
            # `h:-1` both raise, which would be a 500 on the sign-in path --
            # from input the allow-list above has otherwise already accepted,
            # since it compares hostnames and never looks at the port.
            port = None
        # `lex streamlit` serves http://localhost:8501, so a non-default port
        # is kept; httpx reports None for the scheme's own default.
        netloc = f"{host}:{port}" if port else host
        return f"{scheme}://{netloc}"
    if PUBLIC_URL:
        return PUBLIC_URL
    return str(request.base_url).rstrip("/")


def _callback_url(request: Request) -> str:
    return f"{_external_base_url(request)}/auth/callback"


def _login_url(request: Request, next_path: Optional[str] = None) -> str:
    """The proxy's login entry point, optionally carrying where to come back to.

    Without ``next`` the callback can only land on ``/``, which for an embedded
    dashboard means the user is dropped on the app's first page having lost
    whatever they were doing -- the "clicking a button throws me back to the
    first page" report.
    """
    return _external_base_url(request) + _login_path(request, next_path)


def _login_path(request: Request, next_path: Optional[str] = None) -> str:
    """``/auth/login``, optionally with ``?next=``. Relative, for same-origin hops.

    A plain redirect stays relative so it cannot be broken by a misconfigured
    ``STREAMLIT_URL``/``BASE_URL``; only the frame breakout needs the absolute
    form, because that URL is handed to script navigating the *top* window.
    """
    safe = _safe_next_path(next_path)
    if not safe:
        return "/auth/login"
    return f"/auth/login?{urlencode({'next': safe})}"


def _is_iframe_document_request(request: Request) -> bool:
    """True for a document navigation happening inside an ``<iframe>``/``<frame>``.

    Browsers set ``Sec-Fetch-Dest: iframe`` (or ``frame``) on the iframe's own
    document request. Redirecting such a request to Keycloak would load the login
    page inside the frame, which Keycloak forbids via ``X-Frame-Options`` /
    ``Content-Security-Policy: frame-ancestors 'self'`` -- so the browser renders
    "refused to connect" instead. We detect that case to break out of the frame.
    """
    return request.headers.get("sec-fetch-dest", "").strip().lower() in {"iframe", "frame"}


def _is_document_request(request: Request) -> bool:
    """True for a top-level or framed *document* navigation, not a subresource.

    ``Sec-Fetch-Dest`` is absent on older browsers; falling back to the Accept
    header keeps behaviour reasonable there.
    """
    dest = request.headers.get("sec-fetch-dest", "").strip().lower()
    if dest:
        return dest in {"document", "iframe", "frame"}
    return "text/html" in request.headers.get("accept", "")


def _frame_may_move_the_tab(request: Request) -> bool:
    """Whether a framed request may navigate the top window by itself.

    Not when another application put the frame on screen. ``Sec-Fetch-Site``
    describes the relation between the document that *started* the navigation
    and its target, so on a framed document load it names the embedder:
    ``same-site`` is lex-app's shell on the instance hostname framing the
    dashboard host beside it, ``cross-site`` is anyone else. In both cases the
    top window is showing someone else's page -- the one the user was actually
    reading -- and a frame does not get to decide that page is over.

    That is the whole of the reported fault. A sidebar entry framed the
    dashboard with no credential, the frame 401'd, and this page moved the tab
    to the dashboard host's login; the user clicked something inside their
    instance and ended up on ``https://<instance>-streamlit.<domain>/``.

    Withheld here, the response is still the breakout page: it still posts
    ``lex-auth-required``, which is the channel the React shell listens on and
    recovers the dashboard through, and still offers the ``target="_top"``
    link, which is a user gesture and always permitted. The frame is never
    redirected to Keycloak, so the "refused to connect" wall the breakout was
    written for cannot come back either way.

    ``same-origin`` -- the proxy framing its own page -- keeps the automatic
    navigation: the window it would move is one this response already owns. So
    does a request with no ``Sec-Fetch-Site`` at all, which is the convention
    the rest of this routing follows (see scenario 1.214): an absent
    ``Sec-Fetch-*`` means a client too old or too synthetic to be the browser
    tab this guard is about, and gets the pre-existing behaviour.
    """
    site = request.headers.get("sec-fetch-site", "").strip().lower()
    return site not in {"same-site", "cross-site"}


def _frame_breakout_response(request: Request) -> Response:
    """A 401 that escapes the iframe to a full-page login instead of framing the IdP.

    Three layered mechanisms, most-reliable last:
      1. an automatic top-level navigation, withheld when another application is
         the one framing this -- see :func:`_frame_may_move_the_tab`;
      2. a ``postMessage`` so a cooperating parent shell can drive re-auth;
      3. a user-clickable ``target="_top"`` link, which is always permitted because
         it is a user gesture.
    """
    login = _login_url(request, _current_relative_path(request))
    login_js = json.dumps(login)  # safe JS string literal
    login_attr = html.escape(login, quote=True)
    takeover = (
        "try{if(window.top!==window.self){window.top.location.href=u;}"
        "else{window.location.href=u;}}catch(e){}"
        if _frame_may_move_the_tab(request)
        else ""
    )
    body = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Session expired</title></head><body>"
        "<script>(function(){var u=" + login_js + ";"
        + takeover +
        "try{window.parent.postMessage({type:'lex-auth-required',login:u},'*');}catch(e){}"
        "})();</script>"
        "<div style=\"font:16px system-ui,Arial,sans-serif;padding:24px;text-align:center\">"
        "Your session has expired. "
        "<a href=\"" + login_attr + "\" target=\"_top\" rel=\"noopener\">Sign in again</a>."
        "</div></body></html>"
    )
    return HTMLResponse(body, status_code=401)


def _unauthenticated_response(request: Request) -> Response:
    """Response to send when no valid identity is present.

    - iframe/frame document load of a *page* -> break out to a top-level login
      (never frame the IdP)
    - iframe/frame document load of a utility path -> 401 JSON, no breakout
    - other top-level HTML navigation -> normal redirect to login
    - XHR / fetch / API call -> 401 JSON
    """
    accepts_html = "text/html" in request.headers.get("accept", "")
    if accepts_html and _is_iframe_document_request(request):
        # A framed *page* is a dashboard the user is looking at, so recovering
        # it is worth taking over the tab for. A framed utility path is not:
        # it is a hidden shim nobody can see, and the breakout would move the
        # whole window on behalf of a frame the user does not know exists.
        # That is the mechanism behind LEX-1477 -- see _NON_LANDING_PREFIXES.
        # 401 instead, which the shim's own code can deal with or ignore.
        if not _is_landing_path(request.url.path):
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        return _frame_breakout_response(request)
    if accepts_html:
        return RedirectResponse(url=_login_path(request, _current_relative_path(request)))
    return JSONResponse({"error": "Authentication required"}, status_code=401)


# -----------------------------------------------------------------------------
# NEW: persist auth_token login to session
# -----------------------------------------------------------------------------
async def _persist_jwt_to_session_if_needed(request: Request, jwt_token: str, payload: Dict[str, Any], token_source: str) -> None:
    """
    Streamlit embedded mode often sends auth_token only on the initial iframe URL.
    Follow-up requests won't include it, so we persist the validated JWT into our session/token store.

    token_source: "query" | "header" | "cookie"
    """
    if not PERSIST_JWT_AUTH_TO_SESSION:
        return

    incoming_exp = int(payload.get("exp") or 0)

    # Reuse existing valid server-side session if available; otherwise replace stale one.
    existing_sid = (request.session.get("user") or {}).get("sid")
    if existing_sid:
        existing = await _get_tokens(existing_sid)
        existing_exp = int((existing or {}).get("expires_at") or 0)
        still_valid = bool(
            existing
            and existing.get("access_token")
            and (not existing_exp or _now() < (existing_exp - TOKEN_EXPIRY_SKEW_SECONDS))
        )
        # A strictly newer token supersedes a still-valid one. The embedded path
        # gets no refresh token, so renewal can only arrive as a fresh
        # ``auth_token`` from the frontend — which necessarily shows up *before*
        # the stored one expires. Keeping the older token here would discard
        # every such renewal and let the session die anyway, which is exactly the
        # dead end the caller was trying to avoid.
        if still_valid and not (incoming_exp and incoming_exp > existing_exp):
            return
        await _drop_tokens(existing_sid)

    sid = secrets.token_urlsafe(16)

    # Keycloak access tokens don't always include email; use best-effort identity.
    email = payload.get("email") or payload.get("preferred_username") or ""

    # Store this access token as if it were our "session token set".
    # expires_at taken from JWT exp so _ensure_valid_access_token can enforce expiry.
    token_set = {
        "access_token": jwt_token,
        "expires_at": int(payload.get("exp") or 0),
        "id_token": None,
        "refresh_token": None,
    }
    await _put_tokens(sid, email, token_set)

    # Tiny session cookie
    request.session["user"] = {"email": email, "sid": sid}

    # NOTE: SessionMiddleware will set Set-Cookie automatically because session changed.


# -----------------------------------------------------------------------------
# Django-anchored hand-off (LEX_STREAMLIT_AUTH = dual | handoff)
# -----------------------------------------------------------------------------
# A dashboard never holds Keycloak tokens of its own here. Its standing is an
# opaque handle in the `__Host-lex_dash` cookie that names a DashboardBinding row
# (stored hashed) on the user's Django sign-in (django-oauth2-codeflow's
# OIDCSession). Every request and every WebSocket asks the broker for a fresh
# access token of that sign-in, and only that token plus identity headers go
# upstream -- never a refresh or id token. Getting a handle:
#
#   1. no usable handle -> a nonce cookie, and the frame goes to
#      <app host>/oidc/handoff?aud=streamlit&return=<this URL>&state=<nonce>;
#   2. Django answers with a single-use code (or, in a frame without a sign-in,
#      error=login_required -- Keycloak is never shown in a frame);
#   3. /auth/handoff checks `state` against the nonce cookie, redeems the code
#      in-process (lex.authentication.dashboards.redeem: same image, same
#      database) and sets the handle cookie.
#
# The handle never changes, so the Cookie header Streamlit froze at WebSocket
# connect keeps working for /auth/token for as long as the sign-in lasts.
#
# Django ORM work never runs on this event loop: a small dedicated thread pool
# (`_run_orm`) runs it with Django's per-request connection handling around it.

#: Worker threads for database work; each holds at most one connection at a time.
DB_THREADS = _env_int("LEX_PROXY_DB_THREADS", 8)
_ORM_EXECUTOR: Optional[ThreadPoolExecutor] = None
_ORM_EXECUTOR_LOCK = threading.Lock()

#: Hand-offs in a row, with no handle cookie coming back, after which the browser is taken to be
#: blocking the cookies. One is not enough: the first may only have outlived its nonce (a sign-in
#: that took longer than the nonce cookie lives).
HANDOFF_ATTEMPTS_BEFORE_BLOCKED = 2

#: Reasons a dashboard reports to the frontend around it (`{"type": "lex-auth", "event": "required"}`).
_REQUIRED_REASONS = frozenset({"login_required", "session_ended", "handoff_failed"})


def _orm_executor() -> ThreadPoolExecutor:
    global _ORM_EXECUTOR
    with _ORM_EXECUTOR_LOCK:
        if _ORM_EXECUTOR is None:
            _ORM_EXECUTOR = ThreadPoolExecutor(
                max_workers=max(1, DB_THREADS), thread_name_prefix="lex-proxy-db"
            )
        return _ORM_EXECUTOR


def _shutdown_orm_executor() -> None:
    global _ORM_EXECUTOR
    with _ORM_EXECUTOR_LOCK:
        executor, _ORM_EXECUTOR = _ORM_EXECUTOR, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _orm_job(fn, *args, **kwargs):
    """Run ``fn`` the way Django runs a request: set up, a usable connection, closed per CONN_MAX_AGE."""
    from django.apps import apps

    if not apps.ready:  # a proxy started without `lex streamlit` (which sets Django up first)
        from lex.tools.django_bootstrap import setup_django

        setup_django()
    from django.db import close_old_connections

    close_old_connections()
    try:
        return fn(*args, **kwargs)
    finally:
        close_old_connections()


async def _run_orm(fn, *args, **kwargs):
    """Run synchronous Django ORM code off the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_orm_executor(), partial(_orm_job, fn, *args, **kwargs))


@dataclass
class _HandoffAuth:
    """What a handle cookie amounts to right now.

    ``kind``: ``ok`` (a token valid for at least a minute -- or, with Keycloak unreachable, the
    stored one for as long as it is still valid: the library serves it), ``unavailable``
    (Keycloak unreachable and no valid token left: identity only, marked degraded), ``ended``
    (the sign-in is over), ``none`` (no such binding, or it was revoked), ``error`` (the
    database could not be asked).
    """

    kind: str
    binding_id: Optional[int] = None
    token: Optional[str] = None
    expires_at: int = 0
    identity: Dict[str, str] = field(default_factory=dict)
    reset_cookie: bool = False
    retry_after: int = 5
    reason: str = ""


def _resolve_handle_blocking(handle: str, can_set_cookie: bool = True) -> _HandoffAuth:
    """Handle -> binding -> a fresh access token of its sign-in (in a worker thread).

    ``can_set_cookie``: whether the answer goes out on a response that can carry a ``Set-Cookie``
    to the browser. Only then is a due daily re-set of the handle cookie claimed (and recorded):
    the loopback ``/auth/token`` pull and a WebSocket handshake cannot set a cookie, so they must
    not use the re-set up -- or the 30-day cookie would never slide.
    """
    from oauth2_authcodeflow.exceptions import OIDCAuthUnavailable, OIDCSessionEnded

    from lex.authentication.dashboards import broker, handoff

    binding = handoff.resolve(handle, aud=handoff.AUD_STREAMLIT)
    if binding is None:
        return _HandoffAuth("none", reason="unknown_handle")
    identity = handoff.identity(binding)
    try:
        token = broker.access_token(binding)
    except OIDCSessionEnded as exc:
        return _HandoffAuth("ended", binding.pk, identity=identity, reason=exc.reason)
    except OIDCAuthUnavailable as exc:
        return _HandoffAuth(
            "unavailable", binding.pk, identity=identity,
            retry_after=int(exc.retry_after or 5), reason="auth_unavailable",
        )
    reset = can_set_cookie and handoff.cookie_due(binding)
    if reset:
        handoff.mark_cookie_set(binding)
    return _HandoffAuth(
        "ok", binding.pk, token=token, expires_at=_decode_exp_no_verify(token),
        identity=identity, reset_cookie=reset,
    )


def _redeem_blocking(code: str, origin: str, state: str) -> Tuple[Optional[str], str]:
    """(handle, "") for a redeemed code, else (None, reason) -- in a worker thread."""
    from lex.authentication.dashboards import handoff

    try:
        return handoff.redeem(code, aud=handoff.AUD_STREAMLIT, origin=origin, state=state).handle, ""
    except handoff.HandoffError as exc:
        return None, exc.reason


def _handle_standing_blocking(handle: str) -> str:
    """``ok``, ``ended`` (its sign-in is over or its user deactivated: revoked now) or ``none``
    (no such binding, or revoked already) -- from the database alone, no Keycloak call."""
    from lex.authentication.dashboards import broker, handoff

    binding = handoff.resolve(handle, aud=handoff.AUD_STREAMLIT)
    if binding is None:
        return "none"
    problem = broker.standing_problem(binding)
    if problem is not None:
        handoff.revoke(binding, problem)
        return "ended"
    return "ok"


def _revoke_handle_blocking(handle: str, reason: str) -> bool:
    from lex.authentication.dashboards import handoff

    binding = handoff.resolve(handle, aud=handoff.AUD_STREAMLIT)
    return bool(binding is not None and handoff.revoke(binding, reason))


def _dash_secure(conn) -> bool:
    """Whether the browser reached us over https, so the `__Host-` cookies apply."""
    return PUBLIC_IS_HTTPS or SESSION_HTTPS_ONLY or conn.url.scheme in ("https", "wss")


def _read_dash_cookie(conn, name: str) -> str:
    return conn.cookies.get(dash_cookies.cookie_name(name, _dash_secure(conn)), "")


def _read_handle_any(conn) -> str:
    """The handle for /auth/token, a loopback call from Streamlit that carries the browser's frozen
    Cookie header but no X-Forwarded-Proto to tell which name applies.

    The configuration decides, never the request: a proxy configured for https (``STREAMLIT_URL``
    https, or ``SESSION_HTTPS_ONLY``) reads only ``__Host-lex_dash`` -- the unprefixed name is one
    any sibling host can plant (``Domain=``), so honouring it there would let another site choose
    which sign-in a dashboard renews. Only a plain-http proxy, which sets the unprefixed name
    itself, reads it.
    """
    handle = conn.cookies.get(dash_cookies.HANDLE_COOKIE, "")
    if handle or PUBLIC_IS_HTTPS or SESSION_HTTPS_ONLY:
        return handle
    return conn.cookies.get(dash_cookies.cookie_name(dash_cookies.HANDLE_COOKIE, False), "")


def _set_dash_cookie(response: Response, conn, name: str, value: str, max_age: int) -> None:
    secure = _dash_secure(conn)
    response.headers.append(
        "set-cookie",
        dash_cookies.set_cookie_header(dash_cookies.cookie_name(name, secure), value, max_age=max_age, secure=secure),
    )


def _clear_dash_cookie(response: Response, conn, name: str) -> None:
    secure = _dash_secure(conn)
    response.headers.append(
        "set-cookie", dash_cookies.delete_cookie_header(dash_cookies.cookie_name(name, secure), secure=secure)
    )


def _store_nonces(response: Response, conn, nonces: List[str]) -> None:
    value = dash_cookies.nonce_cookie_value(nonces)
    if value:
        _set_dash_cookie(response, conn, dash_cookies.NONCE_COOKIE, value, dash_cookies.NONCE_MAX_AGE)
    else:
        _clear_dash_cookie(response, conn, dash_cookies.NONCE_COOKIE)


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def _handoff_auth(conn, *, handle: Optional[str] = None, can_set_cookie: bool = True) -> Optional[_HandoffAuth]:
    """The hand-off standing of a request or WebSocket; ``None`` when it carries no handle.

    ``can_set_cookie=False`` for answers that cannot carry a ``Set-Cookie`` (the loopback token
    pull, a WebSocket handshake): they never claim the handle cookie's daily re-set.
    """
    handle = _read_dash_cookie(conn, dash_cookies.HANDLE_COOKIE) if handle is None else handle
    if not handle:
        return None
    try:
        auth = await _run_orm(_resolve_handle_blocking, handle, can_set_cookie)
    except Exception as exc:  # the database itself could not be asked
        logger.warning("Dashboard sign-in could not be checked (%s); answering 503", type(exc).__name__)
        return _HandoffAuth("error", reason="database_unavailable")
    if auth.kind == "unavailable":
        logger.warning("Keycloak is unavailable and the dashboard's token has run out; forwarding identity only")
    elif auth.kind == "ended":
        logger.info("Dashboard sign-in ended (%s); its binding is revoked", auth.reason)
    return auth


def _browser_origin(conn) -> Optional[str]:
    """The origin the browser used, if the instance lists it as a dashboard host.

    The handle cookie is set on exactly this host, so the hand-off has to come back to it --
    and Django only sends a code to a host it knows. The ``Host`` header is the client's, so
    it is only trusted once it matches the list.
    """
    host = (conn.headers.get("host") or "").strip()
    if not host:
        return None
    origin = dash_sites.normalize_origin(f"{'https' if _dash_secure(conn) else 'http'}://{host}")
    if origin is None:
        return None
    if dash_sites.is_dashboard_origin(origin) or origin == dash_sites.normalize_origin(PUBLIC_URL):
        return origin
    return None


def _ws_origin_allowed(websocket) -> bool:
    """Whether a WebSocket handshake comes from the dashboard's own pages.

    A browser sends the dashboard's cookies with a socket that *any* page opens -- they are
    ``SameSite=None``, the dashboard lives in a cross-site frame -- and the relay turns them into
    the user's identity and access token upstream (cross-site WebSocket hijacking). So a present
    ``Origin`` must be the dashboard's own: the origin of the host the browser connected to, or a
    dashboard origin of this instance (``LEX_INSTANCE_HOSTS``, ``STREAMLIT_URL``). ``null`` (a
    sandboxed or opaque page) is not. A handshake without ``Origin`` is not a browser's and so
    carries no browser's cookies: it passes, and is authenticated like any other.
    """
    raw = websocket.headers.get("origin")
    if raw is None:
        return True
    origin = dash_sites.normalize_origin(raw)
    if origin is None:
        return False
    if origin == _browser_origin(websocket) or dash_sites.is_dashboard_origin(origin):
        return True
    if PUBLIC_URL and origin == dash_sites.normalize_origin(PUBLIC_URL):
        return True
    host = (websocket.headers.get("host") or "").strip()
    schemes = ("https",) if _dash_secure(websocket) else ("https", "http")
    return bool(host) and any(origin == dash_sites.normalize_origin(f"{scheme}://{host}") for scheme in schemes)


def _handoff_app_origin(conn, query_items: Optional[List[Tuple[str, str]]] = None) -> Optional[str]:
    """The app host a hand-off from this dashboard goes to.

    Django's session cookie is host-only, so the right host is the one the user is signed in on:
    the frontend that framed the dashboard names its own origin (`lex_app_origin`), and that is
    taken when the instance lists it as an app host (a loopback dev server is not Django, so it
    does not count). Otherwise -- a dashboard opened on its own -- the listed app host that is
    same-site with the dashboard.
    """
    items = list(conn.query_params.multi_items()) if query_items is None else query_items
    claimed = dash_sites.normalize_origin(dict(items).get("lex_app_origin"))
    if claimed and claimed in dash_sites.app_origins():
        return claimed
    return dash_sites.pick_app_origin(conn.headers.get("host", ""))


def _query_items_without(conn, *names: str) -> List[Tuple[str, str]]:
    return [(key, value) for key, value in conn.query_params.multi_items() if key not in names]


def _relative(path: str, items: List[Tuple[str, str]]) -> str:
    """``path?query`` -- the path made one that can only mean this origin (never ``//host``)."""
    return dash_sites.local_path(path) + (f"?{urlencode(items)}" if items else "")


def _clean_relative_path(conn) -> str:
    """This URL without the hand-off marker and any `auth_token` (never a credential in a URL)."""
    return _relative(conn.url.path, _query_items_without(conn, dash_cookies.LOOP_MARKER, "auth_token"))


def _strip_params(relative_url: str, names: Tuple[str, ...]) -> str:
    parts = urlsplit(relative_url)
    items = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in names]
    return _relative(parts.path or "/", items)


def _handoff_attempts(conn) -> int:
    raw = conn.query_params.get(dash_cookies.LOOP_MARKER)
    if raw is None:
        return 0
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _frontend_extra_origins() -> List[str]:
    """The configured frontend, which may have its own host (REACT_APP_URL / LEX_FRONTEND_URL)."""
    return [o for o in (os.getenv("REACT_APP_URL"), os.getenv("LEX_FRONTEND_URL")) if o]


def _message_targets(next_path: str) -> List[str]:
    """Origins this dashboard's auth messages may go to: the frontend that framed it (the
    `lex_app_origin` the frontend put on the URL, when the instance lists it), else every app
    origin of the instance. A message posted to an origin the parent does not have is dropped
    by the browser, so no message can reach a page the instance does not list."""
    query = dict(parse_qsl(urlsplit(next_path).query))
    claimed = dash_sites.normalize_origin(query.get("lex_app_origin"))
    if claimed and dash_sites.is_app_origin(claimed, extra=_frontend_extra_origins()):
        return [claimed]
    return list(dash_sites.app_origins())


def _script_json(value: Any) -> str:
    """JSON safe inside a <script> element (no "</" can end it early)."""
    return json.dumps(value, sort_keys=True).replace("<", "\\u003c")


_PAGE_STYLE = "font:16px system-ui,Arial,sans-serif;padding:24px;text-align:center"


def _sign_in_required_page(conn, *, reason: str, next_path: str) -> Response:
    """The page a dashboard frame shows when its sign-in is gone (401).

    It tells the frontend around it -- ``{"type": "lex-auth", "v": 2, "event": "required",
    "surface": "streamlit", "reason": ...}`` posted from the dashboard frame itself, to pinned
    origins, with no secret -- and the frontend renews its own sign-in and reloads the
    dashboard in place. Nothing here navigates the top window. Shown on its own (no frame), it
    offers the sign-in, which comes back to this dashboard.
    """
    reason = reason if reason in _REQUIRED_REASONS else "handoff_failed"
    clean = _strip_params(next_path, (dash_cookies.LOOP_MARKER, "auth_token"))
    origin = _browser_origin(conn)
    app_origin = _handoff_app_origin(conn, parse_qsl(urlsplit(next_path).query)) if origin else None
    sign_in = (
        f"{app_origin}/oidc/authenticate?{urlencode({'next': origin + clean})}" if app_origin and origin else clean
    )
    message = {"type": "lex-auth", "v": 2, "event": "required", "surface": "streamlit", "reason": reason}
    body = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Sign-in required</title></head>"
        f"<body><div style=\"{_PAGE_STYLE}\">"
        "<p>Your sign-in for this dashboard has ended.</p>"
        f"<p id=\"framed\" hidden><a href=\"{html.escape(clean, quote=True)}\">Reload the dashboard</a></p>"
        f"<p id=\"standalone\"><a href=\"{html.escape(sign_in, quote=True)}\" target=\"_top\" rel=\"noopener\">Sign in again</a></p>"
        "</div><script>(function(){"
        f"var m={_script_json(message)};var targets={_script_json(_message_targets(next_path))};"
        "try{m.id=(window.crypto&&crypto.randomUUID)?crypto.randomUUID():String(Date.now())+Math.random().toString(36).slice(2);}catch(e){}"
        "if(window.parent&&window.parent!==window){"
        "document.getElementById('framed').hidden=false;document.getElementById('standalone').hidden=true;"
        "for(var i=0;i<targets.length;i++){try{window.parent.postMessage(m,targets[i]);}catch(e){}}}"
        "})();</script></body></html>"
    )
    return _no_store(HTMLResponse(body, status_code=401))


def _cookies_blocked_page(conn) -> Response:
    """The page shown when the browser keeps no dashboard cookie in this frame (no redirect loop).

    Offers storage access (a user gesture, where the browser supports it) and opening the
    dashboard in a tab of its own, where its cookies are first-party.
    """
    clean = _clean_relative_path(conn)
    body = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Cookies blocked</title></head>"
        f"<body><div style=\"{_PAGE_STYLE}\">"
        "<p>This dashboard needs a cookie that your browser does not keep in this frame.</p>"
        "<p><button id=\"allow\" hidden>Allow access</button></p>"
        f"<p><a href=\"{html.escape(clean, quote=True)}\" target=\"_blank\" rel=\"noopener\">Open the dashboard in a new tab</a></p>"
        "</div><script>(function(){"
        f"var url={_script_json(clean)};var b=document.getElementById('allow');"
        "if(document.requestStorageAccess){b.hidden=false;b.onclick=function(){"
        "document.requestStorageAccess().then(function(){window.location.replace(url);},"
        "function(){b.disabled=true;b.textContent='Access was not granted';});};}"
        "})();</script></body></html>"
    )
    return _no_store(HTMLResponse(body, status_code=401))


def _handoff_misconfigured_page(conn, detail: str) -> Response:
    logger.warning("Dashboard hand-off impossible: %s (Host %r)", detail, conn.headers.get("host"))
    body = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Dashboard not configured</title></head>"
        f"<body><div style=\"{_PAGE_STYLE}\"><p>This dashboard cannot sign you in: {html.escape(detail)}.</p>"
        "<p>Check LEX_INSTANCE_HOSTS (or STREAMLIT_URL and DOMAIN_HOSTED) for this instance.</p></div></body></html>"
    )
    return _no_store(HTMLResponse(body, status_code=400))


def _signed_out_page(conn) -> Response:
    body = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Signed out</title></head>"
        f"<body><div style=\"{_PAGE_STYLE}\"><p>You are signed out of this dashboard.</p>"
        "<p><a href=\"/\">Open the dashboard again</a></p></div></body></html>"
    )
    return _no_store(HTMLResponse(body, status_code=200))


def _handoff_unauthenticated(conn, reason: str = "not_authenticated") -> Response:
    """The contract's background answer (fetch/XHR, assets): 401 JSON, never a redirect."""
    reason = reason if reason in ("not_authenticated", "session_expired") else "not_authenticated"
    response = JSONResponse({"error": reason, "login_url": "/"}, status_code=401)
    response.headers["WWW-Authenticate"] = 'Session realm="lex-dashboard"'
    return _no_store(response)


def _handoff_unavailable(conn, retry_after: int = 5) -> Response:
    """Auth (or the database) briefly unavailable: 503, retried, never a sign-out."""
    retry_after = max(1, int(retry_after or 5))
    if getattr(conn, "method", "GET") in ("GET", "HEAD") and _is_document_request(conn):
        target = html.escape(_clean_relative_path(conn), quote=True)
        body = (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta http-equiv=\"refresh\" content=\"{retry_after};url={target}\">"
            "<title>Temporarily unavailable</title></head>"
            f"<body><div style=\"{_PAGE_STYLE}\"><p>Sign-in is temporarily unavailable. "
            "This page retries automatically.</p></div></body></html>"
        )
        response: Response = HTMLResponse(body, status_code=503)
    else:
        response = JSONResponse({"error": "auth_unavailable", "retry_after": retry_after}, status_code=503)
    response.headers["Retry-After"] = str(retry_after)
    return _no_store(response)


def _start_handoff(request: Request, *, reason: str = "not_authenticated", stale_handle: bool = False) -> Response:
    """No usable handle: begin the hand-off (documents), or answer 401 (everything else)."""
    if request.method not in ("GET", "HEAD") or not _is_document_request(request):
        response = _handoff_unauthenticated(request, reason)
        if stale_handle:
            _clear_dash_cookie(response, request, dash_cookies.HANDLE_COOKIE)
        return response
    attempts = 0 if stale_handle else _handoff_attempts(request)
    if attempts >= HANDOFF_ATTEMPTS_BEFORE_BLOCKED and not _read_dash_cookie(request, dash_cookies.HANDLE_COOKIE):
        return _cookies_blocked_page(request)
    origin = _browser_origin(request)
    if origin is None:
        return _handoff_misconfigured_page(request, "this host is not one of the instance's dashboard hosts")
    app_origin = _handoff_app_origin(request)
    if app_origin is None:
        return _handoff_misconfigured_page(request, "the instance names no app host")
    items = _query_items_without(request, dash_cookies.LOOP_MARKER, "auth_token")
    items.append((dash_cookies.LOOP_MARKER, str(attempts + 1)))
    nonce = secrets.token_urlsafe(24)
    target = f"{app_origin}/oidc/handoff?" + urlencode(
        {"aud": "streamlit", "return": origin + _relative(request.url.path, items), "state": nonce}
    )
    response = _no_store(RedirectResponse(url=target, status_code=303))
    pending = [nonce] + dash_cookies.pending_nonces(_read_dash_cookie(request, dash_cookies.NONCE_COOKIE))
    _store_nonces(response, request, pending)
    if stale_handle:
        _clear_dash_cookie(response, request, dash_cookies.HANDLE_COOKIE)
    return response


def _handoff_identity_headers(auth: _HandoffAuth) -> List[Tuple[str, str]]:
    """Identity, and the access token when there is one -- nothing else goes upstream."""
    identity = auth.identity
    headers = [
        ("X-Streamlit-User-ID", identity.get("sub", "")),
        ("X-Streamlit-User-Email", identity.get("email", "")),
        ("X-Streamlit-User-Username", identity.get("preferred_username", "")),
        ("X-Streamlit-Auth-Method", "handoff"),
        ("X-Forwarded-User", identity.get("email", "")),
    ]
    if auth.token:
        headers.append(("Authorization", f"Bearer {auth.token}"))
    else:
        headers.append(("X-Lex-Auth-Degraded", auth.reason or "auth_unavailable"))
    return headers


async def _proxy_with_handoff(request: Request, auth: _HandoffAuth, handle: str) -> Response:
    """Forward a request the hand-off authenticated (``ok``, or ``unavailable`` = degraded)."""
    marker_or_token = any(
        request.query_params.get(name) is not None for name in (dash_cookies.LOOP_MARKER, "auth_token")
    )
    if marker_or_token and request.method in ("GET", "HEAD") and _is_document_request(request):
        # Signed in: the hand-off marker (and any auth_token an old frontend put on the URL) has
        # done its job; a clean URL keeps a later reload from reading it as a failed hand-off.
        response: Response = _no_store(RedirectResponse(url=_clean_relative_path(request), status_code=303))
    else:
        headers = _forwardable_request_headers(request)
        for name, value in _handoff_identity_headers(auth):
            headers[name] = value
        response, _ = await _send_upstream(request, headers)
    if auth.reset_cookie:
        _set_dash_cookie(response, request, dash_cookies.HANDLE_COOKIE, handle, dash_cookies.HANDLE_MAX_AGE)
    return response


async def auth_handoff(request: Request):
    """``/auth/handoff``: where Django sends the frame back with a code (or ``error``)."""
    if not HANDOFF_ENABLED:
        return Response(status_code=404)
    next_path = _safe_next_path(request.query_params.get("next")) or "/"
    state = request.query_params.get("state", "")
    nonces = dash_cookies.pending_nonces(_read_dash_cookie(request, dash_cookies.NONCE_COOKIE))
    matched = bool(state) and any(
        secrets.compare_digest(nonce.encode("utf-8"), state.encode("utf-8", "ignore")) for nonce in nonces
    )
    remaining = [nonce for nonce in nonces if nonce != state]

    error = request.query_params.get("error")
    if error:
        response = _sign_in_required_page(
            request, reason="login_required" if error == "login_required" else "handoff_failed", next_path=next_path
        )
        if matched:
            _store_nonces(response, request, remaining)
        return response

    code = request.query_params.get("code", "")
    if not matched or not code:
        # No nonce (blocked, or outlived by a slow sign-in) or a code meant for another browser:
        # it is never redeemed here. Back to the dashboard URL, whose hand-off count decides
        # between one more hand-off and the blocked-cookie page.
        return _no_store(RedirectResponse(url=next_path, status_code=303))

    origin = _browser_origin(request)
    if origin is None:
        return _handoff_misconfigured_page(request, "this host is not one of the instance's dashboard hosts")
    try:
        handle, failure = await _run_orm(_redeem_blocking, code, origin, state)
    except Exception as exc:
        logger.warning("Dashboard hand-off could not be redeemed (%s); retrying", type(exc).__name__)
        retry = _no_store(HTMLResponse(
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta http-equiv=\"refresh\" content=\"5;url={html.escape(_strip_params(next_path, (dash_cookies.LOOP_MARKER,)), quote=True)}\">"
            f"<title>Temporarily unavailable</title></head><body><div style=\"{_PAGE_STYLE}\">"
            "<p>Sign-in is temporarily unavailable. This page retries automatically.</p></div></body></html>",
            status_code=503,
        ))
        retry.headers["Retry-After"] = "5"
        return retry
    if not handle:
        logger.info("Dashboard hand-off refused: %s", failure)
        response = _sign_in_required_page(
            request, reason="session_ended" if failure == "session_ended" else "handoff_failed", next_path=next_path
        )
        _store_nonces(response, request, remaining)
        return response

    response = _no_store(RedirectResponse(url=next_path, status_code=303))
    _set_dash_cookie(response, request, dash_cookies.HANDLE_COOKIE, handle, dash_cookies.HANDLE_MAX_AGE)
    _store_nonces(response, request, remaining)
    return response


async def auth_required(request: Request):
    """``/auth/required``: the sign-in-required page, for a Streamlit page whose sign-in ended.

    The Streamlit app navigates its own document here when ``/auth/token`` says the sign-in is
    over, so the message to the frontend comes from the dashboard frame itself (the frontend
    only listens to that frame, not to the component frames inside it).
    """
    if not HANDOFF_ENABLED:
        return Response(status_code=404)
    next_path = _safe_next_path(request.query_params.get("next")) or "/"
    reason = request.query_params.get("reason") or "session_ended"
    return _sign_in_required_page(request, reason=reason, next_path=next_path)


async def auth_status(request: Request):
    """``/auth/status``: whether this browser's dashboard sign-in still stands.

    Asked by the watcher every hand-off page carries (``lex/streamlit_app.py``): every minute while
    the page is visible, and when it becomes visible again. A page whose socket dropped cannot
    learn any other way that its sign-in ended -- Streamlit retries a reconnect refused with 4401
    forever -- so on 401 the watcher sends the page to ``/auth/required``, which tells the
    frontend around it.

    From the database alone (one read; no Keycloak call, no refresh): 200 ``{"status": "ok"}``
    while the binding is live -- or, in dual mode, while a legacy session stands in for a dead
    handle; 401 ``session_expired`` once the binding is revoked, its sign-in ended or its user was
    deactivated; 401 ``not_authenticated`` without a handle; 503 when the database cannot be
    asked. It sets no cookie. 404 in legacy mode.
    """
    if not HANDOFF_ENABLED:
        return Response(status_code=404)
    handle = _read_dash_cookie(request, dash_cookies.HANDLE_COOKIE)
    standing = "none"
    if handle:
        try:
            standing = await _run_orm(_handle_standing_blocking, handle)
        except Exception as exc:  # the database itself could not be asked
            logger.warning("Dashboard sign-in could not be checked (%s); answering 503", type(exc).__name__)
            return _handoff_unavailable(request)
    if standing == "ok" or (LEGACY_ENABLED and "user" in request.session):
        return _no_store(JSONResponse({"status": "ok"}))
    return _handoff_unauthenticated(request, "session_expired" if handle else "not_authenticated")


async def _ws_refuse(websocket: WebSocket, code: int) -> None:
    """Accept, then close with ``code`` -- closing during the handshake reaches the browser as 1006."""
    raw = websocket.headers.get("sec-websocket-protocol")
    offered = [p.strip() for p in raw.split(",") if p.strip()] if raw else []
    with suppress(Exception):
        if websocket.client_state == WebSocketState.CONNECTING:
            await websocket.accept(subprotocol=offered[0] if offered else None)
        await websocket.close(code=code)


# -----------------------------------------------------------------------------
# Auth routes
# -----------------------------------------------------------------------------
async def login(request: Request):
    if not LEGACY_ENABLED:
        # handoff: the proxy runs no Keycloak sign-in of its own; the dashboard URL starts the
        # hand-off through the app, which signs in at top level when it has to.
        return _no_store(RedirectResponse(url=_safe_next_path(request.query_params.get("next")) or "/", status_code=303))
    # Stashed in the session rather than round-tripped through Keycloak: the
    # session is already the store authlib keeps the OIDC state in, so it
    # survives the redirect, and a value that never leaves the server cannot be
    # tampered with in transit. `_safe_next_path` still re-validates on the way
    # out, so a poisoned session cannot become an open redirect either.
    safe = _safe_next_path(request.query_params.get("next"))
    if safe:
        request.session[_NEXT_SESSION_KEY] = safe
    else:
        request.session.pop(_NEXT_SESSION_KEY, None)
    return await oauth.oidc.authorize_redirect(request, _callback_url(request))


async def auth_callback(request: Request):
    if not LEGACY_ENABLED:
        return Response(status_code=404)  # handoff: no Keycloak sign-in of the proxy's own
    token = await oauth.oidc.authorize_access_token(request)
    userinfo = token.get("userinfo") or await oauth.oidc.userinfo(token=token)

    sid = secrets.token_urlsafe(16)
    email = userinfo.get("email") or ""

    await _put_tokens(sid, email, token)

    # Read before the session is written, and re-validated on the way out.
    next_path = _safe_next_path(request.session.pop(_NEXT_SESSION_KEY, None)) or "/"
    request.session["user"] = {"email": email, "sid": sid}

    resp = RedirectResponse(url=next_path, status_code=303)

    # Optional short-lived access cookie
    if SET_ST_ACCESS_COOKIE and token.get("access_token"):
        resp.set_cookie(
            "st_access",
            token["access_token"],
            httponly=True,
            secure=SESSION_HTTPS_ONLY,
            samesite=SESSION_SAMESITE,
            max_age=ST_ACCESS_COOKIE_MAX_AGE,
            path="/",
        )
    return resp


async def _revoke_dashboard_handle(request: Request, reason: str) -> bool:
    """Revoke the binding behind this browser's handle cookie (if any). True when there was one."""
    handle = _read_dash_cookie(request, dash_cookies.HANDLE_COOKIE)
    if not handle:
        return False
    try:
        await _run_orm(_revoke_handle_blocking, handle, reason)
    except Exception as exc:  # the cookie is still removed from the browser below
        logger.warning("Dashboard binding could not be revoked (%s)", type(exc).__name__)
    return True


async def oauth2_logout(request: Request):
    """Sign out of this dashboard only. With the hand-off: the binding is revoked and its cookie
    removed, and a signed-out page is shown -- not "/", which would just hand off again."""
    had_handle = await _revoke_dashboard_handle(request, "logout") if HANDOFF_ENABLED else False
    if HANDOFF_ENABLED and (had_handle or not LEGACY_ENABLED):
        if LEGACY_ENABLED:
            sid = (request.session.get("user") or {}).get("sid")
            if sid:
                await _drop_tokens(sid)
            request.session.clear()
        resp = _signed_out_page(request)
        _clear_dash_cookie(resp, request, dash_cookies.HANDLE_COOKIE)
        resp.delete_cookie("st_access", path="/")
        return resp

    sid = (request.session.get("user") or {}).get("sid")
    if sid:
        await _drop_tokens(sid)
    request.session.clear()

    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie("st_access", path="/")
    return resp


def _sign_out_target(request: Request) -> Optional[str]:
    """Where the app's total logout returns to: ``rd`` when it is on a dashboard host, else this
    dashboard's root. ``None`` when this host is not a dashboard host or no app host is known."""
    origin = _browser_origin(request)
    if origin is None:
        return None
    rd = request.query_params.get("rd") or ""
    rd_origin = dash_sites.origin_of(rd)
    if rd_origin and (rd_origin == origin or dash_sites.is_dashboard_origin(rd_origin)):
        return rd
    relative = _safe_next_path(rd)
    return origin + (relative or "/")


async def oauth2_sign_out(request: Request):
    if HANDOFF_ENABLED:
        had_handle = await _revoke_dashboard_handle(request, "sign_out")
        if had_handle or not LEGACY_ENABLED:
            # Signing out is the app's job: its total logout ends the Django sign-in (with
            # id_token_hint) and Keycloak's, which ends every binding on it.
            if LEGACY_ENABLED:
                sid = (request.session.get("user") or {}).get("sid")
                if sid:
                    await _drop_tokens(sid)
                request.session.clear()
            back = _sign_out_target(request)
            app_origin = _handoff_app_origin(request) if back else None
            if not back or not app_origin:
                resp = _signed_out_page(request)
            else:
                resp = _no_store(RedirectResponse(
                    url=f"{app_origin}/oidc/total_logout?{urlencode({'next': back})}", status_code=303
                ))
            _clear_dash_cookie(resp, request, dash_cookies.HANDLE_COOKIE)
            resp.delete_cookie("st_access", path="/")
            return resp

    rd = _external_base_url(request)

    sid = (request.session.get("user") or {}).get("sid")
    t = await _get_tokens(sid) if sid else None
    id_token = (t or {}).get("id_token")

    if sid:
        await _drop_tokens(sid)
    request.session.clear()

    if not id_token:
        resp = RedirectResponse(url=rd, status_code=303)
        resp.delete_cookie("st_access", path="/")
        return resp

    endpoints = await _get_oidc_endpoints()
    end_session_endpoint = endpoints.get("end_session_endpoint") or (
        endpoints.get("issuer", "").rstrip("/") + "/protocol/openid-connect/logout"
    )

    qp = httpx.QueryParams(
        {
            "id_token_hint": id_token,
            "post_logout_redirect_uri": rd,
            "client_id": os.getenv("OIDC_RP_CLIENT_ID") or "",
        }
    )
    logout_url = f"{end_session_endpoint}?{qp}"
    return RedirectResponse(url=logout_url, status_code=302)


async def logout(request: Request):
    return await oauth2_logout(request)


async def internal_token(request: Request):
    """Hand the co-located Streamlit process a *currently valid* access token.

    Streamlit only ever sees the headers of the WebSocket handshake that opened
    its session, so a dashboard left open outlives the token it was started
    with. This is the pull side of that: same session, same refresh authority,
    but readable at any moment rather than only at connect time.

    ``_ensure_valid_access_token`` does the refreshing, so this endpoint returns
    a token that is valid *now* even when the stored one had already expired.
    Deliberately no refresh token in the response -- spending it stays this
    proxy's job alone, which is what keeps rotation single-writer.
    """
    supplied = request.headers.get(INTERNAL_AUTH_HEADER, "")
    # compare_digest rejects non-ASCII str outright, so compare bytes: a header
    # carrying a stray non-ASCII byte is a 403, not a 500.
    if not supplied or not secrets.compare_digest(
        supplied.encode("utf-8", "ignore"), INTERNAL_AUTH_SECRET.encode("utf-8")
    ):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if HANDOFF_ENABLED:
        # The hand-off's answers: 401 only when the sign-in is over (or there is none); 503
        # while Keycloak or the database cannot be asked -- the dashboard keeps going and
        # retries, and must not ask anyone to sign in again over a blip.
        handle = _read_handle_any(request)
        if handle:
            # A loopback answer cannot set a cookie: it never claims the daily re-set.
            auth = await _handoff_auth(request, handle=handle, can_set_cookie=False)
            if auth is not None and auth.token and auth.kind in ("ok", "unavailable"):
                return _no_store(JSONResponse({
                    "access_token": auth.token,
                    "expires_at": int(auth.expires_at or 0),
                    "email": auth.identity.get("email", ""),
                }))
            if auth is not None and auth.kind in ("unavailable", "error"):
                return _handoff_unavailable(request, auth.retry_after)
            if not LEGACY_ENABLED or "user" not in request.session:
                return _no_store(JSONResponse({"error": "session_expired"}, status_code=401))
            # dual: the handle names nothing live, but the legacy session the page was opened
            # with (Streamlit's frozen Cookie header still holds the dead handle) renews below.
        if not LEGACY_ENABLED:
            return _no_store(JSONResponse({"error": "not_authenticated"}, status_code=401))

    if "user" not in request.session:
        return JSONResponse({"error": "no_session"}, status_code=401)

    tokens = await _ensure_valid_access_token(request.session)
    if not tokens or not tokens.get("access_token"):
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    return JSONResponse(
        {
            "access_token": tokens.get("access_token"),
            "expires_at": int(tokens.get("expires_at") or 0),
            "email": tokens.get("email") or (request.session.get("user") or {}).get("email") or "",
        }
    )


# -----------------------------------------------------------------------------
# WebSocket proxy
# -----------------------------------------------------------------------------
def _ws_header_kwarg() -> Optional[str]:
    params = signature(ws_connect).parameters
    for name in ("extra_headers", "additional_headers", "headers"):
        if name in params:
            return name
    return None


WS_HEADER_KWARG = _ws_header_kwarg()
WS_HAS_ORIGIN = "origin" in signature(ws_connect).parameters
WS_HAS_SUBPROTOCOLS = "subprotocols" in signature(ws_connect).parameters
WS_HAS_PROXY = "proxy" in signature(ws_connect).parameters
WS_HAS_MAX_SIZE = "max_size" in signature(ws_connect).parameters


def _build_upstream_ws_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    resolved = dict(kwargs)
    if WS_HAS_PROXY and not UPSTREAM_USE_SYSTEM_PROXY:
        resolved["proxy"] = None
    if WS_HAS_MAX_SIZE:
        resolved["max_size"] = None
    return resolved


def _upstream_http_client_kwargs(timeout: httpx.Timeout) -> Dict[str, Any]:
    return {
        "follow_redirects": False,
        "timeout": timeout,
        "trust_env": UPSTREAM_USE_SYSTEM_PROXY,
        "limits": _UPSTREAM_LIMITS,
    }


def _upstream_ws_url_and_origin(client_ws_url: str) -> Tuple[str, str]:
    base = httpx.URL(UPSTREAM)
    ws_scheme = "wss" if base.scheme == "https" else "ws"
    target = base.copy_with(
        scheme=ws_scheme,
        path=httpx.URL(client_ws_url).path,
        query=httpx.URL(client_ws_url).query,
    )

    origin = f"{base.scheme}://{base.host}"
    if base.port:
        origin += f":{base.port}"
    return str(target), origin


async def ws_proxy(websocket: WebSocket):
    if not _ws_origin_allowed(websocket):
        # Cross-site WebSocket hijacking: another site's page would get a socket signed in as
        # the user. Refused before any credential is looked at -- 4403, after accept() with the
        # hand-off (the contract), during the handshake in legacy mode (as legacy closes 4401).
        logger.warning("WebSocket from a foreign origin refused (%r)", websocket.headers.get("origin"))
        if HANDOFF_ENABLED:
            await _ws_refuse(websocket, 4403)
            return
        with suppress(Exception):
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close(code=4403)
        return
    if HANDOFF_ENABLED:
        # The hand-off's handle, checked at connect: a fresh token of the sign-in (or, with
        # Keycloak unreachable, a cached one / identity only). An ended sign-in or a dead handle
        # is 4401 and a database outage 4503 -- both after accept(), so the browser sees them.
        # A handshake cannot set a cookie, so it never claims the handle's daily re-set.
        auth = await _handoff_auth(websocket, can_set_cookie=False)
        if auth is not None and auth.kind in ("ok", "unavailable"):
            await _ws_relay(websocket, _handoff_identity_headers(auth))
            return
        if auth is not None and auth.kind == "error":
            await _ws_refuse(websocket, 4503)
            return
        if not LEGACY_ENABLED:
            await _ws_refuse(websocket, 4401)
            return

    # Off-loop JWKS warmup: a blocking fetch here would stall every other
    # in-flight request and the existing WebSocket pumps.
    await _ensure_jwks_ready()

    scope_session = websocket.scope.get("session") or {}
    user_payload: Optional[Dict[str, Any]] = None
    auth_method = "none"

    # Same precedence as the HTTP path, and for the same reason: the handshake
    # is the *only* moment a long-lived Streamlit connection is handed a
    # credential, so it must be handed the renewable one whenever it exists.
    explicit_token = websocket.query_params.get("auth_token", "") or websocket.headers.get("authorization", "")
    if explicit_token:
        jwt_token = explicit_token[7:] if explicit_token.startswith("Bearer ") else explicit_token
        payload = validate_jwt_token(jwt_token)
        if payload:
            user_payload = _jwt_user_payload(jwt_token, payload)
            auth_method = "jwt"

    if not user_payload and "user" in scope_session:
        tokens = await _ensure_valid_access_token({"user": scope_session.get("user")})
        if tokens:
            user_payload = _session_user_payload(scope_session, tokens)
            auth_method = "session"

    if not user_payload and websocket.cookies.get("st_access"):
        payload = validate_jwt_token(websocket.cookies["st_access"])
        if payload:
            user_payload = _jwt_user_payload(websocket.cookies["st_access"], payload)
            auth_method = "jwt"

    if not user_payload:
        if HANDOFF_ENABLED:  # dual: neither credential -> the contract's accept-then-close
            await _ws_refuse(websocket, 4401)
            return
        with suppress(Exception):
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close(code=4401)
        return

    identity: List[Tuple[str, str]] = [
        ("X-Streamlit-User-ID", str(user_payload.get("sub") or "")),
        ("X-Streamlit-User-Email", user_payload.get("email") or ""),
        ("X-Streamlit-User-Username", user_payload.get("preferred_username") or ""),
        ("X-Streamlit-Auth-Method", auth_method),
        ("X-Forwarded-User", user_payload.get("email") or ""),
    ]
    # One copy of the access token, not three. `bearer_from_headers` in
    # lex/streamlit_app.py reads `Authorization` and always has, so nothing
    # upstream loses the credential; `X-Forwarded-Access-Token` and
    # `X-Streamlit-Access-Token` repeated the same bytes for no reader. The
    # handshake is where that mattered: three copies of a pre-LEX-898 token were
    # ~25 KB of headers on the one request that opens a connection for hours.
    if user_payload.get("access_token"):
        identity.append(("Authorization", f"Bearer {user_payload['access_token']}"))
    if user_payload.get("id_token"):
        identity.append(("X-Forwarded-Id-Token", user_payload["id_token"]))
    if user_payload.get("refresh_token"):
        identity.append(("X-Streamlit-Refresh-Token", user_payload["refresh_token"]))

    await _ws_relay(websocket, identity)


async def _ws_relay(websocket: WebSocket, identity: List[Tuple[str, str]]) -> None:
    """Open the upstream socket with the client's headers plus ``identity``, then pump both ways."""
    target_url, upstream_origin = _upstream_ws_url_and_origin(str(websocket.url))

    excluded = {
        "connection",
        "upgrade",
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-protocol",
        "te",
        "proxy-authorization",
        "proxy-authenticate",
        "keep-alive",
        "host",
        "origin",
        "authorization",
    } | _PROXY_OWNED_HEADERS
    fwd: List[Tuple[str, str]] = [(k, v) for k, v in websocket.headers.items() if k.lower() not in excluded]
    fwd.extend(identity)

    raw_subprotos = websocket.headers.get("sec-websocket-protocol")
    client_subprotocols = [p.strip() for p in raw_subprotos.split(",")] if raw_subprotos else []

    kwargs: Dict[str, Any] = {}
    if WS_HEADER_KWARG:
        if not WS_HAS_ORIGIN:
            fwd.append(("Origin", upstream_origin))
        kwargs[WS_HEADER_KWARG] = fwd
    if WS_HAS_ORIGIN:
        kwargs["origin"] = upstream_origin
    if WS_HAS_SUBPROTOCOLS and client_subprotocols:
        kwargs["subprotocols"] = client_subprotocols
    kwargs = _build_upstream_ws_kwargs(kwargs)

    try:
        async with ws_connect(target_url, **kwargs) as upstream:
            chosen = getattr(upstream, "subprotocol", None)
            if websocket.client_state == WebSocketState.CONNECTING:
                await websocket.accept(subprotocol=chosen)

            async def pump_client_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        t = msg.get("type")
                        if t == "websocket.disconnect":
                            break
                        if "text" in msg:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            async def pump_upstream_to_client():
                import websockets

                try:
                    while True:
                        data = await upstream.recv()
                        if isinstance(data, (bytes, bytearray)):
                            await websocket.send_bytes(data)
                        else:
                            await websocket.send_text(data)
                except websockets.exceptions.ConnectionClosed:
                    pass

            t1 = asyncio.create_task(pump_client_to_upstream())
            t2 = asyncio.create_task(pump_upstream_to_client())
            _, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t
    except Exception as exc:
        logger.warning("Upstream websocket connection failed for %s: %s", target_url, exc)
        raise
    finally:
        with suppress(Exception):
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close()


# -----------------------------------------------------------------------------
# Upstream HTTP: one pooled client, streamed straight through
# -----------------------------------------------------------------------------
# This used to open a fresh ``httpx.AsyncClient`` per request and buffer the
# whole upstream body before answering. On a Streamlit cold start that is 107+
# brand-new TCP connections to localhost inside one event loop, each one
# fully materialising a JS chunk in memory first. A single pooled client
# reuses connections; streaming means the first byte leaves as soon as it
# arrives.
_UPSTREAM_TIMEOUT = httpx.Timeout(float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "30")))

# Keep pooled connections for less time than the upstream will hold them open.
# Streamlit runs behind uvicorn, whose keep-alive timeout is 5s: past that it
# closes the socket, and a pooled connection handed out afterwards fails with
# `RemoteProtocolError: Server disconnected without sending a response` before
# a single byte is exchanged. Observed in production exactly 5s after the
# previous request. Expiring first turns that race into a fresh connection.
_UPSTREAM_KEEPALIVE_EXPIRY = float(os.getenv("UPSTREAM_KEEPALIVE_EXPIRY_SECONDS", "2"))
_UPSTREAM_LIMITS = httpx.Limits(
    max_connections=_env_int("UPSTREAM_MAX_CONNECTIONS", 100),
    max_keepalive_connections=_env_int("UPSTREAM_MAX_KEEPALIVE", 20),
    keepalive_expiry=_UPSTREAM_KEEPALIVE_EXPIRY,
)
_UPSTREAM_CLIENT: Optional[httpx.AsyncClient] = None
_UPSTREAM_CLIENT_LOOP: Optional[Any] = None

# Locks are created per event loop, not at import. An `asyncio.Lock()` built at
# module scope looks loop-agnostic because an *uncontended* acquire never binds
# it -- but the first CONTENDED acquire does, and a contended acquire on another
# loop then raises "is bound to a different event loop". That is: it breaks only
# under the concurrency the single-flight was written for.
_LOOP_LOCKS: "Dict[Tuple[int, str], asyncio.Lock]" = {}


def _loop_lock(name: str) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), name)
    lock = _LOOP_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOOP_LOCKS[key] = lock
    return lock


def _get_upstream_lock() -> asyncio.Lock:
    return _loop_lock("upstream_client")


async def _get_upstream_client() -> httpx.AsyncClient:
    """The process-wide upstream client, created on first use.

    Lazily rather than only in ``lifespan`` so the app still works when it is
    mounted or driven without a lifespan (Starlette's ``TestClient`` runs one
    only as a context manager). ``lifespan`` closes whatever this created.
    """
    global _UPSTREAM_CLIENT, _UPSTREAM_CLIENT_LOOP
    loop = asyncio.get_running_loop()

    # `is_closed` says nothing about WHICH loop the pooled connections belong
    # to. Handing back a client whose keep-alive sockets were opened on a loop
    # that has since finished raises "unable to perform operation on
    # <TCPTransport closed=True>; the handler is closed" -- and a module-level
    # singleton outlives any number of loops (two asyncio.run calls, two
    # TestClient lifecycles, a re-created loop in a host process).
    client = _UPSTREAM_CLIENT
    if client is not None and not client.is_closed and _UPSTREAM_CLIENT_LOOP is loop:
        return client

    if client is not None and _UPSTREAM_CLIENT_LOOP is not loop:
        # Belongs to a dead or foreign loop: abandon it rather than awaiting
        # aclose(), which would itself touch that loop's transports.
        _UPSTREAM_CLIENT = None

    async with _get_upstream_lock():
        if (
            _UPSTREAM_CLIENT is None
            or _UPSTREAM_CLIENT.is_closed
            or _UPSTREAM_CLIENT_LOOP is not loop
        ):
            _UPSTREAM_CLIENT = httpx.AsyncClient(
                **_upstream_http_client_kwargs(_UPSTREAM_TIMEOUT)
            )
            _UPSTREAM_CLIENT_LOOP = loop
        return _UPSTREAM_CLIENT


async def _warm_jwks_quietly() -> None:
    """Prime the JWKS cache without letting a failure escape into startup."""
    with suppress(Exception):
        await _ensure_jwks_ready()


@asynccontextmanager
async def lifespan(app: Starlette):
    """Own the pooled client's lifetime, and warm the JWKS before traffic."""
    # Warm the JWKS in the BACKGROUND, never awaited here. uvicorn runs lifespan
    # startup before it creates the listener, so awaiting a 10s httpx timeout
    # would keep the port closed for 10s -- while `lex streamlit` has already
    # pointed the browser at it. That reads to the user as the dashboard being
    # down, which is the failure this file is trying to remove, not add.
    warmup = asyncio.ensure_future(_warm_jwks_quietly())
    try:
        yield
    finally:
        if not warmup.done():
            warmup.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await warmup

        global _UPSTREAM_CLIENT
        client = _UPSTREAM_CLIENT
        _UPSTREAM_CLIENT = None
        if client is not None and not client.is_closed:
            with suppress(Exception):
                await client.aclose()
        _shutdown_orm_executor()


async def _upstream_send(
    method: str,
    url: httpx.URL,
    *,
    content: bytes,
    headers: Dict[str, str],
) -> httpx.Response:
    """Send one request upstream and return the response, body not yet read.

    The single seam tests patch to stand in for Streamlit. Kept deliberately
    narrow: everything about *which* headers get forwarded is decided by the
    callers, so a stub here observes exactly the proxy's forwarding decisions.
    """
    client = await _get_upstream_client()

    # One retry, and only for failures that happen before any response byte
    # exists. A pooled connection the upstream has already closed fails this
    # way, and the request is safe to replay: `content` is bytes rather than a
    # consumed stream, and nothing has been sent to our own client yet. A
    # failure *during* the body is a different matter -- the response head has
    # gone out, so it cannot be retried, and `_iter_upstream` raises instead.
    last_error: Optional[Exception] = None
    for attempt in (1, 2):
        request = client.build_request(method, url, content=content, headers=headers)
        try:
            return await client.send(request, stream=True)
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as exc:
            last_error = exc
            if attempt == 2:
                break
            logger.info(
                "Upstream connection failed before a response (%s: %s); retrying once on a "
                "fresh connection", type(exc).__name__, exc,
            )

    raise last_error  # type: ignore[misc]  # unreachable unless both attempts failed


async def _iter_upstream(upstream_resp: httpx.Response):
    """Yield the upstream body, whether it is streaming or already buffered.

    The streaming path is the production one and yields **raw** (still encoded)
    bytes, which is what lets ``Content-Encoding`` be forwarded untouched. An
    already-consumed response can only offer ``.content``, which httpx has
    *decoded* -- so ``_build_proxied_response`` drops the encoding header in
    that case, keeping the invariant that the bytes always match the headers
    describing them.

    A failure part-way through the body cannot become an HTTP status: the
    response head has already been sent. Truncating silently is the worst
    option, because a half-delivered Vite chunk surfaces as a ``SyntaxError``
    or ``Failed to fetch dynamically imported module`` -- indistinguishable
    from the bug this module exists to fix, and not retryable. Raising instead
    makes the server drop the connection, which the browser *does* report as a
    network error.
    """
    if upstream_resp.is_stream_consumed:
        # Never yield an empty chunk: for a 304/204/HEAD it would reach
        # GZipMiddleware as a body, skip its `minimum_size` guard, and attach a
        # gzip header (and 10 bytes) to a response that must have none --
        # uvicorn then raises "Response content longer than Content-Length".
        # Real ``aiter_raw()`` yields zero chunks there; this branch has to
        # match, because it is the branch every test double takes.
        if upstream_resp.content:
            yield upstream_resp.content
        return

    try:
        async for chunk in upstream_resp.aiter_raw():
            yield chunk
    except Exception:
        # `.request` itself raises when unset, and a diagnostic that can raise
        # would replace the real error with its own.
        with suppress(Exception):
            logger.warning(
                "Upstream body failed mid-stream; dropping the connection so the client "
                "sees a network error rather than a truncated 200"
            )
        raise


#: Response headers the ASGI server sets itself. Forwarding the upstream's copy
#: makes uvicorn's copy a DUPLICATE -- and `Date` and `Server` are singleton
#: fields (RFC 9110 5.5.2). HTTP/1.1 clients tolerate a repeat; HTTP/2 does not,
#: so an intermediary terminating h2 rejects the message and resets the stream.
#: That reaches the browser as `net::ERR_HTTP2_PROTOCOL_ERROR` against a 200 --
#: a response that started fine and then died, which is exactly how a download
#: stuck at 0 bytes was reported.
_SERVER_OWNED_RESPONSE_HEADERS = frozenset({"date", "server"})

#: Dropped from both directions: meaningful only for a single hop.
_HOP_BY_HOP = frozenset({
    "host",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "authorization",
})


#: Headers only this proxy may set on the way to Streamlit: identity and tokens. A client copy
#: is dropped in every mode. Kept, it reached Streamlit *ahead* of the proxy's own value, and
#: Streamlit's ``st.context.headers`` answers the first of a repeated header -- so any signed-in
#: client could have named another user's identity, or a token of its choosing (BUG-037).
_PROXY_OWNED_HEADERS = frozenset({
    "x-streamlit-user-id",
    "x-streamlit-user-email",
    "x-streamlit-user-username",
    "x-streamlit-auth-method",
    "x-streamlit-access-token",
    "x-streamlit-refresh-token",
    "x-forwarded-user",
    "x-forwarded-email",
    "x-forwarded-preferred-username",
    "x-forwarded-access-token",
    "x-forwarded-id-token",
    "x-auth-request-user",
    "x-auth-request-email",
    "x-auth-request-access-token",
    "x-lex-auth-degraded",
})

#: Request-only. httpx recomputes Content-Length from the body we hand it, and a
#: forwarded value beats httpx's own -- so a mismatch becomes a
#: LocalProtocolError instead of a request. Deliberately NOT dropped from
#: responses: the raw relay preserves the body exactly, so the upstream's length
#: is still correct, and a sized response is what lets the browser draw a
#: progress bar instead of sitting at 0 bytes.
_REQUEST_DROP = _HOP_BY_HOP | {"content-length"} | _PROXY_OWNED_HEADERS


def _forwardable_request_headers(request: Request) -> Dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in _REQUEST_DROP}


def _build_proxied_response(request: Request, upstream_resp: httpx.Response) -> Response:
    """Turn an upstream response into ours, preserving the bytes exactly.

    ``Content-Encoding`` is deliberately **kept** and the body streamed with
    ``aiter_raw()``, i.e. still encoded. The old code read the decoded body and
    then had to strip the header to stay honest -- which silently disabled
    compression for everything Streamlit serves (measured 3.4x on its bundle).
    Passing the encoded bytes through untouched costs no CPU and keeps the
    saving. ``GZipMiddleware`` sees the header and leaves such responses alone.
    """
    # Content-Length is KEPT on the streaming path. `aiter_raw()` relays the
    # upstream's bytes unchanged, so its length is still exactly right -- and a
    # sized response avoids `Transfer-Encoding: chunked`, which HTTP/2 forbids
    # outright (RFC 9113 8.2.2) and which an h2 intermediary has to rewrite.
    # Dropping it was what turned every proxied response into a chunked one.
    drop = _HOP_BY_HOP | _SERVER_OWNED_RESPONSE_HEADERS
    if upstream_resp.is_stream_consumed:
        # `.content` is decoded and may have been re-read, so neither the
        # upstream's encoding nor its length describes what we are about to
        # send. See _iter_upstream.
        drop = drop | {"content-encoding", "content-length"}
    resp_headers = [
        (k, v)
        for k, v in upstream_resp.headers.multi_items()
        if k.lower() not in drop and k.lower() != "set-cookie"
    ]

    response: Response = StreamingResponse(
        _iter_upstream(upstream_resp),
        status_code=upstream_resp.status_code,
        headers=dict(resp_headers),
        background=BackgroundTask(upstream_resp.aclose),
    )

    # Streamlit sets its own cookies (XSRF); preserve every one, not just the last.
    for cookie in upstream_resp.headers.get_list("set-cookie"):
        response.headers.append("set-cookie", cookie)

    # The embedded iframe's document URL carries ?auth_token=<jwt>. Without
    # this, every subresource that document requests sends it upstream in a
    # `Referer` header, and any outbound link leaks it to a third party.
    response.headers.setdefault("Referrer-Policy", "no-referrer")

    return response


async def public_proxy(request: Request):
    """Proxy a PUBLIC_PROXY_PATHS request upstream with no credential at all.

    Only ``/_stcore/health`` and ``/_stcore/host-config`` route here. Neither
    reads a session: health is for probes, and host-config is fetched during
    client bootstrap -- before the WebSocket exists, so before any credential
    could have been established -- and returns client feature flags only.
    No identity headers are injected, so nothing downstream can mistake one of
    these for an authenticated call.
    """
    if request.url.path not in PUBLIC_PROXY_PATHS:  # pragma: no cover - routing guarantees this
        return _unauthenticated_response(request)

    url = httpx.URL(UPSTREAM + request.url.path)
    if request.url.query:
        url = url.copy_with(query=request.url.query.encode("utf-8"))

    # Forward the real body, and let `_forwardable_request_headers` drop
    # Content-Length. Sending content=b"" while forwarding the client's
    # Content-Length made httpx raise LocalProtocolError ("Too little data for
    # declared Content-Length") for any probe that carried a body -- which is
    # neither ConnectError nor TimeoutException, so the readiness endpoint
    # answered 500.
    body = await request.body()

    try:
        upstream_resp = await _upstream_send(
            request.method, url, content=body, headers=_forwardable_request_headers(request)
        )
    except httpx.ConnectError:
        return JSONResponse(
            {"error": f"Upstream unavailable: {UPSTREAM}. Streamlit may still be starting."},
            status_code=503,
        )
    except httpx.TimeoutException:
        return JSONResponse({"error": f"Upstream timeout: {UPSTREAM}"}, status_code=504)
    except httpx.HTTPError as exc:
        # Anything else httpx can raise before a response exists -- notably
        # RemoteProtocolError from a connection the upstream closed. Left
        # unhandled it escaped as an unhandled ASGI exception, which reaches
        # the browser as a dropped request: a failed /media download, or an
        # iframe document that never loads.
        logger.warning("Upstream request to %s failed: %s: %s", UPSTREAM, type(exc).__name__, exc)
        return JSONResponse(
            {"error": f"Upstream request failed: {type(exc).__name__}"},
            status_code=502,
        )

    return _build_proxied_response(request, upstream_resp)


# -----------------------------------------------------------------------------
# HTTP proxy
# -----------------------------------------------------------------------------
async def _send_upstream(request: Request, fwd_headers: Dict[str, str]) -> Tuple[Response, bool]:
    """Send this request upstream with ``fwd_headers``; (response, whether the upstream answered)."""
    url = httpx.URL(UPSTREAM + request.url.path)
    if request.url.query:
        url = url.copy_with(query=request.url.query.encode("utf-8"))

    body = await request.body()

    try:
        upstream_resp = await _upstream_send(request.method, url, content=body, headers=fwd_headers)
    except httpx.ConnectError:
        return JSONResponse(
            {"error": f"Upstream unavailable: {UPSTREAM}. Streamlit may still be starting."},
            status_code=503,
        ), False
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": f"Upstream timeout: {UPSTREAM}"},
            status_code=504,
        ), False
    except httpx.HTTPError as exc:
        # Anything else httpx can raise before a response exists -- notably
        # RemoteProtocolError from a connection the upstream closed. Left
        # unhandled it escaped as an unhandled ASGI exception, which reaches
        # the browser as a dropped request: a failed /media download, or an
        # iframe document that never loads.
        logger.warning("Upstream request to %s failed: %s: %s", UPSTREAM, type(exc).__name__, exc)
        return JSONResponse(
            {"error": f"Upstream request failed: {type(exc).__name__}"},
            status_code=502,
        ), False

    return _build_proxied_response(request, upstream_resp), True


async def proxy(request: Request):
    stale_handle = False
    if HANDOFF_ENABLED:
        # The hand-off first: a live handle forwards the sign-in's fresh access token (or,
        # with Keycloak unreachable, a cached one / identity only, marked degraded).
        handle = _read_dash_cookie(request, dash_cookies.HANDLE_COOKIE)
        auth = await _handoff_auth(request, handle=handle)
        if auth is not None and auth.kind in ("ok", "unavailable"):
            return await _proxy_with_handoff(request, auth, handle)
        if auth is not None and auth.kind == "error":
            return _handoff_unavailable(request, auth.retry_after)
        stale_handle = auth is not None  # a handle that names nothing live
        if not LEGACY_ENABLED:
            # handoff: no handle, or a dead one -> hand off (documents) or 401.
            return _start_handoff(request, reason="session_expired" if stale_handle else "not_authenticated",
                                  stale_handle=stale_handle)
        # dual: on to the legacy credentials -- a dead handle never stands in their way, and
        # whatever the legacy path answers clears it; with none, the hand-off starts below.

    # Off-loop JWKS warmup before any synchronous validate_jwt_token below.
    await _ensure_jwks_ready()

    user_payload: Optional[Dict[str, Any]] = None
    auth_method = "none"

    # Track where token came from (important to decide whether to set cookie)
    token_source = "none"

    # 1) Explicit ``auth_token`` handoff (query param or header).
    #    Highest precedence: it is the freshest credential the caller can
    #    present, and re-sourcing the iframe with a new one is how the embedded
    #    dashboard renews itself.
    jwt_token = ""
    jwt_payload: Optional[Dict[str, Any]] = None
    explicit_token = (
        request.query_params.get("auth_token", "")
        or request.headers.get("auth_token", "")
        or request.headers.get("authorization", "")
    )
    if explicit_token:
        token_source = "query" if request.query_params.get("auth_token") else "header"
        jwt_token = explicit_token[7:] if explicit_token.startswith("Bearer ") else explicit_token
        jwt_payload = validate_jwt_token(jwt_token)
        if jwt_payload:
            user_payload = _jwt_user_payload(jwt_token, jwt_payload)
            auth_method = "jwt"

            # Persist this one-off auth_token into a real session/token-store entry
            await _persist_jwt_to_session_if_needed(request, jwt_token, jwt_payload, token_source)

    # 2) Session auth (cookie from SessionMiddleware -> server-side tokens).
    #    Checked BEFORE the ``st_access`` cookie, and that ordering is the whole
    #    point: the session is the only credential that carries a refresh token.
    #    ``auth_callback`` sets ``st_access`` on every login, so consulting the
    #    cookie first classified a freshly logged-in user as ``jwt`` with no
    #    refresh token at all -- an unrenewable credential that expired minutes
    #    later and took the dashboard down with it.
    if not user_payload and "user" in request.session:
        tokens = await _ensure_valid_access_token(request.session)
        if tokens:
            user_payload = _session_user_payload(request.session, tokens)
            auth_method = "session"

    # 3) ``st_access`` cookie: last resort, for the embedded path where the
    #    frontend cannot repeat ``auth_token`` on follow-up requests and no
    #    server-side session was established.
    if not user_payload and request.cookies.get("st_access"):
        token_source = "cookie"
        jwt_token = request.cookies["st_access"]
        jwt_payload = validate_jwt_token(jwt_token)
        if jwt_payload:
            user_payload = _jwt_user_payload(jwt_token, jwt_payload)
            auth_method = "jwt"
            await _persist_jwt_to_session_if_needed(request, jwt_token, jwt_payload, token_source)

    # 3b) The bootstrap ``auth_token`` has now been persisted to a session, so
    #     the credential no longer needs to be in the URL -- where it sits in
    #     the address bar, in history, and in the `Referer` of every
    #     subresource. Redirect to the same place without it.
    #
    #     Only for document navigations: an XHR is not in the address bar, and
    #     redirecting one would surprise its caller. Only when a session was
    #     actually established, so we never strip the only credential we have.
    #     No loop is possible -- the target has no ``auth_token``, so a second
    #     pass cannot re-enter this branch.
    if (
        STRIP_AUTH_TOKEN_FROM_URL
        and user_payload
        and request.query_params.get("auth_token")
        and request.method in ("GET", "HEAD")
        and _is_document_request(request)
        and (request.session.get("user") or {}).get("sid")
    ):
        stripped = RedirectResponse(url=_current_relative_path(request), status_code=303)
        stripped.headers["Referrer-Policy"] = "no-referrer"
        # The redirect must hand over EVERY credential the response it replaces
        # would have. Without st_access it delivers strictly fewer: where the
        # SessionMiddleware cookie is unusable in the frame -- Safari ITP, or any
        # third-party-cookie blocking, which reaches even SameSite=None -- the
        # follow-up request would arrive with no auth_token (we just stripped
        # it), no session cookie and no st_access, and get a frame breakout. The
        # proxy would have discarded the one credential that still worked.
        if SET_ST_ACCESS_COOKIE and jwt_token:
            stripped.set_cookie(
                "st_access",
                jwt_token,
                httponly=True,
                secure=SESSION_HTTPS_ONLY,
                samesite=SESSION_SAMESITE,
                max_age=ST_ACCESS_COOKIE_MAX_AGE,
                path="/",
            )
        if stale_handle:
            _clear_dash_cookie(stripped, request, dash_cookies.HANDLE_COOKIE)
        return stripped

    # 4) Deny
    if not user_payload:
        if HANDOFF_ENABLED:
            # dual: no credential of either kind -> the hand-off, never the proxy's own
            # Keycloak sign-in (which would have to frame Keycloak, or leave the frame).
            return _start_handoff(request, reason="session_expired" if stale_handle else "not_authenticated",
                                  stale_handle=stale_handle)
        # Never redirect an iframe document load to the IdP: Keycloak's login page
        # sets frame-ancestors 'self' and the browser shows "refused to connect".
        # Break out to a top-level login instead (see _unauthenticated_response).
        return _unauthenticated_response(request)

    fwd_headers = _forwardable_request_headers(request)

    fwd_headers["X-Streamlit-User-ID"] = str(user_payload.get("sub") or "")
    fwd_headers["X-Streamlit-User-Email"] = user_payload.get("email") or ""
    fwd_headers["X-Streamlit-User-Username"] = user_payload.get("preferred_username") or ""
    fwd_headers["X-Streamlit-Auth-Method"] = auth_method
    fwd_headers["X-Forwarded-User"] = user_payload.get("email") or ""

    # Forward the access token if present (either from JWT auth or session), in
    # one header -- see the WebSocket path above for why three became one.
    #
    # Nothing has to clear the caller's own `Authorization` first: `_HOP_BY_HOP`
    # already drops it, as the WebSocket path's `excluded` does. That was
    # housekeeping while a third header outranked `Authorization` in the
    # upstream's reader; now that this is the only copy of the credential it is
    # what keeps a caller from sending a second one alongside it -- Starlette
    # hands header names lower-cased, so the two would be different dict keys
    # and both would go, reaching the upstream joined by a comma into a value
    # that parses as neither.
    if user_payload.get("access_token"):
        fwd_headers["Authorization"] = f"Bearer {user_payload['access_token']}"

    if user_payload.get("id_token"):
        fwd_headers["X-Forwarded-Id-Token"] = user_payload["id_token"]
    if user_payload.get("refresh_token"):
        fwd_headers["X-Streamlit-Refresh-Token"] = user_payload["refresh_token"]

    response, reached = await _send_upstream(request, fwd_headers)
    if stale_handle:
        # dual: the legacy session served this request; the dead handle must not shadow it again.
        _clear_dash_cookie(response, request, dash_cookies.HANDLE_COOKIE)
    if not reached:
        return response

    # ✅ Also set a short-lived st_access cookie when token came from query/header
    # (helps WS + follow-up requests where query param isn't repeated)
    if SET_ST_ACCESS_COOKIE and auth_method == "jwt" and token_source in ("query", "header") and jwt_token:
        response.set_cookie(
            "st_access",
            jwt_token,
            httponly=True,
            secure=SESSION_HTTPS_ONLY,
            samesite=SESSION_SAMESITE,
            max_age=ST_ACCESS_COOKIE_MAX_AGE,
            path="/",
        )

    return response


# -----------------------------------------------------------------------------
# Renewal delivery: a fresh bootstrap credential, without touching the iframe
# -----------------------------------------------------------------------------
# ``_persist_jwt_to_session_if_needed`` already adopts a strictly-newer token,
# and its comment names the intended source: "renewal can only arrive as a
# fresh ``auth_token`` from the frontend". The problem was that the only way to
# *present* one was the iframe URL -- and re-sourcing the iframe loads a new
# document, which is a new Streamlit session with an empty ``st.session_state``.
# So the renewal mechanism and the thing it was protecting were mutually
# exclusive: deliver the token and lose the state, or keep the state and let
# the session die at the access token's own lifetime.
#
# This is the missing third option. The shell POSTs the renewed token here, the
# proxy adopts it into the existing session, and the iframe is never touched.
# The token travels in a request body rather than a URL, so unlike the
# bootstrap it never reaches the address bar, history, or a ``Referer``.
#
# Why this is not a new trust boundary: the token is validated against
# Keycloak's JWKS exactly as every other credential is, so a caller cannot
# invent one -- presenting a genuine Keycloak-signed token for a user means
# already holding that user's credential. And adoption is strictly-newer only,
# so the worst a replay achieves is re-installing a token the session already
# had.

#: Localhost origins the React shell runs on in development, mirroring the set
#: Django already trusts in ``settings.CORS_ORIGIN_WHITELIST``.
_DEV_FRONTEND_ORIGINS = frozenset({
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
})


def _frontend_origins() -> frozenset:
    """Origins permitted to hand this proxy a renewed credential.

    Derived from ``DOMAIN_HOSTED`` by default, deliberately: it is the instance's
    own hostname, it already means "where the frontend is" (Django builds
    ``CORS_ORIGIN_WHITELIST`` from it the same way), and
    ``lex/lex_app/settings.py`` *refuses to start* without it whenever
    ``DEPLOYMENT_ENVIRONMENT`` is set. So in any real deployment it is
    guaranteed present, and this endpoint needs no new variable to be set for
    renewal to work -- which matters because the failure mode of an unset
    allowlist is a dashboard that renews for five minutes and then quietly
    stops.

    ``REACT_APP_URL`` / ``LEX_FRONTEND_URL`` remain an explicit override, for
    the case where the shell is not on the instance hostname. They are the same
    pair ``lex_view()`` resolves for the reverse direction.
    """
    origins = set()

    override = (os.getenv("REACT_APP_URL") or os.getenv("LEX_FRONTEND_URL") or "").strip().rstrip("/")
    if override:
        # Accept a bare host here too, so the two spellings behave alike.
        origins.add(override if "://" in override else f"https://{override}")

    # Every hostname the shell is served on, not just DOMAIN_HOSTED: once a
    # customer-owned domain is attached, the same shell answers on the platform
    # hostname AND on the customer's, and a dashboard framed by the one that is
    # not DOMAIN_HOSTED would be refused its renewed token -- which looks like
    # a dashboard that works for five minutes and then quietly stops.
    # `public_hosts` is DOMAIN_HOSTED alone when nothing else is configured,
    # which is the behaviour this replaces.
    for app_host in lex_hosts.public_hosts("app"):
        if app_host and not app_host.startswith("localhost"):
            origins.add(f"https://{app_host}")

    # The standalone (non-embedded) dashboard talking to itself needs no entry.
    if PUBLIC_URL:
        origins.add(PUBLIC_URL)
    # ...and neither does one reached on an attached hostname of its own.
    origins |= {f"https://{host}" for host in PUBLIC_HOSTS if not host.startswith("localhost")}

    # Development: DOMAIN_HOSTED is absent or "localhost" while the shell runs
    # on another port, which is a different *origin* and so still needs CORS.
    if not PUBLIC_IS_HTTPS:
        origins |= _DEV_FRONTEND_ORIGINS

    return frozenset(origins)


FRONTEND_ORIGINS = _frontend_origins()


def _allowed_origin(request: Request) -> Optional[str]:
    """The request's ``Origin`` if it is permitted to adopt, else ``None``.

    Never ``*``: this endpoint is called with credentials, so a wildcard is both
    refused by browsers alongside ``Allow-Credentials`` and wrong on the merits.
    """
    origin = request.headers.get("origin", "").strip().rstrip("/")
    if not origin:
        return None
    return origin if origin in FRONTEND_ORIGINS else None


def _cors(response: Response, origin: str) -> Response:
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Vary"] = "Origin"
    return response


async def adopt_token(request: Request):
    """Adopt a renewed access token into the caller's existing proxy session.

    ``POST {"auth_token": "<jwt>"}`` with ``credentials: 'include'``. Returns
    204 when the token was taken up, 200 with ``{"adopted": false}`` when the
    stored one is already at least as fresh -- both are successes from the
    caller's point of view, and it must not treat the second as an error and
    retry in a loop.
    """
    if request.method == "OPTIONS":
        origin = _allowed_origin(request)
        if not origin:
            return Response(status_code=403)
        preflight = Response(status_code=204)
        preflight.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        preflight.headers["Access-Control-Allow-Headers"] = "content-type"
        preflight.headers["Access-Control-Max-Age"] = "600"
        return _cors(preflight, origin)

    origin = _allowed_origin(request)
    if not origin:
        # No usable Origin means either a same-site call we cannot attribute or
        # a cross-origin one we do not trust. Refuse rather than guess.
        return JSONResponse({"error": "origin_not_allowed"}, status_code=403)

    if not LEGACY_ENABLED:
        # handoff: a no-op. Dashboards renew through their binding, and the body is never read,
        # so a token posted here goes nowhere. Answered as "not adopted" -- a success to an old
        # frontend, which must not retry in a loop.
        return _cors(_no_store(JSONResponse({"adopted": False}, status_code=200)), origin)

    await _ensure_jwks_ready()

    try:
        body = await request.json()
    except Exception:
        return _cors(JSONResponse({"error": "invalid_body"}, status_code=400), origin)

    token = (body or {}).get("auth_token") or ""
    if not isinstance(token, str) or not token:
        return _cors(JSONResponse({"error": "missing_auth_token"}, status_code=400), origin)

    payload = validate_jwt_token(token)
    if not payload:
        return _cors(JSONResponse({"error": "invalid_token"}, status_code=401), origin)

    before = (request.session.get("user") or {}).get("sid")
    await _persist_jwt_to_session_if_needed(request, token, payload, "header")
    after = (request.session.get("user") or {}).get("sid")

    # A changed sid means the token was strictly newer and superseded the stored
    # one; an unchanged sid means the store already held something at least as
    # fresh. The caller needs to distinguish neither, but saying so keeps the
    # endpoint debuggable from a network log.
    adopted = before != after
    response = JSONResponse({"adopted": adopted}, status_code=200)

    # Refresh the short-lived access cookie too, so follow-up requests that
    # cannot repeat the body -- and the next WebSocket handshake -- see the new
    # credential rather than the one that is about to expire.
    if SET_ST_ACCESS_COOKIE:
        response.set_cookie(
            "st_access",
            token,
            httponly=True,
            secure=SESSION_HTTPS_ONLY,
            samesite=SESSION_SAMESITE,
            max_age=ST_ACCESS_COOKIE_MAX_AGE,
            path="/",
        )
    response.headers["Referrer-Policy"] = "no-referrer"
    return _cors(response, origin)


# -----------------------------------------------------------------------------
# Public assets: Streamlit's own static bundle, served here and never gated
# -----------------------------------------------------------------------------
# Streamlit's frontend is code-split: 1.61 ships 365 JS chunks and 4 CSS files,
# and ``index.html`` names 107 of them in eager ``<link rel="modulepreload">``
# tags. Behind a blanket auth wall that arithmetic is the bug report:
#
#   * one credential-less moment produces >100 simultaneous 401s (the flood in
#     the Streamlit run log);
#   * a *lazily* imported chunk that 401s surfaces in the browser as
#     ``TypeError: Failed to fetch dynamically imported module`` or
#     ``Unable to preload CSS for ...`` -- Vite's dynamic ``import()`` has no
#     other vocabulary for an HTTP error, so an auth failure is reported to the
#     customer as a type error in code they never wrote;
#   * and every one of those chunks took a separate upstream round trip whose
#     ``Content-Encoding`` we then stripped, inflating the eager set from
#     ~520 KB to 1.77 MB.
#
# So the bundle is served from this process instead of proxied. It is
# package-shipped content from the installed streamlit wheel -- byte-identical
# for every install, carrying no tenant data and no identity -- so gating it
# bought nothing and cost all of the above. Serving it locally also removes 107
# upstream hops from first paint, which matters more than usual here because
# ``lex streamlit`` runs this proxy in a thread inside the Streamlit process,
# sharing one GIL with the script runner.
#
# The security boundary does not move: only this bundle and the two bootstrap
# endpoints below are public. Everything else -- ``/``, ``/media/**``,
# uploads, the WebSocket -- still goes through ``proxy``/``ws_proxy``.

#: Paths proxied to Streamlit *without* authentication. Deliberately tiny.
#:  - ``/_stcore/health``      liveness/readiness probes, which have no session
#:  - ``/_stcore/host-config`` fetched during client bootstrap, before the
#:    WebSocket opens. Returns client feature flags only (``allowedOrigins``,
#:    ``useExternalAuthToken``, ...) -- no identity, no tenant data.
PUBLIC_PROXY_PATHS = frozenset({"/_stcore/health", "/_stcore/host-config"})

#: Streamlit's ``--server.baseUrlPath``, normalised to ``""`` or ``"/name"``.
#: ``lex streamlit`` passes the flag through from ``ctx.args``, so the proxy has
#: to be told separately -- it cannot read Streamlit's own config from here.
def _normalise_base_url_path(raw: str) -> str:
    trimmed = (raw or "").strip().strip("/")
    return f"/{trimmed}" if trimmed else ""


STREAMLIT_BASE_URL_PATH = _normalise_base_url_path(
    os.getenv("LEX_STREAMLIT_BASE_URL_PATH") or os.getenv("STREAMLIT_SERVER_BASE_URL_PATH") or ""
)

#: Set false to proxy assets upstream instead of serving them here. The escape
#: hatch for a split deployment, where the bundle in this interpreter is not
#: necessarily the one the upstream's index.html names. Costs the 401-flood
#: protection and the compression, so it is opt-out, not a default.
SERVE_STATIC_LOCALLY = _env_bool("LEX_SERVE_STATIC_LOCALLY", True)

#: One year, matching Streamlit's own contract for content-addressed files.
STATIC_ASSET_MAX_AGE = _env_int("STATIC_ASSET_MAX_AGE", 365 * 24 * 60 * 60)

STATIC_GZIP_MIN_SIZE = _env_int("STATIC_GZIP_MIN_SIZE", 500)
# 6, not zlib's 9. Measured over Streamlit 1.61's 365 shipped JS chunks:
# level 1 = 2.89x in 138 ms, level 6 = 3.33x in 346 ms, level 9 = 3.34x in
# 450 ms. Level 9 buys 0.3% more compression for 30% more CPU, and this
# process shares a GIL with the Streamlit script runner, so 6 is the knee.
STATIC_GZIP_LEVEL = _env_int("STATIC_GZIP_LEVEL", 6)


def _streamlit_static_dir() -> Optional[str]:
    """Absolute path of the installed Streamlit wheel's ``static`` directory.

    Resolved through Streamlit's own ``file_util.get_static_dir()`` rather than
    a hand-built path, and read from *this* interpreter -- the same one that
    runs the Streamlit server -- so the hashed filenames can never drift out of
    step with the ``index.html`` that references them.
    """
    try:
        from streamlit import file_util

        static_dir = file_util.get_static_dir()
    except Exception as exc:  # pragma: no cover - streamlit always present in prod
        logger.warning("Could not locate Streamlit's static directory: %s", exc)
        return None

    if not os.path.isdir(static_dir):
        logger.warning("Streamlit's static directory does not exist: %s", static_dir)
        return None
    return static_dir


def _build_static_routes() -> List[Any]:
    """Mounts for the Streamlit bundle, or ``[]`` if it cannot be located.

    Returning ``[]`` degrades to the previous behaviour -- the catch-all proxies
    ``/static`` upstream, authenticated -- rather than breaking startup. A
    missing bundle is logged loudly by ``_assert_static_bundle_present()``.
    """
    if not SERVE_STATIC_LOCALLY:
        logger.warning(
            "LEX_SERVE_STATIC_LOCALLY=false: Streamlit's asset bundle will be proxied "
            "upstream behind authentication. Cold starts will be slower and an expired "
            "credential will surface as 'Failed to fetch dynamically imported module'."
        )
        return []

    static_dir = _streamlit_static_dir()
    if not static_dir:
        return []

    from starlette.responses import FileResponse
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles

    class _PublicStreamlitStatic(StaticFiles):
        """``StaticFiles`` with Streamlit's cache contract.

        Mirrors ``_apply_cache_headers`` in Streamlit's own
        ``starlette_static_routes.py``: hashed bundles are immutable for a year,
        HTML and ``manifest.json`` are never cached. Getting this wrong is not
        cosmetic -- a cached ``index.html`` referencing chunk hashes from a
        previous deploy produces exactly the dynamic-import TypeErrors this
        change exists to remove.
        """

        async def get_response(self, path: str, scope) -> Response:
            response = await super().get_response(path, scope)
            _apply_asset_cache_headers(response, path)
            return response

    nested = os.path.join(static_dir, "static")
    routes: List[Any] = []

    # Streamlit serves its whole app under `--server.baseUrlPath` when set, so
    # the browser asks for `/<base>/static/js/...`. Mounting only at `/static`
    # would miss every one of those, drop them into the authenticated catch-all,
    # and silently restore the 401 storm -- with the bundle present, so nothing
    # would warn. `_assert_static_bundle_present` covers a missing directory,
    # not a mismatched prefix, which is why the prefix is read here.
    prefix = STREAMLIT_BASE_URL_PATH

    # Streamlit's own layout is static/static/{js,css,media}; index.html points
    # at "./static/js/...". Mount the inner directory at <prefix>/static.
    if os.path.isdir(nested):
        # GZip wraps ONLY the bundle, not the whole app. Compressing here is
        # what recovers the 4.17x on the eagerly-preloaded chunks; applying it
        # app-wide also re-compressed proxied PDFs and ZIPs -- already-compressed
        # bytes, for nothing, on a loop this process shares with the Streamlit
        # script runner -- and forced those responses into chunked encoding.
        # Streamlit gzips its own text responses upstream, so the proxy path
        # loses nothing by passing encodings through untouched.
        routes.append(
            Mount(
                f"{prefix}/static",
                app=GZipMiddleware(
                    _PublicStreamlitStatic(directory=nested),
                    minimum_size=STATIC_GZIP_MIN_SIZE,
                    compresslevel=STATIC_GZIP_LEVEL,
                ),
                name="lex_static",
            )
        )

    # Top-level singletons index.html asks for by name.
    for filename in ("favicon.png", "manifest.json"):
        full = os.path.join(static_dir, filename)
        if not os.path.isfile(full):
            continue

        def _serve(request: Request, _full: str = full, _name: str = filename) -> Response:
            response = FileResponse(_full)
            _apply_asset_cache_headers(response, _name)
            return response

        routes.append(Route(f"{prefix}/{filename}", _serve, methods=["GET", "HEAD"]))

    return routes


def _apply_asset_cache_headers(response: Response, served_path: str) -> None:
    """``immutable`` for content-addressed files, ``no-cache`` for the rest."""
    if response.status_code in {301, 302, 303, 304, 307, 308}:
        return
    normalized = served_path.replace("\\", "/").lstrip("./")
    if not normalized or normalized.endswith(".html") or normalized.endswith("manifest.json"):
        response.headers["Cache-Control"] = "no-cache"
    else:
        response.headers["Cache-Control"] = f"public, immutable, max-age={STATIC_ASSET_MAX_AGE}"


def _assert_static_bundle_present() -> None:
    """Log loudly when the bundle is missing, and say what it costs.

    Not fatal: an unauthenticated 401 storm is bad, but refusing to boot is
    worse. ``requirements.txt`` pins streamlit to the range this layout was
    verified against, so this should only fire on a hand-edited install.
    """
    if _streamlit_static_dir():
        return
    logger.error(
        "Streamlit's static bundle could not be located, so /static/* will be proxied "
        "upstream behind authentication. Expect slow cold starts and, once a credential "
        "expires, 'Failed to fetch dynamically imported module' errors in the browser. "
        "Check that streamlit is installed in this interpreter."
    )


def _warn_if_upstream_is_not_colocated() -> None:
    """Warn when the bundle served here may not be the one upstream references.

    The chunk filenames are content hashes and ``index.html`` -- which names
    them -- comes from ``UPSTREAM``. Serving this interpreter's wheel is only
    guaranteed correct when both are the same process, which is what
    ``lex streamlit`` does. Point ``UPSTREAM`` at another container running a
    different Streamlit and every chunk 404s: the same dynamic-import failures,
    now unconditional. Cheap to detect, impossible to fix silently.
    """
    host = httpx.URL(UPSTREAM).host
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return
    logger.warning(
        "UPSTREAM=%s is not local, but Streamlit's asset bundle is served from THIS "
        "interpreter's wheel. If that container runs a different Streamlit version, its "
        "index.html will name chunk hashes this process does not have and every asset "
        "will 404. Keep the two in lockstep, or set LEX_SERVE_STATIC_LOCALLY=false to "
        "proxy assets upstream instead.",
        UPSTREAM,
    )


# Theme relay
# -----------------------------------------------------------------------------
#: Storage key both origins agree on. Defined in lex/streamlit_theme.py so the
#: relay and the Streamlit-side follower cannot drift apart; that module is pure
#: stdlib, so importing it here costs nothing.
from lex.streamlit_theme import THEME_STORAGE_KEY  # noqa: E402

#: Frontend origins this deployment was actually told about. Empty when nobody
#: configured one, which is a different situation from "configured as
#: localhost" and has to stay distinguishable -- see :func:`theme_relay`.
_THEME_RELAY_CONFIGURED = [
    o.rstrip("/")
    for o in (os.getenv("REACT_APP_URL"), os.getenv("LEX_FRONTEND_URL"))
    if o
]

#: Origins allowed to drive this relay. The lex-app frontend, wherever it lives.
_THEME_RELAY_ALLOWED = _THEME_RELAY_CONFIGURED or ["http://localhost:8000"]

_THEME_RELAY_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>lex theme relay</title></head>
<body>
<!--
  Cross-origin theme relay.

  localStorage is origin-scoped, so the lex-app frontend cannot write the
  Streamlit origin's storage directly -- and without that, a STANDALONE
  Streamlit window never learns the theme changed. postMessage only reaches
  frames you hold a handle to, which a separate window is not.

  This page is served from the Streamlit origin and embedded, hidden, by the
  frontend. The frontend posts a mode to it; this writes it to storage HERE.
  Because a storage write raises a `storage` event in every OTHER tab of the
  same origin, every Streamlit tab -- embedded or standalone -- is notified,
  with no server involved and no polling.
-->
<script>
  var KEY = "__KEY__";
  var ALLOWED = __ALLOWED__;

  window.addEventListener("message", function (ev) {
    if (ALLOWED.indexOf(ev.origin) === -1) return;           // only our frontend
    var d = ev.data;
    if (!d || d.type !== "lex-theme-set") return;
    var mode = d.mode === "dark" ? "dark" : "light";
    try {
      // Writing the same value raises no storage event, so a repeat is a
      // genuine no-op rather than a needless wake-up for every tab.
      if (window.localStorage.getItem(KEY) !== mode) {
        window.localStorage.setItem(KEY, mode);
      }
      // Answer so the caller knows the relay is alive and which value stuck.
      ev.source.postMessage({ type: "lex-theme-ack", mode: mode }, ev.origin);
    } catch (e) {
      /* storage disabled (private mode, blocked cookies) -- degrade quietly */
    }
  });

  // Announce readiness so the frontend can flush a mode queued before load.
  try {
    parent.postMessage({ type: "lex-theme-relay-ready" }, "*");
  } catch (e) {}
</script>
</body></html>
"""


def _is_top_level_document_request(request: Request) -> bool:
    """True only for a navigation of the top-level window.

    ``Sec-Fetch-Dest: document`` is sent for the top-level document and never
    for a framed one (that is ``iframe``/``frame``). A client that sends no
    ``Sec-Fetch-*`` at all reads as False, so an older browser keeps whatever
    the caller's default branch does rather than being guessed about.
    """
    return request.headers.get("sec-fetch-dest", "").strip().lower() == "document"


async def theme_relay(request: Request) -> Response:
    """Serve the cross-origin theme relay.

    Framed by the lex-app frontend so it can write THIS origin's storage. See
    the comment inside the document for why that indirection is necessary.

    A top-level navigation here is always a mistake -- the document has no UI,
    so the tab shows a blank page with no way back to the app. It is also a
    mistake that has reached a customer (LEX-1477, by the route described on
    :data:`_NON_LANDING_PREFIXES`), and the guards upstream of here only stop
    this proxy from causing it; a bookmark, a history entry, or an older proxy
    still in the chain can land a browser here anyway. So when the deployment
    has told us where the frontend lives, send them back to it instead of
    leaving them stranded.

    Only when it was actually configured: the framing allow-list falls back to
    ``http://localhost:8000``, and bouncing a production tab to localhost is
    worse than the blank page it replaces.
    """
    if _is_top_level_document_request(request) and _THEME_RELAY_CONFIGURED:
        return RedirectResponse(url=_THEME_RELAY_CONFIGURED[0], status_code=303)

    body = _THEME_RELAY_HTML.replace("__KEY__", THEME_STORAGE_KEY).replace(
        "__ALLOWED__", json.dumps(_THEME_RELAY_ALLOWED)
    )
    return HTMLResponse(
        body,
        headers={
            # Must be framable by the frontend; that is its entire purpose.
            "Content-Security-Policy": "frame-ancestors " + " ".join(_THEME_RELAY_ALLOWED),
            # Static and tiny, but never stale: the allow-list is baked in.
            "Cache-Control": "no-store",
        },
    )


# -----------------------------------------------------------------------------
# Access logging: make a failed asset request visible
# -----------------------------------------------------------------------------
# There were no access-log lines at all in the production log that this was
# written for, which is why a browser reporting "Failed to fetch dynamically
# imported module" could not be traced to a status code. Two uvicorn servers
# run in this process -- ours on 8501 and Streamlit's own on 8080 -- and Django
# applies its own ``dictConfig`` afterwards, so whether ``uvicorn.access``
# survives is not something this module can rely on.
#
# So it does not rely on it. This logs through the ``lex`` logger hierarchy,
# which the same production log proves is configured and emitting, and it
# chooses levels so the useful half is visible without the noise:
#
#   * 4xx/5xx at WARNING -- visible even when LEX_LOG_LEVEL is WARNING, which
#     is the whole point: an asset that 401s or 404s must never again be
#     invisible;
#   * everything else at INFO, and a *successful* static asset not at all
#     unless asked for. Streamlit eagerly preloads 107 chunks, so logging those
#     would bury the line anyone actually needs.

ACCESS_LOG_ENABLED = _env_bool("LEX_PROXY_ACCESS_LOG", True)
#: Log successful static-asset responses too. Off by default: 107 lines per
#: page load hides everything else. Turn on to confirm the bundle is serving.
ACCESS_LOG_STATIC = _env_bool("LEX_PROXY_ACCESS_LOG_STATIC", False)

access_logger = logging.getLogger("lex.proxy.access")


class AccessLogMiddleware:
    """Log one line per HTTP request, with the status the client actually got.

    Pure ASGI rather than ``BaseHTTPMiddleware``: it must not buffer a
    streaming response, and it must see the status even when the response is a
    passthrough of Streamlit's own bytes.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not ACCESS_LOG_ENABLED:
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        status_holder = {"code": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # An exception that escapes here is what the client sees as a
            # dropped connection, so it is precisely what must be logged.
            self._log(scope, 500, started, note="unhandled exception")
            raise

        self._log(scope, status_holder["code"], started)

    def _log(self, scope, status: int, started: float, note: str = "") -> None:
        path = scope.get("path", "")
        is_static = path.startswith(f"{STREAMLIT_BASE_URL_PATH}/static/")
        failed = status >= 400

        if not failed and is_static and not ACCESS_LOG_STATIC:
            return

        elapsed_ms = (time.monotonic() - started) * 1000
        query = scope.get("query_string", b"")
        # `auth_token` is a credential; never write one to a log.
        suffix = "?<query>" if query else ""
        message = "%s %s%s -> %d (%.0fms)%s"
        args = (
            scope.get("method", "?"),
            path,
            suffix,
            status,
            elapsed_ms,
            f" {note}" if note else "",
        )

        if failed:
            access_logger.warning(message, *args)
        else:
            access_logger.info(message, *args)


# -----------------------------------------------------------------------------
# Routing / app
# -----------------------------------------------------------------------------
_assert_static_bundle_present()
if SERVE_STATIC_LOCALLY:
    _warn_if_upstream_is_not_colocated()

routes = [
    # Before the catch-all proxy, or it would be forwarded upstream to Streamlit.
    Route("/_lex/theme-relay", theme_relay, methods=["GET"]),
    Route("/auth/login", login, methods=["GET"]),
    Route("/auth/callback", auth_callback, methods=["GET"]),
    Route("/oauth2/logout", oauth2_logout, methods=["GET"]),
    Route("/oauth2/sign_out", oauth2_sign_out, methods=["GET"]),
    Route("/auth/logout", oauth2_logout, methods=["GET"]),
    # Must precede the catch-all, or it would be proxied to Streamlit instead.
    Route("/auth/token", internal_token, methods=["GET"]),
    # Same reason. This is how a renewed credential reaches the proxy without
    # re-sourcing the iframe -- see the adopt_token docstring.
    Route("/auth/adopt", adopt_token, methods=["POST", "OPTIONS"]),
    # The Django-anchored hand-off (LEX_STREAMLIT_AUTH dual/handoff; 404 in legacy): where the
    # app sends the frame back with a code, and the page a dashboard whose sign-in ended shows.
    Route("/auth/handoff", auth_handoff, methods=["GET"]),
    Route("/auth/required", auth_required, methods=["GET"]),
    # What the hand-off page's sign-in watcher asks (lex/streamlit_app.py): does the sign-in stand.
    Route("/auth/status", auth_status, methods=["GET"]),
    # Same reason, and the ordering is load-bearing: these are the only paths
    # that must answer without a credential. See PUBLIC_PROXY_PATHS.
    *(
        Route(path, public_proxy, methods=["GET", "HEAD"])
        for path in sorted(PUBLIC_PROXY_PATHS)
    ),
    # Streamlit's own bundle, served locally and ungated. Must precede the
    # catch-all: with it after, /static/* falls into `proxy` and can 401.
    *_build_static_routes(),
    WebSocketRoute("/{path:path}", ws_proxy),
    Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]),
]

app = Starlette(routes=routes, lifespan=lifespan)

if ProxyHeadersMiddleware is not None:
    trusted_hosts = os.getenv("TRUSTED_PROXY_HOSTS", "*")
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted_hosts)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SESSION_HTTPS_ONLY,
    same_site=SESSION_SAMESITE,
)

# Outermost of all, so the status it records is the one the client received --
# after gzip, after the session middleware, and including anything those raise.
app.add_middleware(AccessLogMiddleware)
