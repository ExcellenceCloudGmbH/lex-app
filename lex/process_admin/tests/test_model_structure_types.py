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
    def __init__(self, containers):
        self._containers = containers
        self.model_styling = {}

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
