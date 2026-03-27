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
        self.untracked_models = []
        self.tracked_models = []
        self.tracked_models_defined = False

        data = self._load_yaml_data(self.path)
        self._load_info(data)

        ModelStructure._load_untracked_models_globally_from_data(data)

    @staticmethod
    def _normalize_model_list(data, key):
        values = data.get(key, [])
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValueError(f"'{key}' must be defined as a list in model_structure.yaml.")
        return [str(value).lower() for value in values]

    @classmethod
    def _load_yaml_data(cls, path: str):
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict):
            return data

        if "untracked_models" in data and "tracked_models" in data:
            raise ValueError(
                "model_structure.yaml cannot define both 'untracked_models' and "
                "'tracked_models'. Please use only one of them."
            )

        return data

    def _load_info(self, data):
        try:
            self.structure = data["model_structure"]
            self._structure_defined = True
        except (KeyError, TypeError):
            logger.warning("Structure is not defined in the model info file")
        try:
            self.styling = data["model_styling"]
        except (KeyError, TypeError):
            logger.debug("Styling is not defined in the model info file")

        if isinstance(data, dict):
            self.untracked_models = self._normalize_model_list(data, "untracked_models")
            self.tracked_models = self._normalize_model_list(data, "tracked_models")
            self.tracked_models_defined = "tracked_models" in data

    def structure_is_defined(self):
        return self._structure_defined

    @classmethod
    def load_untracked_models_globally(cls, path: str):
        """
        Loads untracked models from a YAML file and updates the static
        class variable.
        """
        data = cls._load_yaml_data(path)
        cls._load_untracked_models_globally_from_data(data)

    @classmethod
    def _load_untracked_models_globally_from_data(cls, data):
        if not isinstance(data, dict):
            cls.UNTRACKED_MODELS = []
            logger.debug("'untracked_models' not found in the YAML file.")
            return

        if "untracked_models" not in data:
            cls.UNTRACKED_MODELS = []
            if "tracked_models" in data:
                logger.debug(
                    "'tracked_models' found in the YAML file. "
                    "Global UNTRACKED_MODELS cannot be resolved until model discovery."
                )
            else:
                logger.debug("'untracked_models' not found in the YAML file.")
            return

        cls.UNTRACKED_MODELS = cls._normalize_model_list(data, "untracked_models")
        logger.debug(f"UNTRACKED_MODELS loaded: {cls.UNTRACKED_MODELS}")
