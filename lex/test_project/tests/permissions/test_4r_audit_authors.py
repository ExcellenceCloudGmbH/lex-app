"""Audit-log authors for users without names (BUG-007, scoped API keys).

Intent
------
lex renders a ``User`` as "<first> <last>". A service account — the usual
user behind a scoped API key — has neither, so audit rows used to record its
author as " ": nobody could tell who changed the data. The audit-log mixins
now fall back to the username, exactly as the model's ``created_by`` does;
a user with names is recorded as before.

Cluster 4r — scenarios 4.111–4.113. Type: E.
Covers: lex/audit_logging/mixins/AuditLogMixin.py (log_change author),
lex/audit_logging/mixins/BulkAuditLogMixin.py (log_change author, _audit_author).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4r_audit_authors.py -v
"""

from __future__ import annotations

from urllib.parse import urlencode

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.authentication.models import LexAPIKey
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, KEYCLOAK, KeycloakItem

pytestmark = pytest.mark.permissions


class TestCluster04r_AuditAuthors(E2ETestCase):
    """Cluster 4r: who an audit row names."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def setUp(self) -> None:
        super().setUp()
        self.service = User.objects.create_user(username="cluster4r_service")

    def _key_client(self, user, scopes):
        _, raw = LexAPIKey.objects.create_key(name="cluster 4r", user=user, scopes=scopes)
        client = APIClient()
        client.credentials(HTTP_API_KEY=raw)
        return client

    def _authors(self, action):
        return list(
            AuditLog.objects.filter(resource="keycloakitem", action=action).values_list("author", flat=True)
        )

    # -- 4.111 ---------------------------------------------------------
    def test_4_111_nameless_user_is_recorded_by_username(self) -> None:
        """
        Scenario 4.111: a create by a user without names names its username.
        Given: a scoped key for "cluster4r_service" (no first or last name)
        When: it creates a KeycloakItem (AuditLogMixin.log_change)
        Then: the "create" audit row's author is "cluster4r_service", not " ".
        """
        client = self._key_client(self.service, ["create", "read", "list"])
        response = client.post(self.url_create(KEYCLOAK), {"label": "by-service"}, format="json")
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        self.assertEqual(self._authors("create"), ["cluster4r_service"])

    # -- 4.112 ---------------------------------------------------------
    def test_4_112_bulk_delete_names_the_username_too(self) -> None:
        """
        Scenario 4.112: bulk changes use the same fallback.
        Given: two KeycloakItems and a delete-scoped key for the nameless user
        When: it deletes both through many/ (BulkAuditLogMixin.log_change)
        Then: both "delete" audit rows name "cluster4r_service".
        """
        items = [KeycloakItem.objects.create(label=f"bulk-{n}") for n in range(2)]
        client = self._key_client(self.service, ["delete", "read", "list"])
        query = urlencode([("ids", item.pk) for item in items])
        response = client.delete(f"{self.url_many(KEYCLOAK)}?{query}")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(self._authors("delete"), ["cluster4r_service", "cluster4r_service"])

    # -- 4.113 ---------------------------------------------------------
    def test_4_113_named_user_unchanged(self) -> None:
        """
        Scenario 4.113: a user with names is recorded as before.
        Given: a key for a user named "Ada Lovelace"
        When: it creates a KeycloakItem
        Then: the audit row's author is "Ada Lovelace".
        """
        named = User.objects.create_user(username="cluster4r_ada", first_name="Ada", last_name="Lovelace")
        client = self._key_client(named, ["create", "read", "list"])
        response = client.post(self.url_create(KEYCLOAK), {"label": "by-ada"}, format="json")
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        self.assertEqual(self._authors("create"), ["Ada Lovelace"])
