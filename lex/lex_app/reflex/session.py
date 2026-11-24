"""Sign-in for Reflex dashboards without Reflex Enterprise: the Django hand-off (decisions E2-E11).

A Reflex dashboard never runs an OpenID Connect flow and never holds a Keycloak token in the
browser. It borrows the user's lex-app sign-in through the dashboard hand-off that Streamlit's
proxy uses in hand-off mode, with its own audience, ``reflex``
(:mod:`lex.authentication.dashboards`)::

    page event, no binding  ->  /auth/start  ->  <app>/oidc/handoff?aud=reflex  ->  /auth/handoff
                                (a nonce)        (a single-use code)               (redeem, cookie)

Django mints the code for the browser's active sign-in; the backend redeems it in-process --
same image, same database -- into a ``DashboardBinding`` and keeps only its opaque handle, in an
HttpOnly cookie (``lex_rdash``; ``__Host-lex_rdash`` over https). Everything that knows the user
stays in Django: ``broker.access_token`` refreshes once per sign-in for every holder of it, and a
sign-out or a back-channel logout ends the sign-in, so the binding stops answering.

Three pieces put that into Reflex:

* :func:`transformer` -- an ASGI layer outermost on the backend. It answers the hand-off routes
  (``/auth/start``, ``/auth/handoff``, ``/auth/logout``, ``/auth/blocked``), and on the event
  socket (``/_event``) it removes every cookie and adds ``X-Lex-Binding``: the SHA-256 of the
  handle, the form the database stores it in. Reflex copies the socket's request headers into
  state and sends them to the browser, so a cookie on that socket would be readable by page
  scripts; the digest is useless outside the server -- a browser cannot set a header on a
  WebSocket, and a digest presented as a cookie is hashed again and matches nothing.
* :class:`LexSessionMiddleware` -- before every event, the binding's standing, read off the event
  loop and cached per process for :data:`CHECK_SECONDS`. An anonymous session -- no binding, or
  one whose sign-in ended -- is sent through the hand-off, by a full page load (``rx.redirect``
  would route a same-host URL inside the frontend and never reach the backend).
* :func:`identity` -- the user as RFDS sees it (``rfds.auth.set_identity_resolver``), read from
  that cache, so ``can()``, the navigation and ``PolicyMiddleware`` work as before.

The access token for lex-app's API comes from :func:`access_token`, only when a handler asks.
"""

from __future__ import annotations

import dataclasses
import html
import json
import logging
import os
import secrets
import threading
import time
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lex.authentication.dashboards import cookies as dash_cookies
from lex.authentication.dashboards import sites

logger = logging.getLogger(__name__)

#: The hand-off audience of a Reflex dashboard.
AUD = "reflex"

#: The binding's handle (``__Host-`` only over https; see ``dash_cookies.cookie_name``). Not
#: Streamlit's ``lex_dash``: both run on ``localhost`` in development, and cookies ignore ports.
HANDLE_COOKIE = "__Host-lex_rdash"
#: The hand-off nonces in flight.
NONCE_COOKIE = "__Host-lex_rdash_hs"

#: The header the socket guard adds: the SHA-256 of the handle.
BINDING_HEADER = "x-lex-binding"

START_PATH = "/auth/start"
HANDOFF_PATH = "/auth/handoff"
LOGOUT_PATH = "/auth/logout"
BLOCKED_PATH = "/auth/blocked"

#: How long a binding's standing is trusted before the database is asked again. A sign-out
#: elsewhere reaches a dashboard within this.
CHECK_SECONDS = 60.0

#: A token handed out must stay valid at least this long.
TOKEN_MIN_TTL = 60

#: Hand-offs in a row after which a page that still has no binding stops trying: the browser is
#: not keeping the cookie (a frame where third-party cookies are blocked).
ATTEMPTS_BEFORE_BLOCKED = 2

#: Module prefixes of the states whose events an anonymous session may still send: Reflex's own
#: (hydrate, on-load dispatch) and RFDS's -- the same rule as ``rfds.auth.PolicyMiddleware``.
FRAMEWORK_PREFIXES = ("reflex.", "reflex_base.", "reflex_components", "rfds.")

_PAGE_STYLE = "font:16px system-ui,Arial,sans-serif;padding:24px;text-align:center"
_TRUTHY = {"1", "true", "yes", "y", "on"}


# -- where things are ----------------------------------------------------------


def _header(headers: Mapping[str, Any] | None, name: str) -> str:
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return str(value or "")
    return ""


def _scope_headers(scope: Mapping[str, Any]) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}


def _scheme(headers: Mapping[str, str], fallback: str) -> str:
    """``https`` or ``http`` as the browser used it: ``X-Forwarded-Proto`` behind an ingress."""
    forwarded = _header(headers, "x-forwarded-proto").split(",")[0].strip().lower()
    if forwarded in ("http", "https"):
        return forwarded
    return "https" if fallback in ("https", "wss") else "http"


def request_origin(scope: Mapping[str, Any]) -> str | None:
    """The origin the browser reached this backend on, when it is one of the instance's Reflex
    origins (loopback in development); ``None`` otherwise -- nothing is ever sent to a host the
    instance does not list."""
    headers = _scope_headers(scope)
    host = _header(headers, "x-forwarded-host").split(",")[0].strip() or _header(headers, "host")
    if not host:
        return None
    origin = sites.normalize_origin(f"{_scheme(headers, scope.get('scheme', 'http'))}://{host}")
    return origin if origin and sites.is_reflex_origin(origin) else None


def single_port() -> bool:
    """Whether the pages are served from the backend's own origin.

    Production serves both on one port, and so does a development run behind
    lex-app's single-port proxy (``REFLEX_USE_SINGLE_PORT``). Only a plain local
    development run has the frontend on its own port.
    """
    if (os.getenv("REFLEX_USE_SINGLE_PORT") or "").strip().lower() in _TRUTHY:
        return True
    try:
        from reflex.utils.exec import is_prod_mode

        return bool(is_prod_mode())
    except Exception:  # noqa: BLE001 - outside a Reflex process
        return False


def frontend_origin(backend_origin: str) -> str:
    """Where the pages of a backend reached on ``backend_origin`` are served."""
    if single_port():
        return backend_origin
    from reflex.config import get_config

    parts = urlsplit(backend_origin)
    hostname = parts.hostname or "localhost"
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{parts.scheme}://{host}:{get_config().frontend_port}"


def _event_path() -> str:
    try:
        from reflex.config import get_config

        return get_config().prepend_backend_path("/_event")
    except Exception:  # noqa: BLE001
        return "/_event"


def _secure(origin: str) -> bool:
    return origin.startswith("https://")


def _cookie(headers: Mapping[str, str], name: str, secure: bool) -> str:
    from starlette.requests import cookie_parser

    return cookie_parser(_header(headers, "cookie")).get(dash_cookies.cookie_name(name, secure), "")


def _safe_next(raw: str | None) -> str:
    """A same-origin path and query to come back to; ``/`` for anything else."""
    parts = urlsplit(raw or "/")
    if parts.scheme or parts.netloc or not (parts.path or "/").startswith("/") or "\\" in (raw or ""):
        return "/"
    path = parts.path or "/"
    if path.startswith("//"):
        return "/"
    return path + (f"?{parts.query}" if parts.query else "")


def _attempts(next_path: str) -> int:
    value = dict(parse_qsl(urlsplit(next_path).query)).get(dash_cookies.LOOP_MARKER, "0")
    return int(value) if value.isdigit() else 0


def with_marker(next_path: str, attempts: int | None) -> str:
    """``next_path`` with the hand-off counter set to ``attempts`` (``None`` removes it)."""
    parts = urlsplit(next_path)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != dash_cookies.LOOP_MARKER]
    if attempts is not None:
        query.append((dash_cookies.LOOP_MARKER, str(attempts)))
    return urlunsplit(("", "", parts.path or "/", urlencode(query), ""))


# -- the binding's standing ------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Standing:
    """What one binding amounts to, as last read from the database.

    ``identity`` is RFDS's ``Identity`` while the sign-in stands, else ``None`` with ``reason``
    saying why (``unknown_handle``, ``logout``, ``ended:<reason>``, ``user_inactive`` ...).
    ``profile`` holds the names lex-app shows (``sub``, ``email``, ``preferred_username``,
    ``name``).
    """

    checked_at: float
    binding_id: int | None = None
    identity: Any = None
    profile: Mapping[str, str] = dataclasses.field(default_factory=dict)
    reason: str = ""


_STANDINGS: dict[str, Standing] = {}
_TOKENS: dict[int, tuple[str, int]] = {}
_LOCK = threading.Lock()


def binding_digest(state: Any) -> str:
    """The ``X-Lex-Binding`` of the session owning ``state`` (``""`` without a binding)."""
    try:
        headers = state.router.headers.raw_headers
    except Exception:  # noqa: BLE001 - a state without router data
        return ""
    value = _header(headers, BINDING_HEADER)
    return value if len(value) == 64 and all(c in "0123456789abcdef" for c in value) else ""


def cached(digest: str) -> Standing | None:
    with _LOCK:
        return _STANDINGS.get(digest) if digest else None


def forget(digest: str | None = None) -> None:
    """Forget one binding's standing (and token), or every one."""
    with _LOCK:
        if digest is None:
            _STANDINGS.clear()
            _TOKENS.clear()
            return
        standing = _STANDINGS.pop(digest, None)
        if standing is not None and standing.binding_id is not None:
            _TOKENS.pop(standing.binding_id, None)


def _remember(digest: str, standing: Standing) -> Standing:
    with _LOCK:
        _STANDINGS[digest] = standing
        if standing.identity is None and standing.binding_id is not None:
            _TOKENS.pop(standing.binding_id, None)
    return standing


def _issuer(binding: Any) -> str:
    claims = getattr(binding.session, "claims", None) or {}
    issuer = claims.get("iss") if isinstance(claims, Mapping) else None
    if issuer:
        return str(issuer)
    from lex.lex_app.reflex.auth import lex_oidc_setting

    return lex_oidc_setting("issuer_uri") or "lex"


def _load_standing(digest: str) -> Standing:
    """The binding behind ``digest``, from the database (a worker thread; no Keycloak call)."""
    from rfds.auth import Identity

    from lex.authentication.dashboards import broker, handoff
    from lex.authentication.models import DashboardBinding

    now = time.monotonic()
    binding = (
        DashboardBinding.objects.select_related("session", "session__user")
        .filter(handle_hash=digest, aud=AUD, revoked_at__isnull=True)
        .first()
    )
    if binding is None:
        return Standing(now, reason="unknown_handle")
    problem = broker.standing_problem(binding)
    if problem is not None:
        handoff.revoke(binding, problem)
        return Standing(now, binding.pk, reason=problem)
    profile = handoff.identity(binding)
    identity = Identity(
        sub=profile["sub"], issuer=_issuer(binding), email=profile["email"] or None,
        name=profile["name"] or None, roles=frozenset(), groups=frozenset(), auth_time=None,
    )
    return Standing(now, binding.pk, identity, dict(profile))


async def standing(digest: str, *, now: float | None = None) -> Standing:
    """The binding's standing: cached for :data:`CHECK_SECONDS`, else read off the event loop."""
    current = time.monotonic() if now is None else now
    known = cached(digest)
    if known is not None and current - known.checked_at < CHECK_SECONDS:
        return known
    from lex.lex_app.reflex.django_orm import run_orm

    return _remember(digest, await run_orm(_load_standing, digest))


def identity(state: Any) -> Any:
    """RFDS's identity resolver: the user of ``state``'s session, or ``None`` (anonymous).

    Synchronous, as RFDS asks it: it reads what :class:`LexSessionMiddleware` loaded before the
    event.
    """
    known = cached(binding_digest(state))
    return known.identity if known is not None else None


def profile(state: Any) -> Mapping[str, str]:
    """The names lex-app shows for ``state``'s user (empty when anonymous)."""
    known = cached(binding_digest(state))
    return known.profile if known is not None and known.identity is not None else {}


def _token_expiry(token: str) -> int:
    try:
        import jwt

        return int(jwt.decode(token, options={"verify_signature": False, "verify_exp": False}).get("exp") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _fresh_token(binding_id: int) -> str:
    """A fresh access token of the binding's sign-in (a worker thread); ``""`` when Keycloak is down."""
    from oauth2_authcodeflow.exceptions import OIDCAuthUnavailable

    from lex.authentication.dashboards import broker
    from lex.authentication.models import DashboardBinding

    binding = DashboardBinding.objects.select_related("session", "session__user").get(pk=binding_id)
    try:
        return broker.access_token(binding)
    except OIDCAuthUnavailable:
        return ""


async def access_token_of(digest: str) -> str:
    """The access token of the sign-in behind a binding digest (see :func:`access_token`)."""
    from oauth2_authcodeflow.exceptions import OIDCSessionEnded

    from lex.lex_app.reflex.django_orm import run_orm

    known = await standing(digest) if digest else None
    if known is None or known.identity is None or known.binding_id is None:
        return ""
    with _LOCK:
        held = _TOKENS.get(known.binding_id)
    if held and held[1] - time.time() >= TOKEN_MIN_TTL:
        return held[0]
    try:
        token = await run_orm(_fresh_token, known.binding_id)
    except OIDCSessionEnded as exc:
        _remember(digest, Standing(time.monotonic(), known.binding_id, reason=f"ended:{exc}"))
        return ""
    if token:
        with _LOCK:
            _TOKENS[known.binding_id] = (token, _token_expiry(token))
    return token


async def access_token(state: Any) -> str:
    """The user's access token for lex-app's API, valid at least :data:`TOKEN_MIN_TTL` seconds.

    ``""`` when there is no sign-in, when it has ended (the binding is then forgotten), or while
    Keycloak cannot be reached and no valid token is left. The browser never sees it.
    """
    return await access_token_of(binding_digest(state))


# -- sending a session through the hand-off ---------------------------------------------


def _backend_origin_of(state: Any) -> str:
    """The origin the browser reached the backend on, as the socket's request saw it."""
    try:
        headers = state.router.headers
        page = sites.normalize_origin(headers.origin) or ""
        host = headers.host
    except Exception:  # noqa: BLE001
        return ""
    scheme = urlsplit(page).scheme or "http"
    return f"{scheme}://{host}" if host else ""


def _page_path(state: Any) -> str:
    try:
        url = urlsplit(str(state.router.url))
    except Exception:  # noqa: BLE001
        return "/"
    return _safe_next(url.path + (f"?{url.query}" if url.query else ""))


def sign_in_url(state: Any) -> str:
    """Where an anonymous session goes: the hand-off for the page it is on -- or, after
    :data:`ATTEMPTS_BEFORE_BLOCKED` hand-offs that left no binding, the page saying the browser
    keeps no cookie here."""
    origin = _backend_origin_of(state)
    page = _page_path(state)
    path = BLOCKED_PATH if _attempts(page) >= ATTEMPTS_BEFORE_BLOCKED else START_PATH
    return f"{origin}{path}?{urlencode({'next': page})}"


def sign_out_url(state: Any) -> str:
    """The backend's sign-out for ``state``'s session (see ``/auth/logout``)."""
    return f"{_backend_origin_of(state)}{LOGOUT_PATH}"


def navigate(url: str) -> Any:
    """An event that loads ``url`` as a whole page, in the frame the dashboard is in."""
    import reflex as rx

    return rx.call_script(f"window.location.assign({json.dumps(url)})")


def marker_cleanup(state: Any) -> Any:
    """An event taking the hand-off counter (``lex_hs``) off the address bar, or ``None``.

    The counter tells a page that a hand-off already ran (:func:`sign_in_url`); once the binding
    answers it has done its job, and left in place it would count a later, unrelated sign-in.
    ``LexAccessState.load`` -- the first load event of every lex page, run only when signed in
    -- asks for it.
    """
    page = _page_path(state)
    if not _attempts(page) or identity(state) is None:
        return None
    import reflex as rx

    clean = with_marker(page, None)
    return rx.call_script(f"window.history.replaceState(window.history.state, '', {json.dumps(clean)})")


def _is_framework_event(event: Any) -> bool:
    try:
        return event.state_cls.__module__.startswith(FRAMEWORK_PREFIXES)
    except Exception:  # noqa: BLE001 - an unknown state: let Reflex report it
        return True


try:
    from reflex.middleware import Middleware as _Middleware
except Exception:  # noqa: BLE001 - imported outside a Reflex install (Django-only tooling)
    _Middleware = object  # type: ignore[assignment,misc]


class LexSessionMiddleware(_Middleware):  # type: ignore[misc,valid-type]
    """Before every event: the session's binding, and the hand-off for a session without one.

    Runs before RFDS's ``PolicyMiddleware`` (``create_app`` adds it first), so RFDS reads an
    identity that is at most :data:`CHECK_SECONDS` old. Framework events -- hydrating the page,
    dispatching its load events -- always pass; every other event of an anonymous session is
    replaced by a page load of the hand-off.
    """

    async def preprocess(self, app: Any, state: Any, event: Any) -> Any:
        digest = binding_digest(state)
        try:
            known = await standing(digest) if digest else None
        except Exception:  # noqa: BLE001 - the database could not be asked: no one, this time
            logger.warning("Reflex dashboard: the sign-in could not be checked", exc_info=True)
            known = None
        if known is not None and known.identity is not None:
            return None
        if _is_framework_event(event):
            return None
        from reflex.event import Event
        from reflex.state import StateUpdate

        return StateUpdate(events=Event.from_event_type(navigate(sign_in_url(state))))


# -- the hand-off routes -----------------------------------------------------------------


def _no_store(response: Any) -> Any:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _page(status: int, title: str, *paragraphs: str, script: str = "") -> Any:
    from starlette.responses import HTMLResponse

    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return _no_store(HTMLResponse(
        f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{html.escape(title)}</title></head>"
        f"<body><div style=\"{_PAGE_STYLE}\">{body}</div>{script}</body></html>",
        status_code=status,
    ))


def _message_targets() -> list[str]:
    """Origins the dashboard's ``lex-auth`` messages may go to: the instance's app origins (and
    lex-app's development origin when not deployed). A message to any other parent is dropped
    by the browser."""
    targets = list(sites.app_origins())
    if not sites.is_deployed() and sites.DEV_APP_ORIGIN not in targets:
        targets.append(sites.DEV_APP_ORIGIN)
    return targets


def _required_page(reason: str, next_path: str, origin: str) -> Any:
    """The page a frame shows when the browser has no lex-app sign-in to hand off (401).

    It tells lex-app around it -- ``{"type": "lex-auth", "v": 2, "event": "required",
    "surface": "reflex"}``, with no secret -- which renews its own sign-in and reloads the
    dashboard; and it marks this tab so the dashboard says ``restored`` once it is back. Opened
    on its own, it offers lex-app's sign-in, which comes back here.
    """
    clean = with_marker(next_path, None)
    page_url = frontend_origin(origin) + clean
    app_origin = sites.pick_app_origin(urlsplit(origin).netloc)
    sign_in = f"{app_origin}/oidc/authenticate?{urlencode({'next': page_url})}" if app_origin else page_url
    message = {"type": "lex-auth", "v": 2, "event": "required", "surface": "reflex", "reason": reason}
    data = json.dumps({"m": message, "t": _message_targets()}).replace("<", "\\u003c")
    script = (
        "<script>(function(){var d=" + data + ";"
        "try{window.sessionStorage.setItem('lex.reflex.authRequired','1');}catch(e){}"
        "if(window.parent&&window.parent!==window){"
        "document.getElementById('framed').hidden=false;document.getElementById('standalone').hidden=true;"
        "d.m.at=Date.now();for(var i=0;i<d.t.length;i++){try{window.parent.postMessage(d.m,d.t[i]);}catch(e){}}}"
        "})();</script>"
    )
    return _page(
        401, "Sign-in required", "Your sign-in for this dashboard has ended.",
        f"<span id=\"framed\" hidden><a href=\"{html.escape(page_url, quote=True)}\">Reload the dashboard</a></span>"
        f"<span id=\"standalone\"><a href=\"{html.escape(sign_in, quote=True)}\" target=\"_top\">Sign in again</a></span>",
        script=script,
    )


def _redeem(code: str, origin: str, state: str) -> tuple[str, str]:
    from lex.authentication.dashboards import handoff

    try:
        return handoff.redeem(code, aud=AUD, origin=origin, state=state).handle, ""
    except handoff.HandoffError as exc:
        return "", exc.reason


def _revoke_handle(handle: str, reason: str) -> None:
    from lex.authentication.dashboards import handoff

    binding = handoff.resolve(handle, aud=AUD)
    if binding is not None:
        handoff.revoke(binding, reason)


def _set_cookie(response: Any, name: str, value: str, max_age: int, secure: bool) -> None:
    line = dash_cookies.set_cookie_header(dash_cookies.cookie_name(name, secure), value, max_age=max_age,
                                          secure=secure)
    response.headers.append("set-cookie", line)


def _delete_cookie(response: Any, name: str, secure: bool) -> None:
    response.headers.append("set-cookie", dash_cookies.delete_cookie_header(dash_cookies.cookie_name(name, secure),
                                                                           secure=secure))


def _store_nonces(response: Any, nonces: list[str], secure: bool) -> None:
    value = dash_cookies.nonce_cookie_value(nonces)
    if value:
        _set_cookie(response, NONCE_COOKIE, value, dash_cookies.NONCE_MAX_AGE, secure)
    else:
        _delete_cookie(response, NONCE_COOKIE, secure)


async def _start(scope: Mapping[str, Any]) -> Any:
    """``/auth/start?next=<path>``: a nonce, then the browser goes to lex-app's hand-off."""
    from starlette.responses import RedirectResponse

    origin = request_origin(scope)
    if origin is None:
        return _page(400, "Not a dashboard", "This address is not one of this instance's dashboards.")
    headers = _scope_headers(scope)
    query = dict(parse_qsl(scope.get("query_string", b"").decode("latin-1")))
    next_path = _safe_next(query.get("next"))
    app_origin = sites.pick_app_origin(urlsplit(origin).netloc)
    if app_origin is None:
        return _page(500, "Not configured", "This instance names no app host to sign in through.")
    secure = _secure(origin)
    nonce = secrets.token_urlsafe(24)
    back = origin + with_marker(next_path, _attempts(next_path) + 1)
    target = f"{app_origin}/oidc/handoff?" + urlencode({"aud": AUD, "return": back, "state": nonce})
    response = _no_store(RedirectResponse(target, status_code=303))
    pending = [nonce] + dash_cookies.pending_nonces(_cookie(headers, NONCE_COOKIE, secure))
    _store_nonces(response, pending, secure)
    return response


async def _handoff(scope: Mapping[str, Any]) -> Any:
    """``/auth/handoff``: where lex-app sends the browser back with a code (or an error)."""
    from starlette.responses import RedirectResponse

    from lex.lex_app.reflex.django_orm import run_orm

    origin = request_origin(scope)
    if origin is None:
        return _page(400, "Not a dashboard", "This address is not one of this instance's dashboards.")
    headers = _scope_headers(scope)
    query = dict(parse_qsl(scope.get("query_string", b"").decode("latin-1")))
    secure = _secure(origin)
    next_path = _safe_next(query.get("next"))
    state = query.get("state", "")
    nonces = dash_cookies.pending_nonces(_cookie(headers, NONCE_COOKIE, secure))
    matched = bool(state) and any(secrets.compare_digest(n.encode(), state.encode("utf-8", "ignore")) for n in nonces)
    remaining = [n for n in nonces if n != state]

    if query.get("error"):
        reason = "login_required" if query["error"] == "login_required" else "handoff_failed"
        response = _required_page(reason, next_path, origin)
        if matched:
            _store_nonces(response, remaining, secure)
        return response
    code = query.get("code", "")
    page = frontend_origin(origin) + next_path
    if not matched or not code:
        # No nonce of ours (cookies blocked, or a code meant for another browser): never
        # redeemed here. The page's counter decides between one more hand-off and the
        # blocked-cookie page.
        return _no_store(RedirectResponse(page, status_code=303))
    try:
        handle, failure = await run_orm(_redeem, code, origin, state)
    except Exception:  # noqa: BLE001 - the database could not be asked
        logger.warning("Reflex dashboard hand-off could not be redeemed; retrying", exc_info=True)
        response = _page(503, "Temporarily unavailable", "Sign-in is temporarily unavailable. Retrying...",
                         script=f"<script>setTimeout(function(){{window.location.assign({json.dumps(page)});}},5000);</script>")
        response.headers["Retry-After"] = "5"
        return response
    if not handle:
        logger.info("Reflex dashboard hand-off refused: %s", failure)
        response = _required_page("session_ended" if failure == "session_ended" else "handoff_failed",
                                  next_path, origin)
        _store_nonces(response, remaining, secure)
        return response
    response = _no_store(RedirectResponse(page, status_code=303))
    _set_cookie(response, HANDLE_COOKIE, handle, dash_cookies.HANDLE_MAX_AGE, secure)
    _store_nonces(response, remaining, secure)
    return response


async def _logout(scope: Mapping[str, Any]) -> Any:
    """``/auth/logout``: the binding is revoked, then lex-app signs the user out (Keycloak too)."""
    from starlette.responses import RedirectResponse

    from lex.authentication.dashboards.handoff import digest as digest_of
    from lex.lex_app.reflex.django_orm import run_orm

    origin = request_origin(scope)
    if origin is None:
        return _page(400, "Not a dashboard", "This address is not one of this instance's dashboards.")
    secure = _secure(origin)
    handle = _cookie(_scope_headers(scope), HANDLE_COOKIE, secure)
    if handle:
        try:
            await run_orm(_revoke_handle, handle, "logout")
        except Exception:  # noqa: BLE001 - the cookie still goes
            logger.warning("Reflex dashboard sign-out could not revoke its binding", exc_info=True)
        _forget_private_values(digest_of(handle))
        forget(digest_of(handle))
    back = frontend_origin(origin) + "/"
    app_origin = sites.pick_app_origin(urlsplit(origin).netloc)
    target = f"{app_origin}/oidc/total_logout?{urlencode({'next': back})}" if app_origin else back
    response = _no_store(RedirectResponse(target, status_code=303))
    _delete_cookie(response, HANDLE_COOKIE, secure)
    return response


def _forget_private_values(digest: str) -> None:
    """Drop the signed-out user's ``ui.private`` values held in this process.

    ``LexUser.sign_out`` does it too, but a project may sign out through its own page, which
    loads this route without that event.
    """
    known = cached(digest)
    if known is None or known.identity is None:
        return
    from lex.lex_app.reflex.ui import context, private

    private.forget(context.User.from_profile(known.profile).id)


async def _blocked(scope: Mapping[str, Any]) -> Any:
    """``/auth/blocked``: the browser keeps no dashboard cookie here (no redirect loop)."""
    origin = request_origin(scope) or ""
    query = dict(parse_qsl(scope.get("query_string", b"").decode("latin-1")))
    page = (frontend_origin(origin) if origin else "") + with_marker(_safe_next(query.get("next")), None)
    return _page(
        200, "Cookies blocked",
        "This browser does not keep the dashboard's sign-in here, so it cannot stay signed in inside lex-app.",
        f"<a href=\"{html.escape(page, quote=True)}\" target=\"_blank\" rel=\"noopener\">Open the dashboard in its own tab</a>",
    )


_ROUTES = {START_PATH: _start, HANDOFF_PATH: _handoff, LOGOUT_PATH: _logout, BLOCKED_PATH: _blocked}


def _guarded(scope: Mapping[str, Any]) -> dict[str, Any]:
    """The event socket's scope with no cookie, and the binding's digest as ``X-Lex-Binding``."""
    from lex.authentication.dashboards.handoff import digest as digest_of

    headers = _scope_headers(scope)
    origin = request_origin(scope)
    secure = _secure(origin) if origin else _scheme(headers, scope.get("scheme", "http")) == "https"
    handle = _cookie(headers, HANDLE_COOKIE, secure)
    kept = [(k, v) for k, v in scope.get("headers") or [] if k.lower() not in (b"cookie", BINDING_HEADER.encode())]
    if handle:
        kept.append((BINDING_HEADER.encode(), digest_of(handle).encode()))
    return {**scope, "headers": kept}


def transformer(app: Any) -> Any:
    """The ASGI layer: the hand-off routes, and the guard on the event socket (see the module)."""
    event_path = _event_path()

    async def asgi(scope: Any, receive: Any, send: Any) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if kind == "http" and path in _ROUTES and scope.get("method") in ("GET", "HEAD"):
            response = await _ROUTES[path](scope)
            await response(scope, receive, send)
            return
        if path == event_path or path.startswith(event_path.rstrip("/") + "/"):
            scope = _guarded(scope)
        await app(scope, receive, send)

    asgi.lex_session = True  # type: ignore[attr-defined]
    return asgi


# -- exports bound to the user (decision E10) -------------------------------------------


def _state_principal(state: Any) -> str:
    who = identity(state)
    return who.sub if who is not None else ""


async def _request_principal(request: Any) -> str:
    from lex.authentication.dashboards.handoff import digest as digest_of

    scope = request.scope
    origin = request_origin(scope)
    secure = _secure(origin) if origin else request.url.scheme == "https"
    handle = _cookie(_scope_headers(scope), HANDLE_COOKIE, secure)
    if not handle:
        return ""
    known = await standing(digest_of(handle))
    return known.identity.sub if known.identity is not None else ""


def proxy_auth() -> Any:
    """The ``ProxyAuth`` RFDS's open path is configured with. Its headers are never read -- the
    identity resolver answers first -- so only the sign-in URL matters, for a session RFDS meets
    without a binding (lex-app's middleware has normally sent it through the hand-off already)."""
    from rfds.auth import ProxyAuth

    return ProxyAuth(sign_in_url=START_PATH, issuer="lex")


def install(app: Any) -> None:
    """Sign-in for ``app``: the ASGI layer outermost, the middleware first, RFDS's resolvers.

    ``create_app`` calls it before ``rfds.install``, so this middleware runs before RFDS's
    ``PolicyMiddleware``.
    """
    from rfds.auth import set_identity_resolver
    from rfds.platform.tickets import set_principal_resolvers

    transformers = list(app.api_transformer or [])
    if not any(getattr(t, "__name__", "") == "transformer" and t is transformer for t in transformers):
        transformers.append(transformer)
    app.api_transformer = transformers
    app.add_middleware(LexSessionMiddleware(), index=0)
    set_identity_resolver(identity)
    set_principal_resolvers(state=_state_principal, request=_request_principal)


__all__ = [
    "AUD",
    "BINDING_HEADER",
    "CHECK_SECONDS",
    "HANDLE_COOKIE",
    "LexSessionMiddleware",
    "NONCE_COOKIE",
    "Standing",
    "access_token",
    "binding_digest",
    "forget",
    "frontend_origin",
    "identity",
    "install",
    "marker_cleanup",
    "navigate",
    "profile",
    "proxy_auth",
    "request_origin",
    "sign_in_url",
    "sign_out_url",
    "standing",
    "transformer",
    "with_marker",
]
