import logging

import yaml

logger = logging.getLogger(__name__)


class ModelStructure:
    UNTRACKED_MODELS = []
    def __init__(self, path: str):
        self.path = path
        self.structure = {}
        self._structure_defined = False
        self.styling = {}
        # Add the new attribute for untracked models
        self.untracked_models = []

        self._load_info()

        ModelStructure.load_untracked_models_globally(self.path)

    def _load_info(self):
        with open(self.path, "r") as f:
            data = yaml.safe_load(f)
        try:
            self.structure = data["model_structure"]
            self._structure_defined = True
        except (KeyError, TypeError):
            logger.warning("Structure is not defined in the model info file")
        try:
            self.styling = data["model_styling"]
        except (KeyError, TypeError):
            logger.debug("Styling is not defined in the model info file")
        # Try to load the untracked models list, default to empty list if not found
        try:
            self.untracked_models = data["untracked_models"]
        except (KeyError, TypeError):
            # It's okay if this is not defined
            pass

    def structure_is_defined(self):
        return self._structure_defined

    @classmethod
    def load_untracked_models_globally(cls, path: str):
        """
        Loads untracked models from a YAML file and updates the static
        class variable.
        """
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        try:
            cls.UNTRACKED_MODELS = data["untracked_models"]
            logger.debug(f"UNTRACKED_MODELS loaded: {cls.UNTRACKED_MODELS}")
        except (KeyError, TypeError):
            logger.debug("'untracked_models' not found in the YAML file.")
