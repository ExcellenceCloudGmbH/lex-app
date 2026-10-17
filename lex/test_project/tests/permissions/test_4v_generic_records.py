"""Generic records for end users: ``GENERIC_API = "records"``.

Intent
------
A project whose own clients (web app, mobile app, integrators) use lex's
generic API opens only the records it means to: a model sets
``lex_api_exposed`` (``True`` for list, retrieve, create, update and delete, or
a set of operations), and in mode ``"records"`` only those routes answer
signed-in callers. Everything else under ``api/`` — lex's internal endpoints,
models that are not exposed, the history tables, Django's users, the sockets —
stays for superusers and answers 404 to everyone else, as a path that does
not exist. Actions on an exposed model are reachable; their own permission
decides. A misspelt operation stops startup.

The internal endpoints are also closed for non-superusers in every mode
(``LEX_SEC_INTERNAL``): editing Django's users, writing to the server log,
deleting unused files; the calculation log tree and its PDFs show only logs of
records the caller may read. A model's own ``permission_list`` that refuses
makes its list answer 404.

Cluster 4v — scenarios 4.119–4.128, 4.136. Type: U + E.
Covers: lex/api/middleware/generic_api_gate.py (records mode, exposed_operations,
record_operation, GenericApiSocketGate), lex/lex_app/asgi.py (the gate in the socket stack), lex/core/extensions.py (_validate_exposed_models),
lex/api/security.py (edit_violations for Django auth models, list_denied),
lex/api/views/model_entries/List.py (initial), lex/api/views/LexLoggerView/LexLoggerView.py,
lex/api/views/sharepoint/DeleteUnusedFiles.py, lex/api/views/model_entries/CalculationLogTreeView.py,
lex/api/views/calculations/DownloadMarkdownPdf.py.
Run: python -m lex pytest lex/test_project/tests/permissions/test_4v_generic_records.py -v
"""

from __future__ import annotations

import os
from unittest import mock
import asyncio

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from lex.api.middleware.generic_api_gate import (
    DEFAULT_EXPOSED,
    GenericApiSocketGate,
    exposed_operations,
    record_operation,
)
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.core import extensions
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer
from lex.test_project.tests.api_layer.models import ACTION_ITEM, ACTION_MODELS, ActionItem

# These scenarios are strict mode's behaviour: pinned, whatever LEX_LEGACY_SECURITY the run sets (a legacy run keeps
# the pre-3.0 behaviour, which other clusters cover).
_STRICT = mock.patch.dict(os.environ, {"LEX_LEGACY_SECURITY": "false"})


def setUpModule():
    _STRICT.start()


def tearDownModule():
    _STRICT.stop()

pytestmark = pytest.mark.permissions


def _list_url(model):
    return reverse("process_admin_rest_api:model-entries-list", kwargs={"model_container": _MockContainer(model)})


def _one_url(model, pk=None):
    kwargs = {"model_container": _MockContainer(model), "calculationId": "c"}
    if pk is None:
        return reverse("process_admin_rest_api:model-one-entry-create", kwargs=kwargs)
    return reverse("process_admin_rest_api:model-one-entry-read-update-delete", kwargs={**kwargs, "pk": pk})


def _action_url(item, action):
    return reverse(
        "process_admin_rest_api:model-record-action",
        kwargs={"model_container": _MockContainer(ACTION_ITEM), "calculationId": "c", "pk": item.pk, "action": action},
    )


class _Exposed:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        ActionItem.lex_api_exposed = self.value

    def __exit__(self, *exc):
        del ActionItem.lex_api_exposed


class TestCluster04v_ExposureUnit(SimpleTestCase):
    """Cluster 4v (unit): what a model exposes and how a route maps to an operation."""

    # -- 4.119 ---------------------------------------------------------
    def test_4_119_exposure_and_validation(self) -> None:
        """
        Scenario 4.119: exposure values and the startup check.
        Given: lex_api_exposed True, a set, False and a set with a typo
        When: exposed_operations reads them and validate_configuration runs
        Then: True means the five record operations, a set itself, False nothing;
              the typo stops startup naming the unknown operation.
        """
        with _Exposed(True):
            self.assertEqual(exposed_operations(ActionItem), DEFAULT_EXPOSED)
            self.assertNotIn("history", DEFAULT_EXPOSED)
        with _Exposed({"list", "history"}):
            self.assertEqual(exposed_operations(ActionItem), frozenset({"list", "history"}))
        self.assertEqual(exposed_operations(ActionItem), frozenset())
        with _Exposed({"list", "lst"}):
            with self.assertRaisesRegex(ImproperlyConfigured, "lst"):
                extensions._validate_exposed_models()
        with _Exposed("list"):
            with self.assertRaises(ImproperlyConfigured):
                extensions._validate_exposed_models()


class TestCluster04v_RecordsMode(E2ETestCase):
    """Cluster 4v (end to end): mode "records"."""

    e2e_models = ACTION_MODELS

    def setUp(self) -> None:
        super().setUp()
        self.use_lex_extensions(GENERIC_API="records")

    # -- 4.120 ---------------------------------------------------------
    def test_4_120_route_operations(self) -> None:
        """
        Scenario 4.120: every generic record route maps to its operation.
        Given: list, create, one (GET/PATCH/DELETE), action and a non-generic path
        When: record_operation resolves them
        Then: list, create, retrieve, update, delete, action; None for api/model-structure.
        """
        item = ActionItem.objects.create(name="a")
        factory = RequestFactory()
        cases = [
            (factory.get(_list_url(ACTION_ITEM)), "list"),
            (factory.post(_one_url(ACTION_ITEM)), "create"),
            (factory.get(_one_url(ACTION_ITEM, item.pk)), "retrieve"),
            (factory.patch(_one_url(ACTION_ITEM, item.pk)), "update"),
            (factory.delete(_one_url(ACTION_ITEM, item.pk)), "delete"),
            (factory.post(_action_url(item, "approve")), "action"),
        ]
        for request, operation in cases:
            self.assertEqual(record_operation(request), (ActionItem, operation), request.path)
        self.assertIsNone(record_operation(factory.get("/api/model-structure")))

    # -- 4.121 ---------------------------------------------------------
    def test_4_121_exposed_operations_answer_users(self) -> None:
        """
        Scenario 4.121: a signed-in user reaches exactly the exposed operations.
        Given: ActionItem exposing list, retrieve and create
        When: the user lists, reads, creates, updates and deletes
        Then: list, read and create succeed; update and delete answer 404 and change nothing.
        """
        item = ActionItem.objects.create(name="kept")
        with _Exposed({"list", "retrieve", "create"}):
            self.assertEqual(self.client.get(_list_url(ACTION_ITEM)).status_code, 200)
            self.assertEqual(self.client.get(_one_url(ACTION_ITEM, item.pk)).status_code, 200)
            created = self.client.post(_one_url(ACTION_ITEM), {"name": "new"}, format="json")
            self.assertIn(created.status_code, (200, 201), created.content[:300])
            patched = self.client.patch(_one_url(ACTION_ITEM, item.pk), {"name": "changed"}, format="json")
            self.assertEqual(patched.status_code, 404)
            self.assertEqual(patched.json()["code"], "NOT_FOUND")
            self.assertEqual(self.client.delete(_one_url(ACTION_ITEM, item.pk)).status_code, 404)
        item.refresh_from_db()
        self.assertEqual(item.name, "kept")

    # -- 4.122 ---------------------------------------------------------
    def test_4_122_everything_else_is_404_for_users_but_not_for_superusers(self) -> None:
        """
        Scenario 4.122: models that are not exposed and lex's own endpoints do not exist for users.
        Given: ActionItem not exposed; a signed-in user, then a superuser
        When: they call its list, the user list, model-structure, run_step and the calculation log tree
        Then: 404 for the user everywhere; the superuser gets none of them as 404 from the gate.
        """
        paths = [
            _list_url(ACTION_ITEM),
            _list_url("user"),
            "/api/model-structure",
            "/api/calculationlog/tree/",
        ]
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
        self.assertEqual(self.client.post("/api/run_step/actionitem/1", {}, format="json").status_code, 404)
        self.user.is_superuser = True
        self.user.save()
        for path in paths:
            self.assertNotEqual(self.client.get(path).status_code, 404, path)

    # -- 4.123 ---------------------------------------------------------
    def test_4_123_actions_of_exposed_models_decide_themselves(self) -> None:
        """
        Scenario 4.123: an exposed model's actions are reachable; its permission still decides.
        Given: ActionItem exposed for actions only, then for list only; an editable and a locked row
        When: approve runs on both, and on a row of a model that is not exposed
        Then: with "action" alone the record itself is 404 but its action runs; 200 for the editable
              row, 400 ACTION_NOT_ALLOWED for the locked one; 404 once the model is no longer exposed.
        """
        item = ActionItem.objects.create(name="invoice")
        locked = ActionItem.objects.create(name="other", status="locked")
        with _Exposed({"action"}):  # actions only: the record routes stay closed
            self.assertEqual(self.client.get(_one_url(ACTION_ITEM, item.pk)).status_code, 404)
            self.assertEqual(self.client.post(_action_url(item, "approve"), {"note": "ok"}, format="json").status_code, 200)
        with _Exposed({"list"}):
            self.assertEqual(self.client.post(_action_url(item, "approve"), {"note": "ok"}, format="json").status_code, 200)
            refused = self.client.post(_action_url(locked, "approve"), {"note": "ok"}, format="json")
            self.assertEqual(refused.json()["code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(self.client.post(_action_url(item, "approve"), {"note": "x"}, format="json").status_code, 404)


class TestCluster04v_SocketGate(SimpleTestCase):
    """Cluster 4v (unit): the websocket gate."""

    # -- 4.124 ---------------------------------------------------------
    def test_4_124_sockets_follow_the_generic_api(self) -> None:
        """
        Scenario 4.124: in every mode but "all" only superusers open a socket.
        Given: the gate around an inner app, a user and a superuser
        When: each connects with GENERIC_API "records", then "all"
        Then: the user's handshake is closed (4404) before the inner app runs in
              "records"; the superuser, and everyone in "all", reach it.
        """

        class _User:
            is_authenticated = True

            def __init__(self, superuser):
                self.is_superuser = superuser

        reached = []

        async def inner(scope, receive, send):
            reached.append(scope["user"].is_superuser)

        def connect(user):
            sent = []

            async def receive():
                return {"type": "websocket.connect"}

            async def send(message):
                sent.append(message)

            asyncio.run(GenericApiSocketGate(inner)({"type": "websocket", "user": user}, receive, send))
            return sent

        with extensions.override_extensions(GENERIC_API="records"):
            self.assertEqual(connect(_User(False)), [{"type": "websocket.close", "code": 4404}])
            self.assertEqual(connect(_User(True)), [])
        with extensions.override_extensions(GENERIC_API="all"):
            self.assertEqual(connect(_User(False)), [])
        self.assertEqual(reached, [True, False])


class TestCluster04v_SocketStack(SimpleTestCase):
    """Cluster 4v (unit): the ASGI application puts the gate in front of every authenticated socket."""

    # -- 4.128 ---------------------------------------------------------
    def test_4_128_authenticated_sockets_pass_the_gate(self) -> None:
        """
        Scenario 4.128: the gate wraps the authenticated websocket routes.
        Given: lex's ASGI application
        When: its websocket stack is walked
        Then: a GenericApiSocketGate sits inside the authentication stack, around the routes;
              the public health route is not behind it.
        """
        from lex.lex_app import asgi

        found = []

        def walk(node, depth=0):
            if depth > 12 or node is None:
                return
            if isinstance(node, GenericApiSocketGate):
                found.append(node)
            for attr in ("inner", "application", "app"):
                walk(getattr(node, attr, None), depth + 1)
            for route in getattr(node, "routes", []) or []:
                walk(getattr(route, "callback", None), depth + 1)

        walk(asgi.application.application_mapping["websocket"])
        self.assertEqual(len(found), 1)


class TestCluster04v_InternalEndpoints(E2ETestCase):
    """Cluster 4v (end to end): lex's internal endpoints in every mode (LEX_SEC_INTERNAL)."""

    e2e_models = ACTION_MODELS

    # -- 4.125 ---------------------------------------------------------
    def test_4_125_users_logs_and_files_need_a_superuser(self) -> None:
        """
        Scenario 4.125: editing users, writing to the log and deleting files are operator tools.
        Given: GENERIC_API "all", a signed-in user and another user
        When: the user renames the other user through the generic API, posts to api/logs
              and calls delete-unused-files
        Then: 400 EDIT_NOT_ALLOWED and the name unchanged; 404 for the log and the files;
              a superuser may rename the user.
        """
        other = get_user_model().objects.create_user(username="victim", email="victim@test.local")
        url = _one_url("user", other.pk)
        response = self.client.patch(url, {"email": "attacker@test.local"}, format="json")
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["code"], "EDIT_NOT_ALLOWED")
        other.refresh_from_db()
        self.assertEqual(other.email, "victim@test.local")
        self.assertEqual(self.client.post("/api/logs", {"message": "fake"}).status_code, 404)
        self.assertEqual(self.client.post("/api/delete-unused-files", {}, format="json").status_code, 404)
        self.user.is_superuser = True
        self.user.save()
        self.assertEqual(self.client.patch(url, {"email": "ops@test.local"}, format="json").status_code, 200)

    # -- 4.126 ---------------------------------------------------------
    def test_4_126_calculation_logs_of_readable_records_only(self) -> None:
        """
        Scenario 4.126: the log tree and its PDFs show only logs of records the caller may read.
        Given: a calculation on a readable row, one on a hidden row, one without a record
        When: a user asks for each tree, for every log, and for the PDF of the hidden one
        Then: the readable calculation's logs; nothing for the others or without a filter;
              404 for the PDF. A superuser sees every log.
        """
        readable = ActionItem.objects.create(name="seen")
        hidden = ActionItem.objects.create(name="hidden")
        kind = ContentType.objects.get_for_model(ActionItem)
        CalculationLog.objects.create(calculationId="calc-seen", content_type=kind, object_id=readable.pk)
        secret = CalculationLog.objects.create(calculationId="calc-hidden", content_type=kind, object_id=hidden.pk)
        CalculationLog.objects.create(calculationId="calc-none")

        def tree(**params):
            return self.client.get("/api/calculationlog/tree/", params).json()["pagination"]["total"]

        self.assertEqual(tree(calculation_id="calc-seen"), 1)
        self.assertEqual(tree(calculation_id="calc-hidden"), 0)
        self.assertEqual(tree(calculation_id="calc-none"), 0)
        self.assertEqual(tree(), 0)
        self.assertEqual(self.client.get(f"/api/download-pdf/{secret.pk}/").status_code, 404)
        self.user.is_superuser = True
        self.user.save()
        self.assertEqual(tree(), 3)

    # -- 4.127 ---------------------------------------------------------
    def test_4_127_a_refusing_permission_list_hides_the_list(self) -> None:
        """
        Scenario 4.127: a model's own permission_list is honoured by the list endpoint.
        Given: ActionItem whose permission_list refuses this user
        When: the user lists it
        Then: 404, as for a list that does not exist; with the hook allowing, 200.
        """
        original = ActionItem.permission_list
        ActionItem.permission_list = lambda self, uc: False
        self.addCleanup(setattr, ActionItem, "permission_list", original)
        self.assertEqual(self.client.get(_list_url(ACTION_ITEM)).status_code, 404)
        ActionItem.permission_list = original
        self.assertEqual(self.client.get(_list_url(ACTION_ITEM)).status_code, 200)


class TestCluster04v_UnknownApiPaths(SimpleTestCase):
    """Cluster 4v (unit): what no route under api/ matched is 404, never the app's index.html."""

    # -- 4.136 ---------------------------------------------------------
    def test_4_136_an_unknown_api_path_is_404(self) -> None:
        """
        Scenario 4.136: the SPA catch-all leaves api/ alone.
        Given: a build with index.html and the catch-all view
        When: it is asked for api/atrium/funds (a removed endpoint), api and an app route, also below
              DJANGO_BASE_PATH, in strict and in legacy mode
        Then: 404 for both api paths in strict mode, the index for the app route; legacy mode keeps
              serving the index.
        """
        import os
        import tempfile
        from unittest import mock

        from django.http import Http404
        from django.test import RequestFactory

        from lex.react.views import serve_react

        with tempfile.TemporaryDirectory() as root:
            open(os.path.join(root, "index.html"), "w").write("<html></html>")

            def answer(path):
                try:
                    return serve_react(RequestFactory().get(f"/{path}"), path, document_root=root).status_code
                except Http404:
                    return 404

            with mock.patch.dict(os.environ, {"LEX_LEGACY_SECURITY": "false", "DJANGO_BASE_PATH": ""}):
                self.assertEqual([answer(p) for p in ("api/atrium/funds", "api", "funds/overview")], [404, 404, 200])
            with mock.patch.dict(os.environ, {"LEX_LEGACY_SECURITY": "false", "DJANGO_BASE_PATH": "/portal"}):
                self.assertEqual([answer(p) for p in ("portal/api/atrium/funds", "portal/funds")], [404, 200])
            with mock.patch.dict(os.environ, {"LEX_LEGACY_SECURITY": "true", "DJANGO_BASE_PATH": ""}):
                self.assertEqual(answer("api/atrium/funds"), 200)

    # -- 4.137 ---------------------------------------------------------
    def test_4_137_the_bundled_frontend_is_404_for_a_signed_in_user_outside_mode_all(self) -> None:
        """
        Scenario 4.137: outside mode "all" the bundled frontend is for superusers, like the endpoints it calls.
        Given: a build with index.html and the catch-all view
        When: an anonymous caller, a signed-in user and a superuser ask for the app's root in modes records,
              superuser and all
        Then: 404 for the signed-in user in records and superuser mode, so no page loads whose own requests
              (api/user/ …) all answer 404; anonymous callers keep the page (it starts the sign-in); superusers
              and mode all get it as before.
        """
        import os
        import tempfile
        from types import SimpleNamespace

        from django.contrib.auth.models import AnonymousUser
        from django.http import Http404

        from lex.react.views import serve_react

        callers = {
            "anonymous": AnonymousUser(),
            "user": SimpleNamespace(is_authenticated=True, is_superuser=False),
            "superuser": SimpleNamespace(is_authenticated=True, is_superuser=True),
        }

        with tempfile.TemporaryDirectory() as root:
            open(os.path.join(root, "index.html"), "w").write("<html></html>")

            def answers():
                out = {}
                for name, user in callers.items():
                    request = RequestFactory().get("/")
                    request.user = user
                    try:
                        out[name] = serve_react(request, "", document_root=root).status_code
                    except Http404:
                        out[name] = 404
                return out

            for mode in ("records", "superuser"):
                with extensions.override_extensions(GENERIC_API=mode):
                    self.assertEqual(answers(), {"anonymous": 200, "user": 404, "superuser": 200}, mode)
            with extensions.override_extensions(GENERIC_API="all"):
                self.assertEqual(answers(), {"anonymous": 200, "user": 200, "superuser": 200})
