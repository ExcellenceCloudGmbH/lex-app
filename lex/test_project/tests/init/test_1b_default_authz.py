"""
Cluster 1b (cont.): Default roles and scope→policy mapping contract.

Intent (from docs/features/access-and-ui/permissions.md):

Every customer project ships with three default roles —
``admin``, ``standard``, ``view-only`` — and a scope→policy mapping
that enforces a sensible baseline:

    * ``list``, ``read``      → all three roles
    * ``create``, ``delete``  → admin only
    * ``edit``, ``export``    → admin + standard

These are **contracts with the customer**. If they change silently,
customers lose access or gain access they should not have.

Scenario numbering matches
docs/test-plan/test-clusters.md#1-init--project-bootstrap.
"""

from __future__ import annotations

import unittest
from unittest import TestCase

import pytest

pytestmark = pytest.mark.init


class TestCluster01b_DefaultAuthzContract(TestCase):
    """Published defaults must not drift silently."""

    # -- 1.11 ----------------------------------------------------------
    def test_1_11_default_client_roles_registered(self) -> None:
        """Scenario 1.11: Default roles are ``admin``, ``standard``, ``view-only``."""
        from lex.lex_app.management.commands.init import DEFAULT_CLIENT_ROLES

        self.assertEqual(
            set(DEFAULT_CLIENT_ROLES),
            {"admin", "standard", "view-only"},
            "The three default roles are a published contract with customers. "
            "Changing them silently is a breaking change.",
        )

    # -- 1.12 ----------------------------------------------------------
    def test_1_12_scope_policy_mapping_matches_documented_contract(self) -> None:
        """Scenario 1.12: Default scope → policy mapping."""
        from lex.lex_app.management.commands.init import (
            DEFAULT_SCOPE_POLICY_MAPPING,
        )
        admin, standard, viewonly = (
            "Policy - admin", "Policy - standard", "Policy - view-only",
        )

        self.assertEqual(
            set(DEFAULT_SCOPE_POLICY_MAPPING["create"]), {admin},
            "Only admins may create resources by default",
        )
        self.assertEqual(
            set(DEFAULT_SCOPE_POLICY_MAPPING["delete"]), {admin},
            "Only admins may delete resources by default",
        )
        self.assertEqual(
            set(DEFAULT_SCOPE_POLICY_MAPPING["edit"]), {admin, standard},
            "Admin + standard may edit; view-only may not",
        )
        self.assertEqual(
            set(DEFAULT_SCOPE_POLICY_MAPPING["export"]), {admin, standard},
            "Admin + standard may export; view-only may not",
        )
        self.assertEqual(
            set(DEFAULT_SCOPE_POLICY_MAPPING["read"]), {admin, standard, viewonly},
            "All three roles may read",
        )
        self.assertEqual(
            set(DEFAULT_SCOPE_POLICY_MAPPING["list"]), {admin, standard, viewonly},
            "All three roles may list",
        )

    # -- 1.12b ---------------------------------------------------------
    def test_1_12b_every_default_scope_has_a_policy(self) -> None:
        """
        Every one of the six default scopes (``list``, ``read``, ``create``,
        ``edit``, ``delete``, ``export``) must have at least one policy
        assigned. Otherwise a customer would get a scope that no role has
        access to — silently broken.
        """
        from lex.lex_app.management.commands.init import (
            DEFAULT_SCOPE_POLICY_MAPPING,
        )
        for scope in ("list", "read", "create", "edit", "delete", "export"):
            policies = DEFAULT_SCOPE_POLICY_MAPPING.get(scope, [])
            self.assertTrue(
                policies,
                f"Scope {scope!r} has no default policies — no role can "
                f"use it. This is a silent permission bug.",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
