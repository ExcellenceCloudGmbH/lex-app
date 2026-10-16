"""Record and collection actions (@lex_action).

Intent
------
Operations that are not CRUD on one model — approve, submit, import a batch —
are declared on the model and get their own URL with lex's guarantees: the
record must exist and be readable (404 otherwise), the declared permission
decides (400 ACTION_NOT_ALLOWED, never 403), input is validated and unknown
keys are refused, the method runs in one transaction that an error rolls
back, and the result can be JSON, nothing (204) or a file. An action that
declares no permission is always refused.

Cluster 10s — scenarios 10.106–10.112. Type: E.
Covers: lex/api/actions.py (lex_action, actions_for, ModelActionView), the
action routes in lex/process_admin/sites/process_admin_site.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10s_record_actions.py -v
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import ACTION_ITEM, ACTION_MODELS, ActionItem

pytestmark = pytest.mark.api_layer


def _record_url(item, action):
    return reverse(
        "process_admin_rest_api:model-record-action",
        kwargs={"model_container": _MockContainer(ACTION_ITEM), "calculationId": "default", "pk": item.pk, "action": action},
    )


def _collection_url(action):
    return reverse(
        "process_admin_rest_api:model-collection-action",
        kwargs={"model_container": _MockContainer(ACTION_ITEM), "calculationId": "default", "action": action},
    )


class TestCluster10s_RecordActions(E2ETestCase):
    """Cluster 10s: @lex_action endpoints."""

    e2e_models = ACTION_MODELS

    # -- 10.106 --------------------------------------------------------
    def test_10_106_record_action_runs_with_validated_input(self) -> None:
        """
        Scenario 10.106: a record action changes the record and answers its result.
        Given: an editable ActionItem
        When: approve is posted with a note
        Then: HTTP 200 {"status": "approved", "by": <caller>} and the row is approved.
        """
        item = ActionItem.objects.create(name="invoice")
        response = self.client.post(_record_url(item, "approve"), {"note": "ok"}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json(), {"status": "approved", "by": "e2e_user"})
        item.refresh_from_db()
        self.assertEqual((item.status, item.note), ("approved", "ok"))

    # -- 10.107 --------------------------------------------------------
    def test_10_107_input_is_strict(self) -> None:
        """
        Scenario 10.107: input outside the declared serializer is refused.
        Given: approve, whose input declares only "note"
        When: it is posted with an extra "status" key, and without "note"
        Then: 400 UNKNOWN_FIELDS naming "status", then 400 for the missing note;
              the row is unchanged.
        """
        item = ActionItem.objects.create(name="invoice")
        response = self.client.post(_record_url(item, "approve"), {"note": "ok", "status": "paid"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "UNKNOWN_FIELDS")
        self.assertEqual(self.client.post(_record_url(item, "approve"), {}, format="json").status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.status, "draft")

    # -- 10.108 --------------------------------------------------------
    def test_10_108_permission_decides_and_hidden_rows_do_not_exist(self) -> None:
        """
        Scenario 10.108: permission and visibility gate every action.
        Given: a locked (not editable) row, a hidden row and an unguarded action
        When: approve runs on the locked and hidden rows, and unguarded runs
        Then: locked answers 400 ACTION_NOT_ALLOWED, hidden 404, and the
              action without a declared permission 400 ACTION_NOT_ALLOWED.
        """
        locked = ActionItem.objects.create(name="locked-one", status="locked")
        hidden = ActionItem.objects.create(name="hidden")
        response = self.client.post(_record_url(locked, "approve"), {"note": "x"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(self.client.post(_record_url(hidden, "approve"), {"note": "x"}, format="json").status_code, 404)
        response = self.client.post(_record_url(locked, "unguarded"), {}, format="json")
        self.assertEqual(response.json()["code"], "ACTION_NOT_ALLOWED")

    # -- 10.109 --------------------------------------------------------
    def test_10_109_errors_roll_the_action_back(self) -> None:
        """
        Scenario 10.109: a failing action leaves no trace.
        Given: explode, which creates a row and then raises LexActionError(409)
        When: it is posted
        Then: HTTP 409 {code: NOT_NOW} and the side-effect row does not exist.
        """
        item = ActionItem.objects.create(name="invoice")
        response = self.client.post(_record_url(item, "explode"), {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "NOT_NOW")
        self.assertFalse(ActionItem.objects.filter(name="side-effect").exists())

    # -- 10.110 --------------------------------------------------------
    def test_10_110_file_results_pass_through(self) -> None:
        """
        Scenario 10.110: an action can answer with a file.
        Given: download (GET only, superusers only)
        When: a regular user, then a superuser calls it, and a POST is tried
        Then: 400 for the regular user; the superuser gets the CSV unchanged;
              POST answers 405.
        """
        item = ActionItem.objects.create(name="invoice")
        self.assertEqual(self.client.get(_record_url(item, "download")).status_code, 400)
        self.user.is_superuser = True
        self.user.save()
        response = self.client.get(_record_url(item, "download"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertEqual(response.content, b"name\ninvoice\n")
        self.assertEqual(self.client.post(_record_url(item, "download"), {}, format="json").status_code, 405)

    # -- 10.111 --------------------------------------------------------
    def test_10_111_collection_action(self) -> None:
        """
        Scenario 10.111: a collection action runs without a record.
        Given: batch (permission "create", input count 1..5)
        When: it is posted with count 3, then 9
        Then: 200 with three new ids; 400 for 9.
        """
        response = self.client.post(_collection_url("batch"), {"count": 3}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(len(response.json()["ids"]), 3)
        self.assertEqual(ActionItem.objects.filter(name__startswith="batch-").count(), 3)
        self.assertEqual(self.client.post(_collection_url("batch"), {"count": 9}, format="json").status_code, 400)

    # -- 10.112 --------------------------------------------------------
    def test_10_112_unknown_or_misplaced_actions_are_not_found(self) -> None:
        """
        Scenario 10.112: only declared actions exist, each in its place.
        Given: the model's actions
        When: an unknown action, a record action at the collection URL, and a
              collection action at a record URL are called
        Then: all three answer 404.
        """
        item = ActionItem.objects.create(name="invoice")
        self.assertEqual(self.client.post(_record_url(item, "nope"), {}, format="json").status_code, 404)
        self.assertEqual(self.client.post(_collection_url("approve"), {}, format="json").status_code, 404)
        self.assertEqual(self.client.post(_record_url(item, "batch"), {}, format="json").status_code, 404)
