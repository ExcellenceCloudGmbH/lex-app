"""Generic model-mutation WebSocket broadcast — plain CRUD refreshes open lists.

Intent: a customer with a table view open expects a newly created / updated /
deleted record to appear without pressing "Refresh". The framework already
broadcasts *calculation* state changes over WebSocket (the frontend listens and
refreshes the AG Grid), but ordinary CRUD on a non-CalculationModel produced
**no** broadcast at all — so the open list silently went stale. This batch adds
a generic ``model_data_update`` broadcast emitted on every REST mutation, and the
consumer that fans it out to connected clients. A regression here means stale
grids and the "I have to refresh manually" bug returns.

Cluster 9e — scenarios 9.29–9.36. Type: U + I + E.
Covers: lex/core/signals/ModelMutationSignal.py, lex/api/consumers/ModelDataUpdateConsumer.py,
        lex/lex_app/routing.py, lex/api/views/model_entries/One.py, lex/api/views/model_entries/Many.py.
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9e_model_mutation_broadcast.py -v
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from ..crud_api.models import ALL_MODELS, SIMPLE, SimpleItem

pytestmark = pytest.mark.signals_ws


class TestCluster09e_ModelMutationBroadcastHelper(TestCase):
    """9.29–9.31 — the ``broadcast_model_mutation`` helper contract.

    The helper is the single emission point for generic CRUD broadcasts. It
    must (a) build the message shape the consumer/frontend expect, (b) never
    fire before the DB transaction commits (otherwise a client refreshes and
    does not yet see the row), and (c) no-op when there is no model name.
    """

    # -- 9.29 ----------------------------------------------------------
    def test_9_29_build_message_has_record_mutation_shape(self) -> None:
        """
        Scenario 9.29: message envelope is ``record_mutation`` + payload.
        Given: a model name, action, and record id
        When: ``build_model_mutation_message`` is called
        Then: the envelope type is ``record_mutation`` and the payload carries
              model_name / action / record_id so the frontend can match the
              open resource and refresh it.
        """
        from lex.core.signals.ModelMutationSignal import build_model_mutation_message

        message = build_model_mutation_message("simpleitem", "created", "simpleitem_5")

        self.assertEqual(
            message["type"], "record_mutation",
            "Consumer dispatches on event['type']; it must be 'record_mutation'",
        )
        payload = message["payload"]
        self.assertEqual(payload["model_name"], "simpleitem")
        self.assertEqual(payload["action"], "created")
        self.assertEqual(payload["record_id"], "simpleitem_5")

    # -- 9.30 ----------------------------------------------------------
    def test_9_30_empty_model_name_is_noop(self) -> None:
        """
        Scenario 9.30: no model name → no broadcast (defensive guard).
        Given: an empty model name
        When: ``broadcast_model_mutation`` is called
        Then: nothing is ever sent to the channel layer.
        """
        from lex.core.signals import ModelMutationSignal

        with patch.object(ModelMutationSignal, "sync_channel_group_send") as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                ModelMutationSignal.broadcast_model_mutation("", "created")
        mock_send.assert_not_called()

    # -- 9.31 ----------------------------------------------------------
    def test_9_31_broadcast_deferred_until_commit(self) -> None:
        """
        Scenario 9.31: broadcast fires once, only after the transaction commits.
        Given: a pending DB transaction
        When: ``broadcast_model_mutation`` is called inside it
        Then: nothing is sent until commit; on commit exactly one message goes
              to the ``model_data_update`` group with the ``record_mutation``
              envelope. Emitting before commit would let a client refresh and
              miss the just-written row — the on_commit deferral prevents that.
        """
        from lex.core.signals import ModelMutationSignal

        with patch.object(ModelMutationSignal, "sync_channel_group_send") as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                ModelMutationSignal.broadcast_model_mutation(
                    "simpleitem", "updated", "simpleitem_7"
                )
                self.assertEqual(
                    mock_send.call_count, 0,
                    "Broadcast must NOT fire before the transaction commits",
                )

        self.assertEqual(
            mock_send.call_count, 1,
            "Exactly one broadcast must fire after commit",
        )
        group, message = mock_send.call_args.args
        self.assertEqual(group, "model_data_update")
        self.assertEqual(message["type"], "record_mutation")
        self.assertEqual(message["payload"]["model_name"], "simpleitem")
        self.assertEqual(message["payload"]["action"], "updated")


class TestCluster09e_ModelDataUpdateConsumer(SimpleTestCase):
    """9.32 — the consumer fans a group message out to the socket.

    The consumer joins the ``model_data_update`` group on connect and forwards
    every ``record_mutation`` group message to the browser verbatim. The
    frontend turns that into an SSRM grid refresh.
    """

    # -- 9.32 ----------------------------------------------------------
    def test_9_32_consumer_joins_group_and_forwards_payload(self) -> None:
        """
        Scenario 9.32: connect joins the group; record_mutation reaches the client.
        Given: a connected consumer
        When: a ``record_mutation`` event is delivered to it
        Then: it added itself to the ``model_data_update`` group on connect and
              sends the payload to the socket as JSON.
        """
        from lex.api.consumers.ModelDataUpdateConsumer import ModelDataUpdateConsumer

        consumer = ModelDataUpdateConsumer()
        consumer.channel_name = "test-channel"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer.channel_layer.group_discard = AsyncMock()
        consumer.accept = AsyncMock()
        consumer.send = AsyncMock()

        async_to_sync(consumer.connect)()
        consumer.channel_layer.group_add.assert_awaited_once_with(
            "model_data_update", "test-channel"
        )
        consumer.accept.assert_awaited_once()

        payload = {"model_name": "simpleitem", "action": "created", "record_id": "simpleitem_3"}
        async_to_sync(consumer.record_mutation)({"type": "record_mutation", "payload": payload})

        consumer.send.assert_awaited_once()
        sent = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(sent["type"], "record_mutation")
        self.assertEqual(sent["payload"], payload)


class TestCluster09e_CrudTriggersBroadcast(E2ETestCase):
    """9.33–9.36 — real REST CRUD emits the generic broadcast end-to-end.

    These drive the public REST endpoints exactly as the frontend does and
    assert that a ``model_data_update`` broadcast is emitted for the model that
    changed. This is the surface that actually fixes the customer-visible bug:
    a list view open on a plain (non-calculation) model now gets a refresh
    signal on create/update/delete.
    """

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        self.broadcasts: list[tuple[str, dict]] = []
        patcher = patch(
            "lex.core.signals.ModelMutationSignal.sync_channel_group_send",
            side_effect=lambda group, message: self.broadcasts.append((group, message)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mutation_payloads(self) -> list[dict]:
        return [m["payload"] for g, m in self.broadcasts if g == "model_data_update"]

    # -- 9.33 ----------------------------------------------------------
    def test_9_33_create_emits_broadcast(self) -> None:
        """Scenario 9.33: POST create emits a record_mutation for the model."""
        resp = self.client.post(
            self.url_create(SIMPLE),
            data={"name": "alpha", "value": 1},
            format="json",
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))

        payloads = self._mutation_payloads()
        self.assertTrue(
            any(p["model_name"] == SIMPLE and p["action"] == "created" for p in payloads),
            f"create must broadcast a 'created' record_mutation for {SIMPLE}; got {payloads!r}",
        )

    # -- 9.34 ----------------------------------------------------------
    def test_9_34_update_emits_broadcast(self) -> None:
        """Scenario 9.34: PATCH update emits a 'updated' record_mutation."""
        item = SimpleItem.objects.create(name="beta", value=1)
        self.broadcasts.clear()

        resp = self.client.patch(
            self.url_detail(SIMPLE, item.pk),
            data={"value": 99},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        payloads = self._mutation_payloads()
        self.assertTrue(
            any(p["model_name"] == SIMPLE and p["action"] == "updated" for p in payloads),
            f"update must broadcast an 'updated' record_mutation for {SIMPLE}; got {payloads!r}",
        )

    # -- 9.35 ----------------------------------------------------------
    def test_9_35_delete_emits_broadcast(self) -> None:
        """Scenario 9.35: DELETE emits a 'deleted' record_mutation."""
        item = SimpleItem.objects.create(name="gamma", value=1)
        self.broadcasts.clear()

        resp = self.client.delete(self.url_detail(SIMPLE, item.pk))
        self.assertIn(
            resp.status_code,
            (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT),
        )

        payloads = self._mutation_payloads()
        self.assertTrue(
            any(p["model_name"] == SIMPLE and p["action"] == "deleted" for p in payloads),
            f"delete must broadcast a 'deleted' record_mutation for {SIMPLE}; got {payloads!r}",
        )

    # -- 9.36 ----------------------------------------------------------
    def test_9_36_bulk_delete_emits_broadcast(self) -> None:
        """Scenario 9.36: bulk DELETE via the Many endpoint emits a broadcast."""
        a = SimpleItem.objects.create(name="d1", value=1)
        b = SimpleItem.objects.create(name="d2", value=2)
        self.broadcasts.clear()

        resp = self.client.delete(
            self.url_many(SIMPLE),
            data={"pks": [a.pk, b.pk]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        payloads = self._mutation_payloads()
        self.assertTrue(
            any(p["model_name"] == SIMPLE and p["action"] == "deleted" for p in payloads),
            f"bulk delete must broadcast a 'deleted' record_mutation for {SIMPLE}; got {payloads!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
