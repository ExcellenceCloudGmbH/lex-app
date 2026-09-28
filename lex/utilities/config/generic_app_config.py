import importlib
import os
import sys
from pathlib import Path

from django.apps import AppConfig
from django.db import models
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.process_admin.utils.model_structure_builder import ModelStructureBuilder
from lex.utilities.import_system.import_utils import install_custom_import_system


def _is_structure_yaml_file(file):
    return file == "model_structure.yaml"


#: The project's Reflex dashboards. Named like a structure file, but it is the
#: Reflex app's to import, and only the Reflex app's: it defines Reflex states and
#: pages, which have no business being created in every process Django starts.
_REFLEX_STRUCTURE_FILE = "_reflex_structure.py"


def _is_structure_file(file):
    return file.endswith('_structure.py') and file != _REFLEX_STRUCTURE_FILE


class GenericAppConfig(AppConfig):
    # `rxconfig` is Reflex's configuration module: importing it as a model module
    # would build the Reflex config in every process Django starts.
    _EXCLUDED_FILES = ("asgi", "wsgi", "settings", "urls", 'setup', 'rxconfig')
    _EXCLUDED_DIRS = ('venv', '.venv', 'build', 'migrations')
    _EXCLUDED_PREFIXES = ('_', '.', 'test_')
    _EXCLUDED_POSTFIXES = ('_', '.', 'create_db', 'CalculationIDs', '_test')
    #: Directories inside lex's OWN packages that discovery must not walk into.
    #: Discovery imports a module under its app-relative name -- `lex_app.reflex.auth`
    #: -- which is a second module object beside the `lex.lex_app.reflex.auth` the
    #: rest of the code imports. For `reflex` that would define every Reflex state
    #: twice. It holds no models, and its report is registered explicitly.
    _LEX_PACKAGE_EXCLUDED_DIRS = ('reflex',)

    def __init__(self, app_name, app_module):
        super().__init__(app_name, app_module)
        self.subdir = None
        self.project_path = None
        self.model_structure_builder = None
        self.pending_relationships = None
        self.untracked_models = ["calculationlog", "auditlog", "auditlogstatus", "legacyuserchangelog", "legacycalculationlog", "legacycalculationid"]
        self.discovered_models = None
        self.import_finder = None

    def ready(self):
        self.start(repo=self.name)


    def start(self, repo=None, is_lex=True):
        from lex.lex_app.reflex.Reflex import reflex_enabled

        self.pending_relationships = {}
        self.discovered_models = {}
        self._discovering_lex_package = is_lex
        predefined_structure = {"AuditLog": {
            "auditlog": None,
        },
        "CalculationLog" : {
            "calculationlog": None,
        },
        "Streamlit":{
            "streamlit": None
        }
        }
        if reflex_enabled():
            predefined_structure["Reflex"] = {"reflex": None}

        self.model_structure_builder = ModelStructureBuilder(repo=repo, predefined_structure= predefined_structure)

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

        if (
            not self.model_structure_builder.model_structure
            and not self.model_structure_builder.model_structure_is_explicitly_defined
            and not is_lex
        ):
            self.model_structure_builder.build_structure(self.discovered_models)

        self.untracked_models += self.model_structure_builder.resolve_untracked_models(
            self.discovered_models.keys()
        )
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
                elif _is_structure_file(file):
                    self._process_module(rel_module_name, file)
                elif self._is_valid_module(module_name, file):
                    self._process_module(rel_module_name, file)

    def _dir_filter(self, directory):
        if directory in self._EXCLUDED_DIRS or directory.startswith(self._EXCLUDED_PREFIXES):
            return False
        return not (
            getattr(self, "_discovering_lex_package", False)
            and directory in self._LEX_PACKAGE_EXCLUDED_DIRS
        )

    def _is_valid_module(self, module_name, file):
        return (file.endswith('.py')
                and not module_name.endswith(self._EXCLUDED_POSTFIXES)
                and module_name not in self._EXCLUDED_FILES
                and not module_name.startswith(self._EXCLUDED_PREFIXES))

    def _process_module(self, full_module_name, file):
        if _is_structure_file(file):
            self.model_structure_builder.extract_and_save_structure(full_module_name)
            return

        self.load_models_from_module(full_module_name)

    def load_models_from_module(self, full_module_name):
        from lex.core.models.HTMLReport import HTMLReport

        try:
            if not full_module_name.startswith('.'):
                # Import will use custom system if installed, otherwise standard import
                module = importlib.import_module(full_module_name)

                for name, obj in module.__dict__.items():
                    if not isinstance(obj, type):
                        continue

                    # Keep discovery local to this module and avoid re-registering imports.
                    if getattr(obj, "__module__", None) != module.__name__:
                        continue

                    is_django_model = issubclass(obj, models.Model) and hasattr(obj, "_meta")
                    is_html_report = issubclass(obj, HTMLReport)

                    if is_django_model:
                        if not obj._meta.abstract:
                            self.add_model(name, obj)
                        continue

                    if is_html_report:
                        if getattr(obj, "__name__", None) == "HTMLReport":
                            continue
                        self.add_model(name, obj)
        except (RuntimeError, AttributeError, ImportError) as e:
            # Write to stderr — this code can run inside an MCP stdio server
            # process, where stdout is the JSON-RPC channel and any non-JSON
            # text corrupts the stream.
            print(f"Error importing {full_module_name}: {e}", file=sys.stderr)
            raise

    def add_model(self, name, model):
        """Add model to discovered_models, avoiding duplicates."""
        if name not in self.discovered_models:
            self.discovered_models[name] = model

    def register_models(self):
        from django.contrib import admin
        from lex.lex_app.reflex.Reflex import Reflex, reflex_enabled
        from lex.lex_app.streamlit.Streamlit import Streamlit

        ModelRegistration.register_models(
            [o for o in self.discovered_models.values() if not admin.site.is_registered(o)],
            self.untracked_models,
            self.model_structure_builder.history_tracking_enabled,
        )
        if (
            self.model_structure_builder.model_structure
            or self.model_structure_builder.model_structure_is_explicitly_defined
        ):
            ModelRegistration.register_model_structure(
                self.model_structure_builder.model_structure,
                auto_include_missing_models=not self.model_structure_builder.model_structure_is_explicitly_defined,
            )
        ModelRegistration.register_model_styling(self.model_structure_builder.model_styling)
        ModelRegistration.register_widget_structure(self.model_structure_builder.widget_structure)
        ModelRegistration.register_models(
            [Streamlit, Reflex] if reflex_enabled() else [Streamlit],
            self.untracked_models,
            self.model_structure_builder.history_tracking_enabled,
        )
