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
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

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


class TestCluster08v_StoreWriteThrough(SimpleTestCase):
    """Cluster 8v: set_task_id / clear mirror into the cluster index."""

    def setUp(self):
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)

    def test_08_85_set_task_id_registers_in_cluster_index(self):
        """
        Scenario 8.85: attaching a task_id writes through to the index.
        Given: a tracked record with a known calculation_id.
        When:  set_task_id attaches its Celery task_id.
        Then:  cluster_cancel_index.register_task is called with that
               calculation_id, record_id, and task_id — making the node
               discoverable cluster-wide.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="m_5", calculation_id="calc-1", record="m_5",
            model_label="lex_app.celerysynccalc", record_pk=5,
        )
        with mock.patch.object(idx, "register_task") as register:
            ActiveCalculationStateStore.set_task_id("m_5", "task-9")
        register.assert_called_once_with("calc-1", "m_5", "task-9")

    def test_08_86_clear_unregisters_from_cluster_index(self):
        """
        Scenario 8.86: a terminal node leaves the cluster index.
        Given: a tracked record with a calculation_id.
        When:  clear() removes it from the in-memory store.
        Then:  cluster_cancel_index.unregister_task is called with the
               record's calculation_id and record_id.
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="m_5", calculation_id="calc-1", record="m_5",
            model_label="lex_app.celerysynccalc", record_pk=5,
        )
        with mock.patch.object(idx, "unregister_task") as unregister:
            ActiveCalculationStateStore.clear("m_5")
        unregister.assert_called_once_with("calc-1", "m_5")


from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc


class TestCluster08v_CancelUnionsClusterTree(E2ETestCase):
    """Cluster 8v: cancel() revokes tasks discovered only in Redis."""

    e2e_models = [CelerySyncCalc]
    e2e_unpatch = {"mark_in_progress"}

    def setUp(self):
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)

    def test_08_87_cancel_revokes_cluster_discovered_descendant(self):
        """
        Scenario 8.87: a child registered only on another pod is revoked.
        Given: a parent (in-memory, with task_id) whose child task_id
               lives only in the Redis tree (the cross-pod case).
        When:  CalculationModel.cancel(parent) runs.
        Then:  _revoke_celery_task is called for BOTH the parent's task
               and the cluster-discovered child task; revoked_tasks
               reflects the full cluster set — no dangling worker.
        """
        from lex.core.models.CalculationModel import CalculationModel

        parent = CelerySyncCalc.objects.create(name="parent")
        parent.is_calculated = CalculationModel.IN_PROGRESS
        parent.save(skip_hooks=True)

        record_id = f"{parent._meta.model_name}_{parent.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id, calculation_id="calc-1", record=str(parent),
            model_label=parent._meta.label_lower, record_pk=parent.pk,
        )
        # Parent's own task is in memory; the child task is only in Redis.
        with mock.patch.object(idx, "register_task"):
            ActiveCalculationStateStore.set_task_id(record_id, "task-parent")

        revoked = []
        with mock.patch.object(
            CalculationModel, "_revoke_celery_task",
            side_effect=lambda tid: revoked.append(tid),
        ), mock.patch.object(
            idx, "get_tree",
            return_value={record_id: "task-parent", "celerysynccalc_999": "task-child"},
        ), mock.patch.object(idx, "mark_cancelled") as mark:
            result = CalculationModel.cancel(parent)

        self.assertIn("task-parent", revoked)
        self.assertIn(
            "task-child", revoked,
            msg="cluster-discovered child task must be revoked too",
        )
        self.assertIn("task-child", result["revoked_tasks"])
        mark.assert_called_once_with("calc-1")
