from django.test import TestCase

from lex.process_admin.settings import processAdminSite


class UserModelRegistrationTests(TestCase):
    def test_user_model_is_registered_and_in_model_structure(self):
        processAdminSite.initialized = False
        processAdminSite.model_collection = None

        _ = processAdminSite.urls
        model_collection = processAdminSite.model_collection

        self.assertIsNotNone(model_collection)
        self.assertIn("user", model_collection.all_model_ids)
        self.assertIn("user", (model_collection.model_structure.get("Models") or {}))
