"""Database context in lex's own on_commit callbacks (DB_CONTEXT).

Intent
------
Work lex defers to ``transaction.on_commit`` runs after the request's
transaction ended, where a ``SET LOCAL`` no longer holds. With
``lex_config.DB_CONTEXT`` set, each of lex's own commit callbacks — the
recalculation after ``as_transaction``, the calculation-state bookkeeping in
``CalculationModel`` and the deferred ``CalculationLog`` write — runs in a
fresh transaction that first tells the database who started the work
(``local=True``). Without the hook the callbacks are registered exactly as
before.

Cluster 7w — scenarios 7.240–7.243. Type: U.
Covers: lex/core/transactions/transactions.py (as_transaction),
lex/core/models/CalculationModel.py (_register_in_progress_state_persistence,
_register_terminal_state_persistence), lex/audit_logging/models/CalculationLog.py
(log), lex/core/db_context.py (on_commit).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7w_db_context_on_commit.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import TestCase

from lex.api.utils.Context import operation_context
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.core.calculated_updates.update_handler import CalculatedModelUpdateHandler
from lex.core.extensions import override_extensions
from lex.core.models.CalculationModel import CalculationModel
from lex.core.transactions.transactions import as_transaction

pytestmark = pytest.mark.calculations

CALLS: list = []


def record(user, *, local):
    CALLS.append((getattr(user, "username", None), local))


HOOK = f"{__name__}.record"


class TestCluster07w_DbContextOnCommit(TestCase):
    """Cluster 7w: commit callbacks re-apply DB_CONTEXT."""

    def setUp(self) -> None:
        super().setUp()
        CALLS.clear()
        user = SimpleNamespace(username="cluster7w_user", is_authenticated=True)
        token = operation_context.set({"request_obj": SimpleNamespace(user=user)})
        self.addCleanup(operation_context.reset, token)
        # as_transaction swaps the update handler's save behaviour; app start
        # creates that handler, a bare test does not.
        self.addCleanup(setattr, CalculatedModelUpdateHandler, "instance", CalculatedModelUpdateHandler.instance)
        CalculatedModelUpdateHandler()

    def _seen_in_callback(self, effect):
        """What the hook had been told when ``effect`` ran."""
        seen = []
        effect.side_effect = lambda *args, **kwargs: seen.append(list(CALLS))
        return seen

    # -- 7.240 ---------------------------------------------------------
    def test_7_240_recalculation_after_as_transaction(self) -> None:
        """
        Scenario 7.240: as_transaction's recalculation runs as the caller.
        Given: DB_CONTEXT set and an operation started by "cluster7w_user"
        When: a function runs through as_transaction and the transaction commits
        Then: nothing runs before the commit; the recalculation runs after the
              hook was told (cluster7w_user, local=True).
        """
        with override_extensions(DB_CONTEXT=HOOK), mock.patch(
            "lex.core.transactions.transactions.ObjectsToRecalculateStore.do_recalculations"
        ) as recalculate:
            seen = self._seen_in_callback(recalculate)
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                as_transaction(lambda: None)()
            self.assertEqual((CALLS, seen), ([], []))
            for callback in callbacks:
                callback()
        self.assertEqual(seen, [[("cluster7w_user", True)]])

    # -- 7.241 ---------------------------------------------------------
    def test_7_241_calculation_state_bookkeeping(self) -> None:
        """
        Scenario 7.241: CalculationModel's state bookkeeping runs as the caller.
        Given: DB_CONTEXT set; a record in progress and a finished one
        When: both state registrations happen inside a transaction that commits
        Then: each callback ran after its own (cluster7w_user, local=True) and
              the persisted-state markers are set.
        """
        in_progress = SimpleNamespace(is_calculated=CalculationModel.IN_PROGRESS)
        finished = SimpleNamespace(is_calculated=CalculationModel.SUCCESS)
        with override_extensions(DB_CONTEXT=HOOK), self.captureOnCommitCallbacks(execute=True):
            CalculationModel._register_in_progress_state_persistence(in_progress)
            CalculationModel._register_terminal_state_persistence(finished)
            self.assertEqual(CALLS, [])
        self.assertEqual(CALLS, [("cluster7w_user", True), ("cluster7w_user", True)])
        self.assertTrue(CalculationModel._has_persisted_in_progress_state(in_progress))
        self.assertEqual(
            getattr(finished, CalculationModel._TERMINAL_STATE_PERSISTENCE_ATTR), CalculationModel.SUCCESS
        )

    # -- 7.242 ---------------------------------------------------------
    def test_7_242_deferred_calculation_log_write(self) -> None:
        """
        Scenario 7.242: a CalculationLog line written mid-transaction is
        persisted as the caller.
        Given: DB_CONTEXT set and a resolvable log context
        When: CalculationLog.log runs inside a transaction that commits
        Then: the message is persisted after the hook was told
              (cluster7w_user, local=True).
        """
        context = SimpleNamespace(audit_log=None, current_record=None, root_record=None, calculation_id="c7w")
        with override_extensions(DB_CONTEXT=HOOK), mock.patch(
            "lex.audit_logging.utils.ContextResolver.ContextResolver.resolve", return_value=context
        ), mock.patch.object(CalculationLog, "_persist_message") as persist:
            seen = self._seen_in_callback(persist)
            with self.captureOnCommitCallbacks(execute=True):
                CalculationLog.log("cluster 7w line")
                self.assertEqual(seen, [])
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[1], "cluster 7w line")
        self.assertEqual(seen, [[("cluster7w_user", True)]])

    # -- 7.243 ---------------------------------------------------------
    def test_7_243_without_hook_callbacks_run_as_before(self) -> None:
        """
        Scenario 7.243: without DB_CONTEXT the callbacks are the plain ones.
        Given: no DB_CONTEXT
        When: the three call sites register their callbacks and commit
        Then: they still run and the hook is never called.
        """
        in_progress = SimpleNamespace(is_calculated=CalculationModel.IN_PROGRESS)
        context = SimpleNamespace(audit_log=None, current_record=None, root_record=None, calculation_id="c7w")
        with mock.patch(
            "lex.core.transactions.transactions.ObjectsToRecalculateStore.do_recalculations"
        ) as recalculate, mock.patch(
            "lex.audit_logging.utils.ContextResolver.ContextResolver.resolve", return_value=context
        ), mock.patch.object(CalculationLog, "_persist_message") as persist:
            with self.captureOnCommitCallbacks(execute=True):
                as_transaction(lambda: None)()
                CalculationModel._register_in_progress_state_persistence(in_progress)
                CalculationLog.log("plain")
        recalculate.assert_called_once()
        persist.assert_called_once()
        self.assertTrue(CalculationModel._has_persisted_in_progress_state(in_progress))
        self.assertEqual(CALLS, [])
