# Cluster-Wide Cascade Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `CalculationModel.cancel()` revoke every descendant Celery task across all worker pods — not just the ones registered in the backend process's in-memory store — so a cancellation actually stops the whole running calculation tree.

**Architecture:** Add a thin, best-effort Redis "cluster cancel index" alongside the existing per-process `ActiveCalculationStateStore`. Each calculation node's `task_id` is written through to a Redis HASH keyed by `calculation_id` at the existing `set_task_id`/`clear` chokepoints. `cancel()` reads that HASH to discover the full tree cluster-wide and signal-revokes every node (the primary kill). A `calculation_id`-keyed "cancelled" marker, checked once at `calc_and_save` start, is a non-relied-upon net for the late-booting-pod case. Every Redis op degrades to a silent no-op when Redis is unavailable, `CELERY_ACTIVE` is off, or `LEX_CLUSTER_CANCEL_ENABLED=false`, so local/sync/test execution is unchanged.

**Tech Stack:** Python 3, Django, Celery (Redis broker on `…/1`), `redis-py`, pytest via `python -m lex pytest`.

**Design spec:** [`docs/superpowers/specs/2026-06-05-cluster-cascade-cancellation-design.md`](2026-06-05-cluster-cascade-cancellation-design.md)

**Branch:** all work lands on `feat/cluster-cascade-cancellation` (already created off `lex-app-v2`; the spec is already committed there). The branch ruleset forbids pushing directly to `lex-app-v2` — open a PR at the end.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `lex/core/cancellation/__init__.py` | New package marker | Create |
| `lex/core/cancellation/cluster_cancel_index.py` | Owns the redis-py client + tree/marker operations + graceful degradation + config reads. Single home for all `lex:calc:*` Redis access. | Create |
| `lex/lex_app/settings.py` | Declares the three `LEX_CLUSTER_CANCEL_*` knobs | Modify (`:438` area) |
| `lex/core/signals/ActiveCalculationStateStore.py` | `set_task_id` / `clear` write through to the index | Modify (`set_task_id` `:98`, `clear` `:192`) |
| `lex/core/models/CalculationModel.py` | `cancel()` unions Redis tree with in-memory descendants, sets marker, revokes the full set | Modify (`cancel` `:370`) |
| `lex/lex_app/celery_tasks.py` | `calc_and_save` start-of-task cooperative marker check | Modify (`calc_and_save` `:802`) |
| `lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py` | Cluster 8v test batch covering all of the above | Create |

**Cluster allocation (lex-testing):** batch **8v**, scenarios **8.78–8.88**, cluster 8 ("Celery & Async"). Highest cluster-8 letter currently in use is `8u`; highest scenario is `8.77`. Before scaffolding the test file in Task 7, the executing agent MUST run the **lex-testing** skill Step 5 confirmation against the live `lex/test_project/test-plan/test-writing-plan.md` (letters/scenarios may have advanced since this plan was written) and adjust the letter/range if `8v`/`8.78` is taken.

---

## Task 1: Cluster cancel index module

**Files:**
- Create: `lex/core/cancellation/__init__.py`
- Create: `lex/core/cancellation/cluster_cancel_index.py`
- Create (test): `lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py`

- [ ] **Step 1: Create the empty package marker**

Create `lex/core/cancellation/__init__.py` with a one-line module docstring:

```python
"""Cluster-wide cancellation support (Redis cancel index)."""
```

- [ ] **Step 2: Write the failing tests for the index module**

Create `lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m lex pytest lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lex.core.cancellation.cluster_cancel_index'`.

- [ ] **Step 4: Implement the index module**

Create `lex/core/cancellation/cluster_cancel_index.py`:

```python
"""Cluster-wide cancellation index backed by Redis.

Additive, best-effort sidecar to the per-process
``ActiveCalculationStateStore``. It records each calculation node's
Celery ``task_id`` under a tree keyed by ``calculation_id`` so the
backend's ``CalculationModel.cancel`` can discover and revoke every
descendant task regardless of which worker pod registered it, and
exposes a cooperative "cancelled" marker a late-booting worker checks
at task start.

Every operation degrades to a silent no-op when Redis is unavailable,
``CELERY_ACTIVE`` is off, or ``LEX_CLUSTER_CANCEL_ENABLED`` is false, so
local/sync/test execution is unaffected. The keys are namespaced with
the instance identifier (matching Celery's broker ``global_keyprefix``)
so instances sharing a Redis never collide.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_client = None
_client_initialised = False


def _settings():
    from django.conf import settings

    return settings


def _enabled_by_config() -> bool:
    s = _settings()
    if not getattr(s, "CELERY_ACTIVE", False):
        return False
    return bool(getattr(s, "LEX_CLUSTER_CANCEL_ENABLED", True))


def _tree_ttl() -> int:
    return int(getattr(_settings(), "LEX_CLUSTER_CANCEL_TREE_TTL_SECONDS", 14400))


def _marker_ttl() -> int:
    return int(getattr(_settings(), "LEX_CLUSTER_CANCEL_MARKER_TTL_SECONDS", 3600))


def _key_prefix() -> str:
    return f"{os.getenv('INSTANCE_RESOURCE_IDENTIFIER', 'celery')}:"


def _tree_key(calculation_id: str) -> str:
    return f"{_key_prefix()}lex:calc:tree:{calculation_id}"


def _marker_key(calculation_id: str) -> str:
    return f"{_key_prefix()}lex:calc:cancelled:{calculation_id}"


def _get_client():
    """Lazily build a redis-py client from the Celery broker URL.

    Returns ``None`` (so callers no-op) whenever the index is disabled
    by config or the client cannot be constructed.
    """
    global _client, _client_initialised
    if not _enabled_by_config():
        return None
    if _client_initialised:
        return _client
    _client_initialised = True
    try:
        import redis

        url = getattr(_settings(), "CELERY_BROKER_URL", None)
        _client = redis.from_url(url, decode_responses=True) if url else None
    except Exception:
        logger.warning(
            "Cluster cancel index: redis client unavailable; "
            "falling back to signals-only cancellation",
            exc_info=True,
        )
        _client = None
    return _client


def reset_client_cache() -> None:
    """Drop the cached client. Used by tests after changing settings."""
    global _client, _client_initialised
    _client = None
    _client_initialised = False


def register_task(
    calculation_id: Optional[str], record_id: str, task_id: Optional[str]
) -> None:
    """Record ``record_id -> task_id`` in the tree for ``calculation_id``."""
    if not calculation_id or not record_id or not task_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        key = _tree_key(calculation_id)
        client.hset(key, record_id, str(task_id))
        client.expire(key, _tree_ttl())
    except Exception:
        logger.warning(
            "Cluster cancel index: register_task failed for %s", record_id,
            exc_info=True,
        )


def unregister_task(calculation_id: Optional[str], record_id: str) -> None:
    """Remove ``record_id`` from the tree (node reached a terminal state)."""
    if not calculation_id or not record_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.hdel(_tree_key(calculation_id), record_id)
    except Exception:
        logger.warning(
            "Cluster cancel index: unregister_task failed for %s", record_id,
            exc_info=True,
        )


def get_tree(calculation_id: Optional[str]) -> Dict[str, str]:
    """Return ``{record_id: task_id}`` for every node in the tree."""
    if not calculation_id:
        return {}
    client = _get_client()
    if client is None:
        return {}
    try:
        return dict(client.hgetall(_tree_key(calculation_id)) or {})
    except Exception:
        logger.warning(
            "Cluster cancel index: get_tree failed for %s", calculation_id,
            exc_info=True,
        )
        return {}


def mark_cancelled(calculation_id: Optional[str]) -> None:
    """Set the cooperative cancelled marker (TTL-bounded)."""
    if not calculation_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.set(_marker_key(calculation_id), "1", ex=_marker_ttl())
    except Exception:
        logger.warning(
            "Cluster cancel index: mark_cancelled failed for %s", calculation_id,
            exc_info=True,
        )


def is_cancelled(calculation_id: Optional[str]) -> bool:
    """True when the cooperative cancelled marker is set."""
    if not calculation_id:
        return False
    client = _get_client()
    if client is None:
        return False
    try:
        return bool(client.exists(_marker_key(calculation_id)))
    except Exception:
        logger.warning(
            "Cluster cancel index: is_cancelled failed for %s", calculation_id,
            exc_info=True,
        )
        return False
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m lex pytest lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py -v`
Expected: PASS — 7 tests (8.78–8.84).

- [ ] **Step 6: Commit**

```bash
git add lex/core/cancellation/__init__.py \
        lex/core/cancellation/cluster_cancel_index.py \
        lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py
git commit -m "feat(cancellation): add cluster cancel index (Redis tree + marker)"
```

---

## Task 2: Settings knobs

**Files:**
- Modify: `lex/lex_app/settings.py` (after the `CELERY_RETRY_DELAY` line, ~`:438`)

- [ ] **Step 1: Add the three config knobs**

In `lex/lex_app/settings.py`, immediately after this existing block:

```python
CELERY_MAX_RETRIES = int(os.getenv("CELERY_MAX_RETRIES", "3"))
CELERY_RETRY_DELAY = int(os.getenv("CELERY_RETRY_DELAY", "60"))  # seconds
```

insert:

```python
# Cluster-wide cascade cancellation (Redis cancel index).
# When enabled, CalculationModel.cancel discovers child task_ids that
# other worker pods registered (via the Redis tree index) and revokes
# them too. Inert whenever CELERY_ACTIVE is off or no Redis is reachable.
LEX_CLUSTER_CANCEL_ENABLED = (
    os.getenv("LEX_CLUSTER_CANCEL_ENABLED", "true").lower() == "true"
)
LEX_CLUSTER_CANCEL_TREE_TTL_SECONDS = int(
    os.getenv("LEX_CLUSTER_CANCEL_TREE_TTL_SECONDS", "14400")  # 4 hours
)
LEX_CLUSTER_CANCEL_MARKER_TTL_SECONDS = int(
    os.getenv("LEX_CLUSTER_CANCEL_MARKER_TTL_SECONDS", "3600")  # 1 hour
)
```

- [ ] **Step 2: Verify the settings import cleanly**

Run: `python -m lex pytest lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py -v`
Expected: PASS — same 7 tests still green (the `@override_settings` defaults now match real module-level settings).

- [ ] **Step 3: Commit**

```bash
git add lex/lex_app/settings.py
git commit -m "feat(cancellation): add LEX_CLUSTER_CANCEL_* settings knobs"
```

---

## Task 3: Write-through from ActiveCalculationStateStore

**Files:**
- Modify: `lex/core/signals/ActiveCalculationStateStore.py` (`set_task_id` `:98`, `clear` `:192`)
- Test: append to `lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py`

- [ ] **Step 1: Write the failing write-through tests**

Append to the test file:

```python
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore


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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m lex pytest "lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py::TestCluster08v_StoreWriteThrough" -v`
Expected: FAIL — `register_task` / `unregister_task` are never called (no write-through yet).

- [ ] **Step 3: Wire set_task_id**

In `lex/core/signals/ActiveCalculationStateStore.py`, replace the existing `set_task_id` body (`:98`–`:111`):

```python
    @classmethod
    def set_task_id(cls, record_id: str, task_id: Optional[str]) -> None:
        """Attach a Celery ``task_id`` to a tracked record.

        Called by :meth:`CalculationModel.dispatch_calculation_task` right
        after Celery returns the ``AsyncResult``. The task ID is the only
        handle the cancellation endpoint has to revoke the work — without
        it, Celery-only cancel cannot terminate the worker.
        """
        if not record_id or not task_id:
            return
        calculation_id = None
        with cls._lock:
            entry = cls._state_map.get(record_id)
            if isinstance(entry, dict):
                entry["task_id"] = str(task_id)
                calculation_id = entry.get("calculation_id") or None
        # Mirror into the cluster cancel index so other processes (the
        # backend running cancel()) can discover this node's task_id.
        # Best-effort: a Redis failure never breaks registration.
        if calculation_id:
            try:
                from lex.core.cancellation import cluster_cancel_index

                cluster_cancel_index.register_task(
                    calculation_id, record_id, str(task_id)
                )
            except Exception:  # pragma: no cover — defensive
                logger.warning(
                    "Failed to mirror task_id into cluster cancel index for %s",
                    record_id,
                    exc_info=True,
                )
```

- [ ] **Step 4: Wire clear**

In the same file, replace the existing `clear` body (`:192`–`:197`):

```python
    @classmethod
    def clear(cls, record_id: str) -> None:
        """Remove a record from the active-calculations store (terminal state reached)."""
        if not record_id:
            return
        calculation_id = None
        with cls._lock:
            entry = cls._state_map.pop(record_id, None)
            if isinstance(entry, dict):
                calculation_id = entry.get("calculation_id") or None
        if calculation_id:
            try:
                from lex.core.cancellation import cluster_cancel_index

                cluster_cancel_index.unregister_task(calculation_id, record_id)
            except Exception:  # pragma: no cover — defensive
                logger.warning(
                    "Failed to remove task_id from cluster cancel index for %s",
                    record_id,
                    exc_info=True,
                )
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m lex pytest "lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py::TestCluster08v_StoreWriteThrough" -v`
Expected: PASS — 2 tests (8.85, 8.86).

- [ ] **Step 6: Commit**

```bash
git add lex/core/signals/ActiveCalculationStateStore.py \
        lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py
git commit -m "feat(cancellation): write task_ids through to the cluster cancel index"
```

---

## Task 4: cancel() cluster-wide discovery + revoke

**Files:**
- Modify: `lex/core/models/CalculationModel.py` (`cancel` `:370`–`:502`)
- Test: append to `lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py`

- [ ] **Step 1: Write the failing cancel-union test**

Append to the test file:

```python
class TestCluster08v_CancelUnionsClusterTree(TestCase):
    """Cluster 8v: cancel() revokes tasks discovered only in Redis."""

    def setUp(self):
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
        from lex.test_project.tests.celery_async.models import CelerySyncCalc

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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m lex pytest "lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py::TestCluster08v_CancelUnionsClusterTree" -v`
Expected: FAIL — `task-child` is never revoked (cancel() does not yet read the Redis tree).

- [ ] **Step 3: Add cluster discovery to cancel()**

In `lex/core/models/CalculationModel.py`, inside `cancel()`, locate the descendant-collection block (currently `:438`–`:444`):

```python
        descendants = []
        if recursive and calculation_id:
            descendants = [
                d
                for d in ActiveCalculationStateStore.find_descendants(calculation_id)
                if d.get("record_id") != record_id
            ]
```

Immediately **after** it, insert:

```python
        # Cluster-wide discovery: child task_ids registered by *other*
        # worker pods are invisible to this process's in-memory store.
        # Pull them from the Redis cancel index so cancel() can revoke
        # the whole tree, and set the cooperative marker so a late-booting
        # pod self-aborts. Best-effort; empty/no-op without Redis.
        from lex.core.cancellation import cluster_cancel_index

        cluster_tree = {}
        if recursive and calculation_id:
            cluster_tree = cluster_cancel_index.get_tree(calculation_id)
            cluster_cancel_index.mark_cancelled(calculation_id)
```

- [ ] **Step 4: Extend the early-return guard**

A few lines down, change the guard (currently `:446`):

```python
        if not task_id and not descendants:
```

to:

```python
        if not task_id and not descendants and not cluster_tree:
```

- [ ] **Step 5: Revoke the cluster-discovered tasks**

Locate the end of the in-memory descendant loop, immediately **before** the `# Persist CANCELLED on the primary target.` comment (currently `:487`). Insert:

```python
        # Revoke any cluster-discovered task not already handled above
        # (children that ran on other pods). The worker's on_failure maps
        # the resulting Terminated/TaskRevokedError to CANCELLED and the
        # merged self-termination work makes the now-idle pod exit.
        already_revoked = set(revoked)
        for cluster_record_id, cluster_task in cluster_tree.items():
            if not cluster_task or cluster_task in already_revoked:
                continue
            if cluster_record_id == record_id:
                continue
            try:
                cls._revoke_celery_task(cluster_task)
                revoked.append(cluster_task)
                already_revoked.add(cluster_task)
            except Exception:  # pragma: no cover — best-effort
                logger.warning(
                    "Failed to revoke cluster-discovered task %s during cancel of %s",
                    cluster_task,
                    record_id,
                    exc_info=True,
                )
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m lex pytest "lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py::TestCluster08v_CancelUnionsClusterTree" -v`
Expected: PASS — 1 test (8.87).

- [ ] **Step 7: Run the existing cancellation suites to confirm no regression**

Run: `python -m lex pytest lex/test_project/tests/celery_async/test_8u_cancel_revoke.py lex/test_project/tests/calculations/test_7n_cancellation.py -v`
Expected: PASS — both suites green, unchanged (cluster discovery is a no-op without Redis).

- [ ] **Step 8: Commit**

```bash
git add lex/core/models/CalculationModel.py \
        lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py
git commit -m "feat(cancellation): cancel() revokes cluster-discovered descendant tasks"
```

---

## Task 5: calc_and_save cooperative marker check

**Files:**
- Modify: `lex/lex_app/celery_tasks.py` (`calc_and_save` `:802`)
- Test: append to `lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py`

- [ ] **Step 1: Write the failing cooperative-check tests**

Append to the test file:

```python
class TestCluster08v_CooperativeMarkerCheck(SimpleTestCase):
    """Cluster 8v: calc_and_save aborts when the cancelled marker is set."""

    def test_08_88_calc_and_save_aborts_when_marker_set(self):
        """
        Scenario 8.88: a late pod pulling a cancelled task self-aborts.
        Given: the cancelled marker is set for the active calculation_id.
        When:  calc_and_save runs (as a late-booting pod would).
        Then:  it raises CalculationCancelled before touching any model,
               so on_failure records CANCELLED instead of running to SUCCESS.
        """
        from lex.core.models.CalculationModel import CalculationCancelled
        from lex.lex_app.celery_tasks import calc_and_save

        model = mock.MagicMock(name="model")
        with mock.patch(
            "lex.api.utils.operation_context"
        ) as op_ctx, mock.patch.object(idx, "is_cancelled", return_value=True):
            op_ctx.get.return_value = {"calculation_id": "calc-1"}
            with self.assertRaises(CalculationCancelled):
                calc_and_save([model])
        model.lex_func.assert_not_called()

    def test_08_89_calc_and_save_runs_when_marker_absent(self):
        """
        Scenario 8.89: no marker => the calculation runs normally.
        Given: is_cancelled returns False for the calculation_id.
        When:  calc_and_save runs.
        Then:  the model's lex_func/save are invoked (no spurious abort);
               the cooperative net never blocks healthy work.
        """
        from lex.lex_app.celery_tasks import calc_and_save

        model = mock.MagicMock(name="model")
        model.lex_func.return_value = lambda: None
        with mock.patch(
            "lex.api.utils.operation_context"
        ) as op_ctx, mock.patch.object(idx, "is_cancelled", return_value=False):
            op_ctx.get.return_value = {"calculation_id": "calc-1"}
            summary = calc_and_save([model])
        model.lex_func.assert_called()
        self.assertEqual(summary["processed_successfully"], 1)
```

> Note: `calc_and_save` is wrapped by `lex_shared_task`, which returns `(result, args)` when no `context`/`model_context` kwargs are passed it still returns the raw function result. Calling `calc_and_save([model])` with no `context` kwarg runs the wrapper's `else` branch and returns the function's own return value (the summary dict). The marker check runs inside the function body, so the raise propagates through the wrapper's `except`/`raise`.

- [ ] **Step 2: Run to verify failure**

Run: `python -m lex pytest "lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py::TestCluster08v_CooperativeMarkerCheck" -v`
Expected: FAIL — 8.88 does not raise (no marker check yet); `model.lex_func` is called.

- [ ] **Step 3: Add the cooperative check to calc_and_save**

In `lex/lex_app/celery_tasks.py`, replace the start of `calc_and_save` (currently `:802`–`:811`):

```python
@lex_shared_task
def calc_and_save(models: List[Model], *args, **kwargs):
    """
    Calculates and saves a list of models.
    Aborts the entire batch immediately if any error occurs.
    """
    summary = {
        "total_models": len(models),
        "processed_successfully": 0,
        "errors": 0
    }
```

with:

```python
@lex_shared_task
def calc_and_save(models: List[Model], *args, **kwargs):
    """
    Calculates and saves a list of models.
    Aborts the entire batch immediately if any error occurs.

    Cooperative cancellation net (not the primary mechanism): if the
    calculation's cancelled marker is already set in the cluster cancel
    index, abort before running anything. This catches a child task that
    a late-booting worker pod pulled *after* cancel()'s revoke broadcast,
    so it lands CANCELLED instead of computing to completion. Signal
    revoke remains the relied-upon kill; this is skipped without Redis.
    """
    from lex.core.cancellation import cluster_cancel_index
    from lex.core.models.CalculationModel import CalculationCancelled

    try:
        from lex.api.utils import operation_context

        _calc_id = operation_context.get().get("calculation_id") or ""
    except Exception:
        _calc_id = ""
    if _calc_id and cluster_cancel_index.is_cancelled(_calc_id):
        raise CalculationCancelled("Calculation cancelled before task start")

    summary = {
        "total_models": len(models),
        "processed_successfully": 0,
        "errors": 0
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m lex pytest "lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py::TestCluster08v_CooperativeMarkerCheck" -v`
Expected: PASS — 2 tests (8.88, 8.89).

- [ ] **Step 5: Run the full 8v file**

Run: `python -m lex pytest lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py -v`
Expected: PASS — all 8v scenarios (8.78–8.89).

- [ ] **Step 6: Commit**

```bash
git add lex/lex_app/celery_tasks.py \
        lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py
git commit -m "feat(cancellation): calc_and_save self-aborts on the cooperative marker"
```

---

## Task 6: Full regression run

**Files:** none (verification only)

- [ ] **Step 1: Run the calculation + celery_async cluster suites**

Run:
```bash
python -m lex pytest lex/test_project/tests/celery_async lex/test_project/tests/calculations -v
```
Expected: PASS — no regressions across the Celery/async and calculation clusters.

- [ ] **Step 2: Run with coverage on the touched modules**

Run:
```bash
python -m lex pytest --cov=lex.core.cancellation --cov=lex.core.signals.ActiveCalculationStateStore \
  lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py
```
Expected: PASS; `cluster_cancel_index.py` shows high line coverage (>90%).

---

## Task 7: Bring the test-plan into sync (lex-testing Step 7 — mandatory)

**Files:**
- Modify: `lex/test_project/test-plan/progress/session-log.md`
- Modify: `lex/test_project/test-plan/test-clusters.md`
- Modify: `lex/test_project/test-plan/progress/dashboard.md`
- Modify: `lex/test_project/test-plan/test-writing-plan.md`

- [ ] **Step 1: Re-read the current plan state**

Read all four files above. Confirm the final allocated letter/scenarios (this plan assumed **8v / 8.78–8.89**; if the live plan has advanced, use the real next-free values and rename the test file + classes accordingly before continuing).

- [ ] **Step 2: Append a session-log row**

Append (append-only, bottom row) to `lex/test_project/test-plan/progress/session-log.md` a row recording: batch 8v, scenarios 8.78–8.89, file `test_8v_cluster_cascade_cancel.py`, source files covered (cluster_cancel_index, ActiveCalculationStateStore, CalculationModel.cancel, calc_and_save), measured pass count, type U+I.

- [ ] **Step 3: Update the cluster status + scenario range**

In `lex/test_project/test-plan/test-clusters.md`, bump cluster 8's max scenario to 8.89 and add the 8v batch description. Mirror the change in `lex/test_project/test-plan/progress/dashboard.md`.

- [ ] **Step 4: Append the batch row**

In `lex/test_project/test-plan/test-writing-plan.md`, append a batch row matching the most recent row's shape: batch 8v, scenario range 8.78–8.89, type U+I, files covered, test file path, test classes (`TestCluster08v_*`), fixtures (none), Status ✅ Complete with the real pass count.

- [ ] **Step 5: Commit**

```bash
git add lex/test_project/test-plan/
git commit -m "docs(test-plan): record cluster 8v cascade-cancellation batch"
```

---

## Task 8: Open the pull request

**Files:** none

- [ ] **Step 1: Push the branch and open the PR**

```bash
git push -u origin feat/cluster-cascade-cancellation
gh pr create --base lex-app-v2 --title "feat(cancellation): cluster-wide cascade cancellation" \
  --body "$(cat <<'EOF'
## Summary
- Add a Redis cluster cancel index so CalculationModel.cancel() discovers and revokes child Celery tasks registered on other worker pods (fixes dangling workers after a cancellation).
- Cooperative cancelled-marker checked at calc_and_save start as a non-relied-upon net for the late-booting-pod case.
- Fully inert when Redis is down, CELERY_ACTIVE is off, or LEX_CLUSTER_CANCEL_ENABLED=false — local/sync/test behaviour unchanged.

Design: docs/superpowers/specs/2026-06-05-cluster-cascade-cancellation-design.md

## Test plan
- [ ] python -m lex pytest lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py -v
- [ ] python -m lex pytest lex/test_project/tests/celery_async lex/test_project/tests/calculations -v (no regressions)
- [ ] Cluster acceptance: cancel a parent whose children run on multiple pods → every descendant worker terminates; an over-provisioned late pod self-aborts a queued child.
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** §3.1 Redis data model → Task 1 module + key helpers; §3.2 write-through → Task 3; §3.3 cancel flow → Task 4; §3.4 cooperative net → Task 5; §3.5 config → Task 2; §5 testing → Tasks 1/3/4/5 + Task 7 sync; §5a deferred queue-purge → intentionally NOT implemented (documented as deferred). All covered.
- **Type consistency:** module public API used identically across tasks — `register_task(calculation_id, record_id, task_id)`, `unregister_task(calculation_id, record_id)`, `get_tree(calculation_id) -> dict`, `mark_cancelled(calculation_id)`, `is_cancelled(calculation_id) -> bool`, `reset_client_cache()`, `_get_client()`, `_tree_key`/`_marker_key`. `cancel()` keeps its existing return keys.
- **No placeholders:** every code step shows complete code; every run step shows the exact command and expected result.
