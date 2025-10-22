import yaml


class ModelStructure:
    UNTRACKED_MODELS = []
    def __init__(self, path: str):
        self.path = path
        self.structure = {}
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
        except (KeyError, TypeError):
            print("Error: Structure is not defined in the model info file")
        try:
            self.styling = data["model_styling"]
        except (KeyError, TypeError):
            print("Error: Styling is not defined in the model info file")
        # Try to load the untracked models list, default to empty list if not found
        try:
            self.untracked_models = data["untracked_models"]
        except (KeyError, TypeError):
            # It's okay if this is not defined, so we don't print an error
            pass

    def structure_is_defined(self):
        return bool(self.structure)

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
            print(f"Static UNTRACKED_MODELS updated to: {cls.UNTRACKED_MODELS}")
        except (KeyError, TypeError):
            print("Warning: 'untracked_models' not found in the YAML file.")
