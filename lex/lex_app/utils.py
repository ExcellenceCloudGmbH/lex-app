import os
import sys
import types
from pathlib import Path
import importlib
import importlib.util
import importlib.abc
import importlib.machinery

from django.apps import AppConfig
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


# --- helpers: virtual package + short->long aliasing ---

def _ensure_virtual_prefix_package(prefix: str, root_path: str) -> None:
    """
    Ensure a virtual top-level package `prefix` exists whose __path__
    points at `root_path`. This makes `import prefix.X.Y` load files
    from `<root_path>/X/Y.py`.
    """
    if prefix in sys.modules:
        return
    spec = importlib.machinery.ModuleSpec(prefix, loader=None, is_package=True)
    spec.submodule_search_locations = [root_path]
    mod = types.ModuleType(prefix)
    mod.__spec__ = spec
    mod.__path__ = spec.submodule_search_locations
    sys.modules[prefix] = mod


def _discover_roots(project_path: str) -> set[str]:
    """
    Top-level package roots under project_path (directories with __init__.py).
    You can expand this as needed; packages are what you import as `X.*`.
    """
    roots = set()
    for entry in os.listdir(project_path):
        if entry.startswith(('.', '_')):
            continue
        full = os.path.join(project_path, entry)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, '__init__.py')):
            roots.add(entry)
    return roots


class _ExistingModuleLoader(importlib.abc.Loader):
    """No-op loader that returns an already-imported module."""
    def create_module(self, spec):
        return sys.modules.get(spec.name)
    def exec_module(self, module):
        return


class _ShortToLongAliasFinder(importlib.abc.MetaPathFinder):
    """
    Intercepts imports of short names like `X.Y` where X is a discovered root,
    imports the long name `PREFIX.X.Y`, then aliases the short name to it.
    """
    def __init__(self, prefix: str, roots: set[str]):
        self.prefix = prefix
        self.roots = set(roots)
        self._marker = f'__short_to_long__{prefix}'

    def find_spec(self, fullname, path, target=None):
        # Only handle short roots (e.g., "InvestmentStructure.Organisation")
        head = fullname.split('.', 1)[0]
        if head not in self.roots:
            return None

        long_name = f"{self.prefix}.{fullname}"

        # If already imported/aliased, return a trivial spec.
        if fullname in sys.modules:
            is_pkg = hasattr(sys.modules[fullname], '__path__')
            spec = importlib.machinery.ModuleSpec(fullname, _ExistingModuleLoader(), is_package=is_pkg)
            if is_pkg:
                spec.submodule_search_locations = list(sys.modules[fullname].__path__)
            return spec

        # Import the *long* name via normal machinery (uses the virtual package).
        # This does not re-enter this finder (we only handle short names).
        module = importlib.import_module(long_name)

        # Alias short -> long (same module object)
        sys.modules[fullname] = module

        # Also wire parent attributes so attribute access works (pkg.module)
        parts = fullname.split('.')
        for i in range(1, len(parts)):
            parent = '.'.join(parts[:i])
            child = parts[i]
            pmod = sys.modules.get(parent)
            if pmod is None:
                # Create a lightweight package parent if missing
                pmod = types.ModuleType(parent)
                pmod.__path__ = []
                sys.modules[parent] = pmod
            setattr(pmod, child, sys.modules[fullname if i == len(parts)-1 else '.'.join(parts[:i+1])])

        # Return a no-op spec for the short name (module is already in sys.modules)
        is_pkg = hasattr(module, '__path__')
        spec = importlib.machinery.ModuleSpec(fullname, _ExistingModuleLoader(), is_package=is_pkg)
        if is_pkg and hasattr(module, '__path__'):
            spec.submodule_search_locations = list(module.__path__)
        return spec


def _install_short_to_long_aliasing(prefix: str, project_path: str):
    """
    Install (once) the virtual package and the short->long alias finder.
    """
    if not prefix:
        return
    # Avoid double install
    for f in sys.meta_path:
        if getattr(f, '_marker', None) == f'__short_to_long__{prefix}':
            return

    _ensure_virtual_prefix_package(prefix, project_path)
    roots = _discover_roots(project_path)
    finder = _ShortToLongAliasFinder(prefix, roots)
    sys.meta_path.insert(0, finder)


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

        # ✅ Install the aliasing BEFORE discovery/imports.
        # Choose the long prefix you want (sounds like your app/repo name).
        long_prefix = repo or ""
        if long_prefix == settings.repo_name:
            _install_short_to_long_aliasing(long_prefix, str(self.project_path))

        self.discover_models(self.project_path, repo=repo)

        if not self.model_structure_builder.model_structure and not subdir:
            self.model_structure_builder.build_structure(self.discovered_models)

        self.untracked_models += self.model_structure_builder.untracked_models
        self.register_models()


    def discover_models(self, path, repo):
        for root, dirs, files in os.walk(path):
            # Skip 'venv', '.venv', and 'build' directories
            dirs[:] = [directory for directory in dirs if self._dir_filter(directory)]
            for file in files:
                # Process only .py files that do not start with '_'

                absolute_path = os.path.join(root, file)
                module_name = os.path.relpath(absolute_path, self.project_path)
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
                # not module_name.startswith(self._EXCLUDED_PREFIXES) and
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
        if name not in self.discovered_models:
            self.discovered_models[name] = model

    # All model Registrations happen here
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
