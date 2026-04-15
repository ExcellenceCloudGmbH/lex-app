"""
Tests for celery_tasks.py — dispatch utilities, context managers, and detection.

**What is tested:**

    * ``_celery_is_active()`` — environment variable detection for Celery dispatch
    * ``is_celery_worker_process()`` — all detection paths (current_task, env vars,
      sys.argv)
    * ``CallbackTask._extract_model_instances()`` — extracting Django Model instances
      from task args (single, list, tuple, non-model, empty)
    * ``CallbackTask.on_success()`` / ``on_failure()`` — callback entry points skip
      initial_data_upload, invoke _update_model_status for CalculationModel instances
    * ``FireAndForget`` — context manager push/pop, should_force_dispatch filtering,
      get_current_context, wait_for_completion
    * ``WaitForTasks`` — context manager push/pop, should_dispatch filtering,
      get_current_context, wait_for_completion, FireAndForget override
    * ``is_in_fire_and_forget_context()`` — utility combining context lookup + dispatch
    * ``register_task_with_context()`` — result registration with active context
    * ``respect_fire_and_forget`` — decorator injects _force_async kwarg

**Why this matters:**

    These components control whether calculations run synchronously or are dispatched
    to Celery workers.  If ``_celery_is_active`` reads the wrong env var format,
    tasks silently fall back to sync and performance degrades.  If context managers
    don't push/pop correctly, nested scopes leak tasks into the wrong parent.  If
    ``_extract_model_instances`` can't parse args, callback status updates are lost.

**How to run:**

    .. code-block:: bash

        lex test lex.tests.test_celery_tasks_unit --verbosity=2 --noinput
"""

from unittest.mock import MagicMock, patch, PropertyMock

from django.db.models import Model
from django.test import SimpleTestCase

from lex.lex_app.celery_tasks import (
    _celery_is_active,
    is_celery_worker_process,
    CallbackTask,
    FireAndForget,
    WaitForTasks,
    is_in_fire_and_forget_context,
    register_task_with_context,
    respect_fire_and_forget,
    tasks_context,
    unblock_tasks_context,
)


# ─── helpers ──────────────────────────────────────────────────────────

def _reset_context_vars():
    """Reset both ContextVars to empty stacks so tests are isolated."""
    tasks_context.set({"task_context_stack": []})
    unblock_tasks_context.set({"unblock_context_stack": []})


def _make_model_instance(pk=1):
    """Create a minimal Django Model subclass instance for extraction tests."""
    cls = type("FakeModel", (Model,), {"__module__": "test", "Meta": type("Meta", (), {"app_label": "test"})})
    inst = cls.__new__(cls)
    inst.pk = pk
    return inst


# ════════════════════════════════════════════════════════════════════════
#  _celery_is_active
# ════════════════════════════════════════════════════════════════════════

class TestCeleryIsActive(SimpleTestCase):
    """Verify _celery_is_active reads CELERY_ACTIVE env var correctly."""

    @patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False)
    def test_true_lowercase(self):
        """CELERY_ACTIVE=true → True."""
        self.assertTrue(_celery_is_active())

    @patch.dict("os.environ", {"CELERY_ACTIVE": "True"}, clear=False)
    def test_true_titlecase(self):
        """CELERY_ACTIVE=True → True (case-insensitive)."""
        self.assertTrue(_celery_is_active())

    @patch.dict("os.environ", {"CELERY_ACTIVE": "TRUE"}, clear=False)
    def test_true_uppercase(self):
        """CELERY_ACTIVE=TRUE → True."""
        self.assertTrue(_celery_is_active())

    @patch.dict("os.environ", {"CELERY_ACTIVE": "False"}, clear=False)
    def test_false_value(self):
        """CELERY_ACTIVE=False → False."""
        self.assertFalse(_celery_is_active())

    @patch.dict("os.environ", {"CELERY_ACTIVE": "0"}, clear=False)
    def test_zero_value(self):
        """CELERY_ACTIVE=0 → False (not 'true')."""
        self.assertFalse(_celery_is_active())

    @patch.dict("os.environ", {}, clear=True)
    def test_unset_defaults_false(self):
        """CELERY_ACTIVE not set → False (default)."""
        self.assertFalse(_celery_is_active())

    @patch.dict("os.environ", {"CELERY_ACTIVE": ""}, clear=False)
    def test_empty_string_is_false(self):
        """CELERY_ACTIVE='' → False."""
        self.assertFalse(_celery_is_active())


# ════════════════════════════════════════════════════════════════════════
#  is_celery_worker_process
# ════════════════════════════════════════════════════════════════════════

class TestIsCeleryWorkerProcess(SimpleTestCase):
    """Verify all detection paths for identifying a Celery worker process."""

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "", "CELERY_WORKER_RUNNING": ""}, clear=False)
    def test_current_task_with_request_returns_true(self):
        """celery.current_task with a request attribute → True."""
        mock_current_task = MagicMock()
        mock_current_task.request = MagicMock()
        # The function does `from celery import current_task` — patch at celery module level
        with patch("celery.current_task", mock_current_task):
            self.assertTrue(is_celery_worker_process())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "true", "CELERY_WORKER_RUNNING": ""}, clear=False)
    def test_is_running_in_celery_env_var(self):
        """IS_RUNNING_IN_CELERY=true → True."""
        with patch("celery.current_task", None):
            self.assertTrue(is_celery_worker_process())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "", "CELERY_WORKER_RUNNING": "true"}, clear=False)
    def test_celery_worker_running_env_var(self):
        """CELERY_WORKER_RUNNING=true → True."""
        with patch("celery.current_task", None):
            self.assertTrue(is_celery_worker_process())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "", "CELERY_WORKER_RUNNING": ""}, clear=False)
    def test_sys_argv_celery_worker(self):
        """sys.argv contains 'celery' and 'worker' → True."""
        with patch("celery.current_task", None):
            with patch("sys.argv", ["celery", "worker", "--concurrency=4"]):
                self.assertTrue(is_celery_worker_process())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "", "CELERY_WORKER_RUNNING": ""}, clear=False)
    def test_not_a_worker_process(self):
        """No indicators present → False."""
        with patch("celery.current_task", None):
            with patch("sys.argv", ["manage.py", "runserver"]):
                self.assertFalse(is_celery_worker_process())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "", "CELERY_WORKER_RUNNING": ""}, clear=False)
    def test_import_error_handled_gracefully(self):
        """ImportError from celery → falls through to env checks."""
        with patch.dict("sys.modules", {"celery": None}):
            with patch("sys.argv", ["manage.py", "test"]):
                self.assertFalse(is_celery_worker_process())


# ════════════════════════════════════════════════════════════════════════
#  CallbackTask._extract_model_instances
# ════════════════════════════════════════════════════════════════════════

class TestExtractModelInstances(SimpleTestCase):
    """Verify model instance extraction from task args."""

    def setUp(self):
        self.task = CallbackTask()

    def test_single_model_in_args(self):
        """args=(Model,) → [Model]."""
        model = _make_model_instance(pk=1)
        result = self.task._extract_model_instances((model,))
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], model)

    def test_list_of_models_in_args(self):
        """args=([Model, Model],) → [Model, Model]."""
        m1 = _make_model_instance(pk=1)
        m2 = _make_model_instance(pk=2)
        result = self.task._extract_model_instances(([m1, m2],))
        self.assertEqual(len(result), 2)

    def test_tuple_of_models_in_args(self):
        """args=((Model, Model),) → [Model, Model]."""
        m1 = _make_model_instance(pk=1)
        m2 = _make_model_instance(pk=2)
        result = self.task._extract_model_instances(((m1, m2),))
        self.assertEqual(len(result), 2)

    def test_mixed_list_filters_non_models(self):
        """args=([Model, 'string', 42],) → only Model instances."""
        model = _make_model_instance(pk=1)
        result = self.task._extract_model_instances(([model, "not a model", 42],))
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], model)

    def test_non_model_first_arg(self):
        """args=('string',) → empty list."""
        result = self.task._extract_model_instances(("not a model",))
        self.assertEqual(result, [])

    def test_empty_args(self):
        """args=() → empty list."""
        result = self.task._extract_model_instances(())
        self.assertEqual(result, [])

    def test_none_args(self):
        """args=None → empty list."""
        result = self.task._extract_model_instances(None)
        self.assertEqual(result, [])


# ════════════════════════════════════════════════════════════════════════
#  CallbackTask.on_success / on_failure
# ════════════════════════════════════════════════════════════════════════

class TestCallbackTaskEntryPoints(SimpleTestCase):
    """Verify on_success and on_failure skip initial_data_upload and dispatch correctly."""

    def setUp(self):
        self.task = CallbackTask()

    def test_on_success_skips_initial_data_upload(self):
        """Task named 'initial_data_upload' → returns immediately, no status update."""
        self.task.name = "initial_data_upload"
        # Should not raise even with invalid args
        self.task.on_success(retval=None, task_id="t1", args=(), kwargs={})

    def test_on_failure_skips_initial_data_upload(self):
        """Task named 'initial_data_upload' → returns immediately."""
        self.task.name = "initial_data_upload"
        self.task.on_failure(
            exc=RuntimeError("boom"), task_id="t1",
            args=(), kwargs={}, einfo=None,
        )

    @patch.object(CallbackTask, "_update_model_status")
    @patch.object(CallbackTask, "_extract_model_instances")
    def test_on_success_calls_update_for_calculation_models(self, mock_extract, mock_update):
        """on_success updates status to SUCCESS for CalculationModel instances."""
        self.task.name = "calc_and_save"
        mock_model = MagicMock(spec=["is_calculated"])
        # Make isinstance check pass for CalculationModel
        mock_model.__class__ = type("FakeCalcModel", (MagicMock,), {})
        # We need the isinstance check to pass — mock _extract to return a CalculationModel mock
        from lex.core.models.CalculationModel import CalculationModel
        calc_mock = MagicMock(spec=CalculationModel)
        mock_extract.return_value = [calc_mock]

        self.task.on_success(
            retval=None, task_id="t-success",
            args=([calc_mock],), kwargs={"context": {"calc_id": "c1"}},
        )

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        self.assertEqual(call_kwargs[1]["task_id"], "t-success")

    @patch.object(CallbackTask, "_update_model_status")
    @patch.object(CallbackTask, "_extract_model_instances")
    def test_on_failure_calls_update_with_error_info(self, mock_extract, mock_update):
        """on_failure updates status to ERROR with error message and stack trace."""
        self.task.name = "calc_and_save"
        from lex.core.models.CalculationModel import CalculationModel
        calc_mock = MagicMock(spec=CalculationModel)
        mock_extract.return_value = [calc_mock]

        self.task.on_failure(
            exc=RuntimeError("db connection lost"),
            task_id="t-fail",
            args=([calc_mock],),
            kwargs={"context": {"calc_id": "c2"}, "model_context": None},
            einfo="Traceback: ...",
        )

        mock_update.assert_called_once()
        call_args = mock_update.call_args
        self.assertIn("db connection lost", call_args[1]["error_message"])
        self.assertEqual(call_args[1]["stack_trace"], "Traceback: ...")

    def test_on_success_swallows_callback_exception(self):
        """Exception in on_success is caught and logged, not re-raised."""
        self.task.name = "calc_and_save"
        with patch.object(
            CallbackTask, "_extract_model_instances",
            side_effect=RuntimeError("extraction failed"),
        ):
            # Should not raise
            self.task.on_success(retval=None, task_id="t1", args=(), kwargs={})


# ════════════════════════════════════════════════════════════════════════
#  FireAndForget
# ════════════════════════════════════════════════════════════════════════

class TestFireAndForgetShouldForceDispatch(SimpleTestCase):
    """Verify task name filtering logic for FireAndForget dispatch."""

    def test_no_filter_dispatches_all(self):
        """force_tasks=None → all tasks dispatched."""
        ff = FireAndForget(force_tasks=None)
        self.assertTrue(ff.should_force_dispatch("any_task"))

    def test_force_tasks_whitelist(self):
        """force_tasks={'a', 'b'} → only those tasks dispatched."""
        ff = FireAndForget(force_tasks={"send_email", "notify_slack"})
        self.assertTrue(ff.should_force_dispatch("send_email"))
        self.assertFalse(ff.should_force_dispatch("compute_nav"))

    def test_exclude_tasks_takes_priority(self):
        """exclude_tasks overrides force_tasks=None."""
        ff = FireAndForget(force_tasks=None, exclude_tasks={"compute_nav"})
        self.assertTrue(ff.should_force_dispatch("send_email"))
        self.assertFalse(ff.should_force_dispatch("compute_nav"))

    def test_exclude_overrides_force(self):
        """Task in both force and exclude → excluded."""
        ff = FireAndForget(
            force_tasks={"send_email", "compute_nav"},
            exclude_tasks={"compute_nav"},
        )
        self.assertTrue(ff.should_force_dispatch("send_email"))
        self.assertFalse(ff.should_force_dispatch("compute_nav"))


class TestFireAndForgetContextManager(SimpleTestCase):
    """Verify context manager push/pop and get_current_context."""

    def setUp(self):
        _reset_context_vars()

    def tearDown(self):
        _reset_context_vars()

    @patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False)
    def test_enter_pushes_to_stack(self):
        """Entering FireAndForget pushes self onto unblock_context_stack."""
        ff = FireAndForget()
        ff._active = True
        ff.__enter__()
        self.assertIs(FireAndForget.get_current_context(), ff)
        ff.__exit__(None, None, None)

    @patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False)
    def test_exit_pops_from_stack(self):
        """Exiting FireAndForget removes self from stack."""
        ff = FireAndForget()
        ff._active = True
        ff.__enter__()
        ff.__exit__(None, None, None)
        self.assertIsNone(FireAndForget.get_current_context())

    @patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False)
    def test_nested_contexts_innermost_wins(self):
        """Nested FireAndForget → get_current_context returns innermost."""
        outer = FireAndForget(force_tasks={"outer_task"})
        outer._active = True
        inner = FireAndForget(force_tasks={"inner_task"})
        inner._active = True

        outer.__enter__()
        inner.__enter__()
        current = FireAndForget.get_current_context()
        self.assertIs(current, inner)

        inner.__exit__(None, None, None)
        self.assertIs(FireAndForget.get_current_context(), outer)

        outer.__exit__(None, None, None)
        self.assertIsNone(FireAndForget.get_current_context())

    def test_inactive_noop(self):
        """When CELERY_ACTIVE is false, context manager is a no-op."""
        ff = FireAndForget()
        ff._active = False
        ff.__enter__()
        self.assertIsNone(FireAndForget.get_current_context())
        ff.__exit__(None, None, None)


class TestFireAndForgetWaitForCompletion(SimpleTestCase):
    """Verify wait_for_completion blocks on dispatched results."""

    def test_empty_results_returns_immediately(self):
        """No dispatched results → no-op."""
        ff = FireAndForget()
        ff.wait_for_completion()  # should not raise

    def test_waits_for_all_results(self):
        """Calls result.get() for each dispatched result."""
        from contextlib import nullcontext
        ff = FireAndForget()
        r1 = MagicMock()
        r2 = MagicMock()
        ff.dispatched_results = [r1, r2]

        with patch("lex.lex_app.celery_tasks.allow_join_result", return_value=nullcontext()):
            ff.wait_for_completion()

        r1.get.assert_called_once()
        r2.get.assert_called_once()
        self.assertEqual(ff.dispatched_results, [])

    def test_raises_on_task_failure(self):
        """If a dispatched task raises, it propagates."""
        from contextlib import nullcontext
        ff = FireAndForget()
        r1 = MagicMock()
        r1.get.side_effect = RuntimeError("task failed")
        r1.id = "t-1"
        ff.dispatched_results = [r1]

        with patch("lex.lex_app.celery_tasks.allow_join_result", return_value=nullcontext()):
            with self.assertRaises(RuntimeError):
                ff.wait_for_completion()


# ════════════════════════════════════════════════════════════════════════
#  WaitForTasks
# ════════════════════════════════════════════════════════════════════════

class TestWaitForTasksShouldDispatch(SimpleTestCase):
    """Verify task name filtering logic for WaitForTasks dispatch."""

    def test_no_filter_dispatches_all(self):
        """include_tasks=None → all tasks dispatched."""
        wft = WaitForTasks(include_tasks=None)
        self.assertTrue(wft.should_dispatch("any_task"))

    def test_include_tasks_whitelist(self):
        """include_tasks={'a', 'b'} → only those tasks dispatched."""
        wft = WaitForTasks(include_tasks={"compute_nav", "compute_risk"})
        self.assertTrue(wft.should_dispatch("compute_nav"))
        self.assertFalse(wft.should_dispatch("send_email"))

    def test_exclude_tasks_takes_priority(self):
        """exclude_tasks overrides include_tasks=None."""
        wft = WaitForTasks(include_tasks=None, exclude_tasks={"send_email"})
        self.assertTrue(wft.should_dispatch("compute_nav"))
        self.assertFalse(wft.should_dispatch("send_email"))

    def test_exclude_overrides_include(self):
        """Task in both include and exclude → excluded."""
        wft = WaitForTasks(
            include_tasks={"compute_nav", "send_email"},
            exclude_tasks={"send_email"},
        )
        self.assertTrue(wft.should_dispatch("compute_nav"))
        self.assertFalse(wft.should_dispatch("send_email"))


class TestWaitForTasksContextManager(SimpleTestCase):
    """Verify context manager push/pop and get_current_context."""

    def setUp(self):
        _reset_context_vars()

    def tearDown(self):
        _reset_context_vars()

    @patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False)
    def test_enter_pushes_to_stack(self):
        """Entering WaitForTasks pushes self onto task_context_stack."""
        wft = WaitForTasks()
        wft._active = True
        wft.__enter__()
        self.assertIs(WaitForTasks.get_current_context(), wft)
        # Clean up without triggering wait_for_completion
        tasks_context.get()["task_context_stack"].pop()

    @patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False)
    def test_exit_pops_and_waits(self):
        """Exiting WaitForTasks pops from stack and calls wait_for_completion."""
        wft = WaitForTasks()
        wft._active = True
        wft.__enter__()

        with patch.object(wft, "wait_for_completion") as mock_wait:
            wft.__exit__(None, None, None)

        mock_wait.assert_called_once()
        self.assertIsNone(WaitForTasks.get_current_context())

    @patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False)
    def test_nested_contexts_innermost_wins(self):
        """Nested WaitForTasks → get_current_context returns innermost."""
        outer = WaitForTasks(include_tasks={"outer_task"})
        outer._active = True
        inner = WaitForTasks(include_tasks={"inner_task"})
        inner._active = True

        outer.__enter__()
        inner.__enter__()
        self.assertIs(WaitForTasks.get_current_context(), inner)

        # Pop inner manually, patch wait
        with patch.object(inner, "wait_for_completion"):
            inner.__exit__(None, None, None)
        self.assertIs(WaitForTasks.get_current_context(), outer)

        # Pop outer
        with patch.object(outer, "wait_for_completion"):
            outer.__exit__(None, None, None)
        self.assertIsNone(WaitForTasks.get_current_context())

    def test_inactive_noop(self):
        """When CELERY_ACTIVE is false, context manager is a no-op."""
        wft = WaitForTasks()
        wft._active = False
        wft.__enter__()
        self.assertIsNone(WaitForTasks.get_current_context())
        wft.__exit__(None, None, None)


class TestWaitForTasksWaitForCompletion(SimpleTestCase):
    """Verify wait_for_completion blocks on dispatched results."""

    def setUp(self):
        _reset_context_vars()

    def tearDown(self):
        _reset_context_vars()

    def test_empty_results_returns_immediately(self):
        """No dispatched results → no-op."""
        wft = WaitForTasks()
        wft.wait_for_completion()

    def test_skips_blocking_when_fire_and_forget_active(self):
        """Inside a FireAndForget context, wait_for_completion is a no-op."""
        wft = WaitForTasks()
        r1 = MagicMock()
        wft.dispatched_results = [r1]

        ff = FireAndForget()
        ff._active = True
        unblock_tasks_context.get()["unblock_context_stack"].append(ff)

        wft.wait_for_completion()
        r1.get.assert_not_called()

        # Clean up
        unblock_tasks_context.get()["unblock_context_stack"].pop()

    def test_waits_for_all_results(self):
        """Calls result.get() for each dispatched result."""
        from contextlib import nullcontext
        wft = WaitForTasks()
        r1 = MagicMock()
        r2 = MagicMock()
        wft.dispatched_results = [r1, r2]

        with patch("lex.lex_app.celery_tasks.allow_join_result", return_value=nullcontext()):
            wft.wait_for_completion()

        r1.get.assert_called_once()
        r2.get.assert_called_once()
        self.assertEqual(wft.dispatched_results, [])


# ════════════════════════════════════════════════════════════════════════
#  is_in_fire_and_forget_context
# ════════════════════════════════════════════════════════════════════════

class TestIsInFireAndForgetContext(SimpleTestCase):
    """Verify the convenience function for checking FireAndForget state."""

    def setUp(self):
        _reset_context_vars()

    def tearDown(self):
        _reset_context_vars()

    def test_no_context_returns_false(self):
        """No active FireAndForget → False."""
        self.assertFalse(is_in_fire_and_forget_context())

    def test_with_context_no_task_name_returns_true(self):
        """Active FireAndForget + no task_name filter → True."""
        ff = FireAndForget()
        unblock_tasks_context.get()["unblock_context_stack"].append(ff)
        self.assertTrue(is_in_fire_and_forget_context())

    def test_with_context_matching_task_returns_true(self):
        """Active FireAndForget + task in force set → True."""
        ff = FireAndForget(force_tasks={"send_email"})
        unblock_tasks_context.get()["unblock_context_stack"].append(ff)
        self.assertTrue(is_in_fire_and_forget_context("send_email"))

    def test_with_context_non_matching_task_returns_false(self):
        """Active FireAndForget + task NOT in force set → False."""
        ff = FireAndForget(force_tasks={"send_email"})
        unblock_tasks_context.get()["unblock_context_stack"].append(ff)
        self.assertFalse(is_in_fire_and_forget_context("compute_nav"))


# ════════════════════════════════════════════════════════════════════════
#  register_task_with_context
# ════════════════════════════════════════════════════════════════════════

class TestRegisterTaskWithContext(SimpleTestCase):
    """Verify result registration with the active dispatch context."""

    def setUp(self):
        _reset_context_vars()

    def tearDown(self):
        _reset_context_vars()

    def test_no_context_returns_result(self):
        """No active context → returns result, does not register."""
        result = MagicMock()
        returned = register_task_with_context(result)
        self.assertIs(returned, result)

    def test_fire_and_forget_takes_priority(self):
        """FireAndForget context takes priority over WaitForTasks."""
        ff = FireAndForget()
        wft = WaitForTasks()
        unblock_tasks_context.get()["unblock_context_stack"].append(ff)
        tasks_context.get()["task_context_stack"].append(wft)

        result = MagicMock()
        register_task_with_context(result)

        self.assertIn(result, ff.dispatched_results)
        self.assertNotIn(result, wft.dispatched_results)

    def test_wait_for_tasks_receives_when_no_ff(self):
        """Only WaitForTasks context → result registered there."""
        wft = WaitForTasks()
        tasks_context.get()["task_context_stack"].append(wft)

        result = MagicMock()
        register_task_with_context(result)

        self.assertIn(result, wft.dispatched_results)


# ════════════════════════════════════════════════════════════════════════
#  respect_fire_and_forget decorator
# ════════════════════════════════════════════════════════════════════════

class TestRespectFireAndForget(SimpleTestCase):
    """Verify the decorator injects _force_async when inside FireAndForget."""

    def setUp(self):
        _reset_context_vars()

    def tearDown(self):
        _reset_context_vars()

    def test_no_context_no_injection(self):
        """No FireAndForget context → _force_async not injected."""
        captured_kwargs = {}

        @respect_fire_and_forget
        def my_func(**kwargs):
            captured_kwargs.update(kwargs)

        my_func(x=1)
        self.assertNotIn("_force_async", captured_kwargs)

    def test_context_active_injects_force_async(self):
        """Inside FireAndForget → _force_async=True injected."""
        captured_kwargs = {}

        @respect_fire_and_forget
        def my_func(**kwargs):
            captured_kwargs.update(kwargs)

        ff = FireAndForget()
        unblock_tasks_context.get()["unblock_context_stack"].append(ff)

        my_func(x=1)
        self.assertTrue(captured_kwargs.get("_force_async"))

    def test_excluded_task_no_injection(self):
        """Inside FireAndForget but task excluded → _force_async not injected."""
        captured_kwargs = {}

        @respect_fire_and_forget
        def compute_nav(**kwargs):
            captured_kwargs.update(kwargs)

        ff = FireAndForget(force_tasks={"send_email"})
        unblock_tasks_context.get()["unblock_context_stack"].append(ff)

        compute_nav(x=1)
        self.assertNotIn("_force_async", captured_kwargs)
