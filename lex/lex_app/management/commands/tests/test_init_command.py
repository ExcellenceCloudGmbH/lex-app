"""
Tests for the Init management command's argument parsing and migration forwarding.
"""
import json
from unittest import TestCase
from unittest.mock import patch, MagicMock, call
from io import StringIO

from lex.lex_app.management.commands.Init import Command, KeycloakSyncManager


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

    @patch('lex.lex_app.management.commands.Init.call_command')
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

    @patch('lex.lex_app.management.commands.Init.call_command')
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

    @patch('lex.lex_app.management.commands.Init.call_command')
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

    @patch('lex.lex_app.management.commands.Init.call_command')
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

    @patch('lex.lex_app.management.commands.Init.call_command')
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

    @patch('lex.lex_app.management.commands.Init.call_command')
    def test_skip_makemigrations(self, mock_call):
        mock_call.return_value = None
        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=True):
            self.cmd.execute_migrations(verbosity=1, create_new=False)

        mock_call.assert_called_once()
        self.assertEqual(mock_call.call_args_list[0][0][0], 'migrate')

    @patch('lex.lex_app.management.commands.Init.call_command')
    def test_migration_failure_returns_false(self, mock_call):
        mock_call.side_effect = Exception('migration boom')
        result = self.cmd.execute_migrations(verbosity=1, create_new=True)
        self.assertFalse(result)


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
        manager.kc_manager.admin.create_client_role.assert_not_called()

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
