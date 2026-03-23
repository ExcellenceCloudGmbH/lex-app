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

from lex.process_admin.views.model_relation_views import ModelStructureObtainView


class _AllowedModel:
    def can_list(self, request):
        return True


class _DeniedModel:
    def can_list(self, request):
        return False


class _Container:
    def __init__(self, model_class):
        self.model_class = model_class


class ModelStructurePermissionPruningTests(TestCase):
    def setUp(self):
        self.view = ModelStructureObtainView()
        self.view.get_container_func = self._get_container
        self.view.get_hidden_historical_models_func = lambda: {
            "historical_allowed_model": {
                "type": "Model",
                "readable_name": "Historical Allowed",
            },
            "historical_denied_model": {
                "type": "Model",
                "readable_name": "Historical Denied",
            },
        }
        self.view.get_model_structure_func = lambda: {
            "Main": {
                "type": "Folder",
                "children": {
                    "allowed_model": {
                        "type": "Model",
                        "readable_name": "Allowed",
                    },
                },
            }
        }
        self.request = object()
        self.containers = {
            "allowed_model": _Container(_AllowedModel),
            "denied_model": _Container(_DeniedModel),
            "historical_allowed_model": _Container(_AllowedModel),
            "historical_denied_model": _Container(_DeniedModel),
        }

    def _get_container(self, model_id):
        return self.containers[model_id]

    def test_folder_is_removed_when_all_child_models_are_denied(self):
        tree = {
            "Main": {
                "type": "Folder",
                "children": {
                    "denied_model": {"type": "Model", "readable_name": "Denied"},
                },
            }
        }

        self.view.delete_restricted_nodes_from_model_structure(tree, self.request)

        self.assertEqual(tree, {})

    def test_folder_kept_when_at_least_one_child_model_is_visible(self):
        tree = {
            "Main": {
                "type": "Folder",
                "children": {
                    "allowed_model": {"type": "Model", "readable_name": "Allowed"},
                    "denied_model": {"type": "Model", "readable_name": "Denied"},
                },
            }
        }

        self.view.delete_restricted_nodes_from_model_structure(tree, self.request)

        self.assertIn("Main", tree)
        self.assertEqual(set(tree["Main"]["children"].keys()), {"allowed_model"})

    def test_get_returns_visible_tree_and_only_permitted_hidden_historical_models(self):
        response = self.view.get(self.request)

        self.assertEqual(set(response.data.keys()), {"modelHierarchy", "historicalModels"})
        self.assertIn("Main", response.data["modelHierarchy"])
        self.assertEqual(
            set(response.data["historicalModels"].keys()),
            {"historical_allowed_model"},
        )
