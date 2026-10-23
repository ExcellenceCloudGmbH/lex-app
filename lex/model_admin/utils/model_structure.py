import logging

import yaml

logger = logging.getLogger(__name__)


class ModelStructure:
    UNTRACKED_MODELS = []
    DEFAULT_HISTORY_TRACKING_ENABLED = True

    def __init__(self, path: str):
        self.path = path
        self.structure = {}
        self._structure_defined = False
        self.styling = {}
        self.untracked_models = []
        self.tracked_models = []
        self.tracked_models_defined = False
        self.history_tracking_enabled = self.DEFAULT_HISTORY_TRACKING_ENABLED

        data = self._load_yaml_data(self.path)
        self._load_info(data)

        ModelStructure._load_untracked_models_globally_from_data(data)

    @staticmethod
    def _is_model_list_defined(values):
        return values not in (None, "", {}) and not isinstance(values, bool)

    @staticmethod
    def _normalize_model_list(values, key):
        if not ModelStructure._is_model_list_defined(values):
            return []
        # Accept list, dict, and bare-string formats so that all of
        # the following YAML forms work:
        #   untracked_models:          (list)
        #     - mymodel
        #   untracked_models:          (dict)
        #     mymodel: null
        #   untracked_models: mymodel  (bare string)
        if isinstance(values, dict):
            values = list(values.keys())
        elif isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            raise ValueError(f"'{key}' must be defined as a list in model_structure.yaml.")
        return [str(value).lower() for value in values]

    @classmethod
    def _get_model_list(cls, data, key):
        if key not in data:
            return [], False

        values = data.get(key)
        if not cls._is_model_list_defined(values):
            return [], False

        return cls._normalize_model_list(values, key), True

    @classmethod
    def _get_history_tracking_enabled(cls, data):
        if not isinstance(data, dict):
            return cls.DEFAULT_HISTORY_TRACKING_ENABLED

        value = data.get("history_tracking_enabled")
        if value is None:
            return cls.DEFAULT_HISTORY_TRACKING_ENABLED
        if not isinstance(value, bool):
            raise ValueError(
                "'history_tracking_enabled' must be defined as a boolean in "
                "model_structure.yaml."
            )
        return value

    @classmethod
    def _load_yaml_data(cls, path: str):
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict):
            return data

        if (
            cls._is_model_list_defined(data.get("untracked_models"))
            and cls._is_model_list_defined(data.get("tracked_models"))
        ):
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
            self.untracked_models, _ = self._get_model_list(data, "untracked_models")
            self.tracked_models, self.tracked_models_defined = self._get_model_list(
                data, "tracked_models"
            )
            self.history_tracking_enabled = self._get_history_tracking_enabled(data)

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

        cls.UNTRACKED_MODELS = cls._normalize_model_list(
            data.get("untracked_models"), "untracked_models"
        )
        logger.debug(f"UNTRACKED_MODELS loaded: {cls.UNTRACKED_MODELS}")
