"""
Tests for the ``CalculationModel`` state machine.

Why this matters
----------------
``CalculationModel`` is the base class for every model whose rows can be
"calculated" — either synchronously or via Celery. When a row's
``is_calculated`` field transitions to ``IN_PROGRESS``, the framework:

1. Fires ``calculate_hook`` (a django-lifecycle hook on AFTER_CREATE /
   AFTER_UPDATE).
2. Resolves which user-defined method to call via ``lex_func()`` —
   either ``calculate()`` or ``update()``, plain or ``@lex_shared_task``.
3. Runs the method inside ``execute_calculation_sync()`` or dispatches to
   Celery via ``dispatch_calculation_task()``.
4. Transitions to ``SUCCESS`` on success, or ``ERROR`` (with
   ``calculation_error_message`` capture) on failure.

Every calculated model in every customer project inherits from
``CalculationModel``. Incorrect status transitions, lost error messages,
or re-entrant hook execution break the entire calculation pipeline, so
these are release-gating tests.

Methodology
-----------
Tests come in two flavours, matching the thing under test:

* **Pure-logic (status constants, ``lex_func()`` resolution)** — use
  ``SimpleTestCase`` with an unmanaged ``StubCalcModel`` (no DB).
* **State machine (``execute_calculation_sync``)** — use
  ``TransactionTestCase`` with a real managed model whose table is
  created via ``schema_editor`` in ``setUpClass``. The real
  ``transaction.atomic()`` block runs against the test DB; only true
  boundaries (Redis-backed ``CacheManager``/``ActiveCalculationStateStore``
  + channel layer via ``update_calculation_status``) are mocked.

See also
--------
- ``docs/features/processing/calculations.md``
- ``docs/reference/CalculationModel Internals.md``
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
#  Minimal concrete subclass for testing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class StubCalcModel(CalculationModel):
    """In-memory stub — overrides ``calculate`` with a configurable callable."""

    calculation_error_message = models.TextField(blank=True, default="")
    _calc_side_effect = None  # set by tests to control calculate() behaviour

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):
        if self._calc_side_effect:
            effect = self._calc_side_effect
            if callable(effect) and isinstance(effect, type) and issubclass(effect, BaseException):
                raise effect("forced error")
            if isinstance(effect, BaseException):
                raise effect
            if callable(effect):
                return effect()
        return None


class StubCalcModelWithUpdate(CalculationModel):
    """Stub that overrides ``update`` instead of ``calculate``."""

    class Meta:
        app_label = "lex_app"
        managed = False

    def update(self):
        return "updated"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1.  Status constants
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCalculationModelStatuses(SimpleTestCase):
    """Verify the five canonical status constants exist and are correct."""

    def test_in_progress_constant(self):
        """``IN_PROGRESS`` == ``'IN_PROGRESS'``."""
        self.assertEqual(CalculationModel.IN_PROGRESS, "IN_PROGRESS")

    def test_error_constant(self):
        """``ERROR`` == ``'ERROR'``."""
        self.assertEqual(CalculationModel.ERROR, "ERROR")

    def test_success_constant(self):
        """``SUCCESS`` == ``'SUCCESS'``."""
        self.assertEqual(CalculationModel.SUCCESS, "SUCCESS")

    def test_not_calculated_constant(self):
        """``NOT_CALCULATED`` == ``'NOT_CALCULATED'``."""
        self.assertEqual(CalculationModel.NOT_CALCULATED, "NOT_CALCULATED")

    def test_aborted_constant(self):
        """``ABORTED`` == ``'ABORTED'``."""
        self.assertEqual(CalculationModel.ABORTED, "ABORTED")

    def test_statuses_tuple_has_five_entries(self):
        """``STATUSES`` must contain exactly five (value, label) pairs."""
        self.assertEqual(len(CalculationModel.STATUSES), 5)

    def test_default_is_not_calculated(self):
        """A fresh instance defaults to ``NOT_CALCULATED``."""
        obj = StubCalcModel()
        self.assertEqual(obj.is_calculated, CalculationModel.NOT_CALCULATED)

    def test_statuses_are_pairs(self):
        """Each entry in ``STATUSES`` is a ``(value, label)`` pair where both elements match."""
        for status in CalculationModel.STATUSES:
            self.assertEqual(len(status), 2)
            self.assertEqual(status[0], status[1])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2.  lex_func() resolution
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLexFuncResolution(SimpleTestCase):
    """Prove ``lex_func()`` picks the correct user-overridden method."""

    def test_picks_calculate_when_overridden(self):
        """When the subclass overrides ``calculate``, ``lex_func`` returns it."""
        obj = StubCalcModel()
        func = obj.lex_func()
        # Should be StubCalcModel.calculate, not the base placeholder
        self.assertNotEqual(
            getattr(func, "__func__", None),
            CalculationModel.calculate,
        )

    def test_picks_update_when_only_update_overridden(self):
        """When only ``update`` is overridden, ``lex_func`` returns it."""
        obj = StubCalcModelWithUpdate()
        func = obj.lex_func()
        self.assertNotEqual(
            getattr(func, "__func__", None),
            CalculationModel.update,
        )

    def test_fallback_raises_not_implemented(self):
        """If neither is overridden, calling the result raises ``NotImplementedError``."""

        class BareModel(CalculationModel):
            class Meta:
                app_label = "lex_app"
                managed = False

        obj = BareModel()
        func = obj.lex_func()
        with self.assertRaises(NotImplementedError):
            func()

    def test_detects_task_wrapped_calculate(self):
        """A Celery task proxy (no ``__func__``) is treated as an override."""

        class TaskWrapped(CalculationModel):
            class Meta:
                app_label = "lex_app"
                managed = False

        # Simulate a @lex_shared_task descriptor — it replaces the method
        # with a proxy that does NOT have __func__.
        obj = TaskWrapped()
        fake_proxy = MagicMock(spec=[])  # no __func__ attribute
        obj.calculate = fake_proxy

        result = obj.lex_func()
        self.assertIs(result, fake_proxy)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3.  execute_calculation_sync  status transitions (real DB, real atomic block)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ExecSyncCalcModel(CalculationModel):
    """
    Real managed ``CalculationModel`` used by ``TestExecuteCalculationSync``.

    The table is built at class setup time via ``schema_editor`` and torn
    down at the end, mirroring the pattern used by
    ``test_calculation_audit_recovery`` — this lets the test exercise the
    real ``transaction.atomic()`` block inside ``execute_calculation_sync``
    without polluting the permanent schema.
    """

    name = models.CharField(max_length=100, default="")
    calculation_error_message = models.TextField(blank=True, default="")
    _calc_side_effect = None  # set per-instance by tests to control calculate()

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

    # Permission hooks — the base save() path checks these. Allow-all keeps
    # the focus on the state machine, not on authorization.
    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class TestExecuteCalculationSync(TransactionTestCase):
    """
    Exercise ``execute_calculation_sync`` against a real managed model and
    a real DB connection, so the inner ``transaction.atomic()`` block is
    actually hit. Only the Redis-backed status broadcast and cache cleanup
    are mocked — those are the *true* boundaries at this layer.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(ExecSyncCalcModel)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(ExecSyncCalcModel)
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        # Force the sync path regardless of whatever the surrounding env
        # has in CELERY_ACTIVE — this test is explicitly about the sync
        # branch of the state machine.
        env_patch = patch.dict(
            os.environ, {"CELERY_ACTIVE": "False"}, clear=False
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

        # Boundary mocks: update_calculation_status writes to Redis via the
        # channel layer + ActiveCalculationStateStore, and CacheManager
        # cleanup also hits Redis. Neither is what this test is about.
        # ``update_calculation_status`` is imported lazily inside
        # ``execute_calculation_sync`` so we patch the source module.
        status_patch = patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        )
        status_patch.start()
        self.addCleanup(status_patch.stop)

        cache_patch = patch("lex.core.models.CalculationModel.CacheManager")
        mock_cache = cache_patch.start()
        mock_cache.cleanup_calculation.return_value = MagicMock(success=True)
        self.addCleanup(cache_patch.stop)

    def _instance(self, side_effect=None, name="calc"):
        """
        Build a committed ``ExecSyncCalcModel`` row already in IN_PROGRESS,
        wired with the requested side effect for its ``calculate()`` call.
        """
        with OperationContext({}, f"calc-setup-{name}"):
            instance = ExecSyncCalcModel.objects.create(name=name)
        instance._calc_side_effect = side_effect
        instance.is_calculated = CalculationModel.IN_PROGRESS
        return instance

    def test_success_transition(self):
        """
        A ``calculate()`` that returns normally must leave the row with
        ``is_calculated == SUCCESS`` after ``execute_calculation_sync``
        runs — both on the in-memory instance and in the database row
        that the method itself saves via ``save(skip_hooks=True)``.
        """
        instance = self._instance()

        with OperationContext({}, "calc-success"), model_logging_context(instance):
            instance.execute_calculation_sync()

        self.assertEqual(instance.is_calculated, CalculationModel.SUCCESS)

        refreshed = ExecSyncCalcModel.objects.get(pk=instance.pk)
        self.assertEqual(refreshed.is_calculated, CalculationModel.SUCCESS)

    def test_error_transition_on_exception(self):
        """
        When ``calculate()`` raises, the method must re-raise and the row
        must end in ``is_calculated == ERROR`` — the persisted row too,
        so a second request sees the error state.
        """
        instance = self._instance(side_effect=ValueError("test"), name="err")

        with self.assertRaises(ValueError):
            with OperationContext({}, "calc-error"), model_logging_context(instance):
                instance.execute_calculation_sync()

        self.assertEqual(instance.is_calculated, CalculationModel.ERROR)
        refreshed = ExecSyncCalcModel.objects.get(pk=instance.pk)
        self.assertEqual(refreshed.is_calculated, CalculationModel.ERROR)

    def test_error_message_captured(self):
        """
        On failure, the original exception message must be written to
        ``calculation_error_message`` (the standard error-surfacing field)
        so the UI can show the user what broke.
        """
        instance = self._instance(side_effect=RuntimeError("bad math"), name="msg")

        with self.assertRaises(RuntimeError):
            with OperationContext({}, "calc-msg"), model_logging_context(instance):
                instance.execute_calculation_sync()

        self.assertIn("bad math", instance.calculation_error_message)
        refreshed = ExecSyncCalcModel.objects.get(pk=instance.pk)
        self.assertIn("bad math", refreshed.calculation_error_message)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.  should_use_celery
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestShouldUseCelery(SimpleTestCase):
    """Prove ``should_use_celery`` checks env var + broker availability."""

    @patch.dict("os.environ", {"CELERY_ACTIVE": "false"})
    def test_returns_false_when_celery_not_active(self):
        """``CELERY_ACTIVE=false`` → always sync."""
        obj = StubCalcModel()
        self.assertFalse(obj.should_use_celery())

    @patch.dict("os.environ", {"CELERY_ACTIVE": "true"})
    def test_returns_false_when_func_has_no_delay(self):
        """Even if CELERY_ACTIVE, no ``delay`` attr on func → sync."""
        obj = StubCalcModel()
        # StubCalcModel.calculate is a plain method, no .delay
        self.assertFalse(obj.should_use_celery())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5.  persist_error_state  +  build_exception_chain
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestPersistErrorState(SimpleTestCase):
    """Prove ``persist_error_state`` sets ERROR and saves each object."""

    def test_persists_single_object(self):
        """One object → saved with ``is_calculated = ERROR``."""
        obj = StubCalcModel()
        obj.save = MagicMock()

        persisted = CalculationModel.persist_error_state([obj])

        self.assertEqual(len(persisted), 1)
        self.assertEqual(obj.is_calculated, CalculationModel.ERROR)
        obj.save.assert_called_once()

    def test_skips_none(self):
        """``None`` entries in the list are silently skipped."""
        persisted = CalculationModel.persist_error_state([None, None])
        self.assertEqual(len(persisted), 0)

    def test_deduplicates_by_identity(self):
        """The same object appearing twice is persisted only once."""
        obj = StubCalcModel()
        obj.save = MagicMock()

        persisted = CalculationModel.persist_error_state([obj, obj])
        self.assertEqual(len(persisted), 1)
        self.assertEqual(obj.save.call_count, 1)


class TestBuildExceptionChain(SimpleTestCase):
    """Prove ``build_exception_chain`` collects calc objects and tracebacks."""

    def test_appends_current_obj(self):
        """``current_obj`` is always appended to the chain."""
        exc = ValueError("x")
        obj = StubCalcModel()

        calc_objs, details, traces = CalculationModel.build_exception_chain(exc, current_obj=obj)

        self.assertIn(obj, calc_objs)

    def test_without_current_obj(self):
        """Without ``current_obj``, chain still returns lists."""
        exc = RuntimeError("y")
        calc_objs, details, traces = CalculationModel.build_exception_chain(exc)
        self.assertIsInstance(calc_objs, list)
        self.assertIsInstance(details, list)
        self.assertIsInstance(traces, list)

    def test_no_artifacts_on_plain_exception(self):
        """A plain exception with no prior chain attributes produces empty lists."""
        exc = RuntimeError("simple error")
        calc_obj, details, trace = CalculationModel.build_exception_chain(exc)
        self.assertEqual(calc_obj, [])
        self.assertEqual(details, [])
        self.assertEqual(trace, [])

    def test_does_not_duplicate_current_obj(self):
        """If ``calc_obj`` already ends with ``current_obj``, don't append again."""
        exc = RuntimeError("error")
        obj = StubCalcModel()
        exc.calc_obj = [obj]
        calc_obj, _, _ = CalculationModel.build_exception_chain(exc, current_obj=obj)
        self.assertEqual(calc_obj.count(obj), 1)

    def test_preserves_existing_artifacts(self):
        """Pre-existing chain attributes are carried through unchanged."""
        exc = RuntimeError("error")
        exc.exception_details = ["Detail A"]
        exc.stack_trace = ["trace"]
        exc.calc_obj = ["obj1"]
        calc_obj, details, trace = CalculationModel.build_exception_chain(exc)
        self.assertEqual(details, ["Detail A"])
        self.assertEqual(trace, ["trace"])
        self.assertIn("obj1", calc_obj)

    def test_current_obj_none_is_noop(self):
        """Passing ``current_obj=None`` does not add ``None`` to the chain."""
        exc = RuntimeError("error")
        calc_obj, _, _ = CalculationModel.build_exception_chain(exc, current_obj=None)
        self.assertEqual(calc_obj, [])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6.  CalculationModelException
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCalculationModelException(SimpleTestCase):
    """Prove the custom exception carries structured error context."""

    def test_stores_calc_obj(self):
        """``calc_obj`` kwarg is normalised to a list."""
        exc = CalculationModelException(calc_obj="single")
        self.assertEqual(exc.calc_obj, ["single"])

    def test_stores_exception_details(self):
        """``exception_details`` kwarg is normalised to a list."""
        exc = CalculationModelException(exception_details="detail")
        self.assertEqual(exc.exception_details, ["detail"])

    def test_stores_stack_trace(self):
        """``stack_trace`` kwarg is normalised to a list."""
        exc = CalculationModelException(stack_trace="trace")
        self.assertEqual(exc.stack_trace, ["trace"])

    def test_preferred_detail_becomes_message(self):
        """The first non-None detail is used as the API error message."""
        exc = CalculationModelException(
            exception_details=[None, "real detail"]
        )
        self.assertIn("real detail", str(exc.detail))

    def test_fallback_to_args_message(self):
        """Positional string arg is used as message when no details provided."""
        exc = CalculationModelException("Direct message")
        self.assertIn("Direct message", str(exc))

    def test_none_kwargs_become_empty_lists(self):
        """Omitting all kwargs normalises to empty lists."""
        exc = CalculationModelException()
        self.assertEqual(exc.calc_obj, [])
        self.assertEqual(exc.exception_details, [])
        self.assertEqual(exc.stack_trace, [])

    def test_is_api_exception(self):
        """``CalculationModelException`` inherits from DRF ``APIException``."""
        from rest_framework.exceptions import APIException
        exc = CalculationModelException("test")
        self.assertIsInstance(exc, APIException)
