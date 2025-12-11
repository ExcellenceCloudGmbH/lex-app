import os
import importlib
from pathlib import Path

from django.apps import AppConfig
from django.db import models

from lex.process_admin.utils.model_registration import ModelRegistration
from lex.process_admin.utils.model_structure_builder import ModelStructureBuilder
from lex.authentication.utils.lex_authentication import LexAuthentication
from lex.utilities.import_system.import_utils import install_custom_import_system


def _is_structure_yaml_file(file):
    return file == "model_structure.yaml"


def _is_structure_file(file):
    return file.endswith('_structure.py')


class GenericAppConfig(AppConfig):
    _EXCLUDED_FILES = ("asgi", "wsgi", "settings", "urls", 'setup')
    _EXCLUDED_DIRS = ('venv', '.venv', 'build', 'migrations')
    _EXCLUDED_PREFIXES = ('_', '.')
    _EXCLUDED_POSTFIXES = ('_', '.', 'create_db', 'CalculationIDs', '_test')

    def __init__(self, app_name, app_module):
        super().__init__(app_name, app_module)
        self.subdir = None
        self.project_path = None
        self.model_structure_builder = None
        self.pending_relationships = None
        self.untracked_models = ["calculationlog", "auditlog", "auditlogstatus"]
        self.discovered_models = None
        self.import_finder = None

    def ready(self):
        self.start(repo=self.name)


    def start(self, repo=None, is_lex=True):
        self.pending_relationships = {}
        self.discovered_models = {}
        self.model_structure_builder = ModelStructureBuilder(repo=repo)
        self.project_path = os.path.dirname(self.module.__file__) if is_lex else Path(
            os.getenv("PROJECT_ROOT", os.getcwd())
        ).resolve()

        # ✅ Only install custom import system when subdir is NOT empty
        if not is_lex and repo:
            self.import_finder = install_custom_import_system(
                self.project_path,
                repo
            )

        self.discover_models(self.project_path, repo=repo)

        if not self.model_structure_builder.model_structure and not is_lex:
            self.model_structure_builder.build_structure(self.discovered_models)

        self.untracked_models += self.model_structure_builder.untracked_models
        self.register_models()

    def discover_models(self, path, repo):
        for root, dirs, files in os.walk(path):
            dirs[:] = [directory for directory in dirs if self._dir_filter(directory)]
            for file in files:
                absolute_path = os.path.join(root, file)
                module_name = os.path.relpath(absolute_path, self.project_path)

                # Add repo prefix if needed and not already present
                if repo and not module_name.startswith(repo):
                    module_name = f"{repo}.{module_name}"

                rel_module_name = module_name.replace(os.path.sep, '.')[:-3]
                module_name = rel_module_name.split('.')[-1]

                if _is_structure_yaml_file(file):
                    self.model_structure_builder.extract_from_yaml(absolute_path)
                elif self._is_valid_module(module_name, file):
                    self._process_module(rel_module_name, file)

    def _dir_filter(self, directory):
        return directory not in self._EXCLUDED_DIRS and not directory.startswith(self._EXCLUDED_PREFIXES)

    def _is_valid_module(self, module_name, file):
        return (file.endswith('.py') and
                not module_name.endswith(self._EXCLUDED_POSTFIXES) and
                module_name not in self._EXCLUDED_FILES)

    def _process_module(self, full_module_name, file):
        if file.endswith('_authentication_settings.py'):
            try:
                module = importlib.import_module(full_module_name)
                LexAuthentication().load_settings(module)
            except ImportError as e:
                print(f"Error importing authentication settings: {e}")
                raise
            except Exception as e:
                print(f"Authentication settings doesn't have method create_groups()")
                raise
        else:
            self.load_models_from_module(full_module_name)

    def load_models_from_module(self, full_module_name):
        try:
            if not full_module_name.startswith('.'):
                # Import will use custom system if installed, otherwise standard import
                module = importlib.import_module(full_module_name)

                for name, obj in module.__dict__.items():
                    if (isinstance(obj, type)
                            and issubclass(obj, models.Model)
                            and hasattr(obj, '_meta')
                            and not obj._meta.abstract):
                        self.add_model(name, obj)
        except (RuntimeError, AttributeError, ImportError) as e:
            print(f"Error importing {full_module_name}: {e}")
            raise

    def add_model(self, name, model):
        """Add model to discovered_models, avoiding duplicates."""
        if name not in self.discovered_models:
            self.discovered_models[name] = model

    def register_models(self):
        from django.contrib import admin
        from lex.lex_app.streamlit.Streamlit import Streamlit

        ModelRegistration.register_models(
            [o for o in self.discovered_models.values() if not admin.site.is_registered(o)],
            self.untracked_models
        )

        ModelRegistration.register_model_structure(self.model_structure_builder.model_structure)
        ModelRegistration.register_model_styling(self.model_structure_builder.model_styling)
        ModelRegistration.register_widget_structure(self.model_structure_builder.widget_structure)
        ModelRegistration.register_models([Streamlit], self.untracked_models)