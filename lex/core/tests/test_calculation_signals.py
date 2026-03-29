from unittest.mock import patch

from django.test import SimpleTestCase

from lex.api.utils import operation_context
from lex.core.models.CalculationModel import CalculationModel, CalculationModelException
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.core.signals.CalculationSignals import update_calculation_status


class DummyCalculationModel(CalculationModel):
    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):
        return None


class DummyChannelLayer:
    def __init__(self):
        self.messages = []

    def group_send(self, group, message):
        self.messages.append((group, message))


class CalculationStatusSignalTests(SimpleTestCase):
    def setUp(self):
        self._state_map = {}
        self._load_state_map_patcher = patch.object(
            ActiveCalculationStateStore,
            "_load_state_map",
            side_effect=self._load_state_map,
        )
        self._save_state_map_patcher = patch.object(
            ActiveCalculationStateStore,
            "_save_state_map",
            side_effect=self._save_state_map,
        )
        self._load_state_map_patcher.start()
        self._save_state_map_patcher.start()
        self._set_context("")

    def tearDown(self):
        self._load_state_map_patcher.stop()
        self._save_state_map_patcher.stop()
        self._set_context("")

    def _load_state_map(self):
        return dict(self._state_map)

    def _save_state_map(self, state_map):
        self._state_map = dict(state_map)

    @staticmethod
    def _set_context(calculation_id):
        operation_context.set(
            {
                "operation_id": "test-op",
                "request_obj": {},
                "calculation_id": calculation_id,
                "audit_log_temp": None,
            }
        )

    @staticmethod
    def _build_instance(pk):
        instance = DummyCalculationModel(id=pk)
        instance.is_calculated = CalculationModel.IN_PROGRESS
        return instance

    def test_in_progress_is_not_rebroadcast_with_same_calculation_id(self):
        self._set_context("calc-1")
        instance = self._build_instance(1)
        channel_layer = DummyChannelLayer()

        with patch(
            "lex.core.signals.CalculationSignals.get_channel_layer",
            return_value=channel_layer,
        ), patch(
            "lex.core.signals.CalculationSignals.async_to_sync",
            side_effect=lambda fn: fn,
        ):
            update_calculation_status(instance)
            update_calculation_status(instance)

        self.assertEqual(len(channel_layer.messages), 1)
        event = channel_layer.messages[0][1]
        self.assertEqual(event["type"], "calculation_in_progress")
        self.assertEqual(event["payload"]["calculation_id"], "calc-1")

    def test_in_progress_rebroadcasts_when_calculation_id_becomes_available(self):
        instance = self._build_instance(2)
        channel_layer = DummyChannelLayer()

        with patch(
            "lex.core.signals.CalculationSignals.get_channel_layer",
            return_value=channel_layer,
        ), patch(
            "lex.core.signals.CalculationSignals.async_to_sync",
            side_effect=lambda fn: fn,
        ):
            self._set_context("")
            update_calculation_status(instance)

            self._set_context("calc-2")
            update_calculation_status(instance)

        self.assertEqual(len(channel_layer.messages), 2)
        first_event = channel_layer.messages[0][1]
        second_event = channel_layer.messages[1][1]
        self.assertNotIn("calculation_id", first_event["payload"])
        self.assertEqual(second_event["payload"]["calculation_id"], "calc-2")

    def test_in_progress_without_context_keeps_existing_calculation_id(self):
        instance = self._build_instance(6)
        channel_layer = DummyChannelLayer()

        with patch(
            "lex.core.signals.CalculationSignals.get_channel_layer",
            return_value=channel_layer,
        ), patch(
            "lex.core.signals.CalculationSignals.async_to_sync",
            side_effect=lambda fn: fn,
        ):
            self._set_context("calc-6")
            update_calculation_status(instance)

            self._set_context("")
            update_calculation_status(instance)

        self.assertEqual(len(channel_layer.messages), 1)
        record_id = f"{instance._meta.model_name}_{instance.id}"
        self.assertEqual(
            ActiveCalculationStateStore.get_entry(record_id).get("calculation_id"),
            "calc-6",
        )

    def test_calculate_hook_emits_in_progress_before_sync_execution(self):
        instance = self._build_instance(3)

        with patch.object(
            DummyCalculationModel, "should_use_celery", return_value=False
        ), patch.object(
            DummyCalculationModel, "execute_calculation_sync"
        ) as execute_sync_mock, patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        ) as update_status_mock:
            DummyCalculationModel.calculate_hook(instance)

        update_status_mock.assert_called_once_with(instance)
        execute_sync_mock.assert_called_once_with()

    def test_calculate_hook_does_not_rewrap_existing_calculation_exception(self):
        instance = self._build_instance(4)
        child_instance = self._build_instance(40)
        existing_exception = CalculationModelException(
            calc_obj=[child_instance],
            exception_details=["inner-failure"],
            stack_trace=["stack"],
        )

        with patch.object(
            DummyCalculationModel, "should_use_celery", return_value=False
        ), patch.object(
            DummyCalculationModel, "execute_calculation_sync", side_effect=existing_exception
        ), patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        ), patch.object(
            DummyCalculationModel, "save"
        ) as save_mock:
            with self.assertRaises(CalculationModelException) as raised:
                DummyCalculationModel.calculate_hook(instance)

        exc = raised.exception
        # The parent wraps the child chain: calc_obj accumulates [child, parent]
        self.assertEqual(exc.calc_obj, [child_instance, instance])
        self.assertEqual(exc.exception_details[0], "inner-failure")
        # Parent must persist its own ERROR state
        save_mock.assert_called_once_with(skip_hooks=True)
        self.assertEqual(instance.is_calculated, CalculationModel.ERROR)

    def test_calculate_hook_skips_reentrant_invocation(self):
        instance = self._build_instance(5)

        def simulate_internal_save_reentry():
            DummyCalculationModel.calculate_hook(instance)

        with patch.object(
            DummyCalculationModel, "should_use_celery", return_value=False
        ), patch.object(
            DummyCalculationModel, "execute_calculation_sync", side_effect=simulate_internal_save_reentry
        ) as execute_sync_mock, patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        ) as update_status_mock:
            DummyCalculationModel.calculate_hook(instance)

        # Outer invocation executes exactly once; nested invocation returns early.
        self.assertEqual(execute_sync_mock.call_count, 1)
        update_status_mock.assert_called_once_with(instance)


class CalculationExceptionUtilityTests(SimpleTestCase):
    def test_calculation_model_exception_string_uses_actual_detail(self):
        exception = CalculationModelException(
            exception_details=["SharePoint Server cannot handle requests at the moment."]
        )

        self.assertEqual(
            str(exception),
            "SharePoint Server cannot handle requests at the moment.",
        )

    def test_build_exception_chain_recovers_nested_calculation_exception(self):
        instance = DummyCalculationModel(id=41)
        child_instance = DummyCalculationModel(id=410)
        nested_exception = CalculationModelException(
            calc_obj=[child_instance],
            exception_details=["inner-failure"],
            stack_trace=["inner-trace"],
        )

        try:
            try:
                raise nested_exception
            except CalculationModelException:
                raise RuntimeError("outer-wrapper")
        except RuntimeError as wrapped_exception:
            calc_obj, exception_details, stack_trace = (
                DummyCalculationModel.build_exception_chain(
                    wrapped_exception,
                    current_obj=instance,
                )
            )

        self.assertEqual(calc_obj, [child_instance, instance])
        self.assertEqual(exception_details, ["inner-failure"])
        self.assertEqual(stack_trace, ["inner-trace"])
