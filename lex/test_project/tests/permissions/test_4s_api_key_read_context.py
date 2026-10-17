"""A classic API key keeps its scopes where the serializer decides a row.

Intent
------
A classic API key's ``UserContext`` carries the key's scopes and no
per-record Keycloak permissions. Where the serializer decides a row itself
(no remembered visibility answer: ``LEX_SEC_NOT_FOUND=false``, a project
view), it builds the context ``LexModel.can_read`` would build from the
context it caches for the request. That must be the key's own context: re-
resolving the record's scopes from Keycloak permissions would leave the key
with none, and the default ``permission_read`` (the "read" scope) would
answer every row with an empty body.

Cluster 4s — scenario 4.114. Type: E.
Covers: lex/api/serializers/base_serializers.py (_get_can_read_context).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4s_api_key_read_context.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, KEYCLOAK, KeycloakItem

pytestmark = pytest.mark.permissions


class TestCluster04s_ApiKeyReadContext(E2ETestCase):
    """Cluster 4s: the serializer decides an API-key read with the key's context."""

    e2e_models = ALL_MODELS

    # -- 4.114 ---------------------------------------------------------
    def test_4_114_classic_key_read_decided_by_serializer(self) -> None:
        """
        Scenario 4.114: a classic key reads a record the serializer decides.
        Given: a KeycloakItem (default permissions: the "read" scope), a classic
               API key (all scopes, technical identity) and
               LEX_SEC_NOT_FOUND=false, so no visibility answer is remembered
        When: the key reads the record by id
        Then: 200 with the record's label, not an empty body.
        """
        item = KeycloakItem.objects.create(label="for-the-key")
        self.authenticate_as_api_key(name="cluster 4s")
        with mock.patch.dict(os.environ, {"LEX_SEC_NOT_FOUND": "false"}):
            response = self.client.get(self.url_detail(KEYCLOAK, item.pk))
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json().get("label"), "for-the-key")
