"""
Cluster 1e: ``KeycloakSyncManager`` behaviour.

Intent (from docs/features/ai-onboarding/ and
``lex/lex_app/management/commands/init.py``):

    ``KeycloakSyncManager`` is the beating heart of ``lex init``. It
    reconciles a customer's Django models with the Keycloak
    authorization-server configuration: what resources must exist,
    what policies gate them, how renamed / deleted models are carried
    over. Customers rely on this class to keep role-based access
    correct after every model change — a silent drift here means
    users suddenly see (or don't see) things they shouldn't.

    These tests exercise every decision-making method on the class
    using a stubbed ``kc_manager`` (no real Keycloak). The
    transformations are pure: ``auth_config`` dict in,
    ``auth_config`` dict (or other pure output) out — perfect for
    assertion-heavy tests.

Scenario numbering extends
docs/test-plan/test-clusters.md#1-init--project-bootstrap
(new sub-cluster 1e).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase, mock

from django.core.management.base import CommandError
from lex.lex_app.management.commands.init import (
    KeycloakSyncManager,
)


# ---------------------------------------------------------------------
# Fixture: a KeycloakSyncManager whose kc_manager is a MagicMock.
# ---------------------------------------------------------------------
def _make_sync_manager(client_uuid: str = "test-client-uuid"):
    """Build a ``KeycloakSyncManager`` with a fully stubbed kc_manager.

    Bypasses the real ``__init__`` (which reads ``.env`` and connects
    to Keycloak) and gives every test a clean Mock it can program.
    """
    mgr = KeycloakSyncManager.__new__(KeycloakSyncManager)
    mgr.kc_manager = mock.MagicMock()
    mgr.kc_manager.client_uuid = client_uuid
    mgr.kc_manager.last_authz_import_error = None
    mgr.default_scopes = ["list", "read", "create", "edit", "delete", "export"]
    mgr.exported_configs = None
    return mgr


# ---------------------------------------------------------------------
# 1.30c — Environment loading
# ---------------------------------------------------------------------
class TestCluster01e_EnvironmentLoading(TestCase):
    """``KeycloakSyncManager.__init__`` must not erase CI-provided secrets."""

    def test_1_30c_empty_dotenv_values_do_not_clobber_exported_env(self):
        """Scenario 1.30c: blank placeholder ``.env`` values are ignored.

        The lex-app source tree can contain an empty placeholder ``.env``.
        In CI, the real Keycloak credentials arrive through environment
        variables. Initialising ``KeycloakSyncManager`` must therefore never
        replace an exported secret with ``""`` from that placeholder file.
        """
        from lex.lex_app.management.commands import init as init_module

        exported_env = {
            "KEYCLOAK_URL": "https://auth.example.test",
            "KEYCLOAK_REALM": "real-realm",
            "OIDC_RP_CLIENT_ID": "real-client",
            "OIDC_RP_CLIENT_SECRET": "real-secret",
            "OIDC_RP_CLIENT_UUID": "real-uuid",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "KEYCLOAK_URL=\n"
                "KEYCLOAK_REALM=\n"
                "OIDC_RP_CLIENT_ID=\n"
                "OIDC_RP_CLIENT_SECRET=\n"
                "OIDC_RP_CLIENT_UUID=\n",
                encoding="utf-8",
            )

            with (
                mock.patch.dict(os.environ, exported_env, clear=True),
                mock.patch.object(init_module, "ENV_FILE", env_file),
                mock.patch(
                    "lex.api.views.authentication.KeycloakManager.KeycloakManager",
                ) as manager_cls,
            ):
                mgr = KeycloakSyncManager()

                manager_cls.assert_called_once_with()
                self.assertIs(mgr.kc_manager, manager_cls.return_value)
                for key, expected in exported_env.items():
                    self.assertEqual(
                        os.environ[key], expected,
                        f"Blank .env value for {key} must not clobber exported env.",
                    )


# ---------------------------------------------------------------------
# 1.31 — Exclusion rules
# ---------------------------------------------------------------------
class TestCluster01e_Exclusions(TestCase):
    """``is_keycloak_sync_excluded_model`` — immutable models stay out of Keycloak."""

    def test_1_31_legacy_data_app_is_excluded(self):
        """Scenario 1.31: all models in the ``legacy_data`` app are excluded."""
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_model(
                "legacy_data", "LegacyCalculationLog",
            ),
        )

    def test_1_31b_audit_log_models_are_excluded(self):
        """Scenario 1.31b: audit_logging tables are excluded by name."""
        for name in ("AuditLog", "AuditLogStatus", "CalculationLog"):
            with self.subTest(model=name):
                self.assertTrue(
                    KeycloakSyncManager.is_keycloak_sync_excluded_model(
                        "audit_logging", name,
                    ),
                    f"{name} must be excluded from Keycloak sync",
                )

    def test_1_31c_historical_prefix_models_are_excluded(self):
        """Scenario 1.31c: simple_history's ``Historical*`` shadow models are excluded."""
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_model(
                "any_app", "HistoricalWidget",
            ),
        )
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_model(
                "any_app", "MetaHistoricalWidget",
            ),
        )

    def test_1_31d_normal_model_is_not_excluded(self):
        self.assertFalse(
            KeycloakSyncManager.is_keycloak_sync_excluded_model(
                "myapp", "Widget",
            ),
            "A normal customer model must NOT be accidentally excluded",
        )

    def test_1_31e_empty_model_name_is_not_excluded(self):
        """Defensive: bad input doesn't explode; returns False."""
        self.assertFalse(
            KeycloakSyncManager.is_keycloak_sync_excluded_model("myapp", ""),
        )

    def test_1_32_resource_name_form_is_predictable(self):
        """Scenario 1.32: ``_resource_name`` is ``"<app>.<Model>"``."""
        self.assertEqual(
            KeycloakSyncManager._resource_name("myapp", "Widget"),
            "myapp.Widget",
        )

    def test_1_32b_resource_name_exclusion_matches_model_exclusion(self):
        """Scenario 1.32b: the two exclusion predicates agree on the same input."""
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name(
                "audit_logging.AuditLog",
            ),
        )
        self.assertTrue(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name(
                "HistoricalFoo",
            ),
            "Bare-name ``Historical*`` must also be excluded — simple_history "
            "may present shadow models without an app prefix",
        )
        self.assertFalse(
            KeycloakSyncManager.is_keycloak_sync_excluded_resource_name(None),
        )


# ---------------------------------------------------------------------
# 1.33 — JSON-maybe parsing
# ---------------------------------------------------------------------
class TestCluster01e_ParseJsonMaybe(TestCase):
    """``_parse_json_maybe`` — tolerate lists + JSON strings but reject garbage."""

    def test_1_33_list_passes_through(self):
        """Scenario 1.33: a Python list is returned unchanged."""
        data = [1, 2, 3]
        self.assertEqual(
            KeycloakSyncManager._parse_json_maybe(data, "test"),
            data,
        )

    def test_1_33b_json_string_is_parsed(self):
        """Scenario 1.33b: a JSON string of a list is parsed into a list."""
        self.assertEqual(
            KeycloakSyncManager._parse_json_maybe('["a", "b"]', "test"),
            ["a", "b"],
        )

    def test_1_33c_invalid_json_raises_command_error(self):
        """Scenario 1.33c: malformed JSON raises CommandError with context name."""
        with self.assertRaises(CommandError) as ctx:
            KeycloakSyncManager._parse_json_maybe("{not json", "ctx-label")
        self.assertIn(
            "ctx-label", str(ctx.exception),
            "Error message must carry the context label so operators can "
            "trace which field failed",
        )

    def test_1_33d_non_list_json_raises(self):
        """Scenario 1.33d: JSON of a dict (not a list) is rejected — surface contract."""
        with self.assertRaises(CommandError):
            KeycloakSyncManager._parse_json_maybe('{"k": 1}', "ctx")

    def test_1_33e_unexpected_type_raises(self):
        """Scenario 1.33e: anything not list/str is a type error."""
        with self.assertRaises(CommandError):
            KeycloakSyncManager._parse_json_maybe(42, "ctx")


# ---------------------------------------------------------------------
# 1.34 — Resource discovery on the auth_config
# ---------------------------------------------------------------------
class TestCluster01e_ResourceDiscovery(TestCase):
    """``get_existing_keycloak_resources`` + ``find_missing_models``."""

    def setUp(self):
        self.mgr = _make_sync_manager()

    def test_1_34_existing_resources_extracted_by_name(self):
        """Scenario 1.34: names are pulled out of the ``resources`` list."""
        auth_config = {
            "resources": [{"name": "myapp.Widget"}, {"name": "myapp.Gadget"}],
        }
        self.assertEqual(
            self.mgr.get_existing_keycloak_resources(auth_config),
            {"myapp.Widget", "myapp.Gadget"},
        )

    def test_1_34b_unnamed_resources_are_skipped(self):
        """Scenario 1.34b: resources without a ``name`` key are silently skipped."""
        auth_config = {
            "resources": [{"name": "myapp.Widget"}, {"name": ""}, {}],
        }
        self.assertEqual(
            self.mgr.get_existing_keycloak_resources(auth_config),
            {"myapp.Widget"},
        )

    def test_1_34c_missing_models_is_set_difference(self):
        """Scenario 1.34c: missing = django − keycloak − to_delete."""
        django_models = {"myapp.A", "myapp.B", "myapp.C"}
        keycloak = {"myapp.A"}
        to_delete = {"myapp.C"}

        missing = self.mgr.find_missing_models(django_models, keycloak, to_delete)
        self.assertEqual(
            missing, {"myapp.B"},
            "Only models in Django but not in Keycloak (and not scheduled "
            "for deletion) must be reported as missing",
        )


# ---------------------------------------------------------------------
# 1.35 — Permission discovery by resource name
# ---------------------------------------------------------------------
class TestCluster01e_FindPermissionsForResource(TestCase):
    """``find_permissions_for_resource_name`` picks out scope policies by resource."""

    def setUp(self):
        self.mgr = _make_sync_manager()

    def test_1_35_finds_scope_policy_referencing_resource(self):
        """Scenario 1.35: a scope policy targeting the resource is returned."""
        auth_config = {
            "policies": [
                {
                    "name": "Permission - myapp.Widget - read",
                    "type": "scope",
                    "config": {"resources": '["myapp.Widget"]'},
                },
                {
                    "name": "Permission - myapp.Gadget - read",
                    "type": "scope",
                    "config": {"resources": '["myapp.Gadget"]'},
                },
            ],
        }
        found = self.mgr.find_permissions_for_resource_name(
            "myapp.Widget", auth_config,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "Permission - myapp.Widget - read")

    def test_1_35b_ignores_non_scope_policies(self):
        """Scenario 1.35b: role policies are ignored — only scope policies matter."""
        auth_config = {
            "policies": [
                {
                    "name": "Policy - admin",
                    "type": "role",
                    "config": {"resources": '["myapp.Widget"]'},
                },
            ],
        }
        self.assertEqual(
            self.mgr.find_permissions_for_resource_name("myapp.Widget", auth_config),
            [],
            "Role policies must be skipped even if they reference the "
            "resource — only scope policies are model-scoped permissions",
        )

    def test_1_35c_returns_empty_when_no_match(self):
        self.assertEqual(
            self.mgr.find_permissions_for_resource_name(
                "myapp.NoSuch",
                {"policies": []},
            ),
            [],
        )


# ---------------------------------------------------------------------
# 1.36 — ensure_default_authz
# ---------------------------------------------------------------------
class TestCluster01e_EnsureDefaultAuthz(TestCase):
    """``ensure_default_authz`` is an opt-in writer."""

    def setUp(self):
        self.mgr = _make_sync_manager()

    def test_1_36_noop_when_flag_false(self):
        """Scenario 1.36: with the flag off, ``auth_config`` is untouched."""
        auth_config = {"resources": [], "policies": []}
        snapshot = {"resources": [], "policies": []}
        self.mgr.ensure_default_authz(auth_config, False)
        self.assertEqual(auth_config, snapshot)

    def test_1_36b_adds_default_resource_policy_permission(self):
        """Scenario 1.36b: with the flag on, the three defaults are added."""
        auth_config = {"resources": [], "policies": []}

        self.mgr.ensure_default_authz(auth_config, True)

        resource_names = [r["name"] for r in auth_config["resources"]]
        policy_names = [p["name"] for p in auth_config["policies"]]
        self.assertIn("Default Resource", resource_names)
        self.assertIn("Default Policy", policy_names)
        self.assertIn("Default Permission", policy_names)

    def test_1_36c_idempotent(self):
        """
        Scenario 1.36c: running twice does not duplicate the defaults.

        Customers re-run ``lex init`` often — the second invocation
        must produce the same ``auth_config`` as the first.
        """
        auth_config = {"resources": [], "policies": []}
        self.mgr.ensure_default_authz(auth_config, True)
        self.mgr.ensure_default_authz(auth_config, True)

        resource_count = sum(
            1 for r in auth_config["resources"] if r["name"] == "Default Resource"
        )
        policy_count = sum(
            1 for p in auth_config["policies"] if p["name"] == "Default Policy"
        )
        self.assertEqual(resource_count, 1, "Default Resource must not duplicate")
        self.assertEqual(policy_count, 1, "Default Policy must not duplicate")


# ---------------------------------------------------------------------
# 1.37 — Role ordering
# ---------------------------------------------------------------------
class TestCluster01e_OrderedClientRoleNames(TestCase):
    """``_ordered_client_role_names`` — defaults first, then extras alphabetically."""

    def setUp(self):
        self.mgr = _make_sync_manager()

    def test_1_37_defaults_come_first_in_declared_order(self):
        """Scenario 1.37: ``admin, standard, view-only`` always lead."""
        roles = {"view-only", "admin", "standard"}
        ordered = self.mgr._ordered_client_role_names(roles)
        self.assertEqual(ordered, ["admin", "standard", "view-only"])

    def test_1_37b_extra_roles_sorted_alphabetically_after_defaults(self):
        """Scenario 1.37b: customer-extra roles come after, sorted alphabetically."""
        roles = {"admin", "standard", "view-only", "hr", "auditor"}
        ordered = self.mgr._ordered_client_role_names(roles)
        self.assertEqual(
            ordered,
            ["admin", "standard", "view-only", "auditor", "hr"],
            "Extra roles must appear after the three defaults and be "
            "ordered alphabetically for a stable, reviewable diff",
        )

    def test_1_37c_missing_default_is_skipped(self):
        """If a default role is missing from the input it is simply skipped."""
        ordered = self.mgr._ordered_client_role_names({"admin", "hr"})
        self.assertEqual(ordered, ["admin", "hr"])


# ---------------------------------------------------------------------
# 1.38 — Scope→policy mapping composition
# ---------------------------------------------------------------------
class TestCluster01e_BuildScopePolicyMapping(TestCase):
    """``build_scope_policy_mapping`` fans out extra roles into the standard-role slot."""

    def setUp(self):
        self.mgr = _make_sync_manager()

    def test_1_38_defaults_only(self):
        """Scenario 1.38: with only the three defaults → documented mapping."""
        mapping = self.mgr.build_scope_policy_mapping(
            ["admin", "standard", "view-only"],
        )
        self.assertEqual(set(mapping["create"]), {"Policy - admin"})
        self.assertEqual(
            set(mapping["edit"]), {"Policy - admin", "Policy - standard"},
        )
        self.assertEqual(
            set(mapping["read"]),
            {"Policy - admin", "Policy - standard", "Policy - view-only"},
        )

    def test_1_38b_extra_role_inherits_standard_slot(self):
        """
        Scenario 1.38b: a customer-extra role (e.g. ``hr``) is added to
        every scope that currently has ``Policy - standard``.

        This is the rule that lets a customer add a new role without
        having to hand-edit every permission — the extra simply
        "piggybacks" on standard.
        """
        mapping = self.mgr.build_scope_policy_mapping(
            ["admin", "standard", "view-only", "hr"],
        )

        # Scopes that have standard must also now have Policy - hr.
        for scope in ("read", "edit", "export"):
            with self.subTest(scope=scope):
                self.assertIn(
                    "Policy - hr", mapping[scope],
                    f"Extra role ``hr`` must inherit the {scope!r} scope "
                    f"from ``Policy - standard``; got {mapping[scope]!r}",
                )

        # Scopes that DON'T have standard must NOT get the extra.
        for scope in ("create", "delete"):
            with self.subTest(scope=scope):
                self.assertNotIn(
                    "Policy - hr", mapping[scope],
                    f"Extra role ``hr`` must NOT inherit {scope!r} "
                    f"(admin-only scope); got {mapping[scope]!r}",
                )


# ---------------------------------------------------------------------
# 1.39 — Policy reference normalization
# ---------------------------------------------------------------------
class TestCluster01e_NormalizeRolePolicyReferences(TestCase):
    """``normalize_role_policy_references`` canonicalizes role references in-place."""

    def setUp(self):
        self.mgr = _make_sync_manager()

    def test_1_39_role_name_prefix_maps_to_role_id(self):
        """
        Scenario 1.39: a policy named ``Policy - admin`` has its
        ``config.roles`` canonicalised to ``[{"id": <admin-id>, ...}]``.

        This guards against Keycloak exports that carry roles by name
        only — we must supply the id Keycloak expects on import.
        """
        client_roles = {
            "admin": {"id": "uuid-admin", "name": "admin"},
            "standard": {"id": "uuid-standard", "name": "standard"},
        }
        auth_config = {
            "policies": [
                {
                    "name": "Policy - admin",
                    "type": "role",
                    "config": {},
                    "roles": [{"name": "admin"}],
                },
            ],
        }

        self.mgr.normalize_role_policy_references(auth_config, client_roles)

        policy = auth_config["policies"][0]
        self.assertNotIn(
            "roles", policy,
            "Top-level ``roles`` must be pulled into config.roles",
        )
        canonical = json.loads(policy["config"]["roles"])
        self.assertEqual(
            canonical,
            [{"id": "uuid-admin", "required": True}],
            "Normalized roles must carry the UUID Keycloak expects",
        )

    def test_1_39b_non_role_policies_are_untouched(self):
        """Scenario 1.39b: scope policies pass through."""
        auth_config = {
            "policies": [
                {"name": "Permission - X", "type": "scope", "config": {}},
            ],
        }
        self.mgr.normalize_role_policy_references(auth_config, {})
        self.assertEqual(
            auth_config["policies"][0],
            {"name": "Permission - X", "type": "scope", "config": {}},
            "Non-role policies must be left alone",
        )

    def test_1_39c_non_list_policies_raise(self):
        """Malformed input fails loudly so operators see the problem."""
        with self.assertRaises(CommandError):
            self.mgr.normalize_role_policy_references(
                {"policies": "not a list"}, {},
            )


# ---------------------------------------------------------------------
# 1.40 — export_configs caching & error branch
# ---------------------------------------------------------------------
class TestCluster01e_ExportConfigs(TestCase):
    """``export_configs`` caches the first response and fails loudly on empty."""

    def test_1_40_caches_first_response(self):
        """Scenario 1.40: only ONE call to Keycloak even if invoked twice."""
        mgr = _make_sync_manager()
        mgr.kc_manager.export_authorization_settings.return_value = {
            "resources": [{"name": "myapp.A"}], "policies": [],
        }

        cfg1 = mgr.export_configs()
        cfg2 = mgr.export_configs()

        self.assertIs(
            cfg1, cfg2,
            "Second call must return the cached reference",
        )
        mgr.kc_manager.export_authorization_settings.assert_called_once()

    def test_1_40b_empty_export_raises(self):
        """Scenario 1.40b: an empty export is treated as a hard failure."""
        mgr = _make_sync_manager()
        mgr.kc_manager.export_authorization_settings.return_value = None

        with self.assertRaises(Exception) as ctx:
            mgr.export_configs()
        self.assertIn("Failed to export", str(ctx.exception))


# ---------------------------------------------------------------------
# 1.41 — delete_resources_individual
# ---------------------------------------------------------------------
class TestCluster01e_DeleteResourcesIndividual(TestCase):
    """
    ``delete_resources_individual`` deletes the scope permissions that
    reference a resource before deleting the resource itself.
    """

    def test_1_41_deletes_permissions_then_resource(self):
        """
        Scenario 1.41: for a resource with an attached permission, both
        the permission and the resource are deleted, via Keycloak's UMA
        and admin APIs.
        """
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client_authz_permissions.return_value = [
            {
                "id": "perm-1", "name": "Permission - myapp.A - read",
                "resources": ["res-1"],
            },
        ]
        all_resources = [
            {"name": "myapp.A", "_id": "res-1"},
            {"name": "myapp.B", "_id": "res-2"},  # untouched
        ]

        mgr.delete_resources_individual({"myapp.A"}, all_resources)

        mgr.kc_manager.admin.delete_client_authz_permission.assert_called_once_with(
            client_id="test-client-uuid", permission_id="perm-1",
        )
        mgr.kc_manager.uma.resource_set_delete.assert_called_once_with("res-1")

    def test_1_41b_missing_resource_is_logged_not_fatal(self):
        """Scenario 1.41b: a to-delete name not in the resource list logs a warning but does not raise."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client_authz_permissions.return_value = []

        # Should not raise even though "myapp.Ghost" has no matching _id.
        mgr.delete_resources_individual({"myapp.Ghost"}, [])

        mgr.kc_manager.uma.resource_set_delete.assert_not_called()

    def test_1_41c_permission_without_id_raises(self):
        """
        Scenario 1.41c: a permission record without an ``id`` is a
        data-integrity problem and must abort — operators must see it.
        """
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client_authz_permissions.return_value = [
            {"name": "Permission - broken", "resources": ["res-1"]},  # no id
        ]
        all_resources = [{"name": "myapp.A", "_id": "res-1"}]

        with self.assertRaises(CommandError) as ctx:
            mgr.delete_resources_individual({"myapp.A"}, all_resources)
        self.assertIn("Permission has no id", str(ctx.exception))


# ---------------------------------------------------------------------
# 1.42 — Client-settings snapshot/restore
# ---------------------------------------------------------------------
class TestCluster01e_ClientSettingsSnapshot(TestCase):
    """``_snapshot_client_settings`` + ``_restore_client_settings`` round-trip."""

    def test_1_42_snapshot_keeps_only_published_fields(self):
        """
        Scenario 1.42: the snapshot is restricted to a documented key
        set — the import step must never overwrite e.g. client secrets.
        """
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = {
            "authorizationServicesEnabled": True,
            "redirectUris": ["https://app.example/*"],
            "secret": "super-secret",
            "id": "uuid-client",
        }

        snapshot = mgr._snapshot_client_settings()

        self.assertIn("authorizationServicesEnabled", snapshot)
        self.assertIn("redirectUris", snapshot)
        self.assertNotIn(
            "secret", snapshot,
            "Client secret must never land in the snapshot",
        )
        self.assertNotIn(
            "id", snapshot,
            "Opaque ``id`` must not be snapshotted",
        )

    def test_1_42b_restore_updates_only_if_changed(self):
        """Scenario 1.42b: if the current rep matches the snapshot → no PUT."""
        mgr = _make_sync_manager()
        snapshot = {"authorizationServicesEnabled": True}
        mgr.kc_manager.admin.get_client.return_value = {
            "authorizationServicesEnabled": True,
        }

        mgr._restore_client_settings(snapshot)

        mgr.kc_manager.admin.update_client.assert_not_called()

    def test_1_42c_restore_pushes_differences(self):
        """Scenario 1.42c: if the rep differs → a single PUT with the merged rep."""
        mgr = _make_sync_manager()
        snapshot = {"authorizationServicesEnabled": True}
        mgr.kc_manager.admin.get_client.return_value = {
            "authorizationServicesEnabled": False,  # drifted
        }

        mgr._restore_client_settings(snapshot)

        mgr.kc_manager.admin.update_client.assert_called_once()
        args, _ = mgr.kc_manager.admin.update_client.call_args
        client_id, payload = args
        self.assertEqual(client_id, "test-client-uuid")
        self.assertTrue(payload.get("authorizationServicesEnabled"))


# ---------------------------------------------------------------------
# 1.43 — sync_standard_client_role_permissions (extras inherit standard)
# ---------------------------------------------------------------------
class TestCluster01e_SyncStandardClientRolePermissions(TestCase):
    """
    Extra roles added to ``applyPolicies`` wherever ``Policy - standard``
    already appears — proves the extra-role inheritance works on the
    policy side too (complement to ``build_scope_policy_mapping``).
    """

    def setUp(self):
        self.mgr = _make_sync_manager()

    def test_1_43_extra_role_appended_to_scope_permissions(self):
        """Scenario 1.43: extras are appended to scope permissions, not replaced.
        Only newly created policies should be propagated."""
        auth_config = {
            "policies": [
                # The extra-role policy itself must exist in auth_config:
                {"name": "Policy - hr", "type": "role", "config": {}},
                # A scope permission currently granting standard:
                {
                    "name": "Permission - myapp.A - read",
                    "type": "scope",
                    "config": {
                        "resources": '["myapp.A"]',
                        "scopes": '["read"]',
                        "applyPolicies": '["Policy - admin", "Policy - standard"]',
                    },
                },
            ],
        }

        self.mgr.sync_standard_client_role_permissions(
            auth_config, ["admin", "standard", "view-only", "hr"],
            newly_created_policy_names={"Policy - hr"},
        )

        updated = next(
            p for p in auth_config["policies"]
            if p.get("name") == "Permission - myapp.A - read"
        )
        apply_policies = json.loads(updated["config"]["applyPolicies"])
        self.assertEqual(
            apply_policies,
            ["Policy - admin", "Policy - standard", "Policy - hr"],
            "``Policy - hr`` must be appended right after ``Policy - standard``, "
            "preserving the documented order",
        )

    def test_1_43b_noop_without_extras(self):
        """Scenario 1.43b: with only defaults, nothing changes."""
        auth_config = {
            "policies": [
                {
                    "name": "Permission - myapp.A - read",
                    "type": "scope",
                    "config": {
                        "resources": '["myapp.A"]',
                        "scopes": '["read"]',
                        "applyPolicies": '["Policy - admin", "Policy - standard"]',
                    },
                },
            ],
        }
        import copy
        before = copy.deepcopy(auth_config)

        self.mgr.sync_standard_client_role_permissions(
            auth_config, ["admin", "standard", "view-only"],
        )

        self.assertEqual(
            auth_config, before,
            "With no extras, the auth_config must be untouched",
        )

    def test_1_43c_existing_extra_role_not_overridden_on_subsequent_run(self):
        """Scenario 1.43c: on a subsequent run (policy already exists),
        manually adjusted permissions must NOT be overridden.

        An admin may have removed ``Policy - hr`` from the ``delete``
        permission after the initial propagation. A later ``lex init``
        must preserve that manual adjustment.
        """
        auth_config = {
            "policies": [
                {"name": "Policy - hr", "type": "role", "config": {}},
                # read — admin kept hr here
                {
                    "name": "Permission - myapp.A - read",
                    "type": "scope",
                    "config": {
                        "resources": '["myapp.A"]',
                        "scopes": '["read"]',
                        "applyPolicies": '["Policy - admin", "Policy - standard", "Policy - hr"]',
                    },
                },
                # delete — admin manually REMOVED hr from this permission
                {
                    "name": "Permission - myapp.A - delete",
                    "type": "scope",
                    "config": {
                        "resources": '["myapp.A"]',
                        "scopes": '["delete"]',
                        "applyPolicies": '["Policy - admin", "Policy - standard"]',
                    },
                },
            ],
        }
        import copy
        before = copy.deepcopy(auth_config)

        # Second run: "Policy - hr" already exists, so newly_created is empty
        self.mgr.sync_standard_client_role_permissions(
            auth_config, ["admin", "standard", "view-only", "hr"],
            newly_created_policy_names=set(),
        )

        self.assertEqual(
            auth_config, before,
            "On a subsequent run with no newly created policies, "
            "manual permission adjustments must be preserved",
        )

    def test_1_43d_new_role_propagated_existing_role_preserved(self):
        """Scenario 1.43d: when two extra roles exist but only one is new,
        only the new one is propagated to standard-bearing permissions.

        Pre-existing ``Policy - hr`` (already manually customized) is not
        touched, while brand-new ``Policy - finance`` is added.
        """
        auth_config = {
            "policies": [
                {"name": "Policy - hr", "type": "role", "config": {}},
                {"name": "Policy - finance", "type": "role", "config": {}},
                # read — admin has hr here already, but NOT finance
                {
                    "name": "Permission - myapp.A - read",
                    "type": "scope",
                    "config": {
                        "resources": '["myapp.A"]',
                        "scopes": '["read"]',
                        "applyPolicies": '["Policy - admin", "Policy - standard", "Policy - hr"]',
                    },
                },
                # delete — admin deliberately has NO extras here
                {
                    "name": "Permission - myapp.A - delete",
                    "type": "scope",
                    "config": {
                        "resources": '["myapp.A"]',
                        "scopes": '["delete"]',
                        "applyPolicies": '["Policy - admin", "Policy - standard"]',
                    },
                },
            ],
        }

        # Only "Policy - finance" is newly created
        self.mgr.sync_standard_client_role_permissions(
            auth_config, ["admin", "standard", "view-only", "hr", "finance"],
            newly_created_policy_names={"Policy - finance"},
        )

        read_perm = next(
            p for p in auth_config["policies"]
            if p.get("name") == "Permission - myapp.A - read"
        )
        apply_read = json.loads(read_perm["config"]["applyPolicies"])
        self.assertIn("Policy - finance", apply_read,
                       "New policy 'finance' must be added to read")
        self.assertIn("Policy - hr", apply_read,
                       "Existing 'hr' must remain in read")

        delete_perm = next(
            p for p in auth_config["policies"]
            if p.get("name") == "Permission - myapp.A - delete"
        )
        apply_delete = json.loads(delete_perm["config"]["applyPolicies"])
        self.assertIn("Policy - finance", apply_delete,
                       "New policy 'finance' must be added to delete (inherits standard)")
        self.assertNotIn("Policy - hr", apply_delete,
                          "Existing 'hr' must NOT be re-added to delete "
                          "(admin deliberately removed it)")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

