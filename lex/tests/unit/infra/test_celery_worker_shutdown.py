import importlib
import os
import sys
import types
from unittest import TestCase
from unittest.mock import MagicMock, patch


def _install_celery_and_django_stubs():
    celery_module = types.ModuleType("celery")
    signals_module = types.ModuleType("celery.signals")
    celery_app_module = types.ModuleType("celery.app")
    celery_control_module = types.ModuleType("celery.app.control")
    django_module = types.ModuleType("django")
    django_apps_module = types.ModuleType("django.apps")

    class DummySignal:
        def connect(self, func=None, **kwargs):
            if func is None:
                def decorator(connected_func):
                    return connected_func

                return decorator
            return func

    class DummyCelery:
        def __init__(self, *args, **kwargs):
            self.conf = types.SimpleNamespace(
                broker_url="redis://localhost:6379/0",
                result_backend="redis://localhost:6379/0",
            )

        def config_from_object(self, *args, **kwargs):
            return None

        def autodiscover_tasks(self, *args, **kwargs):
            return None

    class DummyControl:
        def __init__(self, app):
            self.app = app

        def shutdown(self, destination=None):
            return None

    celery_module.Celery = DummyCelery
    signals_module.task_postrun = DummySignal()
    celery_control_module.Control = DummyControl
    django_apps_module.apps = types.SimpleNamespace(get_app_configs=lambda: [])

    sys.modules["celery"] = celery_module
    sys.modules["celery.signals"] = signals_module
    sys.modules["celery.app"] = celery_app_module
    sys.modules["celery.app.control"] = celery_control_module
    sys.modules["django"] = django_module
    sys.modules["django.apps"] = django_apps_module


def _load_celery_module():
    for module_name in ("lex.lex_app", "lex.lex_app.celery"):
        sys.modules.pop(module_name, None)

    _install_celery_and_django_stubs()
    return importlib.import_module("lex.lex_app.celery")


class CeleryWorkerShutdownTests(TestCase):
    def setUp(self):
        self.celery_module = _load_celery_module()

    def test_non_local_deployment_target_detection(self):
        with patch.dict(os.environ, {"DEPLOYMENT_TARGET": "prod"}, clear=False):
            self.assertTrue(self.celery_module._is_non_local_deployment_target())

    def test_local_deployment_target_disables_worker_shutdown(self):
        with patch.dict(os.environ, {"DEPLOYMENT_TARGET": "local"}, clear=False):
            self.assertFalse(self.celery_module._is_non_local_deployment_target())

    def test_postrun_shutdown_targets_only_current_worker(self):
        fake_task = types.SimpleNamespace(
            app=object(),
            request=types.SimpleNamespace(hostname="worker1@example"),
        )

        with patch.dict(os.environ, {"DEPLOYMENT_TARGET": "prod"}, clear=False):
            with patch.object(self.celery_module, "Control") as control_cls:
                control_instance = MagicMock()
                control_cls.return_value = control_instance

                self.celery_module.shutdown_worker_after_task_completion(
                    task_id="task-123",
                    task=fake_task,
                )

        control_cls.assert_called_once_with(app=fake_task.app)
        control_instance.shutdown.assert_called_once_with(
            destination=["worker1@example"]
        )

    def test_postrun_shutdown_is_skipped_in_local_environment(self):
        fake_task = types.SimpleNamespace(
            app=object(),
            request=types.SimpleNamespace(hostname="worker1@example"),
        )

        with patch.dict(os.environ, {"DEPLOYMENT_TARGET": "local"}, clear=False):
            with patch.object(self.celery_module, "Control") as control_cls:
                self.celery_module.shutdown_worker_after_task_completion(
                    task_id="task-123",
                    task=fake_task,
                )

        control_cls.assert_not_called()
