"""A closed generic API is "not found" for anonymous callers too.

Intent
------
With ``lex_config.GENERIC_API = "superuser"`` (or ``"off"``) the generic
``api/*`` endpoints should look like paths that do not exist. The login check
runs before the gate, so an anonymous caller used to be sent to sign in
(302/401) and so learnt that the path exists. Now the login check lets such a
request through to the gate, which answers 404. Only the bundled frontend's
session probe ``api/user/`` stays reachable for anonymous callers, so it
answers its own 401 and operators can still sign in; signed-in non-superusers
get 404 there as before. Mode ``"all"`` is unchanged.

Cluster 4s — scenarios 4.114–4.116. Type: U + E.
Covers: lex/api/middleware/generic_api_gate.py (generic_api_hidden_from_anonymous),
lex/authentication/middleware.py (ApiKeyAwareLoginRequiredMiddleware.is_login_required_for_url).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4s_anonymous_generic_api.py -v
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from rest_framework.test import APIClient

from lex.api.middleware.generic_api_gate import generic_api_hidden_from_anonymous
from lex.authentication.middleware import ApiKeyAwareLoginRequiredMiddleware
from lex.core import extensions
from lex.test_project.tests._e2e_test_case import E2ETestCase

pytestmark = pytest.mark.permissions

_FACTORY = RequestFactory()


class _SignedIn:
    is_authenticated = True
    is_superuser = False


def _request(path, user=None):
    request = _FACTORY.get(path)
    request.user = user or AnonymousUser()
    return request


class TestCluster04s_AnonymousGenericApiUnit(SimpleTestCase):
    """Cluster 4s (unit): which anonymous requests skip the login check."""

    # -- 4.114 ---------------------------------------------------------
    def test_4_114_closed_paths_skip_the_login_check_for_anonymous_callers(self) -> None:
        """
        Scenario 4.114: the login check steps aside only where the gate answers 404.
        Given: GENERIC_API "superuser" and "off", a project API under api/sample/
        When: anonymous and signed-in requests for generic, probe, health and project paths
        Then: generic paths skip the login check for anonymous callers only;
              api/user/, api/health and the project API keep it as before;
              mode "all" never skips it.
        """
        middleware = ApiKeyAwareLoginRequiredMiddleware(lambda request: None)
        for mode in ("superuser", "off"):
            with extensions.override_extensions(GENERIC_API=mode, API_URLCONF="lex.test_project.tests.api_layer._project_api",
                                                API_PREFIX="api/sample/"):
                self.assertTrue(generic_api_hidden_from_anonymous(_request("/api/model_entries/x/list")), mode)
                self.assertTrue(generic_api_hidden_from_anonymous(_request("/api/run_step/x/1")), mode)
                self.assertFalse(generic_api_hidden_from_anonymous(_request("/api/user/")), mode)
                self.assertFalse(generic_api_hidden_from_anonymous(_request("/api/health")), mode)
                self.assertFalse(generic_api_hidden_from_anonymous(_request("/api/sample/items")), mode)
                self.assertFalse(generic_api_hidden_from_anonymous(_request("/api/model_entries/x/list", _SignedIn())), mode)
                self.assertFalse(middleware.is_login_required_for_url(_request("/api/model_entries/x/list")), mode)
        with extensions.override_extensions(GENERIC_API="all"):
            self.assertFalse(generic_api_hidden_from_anonymous(_request("/api/model_entries/x/list")))


class TestCluster04s_AnonymousGenericApi(E2ETestCase):
    """Cluster 4s (end to end): what an anonymous caller gets back."""

    e2e_models: list = []

    # -- 4.115 ---------------------------------------------------------
    def test_4_115_anonymous_caller_gets_404_from_a_closed_generic_api(self) -> None:
        """
        Scenario 4.115: no sign-in redirect for a path that "does not exist".
        Given: GENERIC_API="superuser" and an anonymous client
        When: GET /api/model-structure and POST /api/run_step/x/1
        Then: both answer 404 {"code": "NOT_FOUND"}, not a redirect or 401.
        """
        self.use_lex_extensions(GENERIC_API="superuser")
        anonymous = APIClient()
        for response in (anonymous.get("/api/model-structure"), anonymous.post("/api/run_step/x/1", {}, format="json")):
            self.assertEqual(response.status_code, 404, response.content[:200])
            self.assertEqual(response.json()["code"], "NOT_FOUND")

    # -- 4.116 ---------------------------------------------------------
    def test_4_116_session_probe_and_open_mode_keep_the_login_answer(self) -> None:
        """
        Scenario 4.116: sign-in still works, and the default mode is unchanged.
        Given: an anonymous client
        When: GET /api/user/ with GENERIC_API="superuser", and GET /api/model-structure without GENERIC_API
        Then: neither answers 404 — the probe answers its own 401, the open API the
              login redirect, as before.
        """
        anonymous = APIClient()
        open_mode = anonymous.get("/api/model-structure")
        self.use_lex_extensions(GENERIC_API="superuser")
        probe = anonymous.get("/api/user/")
        for response in (open_mode, probe):
            self.assertNotEqual(response.status_code, 404, response.content[:200])
            self.assertIn(response.status_code, (301, 302, 401, 403))
