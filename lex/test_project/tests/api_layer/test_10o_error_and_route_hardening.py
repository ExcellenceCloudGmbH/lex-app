"""Error bodies, OIDC redirects, unknown api/ paths, global search and Keycloak per process.

Intent
------
* TRACEBACKS – an error response of the generic endpoints carries no server
  traceback unless ``DEBUG`` is on or ``LEX_API_TRACEBACKS=true`` (or the
  hardening is switched off). The rest of the body (``error``) stays, because
  the bundled frontend shows it. Calculation error fields and websocket
  messages are not HTTP error bodies and are unchanged.
* OIDC_REDIRECT – ``next``/``fail`` on ``/oidc/*`` (sign-in, logout,
  total_logout) must be relative, the site's own host, or an origin in
  ``CSRF_TRUSTED_ORIGINS`` / the CORS allow-list (``LEX_CORS_ALLOWED_ORIGINS``),
  so the Vite dev frontend on :3000 keeps working; anything else is 400 before
  the OIDC view runs (an open redirect before).
* An unknown path under ``api/`` answers a JSON 404 instead of the app's
  index.html with 200; real routes and the app's own routes are unaffected.
* ``api/global-search/?q=<text>`` — the form the bundled frontend calls — is
  routed beside ``api/global-search/<text>``.
* One KeycloakManager per process instead of one per request.

Cluster 10o — scenarios 10.83–10.90. Type: E (10.85, 10.88, 10.89 U).
Covers: lex/core/security_flags.py (expose_tracebacks), lex/api/security.py
(error_detail), lex/api/views/model_entries/One.py and
lex/api/views/process_flow/CreateOrUpdate.py (error bodies),
lex/authentication/middleware.py (OidcRedirectGuardMiddleware),
lex/lex_app/settings.py (MIDDLEWARE), lex/react/views.py (serve_react),
lex/process_admin/sites/process_admin_site.py and
lex/api/views/global_search_for_models/Search.py (?q=),
lex/api/middleware/keycloak_permissions.py (_shared_manager).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_error_and_route_hardening.py -v
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest import mock

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from lex.api.middleware import keycloak_permissions
from lex.api.middleware.keycloak_permissions import KeycloakPermissionsMiddleware
from lex.authentication.middleware import OidcRedirectGuardMiddleware
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import EXPLODING, HARDENING_MODELS, ApiSimpleItem, ExplodingItem

pytestmark = pytest.mark.api_layer

_SWITCHES = ("LEX_SEC_", "LEX_LEGACY_SECURITY", "LEX_API_TRACEBACKS")


def _env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(_SWITCHES)}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


@override_settings(DEBUG=False)
class TestCluster10o_Tracebacks(E2ETestCase):
    """TRACEBACKS: what a failing generic request answers."""

    e2e_models = HARDENING_MODELS

    def _failures(self):
        create = self.client.post(self.url_create(EXPLODING), {"name": "explode"}, format="json")
        run_step = self.client.patch(
            reverse("process_admin_rest_api:run_step",
                    kwargs={"model_container": _MockContainer(EXPLODING), "pk": 999999}),
            {"name": "explode"}, format="json",
        )
        with mock.patch.object(ExplodingItem, "permission_create", side_effect=RuntimeError("hook down")):
            refused = self.client.post(self.url_create(EXPLODING), {"name": "fine"}, format="json")
        return {"create": create, "run_step": run_step, "permission_create raised": refused}

    # -- 10.83 ---------------------------------------------------------
    def test_10_83_error_bodies_carry_no_traceback_by_default(self) -> None:
        """
        Scenario 10.83: a failing request answers 500 without a server traceback.
        Given: DEBUG off, default flags, a model whose save() raises
        When: a row is created through the CRUD endpoint and through run_step,
              and a create is refused because permission_create raised
        Then: each answers 500 with a readable "error" and no "traceback";
              nothing is stored.
        """
        with _env():
            answers = self._failures()
        for what, response in answers.items():
            self.assertEqual(response.status_code, 500, what)
            body = response.json()
            self.assertTrue(body.get("error"), f"{what}: the error text must stay: {body}")
            self.assertNotIn("traceback", body, what)
        self.assertIn("save failed on purpose", answers["create"].json()["error"])
        self.assertFalse(ExplodingItem.objects.exists())

    # -- 10.84 ---------------------------------------------------------
    def test_10_84_tracebacks_come_back_only_when_allowed(self) -> None:
        """
        Scenario 10.84: the traceback is shown when explicitly allowed.
        Given: the same failing create
        When: LEX_API_TRACEBACKS=true; LEX_LEGACY_SECURITY=true; DEBUG on;
              DEBUG on with LEX_API_TRACEBACKS=false
        Then: a non-empty "traceback" naming the error in the first three;
              none in the last (the explicit switch wins over DEBUG).
        """
        def traceback_of():
            body = self.client.post(self.url_create(EXPLODING), {"name": "explode"}, format="json").json()
            return body.get("traceback", "")

        with _env(LEX_API_TRACEBACKS="true"):
            self.assertIn("save failed on purpose", traceback_of())
        with _env(LEX_LEGACY_SECURITY="true"):
            self.assertIn("save failed on purpose", traceback_of())
        with _env(), override_settings(DEBUG=True):
            self.assertIn("save failed on purpose", traceback_of())
        with _env(LEX_API_TRACEBACKS="false"), override_settings(DEBUG=True):
            self.assertEqual(traceback_of(), "")


_FACTORY = RequestFactory()


def _guard(request):
    return OidcRedirectGuardMiddleware(lambda r: HttpResponse("passed"))(request)


@override_settings(
    ALLOWED_HOSTS=["lex.example.com", "testserver"],
    CSRF_TRUSTED_ORIGINS=["https://*.example.org", "http://localhost:3000"],
    CORS_ALLOWED_ORIGINS=["https://app.partner.test", "*"],
    CORS_ORIGIN_WHITELIST=["https://grid.partner.test"],
)
class TestCluster10o_OidcRedirects(SimpleTestCase):
    """OIDC_REDIRECT: the guard in front of oauth2_authcodeflow's views."""

    # -- 10.85 ---------------------------------------------------------
    def test_10_85_targets_must_stay_on_allowed_origins(self) -> None:
        """
        Scenario 10.85: next/fail are relative, the own host or a trusted origin.
        Given: a site on lex.example.com, trusted origins https://*.example.org and
               http://localhost:3000, CORS origins https://app.partner.test and
               https://grid.partner.test
        When: /oidc/authenticate, /oidc/logout and /oidc/total_logout are asked
              with various next/fail targets
        Then: relative paths, the own host, a wildcard match, the Vite dev origin
              and both CORS origins pass; a foreign host, a protocol-relative or
              backslash target, a javascript: URL and a look-alike host answer 400
              {code: REDIRECT_NOT_ALLOWED, error}; paths outside /oidc/ are not inspected.
        """
        allowed = [
            "/", "/process_admin/all?tab=1", "https://lex.example.com/funds",
            "https://portal.example.org/", "http://localhost:3000/", "https://app.partner.test/x",
            "https://grid.partner.test",
        ]
        refused = [
            "https://evil.test/", "//evil.test/", "/\\evil.test", "javascript:alert(1)",
            "https://example.org.evil.test/", "http://localhost:3001/", "https://user@evil.test/",
        ]
        with _env():
            for path in ("/oidc/authenticate", "/oidc/logout", "/oidc/total_logout"):
                for target in allowed:
                    for param in ("next", "fail"):
                        request = _FACTORY.get(path, {param: target}, HTTP_HOST="lex.example.com")
                        self.assertEqual(_guard(request).content, b"passed", f"{path} {param}={target}")
                for target in refused:
                    response = _guard(_FACTORY.get(path, {"next": "/", "fail": target}, HTTP_HOST="lex.example.com"))
                    self.assertEqual(response.status_code, 400, f"{path} fail={target}")
                    body = json.loads(response.content)
                    self.assertEqual(body["code"], "REDIRECT_NOT_ALLOWED")
                    self.assertTrue(body["error"])
            outside = _FACTORY.get("/api/user/", {"next": "https://evil.test/"}, HTTP_HOST="lex.example.com")
            self.assertEqual(_guard(outside).content, b"passed")
            with _env(LEX_SEC_OIDC_REDIRECT="false"):
                request = _FACTORY.get("/oidc/authenticate", {"next": "https://evil.test/"}, HTTP_HOST="lex.example.com")
                self.assertEqual(_guard(request).content, b"passed", "the opt-out restores the old behaviour")


class TestCluster10o_OidcRedirectInStack(E2ETestCase):
    """The guard runs in lex's middleware stack, before the OIDC view."""

    e2e_models = HARDENING_MODELS

    # -- 10.86 ---------------------------------------------------------
    def test_10_86_foreign_target_never_reaches_the_oidc_view(self) -> None:
        """
        Scenario 10.86: GET /oidc/total_logout?next=https://evil.test/ is refused.
        Given: lex's MIDDLEWARE and URLconf
        When: the sign-in and total-logout views are asked for a foreign target
        Then: 400 REDIRECT_NOT_ALLOWED from the guard; the guard is installed
              before the session middleware.
        """
        from django.conf import settings

        middleware = list(settings.MIDDLEWARE)
        guard = "lex.authentication.middleware.OidcRedirectGuardMiddleware"
        self.assertIn(guard, middleware)
        self.assertLess(middleware.index(guard), middleware.index("django.contrib.sessions.middleware.SessionMiddleware"))
        with _env():
            for path in ("/oidc/authenticate", "/oidc/total_logout"):
                response = self.client.get(path, {"next": "https://evil.test/"})
                self.assertEqual(response.status_code, 400, path)
                self.assertEqual(response.json()["code"], "REDIRECT_NOT_ALLOWED")


class TestCluster10o_UnknownApiPaths(E2ETestCase):
    """An unknown endpoint below api/ is a JSON 404, not the SPA."""

    e2e_models = HARDENING_MODELS

    # -- 10.87 ---------------------------------------------------------
    def test_10_87_unknown_api_path_is_json_404(self) -> None:
        """
        Scenario 10.87: the SPA catch-all leaves api/ alone.
        Given: a signed-in client and lex's URLconf
        When: it asks for api/no-such-endpoint and api/, and for real routes
              (api/health, the list of a registered model)
        Then: JSON 404 with a readable error for the unknown paths; the real
              routes answer 200 as before.
        """
        with _env():
            for path in ("/api/no-such-endpoint", "/api/funds/overview/", "/api/"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404, path)
                self.assertEqual(response["Content-Type"], "application/json", path)
                self.assertTrue(response.json()["error"])
            self.assertEqual(self.client.get("/api/health").status_code, 200)
            ApiSimpleItem.objects.create(name="listed")
            self.assertEqual(self.client.get(self.url_list("apisimpleitem")).status_code, 200)


class TestCluster10o_ServeReact(SimpleTestCase):
    """serve_react: api/ is never answered with the app's index.html."""

    # -- 10.88 ---------------------------------------------------------
    def test_10_88_spa_routes_keep_index_and_api_paths_do_not(self) -> None:
        """
        Scenario 10.88: what the catch-all answers, also below DJANGO_BASE_PATH.
        Given: a build with index.html
        When: serve_react is asked for api/x, api, an app route and the root, with
              and without DJANGO_BASE_PATH=/portal, and in legacy mode
        Then: 404 for the api paths, 200 (the index) for the app routes; legacy
              mode serves the index for api/x as before.
        """
        from lex.react.views import serve_react

        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "index.html"), "w") as handle:
                handle.write("<html></html>")

            def answer(path):
                return serve_react(RequestFactory().get(f"/{path}"), path, document_root=root).status_code

            with _env(DJANGO_BASE_PATH=""):
                self.assertEqual([answer(p) for p in ("api/x", "api", "funds/overview", "")], [404, 404, 200, 200])
            with _env(DJANGO_BASE_PATH="/portal"):
                self.assertEqual([answer(p) for p in ("portal/api/x", "portal/funds", "apis/page")], [404, 200, 200])
            with _env(LEX_LEGACY_SECURITY="true", DJANGO_BASE_PATH=""):
                self.assertEqual(answer("api/x"), 200)


class TestCluster10o_GlobalSearchQuery(E2ETestCase):
    """api/global-search/?q= — the form the bundled frontend calls."""

    e2e_models = HARDENING_MODELS

    # -- 10.89 ---------------------------------------------------------
    def test_10_89_global_search_accepts_q(self) -> None:
        """
        Scenario 10.89: both search URL forms reach the search.
        Given: an ApiSimpleItem named "needle in a haystack" (search limited to its model)
        When: api/global-search/?q=needle, api/global-search/needle and
              api/global-search/?q= are requested
        Then: the first two answer 200 with the row as the only result; the empty
              query answers the "No match found" sentinel; the ?q= path resolves to
              the Search view.
        """
        from lex.process_admin.settings import processAdminSite

        item = ApiSimpleItem.objects.create(name="needle in a haystack")
        container = processAdminSite.model_collection.get_container("apisimpleitem")
        self.assertEqual(resolve("/api/global-search/").func.view_class.__name__, "Search")
        collection_class = type(processAdminSite.model_collection)
        with mock.patch.object(collection_class, "all_containers", new_callable=mock.PropertyMock, return_value=[container]):
            by_query = self.client.get("/api/global-search/", {"q": "needle"})
            by_path = self.client.get("/api/global-search/needle")
            empty = self.client.get("/api/global-search/", {"q": ""})
        for response in (by_query, by_path):
            self.assertEqual(response.status_code, 200, response.content[:300])
            self.assertEqual([match["id"] for match in response.json()["data"]], [str(item.pk)])
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json(), "No match found")


class _FakeManager:
    instances = 0

    def __init__(self):
        type(self).instances += 1
        self.oidc = mock.MagicMock()
        self.oidc.userinfo.return_value = {"client_roles": ["analyst"]}

    def get_uma_permissions(self, token):
        return [{"rsname": "app.Model", "resource_set_id": None, "scopes": ["read"]}]


class TestCluster10o_KeycloakManagerPerProcess(SimpleTestCase):
    """KeycloakPermissionsMiddleware keeps one KeycloakManager per process."""

    def setUp(self) -> None:
        keycloak_permissions._reset_shared_manager()
        self.addCleanup(keycloak_permissions._reset_shared_manager)
        _FakeManager.instances = 0
        patcher = mock.patch.object(keycloak_permissions, "KeycloakManager", _FakeManager)
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- 10.90 ---------------------------------------------------------
    def test_10_90_one_manager_for_many_requests(self) -> None:
        """
        Scenario 10.90: requests share one KeycloakManager; answers are unchanged.
        Given: three requests with bearer tokens and one without
        When: the middleware handles them
        Then: the manager was built once; every token request got the
              permissions and client roles; the request without a token got the
              empty defaults and built nothing.
        """
        requests = []
        for token in ("a.b.c", "d.e.f", "g.h.i", None):
            headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
            request = RequestFactory().get("/api/x", **headers)
            request.session = {}
            KeycloakPermissionsMiddleware(lambda r: HttpResponse("ok"))(request)
            requests.append(request)
        self.assertEqual(_FakeManager.instances, 1)
        for request in requests[:3]:
            self.assertEqual(request.user_permissions[0]["scopes"], ["read"])
            self.assertEqual(request.client_roles, ["analyst"])
        self.assertEqual((requests[3].user_permissions, requests[3].client_roles), ([], []))
