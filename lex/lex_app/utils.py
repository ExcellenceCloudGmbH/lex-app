import os
import sys
import types
from pathlib import Path
import importlib
import importlib.util
import importlib.abc
import importlib.machinery
import warnings

from django.apps import AppConfig, apps
from django.contrib import admin
from django.db import models

from lex.lex_app.model_utils.ModelRegistration import ModelRegistration
from lex.lex_app.model_utils.ModelStructureBuilder import ModelStructureBuilder
from lex.lex_app.model_utils.LexAuthentication import LexAuthentication
from lex_app import settings


def _is_structure_yaml_file(file):
    return file == "model_structure.yaml"


def _is_structure_file(file):
    return file.endswith('_structure.py')


# ============================================================================
# CUSTOM IMPORT SYSTEM - Core Implementation
# ============================================================================

class ModuleAliasingFinder(importlib.abc.MetaPathFinder):
    """
    Meta path finder that:
    1. Handles both short (Folder1.Object1) and long (Project.Folder1.Object1) imports
    2. Always loads under the LONG canonical name for Django compatibility
    3. Creates aliases so both import styles work
    4. Uses ModelAwareLoader to prevent Django model re-registration
    """

    def __init__(self, project_root, repo_name):
        """
        Args:
            project_root: Base path of the project
            repo_name: Name of the repository/project (e.g., 'ArmiraCashflowDB')
        """
        self.project_root = project_root
        self.repo_name = repo_name
        self.processed_modules = set()
        self._module_aliases = {}  # Track short <-> long name mappings

    def find_spec(self, fullname, path, target=None):
        """
        Find module spec and handle aliasing between short and long names.
        Always use LONG name as canonical to ensure proper Django app_label.
        """
        # Determine if this is a short or long name
        is_long_name = fullname.startswith(f"{self.repo_name}.")
        is_short_name = not is_long_name and self._could_be_short_name(fullname)

        if not (is_long_name or is_short_name):
            return None  # Not our module

        # Generate both short and long versions
        # ✅ ALWAYS use long name as canonical for Django compatibility
        if is_long_name:
            short_name = fullname[len(self.repo_name) + 1:]  # Remove "Project."
            long_name = fullname
        else:
            short_name = fullname
            long_name = f"{self.repo_name}.{fullname}"

        # ✅ Use LONG name as canonical
        canonical_name = long_name

        # Check if canonical (long) version is already loaded
        if canonical_name in sys.modules:
            # If requesting by short name, create alias to long name
            if fullname == short_name:
                return self._create_alias_spec(short_name, canonical_name)
            # If requesting by long name, return existing module
            return self._create_existing_spec(canonical_name)

        # Not loaded yet, find the file and load it under LONG name
        module_path = self._find_module_file(short_name)

        if module_path:
            # Create spec with LONG name as canonical
            is_package = module_path.endswith('__init__.py')

            spec = importlib.machinery.ModuleSpec(
                canonical_name,  # ✅ Always use long name
                ModelAwareLoader(module_path, canonical_name),
                origin=module_path,
                is_package=is_package
            )

            if is_package:
                spec.submodule_search_locations = [os.path.dirname(module_path)]

            # Store the alias mapping
            self._module_aliases[short_name] = canonical_name
            self._module_aliases[long_name] = canonical_name

            # If requesting by short name, we need to load under long name then alias
            if fullname == short_name:
                # Import the long name first
                importlib.import_module(canonical_name)
                # Now return alias spec
                return self._create_alias_spec(short_name, canonical_name)

            # Requesting by long name, return the spec directly
            return spec

        return None  # Module file not found in our project

    def _could_be_short_name(self, name):
        """Check if this could be a short name for our project."""
        # Get first component
        first_part = name.split('.')[0]

        # Check if a directory with this name exists in project_root
        potential_path = os.path.join(self.project_root, first_part)

        # Only return True if it's actually a directory in our project
        if not os.path.isdir(potential_path):
            return False

        # Additional check: verify the full module path exists
        parts = name.split('.')

        # Check for package
        package_init = os.path.join(self.project_root, *parts, '__init__.py')
        if os.path.exists(package_init):
            return True

        # Check for module file
        module_file = os.path.join(self.project_root, *parts) + '.py'
        if os.path.exists(module_file):
            return True

        return False

    def _find_module_file(self, short_name):
        """
        Find the actual .py file for a module given its short name.
        Returns the full path to the file or None.
        """
        parts = short_name.split('.')

        # Try as package (__init__.py)
        package_init = os.path.join(self.project_root, *parts, '__init__.py')
        if os.path.exists(package_init):
            return package_init

        # Try as regular module (.py)
        module_file = os.path.join(self.project_root, *parts) + '.py'
        if os.path.exists(module_file):
            return module_file

        return None

    def _create_existing_spec(self, canonical_name):
        """Create a spec for an already-loaded module."""
        if canonical_name not in sys.modules:
            return None

        canonical_module = sys.modules[canonical_name]
        is_package = hasattr(canonical_module, '__path__')

        spec = importlib.machinery.ModuleSpec(
            canonical_name,
            _AliasingLoader(canonical_module, canonical_name, canonical_name),
            is_package=is_package
        )

        if is_package and hasattr(canonical_module, '__path__'):
            spec.submodule_search_locations = list(canonical_module.__path__)

        return spec

    def _create_alias_spec(self, requested_name, canonical_name):
        """
        Create a spec that aliases requested_name to canonical_name.
        """
        # Safety check: ensure canonical module exists
        if canonical_name not in sys.modules:
            return None

        canonical_module = sys.modules[canonical_name]
        is_package = hasattr(canonical_module, '__path__')

        # Create a spec that will just alias to the existing module
        spec = importlib.machinery.ModuleSpec(
            requested_name,
            _AliasingLoader(canonical_module, requested_name, canonical_name),
            is_package=is_package
        )

        if is_package and hasattr(canonical_module, '__path__'):
            spec.submodule_search_locations = list(canonical_module.__path__)

        return spec


class ModelAwareLoader(importlib.abc.Loader):
    """
    Custom loader that prevents Django model re-registration by tracking
    already-loaded models and reusing them instead of re-executing class definitions.
    """

    def __init__(self, filepath, fullname):
        self.filepath = filepath
        self.fullname = fullname

    def create_module(self, spec):
        """Use default module creation."""
        return None

    def exec_module(self, module):
        """
        Execute module but reuse already-registered Django models to prevent
        re-registration warnings.
        """
        # ✅ Set __file__ and __name__ properly for Django
        module.__file__ = self.filepath
        module.__name__ = self.fullname

        # Read source code
        with open(self.filepath, 'r', encoding='utf-8') as f:
            source_code = f.read()

        # Prepare module namespace
        module_globals = vars(module)

        # Find all registered Django models that might be in this module
        # and inject them BEFORE executing the module code
        self._inject_existing_models(module_globals)

        # Suppress Django model re-registration warnings during execution
        with warnings.catch_warnings():
            warnings.filterwarnings(
                'ignore',
                message=r".*was already registered\.",
                category=RuntimeWarning,
                module='django.db.models.base'
            )

            try:
                compiled = compile(source_code, self.filepath, 'exec')
                exec(compiled, module_globals)
            except Exception as e:
                raise ImportError(f"Failed to execute module {self.fullname}: {e}") from e

    def _inject_existing_models(self, module_globals):
        """
        Inject already-registered Django models into the module namespace
        before execution to prevent re-registration.
        """
        # Get all registered models from Django's registry
        for app_label, models_dict in apps.all_models.items():
            for model_name, model_class in models_dict.items():
                # Get the actual class name (usually PascalCase)
                class_name = model_class.__name__

                # Check if this model might belong to the module being loaded
                # by checking if the model's module matches or starts with our module
                model_module = model_class.__module__

                if model_module == self.fullname or model_module.startswith(f"{self.fullname}."):
                    # Inject the existing model into namespace
                    if class_name not in module_globals:
                        module_globals[class_name] = model_class


class _AliasingLoader(importlib.abc.Loader):
    """Loader that aliases one module name to another already-loaded module."""

    def __init__(self, target_module, alias_name, canonical_name):
        self.target_module = target_module
        self.alias_name = alias_name
        self.canonical_name = canonical_name

    def create_module(self, spec):
        # Return the existing module
        return self.target_module

    def exec_module(self, module):
        # Module is already executed, just ensure aliasing in sys.modules
        if self.alias_name not in sys.modules:
            sys.modules[self.alias_name] = self.target_module

        # Also ensure parent packages exist for the alias
        self._ensure_parent_packages(self.alias_name)

    def _ensure_parent_packages(self, fullname):
        """Ensure all parent packages exist as aliases too."""
        parts = fullname.split('.')
        for i in range(1, len(parts)):
            parent_name = '.'.join(parts[:i])
            if parent_name not in sys.modules:
                # Create a minimal package module
                parent_module = types.ModuleType(parent_name)
                parent_module.__path__ = []
                sys.modules[parent_name] = parent_module

            # Set attribute on parent
            child_name = parts[i]
            child_module = sys.modules.get('.'.join(parts[:i + 1]))
            if child_module:
                setattr(sys.modules[parent_name], child_name, child_module)


def install_custom_import_system(project_root, repo_name):
    """
    Install the custom import system that handles aliasing and prevents
    Django model re-registration.

    Args:
        project_root: Base path of the project
        repo_name: Repository/project name
    """
    # Check if already installed
    for finder in sys.meta_path:
        if isinstance(finder, ModuleAliasingFinder):
            return finder

    # Create and install the finder
    finder = ModuleAliasingFinder(str(project_root), repo_name)
    sys.meta_path.insert(0, finder)

    return finder


# ============================================================================
# GENERIC APP CONFIG - Updated
# ============================================================================

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
        self.start(repo=self.name, subdir=f"lex.{self.name}.")

    def start(self, repo=None, subdir=""):
        self.pending_relationships = {}
        self.discovered_models = {}
        self.model_structure_builder = ModelStructureBuilder(repo=repo)
        self.project_path = os.path.dirname(self.module.__file__) if subdir else Path(
            os.getenv("PROJECT_ROOT", os.getcwd())
        ).resolve()
        self.subdir = "" if not subdir else subdir

        # ✅ Only install custom import system when subdir is NOT empty
        if not subdir and repo:
            self.import_finder = install_custom_import_system(
                self.project_path,
                repo
            )

        self.discover_models(self.project_path, repo=repo)

        if not self.model_structure_builder.model_structure and not subdir:
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
                if repo and not module_name.startswith(repo) and repo != 'lex_app':
                    module_name = f"{repo}.{module_name}"

                rel_module_name = module_name.replace(os.path.sep, '.')[:-3]
                module_name = rel_module_name.split('.')[-1]
                full_module_name = f"{self.subdir}{rel_module_name}"

                if _is_structure_yaml_file(file):
                    self.model_structure_builder.extract_from_yaml(absolute_path)
                elif self._is_valid_module(module_name, file):
                    self._process_module(full_module_name, file)

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
        from lex.lex_app.streamlit.Streamlit import Streamlit

        ModelRegistration.register_models(
            [o for o in self.discovered_models.values() if not admin.site.is_registered(o)],
            self.untracked_models
        )

        ModelRegistration.register_model_structure(self.model_structure_builder.model_structure)
        ModelRegistration.register_model_styling(self.model_structure_builder.model_styling)
        ModelRegistration.register_widget_structure(self.model_structure_builder.widget_structure)
        ModelRegistration.register_models([Streamlit], self.untracked_models)
