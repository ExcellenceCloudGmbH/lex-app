"""The live calculation sockets carry a run only to who may read its record.

Intent
------
A calculation's log lines, its state (with any error message and traceback) and its run id are
data of the record it runs on. Three live channels sent them to every connected socket, whatever
the record's ``permission_read`` said, and under the default ``LEX_WS_AUTH=log`` to sockets
without a sign-in too:

* ``ws/calculation_logs/<record>-<run>``: the record's live log lines. The channel is named after
  the record (``<model>_<pk>``), so any record could be followed by its number;
* ``ws/calculation_status_update``: on connect every running calculation (display name, model,
  primary key, run id), then each state change;
* ``ws/calculations``: the record and run id of every run that starts.

They now follow the rule of the REST endpoints (10.198-10.201) and of the calculation-log tree
(``LEX_SEC_INTERNAL``): a socket hears about the runs of records its user may read, a superuser
about every run, and a socket without a sign-in about none (REST answers it 401). A refused socket
stays open and hears nothing, like a quiet run. The user is judged as in an API call: the same
Keycloak client roles and UMA permissions, read with the sign-in's access token while it is
valid. A socket never refreshes a token, so one whose token ran out keeps the permissions it last
had. ``LEX_SEC_INTERNAL=false`` restores the open channels. A regression hands a record's business
data, errors and tracebacks to users its permission refuses.

Every socket goes through ``lex.lex_app.asgi.application`` with a real sign-in, and every message
comes from the code that sends it in production (``WebSocketHandler``,
``update_calculation_status``, ``WebSocketNotifier``).

Cluster 9k -- scenarios 9.74-9.81. Type: I.
Covers: lex/api/consumers/record_access.py (new), lex/api/consumers/CalculationLogConsumer.py,
        lex/api/consumers/UpdateCalculationStatusConsumer.py,
        lex/api/consumers/CalculationsConsumer.py.
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9k_calculation_sockets_read.py -v
"""

from __future__ import annotations

import atexit
import json
import logging
import os
from datetime import timedelta
from unittest import mock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.utils import timezone
from oauth2_authcodeflow.models import OIDCSession

from lex.api.consumers import record_access
from lex.audit_logging.handlers.WebSocketHandler import WebSocketHandler
from lex.audit_logging.utils.WebSocketNotifier import WebSocketNotifier
from lex.core.calculation_start import calculation_record_id
from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.core.signals.CalculationSignals import update_calculation_status
from lex.lex_app import asgi
from lex.lex_app.tests.auth import create_sign_in
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import GUARDED_CALC_AUDITOR_ROLE, SOCKET_READ_MODELS, SigGuardedCalc, SigScopedCalc

pytestmark = pytest.mark.signals_ws

try:  # importing the ASGI module registers the server's shutdown hook; tests are no server
    atexit.unregister(asgi.on_server_shutdown)
except ValueError:
    pass

#: The security switches; each test starts with none of them set.
SECURITY_PREFIXES = ("LEX_SEC_", "LEX_LEGACY_SECURITY")
#: Keycloak "configured" at an address that can never be reached, so UMA lookups go to the stub.
UNREACHABLE_KEYCLOAK = {
    "KEYCLOAK_URL": "https://keycloak.invalid",
    "KEYCLOAK_REALM": "lex-9k",
    "OIDC_RP_CLIENT_ID": "lex-9k",
}
#: The UMA grant that makes every ``SigScopedCalc`` readable (LexModel's default
#: ``permission_read``); without a ``resource_set_id`` it covers the whole model.
SCOPED_READ = {"rsname": "lex_app.SigScopedCalc", "scopes": ["read"]}

LOG_SOCKET = "/ws/calculation_logs/{record}-{run}"
STATUS_SOCKET = "/ws/calculation_status_update"
RUNS_SOCKET = "/ws/calculations"


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
    """A browser's WebSocket on the real ASGI application."""

    def __init__(self, socket_path: str, session_key: str | None = None) -> None:
        self.path = socket_path
        self.communicator = ApplicationCommunicator(asgi.application, _scope(socket_path, session_key))

    async def open(self) -> dict:
        await self.communicator.send_input({"type": "websocket.connect"})
        return await self.communicator.receive_output(5)

    async def message(self) -> dict:
        """The next message the server sends, decoded; fails on a close or anything else."""
        output = await self.communicator.receive_output(5)
        if output.get("type") != "websocket.send":
            raise AssertionError(f"{self.path}: expected a message, got {output}")
        return json.loads(output["text"])

    async def quiet(self, timeout: float = 0.4) -> bool:
        """Whether the server sends nothing (no message, no close) for ``timeout`` seconds."""
        return await self.communicator.receive_nothing(timeout)

    async def hang_up(self) -> None:
        await self.communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await self.communicator.wait(5)


def _log_line(record: str, run: str, line: str) -> None:
    """A calculation logs ``line``: what LexLogger's WebSocket handler does with it."""
    WebSocketHandler().emit(logging.makeLogRecord({"msg": line, "calculation_record": record, "calculationId": run}))


def _finish(row, state: str, **details) -> None:
    """A run on ``row`` ends in ``state``, announced as the framework announces it."""
    row.is_calculated = state
    row.save(skip_hooks=True)
    update_calculation_status(row, **details)


class TestCluster09k_CalculationSocketsRead(E2ETestCase):
    """Cluster 9k: the three calculation sockets follow the record's ``permission_read``."""

    e2e_models = SOCKET_READ_MODELS
    # The real store and the real run-started notifier: these tests watch what they send.
    e2e_unpatch = {"mark_in_progress", "send_calculation_update"}

    def setUp(self) -> None:
        super().setUp()
        self._environ()
        mode = override_settings(LEX_WS_AUTH="log")  # the default; an unsigned socket is served
        mode.enable()
        self.addCleanup(mode.disable)
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)
        self.addCleanup(async_to_sync(get_channel_layer().flush))
        self.user_key = self._key(self.client)

    # -- fixtures ---------------------------------------------------------------------------
    def _environ(self, **values: str) -> None:
        """For the rest of the test: no security switch set, plus ``values``."""
        keys = {key for key in os.environ if key.startswith(SECURITY_PREFIXES)} | set(values)
        before = {key: os.environ.get(key) for key in keys}
        for key in keys:
            os.environ.pop(key, None)
        os.environ.update(values)

        def restore() -> None:
            for key, value in before.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)

    @staticmethod
    def _key(client) -> str:
        return client.cookies[settings.SESSION_COOKIE_NAME].value

    def _browser(self, username: str, *, superuser: bool = False, **sign_in) -> tuple[str, OIDCSession]:
        """A browser signed in through Keycloak as a new user: its session key and sign-in row."""
        user = get_user_model().objects.create_user(
            username=username, email=f"{username}@example.test", is_superuser=superuser,
        )
        client = Client()
        row = create_sign_in(client, user, **sign_in)
        return self._key(client), row

    def _running(self, model, name: str, **fields):
        """A row with a running calculation, registered the way a Calculate click registers it."""
        row = model.objects.create(name=name, **fields)
        row.is_calculated = CalculationModel.IN_PROGRESS
        row.save(skip_hooks=True)
        record = calculation_record_id(row)
        run = f"{record}_update_9k"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record,
            calculation_id=run,
            record=str(row),
            model_name=row._meta.object_name,
            model_label=row._meta.label_lower,
            record_pk=row.pk,
        )
        return row, record, run

    def _uma(self, grants_by_token: dict) -> mock.MagicMock:
        """Keycloak's UMA answer per access token (nothing for any other token)."""
        self._environ(**UNREACHABLE_KEYCLOAK)
        fetch = self._patch_map["keycloak_uma"]
        fetch.side_effect = lambda token, *args, **kwargs: list(grants_by_token.get(token, []))
        return fetch

    async def _snapshot(self, sock: _Socket) -> list:
        """Open a status socket; the record ids of the running calculations it is told about."""
        self.assertEqual((await sock.open())["type"], "websocket.accept", f"{sock.path}: accepted")
        first = await sock.message()
        self.assertEqual(first["type"], "calculation_reconciliation", "a status socket starts with the snapshot")
        return sorted(entry["record_id"] for entry in first["payload"]["active_calculations"])

    def _run(self, scenario) -> None:
        async_to_sync(scenario)()

    # -- 9.74 -------------------------------------------------------------------------------
    def test_9_74_a_records_live_log_reaches_only_a_user_who_may_read_it(self) -> None:
        """
        Scenario 9.74: the live-log socket streams a record's lines to who may read the record.
        Given: a signed-in user; an open row and a secret row, each with a running calculation.
        When: the user opens the live-log socket of each run, and each run logs a line.
        Then: both sockets are accepted and stay open; the open row's line arrives on its
              socket; the secret row's socket hears nothing, neither the line nor a close.
        """
        _, open_record, open_run = self._running(SigGuardedCalc, "open")
        _, secret_record, secret_run = self._running(SigGuardedCalc, "secret", is_secret=True)

        async def scenario():
            open_sock = _Socket(LOG_SOCKET.format(record=open_record, run=open_run), self.user_key)
            secret_sock = _Socket(LOG_SOCKET.format(record=secret_record, run=secret_run), self.user_key)
            for sock in (open_sock, secret_sock):
                self.assertEqual((await sock.open())["type"], "websocket.accept", f"{sock.path}: accepted")
            await sync_to_async(_log_line)(secret_record, secret_run, "secret: bonus 4200")
            await sync_to_async(_log_line)(open_record, open_run, "open: step 1 done")
            line = await open_sock.message()
            self.assertEqual(line["type"], "calculation_log_real_time")
            self.assertIn("open: step 1 done", json.dumps(line), "the open row's line reaches its reader")
            self.assertTrue(await open_sock.quiet(), "only the socket's own record's lines reach it")
            self.assertTrue(
                await secret_sock.quiet(),
                "a secret row's live log must not reach a user its permission_read refuses",
            )
            await open_sock.hang_up()
            await secret_sock.hang_up()

        self._run(scenario)

    # -- 9.75 -------------------------------------------------------------------------------
    def test_9_75_a_superuser_and_the_switch_off_hear_every_records_log(self) -> None:
        """
        Scenario 9.75: a superuser follows any run; LEX_SEC_INTERNAL=false opens the channel.
        Given: a secret row with a running calculation.
        When: a superuser opens its live-log socket and the run logs a line; then, with
              LEX_SEC_INTERNAL=false, the plain signed-in user does the same.
        Then: the line reaches the superuser, and with the switch off the plain user too.
        """
        _, secret_record, secret_run = self._running(SigGuardedCalc, "secret", is_secret=True)
        root_key, _ = self._browser("root", superuser=True)
        socket_path = LOG_SOCKET.format(record=secret_record, run=secret_run)

        async def hears(session_key: str, line: str) -> None:
            sock = _Socket(socket_path, session_key)
            self.assertEqual((await sock.open())["type"], "websocket.accept")
            await sync_to_async(_log_line)(secret_record, secret_run, line)
            self.assertIn(line, json.dumps(await sock.message()), f"{line!r} reaches the socket")
            await sock.hang_up()

        with self.subTest("superuser"):
            self._run(lambda: hears(root_key, "seen by a superuser"))
        with self.subTest("LEX_SEC_INTERNAL=false"):
            self._environ(LEX_SEC_INTERNAL="false")
            self._run(lambda: hears(self.user_key, "seen with the switch off"))

    # -- 9.76 -------------------------------------------------------------------------------
    def test_9_76_the_status_snapshot_lists_only_runs_on_readable_records(self) -> None:
        """
        Scenario 9.76: the snapshot a status socket gets on connect leaves out refused runs.
        Given: two running calculations, on an open row and on a secret row named
               "Payroll secret".
        When: the plain user and a superuser each open the calculation-status socket.
        Then: the user's snapshot holds the open run only, and the secret run's record id, run
              id and display name appear nowhere in it; the superuser's holds both runs.
        """
        _, open_record, _ = self._running(SigGuardedCalc, "open")
        _, secret_record, secret_run = self._running(SigGuardedCalc, "Payroll secret", is_secret=True)
        root_key, _ = self._browser("root", superuser=True)

        async def scenario():
            sock = _Socket(STATUS_SOCKET, self.user_key)
            self.assertEqual(await self._snapshot(sock), [open_record], "only the readable run is listed")
            await sock.hang_up()
            root = _Socket(STATUS_SOCKET, root_key)
            self.assertEqual(await self._snapshot(root), sorted([open_record, secret_record]),
                             "a superuser sees every run")
            await root.hang_up()

        self._run(scenario)

        async def raw_snapshot():
            sock = _Socket(STATUS_SOCKET, self.user_key)
            await sock.open()
            body = json.dumps(await sock.message())
            await sock.hang_up()
            return body

        body = async_to_sync(raw_snapshot)()
        for hidden in (secret_record, secret_run, "Payroll secret"):
            self.assertNotIn(hidden, body, f"the refused run's {hidden!r} must not reach the user")

    # -- 9.77 -------------------------------------------------------------------------------
    def test_9_77_state_changes_reach_readers_only_and_still_update_the_store(self) -> None:
        """
        Scenario 9.77: each state change, error and traceback included, goes to who may read it.
        Given: running calculations on an open row and on a secret row; the plain user and a
               superuser on the calculation-status socket.
        When: the secret run fails with an error message and a traceback, and the open run
              succeeds (``update_calculation_status``); then a worker reports another secret
              row's run in progress on the channel while only the plain user is connected.
        Then: the user hears the open run's success and nothing of the secret run; the
              superuser hears the failure with its message, then the success; the in-progress
              report still lands in this process's store, though the user does not hear it.
        """
        open_row, open_record, _ = self._running(SigGuardedCalc, "open")
        secret_row, secret_record, _ = self._running(SigGuardedCalc, "secret", is_secret=True)
        later = SigGuardedCalc.objects.create(name="secret too", is_secret=True)
        later_record = calculation_record_id(later)
        root_key, _ = self._browser("root", superuser=True)

        async def scenario():
            sock = _Socket(STATUS_SOCKET, self.user_key)
            root = _Socket(STATUS_SOCKET, root_key)
            await self._snapshot(sock)
            await self._snapshot(root)
            await sync_to_async(_finish)(
                secret_row, CalculationModel.ERROR,
                exception_details="secret failure detail", stack_trace="Traceback: secret frame",
            )
            await sync_to_async(_finish)(open_row, CalculationModel.SUCCESS)

            heard = await sock.message()
            self.assertEqual((heard["type"], heard["payload"]["record_id"]), ("calculation_success", open_record),
                             "the open run's success reaches the user")
            self.assertTrue(await sock.quiet(), "the secret run's failure must not reach the user")

            failure = await root.message()
            self.assertEqual((failure["type"], failure["payload"]["record_id"]), ("calculation_error", secret_record))
            self.assertEqual(failure["payload"].get("message"), "secret failure detail",
                             "a superuser hears the failure with its message")
            success = await root.message()
            self.assertEqual(success["payload"]["record_id"], open_record)
            await root.hang_up()

            await get_channel_layer().group_send("update_calculation_status", {
                "type": "calculation_in_progress",
                "payload": {"record_id": later_record, "calculation_id": f"{later_record}_update_w", "record": "secret too"},
            })
            self.assertTrue(await sock.quiet(), "a worker's report on a secret row must not reach the user")
            entry = await sync_to_async(ActiveCalculationStateStore.get_entry)(later_record)
            self.assertEqual(entry.get("calculation_id"), f"{later_record}_update_w",
                             "every state change keeps this process's store in sync, heard or not")
            await sock.hang_up()

        self._run(scenario)

    # -- 9.78 -------------------------------------------------------------------------------
    def test_9_78_a_runs_start_notice_reaches_only_readers_of_its_record(self) -> None:
        """
        Scenario 9.78: ``ws/calculations`` announces a run's id to who may read its record.
        Given: an open row and a secret row; the plain user and a superuser on ``ws/calculations``.
        When: a run starts on each (``WebSocketNotifier.send_calculation_update``), then a
              notification that names no record is sent.
        Then: the user hears the open run's id and the notification, nothing of the secret run;
              the superuser hears all three.
        """
        _, open_record, open_run = self._running(SigGuardedCalc, "open")
        _, secret_record, secret_run = self._running(SigGuardedCalc, "secret", is_secret=True)
        root_key, _ = self._browser("root", superuser=True)

        def start_both_and_notify() -> None:
            WebSocketNotifier.send_calculation_update(calculation_record=secret_record, calculation_id=secret_run)
            WebSocketNotifier.send_calculation_update(calculation_record=open_record, calculation_id=open_run)
            WebSocketNotifier.send_calculation_notification({"message": "nightly import finished"})

        async def scenario():
            sock = _Socket(RUNS_SOCKET, self.user_key)
            root = _Socket(RUNS_SOCKET, root_key)
            for each in (sock, root):
                self.assertEqual((await each.open())["type"], "websocket.accept")
            await sync_to_async(start_both_and_notify)()

            started = await sock.message()
            self.assertEqual((started["type"], started["payload"]["calculation_record"]), ("calculation_id", open_record),
                             "the open run's id reaches the user")
            notice = await sock.message()
            self.assertEqual(notice["type"], "calculation_notification", "a notice naming no record reaches a signed-in user")
            self.assertTrue(await sock.quiet(), "the secret run's id must not reach the user")

            heard = [await root.message() for _ in range(3)]
            records = [message["payload"].get("calculation_record") for message in heard]
            self.assertEqual(records, [secret_record, open_record, None], "a superuser hears every run start")
            for each in (sock, root):
                await each.hang_up()

        self._run(scenario)

    # -- 9.79 -------------------------------------------------------------------------------
    def test_9_79_a_socket_without_a_sign_in_hears_nothing_under_the_default_log_mode(self) -> None:
        """
        Scenario 9.79: LEX_WS_AUTH=log serves an unsigned socket; no calculation reaches it.
        Given: LEX_WS_AUTH=log (the default) and a running calculation on an open row.
        When: a browser without a session opens the three calculation sockets; the run logs a
              line, starts again (run id notice), succeeds, and a notification is sent.
        Then: every socket is accepted and stays open; the status snapshot is empty; nothing
              else arrives on any of them.
        """
        open_row, open_record, open_run = self._running(SigGuardedCalc, "open")

        def everything_happens() -> None:
            _log_line(open_record, open_run, "open: step 1 done")
            WebSocketNotifier.send_calculation_update(calculation_record=open_record, calculation_id=open_run)
            WebSocketNotifier.send_calculation_notification({"message": "nightly import finished"})
            _finish(open_row, CalculationModel.SUCCESS)

        async def scenario():
            log = _Socket(LOG_SOCKET.format(record=open_record, run=open_run))
            runs = _Socket(RUNS_SOCKET)
            status = _Socket(STATUS_SOCKET)
            for sock in (log, runs):
                self.assertEqual((await sock.open())["type"], "websocket.accept", f"{sock.path}: served under log")
            self.assertEqual(await self._snapshot(status), [], "an unsigned socket is told of no running calculation")
            await sync_to_async(everything_happens)()
            for sock in (log, runs, status):
                self.assertTrue(await sock.quiet(), f"{sock.path}: nothing reaches a socket without a sign-in")
                await sock.hang_up()

        self._run(scenario)

    # -- 9.80 -------------------------------------------------------------------------------
    def test_9_80_a_socket_is_judged_with_its_sign_ins_keycloak_roles_and_permissions(self) -> None:
        """
        Scenario 9.80: the socket's user is judged as in an API call, Keycloak included.
        Given: a running calculation on a ``SigScopedCalc`` row (readable with the Keycloak
               ``read`` scope) and one on a secret ``SigGuardedCalc`` row (readable with the
               client role ``calc-auditor``); Ada signed in with both, Bob with neither.
        When: each opens the calculation-status socket, and each asks
              ``GET /api/active-calculations`` with the same sign-in.
        Then: Ada's snapshot lists both runs and Bob's none, exactly what the REST endpoint
              answers each of them.
        """
        _, scoped_record, _ = self._running(SigScopedCalc, "scoped")
        _, secret_record, _ = self._running(SigGuardedCalc, "secret", is_secret=True)
        ada_key, ada_row = self._browser("ada", client_roles=[GUARDED_CALC_AUDITOR_ROLE])
        bob_key, _ = self._browser("bob")
        self._uma({ada_row.access_token: [SCOPED_READ]})

        async def snapshot(session_key: str) -> list:
            sock = _Socket(STATUS_SOCKET, session_key)
            records = await self._snapshot(sock)
            await sock.hang_up()
            return records

        ada_sees = async_to_sync(snapshot)(ada_key)
        bob_sees = async_to_sync(snapshot)(bob_key)
        self.assertEqual(ada_sees, sorted([scoped_record, secret_record]),
                         "Ada's Keycloak read scope and client role open both runs to her socket")
        self.assertEqual(bob_sees, [], "without them, Bob's socket hears of neither run")

        for name, session_key, socket_sees in (("ada", ada_key, ada_sees), ("bob", bob_key, bob_sees)):
            client = Client()
            client.cookies[settings.SESSION_COOKIE_NAME] = session_key
            response = client.get("/api/active-calculations")
            self.assertEqual(response.status_code, 200, response.content[:300])
            rest_sees = sorted(entry["record_id"] for entry in response.json()["active_calculations"])
            self.assertEqual(socket_sees, rest_sees, f"{name}'s socket and the REST endpoint agree")

    # -- 9.81 -------------------------------------------------------------------------------
    def test_9_81_a_socket_never_refreshes_a_token_and_keeps_its_last_permissions(self) -> None:
        """
        Scenario 9.81: an expired access token is neither refreshed nor sent to Keycloak.
        Given: a running calculation on a ``SigScopedCalc`` row; Ada signed in with the read
               scope, her socket connected while her access token was valid; Carol, with the
               same grant, signed in with a token that has already expired; the socket re-reads
               its user on every message.
        When: Ada's token expires and the run succeeds; Carol opens a status socket.
        Then: Ada still hears the success, with the permissions her socket last had; Carol's
              snapshot is empty, because her socket has no permissions to go on; no token is
              refreshed and no expired token is sent to Keycloak.
        """
        scoped_row, scoped_record, _ = self._running(SigScopedCalc, "scoped")
        ada_key, ada_row = self._browser("ada")
        carol_key, carol_row = self._browser("carol", access_expires_in=-60)
        fetch = self._uma({ada_row.access_token: [SCOPED_READ], carol_row.access_token: [SCOPED_READ]})
        rereads = mock.patch.object(record_access, "REREAD_SECONDS", 0)
        rereads.start()
        self.addCleanup(rereads.stop)
        refreshes = [mock.patch(target, side_effect=AssertionError("a socket refreshed a token"))
                     for target in ("oauth2_authcodeflow.tokens.ensure_fresh", "oauth2_authcodeflow.sessions.ensure_fresh")]
        spies = [patcher.start() for patcher in refreshes]
        for patcher in refreshes:
            self.addCleanup(patcher.stop)

        async def scenario():
            carol = _Socket(STATUS_SOCKET, carol_key)
            self.assertEqual(await self._snapshot(carol), [],
                             "a socket that never had a valid token has no permissions: it hears of no run")
            await carol.hang_up()

            ada = _Socket(STATUS_SOCKET, ada_key)
            self.assertEqual(await self._snapshot(ada), [scoped_record], "Ada's valid token grants the run")
            asked = fetch.call_count
            await sync_to_async(
                OIDCSession.objects.filter(pk=ada_row.pk).update
            )(access_expires_at=timezone.now() - timedelta(minutes=1))
            await sync_to_async(_finish)(scoped_row, CalculationModel.SUCCESS)
            heard = await ada.message()
            self.assertEqual((heard["type"], heard["payload"]["record_id"]), ("calculation_success", scoped_record),
                             "an expired token keeps the socket's last permissions")
            self.assertEqual(fetch.call_count, asked, "an expired token is never sent to Keycloak")
            await ada.hang_up()

        self._run(scenario)
        self.assertNotIn(
            carol_row.access_token, [call.args[0] for call in fetch.call_args_list if call.args],
            "Carol's expired token is never sent to Keycloak",
        )
        for spy in spies:
            self.assertFalse(spy.called, "socket code never refreshes a token")
