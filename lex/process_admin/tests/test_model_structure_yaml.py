from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest import TestCase

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
