"""Database context in Celery tasks (DB_CONTEXT).

Intent
------
A Celery task runs on a worker's own database connection, far from the
request that started it. With ``lex_config.DB_CONTEXT`` set, the task's
context (``CeleryCalculationContext``) tells the database who started the
work — the user carried in the propagated operation context — with
``local=False`` for the whole task, and resets it to ``None`` when the task
ends, so the next task on the same connection starts clean. In synchronous
mode (no propagated context) the request's own context stays untouched.

Cluster 8d — scenarios 8.154–8.156. Type: U.
Covers: lex/lex_app/celery_tasks.py (CeleryCalculationContext), lex/core/db_context.py (for_background).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8d_db_context.py -v
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.test import SimpleTestCase

from lex.core import db_context
from lex.core.extensions import override_extensions
from lex.lex_app.celery_tasks import CeleryCalculationContext

pytestmark = pytest.mark.celery_async

CALLS: list = []


def record(user, *, local):
    CALLS.append((getattr(user, "username", None), local))


HOOK = f"{__name__}.record"
ALICE = SimpleNamespace(username="alice", is_authenticated=True)


class TestCluster08d_DbContextInTasks(SimpleTestCase):
    """Cluster 8d: CeleryCalculationContext applies and resets DB_CONTEXT."""

    def setUp(self) -> None:
        CALLS.clear()

    # -- 8.154 ---------------------------------------------------------
    def test_8_154_task_runs_as_the_user_who_started_it(self) -> None:
        """
        Scenario 8.154: a task applies the starting user's context and resets it.
        Given: DB_CONTEXT set and a propagated context naming alice
        When: the task context is entered and left
        Then: the hook saw (alice, local=False) on entry and (None, local=False) on exit.
        """
        with override_extensions(DB_CONTEXT=HOOK):
            with CeleryCalculationContext({"request_obj": {"user": ALICE}, "calculation_id": "c1"}, None):
                self.assertEqual(CALLS, [("alice", False)])
        self.assertEqual(CALLS, [("alice", False), (None, False)])

    # -- 8.155 ---------------------------------------------------------
    def test_8_155_reset_happens_even_when_the_task_fails(self) -> None:
        """
        Scenario 8.155: a failing task still resets the context.
        Given: the same setup
        When: the task body raises
        Then: the exception propagates and the hook's last call is (None, local=False).
        """
        with override_extensions(DB_CONTEXT=HOOK):
            with self.assertRaises(RuntimeError):
                with CeleryCalculationContext({"request_obj": {"user": ALICE}}, None):
                    raise RuntimeError("task failed")
        self.assertEqual(CALLS[-1], (None, False))

    # -- 8.156 ---------------------------------------------------------
    def test_8_156_no_context_no_hook(self) -> None:
        """
        Scenario 8.156: synchronous mode and projects without DB_CONTEXT are untouched.
        Given: a task context without propagated context, then DB_CONTEXT unset
        When: the task context is entered
        Then: the hook is never called.
        """
        with override_extensions(DB_CONTEXT=HOOK):
            with CeleryCalculationContext(None, None):
                pass
        with CeleryCalculationContext({"request_obj": {"user": ALICE}}, None):
            pass
        self.assertEqual(CALLS, [])
        self.assertIsNone(db_context.hook())
