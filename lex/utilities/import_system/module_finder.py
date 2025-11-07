import os
import sys
import types
import importlib
import importlib.abc
import importlib.machinery


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
        # Skip modules that are part of the new app structure (lex.*)
        # These are already properly namespaced and don't need aliasing
        if fullname.startswith('lex.') and not fullname.startswith('lex_app'):
            # Check if this is a new-style app (utilities, core, authentication, etc.)
            parts = fullname.split('.')
            if len(parts) >= 2 and parts[1] in ('utilities', 'core', 'authentication', 
                                                   'audit_logging', 'api', 'process_admin'):
                return None  # Let Django handle these normally
        
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
            # Import ModelAwareLoader from the same package
            from .model_loader import ModelAwareLoader
            
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
        # Skip if this is a new-style app (lex.*)
        if name.startswith('lex.'):
            parts = name.split('.')
            if len(parts) >= 2 and parts[1] in ('utilities', 'core', 'authentication',
                                                   'audit_logging', 'api', 'process_admin'):
                return False
        
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