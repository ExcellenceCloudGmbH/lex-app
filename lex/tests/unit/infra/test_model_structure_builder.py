"""
Tests for ``lex.process_admin.utils.model_structure_builder`` — the YAML/
predefined model-structure tree builder.

Covers:
- __init__ defaults
- extract_from_yaml: file-not-found, non-yaml, valid YAML
- merge_predefined_and_yaml: leaf-move, order preservation
- _get_all_leaves / _prune_structure / _deep_merge
- build_structure / _get_model_path / _insert_model_to_structure
- _add_reports_to_structure (Z_Reports + Streamlit)
- resolve_untracked_models
"""

import os
from copy import deepcopy
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.process_admin.utils.model_structure_builder import ModelStructureBuilder


class InitDefaultsTest(SimpleTestCase):

    def test_defaults(self):
        b = ModelStructureBuilder()
        self.assertEqual(b.repo, "")
        self.assertEqual(b.model_structure, {})
        self.assertEqual(b.model_styling, {})
        self.assertEqual(b.widget_structure, [])
        self.assertEqual(b.untracked_models, [])
        self.assertTrue(b.history_tracking_enabled)
        self.assertFalse(b.model_structure_is_defined_in_yaml)
        self.assertFalse(b.model_structure_is_explicitly_defined)

    def test_predefined_structure_deep_copied(self):
        original = {"Folder": {"model_a": None}}
        b = ModelStructureBuilder(predefined_structure=original)
        b.model_structure["Folder"]["model_b"] = None
        # Original must not be mutated
        self.assertNotIn("model_b", original["Folder"])


class ExtractFromYamlTest(SimpleTestCase):

    def test_file_not_found(self):
        b = ModelStructureBuilder()
        with self.assertRaises(FileNotFoundError):
            b.extract_from_yaml("/nonexistent/path.yaml")

    def test_non_yaml_file(self):
        b = ModelStructureBuilder()
        # Use a real existing file that's not .yaml
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            tmp_path = f.name
        try:
            with self.assertRaises(ValueError):
                b.extract_from_yaml(tmp_path)
        finally:
            os.unlink(tmp_path)


class GetAllLeavesTest(SimpleTestCase):

    def test_flat(self):
        d = {"a": None, "b": None}
        self.assertEqual(ModelStructureBuilder._get_all_leaves(d), {"a", "b"})

    def test_nested(self):
        d = {"Folder": {"x": None, "Sub": {"y": None}}}
        self.assertEqual(ModelStructureBuilder._get_all_leaves(d), {"x", "y"})

    def test_empty(self):
        self.assertEqual(ModelStructureBuilder._get_all_leaves({}), set())


class PruneStructureTest(SimpleTestCase):

    def test_removes_target_leaves(self):
        d = {"a": None, "b": None, "c": None}
        ModelStructureBuilder._prune_structure(d, {"a", "c"})
        self.assertEqual(d, {"b": None})

    def test_removes_empty_folders(self):
        d = {"Folder": {"a": None}, "Other": {"b": None}}
        ModelStructureBuilder._prune_structure(d, {"a"})
        self.assertNotIn("Folder", d)
        self.assertIn("Other", d)

    def test_no_targets_no_change(self):
        d = {"a": None, "b": None}
        original = deepcopy(d)
        ModelStructureBuilder._prune_structure(d, set())
        self.assertEqual(d, original)


class DeepMergeTest(SimpleTestCase):

    def test_simple_merge(self):
        base = {"a": None}
        update = {"b": None}
        result = ModelStructureBuilder._deep_merge(base, update)
        self.assertEqual(result, {"a": None, "b": None})

    def test_no_override_existing(self):
        base = {"a": "original"}
        update = {"a": "overwrite"}
        result = ModelStructureBuilder._deep_merge(base, update)
        self.assertEqual(result["a"], "original")

    def test_recursive_dict_merge(self):
        base = {"F": {"a": None}}
        update = {"F": {"b": None}}
        result = ModelStructureBuilder._deep_merge(base, update)
        self.assertEqual(result, {"F": {"a": None, "b": None}})


class MergePredefinedAndYamlTest(SimpleTestCase):

    def test_yaml_moves_model(self):
        """Model in predefined/Old is moved to yaml/New."""
        predefined = {"Old": {"model_a": None, "model_b": None}}
        yaml_data = {"New": {"model_a": None}}

        result = ModelStructureBuilder.merge_predefined_and_yaml(predefined, yaml_data)

        self.assertIn("New", result)
        self.assertIn("model_a", result["New"])
        # model_a removed from Old
        if "Old" in result:
            self.assertNotIn("model_a", result["Old"])
        # model_b preserved
        self.assertIn("model_b", result.get("Old", {}))

    def test_yaml_order_preserved(self):
        """YAML items come first in the result."""
        predefined = {"Z": {"z_model": None}}
        yaml_data = {"A": {"a_model": None}}

        result = ModelStructureBuilder.merge_predefined_and_yaml(predefined, yaml_data)
        keys = list(result.keys())
        self.assertEqual(keys[0], "A")


class BuildStructureTest(SimpleTestCase):

    def test_build_inserts_model(self):
        b = ModelStructureBuilder(repo="myrepo")
        model = MagicMock()
        model.__module__ = "myrepo.models.funds"
        models_dict = {"fund": model}

        b.build_structure(models_dict)
        self.assertIn("models", b.model_structure)
        self.assertIn("fund", b.model_structure["models"])

    def test_build_skips_wrong_repo(self):
        b = ModelStructureBuilder(repo="myrepo")
        model = MagicMock()
        model.__module__ = "other_repo.models.funds"

        b.build_structure({"fund": model})
        self.assertEqual(b.model_structure, {"Z_Reports": {"calculationlog": None}})

    def test_get_model_path(self):
        b = ModelStructureBuilder(repo="myrepo")
        path = b._get_model_path("myrepo.models.funds")
        self.assertEqual(path, "models")

    def test_get_model_path_deep(self):
        b = ModelStructureBuilder(repo="myrepo")
        path = b._get_model_path("myrepo.finance.models.accounts")
        self.assertEqual(path, "finance.models")

    def test_insert_model_to_structure(self):
        b = ModelStructureBuilder()
        b._insert_model_to_structure("finance.reports", "quarterly")
        self.assertIn("finance", b.model_structure)
        self.assertIn("reports", b.model_structure["finance"])
        self.assertIsNone(b.model_structure["finance"]["reports"]["quarterly"])


class AddReportsToStructureTest(SimpleTestCase):

    def test_adds_z_reports(self):
        b = ModelStructureBuilder()
        b._add_reports_to_structure()
        self.assertIn("Z_Reports", b.model_structure)
        self.assertIsNone(b.model_structure["Z_Reports"]["calculationlog"])

    @patch.dict(os.environ, {"IS_STREAMLIT_ENABLED": "true"})
    def test_adds_streamlit_when_enabled(self):
        b = ModelStructureBuilder()
        b._add_reports_to_structure()
        self.assertIn("Streamlit", b.model_structure)
        self.assertIsNone(b.model_structure["Streamlit"]["streamlit"])

    @patch.dict(os.environ, {"IS_STREAMLIT_ENABLED": "false"})
    def test_no_streamlit_when_disabled(self):
        b = ModelStructureBuilder()
        b._add_reports_to_structure()
        self.assertNotIn("Streamlit", b.model_structure)


class ResolveUntrackedModelsTest(SimpleTestCase):

    def test_no_tracked_defined_returns_untracked_list(self):
        b = ModelStructureBuilder()
        b.untracked_models = ["model_a", "model_b"]
        b.tracked_models_defined = False
        result = b.resolve_untracked_models(["model_a", "model_b", "model_c"])
        self.assertEqual(result, ["model_a", "model_b"])

    def test_tracked_defined_returns_complement(self):
        b = ModelStructureBuilder()
        b.tracked_models = ["model_a"]
        b.tracked_models_defined = True
        result = b.resolve_untracked_models(["Model_A", "Model_B", "Model_C"])
        self.assertNotIn("model_a", result)
        self.assertIn("model_b", result)
        self.assertIn("model_c", result)


class GetExtractedStructuresTest(SimpleTestCase):

    def test_returns_all_keys(self):
        b = ModelStructureBuilder()
        result = b.get_extracted_structures()
        self.assertIn("model_structure", result)
        self.assertIn("widget_structure", result)
        self.assertIn("model_styling", result)
        self.assertIn("untracked_models", result)
        self.assertIn("tracked_models", result)
        self.assertIn("history_tracking_enabled", result)
