import os
import sys
import tempfile
import yaml
from types import ModuleType
from unittest.mock import patch
from django.test import SimpleTestCase

from lex.lex_app.model_utils.ModelStructureBuilder import ModelStructureBuilder
from lex.lex_app.model_utils.ModelStructure import ModelStructure


class ModelStructureBuilderTests(SimpleTestCase):
    def setUp(self):
        self.builder = ModelStructureBuilder(repo="myrepo")

    def _write_yaml(self, content: dict) -> str:
        """
        Helper to write a dict as YAML to a temp file and return its path.
        """
        tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
        yaml.safe_dump(content, tmp)
        tmp.flush()
        tmp.close()
        return tmp.name

    # === Tests for extract_from_yaml using the real ModelStructure ===

    def test_extract_from_yaml_real_model_structure(self):
        data = {
            "model_structure": {"Foo": {"foo": None}},
            "model_styling": {"Foo": {"name": "My Foo"}},
        }
        path = self._write_yaml(data)
        try:
            self.builder.extract_from_yaml(path)
            self.assertEqual(self.builder.model_structure, data["model_structure"])
            self.assertEqual(self.builder.model_styling, data["model_styling"])
        finally:
            os.unlink(path)

    def test_extract_from_yaml_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self.builder.extract_from_yaml("nonexistent_file.yaml")

    def test_extract_from_yaml_invalid_extension(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp.close()
        try:
            with self.assertRaises(ValueError):
                self.builder.extract_from_yaml(tmp.name)
        finally:
            os.unlink(tmp.name)

    def test_extract_from_yaml_missing_structure_key(self):
        data = {
            "model_styling": {"X": {"name": "X"}},
        }
        path = self._write_yaml(data)
        try:
            with patch("builtins.print") as mock_print:
                self.builder.extract_from_yaml(path)
                self.assertEqual(self.builder.model_styling, data["model_styling"])
                self.assertEqual(self.builder.model_structure, {})
                mock_print.assert_any_call(
                    "Error: Structure is not defined in the model info file"
                )
        finally:
            os.unlink(path)

    def test_extract_from_yaml_missing_styling_key(self):
        data = {
            "model_structure": {"Y": {"y": None}},
        }
        path = self._write_yaml(data)
        try:
            with patch("builtins.print") as mock_print:
                self.builder.extract_from_yaml(path)
                self.assertEqual(self.builder.model_structure, data["model_structure"])
                self.assertEqual(self.builder.model_styling, {})
                mock_print.assert_any_call(
                    "Error: Styling is not defined in the model info file"
                )
        finally:
            os.unlink(path)

    # === Tests for ModelStructure.structure_is_defined ===

    def test_model_structure_structure_is_defined(self):
        # when structure key present and non-empty
        data = {"model_structure": {"A": {"a": None}}}
        path1 = self._write_yaml(data)
        # when model_structure missing or empty
        data2 = {}
        path2 = self._write_yaml(data2)
        try:
            ms1 = ModelStructure(path1)
            self.assertTrue(ms1.structure_is_defined())
            ms2 = ModelStructure(path2)
            self.assertFalse(ms2.structure_is_defined())
        finally:
            os.unlink(path1)
            os.unlink(path2)

    # === Tests for extract_and_save_structure ===

    def test_extract_and_save_structure_success(self):
        module_name = "dummy_mod"
        mod = ModuleType(module_name)
        mod.get_model_structure = lambda: {"a": 1}
        mod.get_widget_structure = lambda: ["w"]
        mod.get_model_styling = lambda: {"s": 2}
        sys.modules[module_name] = mod
        try:
            self.builder.extract_and_save_structure(module_name)
            self.assertEqual(self.builder.model_structure, {"a": 1})
            self.assertEqual(self.builder.widget_structure, ["w"])
            self.assertEqual(self.builder.model_styling, {"s": 2})
        finally:
            del sys.modules[module_name]

    def test_extract_and_save_structure_import_error(self):
        with self.assertRaises(ImportError):
            self.builder.extract_and_save_structure("nonexistent_mod")

    def test_extract_and_save_structure_missing_methods(self):
        module_name = "partial_mod"
        mod = ModuleType(module_name)
        sys.modules[module_name] = mod
        try:
            with patch("builtins.print") as mock_print:
                self.builder.extract_and_save_structure(module_name)
                mock_print.assert_any_call(
                    f"Warning: get_model_structure not found in {module_name}"
                )
                mock_print.assert_any_call(
                    f"Warning: get_widget_structure not found in {module_name}"
                )
                mock_print.assert_any_call(
                    f"Warning: get_model_styling not found in {module_name}"
                )
        finally:
            del sys.modules[module_name]

    def test_extract_and_save_structure_method_raises(self):
        # Create a fake module whose getters all raise
        module_name = "error_mod"
        mod = ModuleType(module_name)

        def bad():
            raise RuntimeError("oops")

        mod.get_model_structure = bad
        mod.get_widget_structure = bad
        mod.get_model_styling = bad
        sys.modules[module_name] = mod

        try:
            with patch("builtins.print") as mock_print:
                # Should catch each RuntimeError and print an error
                self.builder.extract_and_save_structure(module_name)

                mock_print.assert_any_call("Error calling get_model_structure: oops")
                mock_print.assert_any_call("Error calling get_widget_structure: oops")
                mock_print.assert_any_call("Error calling get_model_styling: oops")

            # None of the attributes should have been set
            self.assertEqual(self.builder.model_structure, {})
            self.assertEqual(self.builder.widget_structure, [])
            self.assertEqual(self.builder.model_styling, {})

        finally:
            del sys.modules[module_name]

    # === Test for get_extracted_structures ===

    def test_get_extracted_structures(self):
        self.builder.model_structure = {"m": 1}
        self.builder.widget_structure = ["w"]
        self.builder.model_styling = {"s": 2}
        out = self.builder.get_extracted_structures()
        self.assertEqual(
            out,
            {
                "model_structure": {"m": 1},
                "widget_structure": ["w"],
                "model_styling": {"s": 2},
            },
        )

    # === Tests for internal helpers ===

    def test__get_model_path_normal(self):
        p = self.builder._get_model_path("myrepo.sub.app.mod")
        self.assertEqual(p, "sub.app")

    def test__get_model_path_not_found(self):
        with patch("builtins.print") as mock_print:
            res = self.builder._get_model_path("otherrepo.sub.mod")
            self.assertIsNone(res)
            mock_print.assert_called_with("Path: otherrepo.sub.mod")

    def test__insert_model_to_structure(self):
        self.builder.model_structure = {}
        self.builder._insert_model_to_structure("x.y", "name")
        self.assertEqual(self.builder.model_structure, {"x": {"y": {"name": None}}})

    # === Tests for _add_reports_to_structure ===

    def test__add_reports_to_structure_without_streamlit(self):
        self.builder.model_structure = {}
        os.environ.pop("IS_STREAMLIT_ENABLED", None)
        self.builder._add_reports_to_structure()
        self.assertIn("Z_Reports", self.builder.model_structure)
        self.assertNotIn("Streamlit", self.builder.model_structure)

    def test__add_reports_to_structure_with_streamlit(self):
        self.builder.model_structure = {}
        os.environ["IS_STREAMLIT_ENABLED"] = "true"
        try:
            self.builder._add_reports_to_structure()
            self.assertIn("Z_Reports", self.builder.model_structure)
            self.assertIn("Streamlit", self.builder.model_structure)
        finally:
            os.environ.pop("IS_STREAMLIT_ENABLED", None)

    # === Tests for build_structure ===

    def test_build_structure_filters_by_repo_and_adds_only_matching(self):
        class ModelA:
            pass

        ModelA.__module__ = "myrepo.alpha.beta"

        class ModelB:
            pass

        ModelB.__module__ = "otherrepo.alpha.beta"

        models = {"A": ModelA, "B": ModelB}
        out = self.builder.build_structure(models)

        # we only get the 'alpha' segment (beta is the module name and is dropped),
        # and the model name 'A' becomes 'a' under that:
        self.assertIn("alpha", out)
        self.assertEqual(out["alpha"]["a"], None)

        # never include the otherrepo model
        self.assertNotIn("otherrepo", out)

        # always adds Z_Reports
        self.assertEqual(out["Z_Reports"], {"calculationlog": None})

    def test_build_structure_includes_streamlit_when_enabled(self):
        os.environ["IS_STREAMLIT_ENABLED"] = "true"
        try:

            class M:
                pass

            M.__module__ = "myrepo.x.y"
            out = ModelStructureBuilder(repo="myrepo").build_structure({"m": M})

            # path 'x.y' → _get_model_path gives 'x', so 'm' lives under 'x'
            self.assertIn("x", out)
            self.assertEqual(out["x"]["m"], None)

            self.assertIn("Z_Reports", out)
            self.assertIn("Streamlit", out)
        finally:
            os.environ.pop("IS_STREAMLIT_ENABLED", None)
