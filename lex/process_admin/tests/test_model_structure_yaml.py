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
