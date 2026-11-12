"""
Unit tests for ``lex/authentication/management/commands/_keycloak_env.py``.

What is tested:
  * The Keycloak commands connect to whatever realm the instance was given in
    KEYCLOAK_REALM (or KEYCLOAK_REALM_NAME) — never a built-in one — as the
    instance's own client.
  * Missing settings fail with a CommandError naming them.

How it runs:
  Pure logic — Keycloak is never contacted: KeycloakOpenIDConnection is patched
  and its arguments inspected.
"""

from unittest import mock

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from lex.authentication.management.commands import _keycloak_env
from lex.authentication.management.commands._keycloak_env import instance_keycloak_connection

ENV = {
    "KEYCLOAK_URL": "https://auth.example",
    "KEYCLOAK_REALM": "lpalpha",
    "OIDC_RP_CLIENT_ID": "LEX-acme-1",
    "OIDC_RP_CLIENT_SECRET": "secret",
    "OIDC_RP_CLIENT_UUID": "uuid-1",
}


class InstanceKeycloakConnectionTests(SimpleTestCase):

    def test_connects_to_the_instances_realm_as_its_client(self):
        with mock.patch.dict("os.environ", ENV, clear=True), \
                mock.patch.object(_keycloak_env, "KeycloakOpenIDConnection") as connection:
            _, client_uuid = instance_keycloak_connection()
        kwargs = connection.call_args.kwargs
        self.assertEqual(kwargs["realm_name"], "lpalpha")
        self.assertEqual(kwargs["server_url"], "https://auth.example")
        self.assertEqual((kwargs["client_id"], kwargs["client_secret_key"]), ("LEX-acme-1", "secret"))
        self.assertEqual(client_uuid, "uuid-1")

    def test_keycloak_realm_name_is_accepted_too(self):
        env = {k: v for k, v in ENV.items() if k != "KEYCLOAK_REALM"} | {"KEYCLOAK_REALM_NAME": "lex"}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch.object(_keycloak_env, "KeycloakOpenIDConnection") as connection:
            instance_keycloak_connection()
        self.assertEqual(connection.call_args.kwargs["realm_name"], "lex")

    def test_missing_settings_are_named(self):
        with mock.patch.dict("os.environ", {"KEYCLOAK_URL": "https://auth.example"}, clear=True):
            with self.assertRaisesMessage(CommandError, "KEYCLOAK_REALM, OIDC_RP_CLIENT_ID"):
                instance_keycloak_connection()
