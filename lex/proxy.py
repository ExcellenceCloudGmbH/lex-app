import asyncio
import html
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress
from inspect import signature
from typing import Any, Dict, Optional, Tuple, List
from urllib.parse import urlencode

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

SESSION_SECRET = os.environ.get("SESSION_SECRET") or os.environ.get("SESSION_KEY") or os.environ.get("SESSION_SECRET_KEY")

#: Escape hatch for the one legitimate case: a single-process dev run where
#: nobody minds being logged out by a restart.
ALLOW_EPHEMERAL_SESSION_SECRET = _env_bool(
    "LEX_ALLOW_EPHEMERAL_SESSION_SECRET", not PUBLIC_IS_HTTPS
)

if not SESSION_SECRET:
    if not ALLOW_EPHEMERAL_SESSION_SECRET:
        # A random per-process secret is not a weak secret, it is a *different*
        # secret in every process. Every session cookie signed before a restart
        # becomes undecodable after it, and cookies signed by one replica are
        # rejected by the next -- so users are thrown back to a login they did
        # nothing to deserve. That is indistinguishable from "session timeout
        # resets my state", and it is not a timeout at all.
        raise RuntimeError(
            "SESSION_SECRET is not set. The proxy would sign session cookies with a "
            "random per-process value, so every restart -- and every request that "
            "lands on a different replica -- would silently log all users out and "
            "reset their dashboard state. Set SESSION_SECRET to a fixed secret "
            "shared by every replica. To run without one anyway (single-process "
            "development only), set LEX_ALLOW_EPHEMERAL_SESSION_SECRET=true."
        )
    SESSION_SECRET = "CHANGE_ME_IN_PRODUCTION_" + secrets.token_urlsafe(32)
    logger.warning(
        "SESSION_SECRET is not set; using a random per-process value. Sessions will "
        "not survive a restart and cannot be shared across replicas. Do not deploy "
        "like this."
    )

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
    client_kwargs={"scope": "openid email profile", "verify": OIDC_VERIFY_SSL},
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
    """
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
            print("[proxy] JWT validation failed: no signing key")
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
        print("[proxy] JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        print(f"[proxy] JWT validation failed: {e}")
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


def _safe_next_path(raw: Optional[str]) -> Optional[str]:
    """A same-origin, path-only redirect target, or ``None``.

    Deliberately strict: only a single-leading-slash path (plus query and
    fragment) survives. Anything carrying a scheme, an authority, or a
    protocol-relative ``//host`` prefix is discarded, because this value ends
    up in a ``Location`` header after an OIDC round trip -- exactly the shape
    of an open redirect, and a login flow is the most valuable place to have
    one.
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
    return candidate


def _current_relative_path(request: Request) -> str:
    """This request's path and query, with any ``auth_token`` removed."""
    query = _query_without_auth_token(request)
    return request.url.path + (f"?{query}" if query else "")


def _query_without_auth_token(request: Request) -> str:
    """The request query string minus ``auth_token``, preserving the rest."""
    kept = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "auth_token"
    ]
    return urlencode(kept)


def _external_base_url(request: Request) -> str:
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


def _frame_breakout_response(request: Request) -> Response:
    """A 401 that escapes the iframe to a full-page login instead of framing the IdP.

    Three layered mechanisms, most-reliable last:
      1. best-effort automatic top-level navigation (works when the browser allows
         the frame to navigate the top window);
      2. a ``postMessage`` so a cooperating parent shell can drive re-auth;
      3. a user-clickable ``target="_top"`` link, which is always permitted because
         it is a user gesture.
    """
    login = _login_url(request, _current_relative_path(request))
    login_js = json.dumps(login)  # safe JS string literal
    login_attr = html.escape(login, quote=True)
    body = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Session expired</title></head><body>"
        "<script>(function(){var u=" + login_js + ";"
        "try{if(window.top!==window.self){window.top.location.href=u;}"
        "else{window.location.href=u;}}catch(e){}"
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

    - iframe/frame document load -> break out to a top-level login (never frame the IdP)
    - other top-level HTML navigation -> normal redirect to login
    - XHR / fetch / API call -> 401 JSON
    """
    accepts_html = "text/html" in request.headers.get("accept", "")
    if accepts_html and _is_iframe_document_request(request):
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
# Auth routes
# -----------------------------------------------------------------------------
async def login(request: Request):
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


async def oauth2_logout(request: Request):
    sid = (request.session.get("user") or {}).get("sid")
    if sid:
        await _drop_tokens(sid)
    request.session.clear()

    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie("st_access", path="/")
    return resp


async def oauth2_sign_out(request: Request):
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
        with suppress(Exception):
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close(code=4401)
        return

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
    }
    fwd: List[Tuple[str, str]] = [(k, v) for k, v in websocket.headers.items() if k.lower() not in excluded]

    fwd.append(("X-Streamlit-User-ID", str(user_payload.get("sub") or "")))
    fwd.append(("X-Streamlit-User-Email", user_payload.get("email") or ""))
    fwd.append(("X-Streamlit-User-Username", user_payload.get("preferred_username") or ""))
    fwd.append(("X-Streamlit-Auth-Method", auth_method))
    fwd.append(("X-Forwarded-User", user_payload.get("email") or ""))

    if user_payload.get("access_token"):
        fwd.append(("Authorization", f"Bearer {user_payload['access_token']}"))
        fwd.append(("X-Forwarded-Access-Token", user_payload["access_token"]))
        fwd.append(("X-Streamlit-Access-Token", user_payload["access_token"]))
    if user_payload.get("id_token"):
        fwd.append(("X-Forwarded-Id-Token", user_payload["id_token"]))
    if user_payload.get("refresh_token"):
        fwd.append(("X-Streamlit-Refresh-Token", user_payload["refresh_token"]))

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
    request = client.build_request(method, url, content=content, headers=headers)
    return await client.send(request, stream=True)


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


#: Dropped from both directions: meaningful only for a single hop.
_HOP_BY_HOP = frozenset({
    # Content-Length included: httpx recomputes it from the body we actually
    # pass, and a forwarded value beats httpx's own -- so any mismatch between
    # the two becomes a LocalProtocolError instead of a request.
    "content-length",
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


def _forwardable_request_headers(request: Request) -> Dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}


def _build_proxied_response(request: Request, upstream_resp: httpx.Response) -> Response:
    """Turn an upstream response into ours, preserving the bytes exactly.

    ``Content-Encoding`` is deliberately **kept** and the body streamed with
    ``aiter_raw()``, i.e. still encoded. The old code read the decoded body and
    then had to strip the header to stay honest -- which silently disabled
    compression for everything Streamlit serves (measured 3.4x on its bundle).
    Passing the encoded bytes through untouched costs no CPU and keeps the
    saving. ``GZipMiddleware`` sees the header and leaves such responses alone.
    """
    drop = _HOP_BY_HOP | {"content-length"}
    if upstream_resp.is_stream_consumed:
        # `.content` is decoded, so claiming an encoding would make the body
        # undecodable for the client. See _iter_upstream.
        drop = drop | {"content-encoding"}
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

    return _build_proxied_response(request, upstream_resp)


# -----------------------------------------------------------------------------
# HTTP proxy
# -----------------------------------------------------------------------------
async def proxy(request: Request):
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
        return stripped

    # 4) Deny
    if not user_payload:
        # Never redirect an iframe document load to the IdP: Keycloak's login page
        # sets frame-ancestors 'self' and the browser shows "refused to connect".
        # Break out to a top-level login instead (see _unauthenticated_response).
        return _unauthenticated_response(request)

    method = request.method
    url = httpx.URL(UPSTREAM + request.url.path)
    if request.url.query:
        url = url.copy_with(query=request.url.query.encode("utf-8"))

    fwd_headers = _forwardable_request_headers(request)

    fwd_headers["X-Streamlit-User-ID"] = str(user_payload.get("sub") or "")
    fwd_headers["X-Streamlit-User-Email"] = user_payload.get("email") or ""
    fwd_headers["X-Streamlit-User-Username"] = user_payload.get("preferred_username") or ""
    fwd_headers["X-Streamlit-Auth-Method"] = auth_method
    fwd_headers["X-Forwarded-User"] = user_payload.get("email") or ""

    # Forward access token if present (either from JWT auth or session)
    if user_payload.get("access_token"):
        fwd_headers["Authorization"] = f"Bearer {user_payload['access_token']}"
        fwd_headers["X-Forwarded-Access-Token"] = user_payload["access_token"]
        fwd_headers["X-Streamlit-Access-Token"] = user_payload["access_token"]

    if user_payload.get("id_token"):
        fwd_headers["X-Forwarded-Id-Token"] = user_payload["id_token"]
    if user_payload.get("refresh_token"):
        fwd_headers["X-Streamlit-Refresh-Token"] = user_payload["refresh_token"]

    body = await request.body()

    try:
        upstream_resp = await _upstream_send(method, url, content=body, headers=fwd_headers)
    except httpx.ConnectError:
        return JSONResponse(
            {"error": f"Upstream unavailable: {UPSTREAM}. Streamlit may still be starting."},
            status_code=503,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": f"Upstream timeout: {UPSTREAM}"},
            status_code=504,
        )

    response = _build_proxied_response(request, upstream_resp)

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

    # DOMAIN_HOSTED is a bare hostname, matching how settings.py consumes it.
    domain_hosted = (os.getenv("DOMAIN_HOSTED") or "").strip().strip("/")
    if domain_hosted and not domain_hosted.startswith("localhost"):
        origins.add(f"https://{domain_hosted}")

    # The standalone (non-embedded) dashboard talking to itself needs no entry.
    if PUBLIC_URL:
        origins.add(PUBLIC_URL)

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
        routes.append(
            Mount(
                f"{prefix}/static",
                app=_PublicStreamlitStatic(directory=nested),
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


# -----------------------------------------------------------------------------
# Routing / app
# -----------------------------------------------------------------------------
_assert_static_bundle_present()
if SERVE_STATIC_LOCALLY:
    _warn_if_upstream_is_not_colocated()

routes = [
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

# Added last, so it sits outermost and compresses on the way out. This is what
# restores the compression the proxy path used to destroy: httpx transparently
# decodes the upstream body and `proxy` drops `Content-Encoding` (it must -- the
# bytes it holds are no longer encoded), so before this the whole bundle went
# out as plaintext. Measured over Streamlit 1.61's shipped chunks: 19.7 MB raw
# vs 5.8 MB gzipped, a 3.4x saving that had simply been switched off.
#
# GZipMiddleware passes non-HTTP scopes straight through, so the WebSocket at
# /_stcore/stream is untouched.
app.add_middleware(
    GZipMiddleware,
    minimum_size=STATIC_GZIP_MIN_SIZE,
    compresslevel=STATIC_GZIP_LEVEL,
)
