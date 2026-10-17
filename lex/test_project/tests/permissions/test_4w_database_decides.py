"""The database decides: ``lex_permission_source = "database"``.

Intent
------
A project that keeps its access rules in PostgreSQL (row-level security, the
caller set through ``DB_CONTEXT``) must not have lex second-guess them with
Python hooks or Keycloak scopes it does not use. For a model in database mode
lex's default ``permission_*`` hooks let every call through (a model's own
overrides still apply), lists and records are what the database returns,
an update the policy does not let through answers 404 instead of turning
into an INSERT, and a policy refusal answers 404 too. The Python mode is
unchanged. Because the mode is only safe when the database really decides,
lex refuses to serve (503) when such a table has no row-level security or the
application runs as a superuser, the owner or a ``BYPASSRLS`` role. Keycloak's
permission call is skipped when every model lets the database decide.

Cluster 4w — scenarios 4.129–4.135. Type: U + E (PostgreSQL).
Covers: lex/core/permission_source.py (database_decides, problems, is_policy_refusal,
DatabaseDecidesGuardMiddleware), lex/core/models/LexModel.py (default hooks, _save_row),
lex/api/views/model_entries/filter_backends.py (restrict), lex/api/errors.py (policy refusals),
lex/api/middleware/keycloak_permissions.py (UMA call skipped), lex/core/extensions.py (PERMISSION_SOURCE).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4w_database_decides.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.test import RequestFactory, SimpleTestCase

from lex.core import extensions
from lex.core.permission_source import (
    DatabaseDecidesGuardMiddleware,
    database_decides,
    problems,
    reset_check,
)
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import DATABASE_RULE, DATABASE_RULE_MODELS, DatabaseRuleItem, ProtectedItem

pytestmark = pytest.mark.permissions

ROLE = "cluster4w_app"


def set_user(user, *, local=True, scope=None):
    """DB_CONTEXT of these tests: app.user_id for the policies."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.user_id', %s, %s)", [str(getattr(user, "pk", "") or ""), local])


def _database_mode():
    """DatabaseRuleItem lets the database decide for one test; the first-request check stays off."""
    patches = [
        mock.patch.object(DatabaseRuleItem, "lex_permission_source", "database", create=True),
        mock.patch.dict(os.environ, {"LEX_DATABASE_DECIDES_CHECK": "false"}),
    ]
    return patches


class TestCluster04w_SourceUnit(SimpleTestCase):
    """Cluster 4w (unit): which models the database decides for."""

    # -- 4.129 ---------------------------------------------------------
    def test_4_129_per_model_and_global_source(self) -> None:
        """
        Scenario 4.129: the per-model attribute wins over the global key.
        Given: models with and without lex_permission_source, PERMISSION_SOURCE python and database
        When: database_decides asks, and validation sees a typo
        Then: per model as set; the global key decides for the project's other models, not for
              lex's own (the audit log) or non-LexModels; an unknown source stops startup.
        """
        from django.contrib.auth.models import Group

        self.assertFalse(database_decides(DatabaseRuleItem))
        with mock.patch.object(DatabaseRuleItem, "lex_permission_source", "database", create=True):
            self.assertTrue(database_decides(DatabaseRuleItem))
            self.assertFalse(database_decides(ProtectedItem))
        from lex.audit_logging.models.AuditLog import AuditLog

        with extensions.override_extensions(PERMISSION_SOURCE="database"):
            self.assertTrue(database_decides(ProtectedItem))
            self.assertFalse(database_decides(Group))
            self.assertFalse(database_decides(AuditLog))  # lex's own models keep their rules
            with mock.patch.object(ProtectedItem, "lex_permission_source", "python", create=True):
                self.assertFalse(database_decides(ProtectedItem))
        with extensions.override_extensions(PERMISSION_SOURCE="sql"):
            with self.assertRaises(ImproperlyConfigured):
                extensions.validate_configuration()


class TestCluster04w_DatabaseDecides(E2ETestCase):
    """Cluster 4w (end to end, PostgreSQL): row-level security decides, lex does not."""

    e2e_models = DATABASE_RULE_MODELS

    def setUp(self) -> None:
        if connection.vendor != "postgresql":
            self.skipTest("row-level security is PostgreSQL only")
        super().setUp()
        for patch in _database_mode():
            patch.start()
            self.addCleanup(patch.stop)
        self.use_lex_extensions(DB_CONTEXT=f"{__name__}.set_user")
        table = DatabaseRuleItem._meta.db_table
        history = DatabaseRuleItem.history.model._meta.db_table
        owner = "owner_id = nullif(current_setting('app.user_id', true), '')::int"
        with connection.cursor() as cursor:
            cursor.execute(f"DROP ROLE IF EXISTS {ROLE}")
            cursor.execute(f"CREATE ROLE {ROLE} NOLOGIN")
            cursor.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {ROLE}")
            cursor.execute(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {ROLE}")
            cursor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            cursor.execute(f"CREATE POLICY read ON {table} FOR SELECT USING ({owner})")
            cursor.execute(f"CREATE POLICY add ON {table} FOR INSERT WITH CHECK ({owner})")
            cursor.execute(f"CREATE POLICY change ON {table} FOR UPDATE USING ({owner} AND NOT locked)")
            cursor.execute(f"CREATE POLICY remove ON {table} FOR DELETE USING ({owner})")
        self.addCleanup(self._drop_role, table)
        self.other = DatabaseRuleItem.objects.create(owner_id=self.user.pk + 1000, name="theirs")
        self.mine = DatabaseRuleItem.objects.create(owner_id=self.user.pk, name="mine")
        self.locked = DatabaseRuleItem.objects.create(owner_id=self.user.pk, name="locked", locked=True)
        with connection.cursor() as cursor:
            cursor.execute(f"SET ROLE {ROLE}")
        self.history_table = history

    def _drop_role(self, table):
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
            for policy in ("read", "add", "change", "remove"):
                cursor.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
            cursor.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            cursor.execute(f"DROP OWNED BY {ROLE}")
            cursor.execute(f"DROP ROLE IF EXISTS {ROLE}")

    def _names(self, response):
        body = response.json()
        rows = body["results"] if isinstance(body, dict) else body
        return sorted(row["name"] for row in rows)

    # -- 4.130 ---------------------------------------------------------
    def test_4_130_reads_are_what_the_database_returns(self) -> None:
        """
        Scenario 4.130: no Keycloak scope, no per-row Python check, only the policy.
        Given: three rows, one of another owner; the user holds no Keycloak scopes
        When: the user lists and reads them
        Then: the list holds the user's two rows without running lex's per-row loop;
              the other owner's row answers 404.
        """
        from lex.api.views.model_entries.filter_backends import UserReadRestrictionFilterBackend

        with mock.patch.object(
            UserReadRestrictionFilterBackend, "_handle_lexmodel_default", side_effect=AssertionError("per-row loop")
        ):
            listed = self.client.get(self.url_list(DATABASE_RULE))
        self.assertEqual(listed.status_code, 200, listed.content[:300])
        self.assertEqual(self._names(listed), ["locked", "mine"])
        self.assertEqual(self.client.get(self.url_detail(DATABASE_RULE, self.mine.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url_detail(DATABASE_RULE, self.other.pk)).status_code, 404)

    # -- 4.131 ---------------------------------------------------------
    def test_4_131_writes_the_policy_refuses_answer_404(self) -> None:
        """
        Scenario 4.131: the database decides writes too, and a refusal is "not found".
        Given: the same rows
        When: the user creates an own row, creates one for another owner, changes its own row,
              changes the locked row it can see but not update, and deletes its own row
        Then: create, change and delete succeed; the foreign create and the locked change answer
              404; the locked row is unchanged and no copy of it was inserted.
        """
        created = self.client.post(self.url_create(DATABASE_RULE), {"name": "new", "owner_id": self.user.pk}, format="json")
        self.assertEqual(created.status_code, 201, created.content[:300])
        foreign = self.client.post(
            self.url_create(DATABASE_RULE), {"name": "smuggled", "owner_id": self.user.pk + 1000}, format="json"
        )
        self.assertEqual(foreign.status_code, 404, foreign.content[:300])
        self.assertEqual(self.client.patch(self.url_detail(DATABASE_RULE, self.mine.pk), {"name": "renamed"},
                                           format="json").status_code, 200)
        locked = self.client.patch(self.url_detail(DATABASE_RULE, self.locked.pk), {"name": "forced"}, format="json")
        self.assertEqual(locked.status_code, 404, locked.content[:300])
        self.assertEqual(locked.json()["code"], "NOT_FOUND")
        self.assertEqual(self.client.delete(self.url_detail(DATABASE_RULE, self.mine.pk)).status_code, 200)
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        self.assertEqual(
            sorted(DatabaseRuleItem.objects.values_list("name", flat=True)), ["locked", "new", "theirs"]
        )
        with connection.cursor() as cursor:
            cursor.execute(f"SET ROLE {ROLE}")

    # -- 4.132 ---------------------------------------------------------
    def test_4_132_a_models_own_hook_still_applies(self) -> None:
        """
        Scenario 4.132: database mode replaces lex's defaults, not the project's rules.
        Given: DatabaseRuleItem with its own permission_create returning False
        When: the user creates a row it would be allowed to insert
        Then: refused, nothing inserted.
        """
        with mock.patch.object(DatabaseRuleItem, "permission_create", lambda self, uc: False, create=True):
            refused = self.client.post(self.url_create(DATABASE_RULE), {"name": "blocked", "owner_id": self.user.pk},
                                       format="json")
        self.assertIn(refused.status_code, (401, 403), refused.content[:300])
        self.assertFalse(DatabaseRuleItem.objects.filter(name="blocked").exists())


class TestCluster04w_Guard(E2ETestCase):
    """Cluster 4w (end to end, PostgreSQL): no database mode without row-level security."""

    e2e_models = DATABASE_RULE_MODELS

    def setUp(self) -> None:
        if connection.vendor != "postgresql":
            self.skipTest("row-level security is PostgreSQL only")
        super().setUp()
        patch = mock.patch.object(DatabaseRuleItem, "lex_permission_source", "database", create=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(reset_check)
        self.table = DatabaseRuleItem._meta.db_table

    # -- 4.133 ---------------------------------------------------------
    def test_4_133_problems_name_what_lets_rows_through(self) -> None:
        """
        Scenario 4.133: the check names every reason the database cannot decide.
        Given: the table without row-level security, the suite's superuser connection
        When: problems() runs as the superuser, then as a NOLOGIN role with RLS enabled
        Then: the superuser role and the missing RLS are named; with RLS on and the restricted
              role, nothing is.
        """
        found = problems()
        self.assertTrue(any("superuser" in problem for problem in found), found)
        self.assertTrue(any(self.table in problem and "no row-level security" in problem for problem in found), found)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"ALTER TABLE {self.table} ENABLE ROW LEVEL SECURITY")
            history = DatabaseRuleItem.history.model._meta.db_table
            cursor.execute(f"ALTER TABLE {history} ENABLE ROW LEVEL SECURITY")
            meta = getattr(DatabaseRuleItem.history.model, "meta_history", None)
            if meta is not None:
                cursor.execute(f"ALTER TABLE {meta.model._meta.db_table} ENABLE ROW LEVEL SECURITY")
            cursor.execute(f"CREATE ROLE {ROLE}_guard NOLOGIN")
            cursor.execute(f"SET LOCAL ROLE {ROLE}_guard")
            self.assertEqual(problems(), [])
            transaction.set_rollback(True)

    # -- 4.134 ---------------------------------------------------------
    def test_4_134_the_guard_refuses_service(self) -> None:
        """
        Scenario 4.134: a request is refused while the database cannot decide.
        Given: the same unsafe setup, a fresh check
        When: a request passes the guard middleware, and again with the check switched off
        Then: 503 SERVICE_MISCONFIGURED; with LEX_DATABASE_DECIDES_CHECK=false it passes.
        """
        reset_check()
        guard = DatabaseDecidesGuardMiddleware(lambda request: "served")
        request = RequestFactory().get("/api/model_entries/x/list")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEX_DATABASE_DECIDES_CHECK", None)
            refused = guard(request)
        self.assertEqual(refused.status_code, 503)
        self.assertIn(b"SERVICE_MISCONFIGURED", refused.content)
        with mock.patch.dict(os.environ, {"LEX_DATABASE_DECIDES_CHECK": "false"}):
            self.assertEqual(guard(request), "served")


class TestCluster04w_KeycloakSkipped(SimpleTestCase):
    """Cluster 4w (unit): no UMA call when the database decides everything."""

    # -- 4.135 ---------------------------------------------------------
    def test_4_135_uma_call_skipped(self) -> None:
        """
        Scenario 4.135: Keycloak's permission call is not made when its answer decides nothing.
        Given: a request with an access token and a mocked Keycloak manager
        When: it passes the permissions middleware with PERMISSION_SOURCE "database", then "python"
        Then: in database mode no UMA call, the userinfo call still made; in Python mode both.
        """
        from lex.api.middleware import keycloak_permissions

        manager = mock.MagicMock()
        manager.get_uma_permissions.return_value = []
        manager.oidc.userinfo.return_value = {"sub": "x"}
        middleware = keycloak_permissions.KeycloakPermissionsMiddleware(lambda request: "ok")

        def run():
            request = RequestFactory().get("/")
            request.session = {"oidc_access_token": "token-4-135"}
            with mock.patch.object(keycloak_permissions, "_shared_manager", return_value=manager), \
                    mock.patch.object(keycloak_permissions, "_cache_get", return_value=None), \
                    mock.patch.object(keycloak_permissions, "_cache_set"):
                middleware(request)

        with extensions.override_extensions(PERMISSION_SOURCE="database"):
            run()
        self.assertEqual(manager.get_uma_permissions.call_count, 0)
        self.assertEqual(manager.oidc.userinfo.call_count, 1)
        run()
        self.assertEqual(manager.get_uma_permissions.call_count, 1)
