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
  ``auth=`` check.

Its ``__provider__`` is ``lex_keycloak`` rather than ``keycloak`` on purpose: the
plugin reads ``{PROVIDER}_CLIENT_ID`` first, and ``KEYCLOAK_CLIENT_ID`` already
means something else in lex-app -- the client id handed to the browser.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any, Awaitable, Callable

import httpx
import jwt
import reflex as rx
import reflex_enterprise as rxe
from reflex_enterprise.auth import AuthUserState, OIDCAuthState

logger = logging.getLogger(__name__)

PROVIDER = "lex_keycloak"

_TRUTHY = {"1", "true", "yes", "y", "on"}


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
