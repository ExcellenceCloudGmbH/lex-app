"""lex.ui: who is acting, the page's parameters, and lex-app's API as the user.

Intent: a handler body is plain Python on a worker thread, so everything that needs async work
is reachable synchronously through the invocation: the signed-in user, the page's parameters,
the user's grants, and lex-app's API called with the user's token -- fetched for every call, so
a long job never uses an expired one. A regression is a handler acting as nobody, or as a stale
token.

Cluster 1cf -- scenarios 1.756-1.758. Type: U.
Covers: lex/lex_app/reflex/ui/context.py; session.access_token_of; auth.permissions_of.
Run: python -m lex pytest lex/test_project/tests/init/test_1cf_ui_context.py -v
"""

from __future__ import annotations

import asyncio
import json
from unittest import TestCase, mock

import httpx
import pytest
from django.contrib.auth.models import Group

from lex.lex_app.reflex.data import LexApiError
from lex.lex_app.reflex.ui import context, pools

pytestmark = pytest.mark.init


def _session(tokens, grants=(), client=None, **params):
    async def token():
        return tokens.pop(0) if len(tokens) > 1 else tokens[0]

    async def permissions():
        return list(grants)

    return context.Session(user=context.User(id="u1", name="Anna A", username="anna", email="anna@example.test"),
                           params=context.Params(params), token=token, permissions=permissions, api_client=client)


def _in_thread(session, body):
    """Run ``body(inv)`` on a pool thread, as a handler body runs."""
    async def scenario():
        inv = context.Invocation(handler="T.h", kind="action", session=session,
                                 loop=asyncio.get_running_loop(), live=None, orm=False)

        def call():
            token = context.enter(inv)
            try:
                return body(inv)
            finally:
                context.leave(token)

        return await pools.run("interactive", call)

    try:
        return asyncio.run(scenario())
    finally:
        pools.shutdown()


class TestCluster01cf_Context(TestCase):
    """1.756-1.758."""

    # -- 1.756 ---------------------------------------------------------
    def test_1_756_user_params_and_grants_are_reachable_from_the_body(self) -> None:
        """
        Scenario 1.756: what a body can ask without awaiting.
        Given: a session with a user, page parameters and one model-wide grant.
        When:  the body reads them through the current invocation.
        Then:  the user's four names are there; a parameter reads as an attribute or with
               get(); a missing one is an AttributeError that says so; can() answers from the
               grants; outside a handler, current() says it only works inside one; on_loop
               refuses the event loop's own thread, and on a timeout it cancels the coroutine
               so it never completes (a retry cannot duplicate a write).
        """
        session = _session(["tok"], grants=[{"rsname": "auth.Group", "scopes": ["read"]}], section="open")

        def body(inv):
            params = context.current().session.params
            return (inv.session.user.email, params.section, params.get("missing", "dflt"),
                    inv.can(Group, "read"), inv.can(Group, "edit"))

        self.assertEqual(_in_thread(session, body), ("anna@example.test", "open", "dflt", True, False))
        with self.assertRaises(AttributeError) as missing:
            context.Params({}).nope
        self.assertIn("no parameter", str(missing.exception))
        with self.assertRaises(RuntimeError) as outside:
            context.current()
        self.assertIn("inside a lex.ui handler", str(outside.exception))
        async def on_own_thread():
            inv = context.Invocation(handler="T.h", kind="action", session=session,
                                     loop=asyncio.get_running_loop(), live=None, orm=False)
            with self.assertRaises(RuntimeError, msg="on_loop from the loop's own thread must refuse") as refused:
                inv.on_loop(asyncio.sleep(0))
            return str(refused.exception)

        self.assertIn("worker thread", asyncio.run(on_own_thread()))

        finished = []

        async def slow():
            await asyncio.sleep(0.3)
            finished.append("done")

        async def timed_out():
            with mock.patch.object(context, "LOOP_TIMEOUT_SECONDS", 0.05):
                def body(inv):
                    return inv.on_loop(slow())

                inv = context.Invocation(handler="T.h", kind="action", session=session,
                                         loop=asyncio.get_running_loop(), live=None, orm=False)

                def call():
                    token = context.enter(inv)
                    try:
                        return body(inv)
                    finally:
                        context.leave(token)

                try:
                    await pools.run("interactive", call)
                except Exception as exc:  # noqa: BLE001
                    error = exc
                else:
                    error = None
            await asyncio.sleep(0.5)
            return error

        try:
            error = asyncio.run(timed_out())
        finally:
            pools.shutdown()
        self.assertIsNotNone(error, "a body that waits past the timeout gets an error")
        self.assertEqual(finished, [], "the timed-out coroutine was cancelled and never completed")
        self.assertEqual(context.User.from_profile({"sub": "s", "name": "N", "preferred_username": "u", "email": "e"}),
                         context.User(id="s", name="N", username="u", email="e"))

    # -- 1.757 ---------------------------------------------------------
    def test_1_757_the_api_is_called_as_the_user_with_a_fresh_token_each_time(self) -> None:
        """
        Scenario 1.757: self.api.
        Given: lex-app's API behind a recording transport, and a token that changes between calls.
        When:  the body lists with filters, a search and an ordering, gets, creates, updates
               and deletes.
        Then:  each call carries the token current at that moment as a bearer token; list sends
               the filters (a list as repeated values), the search on the model's text field and
               the ordering, and returns plain rows with the total count, trimmed to the fields
               asked for; the other calls hit lex-app's routes and return the row.
        """
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, dict(request.url.params.multi_items()),
                         request.headers["authorization"], request.content))
            if request.url.path.endswith("/list"):
                return httpx.Response(200, json={"count": 7, "results": [{"id": 1, "name": "alpha", "x": 9}]})
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json={"id": 1, "name": "alpha"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        session = _session(["first", "second"], client=client)

        def body(inv):
            api = context.Api(inv)
            rows = api.list(Group, filters={"name": ["a", "b"]}, search="al", ordering="-name",
                            page=2, per_page=10, fields=["id", "name"])
            got = api.get(Group, 1)
            made = api.create(Group, {"name": "beta"})
            changed = api.update(Group, 1, {"name": "gamma"})
            gone = api.delete(Group, 1)
            return rows, rows.count, got, made, changed, gone

        rows, count, got, made, changed, gone = _in_thread(session, body)
        self.assertEqual((list(rows), count), ([{"id": 1, "name": "alpha"}], 7), "plain rows, trimmed, with the count")
        self.assertEqual((got, made, changed, gone), ({"id": 1, "name": "alpha"},) * 3 + (None,))
        method, path, query, auth, _ = seen[0]
        self.assertEqual((method, auth), ("GET", "Bearer first"), "the first call uses the first token")
        self.assertTrue(path.endswith("/api/model_entries/group/list"), path)
        self.assertEqual((query["page"], query["perPage"], query["ordering"], query["name__icontains"]),
                         ("2", "10", "-name", "al"))
        self.assertEqual(seen[1][3], "Bearer second", "a later call fetches the token again")
        self.assertEqual([s[0] for s in seen], ["GET", "GET", "POST", "PATCH", "DELETE"])
        self.assertEqual(json.loads(seen[2][4]), {"name": "beta"})

    # -- 1.758 ---------------------------------------------------------
    def test_1_758_a_refusal_is_an_api_error_and_a_stand_in_replaces_the_api(self) -> None:
        """
        Scenario 1.758: refusals, no sign-in, and the test stand-in.
        Given: an API that answers 403; a session with no token; a session with a stand-in api.
        When:  the body calls the API.
        Then:  403 raises LexApiError with denied true; no token raises 401; with a stand-in,
               self.api is the stand-in itself.
        """
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"detail": "no"})))
        with self.assertRaises(LexApiError) as denied:
            _in_thread(_session(["tok"], client=client), lambda inv: context.Api(inv).get(Group, 1))
        self.assertTrue(denied.exception.denied)
        with self.assertRaises(LexApiError) as anonymous:
            _in_thread(_session([""]), lambda inv: context.Api(inv).get(Group, 1))
        self.assertEqual(anonymous.exception.status, 401)
        session = _session(["tok"])
        session.api = stand_in = object()
        self.assertIs(_in_thread(session, lambda inv: context.api_of(inv)), stand_in)
