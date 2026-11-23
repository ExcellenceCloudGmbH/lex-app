"""Who is signed in to a Reflex dashboard, what Keycloak lets them do, and how a handler checks it.

A Reflex dashboard signs in through lex-app's own sign-in -- the Django hand-off of
:mod:`lex.lex_app.reflex.session` -- and never runs an OpenID Connect flow of its own. What a
dashboard author uses is here:

* :func:`current_access_token` -- the user's access token, for lex-app's API as the user. It
  comes from Django's sign-in (``broker.access_token``), refreshed there, never in the browser.
* :func:`current_permissions` -- the user's Keycloak UMA permissions, what a Streamlit dashboard
  reads from ``st.session_state.permissions``; fetched once per access token.
* :func:`has_permission` -- a check built from one of them, for :func:`lex_event`::

      class FundDashboard(rx.State):
          @lex_event(auth=has_permission(Fund, "edit"))
          async def revalue(self): ...

* :func:`lex_event` -- ``rx.event`` with an ``auth=`` check that RFDS's ``PolicyMiddleware``
  enforces before the handler runs: the replacement of Reflex Enterprise's ``rxe.event(auth=)``.
* :class:`LexUser` -- the signed-in user's names as frontend vars, and signing out.

Every page, handler and var already requires a sign-in (``LexSessionMiddleware``); a check is
for what a signed-in user may or may not do.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
from typing import Any, Awaitable, Callable

import reflex as rx

from lex.lex_app.reflex import session

logger = logging.getLogger(__name__)


def lex_oidc_setting(key: str) -> str | None:
    """The value lex-app's own Keycloak settings give an OIDC config ``key``.

    The same facts every other part of lex-app authenticates with: the realm's issuer, and the
    confidential client Django signs users in through.
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


def _token_subject(token: str) -> str:
    """The ``sub`` of an access token Django has already validated."""
    try:
        import jwt

        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except Exception:  # noqa: BLE001
        return ""
    return str(claims.get("sub") or "")


def _fetch_uma_permissions(access_token: str) -> list[dict] | None:
    """The user's UMA permissions from Keycloak, or ``None`` if Keycloak could not say.

    ``KeycloakManager`` is the client every other part of lex-app asks, so the permissions a
    dashboard sees are the ones the API enforces.
    """
    try:
        from lex.api.views.authentication.KeycloakManager import KeycloakManager

        permissions = KeycloakManager().get_uma_permissions(access_token)
    except Exception:  # noqa: BLE001
        logger.exception("Could not fetch Keycloak UMA permissions")
        return None
    return permissions if isinstance(permissions, list) else None


async def current_access_token(state: rx.State) -> str:
    """The signed-in user's Keycloak access token -- ``""`` when there is none.

    Send it as a bearer token to call the lex-app API as the user, so the API's permissions
    apply to the dashboard exactly as they do in the grid. It never reaches the browser; Django
    keeps it fresh, once for every holder of the sign-in.
    """
    return await session.access_token(state)


#: UMA permissions per access token (its SHA-256): (subject, permissions).
_UMA: dict[str, tuple[str, list[dict]]] = {}
#: The last permissions each subject was granted, for a Keycloak blip.
_LAST_BY_SUBJECT: dict[str, list[dict]] = {}
_UMA_LOCK = threading.Lock()
_UMA_LIMIT = 2048


async def permissions_of(token: str) -> list[dict]:
    """The UMA permissions Keycloak grants the holder of ``token`` (see :func:`current_permissions`)."""
    if not token:
        return []
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _UMA_LOCK:
        known = _UMA.get(digest)
    if known is not None:
        return list(known[1])
    subject = _token_subject(token)
    fetched = await asyncio.to_thread(_fetch_uma_permissions, token)
    with _UMA_LOCK:
        if fetched is None:
            return list(_LAST_BY_SUBJECT.get(subject, []))
        if len(_UMA) >= _UMA_LIMIT:
            _UMA.clear()
        _UMA[digest] = (subject, fetched)
        _LAST_BY_SUBJECT[subject] = fetched
    return list(fetched)


async def current_permissions(state: rx.State) -> list[dict]:
    """The signed-in user's Keycloak UMA permissions.

    The Reflex counterpart of ``st.session_state.permissions``: the list Keycloak returns, one
    ``{"rsname": ..., "scopes": [...]}`` entry per resource, fetched once per access token. A
    failed lookup keeps what the same user was last granted -- a Keycloak blip should not demote
    someone mid-session -- and never lets one user's permissions stand in for another's. No
    sign-in, no permissions.
    """
    return await permissions_of(await current_access_token(state))


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
    """A check: Keycloak grants the user ``scope`` on ``target``.

    ``target`` is a model class -- resolved to the resource lex-app registers for it,
    ``<app_label>.<ModelName>`` -- or a resource name. Only a model-wide grant counts; a grant
    scoped to single records does not open the whole model. Pass it to :func:`lex_event`'s
    ``auth=``, or call it with a state (``await has_permission(Fund)(self)``). It fails closed:
    no sign-in, no permissions, or Keycloak unreachable for a user it never answered for, is a
    denial.
    """
    resource = _resource_name(target)

    async def check(ctx: Any) -> bool:
        # RFDS hands a check its AuthContext, whose `state` is the event's root state; a caller
        # may also pass a state directly (a ui.State's own ``state`` is a handler helper, not this).
        state = ctx if isinstance(ctx, rx.State) else getattr(ctx, "state", None) or ctx
        for permission in await current_permissions(state):
            if (
                isinstance(permission, dict)
                and permission.get("rsname") == resource
                and permission.get("resource_set_id") is None
                and scope in (permission.get("scopes") or ())
            ):
                return True
        return False

    check.__qualname__ = f"has_permission({resource!r}, {scope!r})"
    check.__name__ = check.__qualname__
    return check


def lex_event(fn: Callable[..., Any] | None = None, *, auth: Any = None, **event_kwargs: Any) -> Any:
    """``rx.event`` with an optional ``auth=`` check -- Reflex Enterprise's ``rxe.event(auth=)``.

    ``auth`` is one check or a list of them (:func:`has_permission`, RFDS's
    ``requires(permission(...))``, any ``async (ctx) -> bool``). RFDS's ``PolicyMiddleware``
    runs them before the handler, with the event's root state as ``ctx.state``; a denial shows
    RFDS's "not permitted" toast and the handler never runs::

        @lex_event(auth=has_permission(Fund, "read"))
        async def load(self): ...

    Without ``auth`` it is ``rx.event``: every handler already requires a sign-in.
    """

    def decorate(handler: Callable[..., Any]) -> Any:
        if auth is not None:
            from rfds.auth import policy

            checks = list(auth) if isinstance(auth, (list, tuple)) else [auth]
            handler = policy(*checks)(handler)
        return rx.event(handler, **event_kwargs) if event_kwargs else rx.event(handler)

    return decorate(fn) if fn is not None else decorate


class LexUser(rx.State):
    """The signed-in user, as frontend vars -- empty until the sign-in is known.

    ``display_name`` is the name to greet someone by (the name, else the username, else the
    e-mail). :meth:`sign_out` ends the dashboard's binding and signs out of lex-app (and
    Keycloak), like lex-app's own sign-out.
    """

    @rx.var(cache=False)
    def sub(self) -> str:
        """Keycloak's subject."""
        return str(session.profile(self).get("sub") or "")

    @rx.var(cache=False)
    def email(self) -> str:
        """The e-mail address."""
        return str(session.profile(self).get("email") or "")

    @rx.var(cache=False)
    def username(self) -> str:
        """Keycloak's ``preferred_username``."""
        return str(session.profile(self).get("preferred_username") or "")

    @rx.var(cache=False)
    def display_name(self) -> str:
        """The name, else the username, else the email -- whichever reads best."""
        info = session.profile(self)
        for candidate in (info.get("name"), info.get("preferred_username"), info.get("email")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    @rx.event
    def sign_out(self):
        """Sign out: the backend revokes the binding, then lex-app signs the user out."""
        return session.navigate(session.sign_out_url(self))


__all__ = [
    "LexUser",
    "current_access_token",
    "current_permissions",
    "has_permission",
    "lex_event",
    "lex_oidc_setting",
]
