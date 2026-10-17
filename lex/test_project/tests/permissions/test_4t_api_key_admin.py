"""Scoped API keys can be managed in the Django admin.

Intent
------
The docs tell operators to revoke a scoped key (``LexAPIKey``) in the Django
admin, but lex disables admin autodiscovery, so nothing registered it. The
authentication app now registers it at startup with the user column and a
revoked filter; registering twice is harmless, and the classic keys of
djangorestframework-api-key are registered along with it.

Cluster 4t — scenario 4.117. Type: U.
Covers: lex/authentication/admin.py (register_api_key_admin), lex/authentication/apps.py (ready).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4t_api_key_admin.py -v
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.test import SimpleTestCase
from rest_framework_api_key.models import APIKey

from lex.authentication.admin import register_api_key_admin
from lex.authentication.models import LexAPIKey

pytestmark = pytest.mark.permissions


class TestCluster04t_ApiKeyAdmin(SimpleTestCase):
    """Cluster 4t: LexAPIKey in the admin."""

    # -- 4.117 ---------------------------------------------------------
    def test_4_117_scoped_keys_are_registered_in_the_admin(self) -> None:
        """
        Scenario 4.117: operators find scoped keys in the admin.
        Given: lex started (the authentication app's ready() ran), and a fresh admin site
        When: the default site is inspected and register_api_key_admin() runs twice on the fresh one
        Then: LexAPIKey is registered on both with the user column and a revoked
              filter; the second call changes nothing; classic keys are registered too.
        """
        self.assertTrue(admin.site.is_registered(LexAPIKey))
        self.assertTrue(admin.site.is_registered(APIKey))
        site = AdminSite(name="cluster4t")
        register_api_key_admin(site)
        first = site._registry[LexAPIKey]
        register_api_key_admin(site)
        self.assertIs(site._registry[LexAPIKey], first)
        self.assertIn("user", first.list_display)
        self.assertIn("revoked", first.list_filter)
        self.assertIn("user__username", first.search_fields)
