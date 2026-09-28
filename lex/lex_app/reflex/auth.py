"""Keycloak sign-in for Reflex dashboards, through Reflex Enterprise.

A Streamlit dashboard is signed in by the proxy in front of it (``lex/proxy.py``):
the proxy runs the OIDC flow against Keycloak, keeps and renews the tokens, and
hands Streamlit the identity in request headers. Reflex Enterprise ships all of
that as ``rxe.AuthPlugin`` -- Authorization Code + PKCE, the ``/login``,
``/callback`` and ``/logout`` routes, tokens in secure cookies, refresh, the
popup flow inside an iframe, and sign-in required by default for every page,
event handler and state var. So a Reflex dashboard needs no proxy. It needs the
plugin pointed at lex-app's Keycloak, which is :class:`LexKeycloakAuthState`:

* it reads the settings a project already has -- ``KEYCLOAK_URL``,
  ``KEYCLOAK_REALM``, ``OIDC_RP_CLIENT_ID``, ``OIDC_RP_CLIENT_SECRET`` -- behind
  the plugin's own ``LEX_KEYCLOAK_*`` and ``OIDC_*`` variables, either of which
  still wins when set;
* it honours ``OIDC_VERIFY_SSL`` and ``OIDC_ISSUER`` as the proxy does;
* it resolves the user's Keycloak UMA permissions -- what a Streamlit dashboard
  reads from ``st.session_state.permissions`` -- through
  :func:`current_permissions`, and :func:`has_permission` turns one into an
  ``auth=`` check;
* it signs users in without a click (:func:`lex_login_page`). Reflex Enterprise's
  ``/login`` is a palette of provider buttons, deliberately, even for a single
  provider; lex-app has exactly one, and a user who is signed in to lex-app
  already has a Keycloak session that can answer at once. So ``/login`` starts
  the sign-in itself: a redirect to Keycloak at the top level, and inside a
  frame -- where Keycloak cannot be shown and a popup needs a click -- a silent
  ``prompt=none`` request that Keycloak answers without a page. The button is
  shown only when the browser leaves no other way;
* it keeps an access token too large for its cookie. A Keycloak access token
  carries every role the user holds, in every client of the realm, and a browser
  refuses a cookie over 4096 bytes; the plugin keeps the session, and a user's
  tabs in step, through the cookies, so without it a restart or a second tab
  signs the user out. Such a token is stored compressed
  (:func:`_fit_access_token_cookie`), and restored by
  ``AccessTokenMetadata.from_cookie_value``, the one function through which the
  plugin reads that cookie.

Its ``__provider__`` is ``lex_keycloak`` rather than ``keycloak`` on purpose: the
plugin reads ``{PROVIDER}_CLIENT_ID`` first, and ``KEYCLOAK_CLIENT_ID`` already
means something else in lex-app -- the client id handed to the browser.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import logging
import os
import time
import zlib
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode

import httpx
import jwt
import reflex as rx
import reflex_enterprise as rxe
from reflex_enterprise.auth import AuthUserState, OIDCAuthState
from reflex_enterprise.auth.enforcement import login_url_for
from reflex_enterprise.auth.oidc.state import AccessTokenMetadata, IsIframedState

logger = logging.getLogger(__name__)

PROVIDER = "lex_keycloak"

_TRUTHY = {"1", "true", "yes", "y", "on"}

#: What Keycloak answers a ``prompt=none`` request with when it cannot sign the
#: user in without showing a page (OpenID Connect Core 1.0, section 3.1.2.6).
SILENT_LOGIN_ERRORS = frozenset(
    {"login_required", "interaction_required", "consent_required", "account_selection_required"}
)

#: How long after starting a sign-in by itself ``/login`` waits before it does so
#: again. Landing back on ``/login`` sooner means the last attempt did not stick
#: -- the user backed out of Keycloak, or the callback failed -- and starting
#: another would bounce them between the app and Keycloak for ever.
AUTO_LOGIN_RETRY_SECONDS = 30.0

#: The most a cookie's name and value may hold. Browsers drop a larger cookie
#: without an error, which for a token means a session that dies with the server.
COOKIE_SIZE_LIMIT = 4096

#: How an access token stored compressed is marked. A token as issued never
#: starts with it: a JWT starts with its base64 header, ``eyJ``.
_COMPRESSED_TOKEN_MARK = "lexz1."

#: The most a compressed token may inflate to: far beyond any real token, and a
#: bound on what a forged cookie can make the server allocate.
_INFLATED_TOKEN_LIMIT = 256 * 1024

#: What ``/login`` learns in the browser before it signs anyone in: whether it
#: is framed -- a cross-origin parent makes reading ``window.top`` throw, which
#: also means framed -- and whether this visit has asked already. React's
#: development mode mounts every component twice; a visit is one history entry
#: (React Router keys each) of one page load (``window`` starts empty on each).
_LOGIN_PAGE_JS = """() => {
    const visit = window.history.state && window.history.state.key;
    const repeat = !!visit && window.__lexSignInVisit === visit;
    window.__lexSignInVisit = visit;
    let framed;
    try {
        framed = window.self !== window.top;
    } catch (e) {
        framed = true;
    }
    return {framed: framed, repeat: repeat};
}"""

#: Token cookies already reported as too large, so a refresh does not repeat it.
_OVERSIZED_REPORTED: set[str] = set()

#: Token cookies already reported as stored compressed, for the same reason.
_COMPRESSION_REPORTED: set[str] = set()


def lex_oidc_setting(key: str) -> str | None:
    """The value lex-app's own Keycloak settings give an OIDC config ``key``.

    The same three facts every other part of lex-app authenticates with: the
    realm's issuer, and the confidential client Django already signs users in
    through.
    """
    key = key.strip().lower()
    if key == "issuer_uri":
        base = (os.getenv("KEYCLOAK_URL") or "").strip().rstrip("/")
        realm = (os.getenv("KEYCLOAK_REALM") or os.getenv("KEYCLOAK_REALM_NAME") or "").strip()
        return f"{base}/realms/{realm}" if base and realm else None
    if key == "client_id":
        return (os.getenv("OIDC_RP_CLIENT_ID") or "").strip() or None
    if key == "client_secret":
        return (os.getenv("OIDC_RP_CLIENT_SECRET") or "").strip() or None
    return None


def _verify_ssl() -> bool:
    """``OIDC_VERIFY_SSL``, read the way the Streamlit proxy reads it (default on)."""
    raw = os.getenv("OIDC_VERIFY_SSL")
    return True if raw is None else raw.strip().lower() in _TRUTHY


_UNVERIFIED_HTTP_CLIENT = None


def _token_subject(token: str) -> str:
    """The ``sub`` of an access token the provider has already validated."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except jwt.PyJWTError:
        return ""
    return str(claims.get("sub") or "")


def _fetch_uma_permissions(access_token: str) -> list[dict] | None:
    """The user's UMA permissions from Keycloak, or ``None`` if Keycloak could not say.

    ``KeycloakManager`` is the client every other part of lex-app asks, so the
    permissions a dashboard sees are the ones the API enforces.
    """
    try:
        from lex.api.views.authentication.KeycloakManager import KeycloakManager

        permissions = KeycloakManager().get_uma_permissions(access_token)
    except Exception:
        logger.exception("Could not fetch Keycloak UMA permissions")
        return None
    return permissions if isinstance(permissions, list) else None


class LexKeycloakAuthState(OIDCAuthState, rx.State):
    """lex-app's Keycloak realm, as a Reflex Enterprise OIDC provider."""

    __provider__ = PROVIDER

    #: The last UMA permissions Keycloak returned, which token they were
    #: fetched with (a digest, never the token), and whose token it was.
    _uma_permissions: list[dict] = []
    _uma_permissions_token: str = ""
    _uma_permissions_subject: str = ""

    #: Whether ``/login`` has to wait for a click: it is framed and Keycloak
    #: could not sign the user in silently there, or an automatic sign-in just
    #: failed to stick, or could not start. Until then the page shows that it
    #: is signing in.
    login_needs_click: bool = False
    #: A silent ``prompt=none`` request is on its way to Keycloak.
    _silent_login_pending: bool = False
    #: Keycloak answered one with "sign in first": this frame needs the popup.
    _silent_login_failed: bool = False
    #: When ``/login`` last started a sign-in by itself.
    _auto_login_at: float = 0.0

    @classmethod
    def display_name(cls) -> str:
        """The label on the sign-in button."""
        return "Keycloak"

    @classmethod
    def _http_client(cls):
        """The plugin's HTTP client, without TLS verification if ``OIDC_VERIFY_SSL`` is off."""
        if _verify_ssl():
            return super()._http_client()
        global _UNVERIFIED_HTTP_CLIENT
        if _UNVERIFIED_HTTP_CLIENT is None:
            from reflex_enterprise.auth.oidc.utils import AsyncHTTPXClient

            _UNVERIFIED_HTTP_CLIENT = AsyncHTTPXClient(httpx.AsyncClient(verify=False))
        return _UNVERIFIED_HTTP_CLIENT

    async def _get_config_value(self, key: str, default: str | None = None) -> str | None:
        """``LEX_KEYCLOAK_<KEY>``, then ``OIDC_<KEY>``, then lex-app's Keycloak settings."""
        value = await super()._get_config_value(key)
        if value:
            return value
        return lex_oidc_setting(key) or default

    async def _valid_issuers(self) -> list[str] | None:
        """Accept ``OIDC_ISSUER`` as well, when tokens carry a different issuer.

        The same situation the proxy's ``OIDC_ISSUER`` exists for: the backend
        reaches Keycloak on one URL while the tokens name another (split-horizon
        DNS, typically). Unset, the plugin checks the configured issuer alone.
        """
        expected = (os.getenv("OIDC_ISSUER") or "").strip().rstrip("/")
        if not expected:
            return None
        configured = (await self._issuer_uri()).strip().rstrip("/")
        issuers = [expected, f"{expected}/", configured, f"{configured}/"]
        return list(dict.fromkeys(issuers))

    def _reset_auth(self):
        """Forget the permissions along with the tokens they belonged to."""
        super()._reset_auth()
        self._uma_permissions = []
        self._uma_permissions_token = ""
        self._uma_permissions_subject = ""

    async def _set_tokens(
        self,
        access_token: str,
        id_token: str | None = None,
        refresh_token: str | None = None,
        granted_scopes: str | None = None,
        **kwargs: Any,
    ):
        """Store the tokens, as the plugin does; then settle the sign-in flags.

        The values are the cookies the browser keeps, and an access token too
        large for its cookie is stored compressed (:func:`_fit_access_token_cookie`)
        -- every token the plugin stores passes through here: the callback's,
        a refresh's, the one the popup hands over. A token too large even then
        is reported, while it can still be explained.

        Any sign-in that got this far -- automatic, silent, the popup, or a
        refresh -- ends the need for a click.
        """
        access_token = _fit_access_token_cookie(_cookie_name(type(self), "_access_token_data"), access_token)
        await super()._set_tokens(
            access_token,
            id_token=id_token,
            refresh_token=refresh_token,
            granted_scopes=granted_scopes,
            **kwargs,
        )
        self._silent_login_pending = False
        self._silent_login_failed = False
        self.login_needs_click = False
        _report_oversized_token_cookies(
            type(self),
            {"_access_token_data": access_token, "_id_token": id_token, "_refresh_token": refresh_token},
        )

    async def _signed_in(self) -> bool:
        """Whether the tokens this session holds resolve to a user."""
        return (await self.userinfo) is not None

    @rx.event
    async def start_login(self, page: dict):
        """Sign the visitor in without a click, as far as the browser allows.

        What ``/login`` runs when it opens, with what it learned in the browser
        (:data:`_LOGIN_PAGE_JS`): whether it is framed, and whether this visit
        has asked already -- which it has when React mounts the page a second
        time, so that call does nothing.

        * Already signed in -- the page guard ran before the tokens were read --
          straight on to the page that was asked for.
        * At the top level, the plugin's own redirect to Keycloak. A user signed
          in to lex-app has a Keycloak session, which answers without a form.
        * In a frame, a silent ``prompt=none`` request (:meth:`_silent_login_redirect`);
          if Keycloak already said it cannot sign this user in here, the button,
          whose popup is the one way left.
        * Back on ``/login`` within :data:`AUTO_LOGIN_RETRY_SECONDS` of the last
          automatic attempt, the button too: that attempt did not stick, and
          another would only bounce the user back here.
        * A sign-in that cannot start -- Keycloak unreachable, say -- leaves the
          button, beside the error the plugin reports.
        """
        if page.get("repeat"):
            return None
        framed = bool(page.get("framed"))
        # What the plugin's own flows read to choose between redirect and popup.
        frame = await self.get_state(IsIframedState)
        frame.is_iframed = framed
        frame.checked_this_session = True

        if await self._signed_in():
            target = self._safe_redirect_url(self._post_login_redirect_target())
            return rx.redirect(target, replace=True)

        now = time.time()
        if (framed and self._silent_login_failed) or now - self._auto_login_at < AUTO_LOGIN_RETRY_SECONDS:
            self.login_needs_click = True
            return None
        self._auto_login_at = now
        self.login_needs_click = False
        if framed:
            redirect = await self._silent_login_redirect()
        else:
            redirect = await self.redirect_to_login()
        if redirect is None:
            self.login_needs_click = True
        return redirect

    async def _silent_login_redirect(self):
        """Ask Keycloak, from inside the frame, whether the user has a session.

        ``prompt=none`` makes Keycloak answer at once instead of showing a page --
        which it could not show in a frame anyway: back to ``/callback`` with a
        code when the user is signed in (to lex-app, say), and with
        ``login_required`` when not, which :meth:`_handle_auth_callback_error_response`
        turns into the button. Either way the frame never displays Keycloak, so
        no popup, and no click, is needed for the common case.

        The request is the plugin's own -- the same PKCE verifier, ``state`` nonce
        and post-sign-in target -- with ``prompt=none`` added, and the callback
        is the plugin's, unchanged. ``None`` when it cannot be built; the plugin
        has reported why.
        """
        async with self._error_context("Starting a silent sign-in", clear_last_error=True):
            self.redirect_to_url = self._post_login_redirect_target()
            params = await self._redirect_to_login_payload()
            params["prompt"] = "none"
            authorize = await self._issuer_endpoint("authorization_endpoint")
            self._silent_login_pending = True
            return rx.redirect(f"{authorize}?{urlencode(params)}")
        return None

    async def _handle_auth_callback_error_response(self, request_params: Mapping[str, Any]):
        """Turn Keycloak's "sign in first" answer to a silent request into the button.

        That answer is the expected outcome for a visitor with no session, not a
        failure to report: no error toast, just ``/login`` again -- for the page
        that was asked for -- where the popup can do what the frame cannot. Any
        other error, and any error that does not follow a silent request, is the
        plugin's to report.
        """
        if self._silent_login_pending:
            self._silent_login_pending = False
            if str(request_params.get("error") or "") in SILENT_LOGIN_ERRORS:
                self._silent_login_failed = True
                self.login_needs_click = True
                target = self._safe_redirect_url(self.redirect_to_url)
                return rx.redirect(login_url_for(target), replace=True)
        return await super()._handle_auth_callback_error_response(request_params)

    async def _redirect_to_logout_payload(self) -> dict[str, str]:
        """Sign-out's request to Keycloak, as the plugin builds it -- and a fresh start.

        The sign-in after a sign-out is a new one, not an automatic attempt
        that failed to stick: ``/login`` goes to Keycloak at once again, however
        soon it comes.
        """
        self._auto_login_at = 0.0
        return await super()._redirect_to_logout_payload()

    async def _lex_uma_permissions(self) -> list[dict]:
        """The signed-in user's UMA permissions, fetched once per access token.

        A failed lookup keeps what the same user was last granted and retries on
        the next call: a Keycloak blip should not demote someone mid-session. It
        never lets one user's permissions stand in for another's.
        """
        token = await self._access_token
        if not token:
            return []
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if digest != self._uma_permissions_token:
            subject = _token_subject(token)
            fetched = await asyncio.to_thread(_fetch_uma_permissions, token)
            if fetched is not None:
                self._uma_permissions = fetched
                self._uma_permissions_token = digest
                self._uma_permissions_subject = subject
            elif subject != self._uma_permissions_subject:
                self._uma_permissions = []
                self._uma_permissions_token = ""
                self._uma_permissions_subject = ""
        return list(self._uma_permissions)


class LexUser(AuthUserState):
    """The signed-in user, with the two names lex-app shows that ``User`` does not.

    ``User.name``, ``.email``, ``.sub`` and ``.picture`` come from Reflex
    Enterprise; these add Keycloak's ``preferred_username`` and the best name to
    greet someone by. Both are empty until sign-in.
    """

    @rxe.var(initial_value="")
    def username(self) -> str:
        """Keycloak's ``preferred_username``."""
        return str(self.userinfo.get("preferred_username") or "")

    @rxe.var(initial_value="")
    def display_name(self) -> str:
        """The name, else the username, else the email -- whichever reads best."""
        info = self.userinfo
        for candidate in (info.get("name"), info.get("preferred_username"), info.get("email")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""


def _report_oversized_token_cookies(provider_cls: type, values: Mapping[str, str | None]) -> None:
    """Warn, once per cookie, when a token is too large for the browser to keep.

    The plugin keeps the tokens in cookies, and a browser drops a cookie over
    :data:`COOKIE_SIZE_LIMIT` bytes without a word. Nothing fails at once -- the
    server still holds the token -- but the session no longer survives a restart
    of the Reflex server or reaches a new tab, and since the plugin keeps a
    user's tabs in step through the cookies, one tab's sign-in signs the others
    out. The access token is stored compressed when it is too large, so this is
    a token too large even then. Keycloak access tokens grow with every role the
    user holds, in every client of the realm, while the client they are issued
    to allows full scope -- the Keycloak default.
    """
    for attribute, value in values.items():
        if not value:
            continue
        name = _cookie_name(provider_cls, attribute)
        size = _cookie_size(name, value)
        if size <= COOKIE_SIZE_LIMIT or name in _OVERSIZED_REPORTED:
            continue
        _OVERSIZED_REPORTED.add(name)
        logger.warning(
            "The Keycloak token in cookie %s is %d bytes%s, and browsers drop cookies over "
            "%d bytes: the dashboards cannot keep this sign-in -- a restart of the Reflex "
            "server or a new tab signs in again, and one tab's sign-in signs the others out. "
            "Keep the token small: while 'Full scope allowed' is on for the client it is "
            "issued to (Keycloak: Clients > the client > Client scopes > its dedicated scope "
            "> Scope), it carries every role the user holds in the realm.",
            name,
            size,
            " even compressed" if _COMPRESSED_TOKEN_MARK in value else "",
            COOKIE_SIZE_LIMIT,
        )


def _cookie_name(provider_cls: type, attribute: str) -> str:
    """The browser's name for the cookie behind a provider's token ``attribute``."""
    cookie = getattr(provider_cls, attribute, None)
    return cookie.get_cookie_name() if hasattr(cookie, "get_cookie_name") else attribute


def _cookie_size(name: str, value: str) -> int:
    """What a browser counts against :data:`COOKIE_SIZE_LIMIT`: name and value.

    Two bytes more for the quotes the access-token cookie's value is sent in --
    it holds ``=`` and ``&`` -- which the browser keeps as part of the value; for
    the ID and refresh tokens, sent unquoted, a two-byte margin.
    """
    return len(name.encode("utf-8")) + len(value.encode("utf-8")) + 2


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _compress_token(token: str) -> str:
    """``token``, compressed into a form :func:`_decompress_token` restores byte for byte.

    A Keycloak access token is a JWT, and nearly all of it is the payload --
    every role the user holds, client by client: repetitive JSON that deflates
    to a fraction. So a JWT keeps its header and signature and has its payload
    deflated; any other token is deflated whole.
    """
    parts = token.split(".")
    if len(parts) == 3:
        try:
            payload = _unb64url(parts[1])
        except ValueError:
            payload = b""
        # Only a payload in canonical base64 is encoded back to the same text.
        if payload and _b64url(payload) == parts[1]:
            deflated = _b64url(zlib.compress(payload, 9))
            return f"{_COMPRESSED_TOKEN_MARK}jwt.{parts[0]}.{deflated}.{parts[2]}"
    return f"{_COMPRESSED_TOKEN_MARK}raw.{_b64url(zlib.compress(token.encode('utf-8'), 9))}"


def _inflate(data: bytes) -> bytes | None:
    """``data`` inflated -- ``None`` unless it is one whole stream within the limit."""
    inflater = zlib.decompressobj()
    try:
        inflated = inflater.decompress(data, _INFLATED_TOKEN_LIMIT)
    except zlib.error:
        return None
    if not inflater.eof or inflater.unconsumed_tail or inflater.unused_data:
        return None
    return inflated


def _decompress_token(stored: str) -> str | None:
    """The token :func:`_compress_token` stored, as it was issued; ``None`` for anything else."""
    kind, _, rest = stored[len(_COMPRESSED_TOKEN_MARK) :].partition(".")
    try:
        if kind == "jwt":
            header, payload, signature = rest.split(".")
            inflated = _inflate(_unb64url(payload))
            return None if inflated is None else f"{header}.{_b64url(inflated)}.{signature}"
        if kind == "raw":
            inflated = _inflate(_unb64url(rest))
            return None if inflated is None else inflated.decode("utf-8")
    except ValueError:  # not base64, the wrong number of parts, not UTF-8
        return None
    return None


def _fit_access_token_cookie(cookie_name: str, value: str) -> str:
    """The access-token cookie's value, its token compressed if the cookie would not fit.

    ``value`` is the plugin's own (``AccessTokenMetadata.to_cookie_value``), and
    only its token changes, only when a browser would refuse the cookie as it
    is, and only if compressing makes it smaller. A token stored compressed
    already -- the popup hands its opener the cookie's value as stored -- is
    left alone.
    """
    size = _cookie_size(cookie_name, value)
    if size <= COOKIE_SIZE_LIMIT:
        return value
    fields = dict(parse_qsl(value))
    token = fields.get("access_token") or ""
    if not token or token.startswith(_COMPRESSED_TOKEN_MARK):
        return value
    fields["access_token"] = _compress_token(token)
    compact = urlencode(fields)
    if _cookie_size(cookie_name, compact) >= size:
        return value  # an opaque, random token: compressing only adds to it
    if cookie_name not in _COMPRESSION_REPORTED:
        _COMPRESSION_REPORTED.add(cookie_name)
        logger.info(
            "The Keycloak access token makes cookie %s %d bytes, more than browsers keep "
            "(%d); it is stored compressed, in %d.",
            cookie_name,
            size,
            COOKIE_SIZE_LIMIT,
            _cookie_size(cookie_name, compact),
        )
    return compact


_PLUGIN_READ_ACCESS_TOKEN_COOKIE = AccessTokenMetadata.from_cookie_value.__func__


def _read_access_token_cookie(cls: type, at_data_qs: str) -> AccessTokenMetadata | None:
    """``AccessTokenMetadata.from_cookie_value``, which reads a compressed token too.

    The plugin reads the access-token cookie through this one classmethod -- for
    the token it sends Keycloak and the lex-app API, the ``at_hash`` it checks
    the ID token against, and the hash that keeps a user's tabs in step -- so a
    token stored compressed comes back as issued everywhere at once. A provider
    cannot override it instead: the plugin's providers are mixins, whose vars
    Reflex copies over a subclass's own. Every other value is the plugin's to
    parse, as before; a compressed one that does not restore is no token.
    """
    metadata = _PLUGIN_READ_ACCESS_TOKEN_COOKIE(cls, at_data_qs)
    if metadata is None or not metadata.access_token.startswith(_COMPRESSED_TOKEN_MARK):
        return metadata
    token = _decompress_token(metadata.access_token)
    return None if token is None else dataclasses.replace(metadata, access_token=token)


_read_access_token_cookie.reads_compressed_tokens = True
if not getattr(AccessTokenMetadata.from_cookie_value, "reads_compressed_tokens", False):
    AccessTokenMetadata.from_cookie_value = classmethod(_read_access_token_cookie)


def lex_login_page(*, providers: Sequence[type], **context: Any) -> rx.Component:
    """``/login``, which signs the visitor in by itself -- the plugin's ``login_page``.

    While the sign-in runs it shows that it is signing in; the button appears
    only once a click is the one way left
    (:attr:`LexKeycloakAuthState.login_needs_click`). With more than one provider
    there is no single sign-in to start, so the plugin's palette stays.
    """
    from reflex_enterprise.auth.pages import default_login_page

    if len(providers) != 1 or not issubclass(providers[0], LexKeycloakAuthState):
        return default_login_page(providers=providers, **context)
    provider = providers[0]
    return rx.center(
        rx.cond(
            provider.login_needs_click,
            rx.vstack(
                rx.heading("Sign in", size="6"),
                provider.get_login_button(),
                spacing="5",
                align="center",
            ),
            rx.vstack(
                rx.spinner(size="3"),
                rx.text("Signing you in…", color_scheme="gray"),
                spacing="3",
                align="center",
            ),
        ),
        on_mount=rx.call_function(_LOGIN_PAGE_JS, callback=provider.start_login),
        min_height="60vh",
    )


async def current_access_token(state: rx.State) -> str:
    """The signed-in user's Keycloak access token -- ``""`` when there is none.

    Send it as a bearer token to call the lex-app API as the user, so the API's
    permissions apply to the dashboard exactly as they do in the grid. It never
    reaches the browser, and Reflex Enterprise keeps it fresh.
    """
    provider = await state.get_state(LexKeycloakAuthState)
    return await provider._access_token


async def current_permissions(state: rx.State) -> list[dict]:
    """The signed-in user's Keycloak UMA permissions.

    The Reflex counterpart of ``st.session_state.permissions``: the list
    Keycloak returns, one ``{"rsname": ..., "scopes": [...]}`` entry per
    resource. Callable from any state's event handler, and from an ``auth=``
    check through ``ctx.auth_user_state``.
    """
    provider = await state.get_state(LexKeycloakAuthState)
    return await provider._lex_uma_permissions()


def _resource_name(target: Any) -> str:
    """A Keycloak resource name: as given, or the one lex-app registers for a model."""
    if isinstance(target, str):
        return target
    meta = getattr(target, "_meta", None)
    if meta is None:
        raise TypeError(
            f"has_permission() takes a model class or a Keycloak resource name, got {target!r}"
        )
    return f"{meta.app_label}.{target.__name__}"


def has_permission(target: Any, scope: str = "read") -> Callable[[Any], Awaitable[bool]]:
    """An ``auth=`` check: Keycloak grants the user ``scope`` on ``target``.

    ``target`` is a model class -- resolved to the resource lex-app registers for
    it, ``<app_label>.<ModelName>`` -- or a resource name. Only a model-wide grant
    counts; a grant scoped to single records does not open the whole model::

        class FundDashboard(rx.State):
            @rxe.event(auth=has_permission(Fund, "edit"))
            async def revalue(self): ...

    The check fails closed: no permissions, or Keycloak unreachable for a user it
    never answered for, is a denial.
    """
    resource = _resource_name(target)

    async def check(ctx: Any) -> bool:
        for permission in await current_permissions(ctx.auth_user_state):
            if (
                isinstance(permission, dict)
                and permission.get("rsname") == resource
                and permission.get("resource_set_id") is None
                and scope in (permission.get("scopes") or ())
            ):
                return True
        return False

    check.__qualname__ = f"has_permission({resource!r}, {scope!r})"
    return check
