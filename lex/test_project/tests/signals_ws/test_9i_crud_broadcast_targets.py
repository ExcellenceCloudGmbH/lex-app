"""CRUD endpoints hand the changed record to the mutation broadcast.

Intent
------
Scoped realtime delivery (``lex_config.MUTATION_GROUPS``) and the
``broadcast_mutations = False`` opt-out both decide per record, so the
generic endpoints must pass what changed: the single-record endpoint
(``One``) passes the created, updated or deleted instance; the bulk endpoint
(``Many``) passes the model class, since one event covers many rows.

Cluster 9i — scenarios 9.49–9.51. Type: E.
Covers: lex/api/views/model_entries/One.py (create/update/delete broadcasts),
lex/api/views/model_entries/Many.py (bulk delete broadcast).
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9i_crud_broadcast_targets.py -v
"""

from __future__ import annotations

from unittest import mock
from urllib.parse import urlencode

import pytest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import BROADCAST_MODELS, SIG_PLAIN, SigPlainItem

pytestmark = pytest.mark.signals_ws


def record_groups(target):
    """Instances by name, the model class (bulk changes) by model."""
    if isinstance(target, type):
        return [f"bulk-{target._meta.model_name}"]
    return [f"row-{target.name}"]


def user_groups(user):
    return ["row-a"]


HOOKS = {"MUTATION_GROUPS": f"{__name__}.record_groups", "USER_GROUPS": f"{__name__}.user_groups"}


class TestCluster09i_CrudBroadcastTargets(E2ETestCase):
    """Cluster 9i: One and Many pass their target to broadcast_model_mutation."""

    e2e_models = BROADCAST_MODELS

    def setUp(self) -> None:
        super().setUp()
        self.sent = []
        patcher = mock.patch(
            "lex.core.signals.ModelMutationSignal.sync_channel_group_send",
            side_effect=lambda group, message: self.sent.append(
                (group, message["payload"]["action"], message["payload"].get("record_id"))
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mutations(self):
        return [event for event in self.sent if event[0].startswith("model_data_update")]

    # -- 9.49 ----------------------------------------------------------
    def test_9_49_single_record_events_follow_the_record(self) -> None:
        """
        Scenario 9.49: One passes the record itself.
        Given: MUTATION_GROUPS naming a group per record name
        When: a SigPlainItem "a" is created, renamed to "b" and deleted through One
        Then: "created" and "updated" reach "model_data_update.row-a" — an
              update is sent to the groups of the record as it was loaded,
              i.e. whoever had it on screen — and "deleted" reaches
              "model_data_update.row-b"; each carries the record's id and goes
              to no other group.
        """
        self.use_lex_extensions(**HOOKS)
        created = self.client.post(self.url_create(SIG_PLAIN), {"name": "a"}, format="json")
        self.assertIn(created.status_code, (200, 201), created.content[:300])
        pk = SigPlainItem.objects.get(name="a").pk
        record_id = f"{SIG_PLAIN}_{pk}"
        updated = self.client.patch(self.url_detail(SIG_PLAIN, pk), {"name": "b"}, format="json")
        self.assertEqual(updated.status_code, 200, updated.content[:300])
        deleted = self.client.delete(self.url_detail(SIG_PLAIN, pk))
        self.assertIn(deleted.status_code, (200, 204), deleted.content[:300])
        self.assertEqual(
            self._mutations(),
            [
                ("model_data_update.row-a", "created", record_id),
                ("model_data_update.row-a", "updated", record_id),
                ("model_data_update.row-b", "deleted", record_id),
            ],
        )

    # -- 9.50 ----------------------------------------------------------
    def test_9_50_bulk_events_follow_the_model(self) -> None:
        """
        Scenario 9.50: Many passes the model class.
        Given: the hooks and two SigPlainItems
        When: both are deleted through many/
        Then: one "deleted" event reaches "model_data_update.bulk-sigplainitem".
        """
        self.use_lex_extensions(**HOOKS)
        items = [SigPlainItem.objects.create(name=f"bulk-{n}") for n in range(2)]
        query = urlencode([("ids", item.pk) for item in items])
        response = self.client.delete(f"{self.url_many(SIG_PLAIN)}?{query}")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(self._mutations(), [("model_data_update.bulk-sigplainitem", "deleted", None)])

    # -- 9.51 ----------------------------------------------------------
    def test_9_51_opted_out_model_is_silent_on_both_endpoints(self) -> None:
        """
        Scenario 9.51: broadcast_mutations = False holds for One and Many.
        Given: SigPlainItem with broadcast_mutations = False (no hooks)
        When: a record is created through One and two are deleted through many/
        Then: nothing is sent.
        """
        with mock.patch.object(SigPlainItem, "broadcast_mutations", False, create=True):
            created = self.client.post(self.url_create(SIG_PLAIN), {"name": "quiet"}, format="json")
            self.assertIn(created.status_code, (200, 201), created.content[:300])
            items = [SigPlainItem.objects.create(name=f"quiet-{n}") for n in range(2)]
            query = urlencode([("ids", item.pk) for item in items])
            self.assertEqual(self.client.delete(f"{self.url_many(SIG_PLAIN)}?{query}").status_code, 200)
        self.assertEqual(self._mutations(), [])
