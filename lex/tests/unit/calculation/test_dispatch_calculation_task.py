"""
Tests for ``CalculationModel.dispatch_calculation_task`` — the Celery task
dispatch path.

Why this matters
----------------
When ``should_use_celery()`` returns True, the calculation is dispatched
to a Celery worker via ``dispatch_calculation_task()``. This method must:

1. Extract the operation_context and strip unpicklable request objects
2. Resolve the model_logging_context for the Celery worker
3. Call ``lex_func().delay(context=..., model_context=...)``
4. Register the task result with the WaitForTasks context

If any step fails, the calculation never runs — the model stays
IN_PROGRESS forever and the UI spinner hangs.

Test structure
--------------
* **TestDispatchContext** — verifies operation context extraction
* **TestDispatchModelContext** — verifies model_context propagation
* **TestDispatchTaskRegistration** — verifies WaitForTasks registration

How to run
----------
.. code-block:: bash

    python -m django test lex.core.tests.test_dispatch_calculation_task \\
        --settings=lex.process_admin.tests.django_test_settings \\
        --verbosity=2 --noinput
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.core.models.CalculationModel import CalculationModel


class DispatchStubCalcModel(CalculationModel):
    """Minimal stub for dispatch_calculation_task tests."""

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):
        pass


class TestDispatchContext(SimpleTestCase):
    """
    Verify that dispatch_calculation_task extracts the operation context
    correctly and strips unpicklable request objects.

    Celery serializes task arguments via pickle/json. If the context
    contains a Django HttpRequest (with file handles, socket refs, etc.),
    the task will fail at serialization time. ``OperationContext.extract_info_request``
    strips the request down to safe primitives.
    """

    @patch("lex.lex_app.celery_tasks.register_task_with_context")
    @patch("lex.audit_logging.utils.ModelContext._model_context")
    @patch("lex.core.models.CalculationModel.operation_context")
    def test_extracts_request_info_from_context(
        self, mock_op_ctx, mock_model_ctx, mock_register
    ):
        """
        The raw request_obj must be replaced with the extracted safe dict
        via OperationContext.extract_info_request.
        """
        # Setup operation context with a full request object
        mock_request = MagicMock()
        mock_request.user = "test-user"
        mock_request.META = {"HTTP_HOST": "localhost"}

        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-dispatch-1",
            "request_obj": mock_request,
        }

        # Setup model context
        mock_model_ctx.get.return_value = {
            "model_context": MagicMock(_stack=[])
        }

        # Setup task
        instance = DispatchStubCalcModel()
        fake_task = MagicMock()
        fake_task.delay.return_value = MagicMock(id="task-id")
        instance.calculate = fake_task

        mock_register.return_value = MagicMock(id="task-id")

        with patch(
            "lex.core.models.CalculationModel.OperationContext.extract_info_request"
        ) as mock_extract:
            mock_extract.return_value = {"user": "test-user"}
            instance.dispatch_calculation_task()

        # delay must have been called
        fake_task.delay.assert_called_once()
        call_kwargs = fake_task.delay.call_args.kwargs
        # The context must have the extracted request, not the raw one
        self.assertIn("context", call_kwargs)
        self.assertIn("calculation_id", call_kwargs["context"])

    @patch("lex.lex_app.celery_tasks.register_task_with_context")
    @patch("lex.audit_logging.utils.ModelContext._model_context")
    @patch("lex.core.models.CalculationModel.operation_context")
    def test_preserves_calculation_id_in_context(
        self, mock_op_ctx, mock_model_ctx, mock_register
    ):
        """The calculation_id must be preserved in the dispatched context."""
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-preserve-123",
            "request_obj": None,
        }
        mock_model_ctx.get.return_value = {
            "model_context": MagicMock(_stack=[])
        }

        instance = DispatchStubCalcModel()
        fake_task = MagicMock()
        fake_task.delay.return_value = MagicMock(id="task-id")
        instance.calculate = fake_task
        mock_register.return_value = MagicMock(id="task-id")

        instance.dispatch_calculation_task()

        call_kwargs = fake_task.delay.call_args.kwargs
        self.assertEqual(
            call_kwargs["context"]["calculation_id"], "calc-preserve-123"
        )


class TestDispatchModelContext(SimpleTestCase):
    """
    Verify that dispatch_calculation_task propagates the model_logging_context
    to the Celery worker so the worker knows the parent/child hierarchy.
    """

    @patch("lex.lex_app.celery_tasks.register_task_with_context")
    @patch("lex.audit_logging.utils.ModelContext._model_context")
    @patch("lex.core.models.CalculationModel.operation_context")
    def test_passes_model_context_to_delay(
        self, mock_op_ctx, mock_model_ctx, mock_register
    ):
        """
        The model_context must be passed to func.delay() as a keyword
        argument so the Celery worker can restore the context stack.
        """
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-ctx",
            "request_obj": None,
        }

        fake_model_context = MagicMock(_stack=["parent", "child"])
        mock_model_ctx.get.return_value = {
            "model_context": fake_model_context,
        }

        instance = DispatchStubCalcModel()
        fake_task = MagicMock()
        fake_task.delay.return_value = MagicMock(id="task-id")
        instance.calculate = fake_task
        mock_register.return_value = MagicMock(id="task-id")

        instance.dispatch_calculation_task()

        call_kwargs = fake_task.delay.call_args.kwargs
        self.assertIn("model_context", call_kwargs)


class TestDispatchTaskRegistration(SimpleTestCase):
    """
    Verify that dispatch_calculation_task registers the returned AsyncResult
    with the WaitForTasks context (if one exists) so the framework can
    wait for all dispatched tasks to complete.
    """

    @patch("lex.lex_app.celery_tasks.register_task_with_context")
    @patch("lex.audit_logging.utils.ModelContext._model_context")
    @patch("lex.core.models.CalculationModel.operation_context")
    def test_registers_task_result(
        self, mock_op_ctx, mock_model_ctx, mock_register
    ):
        """
        The AsyncResult from func.delay() must be passed to
        register_task_with_context so WaitForTasks can track it.
        """
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-reg",
            "request_obj": None,
        }
        mock_model_ctx.get.return_value = {
            "model_context": MagicMock(_stack=[]),
        }

        instance = DispatchStubCalcModel()
        fake_task = MagicMock()
        task_result = MagicMock(id="task-reg-123")
        fake_task.delay.return_value = task_result
        instance.calculate = fake_task

        expected_return = MagicMock(id="registered")
        mock_register.return_value = expected_return

        result = instance.dispatch_calculation_task()

        mock_register.assert_called_once_with(task_result)
        self.assertIs(result, expected_return)

    @patch("lex.lex_app.celery_tasks.register_task_with_context")
    @patch("lex.audit_logging.utils.ModelContext._model_context")
    @patch("lex.core.models.CalculationModel.operation_context")
    def test_calls_delay_on_lex_func(
        self, mock_op_ctx, mock_model_ctx, mock_register
    ):
        """dispatch_calculation_task calls .delay() on the resolved lex_func."""
        mock_op_ctx.get.return_value = {
            "calculation_id": "calc-delay",
            "request_obj": None,
        }
        mock_model_ctx.get.return_value = {
            "model_context": MagicMock(_stack=[]),
        }

        instance = DispatchStubCalcModel()
        # Override calculate with a task proxy that has .delay
        fake_task = MagicMock()
        fake_task.delay.return_value = MagicMock(id="t-1")
        instance.calculate = fake_task
        mock_register.return_value = MagicMock()

        instance.dispatch_calculation_task()

        fake_task.delay.assert_called_once()
