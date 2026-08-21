"""
Cluster 1ab: Ignored client-role self-cleanup (``client-admin`` platform role).

Intent (from local_wiki/projects/admin-role-separation-5/README.md and
``lex/lex_app/management/commands/init.py``):

    The platform introduces a per-client Keycloak client role
    ``client-admin`` that is platform-internal — it must never receive
    tenant-app authorization. ``lex init`` already builds tenant authz
    from every role found on the Keycloak client and mints a
    ``Policy - <role>`` for anything it doesn't recognize; roles listed in
    ``IGNORED_CLIENT_ROLES`` (``manage-client``, ``uma_protection``,
    ``client-admin``) are meant to get nothing. But the sync only ever
    ADDS policies via Keycloak's import endpoint — it never removes an
    entry just because it's absent from the re-imported payload — so an
    instance that minted ``Policy - client-admin`` while running an older
    lex-app (before the role was ignored) would carry that stale grant
    forever without an explicit cleanup step.

    These tests exercise the two halves of that cleanup:
    ``strip_ignored_role_policies`` (pure ``auth_config`` dict transform:
    drops the ignored-role policy and detaches its name from any
    permission's ``applyPolicies``) and ``delete_stale_ignored_role_policies``
    (the live Keycloak side: finds and deletes the policy by name via the
    admin API once nothing references it). Both are stubbed-``kc_manager``
    unit tests — no real Keycloak — matching the pattern established in
    cluster 1e.

Scenario numbering extends
lex/test_project/test-plan/clusters/01-init/cluster.md (new sub-cluster 1ab).
"""

from __future__ import annotations

import json
from unittest import TestCase, mock

from django.core.management.base import CommandError
from lex.lex_app.management.commands.init import (
    IGNORED_CLIENT_ROLES,
    KeycloakSyncManager,
)

import pytest

pytestmark = pytest.mark.init


# ---------------------------------------------------------------------
# Fixture: a KeycloakSyncManager whose kc_manager is a MagicMock.
# Mirrors cluster 1e's ``_make_sync_manager`` (bypasses __init__, which
# reads .env and connects to Keycloak).
# ---------------------------------------------------------------------
def _make_sync_manager(client_uuid: str = "test-client-uuid"):
    mgr = KeycloakSyncManager.__new__(KeycloakSyncManager)
    mgr.kc_manager = mock.MagicMock()
    mgr.kc_manager.client_uuid = client_uuid
    mgr.kc_manager.last_authz_import_error = None
    mgr.default_scopes = ["list", "read", "create", "edit", "delete", "export"]
    mgr.exported_configs = None
    return mgr


# ---------------------------------------------------------------------
# 1.223 — IGNORED_CLIENT_ROLES carries client-admin, not release-manager
# ---------------------------------------------------------------------
class TestCluster01ab_IgnoredClientRolesSet(TestCase):
    """The abandoned ``release-manager`` design must not linger in the ignore set."""

    def test_1_223_client_admin_is_ignored_release_manager_is_not(self):
        """
        Scenario 1.223: ``client-admin`` is ignored (platform-internal,
        never gets tenant authz); the superseded ``release-manager`` name
        is gone from the set entirely.
        """
        self.assertIn("client-admin", IGNORED_CLIENT_ROLES)
        self.assertNotIn("release-manager", IGNORED_CLIENT_ROLES)
        self.assertIn("manage-client", IGNORED_CLIENT_ROLES)
        self.assertIn("uma_protection", IGNORED_CLIENT_ROLES)


# ---------------------------------------------------------------------
# 1.224-1.226 — strip_ignored_role_policies (in-memory auth_config)
# ---------------------------------------------------------------------
class TestCluster01ab_StripIgnoredRolePolicies(TestCase):
    """``strip_ignored_role_policies`` drops the stale policy and detaches references."""

    def test_1_224_removes_ignored_role_policy_from_config(self):
        """
        Scenario 1.224: a pre-existing ``Policy - client-admin`` (minted by
        an older lex-app) is removed from ``auth_config['policies']`` so
        the re-import doesn't simply resend it unchanged.
        """
        mgr = _make_sync_manager()
        auth_config = {
            "policies": [
                {"name": "Policy - admin", "type": "role", "config": {"roles": "[]"}},
                {"name": "Policy - client-admin", "type": "role", "config": {"roles": "[]"}},
            ]
        }

        mgr.strip_ignored_role_policies(auth_config)

        remaining_names = {p.get("name") for p in auth_config["policies"]}
        self.assertEqual(
            remaining_names, {"Policy - admin"},
            "the ignored-role policy must be dropped; unrelated policies stay",
        )

    def test_1_225_detaches_reference_from_permission_apply_policies(self):
        """
        Scenario 1.225: a permission that lists ``Policy - client-admin``
        in ``config.applyPolicies`` has that name removed, leaving the
        other applied policies untouched.
        """
        mgr = _make_sync_manager()
        auth_config = {
            "policies": [
                {"name": "Policy - admin", "type": "role", "config": {"roles": "[]"}},
                {"name": "Policy - client-admin", "type": "role", "config": {"roles": "[]"}},
                {
                    "name": "Permission - myapp.A - read",
                    "type": "scope",
                    "config": {
                        "resources": json.dumps(["myapp.A"]),
                        "scopes": json.dumps(["read"]),
                        "applyPolicies": json.dumps(
                            ["Policy - admin", "Policy - client-admin", "Policy - standard"]
                        ),
                    },
                },
            ]
        }

        mgr.strip_ignored_role_policies(auth_config)

        permission = next(
            p for p in auth_config["policies"] if p.get("name") == "Permission - myapp.A - read"
        )
        applied = json.loads(permission["config"]["applyPolicies"])
        self.assertEqual(
            applied, ["Policy - admin", "Policy - standard"],
            "the ignored-role reference must be detached; other applied policies survive in order",
        )

    def test_1_226_noop_when_no_ignored_policy_present(self):
        """
        Scenario 1.226: the normal case — no stale ignored-role policy in
        the export — is a no-op (idempotent, nothing to clean up).
        """
        mgr = _make_sync_manager()
        auth_config = {
            "policies": [
                {"name": "Policy - admin", "type": "role", "config": {"roles": "[]"}},
                {
                    "name": "Permission - myapp.A - read",
                    "type": "scope",
                    "config": {
                        "resources": json.dumps(["myapp.A"]),
                        "scopes": json.dumps(["read"]),
                        "applyPolicies": json.dumps(["Policy - admin"]),
                    },
                },
            ]
        }
        before = json.loads(json.dumps(auth_config))  # deep copy for comparison

        mgr.strip_ignored_role_policies(auth_config)

        self.assertEqual(auth_config, before, "nothing to clean up must leave the config untouched")


# ---------------------------------------------------------------------
# 1.227-1.229 — delete_stale_ignored_role_policies (live Keycloak side)
# ---------------------------------------------------------------------
class TestCluster01ab_DeleteStaleIgnoredRolePolicies(TestCase):
    """``delete_stale_ignored_role_policies`` finds and deletes by name via the admin API."""

    def test_1_227_deletes_live_client_admin_policy_by_id(self):
        """
        Scenario 1.227: a live ``Policy - client-admin`` (found via
        ``get_client_authz_policies``) is deleted via
        ``delete_client_authz_policy`` using its id; unrelated policies
        are left alone.
        """
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client_authz_policies.return_value = [
            {"id": "policy-admin-1", "name": "Policy - admin"},
            {"id": "policy-client-admin-1", "name": "Policy - client-admin"},
        ]

        mgr.delete_stale_ignored_role_policies()

        mgr.kc_manager.admin.delete_client_authz_policy.assert_called_once_with(
            client_id="test-client-uuid", policy_id="policy-client-admin-1",
        )

    def test_1_228_noop_when_no_ignored_policy_live(self):
        """Scenario 1.228: the normal/healed case — nothing named ``Policy - <ignored role>`` exists live — deletes nothing."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client_authz_policies.return_value = [
            {"id": "policy-admin-1", "name": "Policy - admin"},
        ]

        mgr.delete_stale_ignored_role_policies()

        mgr.kc_manager.admin.delete_client_authz_policy.assert_not_called()

    def test_1_229_ignored_policy_without_id_raises(self):
        """
        Scenario 1.229: a matching policy record without an ``id`` is a
        data-integrity problem and must abort — mirrors the equivalent
        resource/permission-id fail-fast in ``delete_resources_individual``.
        """
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client_authz_policies.return_value = [
            {"name": "Policy - client-admin"},  # no id
        ]

        with self.assertRaises(CommandError) as ctx:
            mgr.delete_stale_ignored_role_policies()
        self.assertIn("Policy has no id", str(ctx.exception))
