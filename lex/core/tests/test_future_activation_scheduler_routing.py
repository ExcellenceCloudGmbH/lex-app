"""Routing tests for future activation scheduling.

These tests explicitly verify how ``_schedule_future_activation`` chooses
between the local in-process scheduler and the Celery Beat scheduler based on
``CELERY_ACTIVE``. This keeps scheduler behavior deterministic regardless of
external ``.env`` values.
"""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from lex.core.services.bitemporal_signals import _schedule_future_activation


class FutureActivationRoutingTest(SimpleTestCase):
    """Verify scheduler branch selection for future activation."""

    @patch("lex.lex_app.celery_tasks.activate_history_version")
    @patch("lex.process_admin.utils.local_scheduler.LocalSchedulerBackend")
    @patch("os.getenv", return_value="false")
    def test_uses_local_scheduler_when_celery_inactive(
        self,
        _mock_getenv,
        mock_scheduler_cls,
        mock_activate_history_version,
    ):
        """When ``CELERY_ACTIVE`` is not true, use ``LocalSchedulerBackend``."""
        scheduler = mock_scheduler_cls.return_value

        now = timezone.now()
        history_instance = SimpleNamespace(valid_from=now + timedelta(hours=1), pk=123)
        meta_instance = MagicMock(meta_task_name=None, meta_task_status=None)
        main_model = SimpleNamespace(_meta=SimpleNamespace(app_label="lex_app"))

        _schedule_future_activation(
            meta_instance=meta_instance,
            history_instance=history_instance,
            main_model=main_model,
            model_name="schedtestmodel",
            now=now,
        )

        scheduler.schedule.assert_called_once()
        kwargs = scheduler.schedule.call_args.kwargs
        self.assertEqual(kwargs["run_at_time"], history_instance.valid_from)
        self.assertIs(kwargs["func"], mock_activate_history_version)
        self.assertEqual(
            kwargs["kwargs"],
            {
                "model_app_label": "lex_app",
                "model_name": "schedtestmodel",
                "history_id": 123,
            },
        )

        self.assertEqual(meta_instance.meta_task_status, "SCHEDULED")
        self.assertTrue(meta_instance.meta_task_name.startswith("local_thread_123_"))
        meta_instance.save.assert_called_once_with(
            update_fields=["meta_task_status", "meta_task_name"]
        )

    @patch("uuid.uuid4", return_value="fixed-uuid")
    @patch("django_celery_beat.models.PeriodicTask.objects.create")
    @patch("django_celery_beat.models.PeriodicTask.objects.filter")
    @patch("django_celery_beat.models.ClockedSchedule.objects.get_or_create")
    @patch("os.getenv", return_value="true")
    def test_uses_celery_beat_when_celery_active(
        self,
        _mock_getenv,
        mock_get_or_create,
        mock_periodic_filter,
        mock_periodic_create,
        _mock_uuid,
    ):
        """When ``CELERY_ACTIVE=true``, schedule a one-off ``PeriodicTask``."""
        now = timezone.now()
        history_instance = SimpleNamespace(valid_from=now + timedelta(hours=1), pk=321)
        meta_instance = MagicMock(meta_task_name="existing-task", meta_task_status=None)
        main_model = SimpleNamespace(_meta=SimpleNamespace(app_label="lex_app"))

        mock_get_or_create.return_value = (MagicMock(), True)

        _schedule_future_activation(
            meta_instance=meta_instance,
            history_instance=history_instance,
            main_model=main_model,
            model_name="schedtestmodel",
            now=now,
        )

        mock_periodic_filter.assert_called_once_with(name="existing-task")
        mock_periodic_filter.return_value.delete.assert_called_once()

        mock_get_or_create.assert_called_once_with(clocked_time=history_instance.valid_from)
        mock_periodic_create.assert_called_once()

        create_kwargs = mock_periodic_create.call_args.kwargs
        self.assertEqual(create_kwargs["task"], "activate_history_version")
        self.assertEqual(json.loads(create_kwargs["args"]), ["lex_app", "schedtestmodel", 321])
        self.assertIn("fixed-uuid", create_kwargs["name"])

        self.assertEqual(meta_instance.meta_task_status, "SCHEDULED")
        self.assertEqual(meta_instance.meta_task_name, create_kwargs["name"])
        meta_instance.save.assert_called_once_with(
            update_fields=["meta_task_name", "meta_task_status"]
        )
