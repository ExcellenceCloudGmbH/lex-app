"""Sign-in for lex's WebSocket consumers: the close codes of the HTTP auth contract v2.

A browser's WebSocket carries the same ``sessionid`` cookie as its API requests. The routes of
:func:`lex.lex_app.routing.authenticated_websocket_urlpatterns` run behind Channels'
``AuthMiddlewareStack`` and django-oauth2-codeflow's ``OIDCSessionASGIMiddleware``
(``lex/lex_app/asgi.py``), and their consumers use :class:`LexSocketAuthMixin`. ``ws/health``
stays public: the frontend probes it while signed out.

**Who is signed in.** An authenticated Django user whose session is an active OIDC sign-in (its
``OIDCSession`` row not ended, and the same user), or a login of another backend --
``createsuperuser`` plus the admin, a local run without Keycloak -- exactly as the HTTP side
treats them (``lex.authentication.identity``). An OIDC login whose sign-in ended is not. API keys
and bearer tokens do not sign a socket in: the stack reads the session cookie only.

**What happens to a socket without one** depends on ``LEX_WS_AUTH`` (``settings.LEX_WS_AUTH``):

* ``off`` -- as before 2.5: every socket is served, nothing is checked or logged.
* ``log`` (the default) -- every socket is still served exactly as before; a socket ``enforce``
  would close is logged once, at WARNING, naming the consumer, the path, the code and the reason.
  This is the first step of the rollout: it shows who would be closed before anyone is. Nothing
  the check does can break a socket in this mode: a check that fails is logged, and the socket
  served.
* ``enforce`` -- the socket is accepted, then closed (closing during the handshake reaches the
  browser as 1006, not the code):

  ========  ===========================================================================
  4401      not signed in, or the sign-in ended -- at connect (also when it ends while the
            socket joins its sign-in's group), when the sign-in ends (the library's
            ``oidc.session.ended`` group message), or at the re-validation every
            ``oidc_revalidate_seconds`` (5 minutes). The client signs in again.
  4403      the consumer's :meth:`LexSocketAuthMixin.lex_socket_authorize` refuses the user.
            The client stops.
  4503      the sign-in could not be checked (the database or Keycloak did not answer, or
            the check failed). The client reconnects with backoff.
  ========  ===========================================================================

**Re-validation** (:func:`sign_in_alive`) closes a socket whose sign-in is over in any of these
ways: the ``OIDCSession`` row ended, or its refresh token expired (Keycloak's session ran out
without anyone asking) -- for a sign-in Keycloak gave no refresh token, its access token expired;
the Django session is gone (a logout that bypassed the library, such as
``/admin/logout/``, an expired or deleted session -- this also covers logins of other backends);
the user was deactivated. A database that does not answer keeps the socket open until the next
round: an outage never closes a signed-in socket.

Socket code never refreshes tokens: it only reads whether the sign-in is still alive, so a socket
outlives an expired access token and ends with the sign-in itself.

A consumer opts in by calling :meth:`~LexSocketAuthMixin.lex_socket_connect` first in
``connect()`` and :meth:`~LexSocketAuthMixin.lex_socket_disconnect` in ``disconnect()``::

    class FundTicker(LexSocketAuthMixin, AsyncWebsocketConsumer):
        async def connect(self):
            if not await self.lex_socket_connect():
                return  # closed with 4401 / 4403 / 4503
            await self.accept()

        async def disconnect(self, code):
            await self.lex_socket_disconnect()

:class:`LexSyncSocketAuthMixin` is the same for a synchronous ``WebsocketConsumer``. Its
re-validation needs a channel layer: a ticker on the event loop sends the consumer a
``lex.socket.revalidate`` message every ``oidc_revalidate_seconds``, and the consumer checks in its
own thread. Without a channel layer it re-validates only at connect.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from oauth2_authcodeflow.contrib.channels import (
    CLOSE_AUTH_UNAVAILABLE,
    CLOSE_FORBIDDEN,
    CLOSE_UNAUTHENTICATED,
    OIDCSessionConsumerMixin,
    SyncOIDCSessionConsumerMixin,
    group_name,
)

logger = logging.getLogger(__name__)

#: ``LEX_WS_AUTH`` values.
WS_AUTH_OFF = "off"
WS_AUTH_LOG = "log"
WS_AUTH_ENFORCE = "enforce"
WS_AUTH_MODES = (WS_AUTH_OFF, WS_AUTH_LOG, WS_AUTH_ENFORCE)
#: ``log``: no client breaks on upgrade, and the rollout starts collecting evidence at once.
DEFAULT_WS_AUTH_MODE = WS_AUTH_LOG

#: How often a signed-in socket checks that its sign-in is still alive (plan: every 5 minutes).
REVALIDATE_SECONDS = 300

#: The message a synchronous consumer's re-validation ticker sends it.
REVALIDATE_MESSAGE = "lex.socket.revalidate"


def ws_auth_mode() -> str:
    """The ``LEX_WS_AUTH`` mode in force: ``off``, ``log`` or ``enforce``.

    ``settings.py`` refuses to start on any other value; one set later (``override_settings``)
    falls back to the default rather than breaking every socket.
    """
    from django.conf import settings

    raw = str(getattr(settings, "LEX_WS_AUTH", DEFAULT_WS_AUTH_MODE) or DEFAULT_WS_AUTH_MODE)
    mode = raw.strip().lower()
    if mode not in WS_AUTH_MODES:
        logger.warning("LEX_WS_AUTH=%r is not one of %s; using %r.", raw, "/".join(WS_AUTH_MODES), DEFAULT_WS_AUTH_MODE)
        return DEFAULT_WS_AUTH_MODE
    return mode


@dataclass(frozen=True)
class SocketSignIn:
    """What a socket's handshake says about its sign-in.

    ``close_code`` is ``None`` when the socket may be served, else the code ``enforce`` closes it
    with. ``session_id`` is the ``OIDCSession`` a served socket follows (``None`` for a login of
    another backend); ``session_key`` and ``user_pk`` are the Django session and the user its
    re-validation checks. ``reason`` is the word the logs use.
    """

    close_code: Optional[int]
    reason: str
    session_id: Optional[str] = None
    session_key: Optional[str] = None
    user_pk: Any = None

    @property
    def signed_in(self) -> bool:
        return self.close_code is None and self.user_pk is not None


def _is_oidc_backend(path: Any) -> bool:
    """Whether a session's ``BACKEND_SESSION_KEY`` names django-oauth2-codeflow's backend."""
    if not path:
        return False
    from django.utils.module_loading import import_string

    try:
        backend = import_string(str(path))
    except ImportError:
        return False
    from oauth2_authcodeflow.auth import AuthenticationBackend

    return isinstance(backend, type) and issubclass(backend, AuthenticationBackend)


def _session_key(scope: Mapping[str, Any]) -> Optional[str]:
    session = scope.get("session")
    try:
        key = getattr(session, "session_key", None) if session is not None else None
    except Exception:  # a lazy session that cannot load
        return None
    return str(key) if key else None


def _signed_in_without_database(scope: Mapping[str, Any]) -> Optional[SocketSignIn]:
    """The answer when the scope settles it alone: anonymous, or resolved by the ASGI middleware."""
    user = scope.get("user")
    if user is None or not getattr(user, "is_authenticated", False):
        return SocketSignIn(CLOSE_UNAUTHENTICATED, "anonymous")
    info = scope.get("oidc_session")
    if info and info.get("session_id"):
        return SocketSignIn(None, "signed_in", str(info["session_id"]), _session_key(scope), getattr(user, "pk", None))
    return None


def resolve_socket_sign_in(scope: Mapping[str, Any]) -> SocketSignIn:
    """Classify a socket's sign-in from its ASGI scope (synchronous; may read the database).

    ``OIDCSessionASGIMiddleware`` puts the active sign-in into ``scope['oidc_session']`` and
    answers ``None`` for every other case -- no sign-in, an ended one, someone else's, or a
    database that did not answer. Those are told apart here, because they close differently
    (4401 against 4503) and a login of another backend is not closed at all. Without the
    middleware the sign-in is resolved here. Any database error -- ``DatabaseError`` or the
    ``InterfaceError`` of a dropped connection -- is 4503.
    """
    settled = _signed_in_without_database(scope)
    if settled is not None:
        return settled
    from django.contrib.auth import BACKEND_SESSION_KEY
    from django.core.exceptions import ValidationError
    from django.db import Error as DatabaseFailure
    from oauth2_authcodeflow.conf import settings as oidc_settings
    from oauth2_authcodeflow.models import OIDCSession

    user = scope.get("user")
    user_pk = getattr(user, "pk", None)
    session = scope.get("session")
    try:
        key = session.get(oidc_settings.OIDC_SESSION_KEY) if session is not None else None
        backend = session.get(BACKEND_SESSION_KEY) if session is not None else None
    except Exception:  # the session store did not answer
        return SocketSignIn(CLOSE_AUTH_UNAVAILABLE, "auth_unavailable")
    if not key:
        if _is_oidc_backend(backend):
            # An OIDC login without a sign-in row: ended, or a 2.x session the next HTTP
            # request migrates. Either way the socket cannot follow it.
            return SocketSignIn(CLOSE_UNAUTHENTICATED, "session_ended")
        return SocketSignIn(None, "django_login", None, _session_key(scope), user_pk)
    try:
        row = OIDCSession.objects.filter(pk=key).only("pk", "user_id", "ended_at").first()
    except (ValidationError, ValueError):
        row = None
    except DatabaseFailure:
        return SocketSignIn(CLOSE_AUTH_UNAVAILABLE, "auth_unavailable")
    if row is None or row.ended_at is not None or row.user_id != user_pk:
        return SocketSignIn(CLOSE_UNAUTHENTICATED, "session_ended")
    return SocketSignIn(None, "signed_in", str(row.pk), _session_key(scope), user_pk)


def sign_in_active(session_id: str) -> bool:
    """Whether the ``OIDCSession`` a socket follows is still active (synchronous)."""
    from oauth2_authcodeflow.models import OIDCSession

    return OIDCSession.objects.filter(pk=session_id, ended_at__isnull=True).exists()


def sign_in_alive(session_id: Optional[str], session_key: Optional[str], user_pk: Any) -> bool:
    """Whether a socket's sign-in is still alive (synchronous; may raise a database error).

    * the ``OIDCSession`` (when it follows one): not ended, the same user, its refresh token not
      expired -- a session Keycloak let run out ends here even when nobody asked for a refresh --
      and, for a row that never had a refresh token, its access token not expired;
    * the Django session: still there and still this user's (a logout that bypassed the library,
      such as ``/admin/logout/``, or a deleted or expired session ends the socket too);
    * the user: still active.
    """
    from django.contrib.auth import SESSION_KEY, get_user_model
    from django.utils import timezone

    now = timezone.now()
    if session_id:
        from oauth2_authcodeflow.models import OIDCSession

        row = (
            OIDCSession.objects.filter(pk=session_id)
            .only("pk", "user_id", "ended_at", "refresh_expires_at", "access_expires_at", "refresh_token_enc")
            .first()
        )
        if row is None or row.ended_at is not None or (user_pk is not None and row.user_id != user_pk):
            return False
        if row.refresh_expires_at is not None and row.refresh_expires_at <= now:
            return False
        if not row.refresh_token_enc and row.access_expires_at is not None and row.access_expires_at <= now:
            return False
    if session_key:
        from importlib import import_module

        from django.conf import settings

        store = import_module(settings.SESSION_ENGINE).SessionStore(session_key=session_key)
        if str(store.load().get(SESSION_KEY) or "") != str(user_pk if user_pk is not None else ""):
            return False
    if user_pk is not None:
        model = get_user_model()
        users = model.objects.filter(pk=user_pk)
        if any(field.name == "is_active" for field in model._meta.concrete_fields):
            users = users.filter(is_active=True)  # a custom user model may have no such field
        if not users.exists():
            return False
    return True


def _authorize_failure(consumer: Any, exc: BaseException) -> SocketSignIn:
    """The close code for an exception out of ``lex_socket_authorize``.

    A lost sign-in is 4401 and an unreachable auth backend 4503, as on the HTTP side
    (``lex.api.security.is_auth_outage``); any other error denies (4403) while the
    ``FAIL_CLOSED`` security flag is on, the default, and lets the socket through otherwise.
    """
    from oauth2_authcodeflow.exceptions import OIDCSessionEnded

    from lex.api.keycloak.uma_client import KeycloakUnauthorized
    from lex.api.security import is_auth_outage
    from lex.authentication.drf_exceptions import SessionRejected
    from lex.core.security_flags import strict

    name = type(consumer).__name__
    if is_auth_outage(exc):
        if isinstance(exc, (OIDCSessionEnded, SessionRejected, KeycloakUnauthorized)):
            return SocketSignIn(CLOSE_UNAUTHENTICATED, "session_ended")
        return SocketSignIn(CLOSE_AUTH_UNAVAILABLE, "auth_unavailable")
    if strict("FAIL_CLOSED"):
        logger.exception("WebSocket %s: lex_socket_authorize raised; the socket is refused (4403).", name)
        return SocketSignIn(CLOSE_FORBIDDEN, "authorize_failed")
    logger.warning("WebSocket %s: lex_socket_authorize raised; allowed (LEX_SEC_FAIL_CLOSED is off).", name, exc_info=True)
    return SocketSignIn(None, "authorize_failed_open")


def _authorized(consumer: Any, sign_in: SocketSignIn, allowed: Any = None, exc: BaseException | None = None) -> SocketSignIn:
    """``sign_in`` after the consumer's check: as it was, or the close code of a refusal."""
    if exc is not None:
        checked = _authorize_failure(consumer, exc)
    else:
        checked = sign_in if allowed else SocketSignIn(CLOSE_FORBIDDEN, "forbidden")
    return sign_in if checked.close_code is None else checked


class _SocketAuthState:
    """What the async and sync mixins share: the mode, the one-warning rule and the log lines."""

    _lex_socket_mode: str = WS_AUTH_OFF
    _lex_socket_warned: bool = False
    _lex_socket_closed: bool = False
    _lex_socket_watch: Optional[SocketSignIn] = None
    _lex_socket_group: Optional[str] = None

    def _lex_socket_where(self) -> str:
        scope = getattr(self, "scope", None) or {}
        return str(scope.get("path") or "")

    def _lex_socket_would_close(self, code: int, reason: str) -> None:
        """``log`` mode: say once per socket what ``enforce`` would do, then serve it as before."""
        name, where = type(self).__name__, self._lex_socket_where()
        if self._lex_socket_warned:
            logger.debug("WebSocket %s on %s: would close with %s (%s) again.", name, where, code, reason)
            return
        self._lex_socket_warned = True
        logger.warning(
            "WebSocket %s on %s would be closed with %s (%s); LEX_WS_AUTH=log serves it as before.",
            name, where, code, reason,
        )

    def _lex_socket_closing(self, code: int, reason: str) -> bool:
        """``enforce`` mode: log the close once. False when the socket was closed already."""
        if self._lex_socket_closed:
            return False
        self._lex_socket_closed = True
        logger.info("WebSocket %s on %s closed with %s (%s).", type(self).__name__, self._lex_socket_where(), code, reason)
        return True

    def _lex_socket_check_failed(self, what: str) -> None:
        logger.warning(
            "WebSocket %s on %s: the sign-in check failed while %s; %s.",
            type(self).__name__, self._lex_socket_where(), what,
            "it is closed with 4503" if self._lex_socket_mode == WS_AUTH_ENFORCE else "LEX_WS_AUTH=log serves it",
            exc_info=True,
        )

    def _lex_socket_alive(self) -> bool:
        """The re-validation of :func:`sign_in_alive` for the sign-in this socket follows."""
        watch = self._lex_socket_watch
        if watch is None:
            return True
        return sign_in_alive(watch.session_id, watch.session_key, watch.user_pk)


class LexSocketAuthMixin(_SocketAuthState, OIDCSessionConsumerMixin):
    """Sign-in for an ``AsyncWebsocketConsumer`` (see the module docstring).

    Built on django-oauth2-codeflow's ``OIDCSessionConsumerMixin``: the same group per sign-in
    (``group_name(session_id)``), the same ``oidc.session.ended`` message and close codes; this
    adds the ``LEX_WS_AUTH`` rollout modes, logins of other backends, 4403/4503, the hook, and a
    re-validation that also sees expired refresh tokens, lost Django sessions and deactivated
    users.
    """

    #: Re-validate a signed-in socket this often (seconds, with 10 % jitter); 0 turns it off.
    oidc_revalidate_seconds = REVALIDATE_SECONDS

    async def lex_socket_authorize(self) -> bool:
        """Whether the signed-in user may use this socket. ``False`` closes it with 4403.

        Override for a socket only some users may open: ``self.scope["user"]`` is the user,
        ``self.scope.get("oidc_session")`` the sign-in. Every signed-in user may, by default.
        """
        return True

    async def lex_socket_connect(self) -> bool:
        """Check the socket's sign-in before ``connect()`` does anything else.

        ``True``: go on and serve the socket (signed in, or ``off`` / ``log`` mode). ``False``:
        the socket was accepted and closed with 4401, 4403 or 4503 (``enforce``); return.
        """
        mode = ws_auth_mode()
        self._lex_socket_mode = mode
        self._lex_socket_warned = False
        self._lex_socket_closed = False
        self._lex_socket_watch = None
        if mode == WS_AUTH_OFF:
            return True
        scope = getattr(self, "scope", None) or {}
        try:
            sign_in = _signed_in_without_database(scope)
            if sign_in is None:
                sign_in = await database_sync_to_async(resolve_socket_sign_in)(scope)
            if sign_in.close_code is None:
                try:
                    allowed = await self.lex_socket_authorize()
                except Exception as exc:  # classified, never swallowed silently
                    sign_in = _authorized(self, sign_in, exc=exc)
                else:
                    sign_in = _authorized(self, sign_in, allowed=allowed)
        except Exception:  # whatever the check raised -- a dropped database connection, a bug
            self._lex_socket_check_failed("checking the sign-in")
            if mode != WS_AUTH_ENFORCE:
                return True
            sign_in = SocketSignIn(CLOSE_AUTH_UNAVAILABLE, "check_failed")
        if sign_in.close_code is None and sign_in.signed_in:
            try:
                alive = await self._lex_socket_follow(sign_in)
            except Exception:  # the channel layer or the database failed while joining
                await self.lex_socket_disconnect()
                self._lex_socket_check_failed("following the sign-in")
                if mode != WS_AUTH_ENFORCE:
                    return True
                sign_in = SocketSignIn(CLOSE_AUTH_UNAVAILABLE, "check_failed")
            else:
                if not alive:
                    await self.lex_socket_disconnect()
                    sign_in = SocketSignIn(CLOSE_UNAUTHENTICATED, "session_ended")
        if sign_in.close_code is not None:
            if mode == WS_AUTH_ENFORCE:
                await self.accept()  # type: ignore[attr-defined]
                if self._lex_socket_closing(sign_in.close_code, sign_in.reason):
                    await self.close(code=sign_in.close_code)  # type: ignore[attr-defined]
                return False
            self._lex_socket_would_close(sign_in.close_code, sign_in.reason)
        return True

    async def lex_socket_disconnect(self) -> None:
        """Stop following the sign-in: cancel the re-validation, leave the sign-in's group."""
        task = getattr(self, "_lex_socket_task", None)
        if task is not None:
            self._lex_socket_task = None
            task.cancel()
        group = getattr(self, "_lex_socket_group", None)
        layer = getattr(self, "channel_layer", None)
        if group and layer is not None:
            self._lex_socket_group = None
            await layer.group_discard(group, self.channel_name)  # type: ignore[attr-defined]

    # The library's names, so code written against OIDCSessionConsumerMixin keeps working.
    async def oidc_connect(self) -> bool:
        return await self.lex_socket_connect()

    async def oidc_disconnect(self) -> None:
        await self.lex_socket_disconnect()

    async def oidc_session_ended(self, event: Mapping[str, Any]) -> None:
        """Group message ``{'type': 'oidc.session.ended'}``: the sign-in this socket follows ended."""
        await self._lex_socket_session_over("session_ended")

    async def _lex_socket_follow(self, sign_in: SocketSignIn) -> bool:
        """Join the sign-in's group, then check it once more; start the re-validation.

        The check after joining closes the gap between the middleware's read and the join: a
        sign-in that ended in between sent its "ended" message before this socket listened.
        Returns whether the sign-in is still alive.
        """
        self._lex_socket_watch = sign_in
        if sign_in.session_id:
            self._lex_socket_group = group_name(sign_in.session_id)
            layer = getattr(self, "channel_layer", None)
            if layer is not None:
                await layer.group_add(self._lex_socket_group, self.channel_name)  # type: ignore[attr-defined]
        if not await database_sync_to_async(self._lex_socket_alive)():
            return False
        if self.oidc_revalidate_seconds:
            self._lex_socket_task = asyncio.ensure_future(self._lex_socket_revalidate())
        return True

    async def _lex_socket_session_over(self, reason: str) -> None:
        if self._lex_socket_mode == WS_AUTH_ENFORCE:
            if self._lex_socket_closing(CLOSE_UNAUTHENTICATED, reason):
                await self.close(code=CLOSE_UNAUTHENTICATED)  # type: ignore[attr-defined]
        elif self._lex_socket_mode == WS_AUTH_LOG:
            self._lex_socket_would_close(CLOSE_UNAUTHENTICATED, reason)

    async def _lex_socket_revalidate(self) -> None:
        """Every ``oidc_revalidate_seconds`` (± 10 %): is the sign-in still alive (:func:`sign_in_alive`)?

        A database that does not answer keeps the socket open -- the next round asks again -- so
        an outage never closes a signed-in socket.
        """
        base = float(self.oidc_revalidate_seconds)
        try:
            while True:
                await asyncio.sleep(base * random.uniform(0.9, 1.1))
                try:
                    alive = await database_sync_to_async(self._lex_socket_alive)()
                except Exception as exc:
                    logger.warning(
                        "WebSocket %s on %s: its sign-in could not be re-validated (%s); it stays open.",
                        type(self).__name__, self._lex_socket_where(), type(exc).__name__,
                    )
                    continue
                if not alive:
                    self._lex_socket_task = None
                    await self._lex_socket_session_over("session_ended")
                    return
        except asyncio.CancelledError:
            pass


class LexSyncSocketAuthMixin(_SocketAuthState, SyncOIDCSessionConsumerMixin):
    """:class:`LexSocketAuthMixin` for a synchronous ``WebsocketConsumer``.

    Synchronous consumers run in a worker thread, so the hooks are plain methods. The periodic
    re-validation runs through the channel layer: a ticker on the event loop sends this consumer
    ``lex.socket.revalidate`` every ``oidc_revalidate_seconds`` and the check runs in the
    consumer's own thread. Without a channel layer there is no ticker: such a consumer notices
    the end of its sign-in only at connect.
    """

    #: Re-validate a signed-in socket this often (seconds); 0 turns it off.
    oidc_revalidate_seconds = REVALIDATE_SECONDS

    def lex_socket_authorize(self) -> bool:
        """Whether the signed-in user may use this socket. ``False`` closes it with 4403."""
        return True

    def lex_socket_connect(self) -> bool:
        """As :meth:`LexSocketAuthMixin.lex_socket_connect`, synchronously."""
        mode = ws_auth_mode()
        self._lex_socket_mode = mode
        self._lex_socket_warned = False
        self._lex_socket_closed = False
        self._lex_socket_watch = None
        if mode == WS_AUTH_OFF:
            return True
        try:
            sign_in = resolve_socket_sign_in(getattr(self, "scope", None) or {})
            if sign_in.close_code is None:
                try:
                    allowed = self.lex_socket_authorize()
                except Exception as exc:  # classified, never swallowed silently
                    sign_in = _authorized(self, sign_in, exc=exc)
                else:
                    sign_in = _authorized(self, sign_in, allowed=allowed)
        except Exception:  # whatever the check raised
            self._lex_socket_check_failed("checking the sign-in")
            if mode != WS_AUTH_ENFORCE:
                return True
            sign_in = SocketSignIn(CLOSE_AUTH_UNAVAILABLE, "check_failed")
        if sign_in.close_code is None and sign_in.signed_in:
            try:
                alive = self._lex_socket_follow(sign_in)
            except Exception:  # the channel layer or the database failed while joining
                self.lex_socket_disconnect()
                self._lex_socket_check_failed("following the sign-in")
                if mode != WS_AUTH_ENFORCE:
                    return True
                sign_in = SocketSignIn(CLOSE_AUTH_UNAVAILABLE, "check_failed")
            else:
                if not alive:
                    self.lex_socket_disconnect()
                    sign_in = SocketSignIn(CLOSE_UNAUTHENTICATED, "session_ended")
        if sign_in.close_code is not None:
            if mode == WS_AUTH_ENFORCE:
                self.accept()  # type: ignore[attr-defined]
                if self._lex_socket_closing(sign_in.close_code, sign_in.reason):
                    self.close(code=sign_in.close_code)  # type: ignore[attr-defined]
                return False
            self._lex_socket_would_close(sign_in.close_code, sign_in.reason)
        return True

    def lex_socket_disconnect(self) -> None:
        """Stop the re-validation ticker and leave the sign-in's group."""
        if getattr(self, "_lex_socket_ticker", None) is not None:
            async_to_sync(self._lex_socket_stop_ticker)()
        group = getattr(self, "_lex_socket_group", None)
        layer = getattr(self, "channel_layer", None)
        if group and layer is not None:
            self._lex_socket_group = None
            async_to_sync(layer.group_discard)(group, self.channel_name)  # type: ignore[attr-defined]

    def oidc_connect(self) -> bool:
        return self.lex_socket_connect()

    def oidc_disconnect(self) -> None:
        self.lex_socket_disconnect()

    def oidc_session_ended(self, event: Mapping[str, Any]) -> None:
        """Group message ``{'type': 'oidc.session.ended'}``: the sign-in this socket follows ended."""
        self._lex_socket_session_over("session_ended")

    def lex_socket_revalidate(self, event: Mapping[str, Any]) -> None:
        """The ticker's ``lex.socket.revalidate``: is the sign-in still alive (:func:`sign_in_alive`)?"""
        if self._lex_socket_watch is None or self._lex_socket_closed:
            return
        try:
            alive = self._lex_socket_alive()
        except Exception as exc:  # the database did not answer: keep the socket, ask next round
            logger.warning(
                "WebSocket %s on %s: its sign-in could not be re-validated (%s); it stays open.",
                type(self).__name__, self._lex_socket_where(), type(exc).__name__,
            )
            return
        if not alive:
            async_to_sync(self._lex_socket_stop_ticker)()
            self._lex_socket_session_over("session_ended")

    def _lex_socket_follow(self, sign_in: SocketSignIn) -> bool:
        """Join the sign-in's group, check it once more, start the ticker; whether it is alive."""
        self._lex_socket_watch = sign_in
        layer = getattr(self, "channel_layer", None)
        if sign_in.session_id and layer is not None:
            self._lex_socket_group = group_name(sign_in.session_id)
            async_to_sync(layer.group_add)(self._lex_socket_group, self.channel_name)  # type: ignore[attr-defined]
        if not self._lex_socket_alive():
            return False
        if layer is not None and self.oidc_revalidate_seconds:
            async_to_sync(self._lex_socket_start_ticker)()
        return True

    def _lex_socket_session_over(self, reason: str) -> None:
        if self._lex_socket_mode == WS_AUTH_ENFORCE:
            if self._lex_socket_closing(CLOSE_UNAUTHENTICATED, reason):
                self.close(code=CLOSE_UNAUTHENTICATED)  # type: ignore[attr-defined]
        elif self._lex_socket_mode == WS_AUTH_LOG:
            self._lex_socket_would_close(CLOSE_UNAUTHENTICATED, reason)

    async def _lex_socket_start_ticker(self) -> None:
        self._lex_socket_ticker = asyncio.ensure_future(self._lex_socket_tick())

    async def _lex_socket_stop_ticker(self) -> None:
        ticker = getattr(self, "_lex_socket_ticker", None)
        self._lex_socket_ticker = None
        if ticker is not None:
            ticker.cancel()

    async def _lex_socket_tick(self) -> None:
        """Send this consumer ``lex.socket.revalidate`` every ``oidc_revalidate_seconds`` (± 10 %)."""
        base = float(self.oidc_revalidate_seconds)
        try:
            while True:
                await asyncio.sleep(base * random.uniform(0.9, 1.1))
                await self.channel_layer.send(self.channel_name, {"type": REVALIDATE_MESSAGE})  # type: ignore[attr-defined]
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # the channel layer is gone: nothing left to re-validate through
            logger.warning(
                "WebSocket %s on %s: re-validation stopped (%s).",
                type(self).__name__, self._lex_socket_where(), type(exc).__name__,
            )


__all__ = [
    "CLOSE_AUTH_UNAVAILABLE",
    "CLOSE_FORBIDDEN",
    "CLOSE_UNAUTHENTICATED",
    "DEFAULT_WS_AUTH_MODE",
    "LexSocketAuthMixin",
    "LexSyncSocketAuthMixin",
    "REVALIDATE_MESSAGE",
    "REVALIDATE_SECONDS",
    "SocketSignIn",
    "WS_AUTH_ENFORCE",
    "WS_AUTH_LOG",
    "WS_AUTH_MODES",
    "WS_AUTH_OFF",
    "resolve_socket_sign_in",
    "sign_in_active",
    "sign_in_alive",
    "ws_auth_mode",
]
