"""
Cluster 1g: ``KeycloakSyncManager`` admin-facing methods.

Intent
------

Sub-cluster 1e covered the pure ``auth_config``-in / ``auth_config``-out
transformations of :class:`KeycloakSyncManager`. Three methods were
still uncovered because they call out to Django's app registry or to
Keycloak's admin API:

* :meth:`KeycloakSyncManager.get_all_django_models` — walks
  ``apps.get_app_configs()``, filters built-ins, respects
  ``settings.repo_name`` scoping, and applies the exclusion rules
  (``legacy_data``, audit-logging tables, ``Historical*`` / ``MetaHistorical*``
  shadow models).
* :meth:`KeycloakSyncManager.get_client_roles` — reads Keycloak's client
  roles, drops ``IGNORED_CLIENT_ROLES``, and lazily creates the three
  defaults (``admin`` / ``standard`` / ``view-only``) if they are
  missing from the customer's realm.
* :meth:`KeycloakSyncManager.ensure_client_role_policies` — makes sure
  every managed role has a matching ``Policy - <role>`` entry in the
  auth config, normalizing the shape of existing ones and appending new
  ones in canonical order.

All three are driven through a stubbed ``kc_manager`` (no Keycloak) and
a patched Django ``apps`` registry (no real model imports). Same
``_make_sync_manager()`` pattern 1e uses.

Scenario numbering extends ``docs/test-plan/test-clusters.md`` —
sub-cluster 1g picks up at **1.44**.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import TestCase, mock

from django.core.management.base import CommandError
from lex.lex_app.management.commands import init as init_module
from lex.lex_app.management.commands.init import (
    DEFAULT_CLIENT_ROLES,
    IGNORED_CLIENT_ROLES,
    KeycloakSyncManager,
)


def _make_sync_manager(client_uuid: str = "test-client-uuid"):
    """Match the 1e fixture — bypass ``__init__``, stub kc_manager."""
    mgr = KeycloakSyncManager.__new__(KeycloakSyncManager)
    mgr.kc_manager = mock.MagicMock()
    mgr.kc_manager.client_uuid = client_uuid
    mgr.kc_manager.last_authz_import_error = None
    mgr.default_scopes = ["list", "read", "create", "edit", "delete", "export"]
    mgr.exported_configs = None
    return mgr


def _fake_model(name: str, *, abstract: bool = False, proxy: bool = False):
    """Build a Django-model stand-in with just the attributes
    ``get_all_django_models`` consults (``__name__`` + ``_meta.abstract``
    + ``_meta.proxy``). No DB, no metaclass, no migrations."""
    return SimpleNamespace(
        __name__=name,
        _meta=SimpleNamespace(abstract=abstract, proxy=proxy),
    )


def _fake_app(label: str, models: list, *, name: str = "", path: str = ""):
    """Build a fake AppConfig. ``name`` and ``path`` control how
    ``is_keycloak_syncable_app`` classifies the app (Django built-in,
    lex-internal, third-party, or user app)."""
    return SimpleNamespace(
        label=label,
        name=name or label,
        path=path,
        get_models=lambda: list(models),
    )


# ---------------------------------------------------------------------
# 1.44 — get_all_django_models
# ---------------------------------------------------------------------
class TestCluster01g_GetAllDjangoModels(TestCase):
    """``get_all_django_models`` walks Django's app registry and
    returns every syncable ``<app>.<Model>`` resource name."""

    def setUp(self):
        self.mgr = _make_sync_manager()

    def _patch_apps(self, configs):
        """Patch the ``apps`` reference used inside init.py only — we
        don't want to mutate Django's real registry."""
        return mock.patch.object(
            init_module.apps, "get_app_configs", return_value=configs,
        )

    def _patch_repo_name(self, repo_name):
        """Help: ``hasattr(settings, "repo_name")`` must be true for the
        repo-scoped branch, and false for the default branch. Patch the
        ``settings`` the module imported, not Django's global one, so
        we don't leak into sibling tests."""
        if repo_name is None:
            # Ensure hasattr returns False.
            return mock.patch.object(
                init_module.settings,
                "repo_name",
                new=mock.DEFAULT,  # placeholder; we delete below
                create=False,
                # We'll handle via a custom context manager below.
            )
        return mock.patch.object(
            init_module.settings, "repo_name", repo_name, create=True,
        )

    def test_1_44_returns_syncable_models_across_apps(self):
        """Scenario 1.44: every concrete, non-excluded model on every
        non-built-in app is returned as ``"<app>.<Model>"``."""
        configs = [
            _fake_app("myapp", [_fake_model("Widget"), _fake_model("Gadget")]),
            _fake_app("otherapp", [_fake_model("Thing")]),
            # Built-in app — must be skipped when repo_name is unset.
            _fake_app("auth", [_fake_model("User")], name="django.contrib.auth"),
        ]
        # Ensure settings has no ``repo_name`` attribute for this branch.
        with self._patch_apps(configs), \
                mock.patch.object(init_module.settings, "repo_name", create=False, new=None) if hasattr(init_module.settings, "repo_name") else mock.patch.dict({}, {}):
            # The above patch is a no-op when repo_name was never set;
            # the command's ``hasattr(settings, 'repo_name')`` gate
            # will still read False because we never set it.
            result = self.mgr.get_all_django_models()

        self.assertEqual(
            result, {"myapp.Widget", "myapp.Gadget", "otherapp.Thing"},
            "Must return every concrete model from non-built-in apps; "
            "built-in ``auth.User`` must be skipped",
        )

    def test_1_44b_abstract_and_proxy_models_are_skipped(self):
        """Scenario 1.44b: abstract and proxy models never get a Keycloak resource.

        A resource for an abstract model would be a Keycloak-side
        ghost that can never be permissioned against."""
        configs = [
            _fake_app("myapp", [
                _fake_model("Real"),
                _fake_model("Abstract", abstract=True),
                _fake_model("Proxy", proxy=True),
            ]),
        ]
        with self._patch_apps(configs):
            result = self.mgr.get_all_django_models()

        self.assertEqual(
            result, {"myapp.Real"},
            "Abstract + proxy models must be skipped — only concrete "
            "tables get Keycloak resources",
        )

    def test_1_44c_excluded_models_are_skipped(self):
        """Scenario 1.44c: ``KEYCLOAK_SYNC_EXCLUDED_*`` rules are
        honoured — ``legacy_data.*``, audit-logging tables, and
        ``Historical*`` / ``MetaHistorical*`` shadow models.

        Note: ``legacy_data`` and ``audit_logging`` are lex-internal apps
        (``lex.*`` prefix), so they are skipped at the app level by
        ``is_keycloak_syncable_app`` before model-level exclusions
        even apply. The model-level exclusions remain a safety net."""
        configs = [
            _fake_app("legacy_data", [_fake_model("LegacyThing")],
                      name="lex.legacy_data"),
            _fake_app("audit_logging", [
                _fake_model("AuditLog"),
                _fake_model("AuditLogStatus"),
                _fake_model("SomeOtherTable"),
            ], name="lex.audit_logging"),
            _fake_app("myapp", [
                _fake_model("Widget"),
                _fake_model("HistoricalWidget"),
                _fake_model("MetaHistoricalWidget"),
            ]),
        ]
        with self._patch_apps(configs):
            result = self.mgr.get_all_django_models()

        self.assertEqual(
            result,
            {"myapp.Widget"},
            "Exclusions: legacy_data (lex-internal app), "
            "audit_logging (lex-internal app), "
            "Historical*/MetaHistorical* (by prefix). "
            "Everything else must survive.",
        )

    def test_1_44d_repo_name_restricts_to_single_app(self):
        """Scenario 1.44d: when ``settings.repo_name`` is set, **only**
        that app's models are returned — the customer's repo is the
        single source of truth in that deployment."""
        configs = [
            _fake_app("customerapp", [_fake_model("Invoice")]),
            _fake_app("otherapp", [_fake_model("Thing")]),
            _fake_app("auth", [_fake_model("User")], name="django.contrib.auth"),
        ]
        with self._patch_apps(configs), \
                mock.patch.object(
                    init_module.settings, "repo_name", "customerapp", create=True,
                ):
            result = self.mgr.get_all_django_models()

        self.assertEqual(
            result, {"customerapp.Invoice"},
            "With repo_name set, only the customer app's models are returned",
        )


# ---------------------------------------------------------------------
# 1.45 — get_client_roles
# ---------------------------------------------------------------------
class TestCluster01g_GetClientRoles(TestCase):
    """``get_client_roles`` — returns managed client roles, creating
    missing defaults on the fly."""

    def setUp(self):
        self.mgr = _make_sync_manager()

    def test_1_45_returns_all_roles_minus_ignored(self):
        """Scenario 1.45: Keycloak-side ignore-list roles are dropped."""
        self.mgr.kc_manager.admin.get_client_roles.return_value = [
            {"name": "admin", "id": "uuid-admin"},
            {"name": "standard", "id": "uuid-std"},
            {"name": "view-only", "id": "uuid-vo"},
            {"name": "manage-client", "id": "uuid-mc"},  # ignored
            {"name": "uma_protection", "id": "uuid-uma"},  # ignored
            {"name": "hr", "id": "uuid-hr"},  # customer extra
        ]

        roles = self.mgr.get_client_roles()

        self.assertEqual(set(roles), {"admin", "standard", "view-only", "hr"})
        for ignored in IGNORED_CLIENT_ROLES:
            self.assertNotIn(
                ignored, roles,
                f"{ignored!r} is in IGNORED_CLIENT_ROLES and must never leak "
                "into the managed-role map",
            )
        # No default is missing → no create call.
        self.mgr.kc_manager.admin.create_client_role.assert_not_called()

    def test_1_45b_creates_missing_default_role(self):
        """Scenario 1.45b: if a default role is missing, it is created
        via ``create_client_role`` and then re-fetched.

        The customer may have removed ``standard`` by hand; init must
        self-heal so the downstream policy pipeline has the role to
        reference."""
        self.mgr.kc_manager.admin.get_client_roles.return_value = [
            {"name": "admin", "id": "uuid-admin"},
            # ``standard`` and ``view-only`` missing — must be created.
        ]

        def _get_role(client_id, role_name):
            return {"name": role_name, "id": f"uuid-{role_name}"}
        self.mgr.kc_manager.admin.get_client_role.side_effect = _get_role

        roles = self.mgr.get_client_roles()

        # Both missing defaults were created with the expected shape.
        created_names = {
            call.kwargs.get("payload", {}).get("name")
            for call in self.mgr.kc_manager.admin.create_client_role.call_args_list
        }
        self.assertEqual(
            created_names, {"standard", "view-only"},
            "Every missing default role must be created exactly once",
        )
        for call in self.mgr.kc_manager.admin.create_client_role.call_args_list:
            payload = call.kwargs.get("payload", {})
            self.assertTrue(
                payload.get("clientRole"),
                "Created roles must carry clientRole=True — realm-role "
                "would point at the wrong role scope",
            )
        self.assertEqual(
            set(roles), {"admin", "standard", "view-only"},
            "After creation the returned map must include every default",
        )

    def test_1_45c_missing_role_id_after_create_raises(self):
        """Scenario 1.45c: if ``get_client_role`` comes back without an
        ``id`` after the create call, that is a data-integrity failure
        and must abort — silent continuation would corrupt every policy
        that references the role downstream."""
        self.mgr.kc_manager.admin.get_client_roles.return_value = []
        self.mgr.kc_manager.admin.get_client_role.return_value = {"name": "admin"}  # no id

        with self.assertRaises(CommandError) as ctx:
            self.mgr.get_client_roles()

        self.assertIn("admin", str(ctx.exception))


# ---------------------------------------------------------------------
# 1.46 — ensure_client_role_policies
# ---------------------------------------------------------------------
class TestCluster01g_EnsureClientRolePolicies(TestCase):
    """``ensure_client_role_policies`` — every managed role has a
    matching ``Policy - <role>`` entry, with the canonical shape
    Keycloak expects on import."""

    def setUp(self):
        self.mgr = _make_sync_manager()
        # Stub out get_client_roles so we don't re-test its internals here.
        self._client_roles = {
            "admin": {"id": "uuid-admin", "name": "admin"},
            "standard": {"id": "uuid-std", "name": "standard"},
            "view-only": {"id": "uuid-vo", "name": "view-only"},
        }
        self.mgr.get_client_roles = lambda: dict(self._client_roles)

    def test_1_46_adds_policies_for_every_managed_role(self):
        """Scenario 1.46: an empty auth_config gains one
        ``Policy - <role>`` per managed role, each referencing the
        role's UUID in the canonical JSON shape."""
        auth_config = {"policies": []}

        ordered, newly_created = self.mgr.ensure_client_role_policies(auth_config)

        # Return value is the ordered role-name list (defaults first).
        self.assertEqual(
            ordered, list(DEFAULT_CLIENT_ROLES),
            "Ordered role names must follow DEFAULT_CLIENT_ROLES order",
        )

        policy_names = [p["name"] for p in auth_config["policies"]]
        self.assertEqual(
            policy_names,
            ["Policy - admin", "Policy - standard", "Policy - view-only"],
            "One Policy entry per managed role, in canonical order",
        )

        for policy, role_name in zip(auth_config["policies"], DEFAULT_CLIENT_ROLES):
            with self.subTest(role=role_name):
                self.assertEqual(policy["type"], "role")
                self.assertEqual(policy["logic"], "POSITIVE")
                self.assertEqual(policy["decisionStrategy"], "UNANIMOUS")
                canonical = json.loads(policy["config"]["roles"])
                self.assertEqual(
                    canonical,
                    [{"id": self._client_roles[role_name]["id"], "required": True}],
                    "Canonical roles JSON must carry the role's UUID — "
                    "Keycloak rejects imports that reference roles by name only",
                )
                self.assertNotIn(
                    "roles", policy,
                    "Top-level ``roles`` key must not exist — everything "
                    "lives in ``config.roles`` as a JSON string",
                )

    def test_1_46b_existing_policy_is_normalized_in_place(self):
        """Scenario 1.46b: a pre-existing ``Policy - admin`` with a
        drifted shape (wrong ``type``, top-level ``roles`` key, stale
        ``config.roles`` JSON) is rewritten in place — not duplicated.

        Customers who hand-edit policies between runs must not see
        ghost copies appear."""
        auth_config = {
            "policies": [
                {
                    "name": "Policy - admin",
                    "type": "regex",  # wrong
                    "logic": "NEGATIVE",  # wrong
                    "decisionStrategy": "AFFIRMATIVE",  # wrong
                    "config": {"roles": '[{"id":"stale","required":false}]'},
                    "roles": [{"name": "admin"}],  # legacy top-level — must go
                },
            ],
        }

        self.mgr.ensure_client_role_policies(auth_config)

        admin_policies = [p for p in auth_config["policies"] if p["name"] == "Policy - admin"]
        self.assertEqual(
            len(admin_policies), 1,
            "Existing policy must be normalized in place, not duplicated",
        )
        policy = admin_policies[0]
        self.assertEqual(policy["type"], "role")
        self.assertEqual(policy["logic"], "POSITIVE")
        self.assertEqual(policy["decisionStrategy"], "UNANIMOUS")
        self.assertNotIn(
            "roles", policy,
            "Legacy top-level ``roles`` must be stripped on normalization",
        )
        canonical = json.loads(policy["config"]["roles"])
        self.assertEqual(
            canonical, [{"id": "uuid-admin", "required": True}],
            "Canonical roles must be refreshed to the current role UUID",
        )

    def test_1_46c_extra_customer_role_becomes_policy(self):
        """Scenario 1.46c: a customer-extra role (``hr``) returned by
        ``get_client_roles`` picks up a ``Policy - hr`` entry, and is
        ordered after the three defaults in the returned list."""
        self._client_roles["hr"] = {"id": "uuid-hr", "name": "hr"}
        auth_config = {"policies": []}

        ordered, newly_created = self.mgr.ensure_client_role_policies(auth_config)

        self.assertEqual(
            ordered, ["admin", "standard", "view-only", "hr"],
            "Extras come after defaults in canonical order",
        )
        policy_names = [p["name"] for p in auth_config["policies"]]
        self.assertIn("Policy - hr", policy_names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

