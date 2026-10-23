"""
Model Admin App

This app provides the custom administrative interface for managing the models of
a LEX project. It includes:

- LexAdminSite: Custom admin site implementation
- ModelCollection: Dynamic model collection management
- Model structure and registration utilities
- Model admin views and URL routing
"""

# Lazy imports to avoid circular dependencies
__all__ = [
    'LexAdminSite',
    'lexAdminSite',
    'adminSite',
    'ModelCollection',
    'ModelContainer',
    'ModelRegistration',
    'ModelStructure',
    'ModelStructureBuilder',
]


def __getattr__(name):
    if name == 'LexAdminSite':
        from lex.model_admin.sites import LexAdminSite
        return LexAdminSite
    elif name == 'lexAdminSite':
        from lex.model_admin.settings import lexAdminSite
        return lexAdminSite
    elif name == 'adminSite':
        from lex.model_admin.settings import adminSite
        return adminSite
    elif name == 'ModelCollection':
        from lex.model_admin.models import ModelCollection
        return ModelCollection
    elif name == 'ModelContainer':
        from lex.model_admin.models import ModelContainer
        return ModelContainer
    elif name == 'ModelRegistration':
        from lex.model_admin.utils import ModelRegistration
        return ModelRegistration
    elif name == 'ModelStructure':
        from lex.model_admin.utils import ModelStructure
        return ModelStructure
    elif name == 'ModelStructureBuilder':
        from lex.model_admin.utils import ModelStructureBuilder
        return ModelStructureBuilder
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
