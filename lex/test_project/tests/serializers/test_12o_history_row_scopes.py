"""History rows tell non-superusers they cannot be edited or deleted.

Intent
------
Only a superuser may write to a history table (``LEX_SEC_HISTORY_WRITE``,
cluster 4p). The bundled frontend decides which controls to show from a
record's ``lex_reserved_scopes`` — its VersionDetailsDrawer offers Delete
when ``delete`` is true. So a serialized ``Historical*`` row says
``edit: []`` and ``delete: false`` to everyone but a superuser; ``export`` is
computed as before. With the flag off, the scopes are computed as before (the main
model's ``permission_edit``/``permission_delete`` plus ``valid_from``/``valid_to``).

Cluster 12o — scenarios 12.64–12.65. Type: E.
Covers: lex/api/serializers/base_serializers.py (get_lex_reserved_scopes,
_history_write_closed), lex/api/security.py (is_history_model).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12o_history_row_scopes.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, EDIT_SCOPED, EditScopedItem

pytestmark = pytest.mark.serializers


def _env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


class TestCluster12o_HistoryRowScopes(E2ETestCase):
    """lex_reserved_scopes of history rows."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        item = EditScopedItem.objects.create(x="1", y="2", z="3")
        item.x = "changed"
        item.save()
        self.history_model = EditScopedItem.history.model
        self.history_name = self.history_model._meta.model_name
        self.row = self.history_model.objects.filter(id=item.pk).order_by("history_id").first()

    def _scopes_of_one(self):
        response = self.client.get(self.url_detail(self.history_name, self.row.history_id))
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()["lex_reserved_scopes"]

    def _scopes_in_list(self):
        response = self.client.get(self.url_list(self.history_name))
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        rows = body["results"] if isinstance(body, dict) else body
        self.assertTrue(rows, "the history list must contain the rows")
        return [row["lex_reserved_scopes"] for row in rows]

    # -- 12.64 ---------------------------------------------------------
    def test_12_64_non_superusers_see_history_rows_as_read_only(self) -> None:
        """
        Scenario 12.64: a history row's scopes close edit and delete for non-superusers.
        Given: an EditScopedItem (non-admins may edit "x", anyone may delete) with history
        When: a signed-in non-superuser reads a history row by id and the history list
        Then: lex_reserved_scopes is {edit: [], delete: false} on both, with
              export as computed before; the main record's own scopes still
              offer "x" and delete.
        """
        with _env(LEX_SEC_HISTORY_WRITE="false"):
            export = self._scopes_of_one()["export"]
        expected = {"edit": [], "delete": False, "export": export}
        with _env():
            self.assertEqual(self._scopes_of_one(), expected)
            for scopes in self._scopes_in_list():
                self.assertEqual(scopes, expected)
            main = self.client.get(self.url_detail(EDIT_SCOPED, self.row.id)).json()["lex_reserved_scopes"]
        self.assertEqual((main["edit"], main["delete"]), (["x"], True))

    # -- 12.65 ---------------------------------------------------------
    def test_12_65_superusers_and_the_opt_out_keep_the_computed_scopes(self) -> None:
        """
        Scenario 12.65: superusers, and everyone with the flag off, get the computed scopes.
        Given: the same history row
        When: the non-superuser reads it with LEX_SEC_HISTORY_WRITE=false, then a
              superuser reads it with the default flags
        Then: both see delete true and an edit list that includes "x" and the
              validity columns, as before the hardening.
        """
        with _env(LEX_SEC_HISTORY_WRITE="false"):
            legacy = self._scopes_of_one()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        with _env():
            superuser = self._scopes_of_one()
        for who, scopes in (("opt-out", legacy), ("superuser", superuser)):
            self.assertTrue(scopes["delete"], who)
            self.assertIn("x", scopes["edit"], who)
            self.assertIn("valid_from", scopes["edit"], who)
