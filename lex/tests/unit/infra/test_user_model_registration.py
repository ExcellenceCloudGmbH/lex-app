from unittest.mock import patch

from django.test import TestCase
from django.urls import converters as django_converters
from django.urls.converters import REGISTERED_CONVERTERS

from lex.process_admin.settings import processAdminSite

_real_register_converter = django_converters.register_converter


def _idempotent_register_converter(converter, type_name):
    """Allow re-registration so that processAdminSite.urls is safe in tests."""
    REGISTERED_CONVERTERS.pop(type_name, None)
    return _real_register_converter(converter, type_name)


class UserModelRegistrationTests(TestCase):
    def test_user_model_is_registered_and_in_model_collection(self):
        """The built-in Django User model is registered and discoverable."""
        processAdminSite.initialized = False
        processAdminSite.model_collection = None

        with patch(
            "lex.process_admin.sites.process_admin_site.register_converter",
            new=_idempotent_register_converter,
        ):
            _ = processAdminSite.urls
        model_collection = processAdminSite.model_collection

        self.assertIsNotNone(model_collection)
        self.assertIn("user", model_collection.all_model_ids)

        # The User container must resolve to the real Django User model.
        container = model_collection.ids2containers["user"]
        from django.contrib.auth.models import User
        self.assertIs(container.model_class, User)
