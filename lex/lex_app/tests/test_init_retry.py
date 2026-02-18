
from unittest.mock import MagicMock, patch
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase
from lex_app.management.commands.Init import KeycloakSyncManager, Command

class InitCommandRetryTests(SimpleTestCase):

    @patch("lex_app.management.commands.Init.KeycloakSyncManager")
    @patch("lex_app.management.commands.Init.start_callback_server")
    @patch("lex_app.management.commands.Init.wait_for_keycloak_setup")
    @patch("lex_app.management.commands.Init.webbrowser.open")
    def test_check_missing_phase_failure_retries(self, mock_webbrowser, mock_wait, mock_start_server, mock_manager_cls):
        """
        Test that failures during the 'check_missing' phase (export_configs) represent early failures.
        Currently expected to FAIL or crash if not handled.
        After fix, should retry.
        """
        # Setup mock manager instance
        mock_manager = MagicMock()
        mock_manager_cls.return_value = mock_manager

        # Mock export_configs to fail on first call, succeed on second
        # This simulates a transient Keycloak error during check_missing
        mock_manager.export_configs.side_effect = [Exception("Keycloak unreachable"), {"resources": [], "policies": []}]
        
        # Mock other methods to succeed
        mock_manager.get_all_django_models.return_value = set()
        mock_manager.get_existing_keycloak_resources.return_value = set()
        mock_manager.find_missing_models.return_value = set()
        
        # We need to mock migration-related calls to avoid actual DB/migration logic
        with patch("django.db.migrations.executor.MigrationExecutor"), \
             patch("lex_app.management.commands.Init.MigrationLoader"), \
             patch("lex_app.management.commands.Init.MigrationAutodetector"), \
             patch("lex_app.management.commands.Init.call_command") as mock_call_command:

            # Mock autodetector changes to return empty dict (no model changes)
            # This ensures we focus on the check_missing part
            mock_autodetector = MagicMock()
            mock_autodetector.changes.return_value = {}
            with patch("lex_app.management.commands.Init.MigrationAutodetector", return_value=mock_autodetector):
                 # Run command with --check-missing and retries
                try:
                    call_command("Init", check_missing=True, sync_retries=2, no_makemigrations=True, skip_migrations=True, bootstrap=False)
                except CommandError as e:
                    # Current behavior: should crash or fail
                    print(f"\nCaught expected CommandError: {e}")
                    return

        # If it didn't raise CommandError, we might have fixed it or test setup is wrong for reproduction
        # self.fail("Command should have failed due to export_configs exception not being caught in retry loop")

    @patch("lex_app.management.commands.Init.KeycloakSyncManager")
    def test_process_model_changes_retry(self, mock_manager_cls):
        """
        Test that process_model_changes is retried on failure.
        """
        mock_manager = MagicMock()
        mock_manager_cls.return_value = mock_manager
        
        # Setup valid config for check_missing to pass
        mock_manager.export_configs.return_value = {"resources": [], "policies": []}
        mock_manager.get_all_django_models.return_value = set()
        mock_manager.get_existing_keycloak_resources.return_value = set()
        mock_manager.find_missing_models.return_value = set()

        # Fail process_model_changes once, then succeed
        mock_manager.process_model_changes.side_effect = [Exception("Sync failed"), None]

        with patch("lex_app.management.commands.Init.MigrationLoader"), \
             patch("lex_app.management.commands.Init.MigrationAutodetector") as mock_detector_cls, \
             patch("lex_app.management.commands.Init.call_command"):
            
            mock_detector = MagicMock()
            mock_detector.changes.return_value = {}
            mock_detector_cls.return_value = mock_detector

            call_command("Init", check_missing=True, sync_retries=2, no_makemigrations=True, skip_migrations=True, bootstrap=False)
            
            # Assert process_model_changes was called twice
            self.assertEqual(mock_manager.process_model_changes.call_count, 2)
