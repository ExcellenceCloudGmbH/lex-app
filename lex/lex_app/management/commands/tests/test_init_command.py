"""
Tests for the init management command's argument parsing and migration forwarding.
"""
import json
from io import StringIO
from unittest import TestCase
from unittest.mock import patch, MagicMock, call

from django.core.management.base import CommandError
from lex.lex_app.management.commands.init import (
    Command,
    KeycloakSyncManager,
    _format_keycloak_import_error_details,
)


def _mock_sync_manager():
    manager = MagicMock(unsafe=True)
    manager.assert_client_is_safe_for_init.return_value = {}
    return manager


# ---------------------------------------------------------------------------
# _parse_extra_args
# ---------------------------------------------------------------------------
class ParseExtraArgsTest(TestCase):
    """Tests for Command._parse_extra_args()"""

    def test_empty_string(self):
        pos, opts = Command._parse_extra_args('')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {})

    def test_whitespace_only(self):
        pos, opts = Command._parse_extra_args('   ')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {})

    # -- boolean flags --------------------------------------------------------
    def test_single_boolean_flag(self):
        pos, opts = Command._parse_extra_args('--merge')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'merge': True})

    def test_hyphenated_flag_converted_to_underscore(self):
        pos, opts = Command._parse_extra_args('--run-syncdb')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'run_syncdb': True})

    def test_multiple_boolean_flags(self):
        pos, opts = Command._parse_extra_args('--fake --run-syncdb')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'fake': True, 'run_syncdb': True})

    # -- positional args ------------------------------------------------------
    def test_single_positional(self):
        pos, opts = Command._parse_extra_args('myapp')
        self.assertEqual(pos, ['myapp'])
        self.assertEqual(opts, {})

    def test_multiple_positionals(self):
        pos, opts = Command._parse_extra_args('myapp 0001')
        self.assertEqual(pos, ['myapp', '0001'])
        self.assertEqual(opts, {})

    # -- --key value syntax ---------------------------------------------------
    def test_flag_with_space_value(self):
        pos, opts = Command._parse_extra_args('--database default')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'database': 'default'})

    # -- --key=value syntax ---------------------------------------------------
    def test_flag_with_equals_value(self):
        pos, opts = Command._parse_extra_args('--database=GCP')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'database': 'GCP'})

    def test_flag_with_equals_and_hyphen(self):
        pos, opts = Command._parse_extra_args('--some-flag=some-value')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'some_flag': 'some-value'})

    def test_equals_and_space_produce_same_result(self):
        """--database=GCP and --database GCP give identical output."""
        pos1, opts1 = Command._parse_extra_args('--database=GCP')
        pos2, opts2 = Command._parse_extra_args('--database GCP')
        self.assertEqual(pos1, pos2)
        self.assertEqual(opts1, opts2)

    # -- quoted values --------------------------------------------------------
    def test_quoted_values_preserved(self):
        pos, opts = Command._parse_extra_args('--name "my migration"')
        self.assertEqual(opts, {'name': 'my migration'})

    # -- mixed / complex combos -----------------------------------------------
    def test_merge_and_empty_with_app(self):
        pos, opts = Command._parse_extra_args('--merge --empty myapp')
        self.assertEqual(opts, {'merge': True, 'empty': 'myapp'})
        self.assertEqual(pos, [])

    def test_fake_with_app_and_migration(self):
        pos, opts = Command._parse_extra_args('--fake myapp 0001')
        self.assertEqual(opts, {'fake': 'myapp'})
        self.assertEqual(pos, ['0001'])

    def test_complex_combination(self):
        pos, opts = Command._parse_extra_args('--fake --database=default myapp 0003')
        self.assertEqual(opts, {'fake': True, 'database': 'default'})
        self.assertEqual(pos, ['myapp', '0003'])

    def test_equals_value_with_extra_flags(self):
        pos, opts = Command._parse_extra_args('--database=GCP --run-syncdb')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'database': 'GCP', 'run_syncdb': True})


# ---------------------------------------------------------------------------
# execute_migrations forwarding
# ---------------------------------------------------------------------------
class ExecuteMigrationsForwardingTest(TestCase):
    """Tests that execute_migrations correctly forwards extra args to call_command."""

    def setUp(self):
        self.cmd = Command()
        self.cmd.stdout = StringIO()
        self.cmd.stderr = StringIO()

    @patch('lex.lex_app.management.commands.init.call_command')
    def test_no_extra_args(self, mock_call):
        mock_call.return_value = None
        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=False):
            self.cmd.execute_migrations(verbosity=1, create_new=True)

        mock_call.assert_called_once_with(
            'makemigrations',
            verbosity=1,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            no_input=True,
        )

    @patch('lex.lex_app.management.commands.init.call_command')
    def test_makemigrations_extra_merge(self, mock_call):
        mock_call.return_value = None
        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=False):
            self.cmd.execute_migrations(
                verbosity=1, create_new=True,
                makemigrations_extra='--merge',
            )

        mock_call.assert_called_once_with(
            'makemigrations',
            verbosity=1,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            no_input=True,
            merge=True,
        )

    @patch('lex.lex_app.management.commands.init.call_command')
    def test_migrate_extra_run_syncdb(self, mock_call):
        mock_call.return_value = None
        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=True):
            self.cmd.execute_migrations(
                verbosity=1, create_new=False,
                migrate_extra='--run-syncdb',
            )

        mock_call.assert_called_once_with(
            'migrate',
            verbosity=1,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            run_syncdb=True,
        )

    @patch('lex.lex_app.management.commands.init.call_command')
    def test_migrate_extra_database_equals(self, mock_call):
        """--migrate-args='--database=GCP' forwards correctly."""
        mock_call.return_value = None
        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=True):
            self.cmd.execute_migrations(
                verbosity=1, create_new=False,
                migrate_extra='--database=GCP',
            )

        mock_call.assert_called_once_with(
            'migrate',
            verbosity=1,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            database='GCP',
        )

    @patch('lex.lex_app.management.commands.init.call_command')
    def test_both_extra_args_forwarded(self, mock_call):
        mock_call.return_value = None
        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=True):
            self.cmd.execute_migrations(
                verbosity=2, create_new=True,
                makemigrations_extra='--empty myapp',
                migrate_extra='--run-syncdb',
            )

        self.assertEqual(mock_call.call_count, 2)

        mm_call = mock_call.call_args_list[0]
        self.assertEqual(mm_call, call(
            'makemigrations',
            verbosity=2,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            no_input=True,
            empty='myapp',
        ))

        mig_call = mock_call.call_args_list[1]
        self.assertEqual(mig_call, call(
            'migrate',
            verbosity=2,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            run_syncdb=True,
        ))

    @patch('lex.lex_app.management.commands.init.call_command')
    def test_skip_makemigrations(self, mock_call):
        mock_call.return_value = None
        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=True):
            self.cmd.execute_migrations(verbosity=1, create_new=False)

        mock_call.assert_called_once()
        self.assertEqual(mock_call.call_args_list[0][0][0], 'migrate')

    @patch('lex.lex_app.management.commands.init.call_command')
    def test_migration_failure_raises_command_error(self, mock_call):
        mock_call.side_effect = Exception('migration boom')
        with self.assertRaises(CommandError):
            self.cmd.execute_migrations(verbosity=1, create_new=True)


class BootstrapArgumentDefaultsTest(TestCase):
    def test_bootstrap_defaults_to_false(self):
        parser = Command().create_parser("manage.py", "init")
        options = parser.parse_args([])

        self.assertFalse(options.bootstrap)

    def test_bootstrap_can_be_enabled_explicitly(self):
        parser = Command().create_parser("manage.py", "init")
        options = parser.parse_args(["--bootstrap"])

        self.assertTrue(options.bootstrap)


class InitCommandKeycloakRetryBehaviorTest(TestCase):
    def setUp(self):
        self.cmd = Command()
        self.cmd.stdout = StringIO()
        self.cmd.stderr = StringIO()

    @patch("lex.lex_app.management.commands.init.KeycloakSyncManager")
    @patch("lex.lex_app.management.commands.init.MigrationAutodetector")
    @patch("lex.lex_app.management.commands.init.MigrationLoader")
    @patch("lex.lex_app.management.commands.init.ProjectState.from_apps")
    @patch("lex.lex_app.management.commands.init.call_command")
    def test_handle_uses_selected_database_for_createcachetable(
        self,
        mock_call_command,
        mock_from_apps,
        mock_loader_cls,
        mock_autodetector_cls,
        mock_manager_cls,
    ):
        mock_manager_cls.return_value = _mock_sync_manager()
        self.cmd.check_unapplied_migrations = MagicMock(return_value=False)

        mock_loader = MagicMock()
        mock_loader.graph = MagicMock()
        mock_loader.project_state.return_value = MagicMock()
        mock_loader_cls.return_value = mock_loader
        mock_from_apps.return_value = MagicMock()

        mock_autodetector = MagicMock()
        mock_autodetector.changes.return_value = {}
        mock_autodetector_cls.return_value = mock_autodetector

        self.cmd.handle(
            dry_run=True,
            preserve_renamed_permissions=True,
            check_missing=False,
            bootstrap=False,
            skip_migrations=False,
            migration_verbosity=1,
            no_makemigrations=True,
            ensure_default_authz=False,
            sync_retries=1,
            makemigrations_args="",
            migrate_args="--database=local",
        )

        self.assertEqual(mock_call_command.call_args_list, [call("createcachetable", database="local")])

    @patch("lex.lex_app.management.commands.init.settings")
    @patch("lex.lex_app.management.commands.init.KeycloakSyncManager")
    @patch("lex.lex_app.management.commands.init.MigrationAutodetector")
    @patch("lex.lex_app.management.commands.init.MigrationLoader")
    @patch("lex.lex_app.management.commands.init.ProjectState.from_apps")
    @patch("lex.lex_app.management.commands.init.call_command")
    def test_handle_creates_postgres_database_before_cache_table(
        self,
        mock_call_command,
        mock_from_apps,
        mock_loader_cls,
        mock_autodetector_cls,
        mock_manager_cls,
        mock_settings,
    ):
        mock_manager_cls.return_value = _mock_sync_manager()
        self.cmd.check_unapplied_migrations = MagicMock(return_value=False)

        mock_loader = MagicMock()
        mock_loader.graph = MagicMock()
        mock_loader.project_state.return_value = MagicMock()
        mock_loader_cls.return_value = mock_loader
        mock_from_apps.return_value = MagicMock()

        mock_autodetector = MagicMock()
        mock_autodetector.changes.return_value = {}
        mock_autodetector_cls.return_value = mock_autodetector

        mock_settings.DATABASES = {
            "default": {"ENGINE": "django.db.backends.postgresql_psycopg2"},
        }

        self.cmd.handle(
            dry_run=True,
            preserve_renamed_permissions=True,
            check_missing=False,
            bootstrap=False,
            skip_migrations=False,
            migration_verbosity=1,
            no_makemigrations=True,
            ensure_default_authz=False,
            sync_retries=1,
            makemigrations_args="",
            migrate_args="",
        )

        self.assertEqual(
            mock_call_command.call_args_list,
            [
                call("create_db", database="default"),
                call("createcachetable", database="default"),
            ],
        )

    @patch("lex.lex_app.management.commands.init.KeycloakSyncManager")
    @patch("lex.lex_app.management.commands.init.MigrationAutodetector")
    @patch("lex.lex_app.management.commands.init.MigrationLoader")
    @patch("lex.lex_app.management.commands.init.ProjectState.from_apps")
    @patch("lex.lex_app.management.commands.init.call_command")
    def test_handle_wraps_unicode_db_errors(
        self,
        mock_call_command,
        mock_from_apps,
        mock_loader_cls,
        mock_autodetector_cls,
        mock_manager_cls,
    ):
        mock_manager_cls.return_value = _mock_sync_manager()

        mock_loader = MagicMock()
        mock_loader.graph = MagicMock()
        mock_loader.project_state.return_value = MagicMock()
        mock_loader_cls.return_value = mock_loader
        mock_from_apps.return_value = MagicMock()

        mock_autodetector = MagicMock()
        mock_autodetector.changes.return_value = {}
        mock_autodetector_cls.return_value = mock_autodetector

        mock_call_command.side_effect = UnicodeDecodeError(
            "utf-8",
            b"abc\xbb",
            3,
            4,
            "invalid start byte",
        )

        with self.assertRaises(CommandError) as exc_info:
            self.cmd.handle(
                dry_run=True,
                preserve_renamed_permissions=True,
                check_missing=False,
                bootstrap=False,
                skip_migrations=False,
                migration_verbosity=1,
                no_makemigrations=True,
                ensure_default_authz=False,
                sync_retries=1,
                makemigrations_args="",
                migrate_args="",
            )

        self.assertIn("Database connection failed while psycopg2/libpq was decoding", str(exc_info.exception))
        self.assertIn("Check whether any PostgreSQL credential source contains non-UTF-8 bytes.", str(exc_info.exception))

    @patch("lex.lex_app.management.commands.init.KeycloakSyncManager")
    @patch("lex.lex_app.management.commands.init.MigrationAutodetector")
    @patch("lex.lex_app.management.commands.init.MigrationLoader")
    @patch("lex.lex_app.management.commands.init.ProjectState.from_apps")
    def test_handle_continues_after_max_retries_on_gateway_timeout(
        self,
        mock_from_apps,
        mock_loader_cls,
        mock_autodetector_cls,
        mock_manager_cls,
    ):
        mock_manager = _mock_sync_manager()
        mock_manager.kc_manager.last_authz_import_error = {
            "kind": "gateway_timeout",
            "status_code": 504,
        }
        mock_manager.process_model_changes.side_effect = [
            Exception("Keycloak import_authorization_settings returned False"),
            Exception("Keycloak import_authorization_settings returned False"),
        ]
        mock_manager_cls.return_value = mock_manager

        mock_loader = MagicMock()
        mock_loader.graph = MagicMock()
        mock_loader.project_state.return_value = MagicMock()
        mock_loader_cls.return_value = mock_loader
        mock_from_apps.return_value = MagicMock()

        mock_autodetector = MagicMock()
        mock_autodetector.changes.return_value = {}
        mock_autodetector_cls.return_value = mock_autodetector

        self.cmd.handle(
            dry_run=False,
            preserve_renamed_permissions=True,
            check_missing=False,
            bootstrap=False,
            skip_migrations=True,
            migration_verbosity=1,
            no_makemigrations=True,
            ensure_default_authz=False,
            sync_retries=2,
            makemigrations_args="",
            migrate_args="",
        )

        self.assertEqual(mock_manager.process_model_changes.call_count, 2)
        self.assertIn(
            "continuing init without aborting",
            self.cmd.stderr.getvalue(),
        )

    @patch("lex.lex_app.management.commands.init.KeycloakSyncManager")
    @patch("lex.lex_app.management.commands.init.MigrationAutodetector")
    @patch("lex.lex_app.management.commands.init.MigrationLoader")
    @patch("lex.lex_app.management.commands.init.ProjectState.from_apps")
    def test_handle_still_raises_for_non_timeout_sync_failures(
        self,
        mock_from_apps,
        mock_loader_cls,
        mock_autodetector_cls,
        mock_manager_cls,
    ):
        mock_manager = _mock_sync_manager()
        mock_manager.kc_manager.last_authz_import_error = {
            "kind": "http_error",
            "status_code": 500,
        }
        mock_manager.process_model_changes.side_effect = [
            Exception("sync failed"),
            Exception("sync failed"),
        ]
        mock_manager_cls.return_value = mock_manager

        mock_loader = MagicMock()
        mock_loader.graph = MagicMock()
        mock_loader.project_state.return_value = MagicMock()
        mock_loader_cls.return_value = mock_loader
        mock_from_apps.return_value = MagicMock()

        mock_autodetector = MagicMock()
        mock_autodetector.changes.return_value = {}
        mock_autodetector_cls.return_value = mock_autodetector

        with self.assertRaises(CommandError):
            self.cmd.handle(
                dry_run=False,
                preserve_renamed_permissions=True,
                check_missing=False,
                bootstrap=False,
                skip_migrations=True,
                migration_verbosity=1,
                no_makemigrations=True,
                ensure_default_authz=False,
                sync_retries=2,
                makemigrations_args="",
                migrate_args="",
            )


class KeycloakSyncManagerRolePolicyTest(TestCase):
    def build_manager(self):
        manager = KeycloakSyncManager.__new__(KeycloakSyncManager)
        manager.kc_manager = MagicMock()
        manager.kc_manager.client_uuid = "client-uuid"
        manager.default_scopes = ["list", "read", "create", "edit", "delete", "export"]
        manager.exported_configs = None
        return manager

    def test_ensure_client_role_policies_includes_extra_client_roles(self):
        manager = self.build_manager()
        manager.kc_manager.admin.get_client_roles.return_value = [
            {"name": "admin", "id": "role-admin"},
            {"name": "standard", "id": "role-standard"},
            {"name": "view-only", "id": "role-view"},
            {"name": "auditor", "id": "role-auditor"},
            {"name": "manage-client", "id": "role-manage-client"},
            {"name": "uma_protection", "id": "role-uma-protection"},
        ]

        auth_config = {"policies": []}
        role_names = manager.ensure_client_role_policies(auth_config)

        self.assertEqual(role_names, ["admin", "standard", "view-only", "auditor"])
        self.assertCountEqual(
            [policy["name"] for policy in auth_config["policies"]],
            ["Policy - admin", "Policy - standard", "Policy - view-only", "Policy - auditor"],
        )
        for policy in auth_config["policies"]:
            self.assertNotIn("roles", policy)
        manager.kc_manager.admin.create_client_role.assert_not_called()

    def test_ensure_client_role_policies_normalizes_existing_role_references(self):
        manager = self.build_manager()
        manager.kc_manager.admin.get_client_roles.return_value = [
            {"name": "admin", "id": "role-admin"},
            {"name": "standard", "id": "role-standard"},
            {"name": "view-only", "id": "role-view"},
        ]

        auth_config = {
            "policies": [
                {
                    "name": "Policy - admin",
                    "type": "role",
                    "logic": "POSITIVE",
                    "decisionStrategy": "UNANIMOUS",
                    "roles": [{"id": "LEX/admin", "required": True}],
                    "config": {
                        "roles": json.dumps([{"id": "LEX/admin", "required": True}]),
                    },
                }
            ]
        }

        manager.ensure_client_role_policies(auth_config)

        admin_policy = next(policy for policy in auth_config["policies"] if policy["name"] == "Policy - admin")
        self.assertNotIn("roles", admin_policy)
        self.assertEqual(
            json.loads(admin_policy["config"]["roles"]),
            [{"id": "role-admin", "required": True}],
        )

    def test_normalize_role_policy_references_rewrites_stale_client_prefixed_ids(self):
        manager = self.build_manager()
        auth_config = {
            "policies": [
                {
                    "name": "Policy - admin",
                    "type": "role",
                    "config": {
                        "roles": json.dumps([{"id": "LEX/admin", "required": True}]),
                    },
                }
            ]
        }
        client_roles = {
            "admin": {"name": "admin", "id": "role-admin"},
            "standard": {"name": "standard", "id": "role-standard"},
        }

        manager.normalize_role_policy_references(auth_config, client_roles)

        admin_policy = auth_config["policies"][0]
        self.assertNotIn("roles", admin_policy)
        self.assertEqual(
            json.loads(admin_policy["config"]["roles"]),
            [{"id": "role-admin", "required": True}],
        )

    def test_process_model_changes_applies_standard_mapping_to_extra_roles(self):
        manager = self.build_manager()
        manager.kc_manager.admin.get_client.return_value = {
            "authorizationServicesEnabled": True,
            "redirectUris": [],
            "webOrigins": [],
            "authenticationFlowBindingOverrides": {},
            "protocol": "openid-connect",
            "publicClient": False,
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True,
        }
        manager.kc_manager.admin.get_client_roles.return_value = [
            {"name": "admin", "id": "role-admin"},
            {"name": "standard", "id": "role-standard"},
            {"name": "view-only", "id": "role-view"},
            {"name": "auditor", "id": "role-auditor"},
            {"name": "manage-client", "id": "role-manage-client"},
            {"name": "uma_protection", "id": "role-uma-protection"},
        ]
        manager.kc_manager.admin.get_client_authz_resources.return_value = []
        manager.kc_manager.export_authorization_settings.return_value = {"resources": [], "policies": []}
        manager.kc_manager.import_authorization_settings.return_value = True

        manager.process_model_changes(
            adds=[("billing", "Invoice")],
            deletes=[],
            renames=[],
        )

        imported_config = manager.kc_manager.import_authorization_settings.call_args.args[0]
        scope_permissions = {
            json.loads(policy["config"]["scopes"])[0]: json.loads(policy["config"]["applyPolicies"])
            for policy in imported_config["policies"]
            if policy["type"] == "scope"
        }

        self.assertEqual(
            scope_permissions["list"],
            ["Policy - admin", "Policy - standard", "Policy - auditor", "Policy - view-only"],
        )
        self.assertEqual(
            scope_permissions["read"],
            ["Policy - admin", "Policy - standard", "Policy - auditor", "Policy - view-only"],
        )
        self.assertEqual(scope_permissions["create"], ["Policy - admin"])
        self.assertEqual(
            scope_permissions["edit"],
            ["Policy - admin", "Policy - standard", "Policy - auditor"],
        )
        self.assertEqual(scope_permissions["delete"], ["Policy - admin"])
        self.assertEqual(
            scope_permissions["export"],
            ["Policy - admin", "Policy - standard", "Policy - auditor"],
        )

        imported_policy_names = [policy["name"] for policy in imported_config["policies"]]
        self.assertNotIn("Policy - manage-client", imported_policy_names)
        self.assertNotIn("Policy - uma_protection", imported_policy_names)

    def test_process_model_changes_updates_existing_standard_permissions_for_extra_roles(self):
        manager = self.build_manager()
        manager.kc_manager.admin.get_client.return_value = {
            "authorizationServicesEnabled": True,
            "redirectUris": [],
            "webOrigins": [],
            "authenticationFlowBindingOverrides": {},
            "protocol": "openid-connect",
            "publicClient": False,
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True,
        }
        manager.kc_manager.admin.get_client_roles.return_value = [
            {"name": "admin", "id": "role-admin"},
            {"name": "standard", "id": "role-standard"},
            {"name": "view-only", "id": "role-view"},
            {"name": "auditor", "id": "role-auditor"},
            {"name": "manage-client", "id": "role-manage-client"},
            {"name": "uma_protection", "id": "role-uma-protection"},
        ]
        manager.kc_manager.admin.get_client_authz_resources.return_value = []
        manager.kc_manager.export_authorization_settings.return_value = {
            "resources": [
                {
                    "name": "billing.Invoice",
                    "ownerManagedAccess": False,
                    "attributes": {},
                    "uris": [],
                    "scopes": [{"name": "edit"}, {"name": "create"}],
                }
            ],
            "policies": [
                {
                    "name": "Permission - billing.Invoice - edit",
                    "type": "scope",
                    "config": {
                        "resources": json.dumps(["billing.Invoice"]),
                        "scopes": json.dumps(["edit"]),
                        "applyPolicies": json.dumps(["Policy - admin", "Policy - standard"]),
                    },
                },
                {
                    "name": "Permission - billing.Invoice - create",
                    "type": "scope",
                    "config": {
                        "resources": json.dumps(["billing.Invoice"]),
                        "scopes": json.dumps(["create"]),
                        "applyPolicies": json.dumps(["Policy - admin"]),
                    },
                },
            ],
        }
        manager.kc_manager.import_authorization_settings.return_value = True

        manager.process_model_changes(
            adds=[],
            deletes=[],
            renames=[],
        )

        imported_config = manager.kc_manager.import_authorization_settings.call_args.args[0]
        permission_policies = {
            policy["name"]: json.loads(policy["config"]["applyPolicies"])
            for policy in imported_config["policies"]
            if policy["name"].startswith("Permission - ")
        }

        self.assertEqual(
            permission_policies["Permission - billing.Invoice - edit"],
            ["Policy - admin", "Policy - standard", "Policy - auditor"],
        )
        self.assertEqual(
            permission_policies["Permission - billing.Invoice - create"],
            ["Policy - admin"],
        )


class KeycloakImportErrorFormattingTest(TestCase):
    def test_http_error_details_include_response_body_excerpt(self):
        details = _format_keycloak_import_error_details(
            {
                "kind": "http_error",
                "status_code": 400,
                "response_text": "Role policy invalid",
            }
        )

        self.assertEqual(details, "Keycloak returned HTTP 400: Role policy invalid")


class KeycloakSyncManagerImmutableResourceTest(TestCase):
    def build_manager(self):
        manager = KeycloakSyncManager.__new__(KeycloakSyncManager)
        manager.kc_manager = MagicMock()
        manager.kc_manager.client_uuid = "client-uuid"
        manager.default_scopes = ["list", "read", "create", "edit", "delete", "export"]
        manager.exported_configs = None
        return manager

    def test_is_keycloak_sync_excluded_resource_name_matches_immutable_models(self):
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name("core.HistoricalQuarter")
        )
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name("core.MetaHistoricalQuarter")
        )
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name("audit_logging.AuditLog")
        )
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name("audit_logging.AuditLogStatus")
        )
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name("legacy_data.LegacyLog")
        )
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name(
                "legacy_data.LegacyDynamicGenericAppArchive"
            )
        )
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name("audit_logging.CalculationLog")
        )
        self.assertFalse(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name("billing.Invoice")
        )

    def test_process_model_changes_prunes_existing_immutable_resources_and_skips_new_ones(self):
        manager = self.build_manager()
        manager.delete_resources_individual = MagicMock()
        manager.kc_manager.admin.get_client.return_value = {
            "authorizationServicesEnabled": True,
            "redirectUris": [],
            "webOrigins": [],
            "authenticationFlowBindingOverrides": {},
            "protocol": "openid-connect",
            "publicClient": False,
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True,
        }
        manager.kc_manager.admin.get_client_roles.return_value = [
            {"name": "admin", "id": "role-admin"},
            {"name": "standard", "id": "role-standard"},
            {"name": "view-only", "id": "role-view"},
        ]
        manager.kc_manager.admin.get_client_authz_resources.return_value = [
            {"name": "audit_logging.AuditLog", "_id": "resource-audit"},
            {"name": "core.HistoricalQuarter", "_id": "resource-history"},
            {"name": "billing.Invoice", "_id": "resource-invoice"},
        ]
        manager.kc_manager.export_authorization_settings.return_value = {
            "resources": [
                {
                    "name": "audit_logging.AuditLog",
                    "ownerManagedAccess": False,
                    "attributes": {},
                    "uris": [],
                    "scopes": [{"name": "read"}],
                },
                {
                    "name": "core.HistoricalQuarter",
                    "ownerManagedAccess": False,
                    "attributes": {},
                    "uris": [],
                    "scopes": [{"name": "read"}],
                },
                {
                    "name": "billing.Invoice",
                    "ownerManagedAccess": False,
                    "attributes": {},
                    "uris": [],
                    "scopes": [{"name": "read"}],
                },
            ],
            "policies": [
                {
                    "name": "Permission - audit_logging.AuditLog - read",
                    "type": "scope",
                    "config": {
                        "resources": json.dumps(["audit_logging.AuditLog"]),
                        "scopes": json.dumps(["read"]),
                        "applyPolicies": json.dumps(["Policy - admin"]),
                    },
                },
                {
                    "name": "Permission - core.HistoricalQuarter - read",
                    "type": "scope",
                    "config": {
                        "resources": json.dumps(["core.HistoricalQuarter"]),
                        "scopes": json.dumps(["read"]),
                        "applyPolicies": json.dumps(["Policy - admin"]),
                    },
                },
            ],
        }
        manager.kc_manager.import_authorization_settings.return_value = True

        manager.process_model_changes(
            adds=[
                ("audit_logging", "AuditLogStatus"),
                ("core", "MetaHistoricalQuarter"),
                ("legacy_data", "LegacyLog"),
                ("billing", "Payment"),
            ],
            deletes=[],
            renames=[],
        )

        imported_config = manager.kc_manager.import_authorization_settings.call_args.args[0]
        imported_resource_names = {resource["name"] for resource in imported_config["resources"]}
        self.assertEqual(imported_resource_names, {"billing.Invoice", "billing.Payment"})

        referenced_resources = set()
        for policy in imported_config["policies"]:
            if policy.get("type") != "scope":
                continue
            referenced_resources.update(json.loads(policy["config"]["resources"]))

        self.assertNotIn("audit_logging.AuditLog", referenced_resources)
        self.assertNotIn("core.HistoricalQuarter", referenced_resources)
        self.assertIn("billing.Payment", referenced_resources)

        manager.delete_resources_individual.assert_called_once_with(
            {"audit_logging.AuditLog", "core.HistoricalQuarter"},
            manager.kc_manager.admin.get_client_authz_resources.return_value,
        )
