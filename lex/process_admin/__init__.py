"""
Process Admin App

This app provides the custom administrative interface for managing business processes
and models in the LEX system. It includes:

- ProcessAdminSite: Custom admin site implementation
- ModelCollection: Dynamic model collection management
- Model structure and registration utilities
- Process admin views and URL routing
"""

# Lazy imports to avoid circular dependencies
__all__ = [
    'ProcessAdminSite',
    'processAdminSite',
    'adminSite',
    'ModelCollection',
    'ModelContainer',
    'ModelRegistration',
    'ModelStructure',
    'ModelStructureBuilder',
]


def __getattr__(name):
    if name == 'ProcessAdminSite':
        from lex.process_admin.sites import ProcessAdminSite
        return ProcessAdminSite
    elif name == 'processAdminSite':
        from lex.process_admin.settings import processAdminSite
        return processAdminSite
    elif name == 'adminSite':
        from lex.process_admin.settings import adminSite
        return adminSite
    elif name == 'ModelCollection':
        from lex.process_admin.models import ModelCollection
        return ModelCollection
    elif name == 'ModelContainer':
        from lex.process_admin.models import ModelContainer
        return ModelContainer
    elif name == 'ModelRegistration':
        from lex.process_admin.utils import ModelRegistration
        return ModelRegistration
    elif name == 'ModelStructure':
        from lex.process_admin.utils import ModelStructure
        return ModelStructure
    elif name == 'ModelStructureBuilder':
        from lex.process_admin.utils import ModelStructureBuilder
        return ModelStructureBuilder
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
