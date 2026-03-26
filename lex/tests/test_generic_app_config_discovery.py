import importlib
import os
import sys
import types
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from lex.core.models.HTMLReport import HTMLReport
from lex.utilities.config.generic_app_config import GenericAppConfig


class GenericAppConfigDiscoveryTests(TestCase):
    def setUp(self):
        app_module = importlib.import_module("lex.utilities.config")
        self.config = GenericAppConfig("utilities", app_module)
        self.config.discovered_models = {}

    @patch("lex.utilities.config.generic_app_config.importlib.import_module")
    def test_load_models_discovers_html_reports_defined_in_module(self, mocked_import):
        module = types.ModuleType("external_app.reports")

        base_report = type("BaseReport", (HTMLReport,), {"__module__": module.__name__})
        concrete_report = type(
            "ConcreteReport",
            (base_report,),
            {"__module__": module.__name__},
        )
        local_html_report = type(
            "HTMLReport",
            (HTMLReport,),
            {"__module__": module.__name__},
        )
        imported_report = type(
            "ImportedReport",
            (HTMLReport,),
            {"__module__": "another_app.reports"},
        )

        module.BaseReport = base_report
        module.ConcreteReport = concrete_report
        module.LocalHTMLReport = local_html_report
        module.ImportedReport = imported_report
        module.HTMLReport = HTMLReport
        mocked_import.return_value = module

        self.config.load_models_from_module(module.__name__)

        self.assertIn("BaseReport", self.config.discovered_models)
        self.assertIn("ConcreteReport", self.config.discovered_models)
        self.assertNotIn("HTMLReport", self.config.discovered_models)
        self.assertNotIn("LocalHTMLReport", self.config.discovered_models)
        self.assertNotIn("ImportedReport", self.config.discovered_models)

    def test_process_module_extracts_structure_from_structure_file(self):
        self.config.model_structure_builder = Mock()

        with patch.object(self.config, "load_models_from_module") as load_models:
            self.config._process_module(
                "external_app.external_app_model_structure",
                "external_app_model_structure.py",
            )

        self.config.model_structure_builder.extract_and_save_structure.assert_called_once_with(
            "external_app.external_app_model_structure"
        )
        load_models.assert_not_called()
