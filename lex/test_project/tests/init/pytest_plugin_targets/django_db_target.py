"""Needs everything `lex pytest` sets up: settings, Django, the test database.

The model import at module level fails at collection unless Django is set up before
the module is imported -- the "settings are not configured" error PyCharm showed.
"""

import os

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase

pytestmark = pytest.mark.init


class DjangoAndTestDatabase(TestCase):
    def test_settings_and_test_database(self):
        self.assertEqual(os.environ.get("DJANGO_SETTINGS_MODULE"), "lex_app.settings")
        self.assertTrue(
            connection.settings_dict["NAME"].startswith("test_"),
            f"expected the test database, got {connection.settings_dict['NAME']!r}",
        )
        User.objects.create(username="pytest-plugin-target")
        self.assertTrue(User.objects.filter(username="pytest-plugin-target").exists())
