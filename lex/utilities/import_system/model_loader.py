import warnings
import importlib.abc
from django.apps import apps


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