"""
Authenticated E2E base class.

Extends :class:`E2ETestCase` with a thin layer that shapes the
:class:`~lex.core.models.LexModel.UserContext` the framework sees,
without bypassing the real permission pipeline.

Why this exists
---------------
Cluster-4 and Cluster-6 scenarios need to assert what a user *with a
specific role / scope profile* can and cannot do. In production, the
Keycloak middleware attaches ``request.user_permissions`` and
``request.userinfo['client_roles']`` so that
:meth:`UserContext.from_request` can build a rich context. The stock
``APIClient`` in :class:`E2ETestCase` does not run that middleware, so
:meth:`from_request` builds a context with empty scopes and no roles.

This fixture fills that gap with the smallest possible surface area:

1. Pre-login, it configures the Django user:
   ``as_superuser``, ``extra_groups`` (creates groups if absent).
2. At setUp, it *wraps* (not replaces) :meth:`UserContext.from_request`
   and :meth:`UserContext.from_request_base` so the real implementation
   runs, then the declared ``extra_client_roles`` and
   ``extra_keycloak_scopes`` are merged into the returned context.

The wrap-don't-replace choice is deliberate: the real method is still
executed, so every permission test still exercises
``_api_key_context``, ``_normalize_permissions``, ``_extract_client_roles``,
and ``_resolve_keycloak_scopes`` — the test just seeds the caller with
the identity attributes that a real request would carry.

Usage
-----
::

    class TestProtected(AuthenticatedE2ETestCase):
        e2e_models = ALL_MODELS
        as_superuser = False
        extra_groups = {"hr"}
        extra_keycloak_scopes = frozenset({"read"})

        def test_hr_sees_salary(self):
            ...
"""

from __future__ import annotations

from typing import FrozenSet, Set
from unittest.mock import patch

from django.contrib.auth.models import Group
from lex.core.models.LexModel import UserContext

from ._e2e_test_case import E2ETestCase


class AuthenticatedE2ETestCase(E2ETestCase):
    """
    :class:`E2ETestCase` subclass that lets tests declare the identity
    attributes a Keycloak middleware would normally attach to the
    request.
    """

    #: If True, mark the test user as a Django superuser before login.
    as_superuser: bool = False

    #: Django group names to add the test user to (groups created if absent).
    extra_groups: Set[str] = frozenset()

    #: Keycloak client roles to inject via ``request.userinfo``.
    extra_client_roles: FrozenSet[str] = frozenset()

    #: Keycloak scopes to seed on the returned :class:`UserContext`.
    #: These are merged into ``keycloak_scopes`` by the wrapper below.
    extra_keycloak_scopes: FrozenSet[str] = frozenset()

    def setUp(self):
        super().setUp()

        # -- Shape the Django user -----------------------------------
        if self.as_superuser:
            self.user.is_superuser = True
            self.user.is_staff = True
            self.user.save(update_fields=["is_superuser", "is_staff"])

        for group_name in self.extra_groups:
            group, _ = Group.objects.get_or_create(name=group_name)
            self.user.groups.add(group)

        # -- Wrap UserContext.from_request{,_base} -------------------
        scopes = frozenset(self.extra_keycloak_scopes)
        roles = frozenset(self.extra_client_roles)

        real_from_request = UserContext.from_request.__func__
        real_from_request_base = UserContext.from_request_base.__func__

        def _merge(ctx: UserContext) -> UserContext:
            if not ctx.is_authenticated:
                return ctx  # leave anonymous / api-key contexts alone
            if not scopes and not roles:
                return ctx
            return UserContext(
                user=ctx.user,
                email=ctx.email,
                is_authenticated=ctx.is_authenticated,
                is_superuser=ctx.is_superuser,
                groups=ctx.groups,
                keycloak_scopes=ctx.keycloak_scopes | scopes,
                user_permissions=ctx.user_permissions,
                client_roles=ctx.client_roles | roles,
            )

        def _wrapped_from_request(cls, request, instance=None):
            return _merge(real_from_request(cls, request, instance))

        def _wrapped_from_request_base(cls, request):
            return _merge(real_from_request_base(cls, request))

        self._uc_patches = [
            patch.object(
                UserContext, "from_request",
                classmethod(_wrapped_from_request),
            ),
            patch.object(
                UserContext, "from_request_base",
                classmethod(_wrapped_from_request_base),
            ),
        ]
        for p in self._uc_patches:
            p.start()

    def tearDown(self):
        for p in getattr(self, "_uc_patches", ()):
            p.stop()
        super().tearDown()

