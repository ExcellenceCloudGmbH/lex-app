"""The Streamlit token carries the caller's own Keycloak permissions.

Intent
------
``StreamlitTokenView`` (lex/authentication/views/token_views.py) hands a Streamlit dashboard
the token it authenticates with. Before lex-app 2.5 the view minted that token itself and
embedded the caller's UMA permissions in it -- and imported ``KeycloakManager`` relative to
its own package, where no such module exists, so the ImportError was swallowed and every
Streamlit token carried no permissions at all (the bug 4.163 was written for).

Since lex-app 2.5 (django-oauth2-codeflow 3) the view mints nothing: it hands out the
caller's *own* Keycloak access token, read from the caller's sign-in. Whatever Keycloak
grants that token -- roles in ``resource_access``, UMA permissions asked for with it -- is
therefore the caller's, and never another user's. 4.163 keeps that intent: two users signed
in at once each get exactly their own token, with their own client roles in it, and the view
asks Keycloak for nobody's permissions on its own.

Cluster 4aa -- scenario 4.163. Type: E.
Covers: lex/authentication/views/token_views.py (StreamlitTokenView.post).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4aa_streamlit_token_permissions.py -v
"""

from __future__ import annotations

from unittest import mock

import jwt
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from lex.lex_app.tests.auth import UMA_FETCH, sign_in

TOKEN_URL = "/api/auth/streamlit-token/"


class TestCluster04aa_StreamlitTokenPermissions(TestCase):
    """4.163: the permissions in a Streamlit token are the caller's, from Keycloak."""

    def _signed_in(self, username, roles):
        user = get_user_model().objects.create_user(
            username=f"cluster4aa_{username}", email=f"{username}@example.com", password="x"
        )
        client = APIClient()
        row = sign_in(self, client, user, client_roles=roles)
        return client, row.access_token

    # -- 4.163 -------------------------------------------------------------
    def test_4_163_streamlit_token_asks_keycloak_with_the_callers_own_token(self) -> None:
        """
        Scenario 4.163: the dashboard token is the caller's own sign-in token.
        Given: alice (client role fund-admin) and bob (client role viewer), both signed in
        When:  each POSTs api/auth/streamlit-token/
        Then:  each gets exactly the access token of their own sign-in -- so the roles in it,
               and the UMA permissions Keycloak grants it, are theirs -- and the view itself
               asks Keycloak for no permissions (nothing is minted, nothing is embedded).
        """
        alice, alice_token = self._signed_in("alice", ["fund-admin"])
        bob, bob_token = self._signed_in("bob", ["viewer"])
        self.assertNotEqual(alice_token, bob_token, "precondition: two distinct sign-ins")

        with mock.patch(UMA_FETCH) as uma:
            answers = {name: client.post(TOKEN_URL) for name, client in (("alice", alice), ("bob", bob))}
        uma.assert_not_called()

        for name, expected, roles in (("alice", alice_token, ["fund-admin"]), ("bob", bob_token, ["viewer"])):
            with self.subTest(caller=name):
                response = answers[name]
                self.assertEqual(response.status_code, 200, response.content[:300])
                token = response.json()["token"]
                self.assertEqual(token, expected, "the caller's own sign-in token, never another user's")
                claims = jwt.decode(token, options={"verify_signature": False})
                self.assertEqual(
                    [r for access in claims["resource_access"].values() for r in access["roles"]],
                    roles,
                    "the token carries the caller's own client roles",
                )
                self.assertEqual(response["Cache-Control"], "no-store")
