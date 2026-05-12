"""
Cluster 1f: Keycloak drift — ``process_model_changes`` end-to-end.

Intent (from docs/installation.md + the cluster plan):

    When a customer evolves their Django models — adds a new model,
    renames one, deletes one — the next ``lex Init`` must reconcile
    Keycloak so the resource list, policies, and scope permissions
    stay in lockstep with what Django actually has. Silent drift here
    means users either lose access they should have, or keep access
    they should have lost.

These tests drive ``KeycloakSyncManager.process_model_changes`` with a
stubbed ``kc_manager`` — no real Keycloak, no real HTTP. The inputs are
the dicts that Keycloak's authorization export/import APIs speak, so
the assertions are stated in the **customer contract**: what resources
end up in the config, what policies get removed, what permissions get
preserved across a rename.

Scenario numbering matches
docs/test-plan/test-clusters.md#1-init--project-bootstrap (1.8 / 1.9 /
1.10) plus scenario 1.15 (state-file durability).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase, mock

from lex.lex_app.management.commands import init as init_module
from lex.lex_app.management.commands.init import (
    KeycloakSyncManager,
    _load_state_map,
    get_state,
    set_state,
)


# ---------------------------------------------------------------------
# Fixture — a KeycloakSyncManager with every external boundary stubbed.
# ---------------------------------------------------------------------
def _make_sync_manager_with_defaults(client_uuid: str = "cli-uuid") -> KeycloakSyncManager:
    """
    Build a ``KeycloakSyncManager`` that can run ``process_model_changes``
    end-to-end without a real Keycloak.

    The three *managed* role policies (admin / standard / view-only) are
    pre-seeded into ``auth_config`` by a stubbed
    ``ensure_client_role_policies`` so the rest of the code path behaves
    like a realistic, already-bootstrapped project.
    """
    mgr = KeycloakSyncManager.__new__(KeycloakSyncManager)
    mgr.kc_manager = mock.MagicMock()
    mgr.kc_manager.client_uuid = client_uuid
    mgr.kc_manager.last_authz_import_error = None
    mgr.kc_manager.import_authorization_settings.return_value = True
    mgr.default_scopes = ["list", "read", "create", "edit", "delete", "export"]
    mgr.exported_configs = None

    # Client settings snapshot/restore should be a no-op in these tests.
    mgr.kc_manager.admin.get_client.return_value = {
        "authorizationServicesEnabled": True,
    }

    # ensure_client_role_policies stub: pretend the three defaults are
    # already wired, and seed the matching role policies into the
    # auth_config so downstream lookups find them.
    def _fake_ensure(auth_config):
        existing = {p.get("name") for p in auth_config.get("policies", [])}
        newly_created = set()
        for role in ("admin", "standard", "view-only"):
            policy_name = f"Policy - {role}"
            if policy_name not in existing:
                auth_config["policies"].append(
                    {"name": policy_name, "type": "role", "config": {}},
                )
                newly_created.add(policy_name)
        return ["admin", "standard", "view-only"], newly_created

    mgr.ensure_client_role_policies = _fake_ensure  # type: ignore[method-assign]

    mgr.get_client_roles = lambda: {  # type: ignore[method-assign]
        "admin": {"id": "uid-admin", "name": "admin"},
        "standard": {"id": "uid-standard", "name": "standard"},
        "view-only": {"id": "uid-view", "name": "view-only"},
    }

    return mgr


def _seed_export(
    mgr: KeycloakSyncManager,
    resources: list[dict] | None = None,
    policies: list[dict] | None = None,
) -> dict:
    """Plant what ``export_configs`` and admin.get_client_authz_resources return."""
    auth_config = {
        "resources": list(resources or []),
        "policies": list(policies or []),
    }
    mgr.kc_manager.export_authorization_settings.return_value = auth_config

    # ``process_model_changes`` pulls the "complete" resource list
    # separately — keep it in sync with the export so the tests don't
    # drift from reality.
    all_resources = [
        {**r, "_id": r.get("_id", f"id-{r['name']}")} for r in (resources or [])
    ]
    mgr.kc_manager.admin.get_client_authz_resources.return_value = all_resources
    mgr.kc_manager.admin.get_client_authz_permissions.return_value = []
    return auth_config


# ---------------------------------------------------------------------
# 1.8 — Add a new model
# ---------------------------------------------------------------------
class TestCluster01f_AddModel(TestCase):
    """Scenario 1.8 — a freshly-added Django model becomes a Keycloak resource."""

    def test_1_8_add_creates_resource_with_default_scopes(self) -> None:
        """
        Scenario 1.8: Add a model, re-run ``lex Init``.

        The customer has one existing model (``myapp.Widget``) and just
        added a second (``myapp.Gadget``). The second run of ``Init``
        must register the new model as a Keycloak resource carrying the
        **six documented default scopes**, and must import the updated
        authorization settings exactly once.
        """
        mgr = _make_sync_manager_with_defaults()
        existing = [{"name": "myapp.Widget"}]
        _seed_export(mgr, resources=existing)

        mgr.process_model_changes(
            adds=[("myapp", "Gadget")],
            deletes=[],
            renames=[],
            preserve_permissions=True,
            ensure_default_authz=False,
        )

        # The final imported config is the dict passed to import_authorization_settings.
        mgr.kc_manager.import_authorization_settings.assert_called_once()
        imported = mgr.kc_manager.import_authorization_settings.call_args[0][0]

        resource_names = [r["name"] for r in imported["resources"]]
        self.assertIn(
            "myapp.Gadget", resource_names,
            "Newly-added Django model must appear as a Keycloak resource — "
            "otherwise the permission system never learns it exists.",
        )

        new_res = next(r for r in imported["resources"] if r["name"] == "myapp.Gadget")
        scope_names = {s.get("name") for s in new_res.get("scopes", [])}
        self.assertEqual(
            scope_names,
            {"list", "read", "create", "edit", "delete", "export"},
            "A freshly-added resource must carry the six documented default "
            "scopes. Customers rely on this being the whole CRUD+export set.",
        )

    def test_1_8b_add_generates_six_scope_permissions(self) -> None:
        """
        Scenario 1.8b: the six per-scope permissions land in the config.

        Without the ``Permission - <resource> - <scope>`` entries, the
        resource exists in Keycloak but no role can actually reach it.
        """
        mgr = _make_sync_manager_with_defaults()
        _seed_export(mgr, resources=[])

        mgr.process_model_changes(
            adds=[("myapp", "Gadget")],
            deletes=[],
            renames=[],
            preserve_permissions=True,
            ensure_default_authz=False,
        )

        imported = mgr.kc_manager.import_authorization_settings.call_args[0][0]
        perm_names = {
            p["name"] for p in imported["policies"]
            if p.get("type") == "scope"
            and p.get("name", "").startswith("Permission - myapp.Gadget")
        }
        expected = {
            f"Permission - myapp.Gadget - {s}"
            for s in ("list", "read", "create", "edit", "delete", "export")
        }
        self.assertEqual(
            perm_names, expected,
            "Every default scope on a new resource must be reachable via "
            "its own scope permission — customers expect all six CRUD+export "
            "rails wired up on add.",
        )

    def test_1_8c_excluded_resource_is_not_synced(self) -> None:
        """
        Scenario 1.8c: even when explicitly passed as an add, framework-
        internal resource names (``audit_logging.AuditLog``) are NEVER
        registered as customer-facing Keycloak resources.
        """
        mgr = _make_sync_manager_with_defaults()
        _seed_export(mgr, resources=[])

        mgr.process_model_changes(
            adds=[("audit_logging", "AuditLog")],
            deletes=[],
            renames=[],
            preserve_permissions=True,
            ensure_default_authz=False,
        )

        imported = mgr.kc_manager.import_authorization_settings.call_args[0][0]
        self.assertNotIn(
            "audit_logging.AuditLog",
            [r["name"] for r in imported["resources"]],
            "Framework-internal resources must never leak into the "
            "customer's Keycloak authorization UI.",
        )


# ---------------------------------------------------------------------
# 1.9 — Rename a model (preserving permissions)
# ---------------------------------------------------------------------
class TestCluster01f_RenameModel(TestCase):
    """Scenario 1.9 — renamed model re-binds its existing permissions."""

    def test_1_9_rename_replaces_old_name_with_new(self) -> None:
        """
        Scenario 1.9: Rename a model, re-run ``lex Init``.

        The old resource name must disappear, the new one must appear,
        and the old Keycloak resource must be deleted on the server so
        two copies never co-exist.
        """
        mgr = _make_sync_manager_with_defaults()
        existing = [
            {"name": "myapp.Widget", "scopes": [{"name": s} for s in ("read", "edit")]},
        ]
        _seed_export(mgr, resources=existing)

        mgr.process_model_changes(
            adds=[],
            deletes=[],
            renames=[("myapp", "Widget", "Gizmo")],
            preserve_permissions=True,
            ensure_default_authz=False,
        )

        imported = mgr.kc_manager.import_authorization_settings.call_args[0][0]
        names = [r["name"] for r in imported["resources"]]
        self.assertNotIn(
            "myapp.Widget", names,
            "After a rename, the old resource name must be gone from the "
            "imported config — duplicates break Keycloak's UMA uniqueness.",
        )
        self.assertIn(
            "myapp.Gizmo", names,
            "After a rename, the new resource name must be present so "
            "permissions can continue to apply without a gap.",
        )

        # The old resource's server-side copy must be deleted via the
        # Keycloak UMA API — confirms we don't leak stale entries.
        mgr.kc_manager.uma.resource_set_delete.assert_called_once_with("id-myapp.Widget")

    def test_1_9b_rename_preserves_existing_permissions(self) -> None:
        """
        Scenario 1.9b: a permission attached to the old name is carried
        across to the new name, with the same apply-policies list.

        This is the customer-visible reason ``--preserve-renamed-permissions``
        defaults to ``True``: a model rename must not silently strip
        access that was already configured.
        """
        mgr = _make_sync_manager_with_defaults()
        existing_resources = [
            {"name": "myapp.Widget", "scopes": [{"name": s} for s in ("read", "edit")]},
        ]
        existing_policies = [
            {
                "name": "Permission - myapp.Widget - read",
                "type": "scope",
                "config": {
                    "resources": json.dumps(["myapp.Widget"]),
                    "scopes": json.dumps(["read"]),
                    "applyPolicies": json.dumps(["Policy - admin", "Policy - view-only"]),
                },
            },
        ]
        _seed_export(mgr, resources=existing_resources, policies=existing_policies)

        mgr.process_model_changes(
            adds=[],
            deletes=[],
            renames=[("myapp", "Widget", "Gizmo")],
            preserve_permissions=True,
            ensure_default_authz=False,
        )

        imported = mgr.kc_manager.import_authorization_settings.call_args[0][0]
        carried = next(
            (
                p for p in imported["policies"]
                if p.get("name") == "Permission - myapp.Gizmo - read"
            ),
            None,
        )
        self.assertIsNotNone(
            carried,
            "The scope permission targeting the old name must be carried "
            "across to the new name — this is the documented intent of "
            "preserve_renamed_permissions=True.",
        )
        self.assertEqual(
            json.loads(carried["config"]["applyPolicies"]),
            ["Policy - admin", "Policy - view-only"],
            "applyPolicies must be copied verbatim from the old permission "
            "so the rename is access-neutral.",
        )
        self.assertEqual(
            json.loads(carried["config"]["resources"]),
            ["myapp.Gizmo"],
            "The preserved permission must now target the *new* resource "
            "name — otherwise it becomes a dangling reference.",
        )


# ---------------------------------------------------------------------
# 1.10 — Delete a model
# ---------------------------------------------------------------------
class TestCluster01f_DeleteModel(TestCase):
    """Scenario 1.10 — a removed Django model is removed from Keycloak."""

    def test_1_10_delete_removes_resource_from_config(self) -> None:
        """
        Scenario 1.10: Delete a model, re-run ``lex Init``.

        The deleted resource must leave the config and its server-side
        entry must be deleted. Every scope permission that referenced it
        must also be stripped — a dangling permission is a footgun.
        """
        mgr = _make_sync_manager_with_defaults()
        existing_resources = [
            {"name": "myapp.Widget"},
            {"name": "myapp.Gadget"},  # survives
        ]
        existing_policies = [
            {
                "name": "Permission - myapp.Widget - read",
                "type": "scope",
                "config": {
                    "resources": json.dumps(["myapp.Widget"]),
                    "scopes": json.dumps(["read"]),
                    "applyPolicies": json.dumps(["Policy - admin"]),
                },
            },
            {
                "name": "Permission - myapp.Gadget - read",
                "type": "scope",
                "config": {
                    "resources": json.dumps(["myapp.Gadget"]),
                    "scopes": json.dumps(["read"]),
                    "applyPolicies": json.dumps(["Policy - admin"]),
                },
            },
        ]
        _seed_export(mgr, resources=existing_resources, policies=existing_policies)

        mgr.process_model_changes(
            adds=[],
            deletes=[("myapp", "Widget")],
            renames=[],
            preserve_permissions=True,
            ensure_default_authz=False,
        )

        imported = mgr.kc_manager.import_authorization_settings.call_args[0][0]
        surviving = [r["name"] for r in imported["resources"]]
        self.assertNotIn(
            "myapp.Widget", surviving,
            "Deleted resource must be removed from the imported config.",
        )
        self.assertIn(
            "myapp.Gadget", surviving,
            "Unrelated surviving resources must not be collateral damage.",
        )

        policy_names = [p.get("name") for p in imported["policies"]]
        self.assertNotIn(
            "Permission - myapp.Widget - read", policy_names,
            "Scope permissions referencing a deleted resource must be "
            "stripped — otherwise Keycloak import fails on dangling refs.",
        )
        self.assertIn(
            "Permission - myapp.Gadget - read", policy_names,
            "Permissions on other resources must be untouched.",
        )

        # And the server-side delete must fire — not just the config edit.
        mgr.kc_manager.uma.resource_set_delete.assert_called_once_with("id-myapp.Widget")


# ---------------------------------------------------------------------
# 1.15 — State file durability
# ---------------------------------------------------------------------
class TestCluster01f_StateFileDurability(TestCase):
    """
    Scenario 1.15: ``.keycloak_state.json`` reflects the current state
    across ``lex Init`` invocations.

    The state file is the only piece of the bootstrap flow that survives
    across processes — so it must roundtrip unchanged through the on-disk
    JSON, and an independent reader must observe exactly what the writer
    wrote. These tests assert that durability contract directly.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state_file = Path(self._tmp.name) / ".keycloak_state.json"
        self._patcher = mock.patch.object(init_module, "STATE_FILE", self.state_file)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmp.cleanup()

    def test_1_15_state_file_roundtrips_through_disk(self) -> None:
        """The write path produces exactly what the read path parses."""
        set_state("bootstrap-abc", "done")
        set_state("bootstrap-def", "cancelled")

        on_disk = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(
            on_disk,
            {"bootstrap-abc": "done", "bootstrap-def": "cancelled"},
            "The JSON written to .keycloak_state.json must be exactly the "
            "state map we handed to set_state — it is the only durable "
            "record of bootstrap status across process boundaries.",
        )

    def test_1_15b_independent_reader_sees_writer_state(self) -> None:
        """
        A brand-new ``_load_state_map`` call (as a separate ``lex Init``
        invocation would do) must see every prior write.
        """
        set_state("s1", "done")
        set_state("s2", "done")

        # Simulate "another process" by calling the loader directly.
        loaded = _load_state_map()
        self.assertEqual(
            loaded.get("s1"), "done",
            "State written by one Init run must be visible to the next.",
        )
        self.assertEqual(get_state("s2"), "done")

    def test_1_15c_absent_state_file_is_empty_not_error(self) -> None:
        """
        Scenario 1.15c: a missing ``.keycloak_state.json`` is the
        first-run condition. It must read as an empty map, not raise —
        otherwise the customer's very first ``lex Init`` crashes.
        """
        self.assertFalse(self.state_file.exists())
        self.assertEqual(
            _load_state_map(), {},
            "Missing state file on a first run must behave like an "
            "empty dict, not raise.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

