import base64
import json
from typing import Optional, Dict
import os
import traceback
import urllib.parse
import jwt
import requests
from datetime import datetime, timezone
import logging

import streamlit as st

from lex.api.views.authentication.KeycloakManager import KeycloakManager
from django.conf import settings

logger = logging.getLogger(__name__)


import threading
import time
import requests
import os
import jwt
import logging
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

log = logging.getLogger(__name__)

# -------------------------
# Token refresh: config
# -------------------------
TOKEN_SKEW_SECONDS = 10          # refresh 60s before exp
REFRESH_MIN_INTERVAL = 15        # floor sleep
REFRESH_MAX_BACKOFF = 300        # cap backoff to 5 minutes

def _oidc_token_endpoint() -> str:
    base = os.getenv("KEYCLOAK_URL").rstrip("/")
    realm = os.getenv("KEYCLOAK_REALM")
    return f"{base}/realms/{realm}/protocol/openid-connect/token"

def _decode_exp_no_verify(token: str) -> int:
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
        return int(claims.get("exp", 0)) if claims else 0
    except Exception:
        return 0

def _now() -> int:
    return int(time.time())

def _compute_next_refresh_at(exp: int, expires_in: int | None) -> int:
    if exp:
        return max(_now() + REFRESH_MIN_INTERVAL, exp - TOKEN_SKEW_SECONDS)
    if expires_in:
        return _now() + max(REFRESH_MIN_INTERVAL, int(expires_in) - TOKEN_SKEW_SECONDS)
    # Unknown expiry: retry soon
    return _now() + REFRESH_MIN_INTERVAL

def _post_refresh(refresh_token: str) -> dict | None:
    url = _oidc_token_endpoint()
    # client_id = os.getenv("KEYCLOAK_CLIENT_ID") or ""
    # client_secret = os.getenv("KEYCLOAK_CLIENT_SECRET") or None
    client_id = os.getenv("OIDC_RP_CLIENT_ID")
    client_secret = os.getenv("OIDC_RP_CLIENT_SECRET")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
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

def _update_tokens_from_response(tok: dict) -> None:
    access = tok.get("access_token") or ""
    refresh = tok.get("refresh_token") or st.session_state.get("refresh_token") or ""
    expires_in = tok.get("expires_in")
    exp = _decode_exp_no_verify(access) if access else 0
    st.session_state.access_token = access
    st.session_state.refresh_token = refresh
    st.session_state.token_exp = exp
    st.session_state.expires_in = expires_in
    # Optionally hydrate user info again if needed by downstream code
    # user = get_user_info(access) or st.session_state.get("user_info")

def _token_refresher(stop_key: str = "stop_token_refresher") -> None:
    backoff = 5
    while not st.session_state.get(stop_key, False):
        access = st.session_state.get("access_token") or ""
        refresh = st.session_state.get("refresh_token") or ""
        exp = st.session_state.get("token_exp") or _decode_exp_no_verify(access)
        expires_in = st.session_state.get("expires_in")

        # Decide next refresh time
        next_at = _compute_next_refresh_at(exp, expires_in)
        sleep_for = max(1, next_at - _now())
        # Sleep with small increments so stop flag is responsive
        end_at = _now() + sleep_for
        while _now() < end_at:
            if st.session_state.get(stop_key, False):
                return
            time.sleep(min(1.0, end_at - _now()))

        if st.session_state.get(stop_key, False):
            return

        if not refresh:
            # Nothing to refresh; try again later
            backoff = min(REFRESH_MAX_BACKOFF, backoff * 2)
            time.sleep(backoff)
            continue

        tok = _post_refresh(refresh)
        print("Access Token", tok.get('access_token'))
        if tok and tok.get("access_token"):
            _update_tokens_from_response(tok)
            backoff = 5  # reset backoff on success
        else:
            # Failure: backoff and retry
            backoff = min(REFRESH_MAX_BACKOFF, backoff * 2)
            time.sleep(backoff)

def start_token_refresh_thread_if_needed() -> None:
    if st.session_state.get("token_refresher_started"):
        return
    # Require a refresh_token; fall back to header if proxy forwards it
    if not st.session_state.get("refresh_token"):
        # Optional header capture if proxy forwards refresh token
        headers = getattr(st.context, "headers", {}) or {}
        rt = headers.get("x-streamlit-refresh-token") or ""
        if rt:
            st.session_state.refresh_token = rt

    if not st.session_state.get("refresh_token"):
        # No refresh path; do not start thread
        st.session_state.token_refresher_started = True
        return

    st.session_state.stop_token_refresher = False
    th = threading.Thread(target=_token_refresher, name="token_refresher", daemon=True)
    add_script_run_ctx(th, get_script_run_ctx())
    th.start()
    st.session_state.token_refresher_started = True
    st.session_state.token_refresher_thread = th

def normalize(d: Dict[str, str]) -> Dict[str, str]:
    """Normalize dictionary keys and values to lowercase."""
    return {(k or "").strip().lower(): (v or "").strip() for k, v in (d or {}).items()}


def get_bearer_token(headers: Dict[str, str]) -> Optional[str]:
    """Extract bearer token from various header formats."""
    for name in ("authorization", "x-forwarded-access-token", "x-auth-request-access-token"):
        val = headers.get(name)
        if not val:
            continue
        return strip_bearer(val)
    return None


def strip_bearer(value: str) -> str:
    """Remove 'Bearer ' prefix from token."""
    v = (value or "").strip()
    if v.lower().startswith("bearer "):
        return v.split(" ", 1)[1].strip()
    return v


def get_user_info(access_token):
    """Get user info from Keycloak using access token."""
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


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# -------------------------
# Session state initialization
# -------------------------
def init_session_state() -> None:
    # Ensure all keys exist; never assume presence across reruns
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
        st.session_state.permissions = {}
    if "user_info" not in st.session_state:
        st.session_state.user_info = {"sub": "", "email": "", "preferred_username": ""}


# -------------------------
# Header utilities
# -------------------------
def normalize_headers(h: Dict[str, str]) -> Dict[str, str]:
    # Case-insensitive access
    return {(k or "").strip().lower(): (v or "").strip() for k, v in (h or {}).items()}


def bearer_from_headers(h: Dict[str, str]) -> Optional[str]:
    # Prefer Authorization, fallback to X-Forwarded-Access-Token, X-Auth-Request-Access-Token
    for name in ("authorization", "x-forwarded-access-token", "x-auth-request-access-token"):
        v = h.get(name)
        if not v:
            continue
        v = v.strip()
        if v.lower().startswith("bearer "):
            return v.split(" ", 1)[1].strip()
        return v
    return None


def decode_jwt_claims_no_verify(token: str) -> Dict:
    # Proxy already validated upstream; here we only need claims to hydrate identity
    try:
        return jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except Exception as e:
        logger.warning(f"JWT decode (no verify) failed: {e}")
        return {}


# -------------------------
# Authentication
# -------------------------
def authenticate_from_proxy_or_jwt() -> None:
    # If already authenticated in this session, do not re-evaluate
    if st.session_state.authenticated:
        return

    headers = getattr(st.context, "headers", {}) or {}
    h = normalize_headers(headers)
    print("Headers h", h)
    print("Headers", headers)

    # Try Streamlit identity headers from proxy
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
    perms_raw = (
        h.get("x-streamlit-user-permissions")
        or headers.get("X-Streamlit-User-Permissions", "")
        or ""
    )

    # If user_id empty, attempt to derive from JWT claims present in headers
    # Works for iframe flow (JWT) and for session flow when proxy added Authorization bearer
    if not user_id:
        token = bearer_from_headers(h)
        print("Token", token)
        if token:
            claims = decode_jwt_claims_no_verify(token)
            user_id = claims.get("sub") or user_id
            user_email = claims.get("email") or user_email
            user_username = claims.get("preferred_username") or user_username
            if not auth_method:
                auth_method = "jwt"

    # Fallback: if still no user_id but email exists, use email as stable identifier
    if not user_id and user_email:
        user_id = user_email

    # Parse permissions JSON safely
    permissions = {}
    if perms_raw:
        try:
            permissions = json.loads(perms_raw)
        except Exception:
            permissions = {}

    # Hydrate session state
    if user_id:
        st.session_state.authenticated = True
        st.session_state.auth_method = auth_method or ("session" if not (bearer_from_headers(h)) else "jwt")
        st.session_state.user_id = user_id
        st.session_state.user_email = user_email
        st.session_state.user_username = user_username or (user_email.split("@")[0] if user_email else "")
        st.session_state.permissions = permissions
        st.session_state.user_info = {
            "sub": st.session_state.user_id,
            "email": st.session_state.user_email,
            "preferred_username": st.session_state.user_username,
        }
        token_from_header = bearer_from_headers(h)  # already available

        st.session_state.access_token = token_from_header
        st.session_state.token_exp = _decode_exp_no_verify(token_from_header)

        rt_hdr = h.get("x-streamlit-refresh-token")
        if rt_hdr:
            st.session_state.refresh_token = rt_hdr

        # Kick off background refresh if a refresh token is present
        start_token_refresh_thread_if_needed()

        logger.info(
            f"Authenticated via {st.session_state.auth_method} as "
            f"{st.session_state.user_email or st.session_state.user_id}"
        )
    else:
        # Not authenticated: leave session_state.authenticated as False
        pass


# -------------------------
# App bootstrap
# -------------------------
init_session_state()
authenticate_from_proxy_or_jwt()

# Fail closed if not authenticated; Streamlit reruns on interactions so session_state persists per session
if not st.session_state.authenticated:
    st.error("❌ Authentication Error: Missing user information.")
    st.info("Please access this application through the main portal.")
    st.stop()

if __name__ == '__main__':
    from lex.lex_app.settings import repo_name

    try:
        exec(f"import {repo_name}._streamlit_structure as streamlit_structure")

        # Your existing model rendering logic...
        params = st.query_params
        model = params.get("model")
        pk = params.get("pk")

        if model and pk:
            # Instance-level visualization
            try:
                from django.apps import apps
                from lex.lex_app.settings import repo_name

                model_class = apps.get_model(repo_name, model)
                model_obj = model_class.objects.filter(pk=pk).first()

                if model_obj is None:
                    st.error(f"❌ Object with ID {pk} not found")
                elif not hasattr(model_obj, 'streamlit_main'):
                    st.error(f"❌ This model doesn't support visualization")
                else:
                    # Pass user info from session state
                    user = st.session_state.get('user_info')
                    model_obj.streamlit_main(user)

            except LookupError:
                st.error(f"❌ Model '{model}' not found")
            except Exception as e:
                st.error(f"❌ Error: {str(e)}")

        elif model and not pk:
            # Class-level visualization
            try:
                from django.apps import apps
                from lex.lex_app.settings import repo_name

                model_class = apps.get_model(repo_name, model)

                if not hasattr(model_class, 'streamlit_class_main'):
                    st.error(f"❌ This model doesn't support class-level visualization")
                else:
                    # Pass user info and permissions from session state
                    user = st.session_state.get('user_info')
                    permissions = st.session_state.get('permissions')
                    model_class.streamlit_class_main()

            except LookupError:
                st.error(f"❌ Model '{model}' not found")
            except Exception as e:
                st.error(f"❌ Error: {str(e)}")

        else:
            # Default application structure
            streamlit_structure.main()
            if st.button("Logout"):
                auth_method = st.session_state.get('auth_method', 'session')

                if auth_method == 'jwt':
                    # For JWT auth, just clear session and show message
                    st.session_state.stop_token_refresher = True
                    th = st.session_state.get("token_refresher_thread")
                    if th and th.is_alive():
                        # Give it a moment to exit; thread is daemon, so app exit also cleans up
                        th.join(timeout=1.0)
                    st.session_state.clear()
                    st.success("✅ Logged out successfully. You can close this window.")
                    st.stop()
                else:
                    # For session auth, redirect to logout
                    rd = urllib.parse.quote("http://localhost:8501", safe="")
                    st.markdown(
                        f"<meta http-equiv='refresh' content='0;url=/oauth2/sign_out?rd={rd}'>",
                        unsafe_allow_html=True
                    )

        # Logout functionality (adjust based on auth method)


    except Exception as e:
        if os.getenv("DEPLOYMENT_ENVIRONMENT") != "PROD":
            raise e
        else:
            with st.expander(":red[An error occurred while trying to load the app.]"):
                st.error(traceback.format_exc())