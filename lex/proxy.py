import asyncio
import html
import json
import logging
import os
import secrets
import time
from contextlib import suppress
from inspect import signature
from typing import Any, Dict, Optional, Tuple, List

import httpx
import jwt  # PyJWT
from authlib.integrations.starlette_client import OAuth
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
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
if not SESSION_SECRET:
    SESSION_SECRET = "CHANGE_ME_IN_PRODUCTION_" + secrets.token_urlsafe(32)

SESSION_HTTPS_ONLY = _env_bool("SESSION_HTTPS_ONLY", PUBLIC_IS_HTTPS)
SESSION_SAMESITE = (os.getenv("SESSION_SAMESITE") or "lax").lower()  # lax | strict | none

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


def _build_token_store() -> TokenStore:
    if TOKEN_REDIS_URL:
        try:
            return RedisTokenStore(TOKEN_REDIS_URL)
        except Exception as e:
            print(f"[proxy] Redis token store disabled: {e}. Falling back to in-memory store.")
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


def _get_jwks_sync() -> Optional[Dict[str, Any]]:
    global _JWKS_CACHE, _JWKS_CACHE_TIME

    if _JWKS_CACHE and (time.time() - _JWKS_CACHE_TIME) < _JWKS_CACHE_TTL:
        return _JWKS_CACHE

    if not KEYCLOAK_URL or not KEYCLOAK_REALM:
        print("[proxy] JWKS fetch skipped: KEYCLOAK_URL or KEYCLOAK_REALM not set")
        return None

    certs_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
    try:
        with httpx.Client(timeout=10.0, verify=OIDC_VERIFY_SSL) as client:
            resp = client.get(certs_url)
            resp.raise_for_status()
            _JWKS_CACHE = resp.json()
            _JWKS_CACHE_TIME = time.time()
            return _JWKS_CACHE
    except Exception as e:
        print(f"[proxy] Failed to fetch JWKS from {certs_url}: {e}")
        return None


def _get_signing_key(token: str):
    jwks_data = _get_jwks_sync()
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


# -----------------------------------------------------------------------------
# URL helpers
# -----------------------------------------------------------------------------
def _external_base_url(request: Request) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    return str(request.base_url).rstrip("/")


def _callback_url(request: Request) -> str:
    return f"{_external_base_url(request)}/auth/callback"


def _login_url(request: Request) -> str:
    return f"{_external_base_url(request)}/auth/login"


def _is_iframe_document_request(request: Request) -> bool:
    """True for a document navigation happening inside an ``<iframe>``/``<frame>``.

    Browsers set ``Sec-Fetch-Dest: iframe`` (or ``frame``) on the iframe's own
    document request. Redirecting such a request to Keycloak would load the login
    page inside the frame, which Keycloak forbids via ``X-Frame-Options`` /
    ``Content-Security-Policy: frame-ancestors 'self'`` -- so the browser renders
    "refused to connect" instead. We detect that case to break out of the frame.
    """
    return request.headers.get("sec-fetch-dest", "").strip().lower() in {"iframe", "frame"}


def _frame_breakout_response(request: Request) -> Response:
    """A 401 that escapes the iframe to a full-page login instead of framing the IdP.

    Three layered mechanisms, most-reliable last:
      1. best-effort automatic top-level navigation (works when the browser allows
         the frame to navigate the top window);
      2. a ``postMessage`` so a cooperating parent shell can drive re-auth;
      3. a user-clickable ``target="_top"`` link, which is always permitted because
         it is a user gesture.
    """
    login = _login_url(request)
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
        return RedirectResponse(url="/auth/login")
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
    return await oauth.oidc.authorize_redirect(request, _callback_url(request))


async def auth_callback(request: Request):
    token = await oauth.oidc.authorize_access_token(request)
    userinfo = token.get("userinfo") or await oauth.oidc.userinfo(token=token)

    sid = secrets.token_urlsafe(16)
    email = userinfo.get("email") or ""

    await _put_tokens(sid, email, token)
    request.session["user"] = {"email": email, "sid": sid}

    resp = RedirectResponse(url="/", status_code=303)

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
    scope_session = websocket.scope.get("session") or {}
    user_payload: Optional[Dict[str, Any]] = None
    auth_method = "none"

    auth_header = websocket.query_params.get("auth_token", "") or websocket.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        jwt_token = auth_header[7:]
    elif auth_header:
        jwt_token = auth_header
    else:
        jwt_token = websocket.cookies.get("st_access")

    if jwt_token:
        payload = validate_jwt_token(jwt_token)
        if payload:
            user_payload = {
                "sub": payload.get("sub"),
                "email": payload.get("email"),
                "preferred_username": payload.get("preferred_username"),
                "access_token": jwt_token,
            }
            auth_method = "jwt"

    if not user_payload and "user" in scope_session:
        tokens = await _ensure_valid_access_token({"user": scope_session.get("user")})
        if tokens:
            claims = _claims_from_token_set(tokens)
            session_email = (scope_session.get("user") or {}).get("email") or ""
            user_payload = {
                "sub": claims.get("sub") or "",
                "email": claims.get("email") or session_email or tokens.get("email") or "",
                "preferred_username": claims.get("preferred_username") or claims.get("name") or "",
                "access_token": tokens.get("access_token"),
                "id_token": tokens.get("id_token"),
                "refresh_token": tokens.get("refresh_token"),
            }
            auth_method = "session"

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
# HTTP proxy
# -----------------------------------------------------------------------------
async def proxy(request: Request):
    user_payload: Optional[Dict[str, Any]] = None
    auth_method = "none"

    # Track where token came from (important to decide whether to set cookie)
    token_source = "none"

    auth_header = (
        request.cookies.get("st_access", "")
        or request.query_params.get("auth_token", "")
        or request.headers.get("auth_token", "")
        or request.headers.get("authorization", "")
    )

    if request.cookies.get("st_access"):
        token_source = "cookie"
    elif request.query_params.get("auth_token"):
        token_source = "query"
    elif request.headers.get("auth_token") or request.headers.get("authorization"):
        token_source = "header"

    # 1) JWT auth
    jwt_token = ""
    jwt_payload: Optional[Dict[str, Any]] = None
    if auth_header:
        jwt_token = auth_header[7:] if auth_header.startswith("Bearer ") else auth_header
        jwt_payload = validate_jwt_token(jwt_token)
        if jwt_payload:
            user_payload = {
                "sub": jwt_payload.get("sub"),
                "email": jwt_payload.get("email"),
                "preferred_username": jwt_payload.get("preferred_username"),
                "access_token": jwt_token,  # keep so we can forward Authorization if desired
            }
            auth_method = "jwt"

            # ✅ FIX: persist this one-off auth_token into a real session/token-store entry
            await _persist_jwt_to_session_if_needed(request, jwt_token, jwt_payload, token_source)

    # 2) Session auth (cookie from SessionMiddleware -> server-side tokens)
    if not user_payload and "user" in request.session:
        tokens = await _ensure_valid_access_token(request.session)
        if tokens:
            claims = _claims_from_token_set(tokens)
            session_email = (request.session.get("user") or {}).get("email") or ""
            user_payload = {
                "sub": claims.get("sub") or "",
                "email": claims.get("email") or session_email or tokens.get("email") or "",
                "preferred_username": claims.get("preferred_username") or claims.get("name") or "",
                "access_token": tokens.get("access_token"),
                "id_token": tokens.get("id_token"),
                "refresh_token": tokens.get("refresh_token"),
            }
            auth_method = "session"

    # 3) Deny
    if not user_payload:
        # Never redirect an iframe document load to the IdP: Keycloak's login page
        # sets frame-ancestors 'self' and the browser shows "refused to connect".
        # Break out to a top-level login instead (see _unauthenticated_response).
        return _unauthenticated_response(request)

    method = request.method
    url = httpx.URL(UPSTREAM + request.url.path)
    if request.url.query:
        url = url.copy_with(query=request.url.query.encode("utf-8"))

    hop_by_hop = {
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
    }
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in hop_by_hop}

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
    timeout = httpx.Timeout(30.0)

    try:
        async with httpx.AsyncClient(**_upstream_http_client_kwargs(timeout)) as client:
            upstream_resp = await client.request(method, url, content=body, headers=fwd_headers)
    except httpx.ConnectError:
        return JSONResponse(
            {"error": f"Upstream unavailable: {UPSTREAM}. Streamlit may still be starting."},
            status_code=503,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": f"Upstream timeout after 30s: {UPSTREAM}"},
            status_code=504,
        )

    drop = hop_by_hop | {"content-length", "content-encoding", "transfer-encoding"}
    resp_headers = {k: v for k, v in upstream_resp.headers.items() if k.lower() not in drop}

    response = Response(content=upstream_resp.content, status_code=upstream_resp.status_code, headers=resp_headers)

    # Preserve upstream Set-Cookie headers (Streamlit sets cookies)
    get_list = getattr(upstream_resp.headers, "get_list", None)
    if callable(get_list):
        for c in upstream_resp.headers.get_list("set-cookie"):
            response.headers.append("set-cookie", c)
    else:
        for k, v in upstream_resp.headers.items():
            if k.lower() == "set-cookie":
                response.headers.append("set-cookie", v)

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
# Theme relay
# -----------------------------------------------------------------------------
#: Storage key both origins agree on. Defined in lex/streamlit_theme.py so the
#: relay and the Streamlit-side follower cannot drift apart; that module is pure
#: stdlib, so importing it here costs nothing.
from lex.streamlit_theme import THEME_STORAGE_KEY  # noqa: E402

#: Origins allowed to drive this relay. The lex-app frontend, wherever it lives.
_THEME_RELAY_ALLOWED = [
    o.rstrip("/")
    for o in (os.getenv("REACT_APP_URL"), os.getenv("LEX_FRONTEND_URL"))
    if o
] or ["http://localhost:8000"]

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


async def theme_relay(request: Request) -> Response:
    """Serve the cross-origin theme relay.

    Framed by the lex-app frontend so it can write THIS origin's storage. See
    the comment inside the document for why that indirection is necessary.
    """
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
# Routing / app
# -----------------------------------------------------------------------------
routes = [
    # Before the catch-all proxy, or it would be forwarded upstream to Streamlit.
    Route("/_lex/theme-relay", theme_relay, methods=["GET"]),
    Route("/auth/login", login, methods=["GET"]),
    Route("/auth/callback", auth_callback, methods=["GET"]),
    Route("/oauth2/logout", oauth2_logout, methods=["GET"]),
    Route("/oauth2/sign_out", oauth2_sign_out, methods=["GET"]),
    Route("/auth/logout", oauth2_logout, methods=["GET"]),
    WebSocketRoute("/{path:path}", ws_proxy),
    Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]),
]

app = Starlette(routes=routes)

if ProxyHeadersMiddleware is not None:
    trusted_hosts = os.getenv("TRUSTED_PROXY_HOSTS", "*")
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted_hosts)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SESSION_HTTPS_ONLY,
    same_site=SESSION_SAMESITE,
)
