import sys
from .module_finder import ModuleAliasingFinder


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