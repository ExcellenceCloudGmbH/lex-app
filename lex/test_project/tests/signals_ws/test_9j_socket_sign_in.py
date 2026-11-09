"""A WebSocket serves only a live sign-in, and says why it closes (HTTP auth contract v2).

Intent: lex's live channels -- calculation ids and notifications, calculation states, a
calculation's log lines, the LexLogger stream, record mutations -- carry customer data, so a
socket must belong to someone signed in, exactly like an API request. Before 2.5 every consumer
accepted anonymous sockets, and a server that closed a socket before ``accept()`` reached the
browser as 1006, which a client cannot tell from a network blip. The contract (plan section 3.4,
``lex/contracts/auth/v2/README.md``) says: accept first, then close with **4401** (not signed in,
or the sign-in ended: re-authenticate), **4403** (forbidden: stop) or **4503** (auth unavailable:
back off). A sign-in that ends -- back-channel logout, an admin ending it -- closes its sockets at
once, also when it ends while the socket joins its group; a re-validation every 5 minutes (the
synchronous consumers' too) catches what the message missed: an ended sign-in, an expired refresh
token, a Django session that is gone, a deactivated user. Socket code never refreshes tokens. A
check that cannot be made (the database connection dropped) is 4503 under ``enforce`` and never
breaks a socket under ``log``. ``ws/health`` stays public. The rollout flag ``LEX_WS_AUTH`` is
``off`` (as before), ``log`` (the default: serve every socket, log once per socket what
``enforce`` would close) or ``enforce``; ``api/auth/session`` advertises ``ws_close_codes`` only
under ``enforce``. A regression either leaks live data to anonymous sockets or sends clients into
a reconnect loop.

Every socket here goes through ``lex.lex_app.asgi.application`` -- origin check, session and
user, ``OIDCSessionASGIMiddleware``, the routes -- or, for a project's own consumer, through the
same stack around it.

Cluster 9j -- scenarios 9.53-9.73. Type: I. 9.68-9.73 come from the security review of
2026-11-06.
Covers: lex/api/consumers/auth.py (new), lex/api/consumers/CalculationsConsumer.py,
        lex/api/consumers/CalculationLogConsumer.py, lex/api/consumers/LogConsumer.py,
        lex/api/consumers/UpdateCalculationStatusConsumer.py,
        lex/api/consumers/ModelDataUpdateConsumer.py, lex/lex_app/asgi.py,
        lex/lex_app/routing.py, lex/lex_app/settings.py (LEX_WS_AUTH),
        lex/authentication/capabilities.py (ws_close_codes).
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9j_socket_sign_in.py -v
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer, WebsocketConsumer
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError, connection
from django.test import Client, TransactionTestCase, override_settings
from django.urls import path
from django.utils import timezone
from oauth2_authcodeflow import sessions as oidc_sessions
from oauth2_authcodeflow.contrib.channels import OIDCSessionASGIMiddleware, group_name
from oauth2_authcodeflow.models import OIDCSession
from oauth2_authcodeflow.testing import FakeOP

import lex
from lex.api.consumers import auth as socket_auth
from lex.api.consumers.CalculationLogConsumer import CalculationLogConsumer
from lex.api.consumers.CalculationsConsumer import CalculationsConsumer
from lex.api.consumers.LogConsumer import LogConsumer
from lex.api.consumers.ModelDataUpdateConsumer import ModelDataUpdateConsumer
from lex.api.consumers.UpdateCalculationStatusConsumer import UpdateCalculationStatusConsumer
from lex.api.keycloak.uma_client import KeycloakUnavailable
from lex.lex_app import asgi, routing
from lex.lex_app.tests.auth import sign_in

pytestmark = pytest.mark.signals_ws

try:  # importing the ASGI module registers the server's shutdown hook; tests are no server
    atexit.unregister(asgi.on_server_shutdown)
except ValueError:
    pass

#: Every authenticated route, a socket path on it, and the group its broadcasts arrive on.
AUTHENTICATED_ROUTES = (
    ("/ws/logs", "log_group"),
    ("/ws/calculations", "calculations"),
    ("/ws/calculation_logs/rec42-calc-1", "rec42"),
    ("/ws/calculation_status_update", "update_calculation_status"),
    ("/ws/model_data_update", "model_data_update"),
)

#: The five consumers behind those routes (plan section 6.5).
AUTHENTICATED_CONSUMERS = (
    LogConsumer, CalculationsConsumer, CalculationLogConsumer, UpdateCalculationStatusConsumer, ModelDataUpdateConsumer,
)

#: A broadcast each route forwards to its sockets (the consumer's handler for the type).
BROADCASTS = {
    "/ws/logs": {"type": "log_message", "level": "INFO", "message": "hello", "timestamp": "t"},
    "/ws/calculations": {"type": "calculation_notification", "payload": {"message": "hello"}},
    "/ws/calculation_logs/rec42-calc-1": {"type": "calculation_log_real_time", "payload": ["line"]},
    "/ws/calculation_status_update": {"type": "calculation_in_progress", "payload": {"record_id": ""}},
    "/ws/model_data_update": {"type": "record_mutation", "payload": {"model_name": "fund"}},
}

LOGGER = "lex.api.consumers.auth"


def _scope(socket_path: str, session_key: str | None = None) -> dict:
    """The ASGI scope of a browser's WebSocket handshake (same-origin, cookies when signed in)."""
    headers = [(b"host", b"localhost"), (b"origin", b"http://localhost")]
    if session_key:
        headers.append((b"cookie", f"{settings.SESSION_COOKIE_NAME}={session_key}".encode()))
    return {
        "type": "websocket", "path": socket_path, "raw_path": socket_path.encode(),
        "query_string": b"", "headers": headers, "subprotocols": [],
        "client": ["127.0.0.1", 50000], "server": ["localhost", 80],
    }


class _Socket:
    """A browser's WebSocket, on asgiref's communicator (``channels.testing`` needs daphne)."""

    def __init__(self, socket_path: str, session_key: str | None = None, app=None) -> None:
        self.path = socket_path
        self.communicator = ApplicationCommunicator(app or asgi.application, _scope(socket_path, session_key))

    async def open(self) -> dict:
        """Send the handshake; the server's first answer (``websocket.accept`` or a close)."""
        await self.communicator.send_input({"type": "websocket.connect"})
        return await self.communicator.receive_output(5)

    async def next(self, timeout: float = 5) -> dict:
        return await self.communicator.receive_output(timeout)

    async def quiet(self, timeout: float = 0.4) -> bool:
        """Whether the server sends nothing for ``timeout`` seconds."""
        return await self.communicator.receive_nothing(timeout)

    async def hang_up(self) -> None:
        """The browser goes away; the consumer's ``disconnect`` runs to the end."""
        await self.communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await self.communicator.wait(5)


async def _broadcast(group: str, message: dict) -> None:
    await get_channel_layer().group_send(group, message)


def _project_app(*consumers) -> object:
    """A project's own consumers, mounted on the same stack as lex's authenticated routes."""
    routes = [path(f"ws/project/{index}", consumer.as_asgi()) for index, consumer in enumerate(consumers)]
    return AuthMiddlewareStack(OIDCSessionASGIMiddleware(URLRouter(routes)))


class _Guarded(socket_auth.LexSocketAuthMixin, AsyncWebsocketConsumer):
    """A project consumer only some users may open: ``lex_socket_authorize`` decides."""

    verdict: object = True

    async def lex_socket_authorize(self) -> bool:
        verdict = type(self).verdict
        if isinstance(verdict, BaseException):
            raise verdict
        return bool(verdict)

    async def connect(self):
        if not await self.lex_socket_connect():
            return
        await self.accept()

    async def disconnect(self, code):
        await self.lex_socket_disconnect()


class _SyncTicker(socket_auth.LexSyncSocketAuthMixin, WebsocketConsumer):
    """A project's synchronous consumer."""

    def connect(self):
        if not self.lex_socket_connect():
            return
        self.accept()

    def disconnect(self, code):
        self.lex_socket_disconnect()


class _SocketTestCase(TransactionTestCase):
    """Real sessions and sign-in rows, committed, so the socket stack sees them."""

    def setUp(self) -> None:
        super().setUp()
        _Guarded.verdict = True
        self.addCleanup(async_to_sync(get_channel_layer().flush))

    def _signed_in(self, username: str = "ada", **kwargs) -> tuple[Client, OIDCSession]:
        """A browser signed in through Keycloak (an OIDC sign-in row); its client and the row."""
        user = get_user_model().objects.create_user(username=username, email=f"{username}@example.test")
        client = Client()
        row = sign_in(self, client, user, **kwargs)
        return client, row

    def _mode(self, mode: str) -> None:
        """Run the rest of this test under ``LEX_WS_AUTH=mode``.

        Enabled through ``addCleanup`` before any sign-in, so it is undone after the sign-in
        helper's own settings (a method-level ``@override_settings`` would be undone first and
        leave the helper restoring a snapshot that still holds this mode).
        """
        overrides = override_settings(LEX_WS_AUTH=mode)
        overrides.enable()
        self.addCleanup(overrides.disable)

    @staticmethod
    def _key(client: Client) -> str:
        return client.cookies[settings.SESSION_COOKIE_NAME].value

    def _run(self, scenario) -> None:
        async_to_sync(scenario)()


@override_settings(LEX_WS_AUTH="enforce")
class TestCluster09j_EnforcedSockets(_SocketTestCase):
    """9.53-9.59: under ``enforce`` a socket is a live sign-in's, or it closes with the contract's code."""

    # -- 9.53 ----------------------------------------------------------
    def test_9_53_anonymous_sockets_are_accepted_then_closed_4401_on_every_authenticated_route(self) -> None:
        """
        Scenario 9.53: an anonymous socket is accepted, then closed with 4401, on every route.
        Given: LEX_WS_AUTH=enforce and a browser with no session.
        When: it opens each of the five authenticated sockets and a broadcast is sent to the
              route's group.
        Then: each is accepted first (a close during the handshake reaches the browser as 1006),
              then closed with 4401; nothing else reaches it -- no broadcast, and no snapshot of
              the active calculations on the calculation-status socket.
        """

        for consumer in AUTHENTICATED_CONSUMERS:
            self.assertTrue(issubclass(consumer, socket_auth.LexSocketAuthMixin),
                            f"plan 6.5 puts the sign-in on {consumer.__name__}")

        async def scenario():
            for socket_path, group in AUTHENTICATED_ROUTES:
                with self.subTest(route=socket_path):
                    sock = _Socket(socket_path)
                    first = await sock.open()
                    self.assertEqual(first["type"], "websocket.accept", f"{socket_path}: accept before close, got {first}")
                    closed = await sock.next()
                    self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4401),
                                     f"{socket_path}: an anonymous socket closes with 4401")
                    await _broadcast(group, BROADCASTS[socket_path])
                    self.assertTrue(await sock.quiet(), f"{socket_path}: nothing reaches a refused socket")
                    await sock.hang_up()

        self._run(scenario)

    # -- 9.54 ----------------------------------------------------------
    def test_9_54_a_signed_in_socket_is_served_on_every_route_and_follows_its_sign_in(self) -> None:
        """
        Scenario 9.54: a signed-in browser's sockets are served and follow its sign-in.
        Given: LEX_WS_AUTH=enforce and a browser signed in through Keycloak.
        When: it opens each authenticated socket; a broadcast goes to the route's group, then the
              library's "sign-in ended" message to the sign-in's group ``oidc-session.<hash>``.
        Then: each socket is accepted and stays open, receives the broadcast, and closes with
              4401 on the sign-in's message -- it joined the sign-in's group.
        """
        client, row = self._signed_in()

        async def scenario():
            for socket_path, group in AUTHENTICATED_ROUTES:
                with self.subTest(route=socket_path):
                    sock = _Socket(socket_path, self._key(client))
                    self.assertEqual((await sock.open())["type"], "websocket.accept")
                    if socket_path == "/ws/calculation_status_update":
                        snapshot = json.loads((await sock.next())["text"])
                        self.assertEqual(snapshot["type"], "calculation_reconciliation",
                                         "a signed-in status socket gets the active calculations")
                    self.assertTrue(await sock.quiet(), f"{socket_path}: a signed-in socket stays open")
                    await _broadcast(group, BROADCASTS[socket_path])
                    sent = await sock.next()
                    self.assertEqual(sent["type"], "websocket.send", f"{socket_path}: the broadcast arrives, got {sent}")
                    await _broadcast(group_name(row.pk), {"type": "oidc.session.ended"})
                    closed = await sock.next()
                    self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4401),
                                     f"{socket_path}: the sign-in's end closes it with 4401")
                    await sock.hang_up()

        self._run(scenario)

    # -- 9.55 ----------------------------------------------------------
    def test_9_55_a_back_channel_logout_closes_the_users_sockets_with_4401_and_no_one_elses(self) -> None:
        """
        Scenario 9.55: Keycloak ends a sign-in; its sockets close at once.
        Given: LEX_WS_AUTH=enforce; Ada and Bob signed in, each with a calculations socket; the
               OpenID provider (FakeOP) knows the client.
        When: the provider posts a back-channel logout for Ada's Keycloak session to
              ``/oidc/backchannel-logout``.
        Then: Ada's socket closes with 4401 (plan E6: "sockets close with 4401"); Bob's stays open.
        """
        op = FakeOP()
        overrides = override_settings(**op.django_settings())
        overrides.enable()
        self.addCleanup(overrides.disable)
        op.install()
        self.addCleanup(op.uninstall)
        ada, ada_row = self._signed_in("ada")
        bob, _ = self._signed_in("bob")
        logout_token = op.logout_token(sid=ada_row.sid)

        async def scenario():
            ada_sock = _Socket("/ws/calculations", self._key(ada))
            bob_sock = _Socket("/ws/calculations", self._key(bob))
            self.assertEqual((await ada_sock.open())["type"], "websocket.accept")
            self.assertEqual((await bob_sock.open())["type"], "websocket.accept")
            response = await sync_to_async(Client().post)("/oidc/backchannel-logout", {"logout_token": logout_token})
            self.assertEqual(response.status_code, 200, "the provider's logout is accepted")
            closed = await ada_sock.next()
            self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4401))
            self.assertTrue(await bob_sock.quiet(), "another user's socket is untouched")
            await ada_sock.hang_up()
            await bob_sock.hang_up()

        self._run(scenario)

    # -- 9.56 ----------------------------------------------------------
    def test_9_56_revalidation_closes_a_socket_whose_sign_in_ended_unannounced(self) -> None:
        """
        Scenario 9.56: the periodic re-validation catches an end the message missed.
        Given: LEX_WS_AUTH=enforce, a signed-in calculations socket, re-validation every 5 minutes
               by default (shortened here to 0.2 s).
        When: its sign-in row is ended in the database without the group message.
        Then: the default interval is 300 s; within the shortened interval the socket closes with
              4401.
        """
        self.assertEqual(CalculationsConsumer.oidc_revalidate_seconds, 300, "plan: re-validate every 5 minutes")
        client, row = self._signed_in()

        async def scenario():
            with mock.patch.object(CalculationsConsumer, "oidc_revalidate_seconds", 0.2):
                sock = _Socket("/ws/calculations", self._key(client))
                self.assertEqual((await sock.open())["type"], "websocket.accept")
                await database_sync_to_async(OIDCSession.objects.filter(pk=row.pk).update)(
                    ended_at=timezone.now(), end_reason="admin")
                closed = await sock.next(timeout=3)
                self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4401))
                await sock.hang_up()

        self._run(scenario)

    # -- 9.57 ----------------------------------------------------------
    def test_9_57_a_socket_never_refreshes_tokens(self) -> None:
        """
        Scenario 9.57: socket code never refreshes tokens.
        Given: LEX_WS_AUTH=enforce, the OpenID provider installed, a sign-in whose access token
               has already expired (the sign-in itself is alive).
        When: a calculations socket opens and is re-validated several times.
        Then: it is served and stays open, and the provider's token endpoint is never called --
              a socket lives as long as the sign-in, not the access token.
        """
        op = FakeOP()
        overrides = override_settings(**op.django_settings())
        overrides.enable()
        self.addCleanup(overrides.disable)
        op.install()
        self.addCleanup(op.uninstall)
        client, row = self._signed_in(access_expires_in=0)
        self.assertLessEqual(row.access_expires_at, timezone.now(), "precondition: the access token has expired")

        async def scenario():
            with mock.patch.object(CalculationsConsumer, "oidc_revalidate_seconds", 0.1):
                sock = _Socket("/ws/calculations", self._key(client))
                self.assertEqual((await sock.open())["type"], "websocket.accept")
                self.assertTrue(await sock.quiet(0.8), "served and kept open across re-validations")
                await sock.hang_up()

        self._run(scenario)
        self.assertEqual(op.token_calls, [], "no socket code asked the token endpoint for anything")

    # -- 9.58 ----------------------------------------------------------
    def test_9_58_a_login_of_another_backend_is_served_an_ended_oidc_login_is_not(self) -> None:
        """
        Scenario 9.58: who counts as signed in is what the HTTP side counts.
        Given: LEX_WS_AUTH=enforce; a user signed in with Django's own backend (createsuperuser +
               admin, a local run without Keycloak), and a Keycloak user whose sign-in row ended
               while the Django session survived.
        When: each opens a calculations socket.
        Then: the Django login is served (as the API serves it); the ended sign-in is accepted
              and closed with 4401.
        """
        admin = get_user_model().objects.create_user(username="admin", email="admin@example.test")
        local = Client()
        local.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
        ended, row = self._signed_in("ada")
        OIDCSession.objects.filter(pk=row.pk).update(ended_at=timezone.now(), end_reason="admin")

        async def scenario():
            served = _Socket("/ws/calculations", self._key(local))
            self.assertEqual((await served.open())["type"], "websocket.accept")
            self.assertTrue(await served.quiet(), "a Django login is a signed-in user")
            await served.hang_up()

            refused = _Socket("/ws/calculations", self._key(ended))
            self.assertEqual((await refused.open())["type"], "websocket.accept")
            closed = await refused.next()
            self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4401),
                             "an ended sign-in is not signed in, whatever the Django session says")
            await refused.hang_up()

        self._run(scenario)

    # -- 9.59 ----------------------------------------------------------
    def test_9_59_the_health_socket_stays_public(self) -> None:
        """
        Scenario 9.59: ``ws/health`` stays public.
        Given: LEX_WS_AUTH=enforce and a browser with no session.
        When: it opens ``/ws/health`` and sends a probe.
        Then: the socket is accepted and answers ``{"status": "Healthy :)"}`` -- the frontend
              probes the backend while signed out.
        """

        async def scenario():
            sock = _Socket("/ws/health")
            self.assertEqual((await sock.open())["type"], "websocket.accept")
            await sock.communicator.send_input({"type": "websocket.receive", "text": "ping"})
            answer = await sock.next()
            self.assertEqual(json.loads(answer["text"]), {"status": "Healthy :)"})
            await sock.hang_up()

        self._run(scenario)


@override_settings(LEX_WS_AUTH="enforce")
class TestCluster09j_ForbiddenAndUnavailable(_SocketTestCase):
    """9.60-9.61: 4403 when the consumer refuses the user, 4503 when the sign-in cannot be checked."""

    # -- 9.60 ----------------------------------------------------------
    def test_9_60_a_consumer_that_refuses_the_user_closes_with_4403(self) -> None:
        """
        Scenario 9.60: forbidden is 4403, not a sign-in problem.
        Given: LEX_WS_AUTH=enforce; a project consumer whose ``lex_socket_authorize`` refuses;
               a signed-in browser.
        When: the browser opens it; then the same with a check that allows.
        Then: the refused socket is accepted and closed with 4403 (the client stops, it does not
              sign in again); the allowed one is served.
        """
        client, _ = self._signed_in()
        app = _project_app(_Guarded)

        async def scenario():
            _Guarded.verdict = False
            refused = _Socket("/ws/project/0", self._key(client), app)
            self.assertEqual((await refused.open())["type"], "websocket.accept")
            closed = await refused.next()
            self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4403))
            await refused.hang_up()

            _Guarded.verdict = True
            allowed = _Socket("/ws/project/0", self._key(client), app)
            self.assertEqual((await allowed.open())["type"], "websocket.accept")
            self.assertTrue(await allowed.quiet(), "an allowed user is served")
            await allowed.hang_up()

        self._run(scenario)

    # -- 9.61 ----------------------------------------------------------
    def test_9_61_a_sign_in_that_cannot_be_checked_closes_with_4503(self) -> None:
        """
        Scenario 9.61: auth unavailable is 4503, never 4401.
        Given: LEX_WS_AUTH=enforce and a signed-in browser.
        When: (a) the database does not answer for sign-in rows while the socket opens;
              (b) a project consumer's check cannot reach Keycloak.
        Then: both sockets are accepted and closed with 4503 -- the client backs off and retries
              instead of throwing away a sign-in that may be fine.
        """
        client, _ = self._signed_in()
        table = OIDCSession._meta.db_table

        def database_down_for_sign_ins(execute, sql, params, many, context):
            if table in sql:
                raise OperationalError("server closed the connection unexpectedly")
            return execute(sql, params, many, context)

        async def unreadable():
            sock = _Socket("/ws/calculations", self._key(client))
            self.assertEqual((await sock.open())["type"], "websocket.accept")
            closed = await sock.next()
            self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4503))
            await sock.hang_up()

        with connection.execute_wrapper(database_down_for_sign_ins):
            self._run(unreadable)

        async def keycloak_down():
            _Guarded.verdict = KeycloakUnavailable("Keycloak did not answer", status=None)
            sock = _Socket("/ws/project/0", self._key(client), _project_app(_Guarded))
            self.assertEqual((await sock.open())["type"], "websocket.accept")
            closed = await sock.next()
            self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4503))
            await sock.hang_up()

        self._run(keycloak_down)


class TestCluster09j_RolloutModes(_SocketTestCase):
    """9.62-9.66: ``log`` serves and tells, ``off`` serves and is silent; the capability and the flag."""

    # -- 9.62 ----------------------------------------------------------
    def test_9_62_log_serves_an_anonymous_socket_as_before_with_one_warning(self) -> None:
        """
        Scenario 9.62: ``log`` breaks nobody and says what ``enforce`` would close.
        Given: LEX_WS_AUTH=log and a browser (or a non-browser client) with no session.
        When: it opens the calculations socket and two broadcasts arrive.
        Then: it is served exactly as before -- accepted, not closed, both broadcasts delivered --
              and exactly one WARNING names the consumer, the path, 4401 and the reason.
        """
        self._mode("log")

        async def scenario():
            sock = _Socket("/ws/calculations")
            self.assertEqual((await sock.open())["type"], "websocket.accept")
            for _ in range(2):
                await _broadcast("calculations", BROADCASTS["/ws/calculations"])
                self.assertEqual((await sock.next())["type"], "websocket.send", "served as before")
            self.assertTrue(await sock.quiet(), "and never closed")
            await sock.hang_up()

        with self.assertLogs(LOGGER, logging.WARNING) as logged:
            self._run(scenario)
        warnings = [record for record in logged.records if record.levelno >= logging.WARNING]
        self.assertEqual(len(warnings), 1, f"one warning per socket, got {logged.output}")
        line = warnings[0].getMessage()
        for part in ("CalculationsConsumer", "/ws/calculations", "4401", "anonymous", "LEX_WS_AUTH=log"):
            self.assertIn(part, line)

    # -- 9.63 ----------------------------------------------------------
    def test_9_63_log_keeps_a_socket_whose_sign_in_ended_open_and_says_so_once(self) -> None:
        """
        Scenario 9.63: under ``log`` an ended sign-in does not close its socket either.
        Given: LEX_WS_AUTH=log and a signed-in calculations socket.
        When: the sign-in's end is announced twice.
        Then: the socket stays open and keeps receiving broadcasts; one WARNING (4401,
              session_ended) is logged for it, not one per announcement.
        """
        self._mode("log")
        client, row = self._signed_in()

        async def scenario():
            sock = _Socket("/ws/calculations", self._key(client))
            self.assertEqual((await sock.open())["type"], "websocket.accept")
            for _ in range(2):
                await _broadcast(group_name(row.pk), {"type": "oidc.session.ended"})
            await _broadcast("calculations", BROADCASTS["/ws/calculations"])
            self.assertEqual((await sock.next())["type"], "websocket.send", "still served")
            await sock.hang_up()

        with self.assertLogs(LOGGER, logging.WARNING) as logged:
            self._run(scenario)
        warnings = [record.getMessage() for record in logged.records if record.levelno >= logging.WARNING]
        self.assertEqual(len(warnings), 1, f"one warning per socket, got {warnings}")
        self.assertIn("4401", warnings[0])
        self.assertIn("session_ended", warnings[0])

    # -- 9.64 ----------------------------------------------------------
    def test_9_64_off_serves_every_socket_as_before_2_5_and_logs_nothing(self) -> None:
        """
        Scenario 9.64: ``off`` is the old behaviour, exactly.
        Given: LEX_WS_AUTH=off; an anonymous browser and a signed-in one.
        When: both open the calculations socket and the signed-in one's sign-in ends.
        Then: both are served and stay open, and the module logs nothing at WARNING.
        """
        self._mode("off")
        client, row = self._signed_in()

        async def scenario():
            anonymous = _Socket("/ws/calculations")
            signed_in = _Socket("/ws/calculations", self._key(client))
            self.assertEqual((await anonymous.open())["type"], "websocket.accept")
            self.assertEqual((await signed_in.open())["type"], "websocket.accept")
            await _broadcast(group_name(row.pk), {"type": "oidc.session.ended"})
            self.assertTrue(await anonymous.quiet())
            self.assertTrue(await signed_in.quiet(), "not closed: off does not follow sign-ins")
            await anonymous.hang_up()
            await signed_in.hang_up()

        with mock.patch.object(logging.getLogger(LOGGER), "warning") as warning:
            self._run(scenario)
        warning.assert_not_called()

    # -- 9.65 ----------------------------------------------------------
    def test_9_65_the_session_endpoint_advertises_close_codes_only_under_enforce(self) -> None:
        """
        Scenario 9.65: ``ws_close_codes`` tells clients the truth.
        Given: a signed-in browser.
        When: it asks ``GET api/auth/session`` under each LEX_WS_AUTH mode.
        Then: ``capabilities.ws_close_codes`` is true under ``enforce`` and false under ``log``
              and ``off`` -- a client that relies on 4401 must only do so when sockets send it.
        """
        client, _ = self._signed_in()
        for mode, expected in (("enforce", True), ("log", False), ("off", False)):
            with self.subTest(mode=mode), override_settings(LEX_WS_AUTH=mode):
                response = client.get("/api/auth/session")
                self.assertEqual(response.status_code, 200)
                self.assertIs(response.json()["capabilities"]["ws_close_codes"], expected)

    # -- 9.66 ----------------------------------------------------------
    def test_9_66_the_flag_defaults_to_log_and_refuses_unknown_values(self) -> None:
        """
        Scenario 9.66: ``LEX_WS_AUTH`` defaults to ``log``; a typo stops the server.
        Given: lex's settings imported in a fresh interpreter.
        When: LEX_WS_AUTH is unset, empty, ``ENFORCE``, ``off``, or ``enforced``.
        Then: unset and empty give ``log`` (no client breaks on upgrade, and the evidence for
              ``enforce`` is collected from the start); values are case-insensitive; an unknown
              value raises ImproperlyConfigured naming the valid ones -- a typo must not silently
              leave sockets unenforced.
        """
        cases = {None: "log", "": "log", "ENFORCE": "enforce", "off": "off"}
        for raw, expected in cases.items():
            with self.subTest(LEX_WS_AUTH=raw):
                self.assertEqual(_ws_auth_in_fresh_interpreter(raw), {"mode": expected})
        refused = _ws_auth_in_fresh_interpreter("enforced")
        self.assertEqual(refused.get("error"), "ImproperlyConfigured", refused)
        self.assertIn("off, log, enforce", refused["message"])


class TestCluster09j_SyncConsumers(_SocketTestCase):
    """9.67: a project's synchronous consumer gets the same contract."""

    # -- 9.67 ----------------------------------------------------------
    def test_9_67_a_synchronous_consumer_closes_with_the_same_codes(self) -> None:
        """
        Scenario 9.67: ``LexSyncSocketAuthMixin`` for a ``WebsocketConsumer``.
        Given: LEX_WS_AUTH=enforce and a project's synchronous consumer on the same stack.
        When: an anonymous browser opens it; a signed-in browser opens it and its sign-in ends.
        Then: the anonymous socket is accepted and closed with 4401; the signed-in one is served
              and closed with 4401 when the sign-in's end is announced.
        """
        self._mode("enforce")
        client, row = self._signed_in()
        app = _project_app(_SyncTicker)

        async def scenario():
            anonymous = _Socket("/ws/project/0", None, app)
            self.assertEqual((await anonymous.open())["type"], "websocket.accept")
            closed = await anonymous.next()
            self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4401))
            await anonymous.hang_up()

            signed_in = _Socket("/ws/project/0", self._key(client), app)
            self.assertEqual((await signed_in.open())["type"], "websocket.accept")
            self.assertTrue(await signed_in.quiet(), "served")
            await sync_to_async(oidc_sessions.end_session)(row, "admin")
            closed = await signed_in.next()
            self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4401))
            await signed_in.hang_up()

        self._run(scenario)


#: The checkout holding the ``lex`` package, so a fresh interpreter imports this lex.
_CHECKOUT = Path(lex.__file__).resolve().parents[1]

_FLAG_PROBE = (
    "import json\n"
    "try:\n"
    "    from lex.lex_app import settings as s\n"
    "except Exception as exc:\n"
    "    print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}))\n"
    "else:\n"
    "    print(json.dumps({'mode': s.LEX_WS_AUTH}))\n"
)


def _ws_auth_in_fresh_interpreter(raw: str | None) -> dict:
    """Import lex's settings in a new interpreter with ``LEX_WS_AUTH=raw`` (unset for None)."""
    cleared = ("DJANGO_SETTINGS_MODULE", "PROJECT_ROOT", "PYTHONPATH", "DEPLOYMENT_ENVIRONMENT", "LEX_WS_AUTH")
    env = {k: v for k, v in os.environ.items() if k not in cleared}
    env["PYTHONPATH"] = _CHECKOUT.as_posix()
    if raw is not None:
        env["LEX_WS_AUTH"] = raw
    result = subprocess.run(
        [sys.executable, "-c", _FLAG_PROBE], cwd=_CHECKOUT, env=env, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, f"fresh interpreter crashed:\n{result.stderr[-3000:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- review fixes (security review, 2026-11-06) -----------------------------------------
class _EndSignInAfterResolve:
    """Ends the sign-in (with the library's group message) after the ASGI middleware resolved
    it and before the consumer runs: the window between the read and the group join."""

    def __init__(self, inner) -> None:
        self.inner = inner

    async def __call__(self, scope, receive, send):
        info = scope.get("oidc_session")
        if info:
            row = await database_sync_to_async(OIDCSession.objects.get)(pk=info["session_id"])
            await database_sync_to_async(oidc_sessions.end_session)(row, "admin")
        return await self.inner(scope, receive, send)


def _sign_in_rows_unreadable(error: type[Exception]):
    """The database fails every query on sign-in rows with ``error`` (a dropped connection)."""
    table = OIDCSession._meta.db_table

    def wrapper(execute, sql, params, many, context):
        if table in sql:
            raise error("connection already closed")
        return execute(sql, params, many, context)

    return connection.execute_wrapper(wrapper)


class TestCluster09j_ReviewConnect(_SocketTestCase):
    """9.68-9.69: no check ever breaks a socket; a sign-in that ends while joining is seen."""

    # -- 9.68 ----------------------------------------------------------
    def test_9_68_a_dropped_database_connection_is_4503_under_enforce_and_harmless_under_log(self) -> None:
        """
        Scenario 9.68: ``InterfaceError`` (not a ``DatabaseError``) while the sign-in is read.
        Given: a signed-in browser; the database connection drops for sign-in rows.
        When: it opens the calculations socket under ``enforce``, then under ``log``.
        Then: ``enforce`` accepts and closes it with 4503 (the client backs off and retries);
              ``log`` serves it as before 2.5 and says once that ``enforce`` would close it with
              4503. Before review the error escaped ``connect()`` in both modes: the browser saw
              1006 and a socket 2.4 served was refused under ``log``.
        """
        from django.db import InterfaceError

        client, _ = self._signed_in()
        for mode in ("enforce", "log"):
            with self.subTest(mode=mode), override_settings(LEX_WS_AUTH=mode):

                async def scenario():
                    sock = _Socket("/ws/calculations", self._key(client))
                    self.assertEqual((await sock.open())["type"], "websocket.accept")
                    if mode == "enforce":
                        closed = await sock.next()
                        self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4503))
                    else:
                        await _broadcast("calculations", BROADCASTS["/ws/calculations"])
                        self.assertEqual((await sock.next())["type"], "websocket.send", "served as before")
                    await sock.hang_up()

                with _sign_in_rows_unreadable(InterfaceError), self.assertLogs(LOGGER, logging.INFO) as logged:
                    self._run(scenario)
                self.assertTrue(any("4503" in line for line in logged.output), logged.output)

    # -- 9.69 ----------------------------------------------------------
    def test_9_69_a_sign_in_that_ends_while_the_socket_joins_its_group_is_closed_at_once(self) -> None:
        """
        Scenario 9.69: the gap between the middleware's read and the group join.
        Given: a signed-in browser whose sign-in ends after the ASGI middleware resolved it and
               before the socket joined the sign-in's group (its "ended" message went nowhere).
        When: the calculations socket opens under ``enforce``, then under ``log``.
        Then: ``enforce`` accepts and closes it with 4401 at connect (before review it was served
              until the next re-validation, up to 5.5 minutes); ``log`` serves it and warns once.
        """
        app = AuthMiddlewareStack(OIDCSessionASGIMiddleware(_EndSignInAfterResolve(
            URLRouter(routing.authenticated_websocket_urlpatterns()))))
        for mode in ("enforce", "log"):
            with self.subTest(mode=mode):
                client, _ = self._signed_in(f"ada-{mode}")
                with override_settings(LEX_WS_AUTH=mode):

                    async def scenario():
                        sock = _Socket("/ws/calculations", self._key(client), app)
                        self.assertEqual((await sock.open())["type"], "websocket.accept")
                        if mode == "enforce":
                            closed = await sock.next()
                            self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4401))
                        else:
                            self.assertTrue(await sock.quiet(), "log serves it")
                        await sock.hang_up()

                    with self.assertLogs(LOGGER, logging.INFO) as logged:
                        self._run(scenario)
                    self.assertTrue(any("session_ended" in line for line in logged.output), logged.output)


@override_settings(LEX_WS_AUTH="enforce")
class TestCluster09j_ReviewRevalidation(_SocketTestCase):
    """9.70-9.73: re-validation sees every way a sign-in ends, also for synchronous consumers."""

    def _closes_at_revalidation(self, client: Client, end, consumer=CalculationsConsumer,
                                socket_path: str = "/ws/calculations", app=None) -> None:
        """Open a socket for ``client``, run ``end()``, and expect 4401 from the next re-validation."""

        async def scenario():
            with mock.patch.object(consumer, "oidc_revalidate_seconds", 0.2):
                sock = _Socket(socket_path, self._key(client), app)
                self.assertEqual((await sock.open())["type"], "websocket.accept")
                self.assertTrue(await sock.quiet(0.3), "served while the sign-in is alive")
                await database_sync_to_async(end)()
                closed = await sock.next(timeout=3)
                self.assertEqual((closed["type"], closed.get("code")), ("websocket.close", 4401))
                await sock.hang_up()

        self._run(scenario)

    # -- 9.70 ----------------------------------------------------------
    def test_9_70_a_logout_that_bypasses_the_library_closes_the_socket(self) -> None:
        """
        Scenario 9.70: the Django session is gone.
        Given: a socket of a Keycloak sign-in, and one of a Django login (another backend).
        When: their Django sessions are deleted -- ``/admin/logout/`` or anything else that ends
              the session without the library.
        Then: the next re-validation closes each with 4401 (before review only the
              ``OIDCSession`` row was looked at, and a Django login was never re-validated).
        """
        from django.contrib.sessions.models import Session

        oidc_client, _ = self._signed_in("ada")
        local = Client()
        local.force_login(get_user_model().objects.create_user(username="admin", email="admin@example.test"),
                          backend="django.contrib.auth.backends.ModelBackend")
        for what, client in (("keycloak sign-in", oidc_client), ("django login", local)):
            with self.subTest(socket=what):
                key = self._key(client)
                self._closes_at_revalidation(client, lambda key=key: Session.objects.filter(session_key=key).delete())

    # -- 9.71 ----------------------------------------------------------
    def test_9_71_a_deactivated_user_loses_the_socket(self) -> None:
        """
        Scenario 9.71: the user is deactivated.
        Given: a signed-in socket.
        When: an administrator deactivates the user (``is_active = False``).
        Then: the next re-validation closes the socket with 4401.
        """
        client, row = self._signed_in("ada")
        self._closes_at_revalidation(
            client, lambda: get_user_model().objects.filter(pk=row.user_id).update(is_active=False))

    # -- 9.72 ----------------------------------------------------------
    def test_9_72_a_sign_in_whose_refresh_token_expired_closes_the_socket(self) -> None:
        """
        Scenario 9.72: Keycloak's session ran out without anyone asking.
        Given: a signed-in socket; nobody uses the app, so no refresh ends the row.
        When: the sign-in's refresh token expires (``refresh_expires_at`` passes) -- or, for a
              sign-in Keycloak gave no refresh token, its access token expires.
        Then: the next re-validation closes the socket with 4401 -- the sign-in cannot continue.
              (An expired access token alone, with a refresh token to renew it, is served: 9.57.)
        """
        past = timezone.now() - timezone.timedelta(seconds=1)
        cases = {
            "refresh token expired": ("ada-expired", {"refresh_expires_at": past}),
            "no refresh token, access token expired": (
                "ada-no-refresh", {"refresh_token_enc": None, "refresh_expires_at": None, "access_expires_at": past}),
        }
        for what, (username, change) in cases.items():
            with self.subTest(case=what):
                client, row = self._signed_in(username)
                self._closes_at_revalidation(
                    client, lambda row=row, change=change: OIDCSession.objects.filter(pk=row.pk).update(**change))

    # -- 9.73 ----------------------------------------------------------
    def test_9_73_a_synchronous_consumer_is_re_validated_through_the_channel_layer(self) -> None:
        """
        Scenario 9.73: ``LexSyncSocketAuthMixin`` re-validates too.
        Given: a project's synchronous consumer, signed in, re-validating every 0.2 s.
        When: the Django session is deleted (no group message).
        Then: the ticker's ``lex.socket.revalidate`` message makes the consumer check and close
              the socket with 4401 (before review a synchronous consumer never re-validated).
        """
        from django.contrib.sessions.models import Session

        client, _ = self._signed_in("ada")
        key = self._key(client)
        self._closes_at_revalidation(
            client, lambda: Session.objects.filter(session_key=key).delete(),
            consumer=_SyncTicker, socket_path="/ws/project/0", app=_project_app(_SyncTicker))
