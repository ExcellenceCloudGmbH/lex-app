import html
import json
import logging
import os
import time
import traceback
import urllib.parse
from typing import Dict

import jwt
import requests
import streamlit as st
import streamlit.components.v1 as components

from lex.lex_app.streamlit.eager_frames import eager_frames_js
from lex.lex_app.streamlit.embed import remember_lex_app_origin
from lex.lex_app.streamlit.quackback import quackback_launcher_js
from lex.lex_app.streamlit.sidebar import (
    embedded_in_lex_app,
    render_logo,
    render_topbar,
    sidebar_state_for,
)
from lex.streamlit_theme import (
    DEBUG_PANEL_HEIGHT,
    embed_theme_from_params,
    theme_debug_enabled,
    theme_follow_enabled,
    theme_follower_html,
)
try:  # Streamlit >= 1.55 moved these; older layouts kept them under scriptrunner.
    from streamlit.runtime.scriptrunner_utils.exceptions import ScriptControlException
except ImportError:  # pragma: no cover - defensive across Streamlit versions
    try:
        from streamlit.runtime.scriptrunner.exceptions import ScriptControlException
    except ImportError:
        class ScriptControlException(BaseException):  # type: ignore[no-redef]
            """Fallback so the handlers below still have something to catch."""

logger = logging.getLogger(__name__)
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

from lex.lex_app.streamlit.auth import (  # noqa: F401 -- re-exported: tests and older code name them here
    INTERNAL_AUTH_HEADER,
    PROXY_INTERNAL_URL,
    PULL_ENDED,
    PULL_FAILED,
    PULL_OK,
    PULL_UNAVAILABLE,
    REFRESH_MAX_BACKOFF,
    REFRESH_MIN_INTERVAL,
    RENEWAL_GRACE_SECONDS,
    RENEW_LEAD_SECONDS,
    RENEW_POLL_SECONDS,
    TOKEN_SKEW_SECONDS,
    _TOKEN_REFRESH_LOCK,
    _adopt_header_token,
    _decode_exp_no_verify,
    _is_token_valid,
    _live_headers,
    _now,
    _pull_from_proxy,
    _pull_token_from_proxy,
    _read_stop_flag,
    _record_pull_outcome,
    _refresher_should_stop,
    _seconds_until_renewal,
    _session_is_live,
    _store_tokens,
    _sync_tokens_from_headers,
    _token_exp_from_state_or_decode,
    _token_refresher,
    bearer_from_headers,
    ensure_valid_access_token,
    get_bearer_token,
    normalize_headers,
    renew_access_token,
    start_token_refresh_thread_if_needed,
    strip_bearer,
)


# Guarded so the module can be imported (by tests, by tooling) without
# Streamlit commands firing at import time. Streamlit executes this file with
# ``__name__ == "__main__"``, so the app itself is unaffected.
if __name__ == "__main__":
    # Read BEFORE set_page_config, which is what carries the sidebar state.
    # Streamlit dropped the "must be the first command" rule, and reading a
    # query parameter enqueues no delta in any case.
    st.set_page_config(
        layout="wide",
        initial_sidebar_state=sidebar_state_for(embedded_in_lex_app(st.query_params)),
    )


def normalize(d: Dict[str, str]) -> Dict[str, str]:
    return {(k or "").strip().lower(): (v or "").strip() for k, v in (d or {}).items()}


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

    from lex.api.keycloak import uma_client

    endpoints = uma_client.KeycloakEndpoints(
        token_url=f"{keycloak_url.rstrip('/')}/realms/{realm_name}/protocol/openid-connect/token",
        userinfo_url=f"{keycloak_url.rstrip('/')}/realms/{realm_name}/protocol/openid-connect/userinfo",
    )
    try:
        # The shared per-call client: the token travels on this request only.
        # TLS stays verified here, as it always was for this call.
        return uma_client.fetch_userinfo(access_token, endpoints=endpoints, verify=True)
    except uma_client.KeycloakCallError as e:
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


def _logout_href() -> str:
    """Where the log-out control points.

    Split out from the old ``render_logout_link`` so the URL and the markup are
    separate concerns: the account block at the bottom of the sidebar now owns
    the rendering, and only needs the destination.
    """
    base_url = _current_base_url()
    base_path = _base_path()

    # After upstream logout, land on /?logout=1 to clear Streamlit session_state
    logout_landing_abs = f"{base_url}{base_path}/?logout=1"
    rd = urllib.parse.quote(logout_landing_abs, safe="")

    auth_method = st.session_state.get("auth_method", "session")
    if auth_method in ("session", "handoff"):
        # handoff: the proxy revokes the dashboard's binding and signs out through the app.
        return f"{base_path}/oauth2/sign_out?rd={rd}"
    # JWT: we cannot revoke upstream; just clear local session_state on landing.
    return f"{base_path}/?logout=1"


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


def _uses_handoff() -> bool:
    """Whether this session signed in through the Django-anchored hand-off (proxy header)."""
    return st.session_state.get("auth_method") == "handoff"


def needs_session_recovery() -> bool:
    """Whether the page must stop and ask for the session to be recovered.

    With the hand-off the proxy's answers are definite: ``/auth/token`` says 401 only when the
    sign-in has ended, so that asks for recovery at once -- and 503 (Keycloak or the database
    unreachable) never does: the dashboard keeps going on what it has and retries underneath.
    Without it, a failed renewal is ambiguous, so it gets the grace window first.
    """
    if _uses_handoff():
        return bool(st.session_state.get("token_session_ended"))
    return not _within_renewal_grace()


#: The proxy's sign-in-required page (served at the root of the dashboard origin).
AUTH_REQUIRED_PATH = "/auth/required"
#: The proxy's answer to "does this browser's dashboard sign-in still stand?" (hand-off only).
AUTH_STATUS_PATH = "/auth/status"
#: How often the sign-in watcher asks, while the page is visible.
AUTH_WATCH_INTERVAL_MS = 60_000


def _in_dashboard_document(code: str) -> str:
    """A component-frame script that runs ``code`` as the dashboard document's own script.

    A component frame is sandboxed, and a sandboxed frame may navigate itself but not the
    dashboard document around it ("disallowed from navigating its ancestors"): a
    ``location.replace`` on its parent is refused. It is same-origin with that document, though,
    so it adds a ``<script>`` element to it -- which runs in the dashboard document's own realm,
    as that document's code, and a document may always navigate itself.
    """
    source = json.dumps(code).replace("<", "\\u003c")
    return (
        "<script>(function(){try{var p=window.parent;if(!p||p===window){return;}"
        "var s=p.document.createElement('script');s.textContent=" + source + ";"
        "(p.document.head||p.document.documentElement).appendChild(s);}catch(e){}})();</script>"
    )


def _go_to_auth_required_js() -> str:
    """Dashboard-document code: replace this page with ``/auth/required`` for this page's URL."""
    return (
        "location.replace(" + json.dumps(AUTH_REQUIRED_PATH)
        + "+'?reason=session_ended&next='+encodeURIComponent(location.pathname+location.search));"
    )


def _handoff_recovery_js() -> str:
    """Send the *dashboard document* to the proxy's sign-in-required page, which tells the
    frontend around it -- the frontend only listens to the dashboard frame itself, not to the
    component frames inside it. Standalone (no frontend around it) the page stays, with its
    sign-in link. Runs in the dashboard document (:func:`_in_dashboard_document`)."""
    return _in_dashboard_document("(function(){if(window.top===window){return;}" + _go_to_auth_required_js() + "})();")


def _auth_watch_js() -> str:
    """The sign-in watcher, installed once into the dashboard document of a hand-off page.

    The token pull notices an ended sign-in only while the page's socket is up. Once the socket
    has dropped, Streamlit retries the reconnect -- refused with 4401 -- forever, and the frame
    would sit on "Connecting" until someone reloads it. So the dashboard document asks the proxy's
    ``/auth/status`` (a cookie-authenticated read, no Keycloak call) every minute while visible,
    and as soon as it becomes visible again or the browser comes back online; a 401 sends it to
    ``/auth/required``, which posts the v2 ``required`` message to the frontend around it (and,
    standalone, offers the sign-in). 503 or no network: nothing, ask again later. 404 (the proxy
    has no hand-off): stop asking.
    """
    code = (
        "(function(){if(window.__lexAuthWatch){return;}window.__lexAuthWatch=1;var busy=0,timer=0;"
        "function check(){if(busy||document.hidden){return;}busy=1;"
        "fetch(" + json.dumps(AUTH_STATUS_PATH) + ",{credentials:'same-origin',cache:'no-store'}).then("
        "function(r){busy=0;if(r.status===401){clearInterval(timer);" + _go_to_auth_required_js() + "}"
        "else if(r.status===404){clearInterval(timer);}},function(){busy=0;});}"
        "timer=setInterval(check," + str(AUTH_WATCH_INTERVAL_MS) + ");"
        "document.addEventListener('visibilitychange',check);window.addEventListener('online',check);})();"
    )
    return _in_dashboard_document(code)


def render_session_recovery() -> None:
    """Get the session renewed, preferring the paths the user never sees.

    With the hand-off, the dashboard document goes to the proxy's ``/auth/required`` page,
    which posts ``{"type": "lex-auth", "v": 2, "event": "required", "surface": "streamlit"}`` to
    the frontend around it; the frontend renews its own sign-in and reloads the dashboard in
    place. Standalone, the sign-in link goes through the hand-off at top level.

    Otherwise (the legacy paths):

    Embedded, the parent shell renews by re-sourcing the iframe with a fresh
    ``auth_token``; it is already listening for ``lex-auth-required`` because the
    proxy's iframe-breakout page posts exactly this message. Standalone there is
    no shell to ask, and a Streamlit component iframe is sandboxed without
    ``allow-top-navigation``, so the sign-in link -- a user gesture, always
    permitted -- is the honest fallback rather than a redirect that would be
    silently blocked.
    """
    if _uses_handoff():
        components.html(_handoff_recovery_js(), height=0)
        st.warning("⏳ Your sign-in has ended.")
        # The dashboard root starts a fresh hand-off: through the app's sign-in when needed.
        st.markdown(
            f'<a href="{html.escape(_current_base_url() + _base_path() + "/", quote=True)}" '
            'target="_top" rel="noopener">Sign in again</a>',
            unsafe_allow_html=True,
        )
        return

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
    if "token_session_ended" not in st.session_state:
        st.session_state.token_session_ended = False
    if "token_auth_unavailable" not in st.session_state:
        st.session_state.token_auth_unavailable = False


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
# The bootstrap CALLS moved into `if __name__ == "__main__":` on lex-app-v2.
# What stays out here are the definitions that block uses.

def _url_embed_theme() -> str:
    """The mode this page's own URL asks for, or "" if it asks for nothing.

    Thin adapter over :func:`lex.streamlit_theme.embed_theme_from_params` -- the
    parsing lives there so it is reachable by tests, which cannot import this
    module (it runs auth and calls ``st.stop()`` at import time).
    """
    raw = st.query_params.get_all("embed_options") if hasattr(st.query_params, "get_all") else []
    if not raw:
        single = st.query_params.get("embed_options")
        raw = single if isinstance(single, list) else ([single] if single else [])
    return embed_theme_from_params(raw)


def render_theme_follower() -> None:
    """Emit the zero-height block that follows theme changes made in lex-app.

    The relay in ``lex/proxy.py`` writes the agreed mode into THIS origin's
    localStorage, raising a ``storage`` event in every Streamlit tab -- embedded
    or standalone. The script that reacts to it lives in
    :func:`lex.streamlit_theme.theme_follower_html`, which documents why the
    reaction is a reload and what stops it firing needlessly.
    """
    import streamlit.components.v1 as components

    # Theme following is OPT-IN. It works by reloading with
    # ?embed_options=<mode>_theme, which sits at the top of Streamlit's own
    # precedence — above the stored theme and above Streamlit's theme menu. A
    # page that follows has therefore lost its theme control: the menu stops
    # working and the app file cannot override it either, because a query
    # parameter is not something app code gets a say in. Worth it when someone
    # asked the two surfaces to match; not worth imposing by default.
    follow = theme_follow_enabled()
    debug = follow and theme_debug_enabled()

    # The eager-frames script is unconditional: it is about WHEN component
    # frames load, and has nothing to do with the theme.
    body = f"<script>{eager_frames_js()}</script>"
    # The feedback launcher, and it decides for itself whether to appear: a
    # framed Streamlit page must not stack a second one over lex-app's, and only
    # the browser knows whether this page is framed. Unconditional here for the
    # same reason the two scripts above are -- whose chrome this page sits
    # inside is not a theme question.
    try:
        from lex.lex_app.streamlit.embed import _resolve_base_url

        body = quackback_launcher_js(_resolve_base_url()) + body
    except Exception:
        # A missing launcher is a missing feedback button; a raise here would
        # take the whole page down with it.
        logger.warning("Could not mount the feedback launcher", exc_info=True)
    if follow:
        body = theme_follower_html(_url_embed_theme(), debug=debug) + body
    if _uses_handoff():
        # A hand-off page learns that its sign-in ended even after its socket dropped.
        body += _auth_watch_js()

    components.html(body, height=DEBUG_PANEL_HEIGHT if debug else 0)


# Form-safe logout control (won't break no matter what streamlit_structure.main() does).
#
# Only DECIDED here; rendered after the app structure, in the `finally` at the
# bottom of this file. Streamlit lays the sidebar out in call order, so
# rendering it here pinned it to the top — above the app's own navigation, which
# is the wrong place for a logout. Rendering it last puts it at the bottom.
#
# The `finally` is what preserves the original guarantee: the control still
# appears even if streamlit_structure.main() raises.
_logout_qp = st.query_params.get("is_logout_enabled")
LOGOUT_ENABLED = _logout_qp is None or str(_logout_qp).lower() not in (
    "0",
    "false",
    "no",
    "n",
    "off",
)

# -------------------------
# Main app
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

    if needs_session_recovery():
        render_session_recovery()
        st.stop()

    # The frontend that frames this dashboard names its own origin (lex_app_origin); once the
    # instance confirms it, lex_view() embeds app pages from that same origin -- the one the
    # user is signed in on, same-site with this dashboard.
    remember_lex_app_origin()

    from lex.lex_app.settings import repo_name

    # ── Framed by lex-app: no sidebar at all ────────────────────────────
    # Not "collapsed", and not "empty" -- gone. lex-app already draws a sidenav,
    # the logo, the signed-in user and a way out, immediately to the left of this
    # frame. A second sidebar inside it is the same furniture twice, and the
    # inner one navigates a different app.
    #
    # Decided here rather than in CSS alone so the chrome is never BUILT: a
    # hidden logo is still an st.logo call, and a hidden account block still
    # renders a display name into the page. A guest surface should not construct
    # host furniture, not merely avoid showing it.
    #
    # Read BEFORE the try, because the `finally` below consults it. Computing it
    # inside would mean an early failure in main() raised NameError from the
    # cleanup path and buried the real error underneath it.
    EMBEDDED = embedded_in_lex_app(st.query_params)

    try:
        try:
            exec(f"import {repo_name}._streamlit_structure as streamlit_structure")
        except Exception:
            streamlit_structure = None

        if not EMBEDDED:
            # The logo only, and early: st.logo renders into Streamlit's header
            # slot, which sits ABOVE even the page navigation.
            render_logo(st)
            # The account bar, before anything the app draws, so it sits at the
            # top of the content column -- and so the way out is on screen
            # before main() has had a chance to fail. That last property is why
            # the account block used to render from a `finally`.
            render_topbar(
                st,
                st.session_state,
                logout_href=_logout_href() if LOGOUT_ENABLED else None,
            )
        # Embedded: lex-app draws the logo and the account itself, and the
        # sidebar arrives collapsed via `initial_sidebar_state` above.

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

    finally:
        # Zero-height and inert; also in `finally` so theme following survives a
        # failure in main(). A page stuck on the wrong theme after an error is a
        # small thing, but it is free to avoid.
        render_theme_follower()
