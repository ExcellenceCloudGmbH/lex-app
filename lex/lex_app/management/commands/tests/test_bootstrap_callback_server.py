from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from dotenv import dotenv_values
from lex.core.management.commands.bootstrap_callback_server import build_env_lines


class BootstrapCallbackServerEnvFormattingTests(TestCase):
    def test_build_env_lines_round_trips_special_character_client_id(self):
        payload = {
            "keycloak_url": "https://auth.example.com",
            "realm": "lex",
            "client_id": "LEX/transactionmonitoringv1[594]",
            "client_secret": "super-secret",
            "client_uuid": "client-uuid",
        }

        env_lines = build_env_lines(payload)

        self.assertIn('OIDC_RP_CLIENT_ID="LEX/transactionmonitoringv1[594]"', env_lines)

        with TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
            values = dotenv_values(str(env_file))

        self.assertEqual(values["OIDC_RP_CLIENT_ID"], "LEX/transactionmonitoringv1[594]")

    def test_build_env_lines_accepts_nested_client_payload_variants(self):
        payload = {
            "keycloakUrl": "https://auth.example.com",
            "realmName": "lex",
            "client": {
                "clientId": "LEX/transactionmonitoringv1[594]",
                "secret": "super-secret",
                "id": "client-uuid",
            },
        }

        env_lines = build_env_lines(payload)

        with TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
            values = dotenv_values(str(env_file))

        self.assertEqual(values["KEYCLOAK_URL"], "https://auth.example.com")
        self.assertEqual(values["KEYCLOAK_REALM"], "lex")
        self.assertEqual(values["OIDC_RP_CLIENT_ID"], "LEX/transactionmonitoringv1[594]")
        self.assertEqual(values["OIDC_RP_CLIENT_SECRET"], "super-secret")
        self.assertEqual(values["OIDC_RP_CLIENT_UUID"], "client-uuid")
