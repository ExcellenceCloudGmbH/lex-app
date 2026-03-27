# import os
# import sys
# from pathlib import Path
# from unittest import TestCase
#
# import django
# from django.apps import apps
#
# sys.path.append(str(Path(__file__).resolve().parents[2]))
# os.environ.setdefault(
#     "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
# )
# if not apps.ready:
#     django.setup()
#
# from lex.process_admin.models.ModelCollection import ModelCollection
#
#
# class _ProcessAdmin:
#     def get_fields_in_table_view(self, model_class):
#         return []
#
#
# class ModelCollectionStructureTests(TestCase):
#     def test_unplaced_models_are_sorted_alphabetically(self):
#         alpha_model = type(
#             "AlphaModel",
#             (),
#             {"__module__": "Repo.Alpha.AlphaModel"},
#         )
#         beta_model = type(
#             "BetaModel",
#             (),
#             {"__module__": "Repo.Alpha.BetaModel"},
#         )
#         zeta_model = type(
#             "ZetaModel",
#             (),
#             {"__module__": "Repo.Zeta.ZetaModel"},
#         )
#
#         collection = ModelCollection(
#             {
#                 zeta_model: _ProcessAdmin(),
#                 beta_model: _ProcessAdmin(),
#                 alpha_model: _ProcessAdmin(),
#             },
#             {},
#             {},
#         )
#
#         self.assertEqual(list(collection.model_structure.keys()), ["Alpha", "Zeta"])
#         self.assertEqual(
#             list(collection.model_structure["Alpha"].keys()),
#             ["alphamodel", "betamodel"],
#         )
#
#     def test_django_contrib_models_are_not_added_to_fallback_structure(self):
#         user_model = type(
#             "User",
#             (),
#             {"__module__": "django.contrib.auth.models"},
#         )
#         app_model = type(
#             "AppModel",
#             (),
#             {"__module__": "Repo.App.AppModel"},
#         )
#
#         collection = ModelCollection(
#             {
#                 user_model: _ProcessAdmin(),
#                 app_model: _ProcessAdmin(),
#             },
#             {},
#             {},
#         )
#
#         self.assertEqual(list(collection.model_structure.keys()), ["App"])
#         self.assertEqual(
#             collection.model_structure["App"],
#             {"appmodel": None},
#         )
#
#     def test_explicit_structure_can_exclude_unmentioned_models(self):
#         alpha_model = type(
#             "AlphaModel",
#             (),
#             {"__module__": "Repo.Alpha.AlphaModel"},
#         )
#         beta_model = type(
#             "BetaModel",
#             (),
#             {"__module__": "Repo.Beta.BetaModel"},
#         )
#
#         collection = ModelCollection(
#             {
#                 alpha_model: _ProcessAdmin(),
#                 beta_model: _ProcessAdmin(),
#             },
#             {"Custom": {"alphamodel": None}},
#             {},
#             auto_include_missing_models=False,
#         )
#
#         self.assertEqual(
#             collection.model_structure,
#             {"Custom": {"alphamodel": None}},
#         )
#
#     def test_explicit_structure_skips_legacy_archive_autofolder(self):
#         alpha_model = type(
#             "AlphaModel",
#             (),
#             {"__module__": "Repo.Alpha.AlphaModel"},
#         )
#         archive_model = type(
#             "ArchiveModel",
#             (),
#             {
#                 "__module__": "Repo.Legacy.ArchiveModel",
#                 "_is_dynamic_legacy_archive": True,
#             },
#         )
#
#         collection = ModelCollection(
#             {
#                 alpha_model: _ProcessAdmin(),
#                 archive_model: _ProcessAdmin(),
#             },
#             {"Custom": {"alphamodel": None}},
#             {},
#             auto_include_missing_models=False,
#         )
#
#         self.assertEqual(
#             collection.model_structure,
#             {"Custom": {"alphamodel": None}},
#         )
