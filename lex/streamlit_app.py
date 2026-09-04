import html
import json
import logging
import os
import threading
import time
import traceback
import urllib.parse
from typing import Optional, Dict

import jwt
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

logger = logging.getLogger(__name__)
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# -------------------------
# Token lifecycle: config
# -------------------------
# Streamlit reads request headers off the WebSocket handshake that opened the
# session -- ``st.context.headers`` resolves the session's *client*, and that
# socket stays open for as long as the tab does. Every credential the auth proxy
# injects is therefore a snapshot taken at connect time: it ages while the user
# reads a chart, and re-reading the headers cannot produce a newer one because
# no new handshake has happened.
#
# Keeping an idle dashboard alive means renewing ahead of expiry through a
# channel that is still live. That channel is the proxy's ``/auth/token``
# endpoint: it holds the refresh token, it is the only component allowed to
# spend it, and it will hand back a currently-valid access token for the same
# session at any moment. Renewing before expiry also keeps Keycloak's SSO idle
# clock from running out, so the tab survives being left alone.
TOKEN_SKEW_SECONDS = 10          # treat a token as spent this long before exp
RENEW_LEAD_SECONDS = 60          # renew this long *before* exp, never after it
REFRESH_MIN_INTERVAL = 15        # floor sleep
REFRESH_MAX_BACKOFF = 300        # cap backoff to 5 minutes
RENEW_POLL_SECONDS = 5           # stop/liveness granularity while sleeping
RENEWAL_GRACE_SECONDS = 120      # ride out a failing renewal this long before
                                 # asking anyone to re-authenticate
_TOKEN_REFRESH_LOCK = threading.RLock()

# Under ``lex streamlit`` the proxy runs in this very process on 8501 while
# Streamlit serves 8080 (see lex/bin/lex.py), so this is a loopback call.
PROXY_INTERNAL_URL = (
    os.getenv("LEX_PROXY_INTERNAL_URL")
    or f"http://127.0.0.1:{os.getenv('LEX_PROXY_PORT', '8501')}"
).rstrip("/")
INTERNAL_AUTH_HEADER = "x-lex-internal-auth"

# Guarded so the module can be imported (by tests, by tooling) without
# Streamlit commands firing at import time. Streamlit executes this file with
# ``__name__ == "__main__"``, so the app itself is unaffected.
if __name__ == "__main__":
    st.set_page_config(layout="wide")


def _oidc_token_endpoint() -> str:
    base = (os.getenv("KEYCLOAK_URL") or "").rstrip("/")
    realm = os.getenv("KEYCLOAK_REALM") or ""
    return f"{base}/realms/{realm}/protocol/openid-connect/token"


def _decode_exp_no_verify(token: str) -> int:
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
        return int(claims.get("exp", 0)) if claims else 0
    except Exception:
        return 0


def _now() -> int:
    return int(time.time())


def _post_refresh(refresh_token: str) -> dict | None:
    url = _oidc_token_endpoint()
    client_id = os.getenv("OIDC_RP_CLIENT_ID")
    client_secret = os.getenv("OIDC_RP_CLIENT_SECRET")

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id or "",
    }
    if client_secret:
        data["client_secret"] = client_secret

    try:
        r = requests.post(url, data=data, timeout=15)
        if r.status_code >= 400:
            log.warning("Refresh failed: %s %s", r.status_code, r.text)
            return None
        return r.json()
    except Exception as e:
        log.warning("Refresh exception: %s", e)
        return None


def _store_tokens(access: str, refresh: str | None = None, expires_in=None) -> None:
    """Record a token set, keeping the refresh token when the response omits it."""
    with _TOKEN_REFRESH_LOCK:
        st.session_state.access_token = access or ""
        if refresh is not None:
            st.session_state.refresh_token = refresh
        st.session_state.token_exp = _decode_exp_no_verify(access) if access else 0
        st.session_state.expires_in = expires_in


def _update_tokens_from_response(tok: dict) -> None:
    access = tok.get("access_token") or ""
    refresh = tok.get("refresh_token") or st.session_state.get("refresh_token") or ""
    _store_tokens(access, refresh, tok.get("expires_in"))


def _token_exp_from_state_or_decode(access: str) -> int:
    exp = int(st.session_state.get("token_exp") or 0) if access else 0
    if not exp and access:
        exp = _decode_exp_no_verify(access)
        if exp:
            st.session_state.token_exp = exp
    return exp


def _is_token_valid(access: str, skew_seconds: int = TOKEN_SKEW_SECONDS) -> bool:
    access = (access or "").strip()
    if not access:
        return False
    exp = _token_exp_from_state_or_decode(access)
    if not exp:
        # If exp is unavailable, keep token as usable and rely on server responses.
        return True
    return _now() < (exp - skew_seconds)


def _live_headers() -> Dict[str, str]:
    """Headers of the session's *current* client connection.

    Re-read rather than cached: a dropped WebSocket that reconnects, or an
    embedded iframe re-sourced with a fresh ``auth_token``, arrives as a new
    handshake through the proxy and therefore carries a newer credential.
    """
    try:
        return normalize_headers(getattr(st.context, "headers", {}) or {})
    except Exception:
        return {}


def _adopt_header_token(h: Dict[str, str]) -> bool:
    """Take the handshake's access token, but only when it is genuinely newer.

    The guard matters more than it looks. The headers are frozen for the life of
    a connection, so once a renewal has landed in session state the header copy
    is *older* than what we hold. Adopting it on inequality alone -- as this used
    to -- silently reverted every renewal on the next rerun and put the session
    straight back on the expired token it had just escaped.
    """
    token_from_header = (bearer_from_headers(h) or "").strip()
    if not token_from_header:
        return False

    with _TOKEN_REFRESH_LOCK:
        current = (st.session_state.get("access_token") or "").strip()
        if token_from_header == current:
            return False

        header_exp = _decode_exp_no_verify(token_from_header)
        current_exp = int(st.session_state.get("token_exp") or 0) if current else 0
        if current and current_exp and header_exp and header_exp <= current_exp:
            return False

        st.session_state.access_token = token_from_header
        st.session_state.token_exp = header_exp
        st.session_state.expires_in = None
        return True


def _sync_tokens_from_headers(h: Dict[str, str]) -> None:
    _adopt_header_token(h)

    # The refresh token is only ever a fallback for deployments without the
    # proxy; adopting the handshake copy is harmless because we never spend it
    # while the proxy is answering.
    rt_hdr = (h.get("x-streamlit-refresh-token") or "").strip()
    if rt_hdr and rt_hdr != (st.session_state.get("refresh_token") or ""):
        st.session_state.refresh_token = rt_hdr


def _pull_token_from_proxy(cookie_header: str) -> bool:
    """Ask the proxy for a currently-valid access token for this browser session.

    The proxy refreshes on our behalf, so this succeeds even when the token we
    hold has already expired. Returns ``False`` when there is no proxy to ask,
    no session cookie to present, or the session itself is gone.
    """
    secret = os.getenv("LEX_INTERNAL_AUTH_SECRET") or ""
    if not secret or not cookie_header:
        return False

    try:
        resp = requests.get(
            f"{PROXY_INTERNAL_URL}/auth/token",
            headers={"Cookie": cookie_header, INTERNAL_AUTH_HEADER: secret},
            timeout=10,
        )
    except Exception as e:
        log.warning("Token pull from proxy failed: %s", e)
        return False

    if resp.status_code != 200:
        log.warning("Token pull from proxy returned %s", resp.status_code)
        return False

    try:
        data = resp.json()
    except Exception:
        return False

    access = (data.get("access_token") or "").strip()
    if not access:
        return False

    # Keep whatever refresh token we already hold: the proxy deliberately does
    # not return one, because spending it must stay single-writer.
    _store_tokens(access, refresh=None, expires_in=None)
    return True


def renew_access_token() -> bool:
    """Obtain a fresh access token, in order of decreasing authority."""
    with _TOKEN_REFRESH_LOCK:
        # Someone else may have renewed while we waited for the lock; renewal
        # involves network round trips, so re-checking here keeps a rerun from
        # blocking behind a refresh that has already delivered.
        if _is_token_valid(st.session_state.get("access_token") or ""):
            return True

        h = _live_headers()

        # 1) A reconnected socket or a re-sourced iframe may already have
        #    delivered a newer credential in the handshake.
        if _adopt_header_token(h) and _is_token_valid(st.session_state.get("access_token") or ""):
            return True

        # 2) The proxy: sole owner of the refresh token, always current.
        if _pull_token_from_proxy(h.get("cookie", "")):
            return True

        # 3) No proxy answering (plain ``streamlit run``, or it is down). Only
        #    then do we spend our own copy of the refresh token -- with the
        #    proxy silent there is nobody to race for the rotation.
        refresh = (st.session_state.get("refresh_token") or "").strip()
        if refresh:
            tok = _post_refresh(refresh)
            if tok and tok.get("access_token"):
                _update_tokens_from_response(tok)
                return True

        return False


def ensure_valid_access_token(allow_refresh: bool = True) -> bool:
    """True when session state holds a usable access token, renewing if needed."""
    access = (st.session_state.get("access_token") or "").strip()
    if _is_token_valid(access):
        return True

    if allow_refresh and renew_access_token():
        return _is_token_valid(st.session_state.get("access_token") or "", skew_seconds=0)

    # Final strict check without skew to avoid dropping a token that is still valid for a few seconds.
    return _is_token_valid(st.session_state.get("access_token") or "", skew_seconds=0)


def _session_is_live(session_id: str) -> bool:
    """False once Streamlit has torn this session down.

    Without it the refresher outlives the browser tab: it only ever consulted a
    session-state flag, which nothing sets when a user simply closes the page,
    so every abandoned session left a thread renewing tokens forever.
    """
    if not session_id:
        return True
    try:
        from streamlit import runtime

        if not runtime.exists():
            return False
        return runtime.get_instance().is_active_session(session_id)
    except Exception:
        return True


def _refresher_should_stop(session_id: str, stop_key: str) -> bool:
    return bool(st.session_state.get(stop_key, False)) or not _session_is_live(session_id)


def _seconds_until_renewal() -> int:
    exp = int(st.session_state.get("token_exp") or 0)
    if not exp:
        expires_in = st.session_state.get("expires_in")
        if expires_in:
            return max(REFRESH_MIN_INTERVAL, int(expires_in) - RENEW_LEAD_SECONDS)
        return REFRESH_MIN_INTERVAL
    return max(REFRESH_MIN_INTERVAL, exp - RENEW_LEAD_SECONDS - _now())


def _token_refresher(session_id: str, stop_key: str = "stop_token_refresher") -> None:
    """Renew ahead of every expiry for as long as the session exists.

    This runs for session *and* jwt auth alike. Skipping it for proxy-managed
    sessions was the original mistake: the proxy can only deliver a token at
    handshake time, so "the proxy will handle it" meant nothing handled it, and
    an idle tab expired on schedule.
    """
    backoff = 5
    try:
        while True:
            if _refresher_should_stop(session_id, stop_key):
                return

            end_at = _now() + _seconds_until_renewal()
            while _now() < end_at:
                if _refresher_should_stop(session_id, stop_key):
                    return
                time.sleep(min(RENEW_POLL_SECONDS, max(1, end_at - _now())))

            if _refresher_should_stop(session_id, stop_key):
                return

            if renew_access_token():
                backoff = 5
                st.session_state.token_renewal_failed = False
                continue

            # Renewal can fail transiently (proxy restarting, IdP blip). Back off
            # and retry rather than tearing the session down on the first miss.
            st.session_state.token_renewal_failed = True
            backoff = min(REFRESH_MAX_BACKOFF, backoff * 2)
            slept = 0
            while slept < backoff:
                if _refresher_should_stop(session_id, stop_key):
                    return
                time.sleep(min(RENEW_POLL_SECONDS, backoff - slept))
                slept += RENEW_POLL_SECONDS
    except Exception as e:
        # Session state torn down from under us, or an unexpected fault. Exiting
        # is right: a thread that cannot reach its session has nothing to renew.
        log.info("Token refresher for session %s stopping: %s", session_id or "?", e)


def start_token_refresh_thread_if_needed() -> None:
    ctx = get_script_run_ctx()
    session_id = getattr(ctx, "session_id", "") or ""

    th = st.session_state.get("token_refresher_thread")
    if th and getattr(th, "is_alive", lambda: False)():
        # Re-bind: the session may have been served by a new ScriptRunner since
        # the thread started.
        add_script_run_ctx(th, ctx)
        st.session_state.token_refresher_started = True
        return

    st.session_state.stop_token_refresher = False
    th = threading.Thread(
        target=_token_refresher,
        name="token_refresher",
        args=(session_id,),
        daemon=True,
    )
    add_script_run_ctx(th, ctx)
    th.start()

    st.session_state.token_refresher_started = True
    st.session_state.token_refresher_thread = th


def normalize(d: Dict[str, str]) -> Dict[str, str]:
    return {(k or "").strip().lower(): (v or "").strip() for k, v in (d or {}).items()}


def normalize_headers(h: Dict[str, str]) -> Dict[str, str]:
    return {(k or "").strip().lower(): (v or "").strip() for k, v in (h or {}).items()}


def strip_bearer(value: str) -> str:
    v = (value or "").strip()
    if v.lower().startswith("bearer "):
        return v.split(" ", 1)[1].strip()
    return v


def get_bearer_token(headers: Dict[str, str]) -> Optional[str]:
    h = normalize_headers(headers)
    for name in ("x-streamlit-access-token", "authorization", "x-forwarded-access-token", "x-auth-request-access-token"):
        val = h.get(name)
        if not val:
            continue
        return strip_bearer(val)
    return None


def bearer_from_headers(h: Dict[str, str]) -> Optional[str]:
    for name in ("x-streamlit-access-token", "authorization", "x-forwarded-access-token", "x-auth-request-access-token"):
        v = h.get(name)
        if not v:
            continue
        v = v.strip()
        if v.lower().startswith("bearer "):
            return v.split(" ", 1)[1].strip()
        return v
    return None


def decode_jwt_claims_no_verify(token: str) -> Dict:
    try:
        return jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except Exception as e:
        logger.warning(f"JWT decode (no verify) failed: {e}")
        return {}


def get_user_info(access_token: str):
    keycloak_url = os.getenv("KEYCLOAK_URL")
    realm_name = os.getenv("KEYCLOAK_REALM")

    if not keycloak_url or not realm_name:
        return None

    userinfo_url = f"{keycloak_url}/realms/{realm_name}/protocol/openid-connect/userinfo"
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(userinfo_url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to get user info: {e}")
        return None


def sync_keycloak_context_from_access_token() -> None:
    # Renewal is allowed for every auth method now. Exempting ``session`` used to
    # be the safe choice because Streamlit would have spent the same refresh
    # token as the proxy; it no longer does -- it asks the proxy instead.
    if not ensure_valid_access_token():
        return

    access_token = (st.session_state.get("access_token") or "").strip()
    if not access_token:
        return

    if st.session_state.get("keycloak_context_token") == access_token:
        return

    user_info = get_user_info(access_token)
    if isinstance(user_info, dict) and user_info:
        st.session_state.user_info = user_info
        st.session_state.user_id = user_info.get("sub") or st.session_state.get("user_id", "")
        st.session_state.user_email = user_info.get("email") or st.session_state.get("user_email", "")
        username = (
            user_info.get("preferred_username")
            or user_info.get("name")
            or st.session_state.get("user_username", "")
        )
        st.session_state.user_username = username

    try:
        from lex.api.views.authentication.KeycloakManager import KeycloakManager

        kc_manager = KeycloakManager()
        permissions = kc_manager.get_uma_permissions(access_token)
    except Exception as e:
        # Keep the permissions we already resolved. Blanking them on a failed
        # lookup silently demotes the user to "no access" mid-session, which
        # reads as a broken dashboard rather than a transient Keycloak blip.
        logger.error(f"Failed to get UMA permissions via KeycloakManager: {e}")
        return

    st.session_state.permissions = permissions if isinstance(permissions, list) else []
    st.session_state.keycloak_context_token = access_token


# -------------------------
# Logout helpers (form-safe)
# -------------------------
def _is_truthy_qp(v) -> bool:
    if v is None:
        return False
    if isinstance(v, list):
        v = v[0] if v else None
    return str(v).lower() in ("1", "true", "yes", "y", "on")


def _base_path() -> str:
    # Supports deployments where Streamlit is mounted under a subpath
    try:
        p = st.get_option("server.baseUrlPath") or ""
    except Exception:
        p = ""
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/")


def _current_base_url() -> str:
    # Prefer explicit public URL if provided
    public = os.getenv("STREAMLIT_PUBLIC_URL") or os.getenv("PUBLIC_URL")
    if public:
        return public.rstrip("/")

    # Otherwise infer from reverse-proxy headers
    h = normalize_headers(getattr(st.context, "headers", {}) or {})
    proto = (h.get("x-forwarded-proto") or "http").split(",")[0].strip()
    host = (h.get("x-forwarded-host") or h.get("host") or "localhost:8501").split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/")


def _local_logout_cleanup() -> None:
    st.session_state.stop_token_refresher = True
    th = st.session_state.get("token_refresher_thread")
    if th and getattr(th, "is_alive", lambda: False)():
        th.join(timeout=1.0)
    st.session_state.clear()


def handle_logout_landing() -> None:
    # If we landed here with ?logout=1, do local cleanup and stop.
    if _is_truthy_qp(st.query_params.get("logout")):
        _local_logout_cleanup()
        try:
            st.query_params.clear()
        except Exception:
            pass
        st.success("✅ Logged out successfully. You can close this window.")
        st.stop()


def render_logout_link() -> None:
    """
    Form-safe logout control:
    - Not a Streamlit widget (so it won't break inside st.form()).
    - Works regardless of whatever streamlit_structure.main() renders.
    """
    base_url = _current_base_url()
    base_path = _base_path()

    # After upstream logout, land on /?logout=1 to clear Streamlit session_state
    logout_landing_abs = f"{base_url}{base_path}/?logout=1"
    rd = urllib.parse.quote(logout_landing_abs, safe="")

    auth_method = st.session_state.get("auth_method", "session")
    if auth_method == "session":
        href = f"{base_path}/oauth2/sign_out?rd={rd}"
    else:
        # JWT: we can't revoke upstream header; just clear local session_state on landing
        href = f"{base_path}/?logout=1"

    st.sidebar.markdown(
        f"""
        <a href="{href}" target="_top" style="
            display:inline-block;
            padding:0.45rem 0.8rem;
            border-radius:0.5rem;
            border:1px solid rgba(49,51,63,0.25);
            text-decoration:none;
            font-weight:600;
        ">Logout</a>
        """,
        unsafe_allow_html=True,
    )


def _within_renewal_grace() -> bool:
    """True while a failing renewal is still plausibly transient.

    A proxy restart or an IdP hiccup should never be visible to someone reading
    a chart, so a failed renewal buys silence for a while and the refresher keeps
    retrying underneath. Past the grace window the failure is structural --
    Keycloak's SSO max lifetime, a revoked session -- and no amount of retrying
    substitutes for signing in again.
    """
    if not st.session_state.get("token_renewal_failed"):
        st.session_state.renewal_failing_since = 0
        return True

    since = int(st.session_state.get("renewal_failing_since") or 0)
    if not since:
        st.session_state.renewal_failing_since = _now()
        return True
    return (_now() - since) < RENEWAL_GRACE_SECONDS


def render_session_recovery() -> None:
    """Get the session renewed, preferring the paths the user never sees.

    Embedded, the parent shell renews by re-sourcing the iframe with a fresh
    ``auth_token``; it is already listening for ``lex-auth-required`` because the
    proxy's iframe-breakout page posts exactly this message. Standalone there is
    no shell to ask, and a Streamlit component iframe is sandboxed without
    ``allow-top-navigation``, so the sign-in link -- a user gesture, always
    permitted -- is the honest fallback rather than a redirect that would be
    silently blocked.
    """
    login_url = f"{_current_base_url()}{_base_path()}/auth/login"
    login_js = json.dumps(login_url)

    components.html(
        "<script>(function(){var u=" + login_js + ";"
        "var m={type:'lex-auth-required',login:u,source:'streamlit'};"
        "try{window.parent.postMessage(m,'*');}catch(e){}"
        "try{if(window.top!==window.parent){window.top.postMessage(m,'*');}}catch(e){}"
        "})();</script>",
        height=0,
    )

    st.warning("⏳ Renewing your session…")
    st.markdown(
        f'<a href="{html.escape(login_url, quote=True)}" target="_top" rel="noopener">'
        "Sign in again</a> if this message does not clear.",
        unsafe_allow_html=True,
    )


# -------------------------
# Session state initialization
# -------------------------
def init_session_state() -> None:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "auth_method" not in st.session_state:
        st.session_state.auth_method = ""
    if "user_id" not in st.session_state:
        st.session_state.user_id = ""
    if "user_email" not in st.session_state:
        st.session_state.user_email = ""
    if "user_username" not in st.session_state:
        st.session_state.user_username = ""
    if "permissions" not in st.session_state:
        st.session_state.permissions = []
    if "user_info" not in st.session_state:
        st.session_state.user_info = {"sub": "", "email": "", "preferred_username": ""}
    if "keycloak_context_token" not in st.session_state:
        st.session_state.keycloak_context_token = ""
    if "access_token" not in st.session_state:
        st.session_state.access_token = ""
    if "refresh_token" not in st.session_state:
        st.session_state.refresh_token = ""
    if "token_exp" not in st.session_state:
        st.session_state.token_exp = 0
    if "expires_in" not in st.session_state:
        st.session_state.expires_in = None
    if "token_refresher_started" not in st.session_state:
        st.session_state.token_refresher_started = False
    if "token_refresher_thread" not in st.session_state:
        st.session_state.token_refresher_thread = None
    if "stop_token_refresher" not in st.session_state:
        st.session_state.stop_token_refresher = False
    if "token_renewal_failed" not in st.session_state:
        st.session_state.token_renewal_failed = False
    if "reauth_requested" not in st.session_state:
        st.session_state.reauth_requested = False


# -------------------------
# Authentication
# -------------------------
def authenticate_from_proxy_or_jwt() -> None:
    """Establish identity from the proxy's headers and keep the token current.

    Identity and token freshness are deliberately decoupled. The proxy already
    authenticated the request; whether the credential it handed over has since
    aged out is a renewal problem, not an identity problem, and answering it
    with "Missing user information" both misdescribed the failure and made it
    unrecoverable.
    """
    headers = getattr(st.context, "headers", {}) or {}
    h = normalize_headers(headers)
    _sync_tokens_from_headers(h)

    if st.session_state.authenticated:
        start_token_refresh_thread_if_needed()
        st.session_state.token_renewal_failed = not ensure_valid_access_token()
        sync_keycloak_context_from_access_token()
        return

    user_id = (
        h.get("x-streamlit-user-id")
        or headers.get("X-Streamlit-User-ID", "")
        or headers.get("X-Streamlit-User-Id", "")
        or ""
    )
    user_email = (
        h.get("x-streamlit-user-email")
        or headers.get("X-Streamlit-User-Email", "")
        or ""
    )
    user_username = (
        h.get("x-streamlit-user-username")
        or headers.get("X-Streamlit-User-Username", "")
        or ""
    )
    auth_method = (
        h.get("x-streamlit-auth-method")
        or headers.get("X-Streamlit-Auth-Method", "")
        or ""
    )

    if not user_id:
        token = bearer_from_headers(h)
        if token:
            claims = decode_jwt_claims_no_verify(token)
            user_id = claims.get("sub") or user_id
            user_email = claims.get("email") or user_email
            user_username = claims.get("preferred_username") or user_username
            if not auth_method:
                auth_method = "jwt"

    if not user_id and user_email:
        user_id = user_email

    if user_id:
        st.session_state.authenticated = True
        st.session_state.auth_method = auth_method or ("session" if not bearer_from_headers(h) else "jwt")
        st.session_state.user_id = user_id
        st.session_state.user_email = user_email
        st.session_state.user_username = user_username or (user_email.split("@")[0] if user_email else "")
        st.session_state.user_info = {
            "sub": st.session_state.user_id,
            "email": st.session_state.user_email,
            "preferred_username": st.session_state.user_username,
        }

        _sync_tokens_from_headers(h)
        start_token_refresh_thread_if_needed()
        st.session_state.token_renewal_failed = not ensure_valid_access_token()
        sync_keycloak_context_from_access_token()

        logger.info(
            f"Authenticated via {st.session_state.auth_method} as "
            f"{st.session_state.user_email or st.session_state.user_id}"
        )


def reset_streamlit_form_context() -> None:
    """
    Clears leaked Streamlit internal form context so the next st.form() starts clean.

    Streamlit marks a DG as "in a form" by setting dg._form_data (FormData). [web:46]
    Root DGs include st._main, st.sidebar, st._event, st.bottom. [web:59]
    Streamlit also tracks the active container stack (context_dg_stack). [web:48]
    """
    try:
        # 1) Clear known root DGs
        for attr in ("_main", "sidebar", "_event", "bottom"):
            dg = getattr(st, attr, None)
            if dg is not None and getattr(dg, "_form_data", None) is not None:
                dg._form_data = None

        # 2) Clear any DGs currently on the context stack (if available)
        try:
            from streamlit.delta_generator_singletons import context_dg_stack  # internal
            stack = context_dg_stack.get() or ()
            for dg in stack:
                if dg is not None and getattr(dg, "_form_data", None) is not None:
                    dg._form_data = None
        except Exception:
            # If internals move between Streamlit versions, ignore.
            pass

    except Exception:
        # Never break app rendering because of this reset.
        pass

# -------------------------
# App bootstrap
# -------------------------
if __name__ == "__main__":
    init_session_state()

    # If user clicked logout link and landed on ?logout=1, we clear session_state safely and stop.
    handle_logout_landing()

    authenticate_from_proxy_or_jwt()

    if not st.session_state.authenticated:
        # No identity on the connection at all: the request never carried the
        # proxy's headers. This is the one situation the message below actually
        # describes -- an expired token is a renewal problem, handled just after,
        # not by claiming the user information is missing.
        st.error("❌ Authentication Error: Missing user information.")
        st.info("Please access this application through the main portal.")
        st.stop()

    if not _within_renewal_grace():
        render_session_recovery()
        st.stop()

    # Form-safe logout control (won't break no matter what streamlit_structure.main() does)
    _logout_qp = st.query_params.get("is_logout_enabled")
    if _logout_qp is None or str(_logout_qp).lower() not in ("0", "false", "no", "n", "off"):
        render_logout_link()

    from lex.lex_app.settings import repo_name

    try:
        try:
            exec(f"import {repo_name}._streamlit_structure as streamlit_structure")
        except Exception:
            streamlit_structure = None

        reset_streamlit_form_context()
        params = st.query_params
        model = params.get("model")
        pk = params.get("pk")

        if model and pk:
            # Instance-level visualization
            try:
                from django.apps import apps

                model_class = apps.get_model(repo_name, model)
                model_obj = model_class.objects.filter(pk=pk).first()

                if model_obj is None:
                    st.error(f"❌ Object with ID {pk} not found")
                elif not hasattr(model_obj, "streamlit_main"):
                    st.error("❌ This model doesn't support visualization")
                else:
                    user = st.session_state.get("user_info")
                    model_obj.streamlit_main(user)

            except LookupError:
                st.error(f"❌ Model '{model}' not found")
            except Exception as e:
                st.error(f"❌ Error: {str(e)}")

        elif model and not pk:
            # Class-level visualization
            try:
                from django.apps import apps

                model_class = apps.get_model(repo_name, model)

                if not hasattr(model_class, "streamlit_class_main"):
                    st.error("❌ This model doesn't support class-level visualization")
                else:
                    user = st.session_state.get("user_info")
                    permissions = st.session_state.get("permissions")
                    model_class.streamlit_class_main()

            except LookupError:
                st.error(f"❌ Model '{model}' not found")
            except Exception as e:
                st.error(f"❌ Error: {str(e)}")

        else:
            # Default application structure
            if streamlit_structure and hasattr(streamlit_structure, "main"):
                streamlit_structure.main()

    except Exception as e:
        if os.getenv("DEPLOYMENT_ENVIRONMENT") != "PROD":
            raise e
        else:
            with st.expander(":red[An error occurred while trying to load the app.]"):
                st.error(traceback.format_exc())
