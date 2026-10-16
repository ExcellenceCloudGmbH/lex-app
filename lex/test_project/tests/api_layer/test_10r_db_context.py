"""Database context per request and per after-commit callback (DB_CONTEXT).

Intent
------
A project whose database decides visibility itself (row-level security)
needs every transaction to know who is asking. With ``lex_config.DB_CONTEXT``
set, lex wraps each request in one transaction and calls the hook with the
signed-in user and ``local=True`` first, so a ``SET LOCAL`` holds for the
whole request and ends with it; a project endpoint re-applies it after DRF
authentication. Callbacks lex registers with ``on_commit`` get a fresh
transaction with the same user. Without ``DB_CONTEXT`` none of this happens:
no extra transaction, no hook.

Cluster 10r — scenarios 10.102–10.105. Type: I + E (PostgreSQL GUC).
Covers: lex/core/db_context.py (LexDbContextMiddleware, apply, on_commit),
lex/api/project/views.py (LexAPIView.initial), MIDDLEWARE in lex/lex_app/settings.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10r_db_context.py -v
"""

from __future__ import annotations

import pytest
from django.db import connection, transaction
from django.test import RequestFactory
from rest_framework.test import APIClient

from lex.api.utils.Context import OperationContext
from lex.core import db_context
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import PROJECT_MODELS

pytestmark = pytest.mark.api_layer

PREFIX = "/api/cluster10/"
PROJECT_API = {"API_URLCONF": "lex.test_project.tests.api_layer._project_api", "API_PREFIX": "api/cluster10/"}

#: (username or None, local, inside a transaction) per hook call.
CALLS: list = []


def record_and_set(user, *, local):
    CALLS.append((getattr(user, "username", None), local, connection.in_atomic_block))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('lex.cluster10_user', %s, %s)", [getattr(user, "username", "") or "", local]
        )


HOOK = f"{__name__}.record_and_set"


class TestCluster10r_DbContext(E2ETestCase):
    """Cluster 10r: DB_CONTEXT."""

    e2e_models = PROJECT_MODELS

    def setUp(self) -> None:
        super().setUp()
        CALLS.clear()

    # -- 10.102 --------------------------------------------------------
    def test_10_102_request_runs_in_a_transaction_with_the_user(self) -> None:
        """
        Scenario 10.102: each request tells the database who is asking.
        Given: DB_CONTEXT set and the project API mounted
        When: a signed-in user calls an endpoint that reads the database setting
        Then: the endpoint sees the user's name; the hook ran inside a
              transaction with local=True.
        """
        self.use_lex_extensions(**PROJECT_API, DB_CONTEXT=HOOK)
        response = self.client.get(PREFIX + "db-context")
        self.assertEqual(response.json(), {"db_user": "e2e_user"})
        self.assertTrue(CALLS)
        self.assertTrue(all(local and in_transaction for _, local, in_transaction in CALLS), CALLS)

    # -- 10.103 --------------------------------------------------------
    def test_10_103_setting_ends_with_the_request(self) -> None:
        """
        Scenario 10.103: SET LOCAL does not leak into the next request.
        Given: a signed-in request followed by an anonymous one on the same connection
        When: both call the (public) probe
        Then: the second does not see the first user's name.
        """
        self.use_lex_extensions(**PROJECT_API, DB_CONTEXT=HOOK)
        self.assertEqual(self.client.get(PREFIX + "db-context").json()["db_user"], "e2e_user")
        self.assertEqual(APIClient().get(PREFIX + "db-context").json()["db_user"], "")

    # -- 10.104 --------------------------------------------------------
    def test_10_104_without_hook_nothing_changes(self) -> None:
        """
        Scenario 10.104: without DB_CONTEXT there is no hook and no extra transaction.
        Given: no DB_CONTEXT
        When: the middleware handles a request
        Then: the hook is never called and the view runs outside a transaction.
        """
        seen = {}

        def view(request):
            seen["atomic"] = connection.in_atomic_block
            return "ok"

        self.assertEqual(db_context.LexDbContextMiddleware(view)(RequestFactory().get("/x")), "ok")
        self.assertFalse(seen["atomic"])
        self.assertEqual(CALLS, [])

    # -- 10.105 --------------------------------------------------------
    def test_10_105_on_commit_callbacks_get_the_users_context(self) -> None:
        """
        Scenario 10.105: lex's on_commit callbacks run with the caller's context.
        Given: DB_CONTEXT set and an operation started by the signed-in user
        When: a callback is registered with db_context.on_commit and the
              transaction commits
        Then: the callback runs in a fresh transaction where the database
              setting names the user.
        """
        self.use_lex_extensions(DB_CONTEXT=HOOK)
        request = RequestFactory().get("/x")
        request.user = self.user
        seen = {}

        def callback():
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('lex.cluster10_user', true)")
                seen["db_user"] = cursor.fetchone()[0]
            seen["atomic"] = connection.in_atomic_block

        with OperationContext(request, "cluster10r"):
            with transaction.atomic():
                db_context.on_commit(callback)
        self.assertEqual(seen, {"db_user": "e2e_user", "atomic": True})
