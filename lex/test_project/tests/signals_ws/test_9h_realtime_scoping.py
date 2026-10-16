"""Who receives realtime events (MUTATION_GROUPS / USER_GROUPS, broadcast_mutations).

Intent
------
Every signed-in socket used to receive every ``record_mutation`` and
calculation-status event — activity metadata (which model changed, which
record is calculating, error tracebacks) shared across tenants. Two optional
``lex_config`` hooks scope delivery: an event goes to the groups
``MUTATION_GROUPS(record)`` names, a socket joins the groups
``USER_GROUPS(user)`` names; payloads do not change. Without the hooks the
single shared group stays. A model can opt out of mutation broadcasts with
``broadcast_mutations = False``.

Cluster 9h — scenarios 9.44–9.48. Type: U.
Covers: lex/core/realtime.py, lex/core/signals/ModelMutationSignal.py,
lex/core/signals/CalculationSignals.py, lex/api/consumers/ModelDataUpdateConsumer.py,
lex/api/consumers/UpdateCalculationStatusConsumer.py.
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9h_realtime_scoping.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from lex.api.consumers.ModelDataUpdateConsumer import ModelDataUpdateConsumer
from lex.api.consumers.UpdateCalculationStatusConsumer import UpdateCalculationStatusConsumer
from lex.core import realtime
from lex.core.extensions import override_extensions
from lex.core.signals.ModelMutationSignal import MODEL_MUTATION_GROUP, broadcast_model_mutation

pytestmark = pytest.mark.signals_ws


class TenantRecord:
    def __init__(self, tenant):
        self.tenant = tenant


class QuietModel:
    broadcast_mutations = False


def record_groups(target):
    return [getattr(target, "tenant", "all")]


def user_groups(user):
    return [user.tenant]


HOOKS = {
    "MUTATION_GROUPS": f"{__name__}.record_groups",
    "USER_GROUPS": f"{__name__}.user_groups",
}


def _sent_groups(callable_):
    sent = []
    with mock.patch(
        "lex.core.signals.ModelMutationSignal.sync_channel_group_send",
        side_effect=lambda group, message: sent.append((group, message)),
    ), mock.patch("django.db.transaction.on_commit", side_effect=lambda fn, using=None: fn()):
        callable_()
    return sent


def _consumer(consumer_cls, user):
    consumer = consumer_cls()
    consumer.channel_name = "test-channel"
    consumer.channel_layer = MagicMock()
    consumer.channel_layer.group_add = AsyncMock()
    consumer.channel_layer.group_discard = AsyncMock()
    consumer.accept = AsyncMock()
    consumer.send = AsyncMock()
    consumer.scope = {"user": user}
    return consumer


class TestCluster09h_RealtimeScoping(SimpleTestCase):
    """Cluster 9h: scoped realtime delivery."""

    # -- 9.44 ----------------------------------------------------------
    def test_9_44_default_is_one_shared_group(self) -> None:
        """
        Scenario 9.44: without hooks everything stays as before.
        Given: no MUTATION_GROUPS/USER_GROUPS
        When: a mutation is broadcast and a socket connects
        Then: the event goes to "model_data_update" with the unchanged payload,
              and the socket joins exactly that group.
        """
        sent = _sent_groups(lambda: broadcast_model_mutation("invoice", "updated", "invoice_1", target=TenantRecord("a")))
        self.assertEqual(
            sent,
            [(MODEL_MUTATION_GROUP, {"type": "record_mutation",
                                     "payload": {"model_name": "invoice", "action": "updated", "record_id": "invoice_1"}})],
        )
        consumer = _consumer(ModelDataUpdateConsumer, SimpleNamespace(is_authenticated=True, tenant="a"))
        async_to_sync(consumer.connect)()
        consumer.channel_layer.group_add.assert_awaited_once_with(MODEL_MUTATION_GROUP, "test-channel")

    # -- 9.45 ----------------------------------------------------------
    def test_9_45_model_can_opt_out(self) -> None:
        """
        Scenario 9.45: broadcast_mutations = False silences a model.
        Given: a model with broadcast_mutations = False
        When: one of its records changes
        Then: nothing is sent.
        """
        self.assertEqual(_sent_groups(lambda: broadcast_model_mutation("quiet", "updated", target=QuietModel)), [])

    # -- 9.46 ----------------------------------------------------------
    def test_9_46_events_reach_only_matching_groups(self) -> None:
        """
        Scenario 9.46: with the hooks an event reaches only its own groups.
        Given: MUTATION_GROUPS by record tenant and USER_GROUPS by user tenant
        When: a tenant-a record changes, a tenant-b user's socket connects and
              an anonymous socket connects
        Then: the event goes to "model_data_update.a" only; the tenant-b socket
              joins "model_data_update.b" only; the anonymous socket joins nothing.
        """
        with override_extensions(**HOOKS):
            sent = _sent_groups(lambda: broadcast_model_mutation("invoice", "updated", target=TenantRecord("a")))
            self.assertEqual([group for group, _ in sent], ["model_data_update.a"])
            consumer = _consumer(ModelDataUpdateConsumer, SimpleNamespace(is_authenticated=True, tenant="b"))
            async_to_sync(consumer.connect)()
            consumer.channel_layer.group_add.assert_awaited_once_with("model_data_update.b", "test-channel")
            anonymous = _consumer(ModelDataUpdateConsumer, SimpleNamespace(is_authenticated=False))
            async_to_sync(anonymous.connect)()
            anonymous.channel_layer.group_add.assert_not_awaited()

    # -- 9.47 ----------------------------------------------------------
    def test_9_47_unsafe_group_names_are_sanitised(self) -> None:
        """
        Scenario 9.47: group names stay valid for the channel layer.
        Given: a tenant name with spaces and slashes
        When: its event groups are computed
        Then: unsafe characters become "_" and the name stays under 100 characters.
        """
        with override_extensions(**HOOKS):
            (group,) = realtime.event_groups("update_calculation_status", TenantRecord("acme / north" + "x" * 200))
        self.assertTrue(group.startswith("update_calculation_status.acme___north"))
        self.assertLess(len(group), 100)

    # -- 9.48 ----------------------------------------------------------
    def test_9_48_calculation_status_sockets_join_user_groups(self) -> None:
        """
        Scenario 9.48: calculation-status sockets follow the same groups.
        Given: the hooks and a tenant-b user with an empty active-calculation snapshot
        When: the user's calculation-status socket connects
        Then: it joins "update_calculation_status.b" only and receives the reconciliation.
        """
        with override_extensions(**HOOKS), mock.patch(
            "lex.api.consumers.UpdateCalculationStatusConsumer.ActiveCalculationStateStore.snapshot", return_value=[]
        ):
            consumer = _consumer(UpdateCalculationStatusConsumer, SimpleNamespace(is_authenticated=True, tenant="b"))
            async_to_sync(consumer.connect)()
            UpdateCalculationStatusConsumer.active_consumers.discard(consumer)
        consumer.channel_layer.group_add.assert_awaited_once_with("update_calculation_status.b", "test-channel")
        consumer.send.assert_awaited()
