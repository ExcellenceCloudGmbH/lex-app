import importlib
import os
import copy
from typing import Dict, Set

from lex.process_admin.utils.model_structure import ModelStructure


class ModelStructureBuilder:
    def __init__(self, repo: str = "", predefined_structure: Dict = None):
        self.repo = repo
        # Store the raw predefined structure
        self.predefined_structure = predefined_structure or {}

        # Initialize active structure as a deep copy of predefined
        # This ensures defaults exist even if no YAML is loaded.
        self.model_structure = copy.deepcopy(self.predefined_structure)

        self.model_styling = {}
        self.widget_structure = []
        self.untracked_models = []

    def extract_from_yaml(self, path: str):
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")

        if not path.endswith(".yaml"):
            raise ValueError(f"Invalid file format: {path}")

        info = ModelStructure(path)

        # 1. Capture YAML Data
        yaml_structure = info.structure
        yaml_styling = info.styling
        self.untracked_models = info.untracked_models

        # 2. Merge YAML structure into Predefined structure (with override logic)
        self.model_structure = self.merge_predefined_and_yaml(self.predefined_structure, yaml_structure)

        # 3. Merge Styling (YAML updates existing styling)
        self.model_styling.update(yaml_styling)

    @staticmethod
    def merge_predefined_and_yaml(predefined: Dict, yaml_data: Dict) -> Dict:
        """
        Merges YAML structure on top of Predefined structure.
        Crucially: If a model (leaf node) exists in YAML, it is REMOVED from its 
        original location in Predefined to allow the YAML to 'move' it.
        """
        # Start with a clean copy of predefined so we don't mutate the source
        base = copy.deepcopy(predefined)

        # A. Identify all models defined in the YAML (The source of truth for location)
        yaml_leaves = ModelStructureBuilder._get_all_leaves(yaml_data)

        # B. Remove those models from the base structure (and clean up empty parents)
        ModelStructureBuilder._prune_structure(base, yaml_leaves)

        # C. Deep merge YAML into the pruned base
        return ModelStructureBuilder._deep_merge(base, yaml_data)

    @staticmethod
    def _get_all_leaves(d: Dict) -> Set[str]:
        """Recursively finds all leaf keys (models) where value is None."""
        leaves = set()
        for k, v in d.items():
            if v is None:
                leaves.add(k)
            elif isinstance(v, dict):
                leaves.update(ModelStructureBuilder._get_all_leaves(v))
        return leaves

    @staticmethod
    def _prune_structure(d: Dict, targets: Set[str]):
        """
        Recursively removes keys present in 'targets'.
        Also removes directories that become empty after pruning.
        """
        keys_to_remove = []
        for k, v in d.items():
            if v is None:
                # If this model is defined in YAML (targets), mark for removal from here
                if k in targets:
                    keys_to_remove.append(k)
            elif isinstance(v, dict):
                ModelStructureBuilder._prune_structure(v, targets)
                # If the folder is now empty, mark it for deletion
                if not v:
                    keys_to_remove.append(k)

        for k in keys_to_remove:
            del d[k]

    @staticmethod
    def _deep_merge(base: Dict, update: Dict) -> Dict:
        """Recursively merges dictionary 'update' into 'base'."""
        for k, v in update.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                ModelStructureBuilder._deep_merge(base[k], v)
            else:
                base[k] = v
        return base

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
            return ".".join(module_parts[repo_index + 1: -1])
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
        # Note: This logic adds direct dict assignments. 
        # If you want these to respect Predefined vs YAML logic, ensure they 
        # are in the predefined structure passed to __init__ instead of hardcoded here.
        if "Z_Reports" not in self.model_structure:
            self.model_structure["Z_Reports"] = {}
        self.model_structure["Z_Reports"]["calculationlog"] = None

        if os.getenv("IS_STREAMLIT_ENABLED") == "true":
            if "Streamlit" not in self.model_structure:
                self.model_structure["Streamlit"] = {}
            self.model_structure["Streamlit"]["streamlit"] = None