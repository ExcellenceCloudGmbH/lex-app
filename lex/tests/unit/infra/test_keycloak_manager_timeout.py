from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from lex.api.views.authentication.KeycloakManager import (
    DEFAULT_KEYCLOAK_AUTHZ_REQUEST_TIMEOUT,
    KeycloakManager,
)


class KeycloakManagerTimeoutTests(SimpleTestCase):
    def build_manager(self):
        manager = object.__new__(KeycloakManager)
        manager.realm_name = "lex"
        manager.client_uuid = "client-uuid"
        manager.oidc = None
        manager.conn = None
        manager.uma = None
        manager.admin = MagicMock()
        manager.admin.connection.token = {"access_token": "token"}
        manager.admin.connection.server_url = "https://keycloak.example.com"
        return manager

    @patch("lex.api.views.authentication.KeycloakManager.requests.post")
    def test_import_authorization_settings_uses_longer_default_read_timeout(self, mock_post):
        manager = self.build_manager()
        mock_post.return_value.status_code = 204

        success = manager.import_authorization_settings({"resources": [], "policies": []})

        self.assertTrue(success)
        self.assertEqual(
            mock_post.call_args.kwargs["timeout"],
            DEFAULT_KEYCLOAK_AUTHZ_REQUEST_TIMEOUT,
        )

    @override_settings(KEYCLOAK_AUTHZ_REQUEST_TIMEOUT="5,180")
    @patch("lex.api.views.authentication.KeycloakManager.requests.get")
    def test_export_authorization_settings_honors_connect_and_read_timeout_override(self, mock_get):
        manager = self.build_manager()
        response = MagicMock(status_code=200)
        response.json.return_value = {"resources": [], "policies": []}
        mock_get.return_value = response

        exported = manager.export_authorization_settings()

        self.assertEqual(exported, {"resources": [], "policies": []})
        self.assertEqual(mock_get.call_args.kwargs["timeout"], (5.0, 180.0))

    @override_settings(KEYCLOAK_AUTHZ_REQUEST_TIMEOUT=180)
    @patch("lex.api.views.authentication.KeycloakManager.requests.post")
    def test_import_authorization_settings_accepts_single_timeout_override(self, mock_post):
        manager = self.build_manager()
        mock_post.return_value.status_code = 204

        success = manager.import_authorization_settings({"resources": [], "policies": []})

        self.assertTrue(success)
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 180.0)

    @patch("lex.api.views.authentication.KeycloakManager.requests.post")
    def test_import_authorization_settings_records_gateway_timeout_error_details(self, mock_post):
        manager = self.build_manager()
        mock_post.return_value = MagicMock(status_code=504, text="Gateway time-out")

        success = manager.import_authorization_settings({"resources": [], "policies": []})

        self.assertFalse(success)
        self.assertEqual(
            manager.last_authz_import_error,
            {
                "kind": "gateway_timeout",
                "status_code": 504,
                "response_text": "Gateway time-out",
            },
        )

    def test_resolve_client_uuid_uses_exact_special_character_client_id_when_uuid_missing(self):
        manager = self.build_manager()
        manager.admin.get_client_id.return_value = "resolved-uuid"

        resolved = manager._resolve_client_uuid("LEX/transactionmonitoringv1[594]", None)

        self.assertEqual(resolved, "resolved-uuid")
        manager.admin.get_client_id.assert_called_once_with("LEX/transactionmonitoringv1[594]")

    def test_resolve_client_uuid_replaces_mismatched_uuid_with_exact_client_id_match(self):
        manager = self.build_manager()
        manager.admin.get_client.return_value = {"clientId": "LEX"}
        manager.admin.get_client_id.return_value = "resolved-uuid"

        resolved = manager._resolve_client_uuid("LEX/transactionmonitoringv1[594]", "uuid-for-lex")

        self.assertEqual(resolved, "resolved-uuid")
        manager.admin.get_client.assert_called_once_with("uuid-for-lex")
        manager.admin.get_client_id.assert_called_once_with("LEX/transactionmonitoringv1[594]")

    def test_resolve_client_uuid_keeps_matching_uuid_without_lookup(self):
        manager = self.build_manager()
        manager.admin.get_client.return_value = {"clientId": "LEX/transactionmonitoringv1[594]"}

        resolved = manager._resolve_client_uuid("LEX/transactionmonitoringv1[594]", "resolved-uuid")

        self.assertEqual(resolved, "resolved-uuid")
        manager.admin.get_client.assert_called_once_with("resolved-uuid")
        manager.admin.get_client_id.assert_not_called()
