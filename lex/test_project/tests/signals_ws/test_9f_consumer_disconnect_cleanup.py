"""Consumer disconnect cleanup — active-consumer set pruned on WebSocket close.

Intent: each WebSocket consumer maintains a class-level ``active_consumers``
set so that ``disconnect_all()`` can close every live socket on server
shutdown. Before PR #615 the ``disconnect()`` override in
``BackendHealthConsumer``, ``CalculationLogConsumer``, and
``CalculationsConsumer`` called ``super().disconnect()`` but forgot to call
``self.active_consumers.discard(self)``. The consumer therefore stayed in the
set after the socket closed, so:
  - ``disconnect_all()`` would attempt to disconnect an already-closed consumer.
  - The set grew unboundedly, leaking references to closed consumers.
The fix adds ``self.active_consumers.discard(self)`` at the start of every
``disconnect()`` override, mirroring the pattern already used in
``UpdateCalculationStatusConsumer``. A regression here causes memory leaks and
spurious "double-disconnect" errors on graceful shutdown.

Cluster 9f — scenarios 9.37–9.41. Type: U.
Covers: lex/api/consumers/BackendHealthConsumer.py,
        lex/api/consumers/CalculationLogConsumer.py,
        lex/api/consumers/CalculationsConsumer.py.
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9f_consumer_disconnect_cleanup.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

pytestmark = pytest.mark.signals_ws


def _make_consumer(cls):
    """Instantiate *cls* with minimal Channels plumbing so connect/disconnect work.

    We skip the Channels channel-layer machinery and just verify the
    ``active_consumers`` bookkeeping in isolation.
    """
    consumer = cls()
    consumer.channel_name = "test.channel"
    consumer.channel_layer = MagicMock()
    consumer.channel_layer.group_add = AsyncMock()
    consumer.channel_layer.group_discard = AsyncMock()
    consumer.channel_layer.group_send = AsyncMock()
    return consumer


class TestCluster09f_ConsumerDisconnectCleanup(SimpleTestCase):
    """Cluster 9f: active_consumers set is pruned when a WebSocket consumer disconnects."""

    def setUp(self) -> None:
        # Always start with a clean class-level set to avoid state leakage
        # between test methods (set is a class attribute, not instance-level).
        from lex.api.consumers.BackendHealthConsumer import BackendHealthConsumer
        from lex.api.consumers.CalculationLogConsumer import CalculationLogConsumer
        from lex.api.consumers.CalculationsConsumer import CalculationsConsumer

        BackendHealthConsumer.active_consumers = set()
        CalculationLogConsumer.active_consumers = set()
        CalculationsConsumer.active_consumers = set()

    # -- 9.37 ---------------------------------------------------------
    def test_9_37_backend_health_consumer_removed_from_set_on_disconnect(self) -> None:
        """
        Scenario 9.37: BackendHealthConsumer.disconnect() removes the consumer
        from active_consumers.
        Given: a BackendHealthConsumer that was added to active_consumers on connect
        When: disconnect() is called
        Then: the consumer is no longer in BackendHealthConsumer.active_consumers —
              a stale reference cannot cause a double-disconnect on server shutdown.
        """
        from lex.api.consumers.BackendHealthConsumer import BackendHealthConsumer

        consumer = _make_consumer(BackendHealthConsumer)
        BackendHealthConsumer.active_consumers.add(consumer)
        self.assertIn(
            consumer,
            BackendHealthConsumer.active_consumers,
            "Pre-condition: consumer must be in active_consumers before disconnect",
        )

        with patch.object(
            BackendHealthConsumer.__bases__[0], "disconnect", new=AsyncMock()
        ):
            async_to_sync(consumer.disconnect)(1001)

        self.assertNotIn(
            consumer,
            BackendHealthConsumer.active_consumers,
            "After disconnect() the consumer must be removed from active_consumers "
            "so server-shutdown disconnect_all() does not attempt a double-close.",
        )

    # -- 9.38 ---------------------------------------------------------
    def test_9_38_calculation_log_consumer_removed_from_set_on_disconnect(self) -> None:
        """
        Scenario 9.38: CalculationLogConsumer.disconnect() removes the consumer
        from active_consumers.
        Given: a CalculationLogConsumer added to active_consumers
        When: disconnect() is called
        Then: the consumer is no longer in CalculationLogConsumer.active_consumers.
        """
        from lex.api.consumers.CalculationLogConsumer import CalculationLogConsumer

        consumer = _make_consumer(CalculationLogConsumer)
        # CalculationLogConsumer.disconnect also calls group_discard, so we need
        # the calculation_record attribute that connect() would normally set.
        consumer.calculation_record = "calc-123"
        CalculationLogConsumer.active_consumers.add(consumer)

        with patch.object(
            CalculationLogConsumer.__bases__[0], "disconnect", new=AsyncMock()
        ):
            async_to_sync(consumer.disconnect)(1001)

        self.assertNotIn(
            consumer,
            CalculationLogConsumer.active_consumers,
            "After disconnect() the consumer must be removed from active_consumers.",
        )

    # -- 9.39 ---------------------------------------------------------
    def test_9_39_calculations_consumer_removed_from_set_on_disconnect(self) -> None:
        """
        Scenario 9.39: CalculationsConsumer.disconnect() removes the consumer
        from active_consumers.
        Given: a CalculationsConsumer added to active_consumers
        When: disconnect() is called
        Then: the consumer is no longer in CalculationsConsumer.active_consumers.
        """
        from lex.api.consumers.CalculationsConsumer import CalculationsConsumer

        consumer = _make_consumer(CalculationsConsumer)
        CalculationsConsumer.active_consumers.add(consumer)

        with patch.object(
            CalculationsConsumer.__bases__[0], "disconnect", new=AsyncMock()
        ):
            async_to_sync(consumer.disconnect)(1001)

        self.assertNotIn(
            consumer,
            CalculationsConsumer.active_consumers,
            "After disconnect() the consumer must be removed from active_consumers.",
        )

    # -- 9.40 ---------------------------------------------------------
    def test_9_40_disconnect_when_not_in_set_is_idempotent(self) -> None:
        """
        Scenario 9.40: disconnect() on a consumer not in active_consumers does not raise.
        Given: a BackendHealthConsumer that was never added to active_consumers
              (or was already removed — e.g. a duplicate disconnect)
        When: disconnect() is called
        Then: no KeyError or AttributeError is raised — set.discard() is safe even
              when the element is absent, unlike set.remove().
        """
        from lex.api.consumers.BackendHealthConsumer import BackendHealthConsumer

        consumer = _make_consumer(BackendHealthConsumer)
        # Consumer is NOT in the set — simulates a duplicate close event
        self.assertNotIn(consumer, BackendHealthConsumer.active_consumers)

        with patch.object(
            BackendHealthConsumer.__bases__[0], "disconnect", new=AsyncMock()
        ):
            # Must not raise
            async_to_sync(consumer.disconnect)(1001)

        self.assertNotIn(
            consumer,
            BackendHealthConsumer.active_consumers,
            "Consumer must not appear in active_consumers after a no-op discard.",
        )

    # -- 9.41 ---------------------------------------------------------
    def test_9_41_disconnect_all_finds_empty_set_after_all_consumers_closed(self) -> None:
        """
        Scenario 9.41: disconnect_all() iterates an empty set when all consumers
        disconnected individually beforehand.
        Given: two BackendHealthConsumer instances that both called disconnect()
        When: disconnect_all() is called
        Then: it calls disconnect(None) on zero consumers and active_consumers
              remains empty — no stale reference leaks through to shutdown.
        """
        from lex.api.consumers.BackendHealthConsumer import BackendHealthConsumer

        consumer_a = _make_consumer(BackendHealthConsumer)
        consumer_b = _make_consumer(BackendHealthConsumer)
        BackendHealthConsumer.active_consumers.update({consumer_a, consumer_b})

        with patch.object(
            BackendHealthConsumer.__bases__[0], "disconnect", new=AsyncMock()
        ):
            async_to_sync(consumer_a.disconnect)(1001)
            async_to_sync(consumer_b.disconnect)(1001)

        self.assertEqual(
            BackendHealthConsumer.active_consumers,
            set(),
            "Both consumers disconnected; set must be empty before disconnect_all() runs.",
        )

        # disconnect_all() should not call any consumer's disconnect again
        disconnect_calls: list = []

        async def _track_disconnect(*args, **kwargs):
            disconnect_calls.append(args)

        with patch.object(consumer_a, "disconnect", side_effect=_track_disconnect):
            with patch.object(consumer_b, "disconnect", side_effect=_track_disconnect):
                async_to_sync(BackendHealthConsumer.disconnect_all)()

        self.assertEqual(
            disconnect_calls,
            [],
            "disconnect_all() must call disconnect() on zero consumers when the "
            "active_consumers set is already empty.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
