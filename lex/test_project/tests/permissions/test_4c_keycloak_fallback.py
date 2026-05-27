"""
Cluster 4c: Keycloak scope fallback (no overrides).

Intent (from docs/features/permissions/ and LexModel defaults):

    When a model does NOT override ``permission_read`` /
    ``permission_edit`` etc., the framework falls back to checking the
    Keycloak scopes attached to the :class:`UserContext`:

        * ``"read"``   scope present → ``permission_read`` allows all
        * no scopes                   → deny

:class:`KeycloakItem` has no overrides and is the fixture for this
cluster. :class:`AuthenticatedE2ETestCase` seeds
``keycloak_scopes`` on the UserContext the same way the Keycloak
middleware would in production.

Scenario numbering matches
docs/test-plan/test-clusters.md#4-permissions.
"""

from __future__ import annotations

import unittest

from lex.core.models.LexModel import UserContext
from lex.tests.e2e._authenticated_e2e_test_case import AuthenticatedE2ETestCase

from .models import ALL_MODELS, KeycloakItem

import pytest

pytestmark = pytest.mark.permissions


def _make_uc(user, *, scopes=frozenset()) -> UserContext:
    """Build a UserContext matching what middleware would produce."""
    return UserContext(
        user=user, email=getattr(user, "email", ""),
        is_authenticated=True, is_superuser=False,
        groups=set(), keycloak_scopes=set(scopes),
    )


class TestCluster04c_KeycloakRead(AuthenticatedE2ETestCase):
    """Scope fallback — ``read`` scope present → allow."""

    e2e_models = ALL_MODELS
    extra_keycloak_scopes = frozenset({"read"})

    # -- 4.7 -----------------------------------------------------------
    def test_4_7_keycloak_read_scope_allows_read(self) -> None:
        """Scenario 4.7: ``keycloak_scopes`` contains ``read`` → allow."""
        item = KeycloakItem.objects.create(label="k4-7")
        uc = _make_uc(self.user, scopes=frozenset({"read"}))

        result = item.permission_read(uc)

        self.assertTrue(
            result.allowed,
            "With 'read' in keycloak_scopes, default permission_read must allow",
        )
        self.assertIsNone(
            result.fields,
            "Default fallback returns allow_all — no field restriction",
        )


class TestCluster04c_KeycloakNoScopes(AuthenticatedE2ETestCase):
    """Scope fallback — no scopes → deny."""

    e2e_models = ALL_MODELS

    # -- 4.8 -----------------------------------------------------------
    def test_4_8_keycloak_no_scopes_denies(self) -> None:
        """Scenario 4.8: No scopes → default ``permission_read`` denies."""
        item = KeycloakItem.objects.create(label="k4-8")
        uc = _make_uc(self.user, scopes=frozenset())

        result = item.permission_read(uc)

        self.assertFalse(
            result.allowed,
            "With empty keycloak_scopes, default permission_read must deny",
        )

    # -- 4.8b ----------------------------------------------------------
    def test_4_8b_keycloak_no_scopes_denies_edit_and_export(self) -> None:
        """Supporting: edit / export also deny in the absence of scopes."""
        item = KeycloakItem.objects.create(label="k4-8b")
        uc = _make_uc(self.user, scopes=frozenset())

        self.assertFalse(
            item.permission_edit(uc).allowed,
            "edit must deny when 'edit' scope absent",
        )
        self.assertFalse(
            item.permission_export(uc).allowed,
            "export must deny when 'export' scope absent",
        )


class TestCluster04c_KeycloakEdit(AuthenticatedE2ETestCase):
    """``edit`` scope allows edit but not read."""

    e2e_models = ALL_MODELS
    extra_keycloak_scopes = frozenset({"edit"})

    # -- 4.8c ----------------------------------------------------------
    def test_4_8c_edit_scope_allows_edit_not_read(self) -> None:
        """
        Supporting: scopes are per-action. ``edit`` does not imply ``read``.
        """
        item = KeycloakItem.objects.create(label="k4-8c")
        uc = _make_uc(self.user, scopes=frozenset({"edit"}))

        self.assertTrue(
            item.permission_edit(uc).allowed,
            "'edit' scope must allow permission_edit",
        )
        self.assertFalse(
            item.permission_read(uc).allowed,
            "'edit' scope alone must NOT allow permission_read — scopes "
            "are per-action and must not leak access across actions",
        )


class TestCluster04c_WithInstance(AuthenticatedE2ETestCase):
    """``UserContext.with_instance`` — instance-specific scope resolution."""

    e2e_models = ALL_MODELS

    # -- 4.12 ----------------------------------------------------------
    def test_4_12_with_instance_resolves_instance_scopes(self) -> None:
        """
        Scenario 4.12: ``UserContext.with_instance`` resolves Keycloak
        scopes for the specific record being operated on.

        Intent (from ``_resolve_keycloak_scopes`` docs and production
        KeycloakPermissionsMiddleware behaviour):

            When ``request.user_permissions`` lists UMA permissions
            keyed by resource name (``rsname``) and optional
            ``resource_set_id``, ``UserContext.with_instance`` must
            attach the scopes for the matching permission entry —
            and ONLY that entry — to the new context. Permissions
            for other instances of the same model, or other models
            entirely, must not leak through.

        We build three UMA permissions:

            * one matching the instance by ``(rsname, resource_set_id)``
              — its scopes MUST be attached
            * one matching ``rsname`` but a different ``resource_set_id``
              — its scopes MUST NOT leak through
            * one for a different ``rsname`` entirely — irrelevant, must
              not leak through

        Then we pull the base context, attach the instance, and
        verify exactly the matching scopes are present.
        """
        from django.test import RequestFactory

        item = KeycloakItem.objects.create(label="k4-12")
        rsname = f"{item._meta.app_label}.{item.__class__.__name__}"

        user_permissions = (
            {  # MATCH — rsname + resource_set_id match this instance
                "rsname": rsname,
                "resource_set_id": str(item.pk),
                "scopes": ["read", "edit"],
            },
            {  # Wrong instance of the same model — must not leak
                "rsname": rsname,
                "resource_set_id": "99999",
                "scopes": ["delete"],
            },
            {  # Different model entirely — must not leak
                "rsname": "other_app.OtherModel",
                "resource_set_id": str(item.pk),
                "scopes": ["export"],
            },
        )

        request = RequestFactory().get("/")
        request.user = self.user
        request.user_permissions = user_permissions

        base = UserContext.from_request_base(request)
        uc = base.with_instance(request, item)

        self.assertIn(
            "read", uc.keycloak_scopes,
            "Scopes from the matching (rsname, resource_set_id) "
            "permission must be attached to the per-instance context.",
        )
        self.assertIn(
            "edit", uc.keycloak_scopes,
            "All scopes from the matching permission must be present.",
        )
        self.assertNotIn(
            "delete", uc.keycloak_scopes,
            "Scopes from a DIFFERENT instance of the same model "
            "(wrong resource_set_id) must NOT leak into this "
            "instance's context — per-instance isolation is the "
            "whole point of with_instance.",
        )
        self.assertNotIn(
            "export", uc.keycloak_scopes,
            "Scopes registered under a different rsname must NOT "
            "leak across model boundaries.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

