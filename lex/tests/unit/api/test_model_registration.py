import sys
from types import ModuleType
from unittest import TestCase
from unittest.mock import MagicMock, patch

from lex.process_admin.utils.model_registration import ModelRegistration


def _module(name, **attrs):
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class _FakeUser:
    @classmethod
    def add_to_class(cls, name, value):
        setattr(cls, name, value)


class _DummyMeta:
    abstract = False
    app_label = "test_app"


class DummyModel:
    _meta = _DummyMeta()


class ModelRegistrationTests(TestCase):
    @patch.object(ModelRegistration, "_register_standard_model", return_value="skipped")
    @patch.object(ModelRegistration, "_validate_model_definition")
    def test_register_models_passes_project_history_flag(
        self, mocked_validate, mocked_register_standard_model
    ):
        fake_process_admin_site = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "lex.process_admin.settings": _module(
                    "lex.process_admin.settings",
                    processAdminSite=fake_process_admin_site,
                ),
                "lex.core.models.Process": _module(
                    "lex.core.models.Process",
                    Process=type("Process", (), {}),
                ),
                "lex.core.models.HTMLReport": _module(
                    "lex.core.models.HTMLReport",
                    HTMLReport=type("HTMLReport", (), {}),
                ),
                "lex.core.models.CalculationModel": _module(
                    "lex.core.models.CalculationModel",
                    CalculationModel=type("CalculationModel", (), {}),
                ),
                "django.contrib.auth.models": _module(
                    "django.contrib.auth.models",
                    User=_FakeUser,
                ),
            },
        ):
            ModelRegistration.register_models(
                [DummyModel],
                untracked_models=[],
                history_tracking_enabled=False,
            )

        mocked_validate.assert_called_once_with(
            DummyModel,
            will_track_history=False,
        )
        mocked_register_standard_model.assert_called_once_with(
            DummyModel,
            [],
            False,
        )
