"""
Tests for ``CalculationModel.calculate_hook`` — the lifecycle hook that
orchestrates calculation dispatch.

Why this matters
----------------
``calculate_hook`` is the single entry point that fires whenever a
CalculationModel row transitions to ``IN_PROGRESS`` (via AFTER_CREATE or
AFTER_UPDATE). It decides:

1. Whether to dispatch to Celery or run synchronously
2. How to register child calculations in ActiveCalculationStateStore
3. How to handle the re-entrancy guard (``_calculation_hook_in_progress``)
4. What happens when the calculation fails — error state persistence,
   exception chain assembly, and CalculationModelException wrapping

Every calculated model in the framework depends on this 120-line method.
A bug here breaks ALL calculations for ALL customer projects.

Test structure
--------------
Split into two test classes mirroring the two runtime paths:

* **TestCalculateHookSync** — ``CELERY_ACTIVE=false`` or no ``.delay``
  → calls ``execute_calculation_sync`` directly.
* **TestCalculateHookCelery** — ``CELERY_ACTIVE=true`` with a task-wrapped
  ``calculate()`` → dispatches via ``dispatch_calculation_task``.
* **TestCalculateHookReentrancy** — verifies the re-entrancy guard.
* **TestCalculateHookErrorHandling** — verifies error state persistence,
  CalculationModelException wrapping, and cache cleanup on failure.

How to run
----------
.. code-block:: bash

    python -m django test lex.core.tests.test_calculate_hook \\
        --settings=lex.process_admin.tests.django_test_settings \\
        --verbosity=2 --noinput
"""

import os
from unittest.mock import MagicMock, patch

from django.db import connection, models
from django.test import SimpleTestCase, TransactionTestCase
from lex.api.utils import OperationContext
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.core.models.CalculationModel import CalculationModel, CalculationModelException
from lex.core.models.LexModel import PermissionResult


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Concrete CalculationModel with real DB table for hook tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class HookCalcModel(CalculationModel):
    """
    Real managed model for ``calculate_hook`` integration tests.

    Table is created/destroyed via ``schema_editor`` in setUpClass/tearDownClass
    so it does not pollute migrations. The ``calculate()`` method is configurable
    via ``_calc_side_effect`` to simulate success, failure, or custom behaviour.
    """

    name = models.CharField(max_length=100, default="")
    calculation_error_message = models.TextField(blank=True, default="")
    _calc_side_effect = None

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        if self._calc_side_effect:
            effect = self._calc_side_effect
            if isinstance(effect, type) and issubclass(effect, BaseException):
                raise effect("forced error")
            if isinstance(effect, BaseException):
                raise effect
            if callable(effect):
                return effect()
        return None

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Synchronous path — CELERY_ACTIVE=false
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCalculateHookSync(TransactionTestCase):
    """
    When ``CELERY_ACTIVE=false`` (or the calculate method has no ``.delay``),
    ``calculate_hook`` must call ``execute_calculation_sync`` directly.
    After a successful calculation, the instance must be ``SUCCESS``.
    After a failed calculation, it must be ``ERROR`` and raise
    ``CalculationModelException``.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(HookCalcModel)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(HookCalcModel)
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        # Force synchronous path
        self._env_patch = patch.dict(
            os.environ, {"CELERY_ACTIVE": "False"}, clear=False
        )
        self._env_patch.start()

        # Mock external boundaries: WebSocket broadcast, cache, state store
        self._status_patch = patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        )
        self._mock_status = self._status_patch.start()

        self._cache_patch = patch("lex.core.models.CalculationModel.CacheManager")
        self._mock_cache = self._cache_patch.start()
        self._mock_cache.cleanup_calculation.return_value = MagicMock(success=True)

        self._state_store_patch = patch(
            "lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore"
        )
        self._mock_state_store = self._state_store_patch.start()
        self._mock_state_store.get_calculation_id.return_value = None

    def tearDown(self):
        self._state_store_patch.stop()
        self._cache_patch.stop()
        self._status_patch.stop()
        self._env_patch.stop()
        super().tearDown()

    def _create_instance(self, name="test", side_effect=None):
        """Create a committed HookCalcModel with is_calculated=NOT_CALCULATED."""
        with OperationContext({}, f"setup-{name}"):
            instance = HookCalcModel.objects.create(name=name)
        instance._calc_side_effect = side_effect
        return instance

    def test_sync_success_transitions_to_success(self):
        """
        When calculate() succeeds, calculate_hook via execute_calculation_sync
        must set is_calculated=SUCCESS and persist it to the DB.
        """
        instance = self._create_instance(name="sync-ok")

        with OperationContext({}, "calc-sync-ok"), model_logging_context(instance):
            instance.is_calculated = CalculationModel.IN_PROGRESS
            instance.save(skip_hooks=True)
            instance.calculate_hook()

        self.assertEqual(instance.is_calculated, CalculationModel.SUCCESS)
        refreshed = HookCalcModel.objects.get(pk=instance.pk)
        self.assertEqual(refreshed.is_calculated, CalculationModel.SUCCESS)

    def test_sync_failure_transitions_to_error(self):
        """
        When calculate() raises, calculate_hook must set is_calculated=ERROR,
        persist the error state, and raise CalculationModelException.
        """
        instance = self._create_instance(
            name="sync-err", side_effect=ValueError("calc exploded")
        )

        with self.assertRaises(CalculationModelException) as ctx:
            with OperationContext({}, "calc-sync-err"), model_logging_context(instance):
                instance.is_calculated = CalculationModel.IN_PROGRESS
                instance.save(skip_hooks=True)
                instance.calculate_hook()

        # Instance should be ERROR
        self.assertEqual(instance.is_calculated, CalculationModel.ERROR)

        # The CalculationModelException must carry the exception details
        exc = ctx.exception
        self.assertTrue(len(exc.exception_details) > 0)
        self.assertIn(instance, exc.calc_obj)

    def test_sync_failure_persists_error_in_db(self):
        """
        After calculate_hook catches an error, the DB row must reflect ERROR
        so a subsequent request sees the error state.
        """
        instance = self._create_instance(
            name="sync-persist", side_effect=RuntimeError("broken")
        )

        with self.assertRaises(CalculationModelException):
            with OperationContext({}, "calc-persist"), model_logging_context(instance):
                instance.is_calculated = CalculationModel.IN_PROGRESS
                instance.save(skip_hooks=True)
                instance.calculate_hook()

        refreshed = HookCalcModel.objects.get(pk=instance.pk)
        self.assertEqual(refreshed.is_calculated, CalculationModel.ERROR)

    def test_sync_registers_in_active_calculation_state_store(self):
        """
        calculate_hook must register the instance in ActiveCalculationStateStore
        before calling execute_calculation_sync, so WebSocket clients see the
        spinner immediately.
        """
        instance = self._create_instance(name="sync-register")

        with OperationContext({}, "calc-register"), model_logging_context(instance):
            instance.is_calculated = CalculationModel.IN_PROGRESS
            instance.save(skip_hooks=True)
            instance.calculate_hook()

        # ActiveCalculationStateStore.mark_in_progress should have been called
        self._mock_state_store.mark_in_progress.assert_called()
        call_kwargs = self._mock_state_store.mark_in_progress.call_args.kwargs
        self.assertEqual(
            call_kwargs["record_id"],
            f"{instance._meta.model_name}_{instance.pk}",
        )

    def test_sync_broadcasts_in_progress_status(self):
        """
        calculate_hook must broadcast IN_PROGRESS status via
        update_calculation_status so all WebSocket subscribers see it.
        """
        instance = self._create_instance(name="sync-broadcast")

        with OperationContext({}, "calc-broadcast"), model_logging_context(instance):
            instance.is_calculated = CalculationModel.IN_PROGRESS
            instance.save(skip_hooks=True)
            instance.calculate_hook()

        # update_calculation_status should be called at least once
        # (initially with IN_PROGRESS, then with SUCCESS/ERROR from
        # execute_calculation_sync)
        self.assertTrue(self._mock_status.call_count >= 1)

    def test_sync_cleans_up_reentrancy_guard(self):
        """
        After calculate_hook completes (success or failure), the
        _calculation_hook_in_progress attribute must be removed so
        future saves don't skip the hook.
        """
        instance = self._create_instance(name="sync-guard")

        with OperationContext({}, "calc-guard"), model_logging_context(instance):
            instance.is_calculated = CalculationModel.IN_PROGRESS
            instance.save(skip_hooks=True)
            instance.calculate_hook()

        self.assertFalse(hasattr(instance, "_calculation_hook_in_progress"))

    def test_sync_failure_cleans_up_reentrancy_guard(self):
        """
        Even on failure, the re-entrancy guard must be cleaned up in the
        finally block.
        """
        instance = self._create_instance(
            name="sync-guard-err", side_effect=ValueError("boom")
        )

        with self.assertRaises(CalculationModelException):
            with OperationContext({}, "calc-guard-err"), model_logging_context(instance):
                instance.is_calculated = CalculationModel.IN_PROGRESS
                instance.save(skip_hooks=True)
                instance.calculate_hook()

        self.assertFalse(hasattr(instance, "_calculation_hook_in_progress"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Re-entrancy guard
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCalculateHookReentrancy(SimpleTestCase):
    """
    When ``_calculation_hook_in_progress`` is already ``True`` on an instance,
    ``calculate_hook`` must skip execution entirely (no infinite recursion).

    This happens when a FileField.save() or other internal path calls
    self.save() while calculate_hook is already running.
    """

    def test_skips_when_reentrancy_guard_is_set(self):
        """
        If _calculation_hook_in_progress is True, calculate_hook returns
        immediately without calling execute_calculation_sync.
        """
        instance = HookCalcModel()
        instance._calculation_hook_in_progress = True
        instance.is_calculated = CalculationModel.IN_PROGRESS

        # Patch execute_calculation_sync to verify it's NOT called
        with patch.object(instance, "execute_calculation_sync") as mock_exec:
            instance.calculate_hook()
            mock_exec.assert_not_called()

    def test_guard_is_set_during_execution(self):
        """
        During calculate_hook execution, _calculation_hook_in_progress must
        be True so any re-entrant save() calls skip the hook.
        """
        instance = HookCalcModel()
        instance.is_calculated = CalculationModel.IN_PROGRESS
        instance.pk = 99
        guard_was_set = []

        def capture_guard():
            guard_was_set.append(
                getattr(instance, "_calculation_hook_in_progress", False)
            )

        with patch.dict(os.environ, {"CELERY_ACTIVE": "False"}):
            with patch(
                "lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore"
            ) as mock_store:
                mock_store.get_calculation_id.return_value = None
                with patch(
                    "lex.core.signals.CalculationSignals.update_calculation_status"
                ):
                    # Mock execute_calculation_sync to capture guard state
                    # without hitting the DB
                    with patch.object(
                        instance, "execute_calculation_sync",
                        side_effect=capture_guard
                    ):
                        instance.calculate_hook()

        self.assertTrue(guard_was_set[0], "Guard must be True during execution")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Celery path — CELERY_ACTIVE=true
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCalculateHookCelery(SimpleTestCase):
    """
    When ``CELERY_ACTIVE=true`` and the lex_func() has a ``.delay`` attribute,
    ``calculate_hook`` should dispatch to Celery rather than running synchronously.

    The Celery dispatch path has three sub-branches:
    1. Explicit async context (FireAndForget/WaitForTasks) → dispatch_calculation_task
    2. Inside a Celery worker already → execute_calculation_sync (avoid nesting)
    3. No explicit context → wrap in WaitForTasks and dispatch
    """

    def _make_instance(self):
        """Create a minimal HookCalcModel with task-wrapped calculate."""
        instance = HookCalcModel()
        instance.is_calculated = CalculationModel.IN_PROGRESS
        instance.pk = 42
        instance._meta = HookCalcModel._meta

        # Simulate a Celery task-wrapped calculate (has .delay)
        fake_task = MagicMock()
        fake_task.delay = MagicMock(return_value=MagicMock(id="task-123"))
        instance.calculate = fake_task
        return instance

    @patch.dict(os.environ, {"CELERY_ACTIVE": "true"})
    @patch("lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore")
    @patch("lex.core.signals.CalculationSignals.update_calculation_status")
    def test_celery_dispatches_with_explicit_async_context(
        self, mock_status, mock_store
    ):
        """
        When FireAndForget or WaitForTasks context exists, calculate_hook
        calls dispatch_calculation_task instead of execute_calculation_sync.
        """
        mock_store.get_calculation_id.return_value = None
        instance = self._make_instance()

        with patch.object(instance, "should_use_celery", return_value=True):
            with patch.object(instance, "dispatch_calculation_task") as mock_dispatch:
                mock_dispatch.return_value = MagicMock(id="task-abc")
                # Simulate explicit async context
                with patch(
                    "lex.lex_app.celery_tasks.FireAndForget"
                ) as ff:
                    ff.get_current_context.return_value = MagicMock()
                    with patch(
                        "lex.lex_app.celery_tasks.WaitForTasks"
                    ):
                        instance.calculate_hook()

                mock_dispatch.assert_called_once()

    @patch.dict(os.environ, {"CELERY_ACTIVE": "true"})
    @patch("lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore")
    @patch("lex.core.signals.CalculationSignals.update_calculation_status")
    def test_celery_dispatches_inside_worker(self, mock_status, mock_store):
        """
        When already inside a Celery worker process and no explicit async
        context exists, calculate_hook still DISPATCHES the nested calc
        (opening its own WaitForTasks) so nested calculations parallelise
        across workers instead of collapsing inline onto the parent's
        worker slot. Abort-safety for the dispatched task comes from the
        cluster cancel marker checked at task start, not from inlining
        (clusters 7q / 8ad).
        """
        mock_store.get_calculation_id.return_value = None
        instance = self._make_instance()

        with patch.object(instance, "should_use_celery", return_value=True):
            with patch.object(instance, "execute_calculation_sync") as mock_sync:
                with patch.object(
                    instance, "dispatch_calculation_task"
                ) as mock_dispatch:
                    mock_dispatch.return_value = MagicMock(id="task-nested")
                    with patch(
                        "lex.lex_app.celery_tasks.FireAndForget"
                    ) as ff:
                        ff.get_current_context.return_value = None
                        with patch(
                            "lex.lex_app.celery_tasks.WaitForTasks"
                        ) as wft:
                            wft.get_current_context.return_value = None
                            wft_instance = MagicMock()
                            wft.return_value = wft_instance
                            wft_instance.__enter__ = MagicMock(
                                return_value=wft_instance
                            )
                            wft_instance.__exit__ = MagicMock(return_value=False)
                            with patch(
                                "lex.lex_app.celery_tasks.is_celery_worker_process",
                                return_value=True,
                            ):
                                instance.calculate_hook()

                mock_dispatch.assert_called_once()
                mock_sync.assert_not_called()

    @patch.dict(os.environ, {"CELERY_ACTIVE": "true"})
    @patch("lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore")
    @patch("lex.core.signals.CalculationSignals.update_calculation_status")
    def test_celery_wraps_in_wait_for_tasks_when_no_context(
        self, mock_status, mock_store
    ):
        """
        When CELERY_ACTIVE=true, not inside a worker, and no explicit async
        context exists, calculate_hook wraps dispatch in WaitForTasks().
        """
        mock_store.get_calculation_id.return_value = None
        instance = self._make_instance()

        with patch.object(instance, "should_use_celery", return_value=True):
            with patch.object(instance, "dispatch_calculation_task") as mock_dispatch:
                mock_dispatch.return_value = MagicMock(id="task-xyz")
                with patch(
                    "lex.lex_app.celery_tasks.FireAndForget"
                ) as ff:
                    ff.get_current_context.return_value = None
                    with patch(
                        "lex.lex_app.celery_tasks.WaitForTasks"
                    ) as wft:
                        wft.get_current_context.return_value = None
                        # WaitForTasks context manager
                        wft_instance = MagicMock()
                        wft.return_value = wft_instance
                        wft_instance.__enter__ = MagicMock(return_value=wft_instance)
                        wft_instance.__exit__ = MagicMock(return_value=False)
                        with patch(
                            "lex.lex_app.celery_tasks.is_celery_worker_process",
                            return_value=False,
                        ):
                            instance.calculate_hook()

                # WaitForTasks was used as context manager
                wft_instance.__enter__.assert_called_once()
                mock_dispatch.assert_called_once()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Error handling — exception chain, state persistence, cache cleanup
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCalculateHookErrorHandling(TransactionTestCase):
    """
    Verify the error handling path in calculate_hook:

    * ``is_calculated`` is set to ERROR on the instance and in the DB
    * ``persist_error_state`` is called for child calc objects
    * ``build_exception_chain`` assembles the error context
    * The raised ``CalculationModelException`` carries the full chain
    * Cache cleanup is attempted even on failure
    * The re-entrancy guard is always cleaned up
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(HookCalcModel)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(HookCalcModel)
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        self._env_patch = patch.dict(
            os.environ, {"CELERY_ACTIVE": "False"}, clear=False
        )
        self._env_patch.start()

        self._status_patch = patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        )
        self._mock_status = self._status_patch.start()

        self._cache_patch = patch("lex.core.models.CalculationModel.CacheManager")
        self._mock_cache = self._cache_patch.start()
        self._mock_cache.cleanup_calculation.return_value = MagicMock(success=True)

        self._state_store_patch = patch(
            "lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore"
        )
        self._mock_state_store = self._state_store_patch.start()
        self._mock_state_store.get_calculation_id.return_value = None

    def tearDown(self):
        self._state_store_patch.stop()
        self._cache_patch.stop()
        self._status_patch.stop()
        self._env_patch.stop()
        super().tearDown()

    def _create_instance(self, name="test", side_effect=None):
        with OperationContext({}, f"setup-{name}"):
            instance = HookCalcModel.objects.create(name=name)
        instance._calc_side_effect = side_effect
        return instance

    def test_error_wraps_in_calculation_model_exception(self):
        """
        Any exception from calculate() is wrapped in CalculationModelException
        with the original exception as __cause__.
        """
        original = ValueError("original error")
        instance = self._create_instance(name="wrap", side_effect=original)

        with self.assertRaises(CalculationModelException) as ctx:
            with OperationContext({}, "calc-wrap"), model_logging_context(instance):
                instance.is_calculated = CalculationModel.IN_PROGRESS
                instance.save(skip_hooks=True)
                instance.calculate_hook()

        exc = ctx.exception
        self.assertIsInstance(exc.__cause__, ValueError)
        self.assertIn("original error", str(exc.__cause__))

    def test_error_includes_self_in_calc_obj(self):
        """
        The CalculationModelException.calc_obj must include the current
        instance so persist_error_state can set it to ERROR.
        """
        instance = self._create_instance(
            name="self-in-chain", side_effect=RuntimeError("err")
        )

        with self.assertRaises(CalculationModelException) as ctx:
            with OperationContext({}, "calc-chain"), model_logging_context(instance):
                instance.is_calculated = CalculationModel.IN_PROGRESS
                instance.save(skip_hooks=True)
                instance.calculate_hook()

        self.assertIn(instance, ctx.exception.calc_obj)

    def test_error_saves_to_db_as_error(self):
        """
        After calculate_hook fails, the DB row must be ERROR — not still
        IN_PROGRESS, which would cause the spinner to hang forever.
        """
        instance = self._create_instance(
            name="db-error", side_effect=TypeError("type error")
        )

        with self.assertRaises(CalculationModelException):
            with OperationContext({}, "calc-db-err"), model_logging_context(instance):
                instance.is_calculated = CalculationModel.IN_PROGRESS
                instance.save(skip_hooks=True)
                instance.calculate_hook()

        refreshed = HookCalcModel.objects.get(pk=instance.pk)
        self.assertEqual(refreshed.is_calculated, CalculationModel.ERROR)

    def test_error_stores_pending_terminal_audit(self):
        """
        On failure, calculate_hook sets ``_pending_terminal_audit`` with the
        failure details for downstream audit processing.
        """
        instance = self._create_instance(
            name="audit-pending", side_effect=RuntimeError("audit test")
        )

        with self.assertRaises(CalculationModelException):
            with OperationContext({}, "calc-audit"), model_logging_context(instance):
                instance.is_calculated = CalculationModel.IN_PROGRESS
                instance.save(skip_hooks=True)
                instance.calculate_hook()

        self.assertTrue(hasattr(instance, "_pending_terminal_audit"))
        audit = instance._pending_terminal_audit
        self.assertEqual(audit["audit_status"], "failure")
        self.assertIn("error_message", audit)
        self.assertIn("stack_trace", audit)

    def test_error_attempts_cache_cleanup_for_root(self):
        """
        Even on failure, calculate_hook attempts cache cleanup for the root
        process so orphaned cache keys don't accumulate.
        """
        instance = self._create_instance(
            name="cache-cleanup", side_effect=RuntimeError("cleanup test")
        )

        # The error path in calculate_hook calls ContextResolver.resolve()
        # for cache cleanup. Mock it to simulate a root process.
        with patch("lex.core.models.CalculationModel.ContextResolver") as mock_cr:
            mock_cr.resolve.return_value = MagicMock(
                root_record=f"hookcalcmodel_{instance.pk}",
                current_record=f"hookcalcmodel_{instance.pk}",
                parent_record=None,
                calculation_id="calc-cleanup",
            )
            with self.assertRaises(CalculationModelException):
                with OperationContext({}, "calc-cleanup"), model_logging_context(instance):
                    instance.is_calculated = CalculationModel.IN_PROGRESS
                    instance.save(skip_hooks=True)
                    instance.calculate_hook()

        # CacheManager.cleanup_calculation should have been called
        self.assertTrue(self._mock_cache.cleanup_calculation.called)
