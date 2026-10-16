"""Database context in background calculations (DB_CONTEXT).

Intent
------
A Calculate click runs the calculation on lex's thread pool, on a connection
of its own. With ``lex_config.DB_CONTEXT`` set, that thread tells the
database who clicked (``local=False``) before the calculation runs and
resets it afterwards, so row-level security sees the same user as the
request did.

Cluster 7v — scenarios 7.238–7.239. Type: E.
Covers: lex/api/views/model_entries/One.py (_calculate_in_background), lex/core/db_context.py.
Run: python -m lex pytest lex/test_project/tests/calculations/test_7v_db_context_thread.py -v
"""

from __future__ import annotations

from unittest import mock

import pytest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, ATOMIC, AtomicCalc

pytestmark = pytest.mark.calculations

CALLS: list = []


def record(user, *, local):
    CALLS.append((getattr(user, "username", None), local))


HOOK = f"{__name__}.record"


def _run_inline(fn, *args, **kwargs):
    fn(*args, **kwargs)


class TestCluster07v_DbContextThread(E2ETestCase):
    """Cluster 7v: the calculation thread applies DB_CONTEXT."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        CALLS.clear()

    def _calculate(self, calc):
        # Inline instead of on the pool; the pool's connection cleanup must not
        # close the test's own connection mid-request.
        with mock.patch("lex.api.views.model_entries.One._calculation_executor.submit", side_effect=_run_inline), \
                mock.patch("django.db.close_old_connections"):
            return self.client.patch(
                self.url_detail(ATOMIC, calc.pk), {"calculate": "true", "is_calculated": "true"}, format="json"
            )

    # -- 7.238 ---------------------------------------------------------
    def test_7_238_background_calculation_runs_as_the_clicking_user(self) -> None:
        """
        Scenario 7.238: the thread applies the user's context and resets it.
        Given: DB_CONTEXT set and an AtomicCalc
        When: the user clicks Calculate (the thread runs inline for the test)
        Then: the hook saw (e2e_user, local=False) and then (None, local=False)
              besides the request's own local=True calls.
        """
        self.use_lex_extensions(DB_CONTEXT=HOOK)
        calc = AtomicCalc.objects.create(name="calc")
        response = self._calculate(calc)
        self.assertIn(response.status_code, (200, 202), response.content[:300])
        background = [call for call in CALLS if call[1] is False]
        self.assertEqual(background, [("e2e_user", False), (None, False)], CALLS)

    # -- 7.239 ---------------------------------------------------------
    def test_7_239_without_hook_nothing_is_called(self) -> None:
        """
        Scenario 7.239: without DB_CONTEXT the calculation runs as before.
        Given: no DB_CONTEXT
        When: Calculate is clicked
        Then: the hook is never called.
        """
        calc = AtomicCalc.objects.create(name="calc")
        self._calculate(calc)
        self.assertEqual(CALLS, [])
