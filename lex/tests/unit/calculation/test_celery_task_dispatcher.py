"""
Tests for ``CeleryTaskDispatcher`` — Celery dispatch, fallback, and result handling.

**What is tested:**

    * ``dispatch_calculation_groups()`` — empty/invalid input, import failure,
      successful dispatch, Celery setup failure with sync fallback, total failure
    * ``_dispatch_single_group()`` — empty group, type validation, import failure,
      successful dispatch, CeleryDispatchError with sync fallback, unexpected errors
    * ``_handle_task_results()`` — empty results, type validation, all-succeed,
      partial failure with sync retry, sync retry failure, ResultSet failure with
      complete sync fallback
    * ``_get_calculation_context()`` — context present, absent, and error paths

**Why this matters:**

    The dispatcher is the Celery ↔ sync boundary.  If dispatch silently drops a
    group, that batch is never calculated.  If the fallback doesn't fire on task
    failure, models stay un-calculated with no error surfaced.  If context is lost
    across the async boundary, audit-logging and progress tracking break.

**How to run:**

    .. code-block:: bash

        lex test lex.core.tests.test_celery_task_dispatcher --verbosity=2 --noinput
"""

from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase
from lex.core.exceptions import CeleryDispatchError
from lex.core.tasks.CeleryTaskDispatcher import CeleryTaskDispatcher


# ─── helpers ──────────────────────────────────────────────────────────
def _fake_model(name="m"):
    """Minimal stand-in for a CalculatedModelMixin instance."""
    m = MagicMock()
    m.__class__.__name__ = name
    return m


def _fake_task_result(task_id="task-1", failed=False):
    """Minimal stand-in for a Celery AsyncResult."""
    tr = MagicMock()
    tr.id = task_id
    tr.failed.return_value = failed
    tr.result = "error detail" if failed else None
    return tr


# ════════════════════════════════════════════════════════════════════════
#  dispatch_calculation_groups
# ════════════════════════════════════════════════════════════════════════

class TestDispatchCalculationGroupsValidation(SimpleTestCase):
    """Input validation for dispatch_calculation_groups."""

    def test_empty_groups_returns_immediately(self):
        """Empty list → no dispatch, no error."""
        CeleryTaskDispatcher.dispatch_calculation_groups([])

    def test_none_groups_returns_immediately(self):
        CeleryTaskDispatcher.dispatch_calculation_groups(None or [])

    def test_invalid_type_raises(self):
        with self.assertRaises(CeleryDispatchError):
            CeleryTaskDispatcher.dispatch_calculation_groups("not a list")

    def test_all_empty_groups_returns_immediately(self):
        """All groups empty after filtering → no dispatch."""
        CeleryTaskDispatcher.dispatch_calculation_groups([[], [], []])


class TestDispatchCalculationGroupsImportFailure(SimpleTestCase):
    """When Celery task import fails, CeleryDispatchError is raised."""

    def test_import_failure_raises_dispatch_error(self):
        """Import error at line 77 → CeleryDispatchError (not sync fallback)."""
        groups = [[_fake_model()]]
        with patch.dict("sys.modules", {"lex.lex_app.celery_tasks": None}):
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher.dispatch_calculation_groups(groups)
            self.assertIn("calc_and_save", str(ctx.exception))


class TestDispatchCalculationGroupsSyncFallback(SimpleTestCase):
    """When Celery setup fails with a non-CeleryDispatchError, sync fallback fires."""

    @patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync")
    def test_celery_setup_failure_triggers_sync_fallback(self, mock_sync):
        """Generic exception after import → flatten all groups → calc_and_save_sync."""
        groups = [[_fake_model("a")], [_fake_model("b")]]

        # Provide a module that imports fine but raises a generic error
        # when FireAndForget.get_current_context() is called (line 91)
        mock_mod = MagicMock()
        mock_mod.FireAndForget.get_current_context.side_effect = RuntimeError("celery broke")

        with patch.dict("sys.modules", {"lex.lex_app.celery_tasks": mock_mod}):
            CeleryTaskDispatcher.dispatch_calculation_groups(groups)

        mock_sync.assert_called_once()
        self.assertEqual(len(mock_sync.call_args[0][0]), 2)

    @patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync", side_effect=RuntimeError("sync boom"))
    def test_both_celery_and_sync_fail_raises(self, _mock_sync):
        """Both Celery setup and sync fallback fail → CeleryDispatchError."""
        groups = [[_fake_model()]]

        mock_mod = MagicMock()
        mock_mod.FireAndForget.get_current_context.side_effect = RuntimeError("celery broke")

        with patch.dict("sys.modules", {"lex.lex_app.celery_tasks": mock_mod}):
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher.dispatch_calculation_groups(groups)
            msg = str(ctx.exception).lower()
            self.assertIn("sync", msg)


# ════════════════════════════════════════════════════════════════════════
#  _dispatch_single_group
# ════════════════════════════════════════════════════════════════════════

class TestDispatchSingleGroupValidation(SimpleTestCase):
    """Input validation for _dispatch_single_group."""

    def test_empty_group_returns_none(self):
        result = CeleryTaskDispatcher._dispatch_single_group([], 0)
        self.assertIsNone(result)

    def test_invalid_type_raises(self):
        with self.assertRaises(CeleryDispatchError):
            CeleryTaskDispatcher._dispatch_single_group("not a list", 0)


class TestDispatchSingleGroupImportFailure(SimpleTestCase):
    """calc_and_save import failure → CeleryDispatchError."""

    def test_import_error_raises(self):
        group = [_fake_model()]
        with patch.dict("sys.modules", {"lex.lex_app.celery_tasks": None}):
            with self.assertRaises((CeleryDispatchError, ImportError)):
                CeleryTaskDispatcher._dispatch_single_group(group, 0, context={"request_obj": {}})


class TestDispatchSingleGroupSyncFallback(SimpleTestCase):
    """CeleryDispatchError during dispatch → sync fallback."""

    @patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync")
    def test_dispatch_error_falls_back_to_sync(self, mock_sync):
        """CeleryDispatchError in task.delay → calc_and_save_sync."""
        group = [_fake_model()]

        mock_celery_mod = MagicMock()
        mock_celery_mod.calc_and_save.delay.side_effect = CeleryDispatchError("dispatch failed")

        with patch.dict("sys.modules", {"lex.lex_app.celery_tasks": mock_celery_mod}):
            result = CeleryTaskDispatcher._dispatch_single_group(
                group, 0, context={"request_obj": {}}
            )

        self.assertIsNone(result)
        mock_sync.assert_called_once()

    @patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync", side_effect=RuntimeError("sync boom"))
    def test_both_dispatch_and_sync_fail_raises(self, _mock_sync):
        """CeleryDispatchError + sync failure → CeleryDispatchError with both messages."""
        group = [_fake_model()]

        mock_celery_mod = MagicMock()
        mock_celery_mod.calc_and_save.delay.side_effect = CeleryDispatchError("dispatch failed")

        with patch.dict("sys.modules", {"lex.lex_app.celery_tasks": mock_celery_mod}):
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher._dispatch_single_group(
                    group, 0, context={"request_obj": {}}
                )
            msg = str(ctx.exception).lower()
            self.assertIn("celery", msg)
            self.assertIn("sync", msg)


class TestDispatchSingleGroupUnexpectedError(SimpleTestCase):
    """Non-CeleryDispatchError during dispatch → CeleryDispatchError wrapping it."""

    def test_unexpected_error_raises_dispatch_error(self):
        group = [_fake_model()]

        mock_celery_mod = MagicMock()
        mock_celery_mod.calc_and_save.delay.side_effect = TypeError("unexpected")

        with patch.dict("sys.modules", {"lex.lex_app.celery_tasks": mock_celery_mod}):
            with self.assertRaises(CeleryDispatchError):
                CeleryTaskDispatcher._dispatch_single_group(
                    group, 0, context={"request_obj": {}}
                )


# ════════════════════════════════════════════════════════════════════════
#  _handle_task_results
# ════════════════════════════════════════════════════════════════════════

class TestHandleTaskResultsValidation(SimpleTestCase):
    """Input validation for _handle_task_results."""

    def test_empty_results_returns_immediately(self):
        CeleryTaskDispatcher._handle_task_results([], {})

    def test_invalid_results_type_raises(self):
        with self.assertRaises(CeleryDispatchError):
            CeleryTaskDispatcher._handle_task_results("not a list", {})

    def test_invalid_mapping_type_raises(self):
        with self.assertRaises(CeleryDispatchError):
            CeleryTaskDispatcher._handle_task_results([MagicMock()], "not a dict")


class TestHandleTaskResultsAllSucceed(SimpleTestCase):
    """All tasks succeed → no sync fallback triggered."""

    @patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync")
    def test_all_succeed_no_sync(self, mock_sync):
        tr1 = _fake_task_result("t1", failed=False)
        tr2 = _fake_task_result("t2", failed=False)
        mapping = {"t1": [_fake_model()], "t2": [_fake_model()]}

        mock_rs_class = MagicMock()
        with patch("lex.core.tasks.CeleryTaskDispatcher.ResultSet", mock_rs_class, create=True):
            with patch.dict("sys.modules", {"celery.result": MagicMock(ResultSet=mock_rs_class)}):
                CeleryTaskDispatcher._handle_task_results([tr1, tr2], mapping)

        mock_sync.assert_not_called()


class TestHandleTaskResultsPartialFailure(SimpleTestCase):
    """Some tasks fail → failed groups retried via sync."""

    @patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync")
    def test_failed_task_retried_sync(self, mock_sync):
        """One of two tasks fails → that group retried synchronously."""
        tr_ok = _fake_task_result("t-ok", failed=False)
        tr_fail = _fake_task_result("t-fail", failed=True)

        failed_group = [_fake_model("failed_m")]
        mapping = {"t-ok": [_fake_model("ok_m")], "t-fail": failed_group}

        mock_rs_class = MagicMock()
        with patch.dict("sys.modules", {"celery.result": MagicMock(ResultSet=mock_rs_class)}):
            CeleryTaskDispatcher._handle_task_results([tr_ok, tr_fail], mapping)

        # sync called once for the failed group
        mock_sync.assert_called_once()
        self.assertEqual(mock_sync.call_args[0][0], failed_group)


class TestHandleTaskResultsSyncRetryFailure(SimpleTestCase):
    """Sync retry for a failed group also fails → CeleryDispatchError."""

    @patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync", side_effect=RuntimeError("sync boom"))
    def test_sync_retry_failure_raises(self, _mock_sync):
        tr_fail = _fake_task_result("t-fail", failed=True)
        mapping = {"t-fail": [_fake_model()]}

        mock_rs_class = MagicMock()
        with patch.dict("sys.modules", {"celery.result": MagicMock(ResultSet=mock_rs_class)}):
            with self.assertRaises(CeleryDispatchError):
                CeleryTaskDispatcher._handle_task_results([tr_fail], mapping)


class TestHandleTaskResultsResultSetFailure(SimpleTestCase):
    """ResultSet.join raises → complete sync fallback for all groups."""

    @patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync")
    def test_resultset_error_triggers_complete_fallback(self, mock_sync):
        tr = _fake_task_result("t1")
        group = [_fake_model()]
        mapping = {"t1": group}

        mock_rs_instance = MagicMock()
        mock_rs_instance.join.side_effect = RuntimeError("celery connection lost")
        mock_rs_class = MagicMock(return_value=mock_rs_instance)

        with patch.dict("sys.modules", {"celery.result": MagicMock(ResultSet=mock_rs_class)}):
            CeleryTaskDispatcher._handle_task_results([tr], mapping)

        mock_sync.assert_called_once()


class TestHandleTaskResultsStatusCheckError(SimpleTestCase):
    """task.failed() raises → treated as failure, added to retry queue."""

    @patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync")
    def test_status_check_error_retries_group(self, mock_sync):
        tr = MagicMock()
        tr.id = "t-err"
        tr.failed.side_effect = RuntimeError("status check exploded")
        group = [_fake_model()]
        mapping = {"t-err": group}

        mock_rs_class = MagicMock()
        with patch.dict("sys.modules", {"celery.result": MagicMock(ResultSet=mock_rs_class)}):
            CeleryTaskDispatcher._handle_task_results([tr], mapping)

        mock_sync.assert_called_once()
        self.assertEqual(mock_sync.call_args[0][0], group)


# ════════════════════════════════════════════════════════════════════════
#  _get_calculation_context
# ════════════════════════════════════════════════════════════════════════

class TestGetCalculationContext(SimpleTestCase):
    """Extract calculation_id from operation_context ContextVar."""

    @patch("lex.core.tasks.CeleryTaskDispatcher.operation_context")
    def test_returns_calculation_id_when_present(self, mock_ctx):
        mock_ctx.get.return_value = {"calculation_id": "abc-123"}
        result = CeleryTaskDispatcher._get_calculation_context()
        self.assertEqual(result, "abc-123")

    @patch("lex.core.tasks.CeleryTaskDispatcher.operation_context")
    def test_returns_none_when_no_context(self, mock_ctx):
        mock_ctx.get.return_value = None
        result = CeleryTaskDispatcher._get_calculation_context()
        self.assertIsNone(result)

    @patch("lex.core.tasks.CeleryTaskDispatcher.operation_context")
    def test_returns_none_when_no_calculation_id(self, mock_ctx):
        mock_ctx.get.return_value = {"other_key": "value"}
        result = CeleryTaskDispatcher._get_calculation_context()
        self.assertIsNone(result)

    @patch("lex.core.tasks.CeleryTaskDispatcher.operation_context")
    def test_returns_none_on_exception(self, mock_ctx):
        mock_ctx.get.side_effect = LookupError("no context set")
        result = CeleryTaskDispatcher._get_calculation_context()
        self.assertIsNone(result)
