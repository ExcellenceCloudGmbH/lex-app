"""Scoped API keys: a key acts as a user, with its own scopes and paths.

Intent
------
A classic lex API key turns every request into a technical identity with all
scopes on all models. A scoped key (``LexAPIKey``, built on
djangorestframework-api-key) authenticates as a real user instead:
permission methods, the audit actor and the database context see that user,
and the key's ``scopes`` are what the default permission methods check. A key
works only below its ``allowed_path_prefixes`` (404 elsewhere); revoked and
expired keys are ignored; projects can accept it under additional headers
(``lex_config.API_KEY_HEADERS``) and replace the lookup
(``lex_config.API_KEY_RESOLVER``). Classic keys keep working unchanged, and a
key request never needs a CSRF token.

Cluster 4q — scenarios 4.104–4.110. Type: E.
Covers: lex/authentication/models/_api_keys.py, lex/api/utils/api_key_requests.py,
lex/authentication/middleware.py, lex/authentication/authentication_backends/api_keys.py,
lex/api/middleware/keycloak_permissions.py, lex/core/models/LexModel.py
(_resolve_keycloak_scopes "*"), lex/api/views/model_entries/filter_backends.py.
Run: python -m lex pytest lex/test_project/tests/permissions/test_4q_scoped_api_keys.py -v
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient

from lex.authentication.models import LexAPIKey
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, KEYCLOAK, KeycloakItem

pytestmark = pytest.mark.permissions


def resolve_from_test_table(raw_key):
    """API_KEY_RESOLVER used by scenario 4.109: accepts any key starting 'custom-'."""
    if raw_key.startswith("custom-"):
        user = User.objects.get(username="cluster4q_service")
        return LexAPIKey(name="resolved", user=user, scopes=["read", "list"])
    return None


class TestCluster04q_ScopedApiKeys(E2ETestCase):
    """Cluster 4q: LexAPIKey."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        self.service = User.objects.create_user(username="cluster4q_service")
        KeycloakItem.objects.create(label="visible-with-read")

    def _key(self, **kwargs):
        kwargs.setdefault("name", "cluster 4q")
        kwargs.setdefault("user", self.service)
        _, raw = LexAPIKey.objects.create_key(**kwargs)
        return raw

    def _client(self, raw, header="HTTP_API_KEY"):
        client = APIClient(enforce_csrf_checks=True)
        client.credentials(**{header: raw})
        return client

    # -- 4.104 ---------------------------------------------------------
    def test_4_104_key_scopes_drive_default_permissions(self) -> None:
        """
        Scenario 4.104: the key's scopes are what default permission methods check.
        Given: KeycloakItem (default permissions) and two keys, with and without "read"
        When: each lists the model
        Then: the read key sees the row; the other key sees none.
        """
        with_read = self._client(self._key(scopes=["read", "list"]))
        without = self._client(self._key(scopes=["list"]))
        rows = with_read.get(self.url_list(KEYCLOAK)).json()
        self.assertEqual(len(rows["results"] if isinstance(rows, dict) else rows), 1)
        rows = without.get(self.url_list(KEYCLOAK)).json()
        self.assertEqual(len(rows["results"] if isinstance(rows, dict) else rows), 0)

    # -- 4.105 ---------------------------------------------------------
    def test_4_105_key_acts_as_its_user(self) -> None:
        """
        Scenario 4.105: requests run as the key's user, and write without CSRF.
        Given: a key with create/read/list scopes for the service user
        When: it creates a KeycloakItem with a CSRF-enforcing client
        Then: the create succeeds and created_by names the service user, not
              "Technical User".
        """
        client = self._client(self._key(scopes=["create", "read", "list"]))
        response = client.post(self.url_create(KEYCLOAK), {"label": "made-by-key"}, format="json")
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        created = KeycloakItem.objects.get(label="made-by-key")
        self.assertIn("cluster4q_service", str(created.created_by))

    # -- 4.106 ---------------------------------------------------------
    def test_4_106_key_limited_to_its_paths(self) -> None:
        """
        Scenario 4.106: outside its allowed path prefixes a key finds nothing.
        Given: a key limited to api/elsewhere/
        When: it lists KeycloakItem
        Then: HTTP 404.
        """
        client = self._client(self._key(scopes=["read", "list"], allowed_path_prefixes=["api/elsewhere/"]))
        self.assertEqual(client.get(self.url_list(KEYCLOAK)).status_code, 404)

    # -- 4.107 ---------------------------------------------------------
    def test_4_107_revoked_and_expired_keys_are_ignored(self) -> None:
        """
        Scenario 4.107: revoked and expired keys authenticate nobody.
        Given: a revoked key and an expired key, both with read scope
        When: each lists KeycloakItem
        Then: neither reaches the data (no 200 with rows).
        """
        revoked = self._key(scopes=["read", "list"], revoked=True)
        expired = self._key(scopes=["read", "list"], expiry_date=timezone.now() - timedelta(days=1))
        for raw in (revoked, expired):
            response = self._client(raw).get(self.url_list(KEYCLOAK))
            if response.status_code == 200:
                body = response.json()
                self.assertEqual(len(body["results"] if isinstance(body, dict) else body), 0)

    # -- 4.108 ---------------------------------------------------------
    def test_4_108_additional_header_names(self) -> None:
        """
        Scenario 4.108: a project can accept keys under its own header.
        Given: API_KEY_HEADERS = ["HTTP_X_PROJECT_KEY"]
        When: a key is sent in X-Project-Key
        Then: it authenticates like one sent in Api-Key.
        """
        self.use_lex_extensions(API_KEY_HEADERS=["HTTP_X_PROJECT_KEY"])
        client = self._client(self._key(scopes=["read", "list"]), header="HTTP_X_PROJECT_KEY")
        body = client.get(self.url_list(KEYCLOAK)).json()
        self.assertEqual(len(body["results"] if isinstance(body, dict) else body), 1)

    # -- 4.109 ---------------------------------------------------------
    def test_4_109_project_resolver_replaces_the_lookup(self) -> None:
        """
        Scenario 4.109: API_KEY_RESOLVER decides which key a raw value is.
        Given: a resolver accepting keys that start with "custom-"
        When: such a key is sent
        Then: it authenticates as the resolver's user with its scopes.
        """
        self.use_lex_extensions(API_KEY_RESOLVER=f"{__name__}.resolve_from_test_table")
        body = self._client("custom-anything").get(self.url_list(KEYCLOAK)).json()
        self.assertEqual(len(body["results"] if isinstance(body, dict) else body), 1)

    # -- 4.110 ---------------------------------------------------------
    def test_4_110_classic_keys_unchanged(self) -> None:
        """
        Scenario 4.110: a classic djangorestframework-api-key key keeps its behaviour.
        Given: a classic APIKey (all scopes, technical identity)
        When: it fetches a KeycloakItem by id
        Then: HTTP 200 with the row's data, as before 3.0 (its all-scope
              technical identity passes the default permission_read).
        """
        item = KeycloakItem.objects.get(label="visible-with-read")
        self.authenticate_as_api_key(name="legacy integration")
        response = self.client.get(self.url_detail(KEYCLOAK, item.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["label"], "visible-with-read")
