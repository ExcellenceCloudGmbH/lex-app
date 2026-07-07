"""Core WebSocket consumers expose health, calculation, and log streams.

Intent: the frontend relies on three long-lived WebSocket contracts: a public
backend-health socket that confirms the server is reachable, a calculations
socket that fans out calculation IDs/notifications, and a per-calculation log
socket that streams live log lines. The ASGI shutdown hook also depends on each
consumer tracking active connections so server shutdown can close them cleanly.

Cluster 9f — scenarios 9.37–9.42. Type: U.
Covers: lex/api/consumers/BackendHealthConsumer.py,
        lex/api/consumers/CalculationsConsumer.py,
        lex/api/consumers/CalculationLogConsumer.py.
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9f_core_consumers.py -v
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from lex.api.consumers.BackendHealthConsumer import BackendHealthConsumer
from lex.api.consumers.CalculationLogConsumer import CalculationLogConsumer
from lex.api.consumers.CalculationsConsumer import CalculationsConsumer

pytestmark = pytest.mark.signals_ws


def _consumer(consumer_cls):
    """Instantiate a consumer with mocked socket/channel-layer boundaries."""
    consumer = consumer_cls()
    consumer.channel_name = "test-channel"
    consumer.channel_layer = MagicMock()
    consumer.channel_layer.group_add = AsyncMock()
    consumer.channel_layer.group_discard = AsyncMock()
    consumer.accept = AsyncMock()
    consumer.send = AsyncMock()
    return consumer


class _ConsumerStateMixin:
    """Clear process-global active consumer sets around every test."""

    def setUp(self) -> None:
        super().setUp()
        for consumer_cls in (
            BackendHealthConsumer,
            CalculationsConsumer,
            CalculationLogConsumer,
        ):
            consumer_cls.active_consumers.clear()

    def tearDown(self) -> None:
        for consumer_cls in (
            BackendHealthConsumer,
            CalculationsConsumer,
            CalculationLogConsumer,
        ):
            consumer_cls.active_consumers.clear()
        super().tearDown()


class TestCluster09f_BackendHealthConsumer(_ConsumerStateMixin, SimpleTestCase):
    """9.37–9.38 — public backend-health WebSocket contract."""

    # -- 9.37 ----------------------------------------------------------
    def test_9_37_backend_health_accepts_tracks_and_echoes_health(self) -> None:
        """
        Scenario 9.37: public health socket accepts and returns Healthy.
        Given: a backend-health WebSocket consumer
        When: it connects and receives any client message
        Then: it accepts the socket, tracks the active connection, and sends the
              documented ``{"status": "Healthy :)"}`` payload.
        """
        consumer = _consumer(BackendHealthConsumer)

        async_to_sync(consumer.connect)()
        consumer.accept.assert_awaited_once()
        self.assertIn(
            consumer,
            BackendHealthConsumer.active_consumers,
            "Connected health sockets must be tracked for shutdown cleanup",
        )

        async_to_sync(consumer.receive)("ping")
        sent = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(sent, {"status": "Healthy :)"})

    # -- 9.38 ----------------------------------------------------------
    def test_9_38_backend_health_disconnect_untracks_connection(self) -> None:
        """
        Scenario 9.38: disconnect removes the health consumer from active set.
        Given: a connected backend-health consumer
        When: it disconnects
        Then: the process-global active set no longer retains it.
        """
        consumer = _consumer(BackendHealthConsumer)
        async_to_sync(consumer.connect)()

        async_to_sync(consumer.disconnect)(1000)

        self.assertNotIn(
            consumer,
            BackendHealthConsumer.active_consumers,
            "Disconnected health sockets must not leak in active_consumers",
        )


class TestCluster09f_CalculationsConsumer(_ConsumerStateMixin, SimpleTestCase):
    """9.39–9.40 — calculation notification WebSocket contract."""

    # -- 9.39 ----------------------------------------------------------
    def test_9_39_calculations_consumer_joins_group_and_forwards_events(self) -> None:
        """
        Scenario 9.39: calculations socket joins group and forwards events.
        Given: a connected calculations consumer
        When: calculation-id and notification events arrive from the channel layer
        Then: the consumer forwards the raw ID event and wraps notifications in
              the JSON shape the frontend listener expects.
        """
        consumer = _consumer(CalculationsConsumer)

        async_to_sync(consumer.connect)()

        consumer.accept.assert_awaited_once()
        consumer.channel_layer.group_add.assert_awaited_once_with(
            "calculations", "test-channel"
        )
        self.assertIn(consumer, CalculationsConsumer.active_consumers)

        async_to_sync(consumer.calculation_id)(
            {"type": "calculation_id", "calculation_id": "calc-123"}
        )
        first = json.loads(consumer.send.await_args_list[0].kwargs["text_data"])
        self.assertEqual(first["calculation_id"], "calc-123")

        payload = {"record_id": "calc_1", "state": "SUCCESS"}
        async_to_sync(consumer.calculation_notification)({"payload": payload})
        second = json.loads(consumer.send.await_args_list[1].kwargs["text_data"])
        self.assertEqual(second, {"type": "calculation_notification", "payload": payload})

    # -- 9.40 ----------------------------------------------------------
    def test_9_40_calculations_disconnect_leaves_group_and_untracks(self) -> None:
        """
        Scenario 9.40: disconnect leaves the calculations channel group.
        Given: a connected calculations consumer
        When: it disconnects
        Then: it discards its channel from the group and removes itself from the
              shutdown-tracked active set.
        """
        consumer = _consumer(CalculationsConsumer)
        async_to_sync(consumer.connect)()

        async_to_sync(consumer.disconnect)(1000)

        consumer.channel_layer.group_discard.assert_awaited_once_with(
            "calculations", "test-channel"
        )
        self.assertNotIn(consumer, CalculationsConsumer.active_consumers)


class TestCluster09f_CalculationLogConsumer(_ConsumerStateMixin, SimpleTestCase):
    """9.41 — per-calculation log stream contract."""

    # -- 9.41 ----------------------------------------------------------
    def test_9_41_calculation_log_groups_by_record_prefix_and_streams_logs(self) -> None:
        """
        Scenario 9.41: calculation-log socket scopes by record prefix.
        Given: a calculation log URL id of ``record-uuid-extra``
        When: the consumer connects and receives a log event
        Then: it joins the ``record`` group and sends log lines in the
              ``calculation_log_real_time`` envelope.
        """
        consumer = _consumer(CalculationLogConsumer)
        consumer.scope = {"url_route": {"kwargs": {"calculationId": "record-uuid-extra"}}}

        async_to_sync(consumer.connect)()

        self.assertEqual(consumer.calculation_id, "record-uuid-extra")
        self.assertEqual(
            consumer.calculation_record,
            "record",
            "Log sockets group by calculation-record prefix so all live lines "
            "for that record land on the same channel group",
        )
        consumer.channel_layer.group_add.assert_awaited_once_with("record", "test-channel")
        consumer.accept.assert_awaited_once()
        self.assertIn(consumer, CalculationLogConsumer.active_consumers)

        async_to_sync(consumer.calculation_log_real_time)({"payload": ["line 1", "line 2"]})
        sent = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(
            sent,
            {"type": "calculation_log_real_time", "logs": ["line 1", "line 2"]},
        )

        async_to_sync(consumer.disconnect)(1000)
        consumer.channel_layer.group_discard.assert_awaited_once_with("record", "test-channel")
        self.assertNotIn(consumer, CalculationLogConsumer.active_consumers)


class TestCluster09f_ShutdownDisconnectAll(_ConsumerStateMixin, SimpleTestCase):
    """9.42 — ASGI shutdown hook can close all tracked core consumers."""

    # -- 9.42 ----------------------------------------------------------
    def test_9_42_disconnect_all_calls_disconnect_on_active_consumers_copy(self) -> None:
        """
        Scenario 9.42: ``disconnect_all`` closes every active consumer.
        Given: active consumers in each process-global set
        When: ``disconnect_all`` runs
        Then: every tracked consumer is asked to disconnect, using a snapshot of
              the set so mutation during disconnect cannot skip peers.
        """
        for consumer_cls in (
            BackendHealthConsumer,
            CalculationsConsumer,
            CalculationLogConsumer,
        ):
            first = _consumer(consumer_cls)
            second = _consumer(consumer_cls)
            first.disconnect = AsyncMock()
            second.disconnect = AsyncMock()
            consumer_cls.active_consumers.update({first, second})

            async_to_sync(consumer_cls.disconnect_all)()

            first.disconnect.assert_awaited_once_with(None)
            second.disconnect.assert_awaited_once_with(None)
