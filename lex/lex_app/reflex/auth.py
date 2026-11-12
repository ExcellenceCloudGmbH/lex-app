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
  plugin reads that cookie;
* it keeps the sign-in as long as Keycloak does (plan "long-lived sessions",
  section 6.4; the mechanism is :mod:`lex.lex_app.reflex._token_guard`): the
  ``at_hash`` an ID token is checked against comes from the access token the
  tab holds now (two tabs no longer refresh each other's tokens); only
  ``invalid_grant`` signs the user out -- a misconfigured client keeps the
  tokens and logs an ERROR, an unreachable Keycloak keeps them and backs off;
  a userinfo 401 gets one refresh, a userinfo outage serves the profile the
  session has; one refresh per refresh token across tabs and replicas, the
  others adopting its result; a second refresh within 30 s is suppressed;
  the cookies last 30 days; and a back-channel logout from Keycloak signs the
  dashboard out at its next token validation.

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
from joserfc import jwk as jose_jwk
from joserfc import jwt as jose_jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from reflex.middleware import Middleware
from reflex_enterprise.auth import AuthUserState, OIDCAuthState
from reflex_enterprise.auth.audit import AuditAction, AuditOutcome
from reflex_enterprise.auth.consts import ACCESS_TOKEN_CACHE_INTERVAL
from reflex_enterprise.auth.enforcement import get_login_endpoint, login_url_for
from reflex_enterprise.auth.oidc import state as _oidc_state
from reflex_enterprise.auth.oidc import utils as _oidc_utils
from reflex_enterprise.auth.oidc.state import (
    AccessTokenMetadata,
    IsIframedState,
    MissingAccessTokenError,
    MissingUserinfoEndpointError,
    UserinfoFetchError,
)
from reflex_enterprise.auth.oidc.types import is_ok
from reflex_enterprise.auth.oidc.utils import compute_at_hash
from reflex_enterprise.utils import call_event_from_computed_var, chain_event_out_of_band

from lex.lex_app.reflex import _token_guard as guard

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


def _token_claims(token: str | None) -> dict:
    """A JWT's claims, unverified -- for a token the plugin has just verified, or for logging."""
    if not token:
        return {}
    try:
        claims = jwt.decode(str(token), options={"verify_signature": False, "verify_exp": False})
    except jwt.PyJWTError:
        return {}
    return claims if isinstance(claims, dict) else {}


def _jwt_alg(token: str) -> str | None:
    """The ``alg`` of a JWT's header (it only chooses the ``at_hash``'s hash size)."""
    try:
        alg = jwt.get_unverified_header(str(token)).get("alg")
    except jwt.PyJWTError:
        return None
    return alg if isinstance(alg, str) and alg else None


#: What a refresh can come to. Fresh: there are new tokens (or another task got them).
_FRESH = frozenset({"refreshed", "adopted", "already"})
#: Kept: no new tokens, and the sign-in stays as it is -- never a sign-out.
_KEPT = frozenset({"suppressed", "backoff", "transient", "config", "lock_wait"})
#: Over: the refresh signed the session out itself (Keycloak's ``invalid_grant``, a back-channel
#: logout, or tokens that failed validation and were cleared).
_SIGNED_OUT = frozenset({"ended", "backchannel", "rejected"})

#: The keyword arguments of ``_set_tokens`` a published token set may carry.
_TOKEN_SET_KEYS = ("access_token", "id_token", "refresh_token", "granted_scopes")


@dataclasses.dataclass(frozen=True)
class RefreshResult:
    """How a token refresh ended: ``outcome`` (see above), and the access token to use now."""

    outcome: str
    token: str

    @property
    def kept(self) -> bool:
        return self.outcome in _KEPT

    @property
    def signed_out(self) -> bool:
        return self.outcome in _SIGNED_OUT


class UserinfoStatusError(UserinfoFetchError):
    """Keycloak's userinfo endpoint answered ``status`` (401: the access token was refused)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Userinfo request failed with status {status}")
        self.status = status


# The plugin's cookie metaclass copies its module's COOKIE_* settings onto each provider class as
# it is created: lex's provider gets 30-day cookies, and the plugin's own stay as they are.
_PLUGIN_COOKIES = guard.plugin_cookies_lasting(_oidc_state)


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
    #: When this tab last got fresh tokens (refreshed, or adopted another tab's refresh); a
    #: refresh asked for within ``_token_guard.REFRESH_COOLDOWN_SECONDS`` is suppressed.
    _lex_refreshed_at: float = 0.0
    #: The access token expired and Keycloak could not renew it yet (an outage, a misconfigured
    #: client): the sign-in is kept and renewal retried, but nobody is signed in meanwhile -- no
    #: identity, no permissions, no data. ``/login`` says so instead of starting a new sign-in.
    sign_in_unavailable: bool = False

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
        self._lex_refreshed_at = 0.0
        self.sign_in_unavailable = False

    # --- the sign-in kept as long as Keycloak keeps it (plan section 6.4) ---------------
    def _lex_access_valid(self) -> bool:
        """Whether this session holds an access token that has not expired.

        Identity, permissions and the token itself are served only while it holds. Through an
        outage the tokens stay in the cookies, to be renewed later, but they are not *used*.
        """
        metadata = self._access_token_metadata
        return metadata is not None and bool(metadata.access_token) and metadata.expires_at > time.time()

    async def _verify_jwt(self, token_json: str):
        """Verify a JWT, checking the session's own ID token against the access token held *now*.

        The plugin remembers the ``at_hash`` of the exchange this tab last stored, in a backend
        var -- one per tab -- while the tokens are cookies the tabs share. After another tab
        refreshed, this tab's memory rejected a perfectly good pair, so it refreshed too, which
        made the other tab's memory stale: two tabs refreshing each other once a second all day
        (armira, 2026-10-28). The expectation is re-derived from the access token in the jar, so
        it binds the ID token to the access token held now. No check asked for stays no check: a
        tab that never stored tokens itself (a new tab, another replica) checks no ``at_hash``,
        as in the plugin, because a refresh answer without a new ID token, or cookies read while
        another tab writes them, would otherwise fail the check and loop.

        The check itself is :meth:`_lex_verify` -- the plugin's, with clock skew allowed and a
        rotated signing key found.
        """
        at_hash = self._expected_at_hash
        if at_hash is not None and token_json and token_json == self._id_token:
            access_token = await self._access_token
            if access_token:
                at_hash = compute_at_hash(access_token, _jwt_alg(token_json))
                if at_hash != self._expected_at_hash:
                    self._expected_at_hash = at_hash
        return await self._lex_verify(token_json, at_hash=at_hash)

    async def _lex_verify(self, token_json: str, *, at_hash: str | None = None):
        """The plugin's ID-token check -- signature, issuer, audience, expiry, ``at_hash`` -- with
        ``_token_guard.JWT_LEEWAY_SECONDS`` of clock skew, and the key set fetched once more when
        a token names a key it lacks (Keycloak rotated its keys; at most once a minute).

        The plugin checks ``iat`` and ``exp`` to the second and keeps the key set for the life of
        the process: a Keycloak two seconds ahead turned a refresh Keycloak accepted into a
        sign-out, and a key rotation signed everybody out at their next refresh and failed every
        new sign-in until the Reflex server restarted.
        """
        issuer = await self._issuer_uri()
        client = self._http_client()
        metadata = await _oidc_utils._fetch_oidc_metadata(issuer, client=client)
        jwks_uri = metadata.get("jwks_uri")
        if not jwks_uri:
            raise RuntimeError("jwks_uri not found in oidc metadata")
        keys = jose_jwk.KeySet.import_key_set(await _oidc_utils._fetch_jwks(jwks_uri, client=client))
        try:
            token = jose_jwt.decode(token_json, keys)
        except InvalidKeyIdError:
            if not guard.may_refetch_keys(jwks_uri):
                raise
            logger.info("An ID token names a signing key the cached key set lacks; fetching Keycloak's keys again.")
            _oidc_utils._OIDC_CACHE.pop(f"jwks:{jwks_uri}", None)
            keys = jose_jwk.KeySet.import_key_set(await _oidc_utils._fetch_jwks(jwks_uri, client=client))
            token = jose_jwt.decode(token_json, keys)
        issuers = await self._valid_issuers()
        stripped = issuer.rstrip("/")
        claims = jose_jwt.JWTClaimsRegistry(
            leeway=guard.JWT_LEEWAY_SECONDS,
            iss={"essential": True, "values": list(issuers) if issuers is not None else [stripped, f"{stripped}/"]},
            aud={"essential": True, "value": await self._client_id()},
        )
        if at_hash is not None:
            claims.options["at_hash"] = {"essential": False, "value": at_hash}
        claims.validate(token.claims)
        return token

    async def _validate_tokens(self) -> bool:
        """The plugin's validation, and a session Keycloak ended through the back channel is over.

        The Django process records ``lex:kc_sid_ended:{sid}`` when Keycloak posts a back-channel
        logout (``lex.lex_app.reflex.backchannel``); tokens of that Keycloak session issued before
        it are signed out here, without waiting for the next refresh to be refused.
        """
        if not await super()._validate_tokens():
            return False
        claims = _token_claims(self._id_token)
        sid = str(claims.get("sid") or "")
        if sid and await asyncio.to_thread(guard.sid_ended, sid, claims.get("iat")):
            await self._lex_sign_out("backchannel_logout", "Keycloak ended this sign-in (back-channel logout)")
            return False
        return True

    @rx.event
    async def do_access_token_refresh(self):
        """The browser's refresh timer fired: refresh when due, audited once, retried when kept.

        The plugin's handler, except that a refresh that kept the tokens is retried (the timer is
        re-armed by the refresh itself) and is no session expiry, and a back-channel sign-out --
        audited where it happened -- is not audited a second time as a failed refresh.
        """
        if not self._refresh_token:
            return None
        self._last_refresh_queued_entry = None
        at_metadata = self._access_token_metadata
        if at_metadata is not None:
            expires_in = max(at_metadata.expires_at - time.time(), 0)
            if expires_in - ACCESS_TOKEN_CACHE_INTERVAL > 0:
                return type(self).trigger_access_token_refresh()
        pre_claims = await self._snapshot_own_identity()
        result = await self._lex_try_refresh()
        if result.token or result.kept or result.outcome == "backchannel":
            return None
        await self._audit_expiry("refresh_failed", userinfo=pre_claims)
        return None

    async def _refresh_access_token(self, expires_at: float | None) -> str:
        """The plugin's entry point to a refresh: the access token to use, ``""`` once signed out."""
        return (await self._lex_refresh(expires_at)).token

    async def _lex_try_refresh(self) -> RefreshResult:
        """``_try_refresh_access_token``, telling its caller how the refresh ended."""
        if not self._refresh_token:
            await self._log_no_refresh_token("Refreshing access token")
            return RefreshResult("no_refresh_token", "")
        metadata = self._access_token_metadata
        return await self._lex_refresh(metadata.expires_at if metadata is not None else None)

    async def _lex_refresh(self, expires_at: float | None) -> RefreshResult:
        """Refresh the tokens -- once per refresh token, across tabs and replicas.

        In order: another task refreshed already (the plugin's own check); a back-channel
        logout ended the session; another tab or replica published a token set for this refresh
        token and this sign-in (adopt it); this tab refreshed less than 30 s ago (suppressed); a
        recent failure backs off; otherwise the tab that takes ``lex:rx:refresh:{sha256(rt)}``
        asks Keycloak and the others wait for its result. Every outcome that keeps the tokens
        re-arms the browser's refresh timer (:meth:`_lex_kept`).
        """
        refresh_token = str(self._refresh_token or "")
        if not refresh_token:
            await self._log_no_refresh_token("Refreshing access token")
            return RefreshResult("no_refresh_token", "")
        metadata = self._access_token_metadata
        if expires_at is not None and metadata is not None and metadata.expires_at != expires_at:
            return RefreshResult("already", metadata.access_token)
        current = metadata.access_token if metadata is not None else ""
        claims = _token_claims(self._id_token)
        sid = str(claims.get("sid") or "")
        if sid and await asyncio.to_thread(guard.sid_ended, sid, claims.get("iat")):
            await self._lex_sign_out("backchannel_logout", "Keycloak ended this sign-in (back-channel logout)")
            return RefreshResult("backchannel", "")
        token_set = await asyncio.to_thread(guard.published, refresh_token)
        if token_set is not None:
            adopted = await self._lex_adopt(token_set, refresh_token)
            if adopted is not None:
                return adopted
        if guard.cooling_down(self._lex_refreshed_at):
            logger.info(self._format_log_message(
                f"A second token refresh within {guard.REFRESH_COOLDOWN_SECONDS:.0f} s was suppressed; "
                "the tokens are kept."
            ))
            return await self._lex_kept("suppressed", current)
        wait = guard.retry_in(refresh_token)
        if wait > 0:
            logger.debug(self._format_log_message(f"Token refresh backing off for {wait:.0f} s more."))
            return await self._lex_kept("backoff", current)
        async with guard.refresh_lock(refresh_token) as leads:
            if not leads:
                token_set = await guard.wait_for_published(refresh_token)
                if token_set is not None:
                    adopted = await self._lex_adopt(token_set, refresh_token)
                    if adopted is not None:
                        return adopted
                logger.info(self._format_log_message(
                    "Another tab's token refresh brought no new tokens; this tab keeps its own."
                ))
                return await self._lex_kept("lock_wait", current)
            token_set = await asyncio.to_thread(guard.published, refresh_token)
            if token_set is not None:  # published while this tab took the lock
                adopted = await self._lex_adopt(token_set, refresh_token)
                if adopted is not None:
                    return adopted
            return await self._lex_refresh_with_keycloak(refresh_token, current)

    async def _lex_refresh_with_keycloak(self, refresh_token: str, current: str) -> RefreshResult:
        """Ask Keycloak, holding the refresh lock; only ``invalid_grant`` signs the user out.

        Keycloak's answer is verified before it is stored (:meth:`_lex_check_token_set`): an
        answer this host cannot verify keeps the old tokens, where the plugin would have stored
        it, failed its own check and signed the user out.
        """
        category, oauth_error, description, exchange = await self._lex_token_request()
        if category == guard.OK:
            token_set = await self._set_tokens_payload_from_exchange(exchange)
            problem = await self._lex_check_token_set(token_set)
            if problem is not None:
                category, oauth_error, description = problem
            else:
                pre_claims = await self._snapshot_own_identity()
                await self._set_tokens(**token_set)
                await chain_event_out_of_band(self, self._cookie_sync_token_update())
                if await self._validate_tokens():
                    guard.note_success(refresh_token)
                    self._lex_refreshed_at = guard.now()
                    await asyncio.to_thread(guard.publish_refreshed, refresh_token, token_set)
                    await self._audit(AuditAction.TOKEN_REFRESH, AuditOutcome.SUCCESS)
                    await self._lex_recovered()
                    return RefreshResult("refreshed", str(exchange.get("access_token") or ""))
                await self._audit(
                    AuditAction.TOKEN_REFRESH, AuditOutcome.FAILURE,
                    reason="post_exchange_validation_failed", userinfo=pre_claims,
                )
                return RefreshResult("rejected", "")
        what = f"{oauth_error or category}: {description}"[:200]
        if category == guard.TERMINAL:
            guard.note_success(refresh_token)  # nothing to back off from: the sign-in is over
            logger.info(self._format_log_message(
                f"Keycloak refused the refresh token ({what}): the sign-in is over; signing the dashboard out."
            ))
            await self._audit(AuditAction.TOKEN_REFRESH, AuditOutcome.FAILURE, reason=oauth_error or "invalid_grant")
            await chain_event_out_of_band(self, type(self).reset_auth)
            return RefreshResult("ended", "")
        delay = guard.note_failure(refresh_token, category)
        if category == guard.CONFIG:
            logger.error(self._format_log_message(
                f"Keycloak's answer to the dashboard's token refresh was refused as misconfigured ({what}); the "
                f"sign-in is kept, next attempt in {delay:.0f} s. Check the client's id and secret "
                "(OIDC_RP_CLIENT_ID / OIDC_RP_CLIENT_SECRET), its Keycloak settings and this host's clock."
            ))
        else:
            logger.warning(self._format_log_message(
                f"Keycloak could not refresh the tokens ({what}); they are kept, next attempt in {delay:.0f} s."
            ))
        await self._audit(AuditAction.TOKEN_REFRESH, AuditOutcome.FAILURE, reason=category)
        return await self._lex_kept(category, current)

    async def _lex_check_token_set(self, token_set: Mapping[str, Any]) -> tuple[str, str | None, str] | None:
        """Verify a token set before it is stored: its ID token (signature, issuer, audience,
        expiry with leeway) bound to its access token by ``at_hash``. ``None`` when it holds;
        else ``(category, None, description)`` -- :data:`CONFIG` for a token this host refuses
        (an unknown key, a clock off by more than the leeway), :data:`TRANSIENT` when Keycloak's
        keys could not be fetched."""
        id_token = token_set.get("id_token")
        metadata = AccessTokenMetadata.from_cookie_value(str(token_set.get("access_token") or ""))
        if not id_token or metadata is None:
            return None  # nothing to bind: the plugin's own handling
        try:
            await self._lex_verify(
                str(id_token), at_hash=compute_at_hash(metadata.access_token, _jwt_alg(str(id_token)))
            )
        except JoseError as exc:
            return guard.CONFIG, None, f"the new ID token was refused: {type(exc).__name__}: {exc}"[:200]
        except Exception as exc:  # the discovery document or the key set could not be fetched
            return guard.TRANSIENT, None, f"the new ID token could not be checked: {type(exc).__name__}"
        return None

    async def _lex_token_request(self) -> tuple[str, str | None, str, dict | None]:
        """The plugin's refresh request to the token endpoint, classified: ``(category,
        oauth_error, description, exchange)``. No answer at all is transient."""
        try:
            data = await self._refresh_access_token_payload()
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if auth_headers := await self._auth_callback_authorization_headers():
                headers.update(auth_headers)
            endpoint = await self._issuer_endpoint("token_endpoint")
            async with self._http_client().post(endpoint, headers=headers, data=data) as resp:
                status = resp.status
                try:
                    body = await resp.json()
                except Exception:  # an HTML page from a proxy, an empty body
                    body = None
        except Exception as exc:  # timeout, connection or TLS error, discovery unavailable
            return guard.TRANSIENT, None, type(exc).__name__, None
        category, oauth_error, description = guard.classify_refresh(status, body)
        if category != guard.OK:
            return category, oauth_error, description or f"HTTP {status}", None
        try:
            exchange = await self._validate_auth_callback_exchange(body)
        except ValueError as exc:  # e.g. not a bearer token: Keycloak is not set up as expected
            return guard.CONFIG, None, str(exc), None
        if not exchange:
            return guard.TRANSIENT, None, "an empty token response", None
        return guard.OK, None, "", exchange

    async def _lex_adopt(self, token_set: Mapping[str, Any], refresh_token: str) -> RefreshResult | None:
        """Take the token set another tab or replica refreshed, through the plugin's ``_set_tokens``.

        Only a set of *this* sign-in: its ID token must name the same user (``sub``) and Keycloak
        session (``sid``) as the one this tab holds, and pass the same verification as Keycloak's
        own answer. ``None`` when the set is not adoptable -- it is ignored, never stored.
        """
        current = _token_claims(self._id_token)
        offered = _token_claims(token_set.get("id_token"))
        if (
            not current.get("sub")
            or offered.get("sub") != current.get("sub")
            or (current.get("sid") and offered.get("sid") != current.get("sid"))
        ):
            logger.warning(self._format_log_message(
                "A published token set belongs to another sign-in; it is not adopted."
            ))
            return None
        problem = await self._lex_check_token_set(token_set)
        if problem is not None:
            logger.warning(self._format_log_message(
                f"A published token set could not be verified ({problem[2]}); it is not adopted."
            ))
            return None
        pre_claims = await self._snapshot_own_identity()
        await self._set_tokens(**{key: token_set[key] for key in _TOKEN_SET_KEYS if token_set.get(key) is not None})
        await chain_event_out_of_band(self, self._cookie_sync_token_update())
        if await self._validate_tokens():
            guard.note_success(refresh_token)
            self._lex_refreshed_at = guard.now()
            logger.info(self._format_log_message(
                "Adopted the tokens another tab or replica refreshed; Keycloak was not asked."
            ))
            await self._audit(AuditAction.TOKEN_REFRESH, AuditOutcome.SUCCESS)
            await self._lex_recovered()
            return RefreshResult("adopted", await self._access_token)
        await self._audit(
            AuditAction.TOKEN_REFRESH, AuditOutcome.FAILURE,
            reason="post_exchange_validation_failed", userinfo=pre_claims,
        )
        return RefreshResult("rejected", "")

    async def _lex_kept(self, outcome: str, current: str) -> RefreshResult:
        """A refresh that left the tokens as they were: retried, and the access token not used
        once expired.

        The browser's refresh timer is re-armed (the plugin's script throttles it to one refresh a
        minute across the tabs), so an outage is retried until it ends; the plugin re-arms only
        after a success. Once the access token has expired, the session is the Reflex equivalent
        of the HTTP contract's 503 (:meth:`_lex_withhold_identity`).
        """
        if not self._lex_access_valid():
            self._lex_withhold_identity(outcome)
        await chain_event_out_of_band(self, type(self).trigger_access_token_refresh)
        return RefreshResult(outcome, current)

    def _lex_withhold_identity(self, why: str) -> None:
        """Serve no identity, permissions or data while the access token is expired and Keycloak
        cannot renew it -- without signing out: the cookies stay, renewal is retried, and the next
        successful refresh restores the session."""
        if not self.sign_in_unavailable:
            logger.warning(self._format_log_message(
                f"The access token has expired and Keycloak could not renew it ({why}): no identity, permissions "
                "or data until it can. The sign-in is kept and renewal is retried."
            ))
        self.sign_in_unavailable = True
        self._cached_userinfo = None
        self._uma_permissions = []
        self._uma_permissions_token = ""

    def _lex_expire_identity(self) -> bool:
        """Drop the cached profile once the access token has expired (:class:`SignInExpiryMiddleware`).

        The plugin keeps a profile for 30 minutes per access token and serves it without looking
        at the token; dropping it makes the next look at who is signed in re-resolve it -- a
        refresh, or no identity while Keycloak cannot renew the token. ``True`` when it dropped one.
        """
        if self._cached_userinfo is None or self._access_token_metadata is None or self._lex_access_valid():
            return False
        self._cached_userinfo = None
        return True

    async def _lex_recovered(self) -> None:
        """Fresh tokens after the sign-in was unavailable: it is again, and a ``/login`` waiting for
        it goes back to the page that was asked for -- no new sign-in."""
        if not self.sign_in_unavailable:
            return
        self.sign_in_unavailable = False
        logger.info(self._format_log_message("Keycloak renewed the tokens again; the sign-in is restored."))
        if self._lex_on_login_page():
            target = self._safe_redirect_url(self._post_login_redirect_target())
            await chain_event_out_of_band(self, rx.redirect(target, replace=True))

    def _lex_on_login_page(self) -> bool:
        try:
            path = str(self.router.url.path or "")
        except Exception:
            return False
        return path.rstrip("/") == get_login_endpoint().rstrip("/")

    async def _lex_sign_out(self, reason: str, why: str) -> None:
        """Sign the dashboard out now (server side), and have the browser drop the cookies."""
        pre_claims = await self._snapshot_own_identity()
        logger.info(self._format_log_message(f"{why}; signing the dashboard out."))
        await self._audit_expiry(reason, userinfo=pre_claims)
        await self._reset_session()
        await chain_event_out_of_band(self, type(self).reset_auth)

    async def _fetch_userinfo(self):
        """The plugin's userinfo request, with the status kept on the error (401 vs an outage)."""
        if not (userinfo_endpoint := await self._issuer_endpoint("userinfo_endpoint")):
            raise MissingUserinfoEndpointError("OIDC issuer does not have a userinfo endpoint")
        if not (access_token := await self._access_token):
            raise MissingAccessTokenError("No access token available to fetch userinfo")
        async with self._http_client().get(
            userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"}
        ) as resp:
            if not is_ok(resp):
                raise UserinfoStatusError(resp.status)
            return await resp.json()

    async def _get_userinfo(self):
        """Who is signed in, signing out only when Keycloak says the sign-in is over.

        Tokens that fail validation, or an access token that expired, are refreshed (once per
        refresh token across tabs). A refresh that could not be done keeps the sign-in: while the
        access token is still valid, the profile the session has is served; once it expired,
        none (:meth:`_lex_userinfo_unavailable`). A userinfo 401 gets one refresh and one retry; a
        userinfo outage (5xx, timeouts) is handled the same way.
        """
        if not await self._validate_tokens() or not self._lex_access_valid():
            if not await self._has_any_token_present():
                return None  # validation signed the session out (a back-channel logout)
            pre_claims = await self._snapshot_own_identity()
            result = await self._lex_try_refresh()
            if result.kept:
                return await self._lex_userinfo_unavailable(f"tokens not refreshed: {result.outcome}")
            if result.signed_out:
                return None
            if not await self._validate_tokens():
                await self._lex_tokens_invalid(pre_claims)
                return None
        try:
            profile = await self._fetch_userinfo()
        except MissingUserinfoEndpointError:
            return await self._get_id_token_claims()
        except UserinfoStatusError as exc:
            if exc.status != 401 or not self._refresh_token:
                return await self._lex_userinfo_unavailable(f"HTTP {exc.status}")
        except Exception as exc:  # no answer: timeout, connection error, discovery unavailable
            return await self._lex_userinfo_unavailable(type(exc).__name__)
        else:
            self.sign_in_unavailable = False
            return profile
        # Keycloak refused the access token: one refresh, one retry.
        pre_claims = await self._snapshot_own_identity()
        result = await self._lex_try_refresh()
        if result.kept:
            return await self._lex_userinfo_unavailable(f"access token refused, not refreshed: {result.outcome}")
        if result.signed_out:
            return None
        if not await self._validate_tokens():
            await self._lex_tokens_invalid(pre_claims)
            return None
        try:
            return await self._fetch_userinfo()
        except Exception as exc:
            return await self._lex_userinfo_unavailable(f"refused again after a refresh: {type(exc).__name__}")

    async def _lex_tokens_invalid(self, pre_claims: Any) -> None:
        """The plugin's own end for tokens a refresh could not make valid: sign the session out."""
        logger.warning(self._format_log_message(
            "Tokens invalid and refresh did not restore them while resolving userinfo; resetting auth "
            f"(user will be logged out). {await self._token_presence_summary()}"
        ))
        await self._audit_expiry("tokens_invalid", userinfo=pre_claims)
        await call_event_from_computed_var(self, type(self).reset_auth)

    async def _lex_userinfo_unavailable(self, why: str):
        """Keycloak could not say who this is right now: keep the sign-in, serve only what is sure.

        Once the access token has expired: nobody (:meth:`_lex_withhold_identity`). While it is
        valid: the cached profile, but only when it is the profile of the user the verified ID
        token names (a cookie jar may hold another user's tokens by now); otherwise the verified
        ID token's own claims; and nothing when the ID token cannot be verified. Never a sign-out.
        """
        if not self._lex_access_valid():
            self._lex_withhold_identity(why)
            return None
        logger.warning(self._format_log_message(
            f"Keycloak's userinfo is unavailable ({why}); the user stays signed in on the verified ID token."
        ))
        try:
            claims = dict(await self._get_id_token_claims())
        except Exception:  # the ID token cannot be verified right now: no identity to vouch for
            self._cached_userinfo = None
            return None
        cached = self._cached_userinfo
        if cached is not None and str((cached.userinfo or {}).get("sub") or "") == str(claims.get("sub") or ""):
            return None  # the userinfo var serves the cached profile: the same user
        self._cached_userinfo = None  # another sign-in's profile is never served for these tokens
        return claims

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
        * Signed in, but the access token expired and Keycloak cannot renew it
          (:attr:`sign_in_unavailable`): the page says so and retries; no new
          sign-in is started.
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

        if self.sign_in_unavailable and self._refresh_token:
            # Signed in, but Keycloak cannot renew the tokens right now: wait here, saying so, and
            # keep retrying; a new sign-in could not complete either. The next refresh that
            # succeeds sends the browser back to the page (``_lex_recovered``).
            self.login_needs_click = False
            return type(self).trigger_access_token_refresh()

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
        never lets one user's permissions stand in for another's, and grants
        nothing once the access token has expired -- also not the permissions
        cached for it -- until a refresh renews it.
        """
        if not self._lex_access_valid():
            return []
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


guard.restore_plugin_cookies(_oidc_state, _PLUGIN_COOKIES)
# After the class exists: its cookies really last 30 days, and the plugin is the release these
# overrides were written against (each logs what is wrong otherwise; the dashboards still work).
guard.check_cookie_lifetime(LexKeycloakAuthState)
guard.check_plugin_version()


class SignInExpiryMiddleware(Middleware):
    """Before every event: a dashboard whose access token has expired does not keep its profile.

    The plugin caches who is signed in for 30 minutes per access token and hands that profile to
    every event without looking at the token again. This runs before the plugin's auth gate
    (``lex.lex_app.reflex.app.create_app`` adds it when the app is built; the plugin adds its gate
    after compiling): once the access token has expired it drops the cached profile, so the gate
    resolves the user again -- through a refresh, or, while Keycloak cannot renew the token, as
    nobody: no identity, no permissions, no data, but still signed in.
    """

    async def preprocess(self, app: Any, state: Any, event: Any) -> Any:
        """Drop an expired session's cached profile before the auth gate reads it."""
        try:
            provider = await state.get_state(LexKeycloakAuthState)
        except Exception:  # a state tree without lex's provider
            return None
        provider._lex_expire_identity()
        return None

    async def postprocess(self, app: Any, state: Any, event: Any, update: Any) -> Any:
        return update


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
    (:attr:`LexKeycloakAuthState.login_needs_click`). While a signed-in user's
    tokens cannot be renewed (:attr:`LexKeycloakAuthState.sign_in_unavailable`)
    it says so and waits for the retry instead. With more than one provider
    there is no single sign-in to start, so the plugin's palette stays.
    """
    from reflex_enterprise.auth.pages import default_login_page

    if len(providers) != 1 or not issubclass(providers[0], LexKeycloakAuthState):
        return default_login_page(providers=providers, **context)
    provider = providers[0]
    return rx.center(
        rx.cond(
            provider.sign_in_unavailable,
            rx.vstack(
                rx.spinner(size="3"),
                rx.text("Sign-in is temporarily unavailable. Retrying…", color_scheme="gray"),
                spacing="3",
                align="center",
            ),
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
        ),
        on_mount=rx.call_function(_LOGIN_PAGE_JS, callback=provider.start_login),
        min_height="60vh",
    )


async def current_access_token(state: rx.State) -> str:
    """The signed-in user's Keycloak access token -- ``""`` when there is none.

    Send it as a bearer token to call the lex-app API as the user, so the API's
    permissions apply to the dashboard exactly as they do in the grid. It never
    reaches the browser, and Reflex Enterprise keeps it fresh. An expired one is
    not handed out: ``""`` until a refresh renews it.
    """
    provider = await state.get_state(LexKeycloakAuthState)
    if not provider._lex_access_valid():
        return ""
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
