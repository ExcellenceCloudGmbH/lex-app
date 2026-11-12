"""The permission lookup of lex-app 2.5: lazy, cached per token, single-flight, never a silent deny.

Intent
------
Keycloak is the single source of truth for who may do what (docs: permissions), and the
permissions middleware is where every request learns it. Before 2.5 each request made two
or more synchronous Keycloak calls (UMA + userinfo), and any failure became ``[]`` -- a
signed-in user saw "permission denied" during a Keycloak blip, and the frontend signed
them out (plan "Long-lived, secure sessions", root causes 12-13). The lookup now:

* is **lazy** -- ``request.user_permissions`` / ``userinfo`` / ``client_roles`` are computed
  on first use, so a request that never checks a permission never reaches Keycloak;
* takes **profile and roles from the validated sign-in** (``request.oidc``: the ID token's
  claims, the access token's ``resource_access[<client>].roles``) -- no userinfo call;
* **caches** UMA answers per access-token digest for at most ``min(60 s, token lifetime)``
  (a token belongs to one user, so the cache can never answer for another), makes **one
  call per token at a time** (single-flight), behind **its own circuit breaker**;
* tells Keycloak's answers apart: 403 ``access_denied`` is ``[]``; a rejected token is the
  contract's 401 ``session_expired``; an outage is 503 ``auth_unavailable`` -- before the
  view for writes -- and **never** a denial;
* and every place that turns a raising permission check into a denial
  (``LEX_SEC_FAIL_CLOSED``) or a grant (legacy) passes those outcomes through instead.

A regression either brings back the sign-out storms (an outage read as "no permission"),
leaks one user's permissions to another (a cache keyed wrongly), or hammers Keycloak again.

Cluster 4y -- scenarios 4.148-4.158. Type: U (4.148-4.151, 4.155-4.157) and E (4.152-4.154, 4.158).
Covers: lex/api/middleware/keycloak_permissions.py, lex/api/keycloak/uma_client.py
(KEYCLOAK_INTERNAL_URL, issuer mismatch), lex/api/security.py (is_auth_outage, guarded),
lex/authentication/drf_exceptions.py, lex/authentication/identity.py,
lex/core/models/LexModel.py (UserContext._extract_client_roles), and the fail-closed sites in
lex/api/views/model_entries/{One,filter_backends}.py, .../mixins/{PermissionAwareSerializerMixin,
DestroyOneWithPayloadMixin}.py, lex/api/views/file_operations/ModelExport.py,
lex/api/serializers/base_serializers.py, lex/api/utils/helpers.py.
Run: python -m lex pytest lex/test_project/tests/permissions/test_4y_permission_lookup.py -v
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from oauth2_authcodeflow.breaker import reset_breakers
from oauth2_authcodeflow.context import OIDCContext
from oauth2_authcodeflow.exceptions import OIDCAuthUnavailable, OIDCSessionEnded
from oauth2_authcodeflow.models import OIDCSession
from oauth2_authcodeflow.testing import create_oidc_session

from lex.api.keycloak import uma_client
from lex.api.middleware import keycloak_permissions
from lex.api.middleware.keycloak_permissions import KeycloakPermissionsMiddleware, reset_uma_cache
from lex.api.security import guarded, is_auth_outage
from lex.authentication.drf_exceptions import AuthUnavailable, SessionRejected
from lex.core.models.LexModel import PermissionResult, UserContext
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import KEYCLOAK, OUTAGE, KeycloakItem, OutageItem

pytestmark = pytest.mark.permissions

CLIENT_ID = "lex-app"
KEYCLOAK_ENV = {"KEYCLOAK_URL": "https://keycloak.test", "KEYCLOAK_REALM": "lex", "OIDC_RP_CLIENT_ID": CLIENT_ID}
FETCH = "lex.api.middleware.keycloak_permissions.uma_client.fetch_uma_permissions"
GRANTED = [{"rsname": "lex_app.KeycloakItem", "resource_set_id": None,
            "scopes": ["read", "list", "create", "edit", "delete", "export"]}]


def _context(token: str, *, expires_in: int = 300, roles=("viewer",)) -> OIDCContext:
    """The sign-in of the user behind ``token`` (a bearer context, as the bearer middleware builds it)."""
    return OIDCContext.from_bearer(token, {
        "sub": f"sub-{token}", "email": f"{token}@example.com", "exp": int(time.time()) + expires_in,
        "resource_access": {CLIENT_ID: {"roles": list(roles)}},
    })


def _request(token=None, *, method="get", **context_kwargs):
    """A request through the permissions middleware; the response is kept on ``request.response``."""
    request = getattr(RequestFactory(), method)("/api/model_entries/x/list")
    request.session = {}
    if token is not None:
        request.oidc = _context(token, **context_kwargs)
    request.response = KeycloakPermissionsMiddleware(lambda r: HttpResponse("view ran"))(request)
    return request


class _LookupTestCase(TestCase):
    """Keycloak configured (UMA has someone to ask), every cache and breaker fresh."""

    def setUp(self) -> None:
        super().setUp()
        env = mock.patch.dict(os.environ, KEYCLOAK_ENV)
        env.start()
        self.addCleanup(env.stop)
        overrides = override_settings(OIDC_RP_CLIENT_ID=CLIENT_ID)
        overrides.enable()
        self.addCleanup(overrides.disable)
        reset_uma_cache()
        reset_breakers()
        self.addCleanup(reset_uma_cache)
        self.addCleanup(reset_breakers)


class TestCluster04y_LazyAndCached(_LookupTestCase):
    """4.148-4.151: nothing is asked until needed, and each token is asked once."""

    # -- 4.148 ---------------------------------------------------------
    def test_4_148_permissions_are_fetched_only_when_read(self) -> None:
        """
        Scenario 4.148: a request that never checks a permission never reaches Keycloak.
        Given: a signed-in GET request through the permissions middleware
        When: the view runs without reading request.user_permissions, and later code reads them
        Then: no UMA call is made until the first read, exactly one is made then, and the
              profile and roles (from the sign-in) never cause a call at all.
        """
        with mock.patch(FETCH, return_value=GRANTED) as fetch:
            request = _request("tok-a")
            self.assertEqual(request.response.content, b"view ran")
            self.assertEqual(request.client_roles, ["viewer"])
            self.assertEqual(request.userinfo["email"], "tok-a@example.com")
            fetch.assert_not_called()
            self.assertEqual(list(request.user_permissions), GRANTED)
            self.assertEqual(len(request.user_permissions), 1)
        self.assertEqual(fetch.call_count, 1, "one UMA call, on the first read only")

    # -- 4.149 ---------------------------------------------------------
    def test_4_149_answers_are_cached_per_token_and_never_shared(self) -> None:
        """
        Scenario 4.149: one UMA call per token while cached; another user's token gets its own answer.
        Given: two users, Keycloak answering each token with that user's permissions
        When: user A's token is used by three requests and user B's by one
        Then: Keycloak is asked once per token; every request gets its own user's answer; the
              cache is keyed by a SHA-256 digest -- the token itself is not kept as a key;
              mutating one request's list does not change what the next request gets.
        """
        def answer(token, **kwargs):
            return [{"rsname": f"resource-of-{token}", "scopes": ["read"]}]

        with mock.patch(FETCH, side_effect=answer) as fetch:
            first = _request("tok-alice")
            first.user_permissions.append({"rsname": "planted"})
            answers = [list(_request(token).user_permissions) for token in ("tok-alice", "tok-alice", "tok-bob")]
        self.assertEqual(answers[0], [{"rsname": "resource-of-tok-alice", "scopes": ["read"]}])
        self.assertEqual(answers[1], answers[0], "a mutated answer must not leak into the cache")
        self.assertEqual(answers[2], [{"rsname": "resource-of-tok-bob", "scopes": ["read"]}])
        self.assertEqual(fetch.call_count, 2, "Keycloak must be asked once per token while the answer is fresh")
        keys = list(keycloak_permissions._cache._data)
        self.assertEqual(len(keys), 2)
        for key in keys:
            self.assertRegex(key, r"^[0-9a-f]{64}$")
            self.assertNotIn("tok-", key, "the cache must not keep tokens as keys")

    # -- 4.150 ---------------------------------------------------------
    def test_4_150_cached_no_longer_than_a_minute_or_the_token(self) -> None:
        """
        Scenario 4.150: an answer lives at most min(60 s, the token's remaining lifetime).
        Given: a token that expires in 20 s and one that expires in 10 minutes
        When: the clock moves on
        Then: the short token's answer is reused for 18 s and asked again at 21 s; the long
              token's answer is reused for 59 s and asked again at 61 s.
        """
        now = [1000.0]
        with mock.patch.object(keycloak_permissions._cache, "clock", side_effect=lambda: now[0]), \
                mock.patch(FETCH, return_value=GRANTED) as fetch:
            for token, expires_in, still_fresh, stale in (("tok-short", 20, 18, 21), ("tok-long", 600, 59, 61)):
                fetch.reset_mock()
                start = now[0]
                list(_request(token, expires_in=expires_in).user_permissions)
                now[0] = start + still_fresh
                list(_request(token, expires_in=expires_in).user_permissions)
                self.assertEqual(fetch.call_count, 1, f"{token}: must still be cached after {still_fresh} s")
                now[0] = start + stale
                list(_request(token, expires_in=expires_in).user_permissions)
                self.assertEqual(fetch.call_count, 2, f"{token}: must be asked again after {stale} s")
                now[0] += 1000

    # -- 4.151 ---------------------------------------------------------
    def test_4_151_concurrent_requests_with_one_token_make_one_call(self) -> None:
        """
        Scenario 4.151: single-flight -- concurrent requests with one token share one UMA call.
        Given: 16 requests of one user in flight at once, and a slow Keycloak
        When: they all read their permissions
        Then: Keycloak is asked exactly once and every request gets the answer; when that one
              call fails, all 16 get the 503 outcome (AuthUnavailable) from that same call.
        """
        calls = []
        gate = threading.Event()

        def slow(token, **kwargs):
            calls.append(token)
            gate.wait(2)
            return GRANTED

        def read(_):
            return list(_request("tok-busy").user_permissions)

        with mock.patch(FETCH, side_effect=slow), ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(read, i) for i in range(16)]
            time.sleep(0.3)
            gate.set()
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(len(calls), 1, f"{len(calls)} UMA calls for one token in flight")
        self.assertTrue(all(result == GRANTED for result in results))

        reset_uma_cache()
        calls.clear()
        gate.clear()

        def failing(token, **kwargs):
            calls.append(token)
            gate.wait(2)
            raise uma_client.KeycloakUnavailable("Keycloak answered 502")

        def outcome(_):
            try:
                return list(_request("tok-busy").user_permissions)
            except AuthUnavailable as exc:
                return exc

        with mock.patch(FETCH, side_effect=failing), ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(outcome, i) for i in range(16)]
            time.sleep(0.3)
            gate.set()
            outcomes = [future.result(timeout=10) for future in futures]
        self.assertEqual(len(calls), 1, "a failing call must be shared as well, not repeated by every waiter")
        self.assertTrue(all(isinstance(result, AuthUnavailable) for result in outcomes), outcomes)


class TestCluster04y_BreakerAndRoles(_LookupTestCase):
    """4.155-4.156: Keycloak is spared while it fails; roles never cost a call."""

    # -- 4.155 ---------------------------------------------------------
    def test_4_155_uma_has_its_own_circuit_breaker(self) -> None:
        """
        Scenario 4.155: repeated UMA failures open the UMA breaker; a later probe closes it.
        Given: a breaker that opens after 3 transient failures (OIDC_CIRCUIT_BREAKER), and
               Keycloak failing
        When: three users' requests fail, a fourth user asks, then the cool-down passes and
              Keycloak answers again; separately, Keycloak names another issuer than the
              token's (KEYCLOAK_INTERNAL_URL without Keycloak's hostname settings)
        Then: the fourth request gets AuthUnavailable with a Retry-After hint and Keycloak is
              not called; after the cool-down one probe goes through and closes the breaker;
              the token endpoint's own breaker is unaffected; the issuer mismatch is a
              configuration outage (503, breaker open at once) -- never a 401 that would send
              every user into a sign-in loop.
        """
        from oauth2_authcodeflow.breaker import CLOSED, OPEN, get_breaker

        breaker_conf = {"failures": 3, "window": 30, "cooldown": 15, "config_cooldown": 300}
        with override_settings(OIDC_CIRCUIT_BREAKER=breaker_conf):
            with mock.patch(FETCH, side_effect=uma_client.KeycloakUnavailable("502")) as fetch:
                for token in ("tok-1", "tok-2", "tok-3"):
                    with self.assertRaises(AuthUnavailable):
                        list(_request(token).user_permissions)
                self.assertEqual(get_breaker(keycloak_permissions.UMA_BREAKER).state, OPEN)
                with self.assertRaises(AuthUnavailable) as blocked:
                    list(_request("tok-4").user_permissions)
                self.assertEqual(fetch.call_count, 3, "an open breaker must not call Keycloak")
                self.assertGreaterEqual(blocked.exception.retry_after, 1)
            self.assertEqual(get_breaker("token_endpoint").state, CLOSED, "UMA failures must not trip the refresh breaker")

            later = time.monotonic() + 16
            with mock.patch("oauth2_authcodeflow.breaker.monotonic", return_value=later), \
                    mock.patch(FETCH, return_value=GRANTED) as fetch:
                self.assertEqual(list(_request("tok-4").user_permissions), GRANTED)
                self.assertEqual(get_breaker(keycloak_permissions.UMA_BREAKER).state, CLOSED)

        reset_breakers()

        class _Answer:
            status_code = 401

            @staticmethod
            def json():
                return {"error": "invalid_grant", "error_description": "Invalid token issuer. Expected 'http://kc'"}

        with mock.patch.object(uma_client, "_session", return_value=mock.Mock(post=mock.Mock(return_value=_Answer()))):
            with self.assertRaises(uma_client.KeycloakUnavailable) as raised:
                uma_client.fetch_uma_permissions("tok-x")
            self.assertEqual(raised.exception.category, "config")
            with self.assertRaises(AuthUnavailable):
                list(_request("tok-x").user_permissions)
        self.assertEqual(get_breaker(keycloak_permissions.UMA_BREAKER).state, OPEN,
                         "a configuration error must open the breaker at once")

        with mock.patch.dict(os.environ, {"KEYCLOAK_INTERNAL_URL": "http://keycloak.svc:8080/"}):
            endpoints = uma_client.KeycloakEndpoints.from_environment()
        self.assertEqual(endpoints.token_url, "http://keycloak.svc:8080/realms/lex/protocol/openid-connect/token",
                         "UMA is a server-to-server call: it uses the in-cluster URL when there is one")

    # -- 4.156 ---------------------------------------------------------
    def test_4_156_roles_and_profile_come_from_the_sign_in(self) -> None:
        """
        Scenario 4.156: client roles and profile come from the validated sign-in, never from a call.
        Given: a user signed in with client roles in the access token and a display name in
               the ID token, and a session that still holds a 2.x "oidc_userinfo" with other roles
        When: the middleware has run and the permission layer builds its UserContext
        Then: request.client_roles and UserContext.client_roles are the token's roles (the
              stale session value is ignored); request.userinfo carries the profile without
              token bookkeeping (no iss/aud/sid); Keycloak's userinfo endpoint is never called.
        """
        user = get_user_model().objects.create_user(username="ada", email="ada@example.com")
        request = RequestFactory().get("/api/model_entries/x/list")
        create_oidc_session(request, user, client_roles=["admin", "editor"], claims={"name": "Ada L"})
        request.session["oidc_userinfo"] = {"client_roles": ["root"]}
        with mock.patch.object(uma_client, "fetch_userinfo") as userinfo, mock.patch(FETCH, return_value=[]):
            KeycloakPermissionsMiddleware(lambda r: HttpResponse("ok"))(request)
            self.assertEqual(sorted(request.client_roles), ["admin", "editor"])
            self.assertEqual(request.userinfo["name"], "Ada L")
            self.assertEqual(sorted(request.userinfo["client_roles"]), ["admin", "editor"])
            for bookkeeping in ("iss", "aud", "sid", "azp"):
                self.assertNotIn(bookkeeping, request.userinfo)
            context = UserContext.from_request(request)
            self.assertEqual(context.client_roles, frozenset({"admin", "editor"}),
                             "roles must come from the access token, never from an old session value")
        userinfo.assert_not_called()


class TestCluster04y_OutagesAreNotDenials(SimpleTestCase):
    """4.157: guarded() passes auth outcomes through instead of denying or allowing."""

    # -- 4.157 ---------------------------------------------------------
    def test_4_157_guarded_re_raises_auth_outages(self) -> None:
        """
        Scenario 4.157: a permission hook that hits an auth outage is neither denied nor allowed.
        Given: hooks raising AuthUnavailable, SessionRejected, the library's OIDCAuthUnavailable
               and OIDCSessionEnded, and lex's KeycloakUnavailable / KeycloakUnauthorized -- and
               one raising an ordinary error
        When: guarded() runs them with LEX_SEC_FAIL_CLOSED on and off
        Then: every auth outcome propagates (to its 503/401 answer) in both modes; the ordinary
              error still denies when fail-closed and allows in legacy mode.
        """
        outages = [
            AuthUnavailable(7), SessionRejected(), OIDCAuthUnavailable("transient", retry_after=3),
            OIDCSessionEnded("refresh_invalid_grant"), uma_client.KeycloakUnavailable("down"),
            uma_client.KeycloakUnauthorized("rejected"),
        ]
        deny, allow = PermissionResult.deny("denied"), PermissionResult.allow_all("allowed")
        for fail_closed in ("true", "false"):
            with mock.patch.dict(os.environ, {"LEX_SEC_FAIL_CLOSED": fail_closed}):
                for outage in outages:
                    with self.subTest(fail_closed=fail_closed, outage=type(outage).__name__):
                        self.assertTrue(is_auth_outage(outage))

                        def hook(_uc, outage=outage):
                            raise outage

                        with self.assertRaises(type(outage)):
                            guarded(hook, None, deny=deny, allow=allow, what="test hook")

                def broken(_uc):
                    raise RuntimeError("bug in a hook")

                expected = deny if fail_closed == "true" else allow
                self.assertIs(guarded(broken, None, deny=deny, allow=allow, what="test hook"), expected)
                self.assertFalse(is_auth_outage(RuntimeError("bug")))


class _SignedInE2E(E2ETestCase):
    """E2E with Keycloak configured for UMA and the UMA call under the test's control."""

    e2e_unpatch = {"keycloak_uma"}

    def setUp(self) -> None:
        super().setUp()
        env = mock.patch.dict(os.environ, KEYCLOAK_ENV)
        env.start()
        self.addCleanup(env.stop)
        reset_breakers()
        self.addCleanup(reset_breakers)

    def uma(self, **kwargs):
        patcher = mock.patch(FETCH, **kwargs)
        fetch = patcher.start()
        self.addCleanup(patcher.stop)
        return fetch

    def assert_still_signed_in(self):
        status_response = self.client.get("/api/auth/session")
        self.assertEqual(status_response.status_code, 200, status_response.content[:300])
        self.assertTrue(status_response.json()["authenticated"])
        self.assertIsNone(OIDCSession.objects.get(pk=self.oidc_session.pk).ended_at, "the sign-in must not end")


class TestCluster04y_KeycloakAnswersEndToEnd(_SignedInE2E):
    """4.152-4.154: what the API answers for each UMA outcome."""

    e2e_models = [KeycloakItem]

    # -- 4.152 ---------------------------------------------------------
    def test_4_152_access_denied_is_no_permission_and_the_user_stays_signed_in(self) -> None:
        """
        Scenario 4.152: Keycloak granting nothing is a permission answer, not an auth failure.
        Given: a signed-in user to whom Keycloak's UMA answers 403 access_denied
        When: the user asks for their permissions and tries to create a KeycloakItem
        Then: the permissions are an empty list (200 []); the create is refused with 403 and
              code permission_denied -- nothing is stored; the user is still signed in.
        """
        self.uma(side_effect=uma_client.KeycloakDenied("no", status=403, error="access_denied"))
        perms = self.client.get("/api/user_permissions/")
        self.assertEqual((perms.status_code, perms.json()), (200, []))
        created = self.client.post(self.url_create(KEYCLOAK), data={"label": "x"}, format="json")
        self.assertEqual(created.status_code, 403, created.content[:300])
        self.assertEqual(created.json().get("code"), "permission_denied")
        self.assertFalse(KeycloakItem.objects.exists())
        self.assert_still_signed_in()

    # -- 4.153 ---------------------------------------------------------
    def test_4_153_a_rejected_token_is_a_lost_session(self) -> None:
        """
        Scenario 4.153: a token Keycloak rejects is answered 401 session_expired -- never [].
        Given: a signed-in user whose access token Keycloak's UMA endpoint rejects (401)
        When: the user reads their permissions, and tries to create a record
        Then: both get 401 {"error": "session_expired", "login_url"} with WWW-Authenticate and
              no-store; the write is refused before the view runs, so nothing is stored.
        """
        self.uma(side_effect=uma_client.KeycloakUnauthorized("rejected", status=401))
        for response in (
            self.client.get("/api/user_permissions/"),
            self.client.post(self.url_create(KEYCLOAK), data={"label": "x"}, format="json"),
        ):
            with self.subTest(method=response.request["REQUEST_METHOD"]):
                self.assertEqual(response.status_code, 401, response.content[:300])
                self.assertEqual(response.json()["error"], "session_expired")
                self.assertEqual(response.json()["login_url"], "/oidc/authenticate")
                self.assertTrue(response["WWW-Authenticate"].startswith("Session realm="))
                self.assertEqual(response["Cache-Control"], "no-store")
        self.assertFalse(KeycloakItem.objects.exists())

    # -- 4.154 ---------------------------------------------------------
    def test_4_154_an_outage_is_503_never_an_empty_answer(self) -> None:
        """
        Scenario 4.154: Keycloak unreachable answers 503 auth_unavailable -- before the view for writes.
        Given: a signed-in user, a stored KeycloakItem, and Keycloak's UMA endpoint failing
        When: the user reads their permissions, lists KeycloakItems and creates one -- and,
              with Keycloak down again, signs out
        Then: each answer is 503 {"error": "auth_unavailable", "retry_after"} with Retry-After
              and no-store -- not [] and not an empty list; the create stores nothing (it was
              refused before the view); the user is still signed in once Keycloak is back; and
              signing out never waits for the permission lookup: it works during the outage.
        """
        KeycloakItem.objects.create(label="existing")
        fetch = self.uma(side_effect=uma_client.KeycloakUnavailable("Keycloak answered 502", status=502))
        responses = {
            "permissions": self.client.get("/api/user_permissions/"),
            "list": self.client.get(self.url_list(KEYCLOAK)),
            "create": self.client.post(self.url_create(KEYCLOAK), data={"label": "new"}, format="json"),
        }
        for name, response in responses.items():
            with self.subTest(request=name):
                self.assertEqual(response.status_code, 503, response.content[:300])
                body = response.json()
                self.assertEqual(body["error"], "auth_unavailable")
                self.assertEqual(response["Retry-After"], str(body["retry_after"]))
                self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(list(KeycloakItem.objects.values_list("label", flat=True)), ["existing"])
        fetch.side_effect = None
        fetch.return_value = GRANTED
        reset_breakers()
        reset_uma_cache()
        self.assert_still_signed_in()

        fetch.side_effect = uma_client.KeycloakUnavailable("Keycloak answered 502", status=502)
        reset_uma_cache()
        signed_out = self.client.post("/oidc/logout")
        self.assertEqual(signed_out.status_code, 302, signed_out.content[:300])
        self.assertIsNotNone(OIDCSession.objects.get(pk=self.oidc_session.pk).ended_at,
                             "the sign-out must end the sign-in even while Keycloak is down")


class TestCluster04y_FailClosedSitesPassOutagesThrough(_SignedInE2E):
    """4.158: every fail-closed site answers an auth outage with its 503/401."""

    e2e_models = [OutageItem]

    def setUp(self) -> None:
        super().setUp()
        self.uma(return_value=[])
        self.item = OutageItem.objects.create(name="kept")
        self.addCleanup(setattr, OutageItem, "outage", None)

    # -- 4.158 ---------------------------------------------------------
    def test_4_158_fail_closed_sites_pass_auth_outages_through(self) -> None:
        """
        Scenario 4.158: a permission hook that hits an auth outage is answered 503/401, not denied.
        Given: a model whose permission hooks raise the library's OIDCAuthUnavailable (what a
               hook gets when it needs the sign-in's token while Keycloak is down), with
               LEX_SEC_FAIL_CLOSED on and off
        When: the list, the detail, create, update, delete and export endpoints are used
        Then: each answers 503 auth_unavailable -- the list is not emptied, the detail not
              hidden, the export not quietly withheld, and nothing is created, changed or
              deleted; an ended sign-in (OIDCSessionEnded) is answered 401 session_expired.
        """
        requests = {
            "list": lambda: self.client.get(self.url_list(OUTAGE)),
            "detail": lambda: self.client.get(self.url_detail(OUTAGE, self.item.pk)),
            "create": lambda: self.client.post(self.url_create(OUTAGE), data={"name": "new"}, format="json"),
            "update": lambda: self.client.patch(self.url_detail(OUTAGE, self.item.pk), data={"name": "changed"},
                                                format="json"),
            "delete": lambda: self.client.delete(self.url_detail(OUTAGE, self.item.pk)),
            "export": lambda: self.client.post(self.url_export(OUTAGE), data={}, format="json"),
        }
        OutageItem.outage = OIDCAuthUnavailable("transient", retry_after=7)
        for fail_closed in ("true", "false"):
            with mock.patch.dict(os.environ, {"LEX_SEC_FAIL_CLOSED": fail_closed}):
                for name, send in requests.items():
                    with self.subTest(fail_closed=fail_closed, request=name):
                        response = send()
                        content = b"".join(response.streaming_content) if response.streaming else response.content
                        self.assertEqual(response.status_code, 503, content[:300])
                        self.assertIn(b"auth_unavailable", content)
        self.assertEqual(list(OutageItem.objects.values_list("name", flat=True)), ["kept"])

        OutageItem.outage = OIDCSessionEnded("refresh_invalid_grant")
        ended = self.client.get(self.url_detail(OUTAGE, self.item.pk))
        self.assertEqual(ended.status_code, 401, ended.content[:300])
        self.assertEqual(ended.json()["error"], "session_expired")
