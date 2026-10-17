"""Actions as the operation primitive of a project.

Intent
------
When every operation of a product runs as a ``@lex_action`` (answer a step,
submit, approve, sign, archive), the action must carry what a hand-written
endpoint carried: the permission decides on the validated input (which step,
which amount), a refusal can answer 404 where anything outside a caller's
access does not exist, a permission can answer in its own words, the action
sets its status (201) and headers (no-store) — also decided while running —,
a GET action validates its query parameters, a throttle limits it, and the
audit log records the run after it succeeded or a failure that the rollback
does not take with it.

Cluster 10y — scenarios 10.138–10.143. Type: E.
Covers: lex/api/actions.py (lex_action options, ActionResult, ActionThrottle, ModelActionView
_permitted/_respond/_audit/_audit_failure/get_throttles, denied_answer), lex/core/extensions.py
(ACTION_DENIED, THROTTLE_RATES, _validate_actions), lex/audit_logging/models/AuditLog.py (action choice),
lex/audit_logging/migrations/0009_auditlog_action_choice.py, lex/api/project/__init__.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10y_hardened_actions.py -v
"""

from __future__ import annotations

import dataclasses
from unittest import mock

import pytest
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from lex.api.actions import actions_for
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.core import extensions
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import ACTION_ITEM, ACTION_MODELS, ActionItem

pytestmark = pytest.mark.api_layer


def _url(item, action):
    return reverse(
        "process_admin_rest_api:model-record-action",
        kwargs={"model_container": _MockContainer(ACTION_ITEM), "calculationId": "c", "pk": item.pk, "action": action},
    )


class TestCluster10y_HardenedActions(E2ETestCase):
    """Cluster 10y: permission on input, answers, throttle, audit."""

    e2e_models = ACTION_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def setUp(self) -> None:
        super().setUp()
        self.item = ActionItem.objects.create(name="wallet")

    # -- 10.138 --------------------------------------------------------
    def test_10_138_the_permission_sees_the_validated_input(self) -> None:
        """
        Scenario 10.138: input first, then the permission with it.
        Given: spend, allowed up to an amount of 100
        When: it runs with an invalid amount, with 50 and with 500
        Then: 400 for the invalid amount (the permission never ran on it); 201 for 50 with
              Cache-Control no-store; 400 ACTION_NOT_ALLOWED for 500, the row unchanged.
        """
        with mock.patch.object(ActionItem, "permission_spend", autospec=True, side_effect=lambda self, uc, d: d["amount"] <= 100) as spy:
            self.assertEqual(self.client.post(_url(self.item, "spend"), {"amount": "x"}, format="json").status_code, 400)
            self.assertEqual(spy.call_count, 0)
            spent = self.client.post(_url(self.item, "spend"), {"amount": 50}, format="json")
            refused = self.client.post(_url(self.item, "spend"), {"amount": 500}, format="json")
        self.assertEqual((spent.status_code, spent.json()), (201, {"spent": 50}))
        self.assertEqual(spent["Cache-Control"], "no-store")
        self.assertEqual((refused.status_code, refused.json()["code"]), (400, "ACTION_NOT_ALLOWED"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.note, "spent 50")

    # -- 10.139 --------------------------------------------------------
    def test_10_139_refusals_answer_as_configured(self) -> None:
        """
        Scenario 10.139: 404 where configured, the permission's own words where it raises.
        Given: hidden_action (denied="not_found"), state_bound (its permission raises 409),
               spend refused with ACTION_DENIED="not_found"
        When: they run
        Then: 404 NOT_FOUND; 409 WRONG_STATE; 404 for the refused spend.
        """
        hidden = self.client.post(_url(self.item, "hidden_action"), {}, format="json")
        self.assertEqual((hidden.status_code, hidden.json()["code"]), (404, "NOT_FOUND"))
        bound = self.client.post(_url(self.item, "state_bound"), {}, format="json")
        self.assertEqual((bound.status_code, bound.json()["code"]), (409, "WRONG_STATE"))
        self.use_lex_extensions(ACTION_DENIED="not_found")
        self.assertEqual(self.client.post(_url(self.item, "spend"), {"amount": 500}, format="json").status_code, 404)

    # -- 10.140 --------------------------------------------------------
    def test_10_140_status_and_headers_decided_while_running(self) -> None:
        """
        Scenario 10.140: ActionResult sets status and headers.
        Given: upsert, which answers 201 the first time and 200 after
        When: it runs twice
        Then: 201 then 200, both with its X-Upsert header and {"created": True/False}.
        """
        first = self.client.post(_url(self.item, "upsert"), {}, format="json")
        second = self.client.post(_url(self.item, "upsert"), {}, format="json")
        self.assertEqual((first.status_code, first.json(), first["X-Upsert"]), (201, {"created": True}, "1"))
        self.assertEqual((second.status_code, second.json()), (200, {"created": False}))

    # -- 10.141 --------------------------------------------------------
    def test_10_141_get_actions_validate_their_query(self) -> None:
        """
        Scenario 10.141: a GET action's input is its query string.
        Given: look (GET, input q of at most 20 characters)
        When: it is called with q, without q, with an unknown parameter
        Then: 200 with q echoed; 400 without it; 400 UNKNOWN_FIELDS for the unknown one.
        """
        ok = self.client.get(_url(self.item, "look"), {"q": "abc"})
        self.assertEqual((ok.status_code, ok.json()), (200, {"q": "abc", "name": "wallet"}))
        self.assertEqual(self.client.get(_url(self.item, "look")).status_code, 400)
        unknown = self.client.get(_url(self.item, "look"), {"q": "abc", "evil": "1"})
        self.assertEqual((unknown.status_code, unknown.json()["code"]), (400, "UNKNOWN_FIELDS"))

    # -- 10.142 --------------------------------------------------------
    def test_10_142_throttled_actions(self) -> None:
        """
        Scenario 10.142: throttle="<scope>" limits an action per user.
        Given: ping throttled at 2 per minute, and an action whose throttle has no rate
        When: ping runs three times; the configuration is validated with the unrated one
        Then: 200, 200, 429 THROTTLED; validation names the missing rate.
        """
        cache.clear()
        specs = actions_for(ActionItem)
        original = specs["ping"]
        specs["ping"] = dataclasses.replace(original, throttle="cluster10y")
        self.addCleanup(specs.__setitem__, "ping", original)
        self.use_lex_extensions(THROTTLE_RATES={"cluster10y": "2/min"})
        answers = [self.client.post(_url(self.item, "ping"), {}, format="json") for _ in range(3)]
        self.assertEqual([answer.status_code for answer in answers], [200, 200, 429])
        self.assertEqual(answers[2].json()["code"], "THROTTLED")
        with extensions.override_extensions(THROTTLE_RATES={}):
            with self.assertRaisesRegex(ImproperlyConfigured, "cluster10y"):
                extensions._validate_actions()

    # -- 10.143 --------------------------------------------------------
    def test_10_143_audit_after_success_and_failures_kept(self) -> None:
        """
        Scenario 10.143: the audit log records what happened, not what was attempted.
        Given: spend (succeeds), explode (raises inside its transaction) and fail_loose (atomic=False)
        When: each runs
        Then: spend leaves one "action" entry with status success and the row's reference;
              explode and fail_loose each leave a "failure" entry naming the error code,
              although explode's own writes were rolled back.
        """
        AuditLog.objects.all().delete()
        self.client.post(_url(self.item, "spend"), {"amount": 5}, format="json")
        self.assertEqual(self.client.post(_url(self.item, "explode"), {}, format="json").status_code, 409)
        self.assertEqual(self.client.post(_url(self.item, "fail_loose"), {}, format="json").status_code, 409)
        entries = {entry.payload["lex_action"]: entry for entry in AuditLog.objects.filter(resource="actionitem")}
        self.assertEqual(set(entries), {"spend", "explode", "fail_loose"})
        status_of = {name: AuditLogStatus.objects.get(audit_log=entry) for name, entry in entries.items()}
        self.assertEqual(entries["spend"].action, "action")
        self.assertEqual(entries["spend"].object_id, self.item.pk)
        self.assertEqual(status_of["spend"].status, "success")
        self.assertEqual((status_of["explode"].status, status_of["explode"].error_traceback), ("failure", "NOT_NOW"))
        self.assertEqual((status_of["fail_loose"].status, status_of["fail_loose"].error_traceback), ("failure", "LOOSE"))
        self.assertFalse(ActionItem.objects.filter(name="side-effect").exists())
