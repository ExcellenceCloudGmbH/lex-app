from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest import TestCase

from lex.process_admin.utils.model_structure_builder import ModelStructureBuilder
from lex.process_admin.utils.model_structure import ModelStructure


class ModelStructureYamlTests(TestCase):
    def test_empty_model_structure_is_still_considered_defined(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("model_structure: {}\n")
            path = Path(handle.name)

        try:
            structure = ModelStructure(str(path))
            self.assertTrue(structure.structure_is_defined())
            self.assertEqual(structure.structure, {})
        finally:
            path.unlink()

    def test_tracked_models_are_loaded_from_yaml(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("tracked_models:\n  - Foo\n  - bar\n")
            path = Path(handle.name)

        try:
            structure = ModelStructure(str(path))
            self.assertEqual(structure.tracked_models, ["foo", "bar"])
            self.assertEqual(structure.untracked_models, [])
            self.assertTrue(structure.history_tracking_enabled)
        finally:
            path.unlink()

    def test_missing_history_tracking_flag_defaults_to_enabled(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("model_structure: {}\n")
            path = Path(handle.name)

        try:
            builder = ModelStructureBuilder()
            builder.extract_from_yaml(str(path))

            self.assertTrue(builder.history_tracking_enabled)
        finally:
            path.unlink()

    def test_history_tracking_can_be_disabled_in_yaml(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("history_tracking_enabled: false\n")
            path = Path(handle.name)

        try:
            structure = ModelStructure(str(path))
            builder = ModelStructureBuilder()
            builder.extract_from_yaml(str(path))

            self.assertFalse(structure.history_tracking_enabled)
            self.assertFalse(builder.history_tracking_enabled)
        finally:
            path.unlink()

    def test_blank_history_tracking_flag_defaults_to_enabled(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("history_tracking_enabled:\n")
            path = Path(handle.name)

        try:
            structure = ModelStructure(str(path))
            self.assertTrue(structure.history_tracking_enabled)
        finally:
            path.unlink()

    def test_history_tracking_flag_must_be_boolean(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write('history_tracking_enabled: "false"\n')
            path = Path(handle.name)

        try:
            with self.assertRaisesRegex(
                ValueError,
                "'history_tracking_enabled' must be defined as a boolean",
            ):
                ModelStructure(str(path))
        finally:
            path.unlink()

    def test_tracked_models_resolve_to_inverse_untracked_models(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("tracked_models:\n  - Foo\n")
            path = Path(handle.name)

        try:
            builder = ModelStructureBuilder()
            builder.extract_from_yaml(str(path))

            self.assertEqual(
                builder.resolve_untracked_models(["Foo", "Bar", "Baz"]),
                ["bar", "baz"],
            )
        finally:
            path.unlink()

    def test_empty_tracked_models_marks_all_discovered_models_as_untracked(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("tracked_models: []\n")
            path = Path(handle.name)

        try:
            builder = ModelStructureBuilder()
            builder.extract_from_yaml(str(path))

            self.assertEqual(
                builder.resolve_untracked_models(["Foo", "Bar"]),
                ["foo", "bar"],
            )
        finally:
            path.unlink()

    def test_blank_tracked_models_defaults_to_tracking_everything(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("tracked_models:\n")
            path = Path(handle.name)

        try:
            builder = ModelStructureBuilder()
            builder.extract_from_yaml(str(path))

            self.assertFalse(builder.tracked_models_defined)
            self.assertEqual(
                builder.resolve_untracked_models(["Foo", "Bar"]),
                [],
            )
        finally:
            path.unlink()

    def test_blank_untracked_models_defaults_to_tracking_everything(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("untracked_models:\n")
            path = Path(handle.name)

        try:
            structure = ModelStructure(str(path))
            self.assertEqual(structure.untracked_models, [])
        finally:
            path.unlink()

    def test_blank_tracked_models_does_not_conflict_with_untracked_models(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(
                "tracked_models:\n"
                "untracked_models:\n"
                "  - bar\n"
            )
            path = Path(handle.name)

        try:
            structure = ModelStructure(str(path))
            self.assertEqual(structure.untracked_models, ["bar"])
            self.assertFalse(structure.tracked_models_defined)
        finally:
            path.unlink()

    def test_tracked_and_untracked_models_cannot_be_defined_together(self):
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(
                "tracked_models:\n"
                "  - foo\n"
                "untracked_models:\n"
                "  - bar\n"
            )
            path = Path(handle.name)

        try:
            with self.assertRaisesRegex(
                ValueError,
                "cannot define both 'untracked_models' and 'tracked_models'",
            ):
                ModelStructure(str(path))
        finally:
            path.unlink()


class ModelStructureDictFormatTests(TestCase):
    """
    Verify dict-format ``untracked_models`` / ``tracked_models`` parsing.

    Customers commonly write YAML dicts (``key: null``) instead of lists.
    The framework must accept both.
    """

    def test_untracked_models_dict_format(self):
        """Dict-format ``untracked_models`` is accepted and lowercased."""
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(
                "model_structure:\n  G:\n    a: null\n"
                "untracked_models:\n  Cashflow: null\n  Valuation: null\n"
            )
            path = Path(handle.name)

        try:
            ms = ModelStructure(str(path))
            self.assertEqual(sorted(ms.untracked_models), ["cashflow", "valuation"])
        finally:
            path.unlink()

    def test_tracked_models_dict_format(self):
        """Dict-format ``tracked_models`` is accepted and lowercased."""
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("tracked_models:\n  Foo: null\n  Bar: null\n")
            path = Path(handle.name)

        try:
            ms = ModelStructure(str(path))
            self.assertEqual(sorted(ms.tracked_models), ["bar", "foo"])
            self.assertTrue(ms.tracked_models_defined)
        finally:
            path.unlink()

    def test_global_untracked_from_dict(self):
        """Dict-format populates class-level ``UNTRACKED_MODELS``."""
        original = list(ModelStructure.UNTRACKED_MODELS)
        try:
            data = {"untracked_models": {"Alpha": None, "Beta": None}}
            ModelStructure._load_untracked_models_globally_from_data(data)
            self.assertEqual(sorted(ModelStructure.UNTRACKED_MODELS), ["alpha", "beta"])
        finally:
            ModelStructure.UNTRACKED_MODELS = original

    def test_full_yaml_with_dict_untracked(self):
        """End-to-end: YAML with dict-format untracked_models parses correctly."""
        yaml_content = (
            "model_structure:\n"
            "  Portfolio:\n"
            "    investor: null\n"
            "untracked_models:\n"
            "  investor: null\n"
            "history_tracking_enabled: true\n"
        )
        with NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(yaml_content)
            path = Path(handle.name)

        try:
            ms = ModelStructure(str(path))
            self.assertIn("Portfolio", ms.structure)
            self.assertEqual(ms.untracked_models, ["investor"])
            self.assertTrue(ms.history_tracking_enabled)
        finally:
            path.unlink()

    def test_normalize_model_list_rejects_scalar(self):
        """A bare scalar (int) raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            ModelStructure._normalize_model_list(42, "tracked_models")
        self.assertIn("tracked_models", str(cm.exception))

