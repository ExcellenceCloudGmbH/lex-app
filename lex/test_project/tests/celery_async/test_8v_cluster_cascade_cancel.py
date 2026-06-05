"""Cluster-wide cascade cancellation — Redis cancel index + wiring.

Intent
------
``CalculationModel.cancel`` must stop the *whole* running calculation
tree, including child calculations that a different worker pod started
and registered only in that pod's memory. The cluster cancel index is
the Redis sidecar that makes those child ``task_id``s discoverable so
``cancel`` can signal-revoke every node. A regression here means a user
presses "abort", sees CANCELLED, yet dangling workers keep computing —
the exact bug this feature fixes.

Cluster 8v — scenarios 8.78–8.88. Type: U (index/write-through/cooperative,
mocked redis client) + I (cancel() union, real model row).
Covers: lex/core/cancellation/cluster_cancel_index.py,
lex/core/signals/ActiveCalculationStateStore.py,
lex/core/models/CalculationModel.py, lex/lex_app/celery_tasks.py.
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py -v
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.test import SimpleTestCase, TestCase, override_settings

from lex.core.cancellation import cluster_cancel_index as idx

pytestmark = pytest.mark.celery_async


@override_settings(
    CELERY_ACTIVE=True,
    LEX_CLUSTER_CANCEL_ENABLED=True,
    LEX_CLUSTER_CANCEL_TREE_TTL_SECONDS=14400,
    LEX_CLUSTER_CANCEL_MARKER_TTL_SECONDS=3600,
    CELERY_BROKER_URL="redis://localhost:6379/1",
)
class TestCluster08v_IndexOperations(SimpleTestCase):
    """Cluster 8v: cluster_cancel_index Redis operations (mocked client)."""

    def setUp(self):
        idx.reset_client_cache()
        self.addCleanup(idx.reset_client_cache)

    def test_08_78_register_task_hsets_tree_and_refreshes_ttl(self):
        """
        Scenario 8.78: register_task writes the node into the tree HASH.
        Given: an enabled index with a mocked redis client.
        When:  register_task("calc-1", "m_5", "task-9") is called.
        Then:  HSET targets the instance-namespaced tree key with
               record_id->task_id and the tree TTL is refreshed.
        """
        client = mock.MagicMock()
        with mock.patch.object(idx, "_get_client", return_value=client):
            idx.register_task("calc-1", "m_5", "task-9")
        client.hset.assert_called_once_with(
            idx._tree_key("calc-1"), "m_5", "task-9"
        )
        client.expire.assert_called_once_with(idx._tree_key("calc-1"), 14400)

    def test_08_79_unregister_task_hdels_node(self):
        """
        Scenario 8.79: unregister_task removes the node from the tree.
        Given: an enabled index with a mocked redis client.
        When:  unregister_task("calc-1", "m_5") is called.
        Then:  HDEL targets the tree key with that record_id.
        """
        client = mock.MagicMock()
        with mock.patch.object(idx, "_get_client", return_value=client):
            idx.unregister_task("calc-1", "m_5")
        client.hdel.assert_called_once_with(idx._tree_key("calc-1"), "m_5")

    def test_08_80_get_tree_returns_hgetall_dict(self):
        """
        Scenario 8.80: get_tree returns the full node->task_id mapping.
        Given: a redis client whose HGETALL yields two nodes.
        When:  get_tree("calc-1") is called.
        Then:  the dict of {record_id: task_id} is returned verbatim.
        """
        client = mock.MagicMock()
        client.hgetall.return_value = {"m_5": "task-9", "m_6": "task-10"}
        with mock.patch.object(idx, "_get_client", return_value=client):
            tree = idx.get_tree("calc-1")
        self.assertEqual(
            tree, {"m_5": "task-9", "m_6": "task-10"},
            msg="get_tree must surface every cluster-registered node",
        )

    def test_08_81_marker_set_and_check_roundtrip(self):
        """
        Scenario 8.81: mark_cancelled SETs the marker; is_cancelled reads it.
        Given: a mocked redis client.
        When:  mark_cancelled then is_cancelled are called.
        Then:  SET uses the marker key + TTL, and is_cancelled reflects EXISTS.
        """
        client = mock.MagicMock()
        client.exists.return_value = 1
        with mock.patch.object(idx, "_get_client", return_value=client):
            idx.mark_cancelled("calc-1")
            cancelled = idx.is_cancelled("calc-1")
        client.set.assert_called_once_with(idx._marker_key("calc-1"), "1", ex=3600)
        self.assertTrue(cancelled, msg="is_cancelled must report a set marker")

    def test_08_82_operations_are_noop_without_client(self):
        """
        Scenario 8.82: every op degrades silently when no client exists.
        Given: _get_client returns None (Redis down / unreachable).
        When:  each public op is called.
        Then:  no exception is raised; reads return empty/false.
        """
        with mock.patch.object(idx, "_get_client", return_value=None):
            idx.register_task("c", "r", "t")   # must not raise
            idx.unregister_task("c", "r")       # must not raise
            idx.mark_cancelled("c")             # must not raise
            self.assertEqual(idx.get_tree("c"), {})
            self.assertFalse(idx.is_cancelled("c"))


class TestCluster08v_IndexDisabled(SimpleTestCase):
    """Cluster 8v: the index is inert when disabled by config."""

    def setUp(self):
        idx.reset_client_cache()
        self.addCleanup(idx.reset_client_cache)

    @override_settings(CELERY_ACTIVE=False, LEX_CLUSTER_CANCEL_ENABLED=True)
    def test_08_83_disabled_when_celery_inactive(self):
        """
        Scenario 8.83: CELERY_ACTIVE off => no client, full no-op.
        Given: CELERY_ACTIVE is False (local/sync/test execution).
        When:  _get_client() is called.
        Then:  it returns None so all ops no-op — local runs are unaffected.
        """
        self.assertIsNone(idx._get_client())

    @override_settings(CELERY_ACTIVE=True, LEX_CLUSTER_CANCEL_ENABLED=False)
    def test_08_84_disabled_when_flag_off(self):
        """
        Scenario 8.84: master switch off => no client, full no-op.
        Given: LEX_CLUSTER_CANCEL_ENABLED is False.
        When:  _get_client() is called.
        Then:  it returns None regardless of CELERY_ACTIVE.
        """
        self.assertIsNone(idx._get_client())
