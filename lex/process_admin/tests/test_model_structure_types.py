import os
import sys
from pathlib import Path
from unittest import TestCase

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from lex.core.models.HTMLReport import HTMLReport
from lex.process_admin.models.utils import enrich_model_structure_with_readable_names_and_types


class _Container:
    def __init__(self, model_class, title):
        self.model_class = model_class
        self.title = title


class _Collection:
    def __init__(self, containers, model_styling=None):
        self._containers = containers
        self.model_styling = model_styling or {}

    @property
    def all_model_ids(self):
        return set(self._containers.keys())

    def get_container(self, node_name):
        return self._containers[node_name]


class ModelStructureTypeTests(TestCase):
    def test_indirect_html_report_subclass_is_typed_as_htmlreport(self):
        class BaseReport(HTMLReport):
            pass

        class CustomerReport(BaseReport):
            pass

        collection = _Collection(
            {"customerreport": _Container(CustomerReport, "Customer Report")}
        )

        node = enrich_model_structure_with_readable_names_and_types(
            "customerreport",
            None,
            collection,
        )

        self.assertEqual(node["type"], "HTMLReport")

    def test_nested_nodes_can_use_global_styling_entries(self):
        class StreamlitReport(HTMLReport):
            pass

        collection = _Collection(
            {"streamlit": _Container(StreamlitReport, "Default Streamlit Title")},
            model_styling={
                "Streamlit": {"name": "Interactive analysis"},
                "streamlit": {"name": "Interactive analysis"},
            },
        )

        node = enrich_model_structure_with_readable_names_and_types(
            "Streamlit",
            {"streamlit": None},
            collection,
        )

        self.assertEqual(node["readable_name"], "Interactive analysis")
        self.assertEqual(
            node["children"]["streamlit"]["readable_name"],
            "Interactive analysis",
        )

    def test_nested_folder_can_use_global_styling_entries(self):
        collection = _Collection(
            {"period": _Container(object, "Default Period Title")},
            model_styling={
                "A_Period": {"name": "Period"},
                "S_Period_Information": {"name": "Period Information"},
            },
        )

        node = enrich_model_structure_with_readable_names_and_types(
            "A_Period",
            {"S_Period_Information": {"period": None}},
            collection,
        )

        self.assertEqual(node["readable_name"], "Period")
        self.assertEqual(
            node["children"]["S_Period_Information"]["readable_name"],
            "Period Information",
        )
