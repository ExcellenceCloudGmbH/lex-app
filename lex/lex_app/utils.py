"""
Backward compatibility module for lex_app utilities.

This module provides backward compatibility imports for code that still references
the old lex.lex_app.utils module. All functionality has been moved to the new
app structure.

DEPRECATION NOTICE:
    Importing from lex.lex_app.utils is deprecated. Please update your imports
    to use the new locations:
    
    OLD: from lex.lex_app.utils import GenericAppConfig
    NEW: from lex.utilities.config.generic_app_config import GenericAppConfig
    
    OLD: from lex.lex_app.utils import ModuleAliasingFinder
    NEW: from lex.utilities.import_system.module_finder import ModuleAliasingFinder
    
    See the migration guide below for complete mapping.

MIGRATION GUIDE:
    
    Import System Components:
    - ModuleAliasingFinder -> lex.utilities.import_system.module_finder
    - ModelAwareLoader -> lex.utilities.import_system.model_loader
    - install_custom_import_system -> lex.utilities.import_system.import_utils
    
    Configuration:
    - GenericAppConfig -> lex.utilities.config.generic_app_config
    
    For other commonly used classes, see the new app structure:
    - Core models -> lex.core.models
    - Authentication -> lex.authentication
    - Logging -> lex.audit_logging
    - API -> lex.api
    - Process Admin -> lex.process_admin
"""

import warnings

# Import system components moved to utilities app
from lex.utilities.import_system.module_finder import ModuleAliasingFinder
from lex.utilities.import_system.model_loader import ModelAwareLoader
from lex.utilities.import_system.import_utils import install_custom_import_system
from lex.utilities.config.generic_app_config import GenericAppConfig


def _deprecated_import_warning(old_path, new_path):
    """Issue a deprecation warning for old import paths."""
    warnings.warn(
        f"Importing from '{old_path}' is deprecated and will be removed in a future version. "
        f"Please update your imports to use '{new_path}' instead.",
        DeprecationWarning,
        stacklevel=3
    )


# Issue deprecation warnings when this module is imported
_deprecated_import_warning(
    'lex.lex_app.utils',
    'lex.utilities (see module docstring for specific mappings)'
)


# Backward compatibility - re-export classes for existing imports
__all__ = [
    'ModuleAliasingFinder',
    'ModelAwareLoader', 
    'install_custom_import_system',
    'GenericAppConfig'
]

