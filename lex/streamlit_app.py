import base64
import json
import os
import traceback
import urllib.parse
import threading
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Dict

import jwt
import requests
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

from lex.api.views.authentication.KeycloakManager import KeycloakManager
from django.conf import settings

logger = logging.getLogger(__name__)
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# -------------------------
# Token refresh: config
# -------------------------
TOKEN_SKEW_SECONDS = 10          # refresh 10s before exp
REFRESH_MIN_INTERVAL = 15        # floor sleep
REFRESH_MAX_BACKOFF = 300        # cap backoff to 5 minutes


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


def _compute_next_refresh_at(exp: int, expires_in: int | None) -> int:
    if exp:
        return max(_now() + REFRESH_MIN_INTERVAL, exp - TOKEN_SKEW_SECONDS)
    if expires_in:
        return _now() + max(REFRESH_MIN_INTERVAL, int(expires_in) - TOKEN_SKEW_SECONDS)
    return _now() + REFRESH_MIN_INTERVAL


def _post_refresh(refresh_token: str) -> dict | None:
    url = _oidc_token_endpoint()
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


def _token_refresher(stop_key: str = "stop_token_refresher") -> None:
    backoff = 5
    while not st.session_state.get(stop_key, False):
        access = st.session_state.get("access_token") or ""
        refresh = st.session_state.get("refresh_token") or ""
        exp = st.session_state.get("token_exp") or _decode_exp_no_verify(access)
        expires_in = st.session_state.get("expires_in")

        next_at = _compute_next_refresh_at(exp, expires_in)
        sleep_for = max(1, next_at - _now())

        end_at = _now() + sleep_for
        while _now() < end_at:
            if st.session_state.get(stop_key, False):
                return
            time.sleep(min(1.0, end_at - _now()))

        if st.session_state.get(stop_key, False):
            return

        if not refresh:
            backoff = min(REFRESH_MAX_BACKOFF, backoff * 2)
            time.sleep(backoff)
            continue

        tok = _post_refresh(refresh)
        if tok and tok.get("access_token"):
            _update_tokens_from_response(tok)
            backoff = 5
        else:
            backoff = min(REFRESH_MAX_BACKOFF, backoff * 2)
            time.sleep(backoff)


def start_token_refresh_thread_if_needed() -> None:
    if st.session_state.get("token_refresher_started"):
        return

    if not st.session_state.get("refresh_token"):
        headers = getattr(st.context, "headers", {}) or {}
        h = normalize_headers(headers)
        rt = h.get("x-streamlit-refresh-token") or ""
        if rt:
            st.session_state.refresh_token = rt

    if not st.session_state.get("refresh_token"):
        st.session_state.token_refresher_started = True
        return

    st.session_state.stop_token_refresher = False
    th = threading.Thread(target=_token_refresher, name="token_refresher", daemon=True)
    add_script_run_ctx(th, get_script_run_ctx())
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
    for name in ("authorization", "x-forwarded-access-token", "x-auth-request-access-token"):
        val = h.get(name)
        if not val:
            continue
        return strip_bearer(val)
    return None


def bearer_from_headers(h: Dict[str, str]) -> Optional[str]:
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
        st.session_state.permissions = {}
    if "user_info" not in st.session_state:
        st.session_state.user_info = {"sub": "", "email": "", "preferred_username": ""}


# -------------------------
# Authentication
# -------------------------
def authenticate_from_proxy_or_jwt() -> None:
    if st.session_state.authenticated:
        return

    headers = getattr(st.context, "headers", {}) or {}
    h = normalize_headers(headers)

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

    permissions = {}
    if perms_raw:
        try:
            permissions = json.loads(perms_raw)
        except Exception:
            permissions = {}

    if user_id:
        st.session_state.authenticated = True
        st.session_state.auth_method = auth_method or ("session" if not bearer_from_headers(h) else "jwt")
        st.session_state.user_id = user_id
        st.session_state.user_email = user_email
        st.session_state.user_username = user_username or (user_email.split("@")[0] if user_email else "")
        st.session_state.permissions = permissions
        st.session_state.user_info = {
            "sub": st.session_state.user_id,
            "email": st.session_state.user_email,
            "preferred_username": st.session_state.user_username,
        }

        token_from_header = bearer_from_headers(h)
        st.session_state.access_token = token_from_header
        st.session_state.token_exp = _decode_exp_no_verify(token_from_header) if token_from_header else 0

        rt_hdr = h.get("x-streamlit-refresh-token")
        if rt_hdr:
            st.session_state.refresh_token = rt_hdr

        start_token_refresh_thread_if_needed()

        logger.info(
            f"Authenticated via {st.session_state.auth_method} as "
            f"{st.session_state.user_email or st.session_state.user_id}"
        )


def reset_streamlit_form_context() -> None:
    """
    Clears leaked Streamlit internal form context so the next st.form() starts clean.

    Streamlit marks a DG as "in a form" by setting dg._form_data (FormData). [web:46]
    Root DGs include st._main, st.sidebar, st._event, st._bottom. [web:59]
    Streamlit also tracks the active container stack (context_dg_stack). [web:48]
    """
    try:
        # 1) Clear known root DGs
        for attr in ("_main", "sidebar", "_event", "_bottom"):
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
init_session_state()

# If user clicked logout link and landed on ?logout=1, we clear session_state safely and stop.
handle_logout_landing()

authenticate_from_proxy_or_jwt()

if not st.session_state.authenticated:
    st.error("❌ Authentication Error: Missing user information.")
    st.info("Please access this application through the main portal.")
    st.stop()

# Form-safe logout control (won't break no matter what streamlit_structure.main() does)
render_logout_link()

# -------------------------
# Main app
# -------------------------
if __name__ == "__main__":
    from lex.lex_app.settings import repo_name

    try:
        exec(f"import {repo_name}._streamlit_structure as streamlit_structure")
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
            streamlit_structure.main()

    except Exception as e:
        if os.getenv("DEPLOYMENT_ENVIRONMENT") != "PROD":
            raise e
        else:
            with st.expander(":red[An error occurred while trying to load the app.]"):
                st.error(traceback.format_exc())
