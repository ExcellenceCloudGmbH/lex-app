"""The Streamlit session's access token: holding it, renewing it, and handing it out.

This lives in an importable module, not in ``lex/streamlit_app.py``, for two reasons.
Streamlit executes ``streamlit_app.py`` afresh as ``__main__`` on every script run, so a
lock or a constant defined there is a new object each run -- the background refresher
and the next run would never share one. And a dashboard cannot import ``__main__``, so
anything it needs at call time (``current_access_token``, ``lex_api``) has to live in a
module it *can* import. ``streamlit_app.py`` imports everything below back by name.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional

import jwt
import requests
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

try:  # Streamlit >= 1.55 moved these; older layouts kept them under scriptrunner.
    from streamlit.runtime.scriptrunner_utils.exceptions import ScriptControlException
except ImportError:  # pragma: no cover - defensive across Streamlit versions
    try:
        from streamlit.runtime.scriptrunner.exceptions import ScriptControlException
    except ImportError:
        class ScriptControlException(BaseException):  # type: ignore[no-redef]
            """Fallback so the handlers below still have something to catch."""

logger = logging.getLogger(__name__)
log = logger

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


def _decode_exp_no_verify(token: str) -> int:
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
        return int(claims.get("exp", 0)) if claims else 0
    except Exception:
        return 0


def _now() -> int:
    return int(time.time())


def _store_tokens(access: str, refresh: str | None = None, expires_in=None) -> None:
    """Record an access token (Streamlit never receives a refresh token; ``refresh`` stays None)."""
    with _TOKEN_REFRESH_LOCK:
        previous = (st.session_state.get("access_token") or "").strip()
        st.session_state.access_token = access or ""
        if refresh is not None:
            st.session_state.refresh_token = refresh
        st.session_state.token_exp = _decode_exp_no_verify(access) if access else 0
        st.session_state.expires_in = expires_in
        _refresh_stored_api_headers(previous, access)


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
        _refresh_stored_api_headers(current, token_from_header)
        return True


def _sync_tokens_from_headers(h: Dict[str, str]) -> None:
    # Only the access token. Streamlit never holds a refresh token: the proxy (legacy) or the
    # app's sign-in (hand-off) is the one place a token is refreshed.
    _adopt_header_token(h)


#: Outcomes of asking the proxy for a fresh token (``_pull_from_proxy``).
PULL_OK = "ok"
#: The sign-in is over (the hand-off answers 401 only then).
PULL_ENDED = "ended"
#: Keycloak, the database or the proxy could not answer right now (503, 5xx, unreachable).
PULL_UNAVAILABLE = "unavailable"
#: Nothing to ask with (no proxy secret, no cookie) or an answer that carried no token.
PULL_FAILED = "failed"


def _pull_from_proxy(cookie_header: str) -> str:
    """Ask the proxy for a currently-valid access token for this browser session.

    The proxy refreshes on our behalf -- through the app's sign-in with the hand-off, through
    its own token store otherwise -- so this succeeds even when the token we hold has already
    expired. Returns one of the ``PULL_*`` outcomes.
    """
    secret = os.getenv("LEX_INTERNAL_AUTH_SECRET") or ""
    if not secret or not cookie_header:
        return PULL_FAILED

    try:
        resp = requests.get(
            f"{PROXY_INTERNAL_URL}/auth/token",
            headers={"Cookie": cookie_header, INTERNAL_AUTH_HEADER: secret},
            timeout=10,
        )
    except Exception as e:
        log.warning("Token pull from proxy failed: %s", e)
        return PULL_UNAVAILABLE

    if resp.status_code == 401:
        log.info("Token pull from proxy: the sign-in has ended (401)")
        return PULL_ENDED
    if resp.status_code >= 500:
        log.warning("Token pull from proxy returned %s; retrying later", resp.status_code)
        return PULL_UNAVAILABLE
    if resp.status_code != 200:
        log.warning("Token pull from proxy returned %s", resp.status_code)
        return PULL_FAILED

    try:
        data = resp.json()
    except Exception:
        return PULL_FAILED

    access = (data.get("access_token") or "").strip()
    if not access:
        return PULL_FAILED

    _store_tokens(access, refresh=None, expires_in=None)
    return PULL_OK


def _pull_token_from_proxy(cookie_header: str) -> bool:
    """``True`` when the proxy handed over a fresh access token (see ``_pull_from_proxy``)."""
    return _pull_from_proxy(cookie_header) == PULL_OK


def _record_pull_outcome(outcome: str) -> None:
    """Remember what the last pull said: an ended sign-in and an outage are told apart."""
    st.session_state.token_session_ended = outcome == PULL_ENDED
    st.session_state.token_auth_unavailable = outcome == PULL_UNAVAILABLE


def renew_access_token(min_ttl: int = TOKEN_SKEW_SECONDS, force: bool = False) -> bool:
    """Obtain a fresh access token, in order of decreasing authority.

    ``min_ttl`` is how many seconds a token must still have to count as fresh enough.
    ``force`` skips the "already fresh" short-cut and asks the proxy regardless -- for
    when the API has just refused the token we hold, whatever its ``exp`` says.
    """
    with _TOKEN_REFRESH_LOCK:
        # Someone else may have renewed while we waited for the lock; renewal
        # involves network round trips, so re-checking here keeps a rerun from
        # blocking behind a refresh that has already delivered.
        if not force and _is_token_valid(st.session_state.get("access_token") or "", skew_seconds=min_ttl):
            return True

        h = _live_headers()

        # 1) A reconnected socket or a re-sourced iframe may already have
        #    delivered a newer credential in the handshake.
        if _adopt_header_token(h) and _is_token_valid(
            st.session_state.get("access_token") or "", skew_seconds=min_ttl
        ):
            return True

        # 2) The proxy: the one place a token is refreshed, always current. There is no
        #    third way any more -- Streamlit holds no refresh token to spend.
        outcome = _pull_from_proxy(h.get("cookie", ""))
        _record_pull_outcome(outcome)
        return outcome == PULL_OK


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


def _read_stop_flag(stop_key: str) -> bool:
    """Read the stop flag without letting Streamlit's control flow kill the thread.

    The refresher carries a script run context so it can reach
    ``st.session_state`` at all. The cost is that a read goes through
    ``SafeSessionState``, which raises ``StopException`` or ``RerunException``
    to interrupt *the script* -- and both derive from ``BaseException``, so an
    ``except Exception`` guard does not catch them. Observed in production:

        Exception in thread token_refresher:
          File "lex/streamlit_app.py", line 298, in _refresher_should_stop
            return bool(st.session_state.get(stop_key, False)) ...
        streamlit.runtime.scriptrunner_utils.exceptions.StopException

    The thread then died. ``start_token_refresh_thread_if_needed`` replaces a
    dead one, but only on the *next script run* -- so if the user's last
    interaction is what killed it and they then leave the tab idle, there is no
    further run, nothing restarts it, and the session expires on the access
    token's own lifetime. That is precisely the "left it open, came back, it
    was dead" report the refresher exists to prevent, and it is why the failure
    looks intermittent: it needs a rerun followed by idleness.

    A rerun is completely ordinary, so it must never end the refresher: treat
    an unreadable flag as "not set" and let ``_session_is_live`` remain the
    authority on whether to stop.
    """
    try:
        return bool(st.session_state.get(stop_key, False))
    except ScriptControlException:
        return False
    except Exception:
        return False


def _refresher_should_stop(session_id: str, stop_key: str) -> bool:
    return _read_stop_flag(stop_key) or not _session_is_live(session_id)


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
    except ScriptControlException:
        # A rerun or a stop interrupted a session-state access somewhere in the
        # loop. The *session* is fine -- only this script run ended -- so
        # exiting would silently stop renewal for a tab that is still open.
        # Hand off to a fresh thread instead; the next script run restarts one.
        log.debug(
            "Token refresher for session %s interrupted by a script rerun", session_id or "?"
        )
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


# ---------------------------------------------------------------------------
# Calling the Lex API from a dashboard
# ---------------------------------------------------------------------------
# A dashboard calls the Lex API as its viewer, with the viewer's access token. That token
# lives for minutes and is renewed in the background (``_token_refresher``), so the only
# safe moment to read it is the moment a request is sent. A header built once and kept --
# in ``st.session_state``, in a module global, in a closure -- is a snapshot that expires
# while the page is still open. It survives unnoticed on full page runs, which rebuild
# it, and fails inside ``st.dialog`` and ``st.fragment``, whose reruns do not: a form
# filled in for longer than the token lasts sends an expired token on submit, and the API
# answers 401. ``lex_api()`` reads the token per request, so that cannot happen.

#: The ``st.session_state`` key dashboards have used for a hand-built header dict.
STORED_HEADERS_KEY = "headers"
_STORED_HEADERS_WARNED_KEY = "_lex_stored_headers_warned"

#: Seconds a request may take when the caller does not say (requests' own default is forever).
DEFAULT_API_TIMEOUT = 60

#: The API under the instance's public URL: the backend's ``/api/``, then lex-app's own ``api/``.
DEPLOYED_API_PATH = "/api/api/"
#: ``python -m lex runserver`` serves lex-app's routes at the root, so ``api/`` once.
LOCAL_API_URL = "http://localhost:8000/api/"


class LexAuthRequired(RuntimeError):
    """The session holds no usable access token, so there is nobody to call the API as."""


def _refresh_stored_api_headers(old: str, new: str) -> None:
    """Keep a dashboard's stored ``st.session_state.headers`` on the current token.

    Dashboards written before :func:`lex_api` built ``{"Authorization": f"Bearer {token}"}``
    once per page run and kept it in ``st.session_state.headers``. Inside a dialog or a
    fragment that copy is never rebuilt, so it goes stale while the session's own token is
    renewed. Updating the dict *in place* keeps it current for code that already holds a
    reference to it. Only a header carrying exactly the token being replaced is touched; a
    header with any other credential is the dashboard's own business.
    """
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or not new or old == new:
        return
    try:
        headers = st.session_state.get(STORED_HEADERS_KEY)
        if not hasattr(headers, "keys") or not hasattr(headers, "__setitem__"):
            return
        replaced = False
        for key in list(headers.keys()):
            if str(key).lower() != "authorization":
                continue
            value = headers[key]
            if isinstance(value, str) and value.strip().lower().startswith("bearer ") and strip_bearer(value) == old:
                headers[key] = f"Bearer {new}"
                replaced = True
        if replaced and not st.session_state.get(_STORED_HEADERS_WARNED_KEY):
            st.session_state[_STORED_HEADERS_WARNED_KEY] = True
            log.warning(
                "st.session_state.%s carries the session's access token; lex-app keeps it current, "
                "but a copy taken from it is not. Call the Lex API through "
                "lex.lex_app.streamlit.lex_api() instead.",
                STORED_HEADERS_KEY,
            )
    except ScriptControlException:
        # Reached from the refresher thread while a script run ends: the next renewal retries.
        return
    except Exception as exc:  # never let a dashboard's own state break token renewal
        log.debug("Could not update st.session_state.%s: %s", STORED_HEADERS_KEY, exc)


def current_access_token(min_ttl: int = RENEW_LEAD_SECONDS) -> Optional[str]:
    """The viewer's access token, renewed first when it expires within ``min_ttl`` seconds.

    Read it at the moment you use it and do not keep it: it lives for minutes. ``None``
    when the session has no token that is still valid, even after asking for a new one.
    """
    access = (st.session_state.get("access_token") or "").strip()
    if not _is_token_valid(access, skew_seconds=min_ttl):
        renew_access_token(min_ttl=min_ttl)
        access = (st.session_state.get("access_token") or "").strip()
    return access if _is_token_valid(access, skew_seconds=0) else None


def default_api_url() -> str:
    """Where :func:`lex_api` sends relative paths: ``LEX_API_URL``, else the instance's API."""
    # Read by literal name so .github/scripts/check_doc_env_vars.py sees it.
    explicit = (os.getenv("LEX_API_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/") + "/"
    domain = (os.getenv("DOMAIN_HOSTED") or "").strip().rstrip("/")
    if domain:
        if "://" not in domain:
            domain = f"https://{domain}"
        return domain + DEPLOYED_API_PATH
    return LOCAL_API_URL


def _origin(url: str) -> str:
    parts = requests.utils.urlparse(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _replayable(kwargs: dict) -> bool:
    """Whether the request body can be sent a second time unchanged.

    Bytes, strings, dicts and ``(name, bytes)`` file tuples can; a file object or a
    generator has been read to its end by the first attempt.
    """

    def _is_stream(value) -> bool:
        return hasattr(value, "read") or (hasattr(value, "__next__") and not isinstance(value, (str, bytes)))

    if _is_stream(kwargs.get("data")):
        return False
    files = kwargs.get("files") or {}
    values = files.values() if isinstance(files, dict) else [item[1] for item in files]
    for value in values:
        content = value[1] if isinstance(value, (tuple, list)) and len(value) > 1 else value
        if _is_stream(content):
            return False
    return True


class LexApiSession(requests.Session):
    """A ``requests.Session`` that calls the Lex API as the dashboard's viewer.

    Every request carries the viewer's access token as it is *at that moment*
    (:func:`current_access_token`), so a request sent from a dialog or a fragment, or
    long after the page was drawn, is never refused for a token that expired meanwhile.
    A ``401`` is answered once: the token is renewed and the request sent again, provided
    its body can be replayed. Relative paths are joined onto ``base_url``; an absolute URL
    must point at the same origin, so the token is never sent anywhere else.
    """

    def __init__(self, base_url: Optional[str] = None) -> None:
        super().__init__()
        self.base_url = (base_url or default_api_url()).rstrip("/") + "/"
        self.headers["Accept"] = "application/json"

    def _absolute(self, url: str) -> str:
        url = str(url)
        parts = requests.utils.urlparse(url)
        if not parts.scheme and not parts.netloc:  # relative, even with a URL in its query
            return requests.compat.urljoin(self.base_url, url.lstrip("/"))
        # Absolute, or scheme-relative ("//host/..."): it must be the API's own origin.
        if _origin(url) != _origin(self.base_url):
            raise ValueError(
                f"lex_api() sends the viewer's access token, so it only calls {_origin(self.base_url)}; "
                f"got {url!r}"
            )
        return url

    @staticmethod
    def _with_token(headers, token: str) -> dict:
        merged = {k: v for k, v in dict(headers or {}).items() if str(k).lower() != "authorization"}
        merged["Authorization"] = f"Bearer {token}"
        return merged

    def request(self, method, url, **kwargs):  # type: ignore[override]
        url = self._absolute(url)
        kwargs.setdefault("timeout", DEFAULT_API_TIMEOUT)
        token = current_access_token()
        if not token:
            raise LexAuthRequired("this session has no valid access token; the viewer has to sign in again")

        caller_headers = kwargs.pop("headers", None)
        response = super().request(method, url, headers=self._with_token(caller_headers, token), **kwargs)
        if response.status_code != 401 or not _replayable(kwargs):
            return response

        # The API refused a token we believed valid (revoked, or a clock disagreeing):
        # renew whatever its exp says, then try once more -- never in a loop.
        renew_access_token(force=True)
        fresh = (st.session_state.get("access_token") or "").strip()
        if not fresh or fresh == token or not _is_token_valid(fresh, skew_seconds=0):
            return response
        response.close()
        return super().request(method, url, headers=self._with_token(caller_headers, fresh), **kwargs)


def lex_api(base_url: Optional[str] = None) -> LexApiSession:
    """A session for calling the Lex API as the viewer, from anywhere in a dashboard.

    ::

        from lex.lex_app.streamlit import lex_api

        response = lex_api().patch(
            f"model_entries/provision/{serializer}/one/{pk}",
            files={"attached_documents": (name, content)},
        )

    Use it instead of building ``{"Authorization": f"Bearer {token}"}`` yourself: a header
    built once goes stale while the page is open, and fails first inside ``st.dialog``
    and ``st.fragment``. ``base_url`` defaults to :func:`default_api_url`.
    """
    return LexApiSession(base_url)
