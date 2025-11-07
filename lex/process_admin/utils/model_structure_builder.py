import importlib
import os
from typing import Dict

from lex.process_admin.utils.model_structure import ModelStructure


class ModelStructureBuilder:
    def __init__(self, repo: str = ""):
        self.repo = repo
        self.model_structure = {}
        self.model_styling = {}
        self.widget_structure = []
        self.untracked_models = []

    def extract_from_yaml(self, path: str):
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")

        if not path.endswith(".yaml"):
            raise ValueError(f"Invalid file format: {path}")

        info = ModelStructure(path)
        self.model_structure = info.structure
        self.model_styling = info.styling
        self.untracked_models = info.untracked_models

    def extract_and_save_structure(self, full_module_name: str) -> None:
        try:
            module = importlib.import_module(full_module_name)
        except ImportError as e:
            raise ImportError(f"Failed to import module {full_module_name}: {e}")

        structure_methods = {
            "model_structure": "get_model_structure",
            "widget_structure": "get_widget_structure",
            "model_styling": "get_model_styling",
        }

        for attr, method_name in structure_methods.items():
            if hasattr(module, method_name):
                try:
                    setattr(self, attr, getattr(module, method_name)())
                except Exception as e:
                    print(f"Error calling {method_name}: {e}")
            else:
                print(f"Warning: {method_name} not found in {full_module_name}")

    def get_extracted_structures(self):
        return {
            "model_structure": self.model_structure,
            "widget_structure": self.widget_structure,
            "model_styling": self.model_styling,
            "untracked_models": self.untracked_models,
        }

    def build_structure(self, models) -> Dict:
        # TODO: Filter models by repo
        for model_name, model in models.items():
            if self.repo not in model.__module__:
                continue
            path = self._get_model_path(model.__module__)
            self._insert_model_to_structure(path, str(model_name).lower())

        self._add_reports_to_structure()
        return self.model_structure

    def _get_model_path(self, path) -> str:
        try:
            module_parts = path.split(".")
            repo_index = module_parts.index(self.repo)
            return ".".join(module_parts[repo_index + 1 : -1])
        except ValueError as e:
            print(f"Path: {path}")

    def _insert_model_to_structure(self, path: str, name: str):
        current = self.model_structure
        for p in path.split("."):
            if p not in current:
                current[p] = {}
            current = current[p]
        current[name] = None

    def _add_reports_to_structure(self):
        self.model_structure["Z_Reports"] = {"calculationlog": None}
        if os.getenv("IS_STREAMLIT_ENABLED") == "true":
            self.model_structure["Streamlit"] = {"streamlit": None}
