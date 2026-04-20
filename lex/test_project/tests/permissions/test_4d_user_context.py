"""
Cluster 4d: ``UserContext`` construction.

Intent (from docs/reference/LexModel Internals.md):

    :meth:`UserContext.from_request` builds a frozen dataclass from a
    request object, pulling:
      - ``email`` from the Django ``User``
      - ``is_superuser`` / ``is_authenticated`` flags
      - ``groups`` from ``user.groups``
      - ``keycloak_scopes`` from the middleware-attached scope set
      - ``client_roles`` from ``userinfo.client_roles``

    API-key authenticated contexts must have ``"api_key"`` in
    ``client_roles``.

Scenario numbering matches
docs/test-plan/test-clusters.md#4-permissions.
"""

from __future__ import annotations

import unittest

from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase

from lex.core.models.LexModel import UserContext


class TestCluster04d_UserContext(TestCase):
    """UserContext.from_request / from_request_base contract."""

    # -- 4.10 ----------------------------------------------------------
    def test_4_10_from_request_base_populates_user_and_email(self) -> None:
        """
        Scenario 4.10: ``UserContext.from_request`` builds correct context.

        We assert the field-independent subset: email, is_authenticated,
        is_superuser flow through from the Django user. Group- /
        scope- / role-extraction paths are covered by unit tests in
        ``lex.tests.unit.auth.*``.
        """
        user = User.objects.create_user(
            username="u4_10", password="pw", email="u4_10@test.local",
        )
        request = RequestFactory().get("/")
        request.user = user

        uc = UserContext.from_request_base(request)

        self.assertEqual(uc.email, "u4_10@test.local")
        self.assertTrue(
            uc.is_authenticated,
            "A logged-in user must produce an authenticated context",
        )
        self.assertFalse(uc.is_superuser, "Plain user is not superuser")

    def test_4_10b_anonymous_request_yields_unauthenticated_context(self) -> None:
        """Supporting assertion: anonymous request → is_authenticated=False."""
        request = RequestFactory().get("/")
        request.user = AnonymousUser()

        uc = UserContext.from_request_base(request)

        self.assertFalse(
            uc.is_authenticated,
            "Anonymous request must yield is_authenticated=False",
        )

    # -- 4.11 ----------------------------------------------------------
    @unittest.skip(
        "Scenario 4.11: API-key context — ``client_roles`` includes "
        "'api_key'. Needs the API-key authentication middleware to "
        "attach identity metadata to the request; not wired in "
        "E2ETestCase. Covered by lex.tests.unit.auth.test_api_key_user_context."
    )
    def test_4_11_api_key_context_includes_api_key_role(self) -> None:
        """Scenario 4.11: API-key client_roles must include 'api_key'."""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

