"""lex.lex_app.tests.auth signs a test client in the way lex 2.5 sees a real sign-in.

Intent
------
Downstream projects' tests (atrium, armira, every customer project) signed users in with
``force_login`` plus hand-made ``session["oidc_*"]`` keys. From lex-app 2.5 (django-oauth2-codeflow
3) a request's client roles come only from its OIDC sign-in (the access token), the old keys are
not read, and a signed-in write asks Keycloak for UMA permissions before the view runs. Without a
supported helper every downstream suite breaks on upgrade -- or sends test tokens to whatever
Keycloak the developer's environment names. The shipped helper must therefore:

* give the request exactly the client roles asked for, end to end (``api/user/``);
* keep writes away from Keycloak: the UMA lookup is answered by the stub, with the session's own
  access token, and the request reaches its view -- no 401, no 503;
* hand the given UMA permissions to lex (``api/user_permissions/``);
* undo itself when the test ends: settings, patch and UMA cache -- also when the test
  method carries its own ``@override_settings``, which ends before the cleanups run;
* work in a project without Keycloak configured (no sign-in backend in its settings).

Cluster 01bh -- scenarios 1.601-1.606. Type: I.
Covers: lex/lex_app/tests/auth.py, and the re-exports in lex/test_project/tests/_e2e_test_case.py
and lex/tests/e2e/_e2e_test_case.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1bh_test_sign_in_helper.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from lex.api.keycloak import uma_client
from lex.api.middleware.keycloak_permissions import uma_permissions
from lex.lex_app.tests.auth import OIDC_BACKEND, sign_in

pytestmark = pytest.mark.init

#: Keycloak "configured" at an address that can never be reached: a write that really asked it
#: would answer 503, and a lookup that bypassed the stub would show up as a failed call.
UNREACHABLE_KEYCLOAK = {
    "KEYCLOAK_URL": "https://keycloak.invalid",
    "KEYCLOAK_REALM": "lex-helper-test",
    "OIDC_RP_CLIENT_ID": "lex-helper-test",
}

INVOICE_PERMISSIONS = [
    {"rsname": "lex_app.Invoice", "resource_set_id": "rs-invoice", "scopes": ["read", "edit"]},
]


class _Throwaway(unittest.TestCase):
    """A test case whose cleanups this test runs itself."""

    __test__ = False  # not a test of its own

    def runTest(self) -> None:  # pragma: no cover - never run
        pass


class TestCluster01bh_TestSignInHelper(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(
            username="helper@example.test", email="helper@example.test"
        )

    def test_1_601_the_signed_in_client_carries_exactly_the_given_client_roles(self) -> None:
        """Scenario 1.601: the roles reach lex end to end.

        Given a user signed in with sign_in(..., client_roles=["fund-admin", "standard"])
        When the client asks api/user/
        Then lex answers 200 with exactly those client roles and the user's e-mail
        """
        client = APIClient()
        sign_in(self, client, self.user, client_roles=["fund-admin", "standard"])

        response = client.get("/api/user/")

        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        self.assertEqual(sorted(body["client_roles"]), ["fund-admin", "standard"])
        self.assertEqual(body["email"], "helper@example.test")

    def test_1_602_a_write_is_decided_from_the_stub_never_from_keycloak(self) -> None:
        """Scenario 1.602: a signed-in write never reaches Keycloak.

        Given Keycloak configured at an address that cannot be reached, and a user signed in with sign_in
        When the client sends a write (POST to api/auth/session, a GET-only route)
        Then the UMA lookup ran once, through the stub, with the sign-in's own access token,
             and the request reached its view (405) -- not the contract's 503 or 401
        """
        client = APIClient()
        with mock.patch.dict(os.environ, UNREACHABLE_KEYCLOAK):
            row = sign_in(self, client, self.user, client_roles=["standard"])
            fetch = uma_client.fetch_uma_permissions  # the stub, while the test runs

            response = client.post("/api/auth/session", data={})

        self.assertEqual(response.status_code, 405, response.content[:300])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0], row.access_token)

    def test_1_603_the_given_uma_permissions_are_what_lex_sees(self) -> None:
        """Scenario 1.603: the stubbed UMA answer is lex's answer.

        Given Keycloak configured, and a user signed in with uma_permissions for lex_app.Invoice (read, edit)
        When the client asks api/user_permissions/
        Then lex answers exactly those permissions in its ra-rbac form
        """
        client = APIClient()
        with mock.patch.dict(os.environ, UNREACHABLE_KEYCLOAK):
            sign_in(self, client, self.user, client_roles=["standard"], uma_permissions=INVOICE_PERMISSIONS)

            response = client.get("/api/user_permissions/")

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(
            sorted(response.json(), key=lambda item: item["action"]),
            [
                {"action": "edit", "resource": "invoice", "record": {"id": "rs-invoice"}},
                {"action": "read", "resource": "invoice", "record": {"id": "rs-invoice"}},
            ],
        )

    def test_1_604_nothing_outlives_the_test(self) -> None:
        """Scenario 1.604: the helper undoes itself.

        Given another test case that signed in with sign_in, and a UMA answer cached meanwhile
        When that test case's cleanups have run
        Then the UMA lookup is lex's real function again, AUTHENTICATION_BACKENDS is as before,
             and the cached answer is gone (the next lookup asks again)
        """
        real_fetch = uma_client.fetch_uma_permissions
        backends_before = list(settings.AUTHENTICATION_BACKENDS)
        inner = _Throwaway()

        with mock.patch.dict(os.environ, UNREACHABLE_KEYCLOAK):
            row = sign_in(inner, APIClient(), self.user, client_roles=["admin"], uma_permissions=INVOICE_PERMISSIONS)
            self.assertIsNot(uma_client.fetch_uma_permissions, real_fetch)
            self.assertIn(OIDC_BACKEND, settings.AUTHENTICATION_BACKENDS)
            self.assertEqual(uma_permissions(row.access_token)[0]["rsname"], "lex_app.Invoice")  # now cached

            inner.doCleanups()

            self.assertIs(uma_client.fetch_uma_permissions, real_fetch)
            self.assertEqual(list(settings.AUTHENTICATION_BACKENDS), backends_before)
            with mock.patch.object(uma_client, "fetch_uma_permissions", return_value=[]) as asked_again:
                self.assertEqual(uma_permissions(row.access_token), [])
            self.assertEqual(asked_again.call_count, 1)

    def test_1_606_a_test_method_s_own_override_settings_does_not_leak(self) -> None:
        """Scenario 1.606: sign_in() inside a test method decorated with @override_settings.

        Given another test case whose test method carries @override_settings(LEX_HELPER_PROBE="method")
              and signs in with sign_in
        When that test case has run: its method (the decorator's override ends with it), then its cleanups
        Then the settings are exactly those from before it: no LEX_HELPER_PROBE, and the sign-in
             backend gone from AUTHENTICATION_BACKENDS
        """
        # A leak here would outlive this test; put the settings back whatever the helper did.
        self.addCleanup(setattr, settings, "_wrapped", settings._wrapped)
        backends_before = list(settings.AUTHENTICATION_BACKENDS)
        user = self.user
        seen = {}

        class _Decorated(unittest.TestCase):
            @override_settings(LEX_HELPER_PROBE="method")
            def runTest(inner) -> None:
                sign_in(inner, APIClient(), user, client_roles=["standard"])
                seen["probe"] = settings.LEX_HELPER_PROBE
                seen["backend"] = OIDC_BACKEND in settings.AUTHENTICATION_BACKENDS

        result = unittest.TestResult()
        _Decorated().run(result)

        self.assertEqual(result.errors + result.failures, [])
        self.assertEqual(seen, {"probe": "method", "backend": True})  # both were in force inside
        self.assertFalse(hasattr(settings, "LEX_HELPER_PROBE"))
        self.assertEqual(list(settings.AUTHENTICATION_BACKENDS), backends_before)

    def test_1_605_force_login_and_the_old_session_keys_carry_no_roles(self) -> None:
        """Scenario 1.605: why the helper exists.

        Given a user signed in the pre-2.5 way: force_login plus a hand-made session["oidc_userinfo"]
        When the client asks api/user/
        Then lex answers no client roles: from 2.5 only an OIDC sign-in carries them
        """
        client = APIClient()
        client.force_login(self.user)
        session = client.session
        session["oidc_userinfo"] = {"email": "helper@example.test", "client_roles": ["admin"]}
        session.save()

        response = client.get("/api/user/")

        # Where a Keycloak authorization server is configured (as in the release gate), 2.5 refuses a
        # session that has no OIDC sign-in outright; with none configured it answers without roles.
        # Either way the hand-made session["oidc_userinfo"] grants nothing.
        if response.status_code == 401:
            self.assertEqual(response.json()["error"], "session_expired", response.content[:300])
        else:
            self.assertEqual(response.status_code, 200, response.content[:300])
            self.assertEqual(response.json()["client_roles"], [])
