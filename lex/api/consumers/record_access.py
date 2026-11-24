"""Which calculations a WebSocket's user may hear about (``LEX_SEC_INTERNAL``).

The live calculation channels carry data of the record a run belongs to:

* ``ws/calculation_logs/<record>-<run>``: the run's log lines;
* ``ws/calculation_status_update``: every run's state, with its error message and traceback,
  and on connect the running calculations (display name, model, primary key, run id);
* ``ws/calculations``: the record and run id of each run that starts.

They follow the rule of ``api/init-calculation-logs`` and ``api/active-calculations``: a socket
hears about the runs of records its user may read (``permission_read``, judged by
:func:`lex.api.security.calculation_record_readable`), a superuser about every run, and
``LEX_SEC_INTERNAL=false`` restores the open channels. A socket without a sign-in reads no
record, whatever ``LEX_WS_AUTH`` lets connect, as the REST endpoints answer it 401. A refused
socket stays open and simply hears nothing, like a quiet run: it learns nothing, and no client
breaks.

A socket is judged like an API call of the same user: the same user, the same Keycloak client
roles and the same UMA permissions (see ``lex.api.middleware.keycloak_permissions``). Socket code
never refreshes a token (:mod:`lex.api.consumers.auth`), because a socket that refreshed would keep
an idle sign-in alive. So the permissions are asked with the sign-in's access token while it is
valid, which the browser's own requests keep it, and a socket whose token has run out keeps the
permissions it last had until its sign-in ends.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

#: How long a socket trusts its user's sign-in, Keycloak permissions and verdicts before reading
#: them again (the same bound as lex's UMA cache).
REREAD_SECONDS = 60.0
#: Verdicts kept per socket between re-reads; a busy cluster calculation reports many records.
MAX_VERDICTS = 1000


class SocketReader:
    """The permission layer's view of one socket's user.

    Its methods read the database: call them through ``database_sync_to_async``.
    """

    def __init__(self, scope: Mapping[str, Any]) -> None:
        self._scope = scope
        self._read_at: Optional[float] = None
        self._request = None
        self._permissions: Optional[list] = None
        self._verdicts: dict = {}

    # -- what the consumers ask -----------------------------------------------------------
    def may_read(self, calculation_record: Optional[str]) -> bool:
        """Whether the socket may hear about the run of ``calculation_record`` (``<model>_<pk>``)."""
        from lex.api.security import calculation_record_readable, sees_every_calculation

        request = self._current_request()
        if sees_every_calculation(request):
            return True
        if not request.user.is_authenticated:
            return False
        key = str(calculation_record or "")
        if key not in self._verdicts:
            if len(self._verdicts) >= MAX_VERDICTS:
                self._verdicts.clear()
            self._verdicts[key] = calculation_record_readable(request, key)
        return self._verdicts[key]

    def signed_in(self) -> bool:
        """Whether the socket may hear what names no record (a project's notification)."""
        from lex.api.security import sees_every_calculation

        request = self._current_request()
        return sees_every_calculation(request) or bool(request.user.is_authenticated)

    def visible(self, entries: Iterable[Mapping[str, Any]]) -> list:
        """The active-calculation entries whose record the socket may read."""
        return [entry for entry in entries if self.may_read(entry.get("record_id"))]

    # -- the request standing for the socket ----------------------------------------------
    def _current_request(self):
        now = time.monotonic()
        if self._read_at is None or now - self._read_at >= REREAD_SECONDS:
            self._request = self._build_request()
            self._verdicts = {}
            self._read_at = now
        return self._request

    def _build_request(self):
        """A Django request carrying the socket's user and sign-in, as the middleware would.

        ``request.oidc`` stays ``None``: code that reaches the token through it refreshes an
        expired one. The roles and permissions it would yield are set here instead.
        """
        from django.contrib.auth.models import AnonymousUser
        from django.http import HttpRequest

        from lex.authentication import identity

        request = HttpRequest()
        request.method = "GET"
        user = self._scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            user = AnonymousUser()
        context, row = self._sign_in(user)
        request.user = user
        request.oidc = None
        request.client_roles = list(context.client_roles()) if context is not None else []
        request.userinfo = identity.profile_claims(context) if context is not None else {}
        request.user_permissions = self._uma_permissions(row) if row is not None else []
        return request

    def _sign_in(self, user):
        """The socket's live Keycloak sign-in (``OIDCContext``, row), or ``(None, None)``."""
        from oauth2_authcodeflow.context import OIDCContext
        from oauth2_authcodeflow.models import OIDCSession

        info = self._scope.get("oidc_session") or {}
        session_id = info.get("session_id") if isinstance(info, Mapping) else None
        if not session_id or not user.is_authenticated:
            return None, None
        row = OIDCSession.objects.filter(pk=session_id, ended_at__isnull=True).first()
        if row is None or row.user_id != user.pk:
            return None, None
        return OIDCContext.from_row(row), row

    def _uma_permissions(self, row) -> list:
        """The sign-in's UMA permissions, asked with its access token while that is valid.

        An expired token, or Keycloak not answering, keeps what the socket last had; a token
        Keycloak no longer accepts grants nothing.
        """
        from django.utils import timezone

        from lex.api.middleware.keycloak_permissions import uma_permissions
        from lex.api.security import is_auth_outage
        from lex.authentication.drf_exceptions import SessionRejected

        expires_at = row.access_expires_at
        if expires_at is None or expires_at <= timezone.now():
            return list(self._permissions or [])
        try:
            permissions = uma_permissions(row.access_token, expires_at=expires_at)
        except SessionRejected:
            permissions = []
        except Exception as exc:
            if not is_auth_outage(exc):
                logger.warning("A socket's Keycloak permissions could not be read", exc_info=True)
            return list(self._permissions or [])
        self._permissions = list(permissions)
        return list(permissions)


__all__ = ["MAX_VERDICTS", "REREAD_SECONDS", "SocketReader"]
