"""The signed-in user's Keycloak permissions, as RFDS's permission checks read them.

RFDS gates UI and handlers on permission *strings* -- ``rfds.auth.can("x")``, the
side navigation's ``NavItem(permission=...)``, ``rfds.auth.requires("x")`` --
and asks a resolver for the strings an identity holds. lex-app's permissions
are Keycloak UMA grants, the same ones the grid and the REST API enforce, so the
resolver hands RFDS those (decision D3), one string per model-wide grant::

    "<app_label>.<ModelName>:<scope>"      e.g. "lex_app.Fund:edit"

:func:`permission` spells one for a model, so a page never types it::

    from rfds.auth import can

    can(permission(Fund, "edit"), rfds.button("Revalue", ...))

Keycloak is asked over HTTP with the user's access token, which a resolver --
synchronous, given only the identity -- cannot do. So :class:`LexAccessState`
fetches the grants in an event (every lex page loads it), remembers them for
that identity, and then lets RFDS's ``AccessState`` load as usual; the resolver
answers from what was remembered. :class:`GrantsRefreshMiddleware` keeps that
fresh while a page stays open, so a handler guarded by RFDS's ``requires()``
does not start refusing on a page left open longer than the grants' lifetime.
A grant scoped to single records does not open the whole model, exactly as in
:func:`~lex.lex_app.reflex.auth.has_permission`.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable, Mapping

import reflex as rx
from reflex.middleware import Middleware

#: How long remembered grants answer for an identity. Every page load, and
#: every event once they are older than :data:`GRANTS_REFRESH_SECONDS`,
#: refreshes them; this bounds how long a withdrawn grant keeps answering.
GRANTS_TTL_SECONDS = 300.0

#: How old remembered grants may get before an event refreshes them first.
GRANTS_REFRESH_SECONDS = 60.0

_GRANTS: dict[str, tuple[float, frozenset[str]]] = {}
_LOCK = threading.Lock()


def resource_name(target: Any) -> str:
    """A Keycloak resource name: as given, or the one lex-app registers for a model."""
    from lex.lex_app.reflex.auth import _resource_name

    return _resource_name(target)


def permission(target: Any, scope: str = "read") -> str:
    """The RFDS permission string for ``scope`` on a model (or a Keycloak resource name)."""
    if not scope or ":" in scope:
        raise ValueError(f"permission() takes a Keycloak scope such as 'read', got {scope!r}")
    return f"{resource_name(target)}:{scope}"


def permission_strings(permissions: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """The RFDS permission strings for a list of Keycloak UMA grants (model-wide grants only)."""
    strings = set()
    for grant in permissions or ():
        if not isinstance(grant, Mapping) or grant.get("resource_set_id") is not None:
            continue
        name = grant.get("rsname")
        if not isinstance(name, str) or not name:
            continue
        for scope in grant.get("scopes") or ():
            if isinstance(scope, str) and scope:
                strings.add(f"{name}:{scope}")
    return frozenset(strings)


def identity_key(identity: Any) -> str:
    """Who grants are remembered for: issuer and subject, never the e-mail address."""
    if identity is None or not getattr(identity, "sub", ""):
        return ""
    return f"{getattr(identity, 'issuer', '') or ''}|{identity.sub}"


def remember_grants(identity: Any, permissions: Iterable[Mapping[str, Any]], *, now: float | None = None) -> frozenset[str]:
    """Remember ``identity``'s grants for the resolver; returns their strings."""
    key = identity_key(identity)
    strings = permission_strings(permissions)
    if key:
        with _LOCK:
            _GRANTS[key] = (time.monotonic() if now is None else now, strings)
    return strings


def grants_age(identity: Any, *, now: float | None = None) -> float | None:
    """Seconds since ``identity``'s grants were remembered; ``None`` when never."""
    key = identity_key(identity)
    with _LOCK:
        entry = _GRANTS.get(key) if key else None
    if entry is None:
        return None
    return (time.monotonic() if now is None else now) - entry[0]


def forget_grants(identity: Any = None) -> None:
    """Forget one identity's remembered grants, or everyone's."""
    with _LOCK:
        if identity is None:
            _GRANTS.clear()
        else:
            _GRANTS.pop(identity_key(identity), None)


def lex_permission_resolver(identity: Any, *, now: float | None = None) -> frozenset[str]:
    """RFDS's permission resolver: the grants last loaded for ``identity``, while fresh.

    Fails closed: an identity nothing was loaded for, or loaded for too long
    ago, holds no permission until its next page load fetches them again.
    """
    key = identity_key(identity)
    if not key:
        return frozenset()
    with _LOCK:
        entry = _GRANTS.get(key)
    if entry is None:
        return frozenset()
    loaded_at, strings = entry
    current = time.monotonic() if now is None else now
    if current - loaded_at > GRANTS_TTL_SECONDS:
        return frozenset()
    return strings


def install_permission_resolver() -> None:
    """Make RFDS resolve permissions from Keycloak's grants (run by ``create_app``)."""
    from rfds.auth import set_permission_resolver

    set_permission_resolver(lex_permission_resolver)


class GrantsRefreshMiddleware(Middleware):
    """Before an event: refresh the signed-in user's remembered grants once they are a minute old.

    RFDS's ``requires()`` checks a handler against the resolver, which answers
    from grants remembered at page load. Without this, a page left open past
    :data:`GRANTS_TTL_SECONDS` would have every such handler refused. The fetch
    is the provider's, cached per access token, so a refresh costs a Keycloak
    call at most once per token. It never stops an event: if anything fails,
    the grants simply stay as they were.
    """

    async def preprocess(self, app: Any, state: Any, event: Any) -> Any:
        try:
            from rfds.auth import current_identity

            from lex.lex_app.reflex.auth import current_permissions

            identity = current_identity(state)
            age = grants_age(identity)
            if identity is not None and (age is None or age > GRANTS_REFRESH_SECONDS):
                remember_grants(identity, await current_permissions(state))
        except Exception:
            pass
        return None

    async def postprocess(self, app: Any, state: Any, event: Any, update: Any) -> Any:
        return update


class LexAccessState(rx.State):
    """Loads the user's Keycloak grants, then RFDS's ``AccessState`` from them.

    Every :func:`~lex.lex_app.reflex.shell.lex_page` loads it, so ``rfds.auth.can``
    and the navigation's permissions are right on the first paint after the
    page's load events.
    """

    @rx.event
    async def load(self):
        """Fetch the grants for the signed-in user and hand over to RFDS's ``AccessState.load``."""
        from rfds.auth import AccessState, current_identity

        from lex.lex_app.reflex.auth import current_permissions

        identity = current_identity(self)
        if identity is not None:
            remember_grants(identity, await current_permissions(self))
        return AccessState.load
