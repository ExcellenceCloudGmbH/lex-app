# Worker Self-Termination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an idle lex-app Celery worker terminate its own process in the two situations the current `task_postrun` path misses — after a task is cancelled (revoked), and when a KEDA-spawned worker never receives a task — so its pod terminates in the GKE cluster (fixes cluster bugs #1 and #2).

**Architecture:** All changes live in `lex/lex_app/celery.py` plus two config knobs in `lex/lex_app/settings.py`. We extract the existing `lex_shutdown_if_idle` body into a reusable, idempotent `_warm_shutdown_if_idle(exclude_task_ids=())` helper (MainProcess-callable), then add two new MainProcess triggers that call it: a `task_revoked` cancel fast-path and an idle-watchdog daemon thread started on `worker_ready`. Both reuse the existing concurrency-correct `(active | reserved)` idle check, so a worker only exits when every concurrency slot is free. The existing `task_postrun` path is retained unchanged. Everything is gated behind the existing `_is_non_local_deployment_target()` check, so local dev and the framework test suite are unaffected.

**Tech Stack:** Python 3.12, Celery (real dependency, already configured under `lex test`), Django settings, `threading.Timer`/`threading.Event`/`threading.Thread`, `celery.signals` (`task_revoked`, `worker_ready`, `worker_shutting_down`), `celery.worker.state`. Tests: `SimpleTestCase` (Django configured, real celery imported, `celery.worker.state` + `os.kill`/`threading.Timer` mocked).

---

## Background the engineer must know

**Why this is the whole fix.** lex-app worker pods are created by a KEDA `ScaledJob` with `restartPolicy: Never`. When the worker *process* exits cleanly, the pod terminates. lex-app has **no Kubernetes API access** — it cannot delete pods. So the only lever the framework has is: *make the idle worker process exit*. A graceful SIGTERM to the Celery MainProcess is a warm shutdown (finish in-flight work, then exit).

**The existing mechanism we build on** (`lex/lex_app/celery.py`):
- `lex_shutdown_if_idle(panel, completed_task_id=None)` — a `@Panel.register` remote-control command that runs in the worker **MainProcess**. Reads `celery.worker.state.active_requests` / `reserved_requests`, and only if the resulting pending set (minus an excluded id) is empty schedules a SIGTERM on a 50 ms `threading.Timer`.
- `shutdown_worker_after_task_completion` — a `@task_postrun.connect` handler (runs in the pool child) that broadcasts `lex_shutdown_if_idle` to its own MainProcess. Fires on **every** task completion but, because of the `(active | reserved)` check, only terminates the worker once **all** of that worker's concurrency slots are free. **This path is never reached by a worker that completes zero tasks** — that is bug #2.
- `_is_non_local_deployment_target()` — returns `True` only when `DEPLOYMENT_TARGET` is set and not `local`. The gate that keeps shutdown behavior off in local/CI.

**Key safety invariant (do not break):** a parent `CalculationModel` blocking on `WaitForTasks` / `AsyncResult.get()` is an **active request**, so it is never counted as idle. Reusing the existing `active_requests` check preserves this for free — the watchdog can never kill a blocked parent.

**Testing approach.** The pre-existing `lex/tests/unit/infra/test_celery_worker_shutdown.py` uses a heavy `sys.modules` celery-stub style. **Do not copy it.** Under `lex test`, Django *and* real celery are configured, so the new tests import the real module (`from lex.lex_app import celery as celery_mod`) and patch `celery_mod.worker_state`, `celery_mod.threading`/`Timer`, and `celery_mod.os.kill`. This matches the design's stated unit approach and is far simpler.

**Reset module globals between tests.** `_warm_shutdown_if_idle` uses a module-level `_shutdown_scheduled` guard, and the watchdog uses a module-level `_watchdog_stop` Event. Every test that touches them MUST reset them in `setUp`.

---

## File Structure

- **Modify** `lex/lex_app/celery.py` — extract `_read_pending_task_ids` + `_warm_shutdown_if_idle` (with `_shutdown_scheduled` guard); turn `lex_shutdown_if_idle` into a thin wrapper; add `_idle_shutdown_enabled` / `_idle_shutdown_seconds` env helpers; add `task_revoked` handler; add watchdog loop + `worker_ready`/`worker_shutting_down` wiring.
- **Modify** `lex/lex_app/settings.py` — expose `LEX_WORKER_IDLE_SHUTDOWN_ENABLED` / `LEX_WORKER_IDLE_SHUTDOWN_SECONDS` as documented Django settings (single source of truth is the env var; celery.py reads the same env directly for testability).
- **Create** `lex/tests/unit/infra/test_worker_self_termination.py` — unit tests for the helper, both new triggers, the gates, and the idempotency guard.

---

## Task 1: Extract `_warm_shutdown_if_idle` helper + idempotency guard

**Files:**
- Modify: `lex/lex_app/celery.py` (imports at top; refactor `lex_shutdown_if_idle` at lines 36-79)
- Test: `lex/tests/unit/infra/test_worker_self_termination.py` (create)

- [ ] **Step 1: Write the failing test**

Create `lex/tests/unit/infra/test_worker_self_termination.py`:

```python
"""
Unit tests for in-framework worker self-termination
(``lex/lex_app/celery.py``).

What is tested:
  * ``_warm_shutdown_if_idle`` schedules exactly one SIGTERM timer iff the
    worker has no active/reserved task other than the excluded ids.
  * The ``_shutdown_scheduled`` guard prevents a second timer being armed.
  * The ``task_revoked`` cancel fast-path and the idle watchdog call the
    helper, and both respect the non-local + feature-enabled gates.

How it runs:
  Django and real celery are configured under ``lex test``; we import the
  real module and patch ``celery_mod.worker_state``, ``threading.Timer``
  and ``os.kill`` so no signal is actually delivered.
"""
import types
from unittest import mock

from django.test import SimpleTestCase

from lex.lex_app import celery as celery_mod


def _fake_request(task_id):
    return types.SimpleNamespace(id=task_id)


def _patch_state(active_ids=(), reserved_ids=()):
    """Return a context manager patching celery_mod.worker_state."""
    fake_state = types.SimpleNamespace(
        active_requests={_fake_request(i) for i in active_ids},
        reserved_requests={_fake_request(i) for i in reserved_ids},
    )
    return mock.patch.object(celery_mod, "worker_state", fake_state)


class WarmShutdownIfIdleTests(SimpleTestCase):
    def setUp(self):
        # Module-level guard must start clean for every test.
        celery_mod._shutdown_scheduled = False

    def test_schedules_sigterm_when_idle(self):
        with _patch_state(active_ids=(), reserved_ids=()):
            with mock.patch.object(celery_mod.threading, "Timer") as timer:
                result = celery_mod._warm_shutdown_if_idle()
        self.assertTrue(result["shutting_down"])
        timer.assert_called_once()

    def test_no_shutdown_when_other_task_active(self):
        with _patch_state(active_ids=("other-task",)):
            with mock.patch.object(celery_mod.threading, "Timer") as timer:
                result = celery_mod._warm_shutdown_if_idle()
        self.assertFalse(result["shutting_down"])
        self.assertEqual(result["pending_count"], 1)
        timer.assert_not_called()

    def test_excluded_task_id_is_ignored(self):
        with _patch_state(active_ids=("task-123",)):
            with mock.patch.object(celery_mod.threading, "Timer") as timer:
                result = celery_mod._warm_shutdown_if_idle(
                    exclude_task_ids={"task-123"}
                )
        self.assertTrue(result["shutting_down"])
        timer.assert_called_once()

    def test_guard_prevents_second_timer(self):
        with _patch_state(active_ids=(), reserved_ids=()):
            with mock.patch.object(celery_mod.threading, "Timer") as timer:
                first = celery_mod._warm_shutdown_if_idle()
                second = celery_mod._warm_shutdown_if_idle()
        self.assertTrue(first["shutting_down"])
        self.assertTrue(second.get("already_scheduled"))
        timer.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `lex test lex.tests.unit.infra.test_worker_self_termination.WarmShutdownIfIdleTests --noinput --keepdb`
Expected: FAIL — `AttributeError: module 'lex.lex_app.celery' has no attribute '_warm_shutdown_if_idle'`.

- [ ] **Step 3: Write minimal implementation**

In `lex/lex_app/celery.py`, add `time` to the imports block (top of file, alongside `import threading`):

```python
import time
```

Add `from celery.signals import task_postrun` → extend to also import the new signals (do this now so later tasks don't re-touch imports):

```python
from celery.signals import task_postrun, task_revoked, worker_ready, worker_shutting_down
```

Replace the existing `lex_shutdown_if_idle` function (current lines 36-79) with the helper + thin wrapper + module guard:

```python
# Module-level guard so at most one SIGTERM timer is ever armed per process.
_shutdown_lock = threading.Lock()
_shutdown_scheduled = False


def _read_pending_task_ids(exclude_task_ids=()):
    """
    Return the set of task ids this worker still owns (active or reserved),
    minus ``exclude_task_ids``. Runs in the worker MainProcess. Raises if the
    canonical ``celery.worker.state`` bookkeeping cannot be read.
    """
    active_ids = {getattr(req, "id", None) for req in worker_state.active_requests}
    reserved_ids = {getattr(req, "id", None) for req in worker_state.reserved_requests}
    pending = (active_ids | reserved_ids) - {None}
    pending -= set(exclude_task_ids)
    return pending


def _warm_shutdown_if_idle(exclude_task_ids=()):
    """
    Schedule a single graceful SIGTERM (warm shutdown) iff this worker has no
    active or reserved task other than ``exclude_task_ids``. MainProcess-only.

    Idempotent: once a shutdown is scheduled, ``_shutdown_scheduled`` makes
    subsequent calls no-ops so stacked SIGTERM timers can never be armed.

    Returns a small dict for observability.
    """
    global _shutdown_scheduled
    try:
        pending = _read_pending_task_ids(exclude_task_ids)
    except Exception:  # pragma: no cover - defensive
        logger.exception("_warm_shutdown_if_idle: failed to read worker state")
        return {"shutting_down": False, "error": "state_unavailable"}

    if pending:
        return {
            "shutting_down": False,
            "pending_count": len(pending),
            "pending": sorted(pending),
        }

    with _shutdown_lock:
        if _shutdown_scheduled:
            return {"shutting_down": True, "already_scheduled": True}
        _shutdown_scheduled = True

    def _terminate():
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:  # pragma: no cover - defensive
            logger.exception("_warm_shutdown_if_idle: SIGTERM failed")

    threading.Timer(0.05, _terminate).start()
    return {"shutting_down": True}


@Panel.register
def lex_shutdown_if_idle(panel, completed_task_id=None):
    """
    Remote-control command (MainProcess) used by the existing ``task_postrun``
    broadcast path. Thin wrapper over ``_warm_shutdown_if_idle`` that excludes
    the just-completed task id (MainProcess may not have processed the pool's
    "task ready" message yet when this command arrives).
    """
    exclude = {completed_task_id} if completed_task_id else set()
    return _warm_shutdown_if_idle(exclude_task_ids=exclude)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `lex test lex.tests.unit.infra.test_worker_self_termination.WarmShutdownIfIdleTests --noinput --keepdb`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/celery.py lex/tests/unit/infra/test_worker_self_termination.py
git commit -m "refactor(celery): extract idempotent _warm_shutdown_if_idle helper"
```

---

## Task 2: Config knobs — env helpers in celery.py + documented settings

**Files:**
- Modify: `lex/lex_app/celery.py` (add helpers next to `_is_non_local_deployment_target`, ~line 90)
- Modify: `lex/lex_app/settings.py` (after line 476, before the `if LEX_TASK_RECOVERY_ENABLED:` block)
- Test: `lex/tests/unit/infra/test_worker_self_termination.py` (add class)

- [ ] **Step 1: Write the failing test**

Append to `lex/tests/unit/infra/test_worker_self_termination.py`:

```python
class IdleShutdownConfigTests(SimpleTestCase):
    def test_enabled_defaults_true(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("LEX_WORKER_IDLE_SHUTDOWN_ENABLED", None)
            self.assertTrue(celery_mod._idle_shutdown_enabled())

    def test_enabled_can_be_disabled(self):
        with mock.patch.dict(
            "os.environ", {"LEX_WORKER_IDLE_SHUTDOWN_ENABLED": "false"}, clear=False
        ):
            self.assertFalse(celery_mod._idle_shutdown_enabled())

    def test_seconds_defaults_to_30(self):
        import os
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("LEX_WORKER_IDLE_SHUTDOWN_SECONDS", None)
            self.assertEqual(celery_mod._idle_shutdown_seconds(), 30.0)

    def test_seconds_reads_env(self):
        with mock.patch.dict(
            "os.environ", {"LEX_WORKER_IDLE_SHUTDOWN_SECONDS": "12"}, clear=False
        ):
            self.assertEqual(celery_mod._idle_shutdown_seconds(), 12.0)

    def test_seconds_falls_back_on_garbage(self):
        with mock.patch.dict(
            "os.environ", {"LEX_WORKER_IDLE_SHUTDOWN_SECONDS": "notanumber"}, clear=False
        ):
            self.assertEqual(celery_mod._idle_shutdown_seconds(), 30.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `lex test lex.tests.unit.infra.test_worker_self_termination.IdleShutdownConfigTests --noinput --keepdb`
Expected: FAIL — `AttributeError: ... has no attribute '_idle_shutdown_enabled'`.

- [ ] **Step 3: Write minimal implementation**

In `lex/lex_app/celery.py`, immediately after `_is_non_local_deployment_target()` (current line 90), add:

```python
def _idle_shutdown_enabled() -> bool:
    """Master switch for both new self-termination triggers (env-read so the
    unit tests don't need full Django settings)."""
    return os.getenv("LEX_WORKER_IDLE_SHUTDOWN_ENABLED", "true").strip().lower() == "true"


def _idle_shutdown_seconds() -> float:
    """Idle grace before the watchdog terminates a never-busy/idle worker."""
    try:
        return float(os.getenv("LEX_WORKER_IDLE_SHUTDOWN_SECONDS", "30"))
    except (TypeError, ValueError):
        return 30.0
```

In `lex/lex_app/settings.py`, after line 476 (`LEX_TASK_MAX_RETRIES = ...`) and before the blank line preceding `if LEX_TASK_RECOVERY_ENABLED:`, add a documented section (single source of truth is the env var; `celery.py` reads the same env directly):

```python

# ---------------------------------------------------------------------------
# Worker self-termination knobs
# (see docs/superpowers/specs/2026-06-05-worker-self-termination-design.md)
#
# Drive the two in-framework triggers that make an idle worker exit so its
# KEDA ScaledJob pod terminates: a task_revoked cancel fast-path and an idle
# watchdog. Both are additionally gated behind a non-local DEPLOYMENT_TARGET,
# so local dev and CI are unaffected regardless of these values.
# Surfaced here for operator discoverability; lex/lex_app/celery.py reads the
# same env vars directly.
# ---------------------------------------------------------------------------
LEX_WORKER_IDLE_SHUTDOWN_ENABLED = (
    os.getenv("LEX_WORKER_IDLE_SHUTDOWN_ENABLED", "true").strip().lower() == "true"
)
LEX_WORKER_IDLE_SHUTDOWN_SECONDS = int(
    os.getenv("LEX_WORKER_IDLE_SHUTDOWN_SECONDS", "30")
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `lex test lex.tests.unit.infra.test_worker_self_termination.IdleShutdownConfigTests --noinput --keepdb`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/celery.py lex/lex_app/settings.py lex/tests/unit/infra/test_worker_self_termination.py
git commit -m "feat(celery): add idle-shutdown config knobs (enabled + seconds)"
```

---

## Task 3: Trigger A — `task_revoked` cancel fast-path (fixes #1)

**Files:**
- Modify: `lex/lex_app/celery.py` (add handler after `shutdown_worker_after_task_completion`, ~line 146)
- Test: `lex/tests/unit/infra/test_worker_self_termination.py` (add class)

- [ ] **Step 1: Write the failing test**

Append to `lex/tests/unit/infra/test_worker_self_termination.py`:

```python
class TaskRevokedFastPathTests(SimpleTestCase):
    def test_calls_helper_excluding_revoked_id_when_non_local(self):
        request = _fake_request("revoked-1")
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=True):
                with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                    celery_mod.shutdown_worker_after_task_revoked(request=request)
        helper.assert_called_once_with(exclude_task_ids={"revoked-1"})

    def test_noop_when_local(self):
        request = _fake_request("revoked-1")
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=False):
            with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                celery_mod.shutdown_worker_after_task_revoked(request=request)
        helper.assert_not_called()

    def test_noop_when_feature_disabled(self):
        request = _fake_request("revoked-1")
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=False):
                with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                    celery_mod.shutdown_worker_after_task_revoked(request=request)
        helper.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `lex test lex.tests.unit.infra.test_worker_self_termination.TaskRevokedFastPathTests --noinput --keepdb`
Expected: FAIL — `AttributeError: ... has no attribute 'shutdown_worker_after_task_revoked'`.

- [ ] **Step 3: Write minimal implementation**

In `lex/lex_app/celery.py`, after `shutdown_worker_after_task_completion` (current end ~line 146), add:

```python
@task_revoked.connect
def shutdown_worker_after_task_revoked(
    sender=None,
    request=None,
    terminated=None,
    signum=None,
    expired=None,
    **extra,
):
    """
    Cancel fast-path (fixes cluster bug #1). ``CalculationModel.cancel()`` ->
    ``revoke(terminate=True)`` fires ``task_revoked`` in the worker MainProcess.
    If the revoked task was the worker's only work, the worker is now idle and
    we schedule a warm shutdown so the KEDA ScaledJob pod terminates within ~1s.
    A concurrency>1 worker with other live tasks stays up (the revoked id is
    excluded, the siblings keep ``pending`` non-empty).

    Runs in MainProcess, so unlike ``task_postrun`` it does not depend on a
    hard-terminated pool child completing its signal handler.
    """
    if not _is_non_local_deployment_target() or not _idle_shutdown_enabled():
        return

    revoked_id = getattr(request, "id", None)
    exclude = {revoked_id} if revoked_id else set()
    try:
        logger.info("Worker idle-check after revoke of task %s", revoked_id)
        _warm_shutdown_if_idle(exclude_task_ids=exclude)
    except Exception:
        logger.exception("shutdown_worker_after_task_revoked failed for %s", revoked_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `lex test lex.tests.unit.infra.test_worker_self_termination.TaskRevokedFastPathTests --noinput --keepdb`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/celery.py lex/tests/unit/infra/test_worker_self_termination.py
git commit -m "feat(celery): terminate idle worker on task_revoked (cancel fast-path)"
```

---

## Task 4: Trigger B — idle watchdog thread (fixes #2)

**Files:**
- Modify: `lex/lex_app/celery.py` (add watchdog constant, state, loop, and `worker_ready`/`worker_shutting_down` handlers after Task 3's handler)
- Test: `lex/tests/unit/infra/test_worker_self_termination.py` (add class)

- [ ] **Step 1: Write the failing test**

Append to `lex/tests/unit/infra/test_worker_self_termination.py`:

```python
class IdleWatchdogTests(SimpleTestCase):
    def setUp(self):
        celery_mod._shutdown_scheduled = False
        celery_mod._watchdog_stop.clear()

    def _clock(self, values):
        """A fake monotonic() that yields successive values then holds the last."""
        seq = list(values)

        def _monotonic():
            return seq.pop(0) if len(seq) > 1 else seq[0]

        return _monotonic

    def test_shuts_down_after_idle_timeout(self):
        # Idle the whole time; clock jumps past the 30s timeout on poll 2.
        sleeps = []

        def fake_sleep(interval):
            sleeps.append(interval)
            if len(sleeps) > 5:  # safety valve so a bug can't hang the suite
                celery_mod._watchdog_stop.set()

        with _patch_state(active_ids=(), reserved_ids=()):
            with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                celery_mod._idle_watchdog_loop(
                    timeout_seconds=30,
                    poll_interval=5,
                    monotonic=self._clock([100, 100, 140]),
                    sleep=fake_sleep,
                )
        helper.assert_called_once_with()

    def test_activity_refreshes_last_active(self):
        # Busy on first poll (resets last_active), then idle but not yet past
        # timeout -> no shutdown; loop bounded by the stop event.
        calls = {"n": 0}

        def fake_sleep(interval):
            calls["n"] += 1
            if calls["n"] >= 2:
                celery_mod._watchdog_stop.set()

        busy_then_idle = [
            types.SimpleNamespace(
                active_requests={_fake_request("t1")}, reserved_requests=set()
            ),
            types.SimpleNamespace(active_requests=set(), reserved_requests=set()),
        ]

        def _read(exclude_task_ids=()):
            state = busy_then_idle[min(calls["n"], 1)]
            ids = {getattr(r, "id", None) for r in state.active_requests}
            return ids - {None}

        with mock.patch.object(celery_mod, "_read_pending_task_ids", side_effect=_read):
            with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                celery_mod._idle_watchdog_loop(
                    timeout_seconds=30,
                    poll_interval=5,
                    monotonic=self._clock([0, 1, 2]),
                    sleep=fake_sleep,
                )
        helper.assert_not_called()

    def test_worker_ready_starts_thread_when_non_local(self):
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=True):
                with mock.patch.object(celery_mod.threading, "Thread") as thread_cls:
                    celery_mod.start_idle_watchdog()
        thread_cls.assert_called_once()
        self.assertTrue(thread_cls.call_args.kwargs.get("daemon"))

    def test_worker_ready_noop_when_local(self):
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=False):
            with mock.patch.object(celery_mod.threading, "Thread") as thread_cls:
                celery_mod.start_idle_watchdog()
        thread_cls.assert_not_called()

    def test_worker_shutting_down_sets_stop_event(self):
        celery_mod._watchdog_stop.clear()
        celery_mod.stop_idle_watchdog()
        self.assertTrue(celery_mod._watchdog_stop.is_set())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `lex test lex.tests.unit.infra.test_worker_self_termination.IdleWatchdogTests --noinput --keepdb`
Expected: FAIL — `AttributeError: ... has no attribute '_idle_watchdog_loop'`.

- [ ] **Step 3: Write minimal implementation**

In `lex/lex_app/celery.py`, after Task 3's `shutdown_worker_after_task_revoked`, add:

```python
# Idle watchdog: a single daemon thread that terminates a worker which has been
# idle (no active/reserved task) for >= LEX_WORKER_IDLE_SHUTDOWN_SECONDS. Catches
# KEDA-spawned surplus pods that never receive a task (cluster bug #2).
_WATCHDOG_POLL_SECONDS = 5.0
_watchdog_stop = threading.Event()
_watchdog_thread = None


def _idle_watchdog_loop(
    timeout_seconds,
    poll_interval=_WATCHDOG_POLL_SECONDS,
    monotonic=time.monotonic,
    sleep=None,
):
    """
    MainProcess daemon loop. Seeds ``last_active`` now (so a legitimately
    spawned worker gets the full grace window to receive its task), then polls:
    if any task is active/reserved, refresh ``last_active``; if idle for at
    least ``timeout_seconds``, request a warm shutdown and exit. ``monotonic``
    and ``sleep`` are injectable for testing.
    """
    sleep = sleep if sleep is not None else _watchdog_stop.wait
    last_active = monotonic()
    while not _watchdog_stop.is_set():
        try:
            pending = _read_pending_task_ids()
        except Exception:  # pragma: no cover - defensive
            logger.exception("idle watchdog: failed to read worker state")
            last_active = monotonic()  # conservative: treat unknown as busy
            sleep(poll_interval)
            continue

        if pending:
            last_active = monotonic()
        elif monotonic() - last_active >= timeout_seconds:
            logger.info(
                "idle watchdog: worker idle >= %ss; requesting warm shutdown",
                timeout_seconds,
            )
            _warm_shutdown_if_idle()
            return
        sleep(poll_interval)


@worker_ready.connect
def start_idle_watchdog(sender=None, **extra):
    """Start the single idle-watchdog daemon thread on worker startup."""
    global _watchdog_thread
    if not _is_non_local_deployment_target() or not _idle_shutdown_enabled():
        return
    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(
        target=_idle_watchdog_loop,
        args=(_idle_shutdown_seconds(),),
        name="lex-idle-watchdog",
        daemon=True,
    )
    _watchdog_thread.start()


@worker_shutting_down.connect
def stop_idle_watchdog(sender=None, **extra):
    """Signal the watchdog loop to stop cleanly on worker shutdown."""
    _watchdog_stop.set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `lex test lex.tests.unit.infra.test_worker_self_termination.IdleWatchdogTests --noinput --keepdb`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/celery.py lex/tests/unit/infra/test_worker_self_termination.py
git commit -m "feat(celery): add idle watchdog thread to reap never-busy workers"
```

---

## Task 5: Full-module regression + cluster acceptance handoff

**Files:**
- Test: run the whole new module + the existing shutdown test together
- Docs: append acceptance steps to the design spec's testing section

- [ ] **Step 1: Run the new module in full**

Run: `lex test lex.tests.unit.infra.test_worker_self_termination --noinput --keepdb --verbosity=2`
Expected: PASS (all classes: 4 + 5 + 3 + 5 = 17 tests).

- [ ] **Step 2: Run the surrounding infra suite to confirm no regression**

Run: `lex test lex.tests.unit.infra --noinput --keepdb`
Expected: PASS, or the only failures are pre-existing skips documented in CLAUDE.md §9 (e.g. the stale `test_celery_worker_shutdown.py` expectations). Note any failure that is NOT pre-existing and fix it before continuing — do not modify unrelated tests to make them green.

- [ ] **Step 3: Record the cluster acceptance check**

The real SIGTERM -> process-exit -> pod-termination is integration-level and is validated against the cluster, not in unit tests. Per the design (`docs/superpowers/specs/2026-06-05-worker-self-termination-design.md` §5), this is paired with the `lex-testing` skill: home is **Cluster 8 ("Celery & Async")**, harness `~/LUND_IT/LexStressLab/D_WorkerRecovery`. Before running it, invoke the `lex-testing` skill to allocate the scenario letter. The two acceptance scenarios:

1. **Cancel kills the pod:** start a real calculation, `CalculationModel.cancel()` it, observe the worker pod that held the task terminate within ~1s, and confirm a sibling task on a concurrency>1 worker is unaffected.
2. **Surplus pod self-reaps:** over-provision so KEDA spawns a worker that receives no task; confirm it self-terminates after `LEX_WORKER_IDLE_SHUTDOWN_SECONDS`.

This step is a manual/cluster check; mark it done once both scenarios are observed in the cluster.

- [ ] **Step 4: Commit (if the spec was annotated)**

```bash
git add docs/superpowers/specs/2026-06-05-worker-self-termination-design.md
git commit -m "docs(spec): record cluster acceptance scenarios for worker self-termination"
```

---

## Self-Review

**Spec coverage:**
- Spec §3.1 (refactor `_warm_shutdown_if_idle` + `_shutdown_scheduled` guard) → Task 1. ✓
- Spec §3.2 (Trigger A `task_revoked`, excludes revoked id, respects gates) → Task 3. ✓
- Spec §3.3 (Trigger B watchdog on `worker_ready`, seeds `last_active`, refreshes on activity, stops on `worker_shutting_down`) → Task 4. ✓
- Spec §3.4 (config `LEX_WORKER_IDLE_SHUTDOWN_ENABLED` default true, `LEX_WORKER_IDLE_SHUTDOWN_SECONDS` default 30; both gated by `_is_non_local_deployment_target`) → Task 2 (+ gating exercised in Tasks 3 & 4). ✓
- Spec §4 safety (concurrency-correct `(active|reserved)`; blocked parent protected; idempotent guard; startup-race grace window) → preserved by reusing `_read_pending_task_ids` and seeding `last_active` at thread start (Task 4); guard verified in Task 1. ✓
- Spec §5 testing (unit for helper/watchdog/revoked + gates; cluster acceptance) → Tasks 1-4 unit, Task 5 acceptance handoff. ✓
- Spec §6 acceptance criteria 1-5 → criteria 1/3 via Task 3 + Task 5 scenario 1; criterion 2 via Task 4 + Task 5 scenario 2; criterion 4 via the gate tests in Tasks 3 & 4; criterion 5 via Task 5 Step 2. ✓
- Spec §7 files touched (`celery.py`, `settings.py`, new test module) → exactly the three files in this plan. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; no "add error handling"-style hand-waves. ✓

**Type/name consistency:** `_warm_shutdown_if_idle(exclude_task_ids=...)`, `_read_pending_task_ids(exclude_task_ids=...)`, `_idle_shutdown_enabled()`, `_idle_shutdown_seconds()`, `shutdown_worker_after_task_revoked(request=...)`, `_idle_watchdog_loop(timeout_seconds, poll_interval, monotonic, sleep)`, `start_idle_watchdog`, `stop_idle_watchdog`, `_watchdog_stop`, `_shutdown_scheduled` — names are identical across every task that references them. ✓

**Deviation from spec (flagged):** Spec §3.4/§7 says `settings.py` "reads" the knobs. To keep the unit tests independent of full Django settings, `celery.py` reads the same env vars directly via `_idle_shutdown_enabled`/`_idle_shutdown_seconds`; `settings.py` still exposes the two as documented settings. The env var remains the single source of truth — this is a mechanism detail, not a behavior change.
