"""Who may run the bitemporal applier's SECURITY DEFINER functions.

Intent
------
``lex_apply_due_activations()`` and its two siblings run with their owner's
rights, past row-level security, and PostgreSQL lets every role execute a new
function. A project whose application role is restricted names the roles that
may run them in ``lex_config.BITEMPORAL_FUNCTION_ROLES`` (for example the role
of the pg_cron job, or none). After every ``migrate`` lex then revokes EXECUTE
from PUBLIC and grants it to those roles only. Unset, nothing changes, so a
deployment whose cron job relies on the default keeps working.

Cluster 5p — scenarios 5.130–5.131. Type: E (PostgreSQL only).
Covers: lex/core/services/activation_applier.py (restrict_function_privileges),
lex/core/apps.py (restrict_bitemporal_functions on post_migrate),
lex/core/extensions.py (BITEMPORAL_FUNCTION_ROLES).
Run: python -m lex pytest lex/test_project/tests/history/test_5p_applier_function_roles.py -v
"""

from __future__ import annotations

import pytest
from django.core.management.sql import emit_post_migrate_signal
from django.db import connection
from django.test import TestCase

from lex.core.extensions import override_extensions
from lex.core.services.activation_applier import FUNCTION_NAMES

pytestmark = pytest.mark.history

RESTRICTED = "cluster5p_app"
CRON = "cluster5p_cron"


class TestCluster05p_ApplierFunctionRoles(TestCase):
    """Cluster 5p: BITEMPORAL_FUNCTION_ROLES."""

    def setUp(self) -> None:
        if connection.vendor != "postgresql":
            self.skipTest("the applier's functions exist on PostgreSQL only")
        with connection.cursor() as cursor:  # rolled back with the test, roles included
            for role in (RESTRICTED, CRON):
                cursor.execute(f"CREATE ROLE {role} NOLOGIN")

    def _may_run(self, role):
        with connection.cursor() as cursor:
            return {
                name: cursor.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", [role, f"{name}()"])
                or cursor.fetchone()[0]
                for name in FUNCTION_NAMES
            }

    # -- 5.130 ---------------------------------------------------------
    def test_5_130_named_roles_only_after_migrate(self) -> None:
        """
        Scenario 5.130: a project names who may run the functions.
        Given: BITEMPORAL_FUNCTION_ROLES = [the cron role]
        When: migrate finishes (post_migrate)
        Then: the cron role may run all three functions, any other role none.
        """
        with override_extensions(BITEMPORAL_FUNCTION_ROLES=[CRON]):
            emit_post_migrate_signal(verbosity=0, interactive=False, db="default")
        self.assertEqual(set(self._may_run(CRON).values()), {True})
        self.assertEqual(set(self._may_run(RESTRICTED).values()), {False})

    # -- 5.131 ---------------------------------------------------------
    def test_5_131_unset_keeps_postgresql_default(self) -> None:
        """
        Scenario 5.131: without the key nothing changes.
        Given: no BITEMPORAL_FUNCTION_ROLES
        When: migrate finishes
        Then: every role may still run the functions (PostgreSQL's default), as before.
        """
        emit_post_migrate_signal(verbosity=0, interactive=False, db="default")
        self.assertEqual(set(self._may_run(RESTRICTED).values()), {True})
