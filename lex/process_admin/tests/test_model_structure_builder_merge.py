from unittest import TestCase

from lex.process_admin.utils.model_structure_builder import ModelStructureBuilder


class ModelStructureBuilderMergeTests(TestCase):
    def test_merge_predefined_and_yaml_keeps_yaml_items_first(self):
        predefined = {
            "Zeta": {"zeta_model": None},
            "Alpha": {"alpha_model": None},
        }
        yaml_data = {
            "Custom": {"custom_model": None},
            "Alpha": {"moved_model": None},
        }

        merged = ModelStructureBuilder.merge_predefined_and_yaml(
            predefined, yaml_data
        )

        self.assertEqual(list(merged.keys()), ["Custom", "Alpha", "Zeta"])
        self.assertEqual(
            list(merged["Alpha"].keys()),
            ["moved_model", "alpha_model"],
        )

    def test_merge_predefined_and_yaml_keeps_yaml_folder_on_conflict(self):
        predefined = {
            "Section": None,
            "Other": {"other_model": None},
        }
        yaml_data = {
            "Section": {"child_model": None},
        }

        merged = ModelStructureBuilder.merge_predefined_and_yaml(
            predefined, yaml_data
        )

        self.assertEqual(merged["Section"], {"child_model": None})
        self.assertEqual(merged["Other"], {"other_model": None})

    def test_merge_predefined_and_yaml_keeps_yaml_leaf_on_conflict(self):
        predefined = {
            "Section": {"child_model": None},
            "Other": {"other_model": None},
        }
        yaml_data = {
            "Section": None,
        }

        merged = ModelStructureBuilder.merge_predefined_and_yaml(
            predefined, yaml_data
        )

        self.assertIsNone(merged["Section"])
        self.assertEqual(merged["Other"], {"other_model": None})
