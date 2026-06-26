# Celery-beat recovery driver — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dedicated `recovery-supervisor` pod with a singleton embedded-beat self-consuming Celery pod, so the recovery schedule is visible/editable in the Django admin while preserving the non-circular recovery property on a scale-to-0 worker topology.

**Architecture:** Reuse the existing heartbeat + `scan_and_recover` engine unchanged. Beat (embedded in a singleton worker that consumes a dedicated `recovery` queue) fires the existing `sweep_dead_workers` task; the scan runs in-process and `_requeue` routes recovered work to the main queue, which drives KEDA to scale real workers 0→N. A Helm value `workers.recoveryDriver` selects `supervisor` (default) or `beat`.

**Tech Stack:** Python 3.12, Django, Celery, `django_celery_beat` (DatabaseScheduler), Redis broker, KEDA (GKE), Helm.

**Spec:** `docs/superpowers/specs/2026-06-11-celery-beat-recovery-driver-design.md`

**Repos / branches:**
- lex-app (this repo): branch `feat/celery-beat-recovery-driver` (already created off `lex-app-v2`).
- `LEX_TERRAFORM_MODULES` (`~/LUND_IT/LEX_TERRAFORM_MODULES`): create branch `feat/celery-beat-recovery-driver` in Task 5.

**How to run the lex-app test suite** (Django bootstraps Django then hands to pytest; `-p no:django` is already in `pyproject.toml`):

```bash
cd ~/Documents/lex
set -a && source .env 2>/dev/null && set +a
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- <test-path> -q
```

If `.venv-test` is missing, create it: `uv venv --python 3.12 .venv-test && VIRTUAL_ENV=.venv-test uv pip install -e .`

---

## File Structure

**lex-app (modify):**
- `lex/lex_app/settings.py` — fix the dangling beat-schedule task path and route the sweep to the `recovery` queue (Task 1, Task 2).
- `lex/lex_app/celery_recovery/entrypoint.py` — add a second `main`-style entrypoint, `beat_main()`, that boots Django and launches the embedded-beat recovery worker via `app.worker_main(...)` (Task 4).
- `pyproject.toml` — register the `lex-recovery-beat` console script (Task 4).
- `lex/tests/unit/infra/test_celery_recovery.py` — add regression tests for the schedule wiring (Task 3).

**lex-app (do NOT change):**
- `lex/lex_app/celery_recovery/heartbeat.py` — the `sweep_dead_workers` exclusion already exists (`_UNTRACKED_TASK_NAMES`, line 29). Verify only.
- `lex/lex_app/celery_recovery/supervisor.py` — `_requeue` already routes to the calc's main queue (line 85). Verify only.

**LEX_TERRAFORM_MODULES (modify):**
- `modules/lex-instance/chart/templates/recovery_supervisor.yaml` — gate behind `recoveryDriver == "supervisor"` (Task 6).
- `modules/lex-instance/chart/templates/celery_beat_recovery.yaml` — new singleton Deployment (Task 5).
- `modules/lex-instance/chart/values.yaml` — add `workers.recoveryDriver` + beat resources (Task 5).

---

## Task 1: Fix the dangling beat-schedule task path (latent bug)

**Context:** `settings.py` schedules the sweep under the task name
`lex.lex_app.celery_recovery.tasks.sweep_dead_workers`, but that module was
deleted — the real registered task is
`lex.lex_app.celery_recovery.supervisor.sweep_dead_workers`
(`supervisor.py:400`, `@shared_task(name=...)`). Today the dedicated supervisor
runs the scan directly so this dead reference never fires; the beat-driven
design depends on it firing, so it must point at the registered name. The
heartbeat exclusion set (`heartbeat.py:29`) already uses the `.supervisor.`
name, so this fix also aligns the schedule with the exclusion.

**Files:**
- Modify: `lex/lex_app/settings.py` (the `CELERY_BEAT_SCHEDULE` block, ~line 553–560)
- Test: `lex/tests/unit/infra/test_celery_recovery.py`

- [ ] **Step 1: Write the failing test**

Add this class to `lex/tests/unit/infra/test_celery_recovery.py` (it imports the
real registered task and asserts the schedule references the same name):

```python
class BeatScheduleWiringTests(SimpleTestCase):
    """The beat schedule must reference the actually-registered sweep task,
    and that task must be excluded from heartbeat tracking. A drift here means
    beat enqueues a task name no worker has registered -> recovery silently
    never runs under the beat-driven driver."""

    def test_schedule_task_name_matches_registered_sweep(self):
        from django.conf import settings as dj_settings

        # The registered task name (source of truth) lives on the @shared_task.
        registered_name = supervisor.sweep_dead_workers.name
        self.assertEqual(
            registered_name,
            "lex.lex_app.celery_recovery.supervisor.sweep_dead_workers",
        )

        schedule = getattr(dj_settings, "CELERY_BEAT_SCHEDULE", {})
        entry = schedule.get("lex-celery-recovery-sweep")
        self.assertIsNotNone(
            entry, "lex-celery-recovery-sweep entry missing from CELERY_BEAT_SCHEDULE"
        )
        self.assertEqual(entry["task"], registered_name)

    def test_registered_sweep_is_excluded_from_heartbeat_tracking(self):
        self.assertIn(
            supervisor.sweep_dead_workers.name,
            heartbeat._UNTRACKED_TASK_NAMES,
        )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ~/Documents/lex
set -a && source .env 2>/dev/null && set +a
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- \
  lex/tests/unit/infra/test_celery_recovery.py::BeatScheduleWiringTests -q
```

Expected: `test_schedule_task_name_matches_registered_sweep` FAILS — the entry's
`task` is `...celery_recovery.tasks.sweep_dead_workers` (the dangling path), not
`...supervisor.sweep_dead_workers`. (`test_registered_sweep_is_excluded...`
should already PASS.)

- [ ] **Step 3: Fix the schedule task name**

In `lex/lex_app/settings.py`, inside the `CELERY_BEAT_SCHEDULE` block, change the
`"task"` value:

```python
        "lex-celery-recovery-sweep": {
            "task": "lex.lex_app.celery_recovery.supervisor.sweep_dead_workers",
            "schedule": float(LEX_TASK_SUPERVISOR_SCAN_INTERVAL),
            "options": {"expires": float(LEX_TASK_SUPERVISOR_SCAN_INTERVAL)},
        },
```

(Only the `"task"` string changes in this task — `.tasks.` → `.supervisor.`.)

- [ ] **Step 4: Run the test to verify it passes**

```bash
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- \
  lex/tests/unit/infra/test_celery_recovery.py::BeatScheduleWiringTests -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/settings.py lex/tests/unit/infra/test_celery_recovery.py
git commit -m "Fix dangling beat-schedule sweep task path to registered name"
```

---

## Task 2: Route the sweep to a dedicated `recovery` queue

**Context:** With the default schedule the sweep lands on
`CELERY_TASK_DEFAULT_QUEUE` (= `INSTANCE_RESOURCE_IDENTIFIER`, the main queue
KEDA watches). That would (a) inflate KEDA's `listLength` scaling signal with
sweep messages and (b) let a scaled-up real worker consume the sweep instead of
the recovery pod. Routing the sweep to a separate `recovery` queue — which only
the recovery pod consumes (`-Q recovery`, Task 4/5) — keeps the scan off the
autoscaling path. `_requeue` is unaffected: it routes *recovered* tasks to the
calc's own queue (`supervisor.py:85`), i.e. the main queue, which is what should
drive KEDA.

**Files:**
- Modify: `lex/lex_app/settings.py` (the same `CELERY_BEAT_SCHEDULE` entry)
- Test: `lex/tests/unit/infra/test_celery_recovery.py`

- [ ] **Step 1: Write the failing test**

Add to `BeatScheduleWiringTests` in
`lex/tests/unit/infra/test_celery_recovery.py`:

```python
    def test_sweep_is_routed_to_dedicated_recovery_queue(self):
        from django.conf import settings as dj_settings

        entry = dj_settings.CELERY_BEAT_SCHEDULE["lex-celery-recovery-sweep"]
        self.assertEqual(
            entry["options"].get("queue"),
            "recovery",
            "sweep must target the dedicated 'recovery' queue, not the main "
            "KEDA-watched queue",
        )

    def test_sweep_queue_differs_from_main_default_queue(self):
        from django.conf import settings as dj_settings

        entry = dj_settings.CELERY_BEAT_SCHEDULE["lex-celery-recovery-sweep"]
        self.assertNotEqual(
            entry["options"].get("queue"),
            getattr(dj_settings, "CELERY_TASK_DEFAULT_QUEUE", "celery"),
            "the recovery sweep queue must be distinct from the main queue so "
            "it never pollutes the KEDA scaling signal",
        )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- \
  "lex/tests/unit/infra/test_celery_recovery.py::BeatScheduleWiringTests::test_sweep_is_routed_to_dedicated_recovery_queue" -q
```

Expected: FAIL — `entry["options"].get("queue")` is `None` (no `queue` key yet).

- [ ] **Step 3: Add the queue route**

In `lex/lex_app/settings.py`, update the entry's `options` to include the queue:

```python
        "lex-celery-recovery-sweep": {
            "task": "lex.lex_app.celery_recovery.supervisor.sweep_dead_workers",
            "schedule": float(LEX_TASK_SUPERVISOR_SCAN_INTERVAL),
            # Route to a dedicated queue consumed ONLY by the recovery pod
            # (celery worker -B -Q recovery). Keeps the sweep off the main
            # KEDA-watched queue so it never inflates the worker scaling signal
            # nor gets eaten by a scaled-up real worker. expires bounds backlog.
            "options": {
                "queue": "recovery",
                "expires": float(LEX_TASK_SUPERVISOR_SCAN_INTERVAL),
            },
        },
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- \
  lex/tests/unit/infra/test_celery_recovery.py::BeatScheduleWiringTests -q
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/settings.py lex/tests/unit/infra/test_celery_recovery.py
git commit -m "Route recovery sweep to dedicated 'recovery' queue (off KEDA signal)"
```

---

## Task 3: Lock in the non-circular routing invariant with a test

**Context:** The whole design hinges on one invariant: the sweep runs on the
`recovery` queue, but *recovered* tasks are re-dispatched to the **main** queue
(so KEDA scales real workers, and recovered work never loops back to the
recovery pod). `_requeue` already does this (`supervisor.py:85`:
`queue = incremented.get("queue") or _default_queue()`). Add a focused test so a
future refactor can't silently break it.

**Files:**
- Test: `lex/tests/unit/infra/test_celery_recovery.py`

- [ ] **Step 1: Write the test**

Add this class (it follows the existing `_fake_app()` + patch style at the top
of the file):

```python
class RequeueRoutingInvariantTests(SimpleTestCase):
    """Recovered tasks must go to their original (main) queue, never the
    'recovery' queue. This is what lets KEDA scale real workers while the
    recovery pod stays isolated on -Q recovery."""

    def test_requeue_uses_payload_queue_not_recovery_queue(self):
        app = _fake_app()
        with mock.patch.object(supervisor, "_requeue_grace_seconds", return_value=60), \
             mock.patch.object(supervisor, "_default_queue", return_value="main-q"), \
             mock.patch.object(registry, "grant_grace"), \
             mock.patch.object(registry, "persist_payload"):
            supervisor._requeue(
                app,
                "task-1",
                {"name": "calc_and_save", "args": (), "kwargs": {},
                 "queue": "main-q", "retries": 0},
            )
        self.assertEqual(app.send_task.call_count, 1)
        _, called_kwargs = app.send_task.call_args
        self.assertEqual(called_kwargs["queue"], "main-q")
        self.assertNotEqual(called_kwargs["queue"], "recovery")

    def test_requeue_falls_back_to_default_main_queue_when_payload_lacks_queue(self):
        app = _fake_app()
        with mock.patch.object(supervisor, "_requeue_grace_seconds", return_value=60), \
             mock.patch.object(supervisor, "_default_queue", return_value="main-q"), \
             mock.patch.object(registry, "grant_grace"), \
             mock.patch.object(registry, "persist_payload"):
            supervisor._requeue(
                app,
                "task-2",
                {"name": "calc_and_save", "args": (), "kwargs": {}, "retries": 0},
            )
        _, called_kwargs = app.send_task.call_args
        self.assertEqual(called_kwargs["queue"], "main-q")
```

- [ ] **Step 2: Run the tests to verify they pass (no production change needed)**

```bash
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- \
  lex/tests/unit/infra/test_celery_recovery.py::RequeueRoutingInvariantTests -q
```

Expected: 2 passed. (This documents existing behavior; if it fails, STOP — the
invariant the design relies on is already broken and must be investigated before
proceeding.)

- [ ] **Step 3: Commit**

```bash
git add lex/tests/unit/infra/test_celery_recovery.py
git commit -m "Pin requeue-to-main-queue invariant for beat recovery driver"
```

---

## Task 4: Add the `lex-recovery-beat` console-script entrypoint

**Context:** The worker container's `start_worker.sh` (and its `celery -A`
target) is baked into the image and not in this repo, so the chart cannot rely
on celery's `-A` app discovery. The supervisor already side-steps this with a
console script that boots Django and imports the app explicitly
(`entrypoint.py`). Mirror that: a `lex-recovery-beat` script that boots Django,
imports the real Celery `app`, and launches an embedded-beat worker bound to the
`recovery` queue via `app.worker_main(...)` (which reuses the already-imported
app — no `-A` discovery). The chart command then becomes just
`["lex-recovery-beat"]`, identical in shape to `["lex-recovery-supervisor"]`.

**Files:**
- Modify: `lex/lex_app/celery_recovery/entrypoint.py` (add `beat_main`)
- Modify: `pyproject.toml` (register the console script)
- Test: `lex/tests/unit/infra/test_celery_recovery.py`

- [ ] **Step 1: Write the failing test**

Add this class. It patches `app.worker_main` so nothing actually starts, and
asserts the argv we pass embeds beat, binds the `recovery` queue, and selects
the DatabaseScheduler:

```python
class RecoveryBeatEntrypointTests(SimpleTestCase):
    """lex-recovery-beat must launch an embedded-beat worker that consumes ONLY
    the 'recovery' queue with the django_celery_beat DatabaseScheduler."""

    def test_beat_main_invokes_worker_main_with_embedded_beat_on_recovery_queue(self):
        from lex.lex_app.celery_recovery import entrypoint
        from lex.lex_app import celery as celery_module

        with mock.patch.object(celery_module.app, "worker_main") as worker_main:
            entrypoint.beat_main(argv=[])

        self.assertEqual(worker_main.call_count, 1)
        (passed_argv,), _ = worker_main.call_args
        self.assertEqual(passed_argv[0], "worker")
        self.assertIn("-B", passed_argv)                 # embedded beat
        self.assertIn("-Q", passed_argv)
        q_index = passed_argv.index("-Q")
        self.assertEqual(passed_argv[q_index + 1], "recovery")
        self.assertIn("--scheduler", passed_argv)
        s_index = passed_argv.index("--scheduler")
        self.assertEqual(
            passed_argv[s_index + 1],
            "django_celery_beat.schedulers:DatabaseScheduler",
        )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- \
  lex/tests/unit/infra/test_celery_recovery.py::RecoveryBeatEntrypointTests -q
```

Expected: FAIL with `AttributeError: module ... entrypoint has no attribute 'beat_main'`.

- [ ] **Step 3: Implement `beat_main` (and factor the shared Django bootstrap)**

In `lex/lex_app/celery_recovery/entrypoint.py`, refactor so the Django bootstrap
is shared, then add `beat_main`. Replace the body of `main()`'s bootstrap with a
helper and add the new entrypoint. The full updated file:

```python
"""Console-script entrypoints for the worker-recovery drivers.

``lex-recovery-supervisor`` runs the always-on supervisor loop (the legacy /
default cluster driver and the local fallback). ``lex-recovery-beat`` runs the
embedded-beat self-consuming worker that fires the recovery sweep on a schedule
visible in the Django admin (django_celery_beat DatabaseScheduler) while
consuming ONLY the dedicated ``recovery`` queue, so recovered work re-dispatched
by ``_requeue`` flows to the main queue and drives KEDA, never looping back here.

Both bootstrap Django exactly like the lex CLI (see ``lex/bin/lex.py`` and
``lex/lex_app/celery.py``) before importing app code.

Usage:

    lex-recovery-supervisor              # always-on supervisor loop
    lex-recovery-supervisor --once       # single pass and exit
    lex-recovery-beat                    # embedded-beat recovery worker
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional


def _bootstrap_django() -> None:
    # Mirror the lex CLI bootstrap (see lex/bin/lex.py). The framework's modules
    # import each other as *top-level* packages (e.g. ``from core.models...``,
    # ``lex_app.settings``), which only resolves when the inner ``lex/`` package
    # directory is on sys.path. Launching a console script bypasses the CLI, so
    # we replicate it or django.setup() dies with ModuleNotFoundError while
    # populating apps. ``parents[2]`` of this file is that inner ``lex/`` dir.
    package_root = Path(__file__).resolve().parents[2].as_posix()
    if package_root not in sys.path:
        sys.path.append(package_root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
    os.environ.setdefault("LEX_APP_PACKAGE_ROOT", package_root)

    import django

    django.setup()


def main() -> None:
    _bootstrap_django()

    from django.core.management import call_command

    call_command("run_recovery_supervisor", *sys.argv[1:])


def beat_main(argv: Optional[List[str]] = None) -> None:
    """Launch an embedded-beat worker bound to the 'recovery' queue.

    Uses ``app.worker_main`` so the already-imported Celery app is reused (no
    ``-A`` discovery, which is fragile in this image). Concurrency 1: the only
    work on this queue is the lightweight sweep; the heavy recovered tasks run
    on the autoscaled main-queue workers, not here.
    """
    _bootstrap_django()

    from lex.lex_app.celery import app

    worker_argv = [
        "worker",
        "-B",
        "-Q",
        "recovery",
        "--concurrency",
        "1",
        "--scheduler",
        "django_celery_beat.schedulers:DatabaseScheduler",
        "-l",
        "info",
        *(argv if argv is not None else sys.argv[1:]),
    ]
    app.worker_main(worker_argv)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- \
  lex/tests/unit/infra/test_celery_recovery.py::RecoveryBeatEntrypointTests -q
```

Expected: 1 passed.

- [ ] **Step 5: Register the console script**

In `pyproject.toml`, under `[project.scripts]`, add the `lex-recovery-beat`
line below the existing supervisor script:

```toml
[project.scripts]
lex = "lex.__main__:main"
lex-generate-configs = "generate_pycharm_configs:generate_pycharm_configs"
lex-recovery-supervisor = "lex.lex_app.celery_recovery.entrypoint:main"
lex-recovery-beat = "lex.lex_app.celery_recovery.entrypoint:beat_main"
```

- [ ] **Step 6: Verify the console script resolves after reinstall**

```bash
VIRTUAL_ENV=.venv-test uv pip install -e . >/dev/null 2>&1
.venv-test/bin/python -c "from importlib.metadata import entry_points; \
print([e.value for e in entry_points(group='console_scripts') if e.name=='lex-recovery-beat'])"
```

Expected: `['lex.lex_app.celery_recovery.entrypoint:beat_main']`

- [ ] **Step 7: Commit**

```bash
git add lex/lex_app/celery_recovery/entrypoint.py pyproject.toml \
        lex/tests/unit/infra/test_celery_recovery.py
git commit -m "Add lex-recovery-beat embedded-beat recovery entrypoint"
```

---

## Task 5: Add the `celery_beat_recovery.yaml` chart template + values

**Context:** This is in the separate `LEX_TERRAFORM_MODULES` repo. The new
template renders only when `workers.recoveryDriver == "beat"`; it mirrors the
existing `recovery_supervisor.yaml` singleton shape (replicas 1, Recreate, same
image/configmap/secret/volumes) but runs the new `lex-recovery-beat` script.

**Files:**
- Create branch in `~/LUND_IT/LEX_TERRAFORM_MODULES`
- Create: `modules/lex-instance/chart/templates/celery_beat_recovery.yaml`
- Modify: `modules/lex-instance/chart/values.yaml`

- [ ] **Step 1: Create the chart branch**

```bash
cd ~/LUND_IT/LEX_TERRAFORM_MODULES
git checkout -b feat/celery-beat-recovery-driver
```

- [ ] **Step 2: Add the driver selector value**

In `modules/lex-instance/chart/values.yaml`, under the `workers:` block, add:

```yaml
workers:
  # Recovery driver: "supervisor" (default, dedicated lex-recovery-supervisor
  # Deployment) or "beat" (embedded-beat self-consuming celery worker on the
  # dedicated 'recovery' queue, schedule visible in Django admin).
  recoveryDriver: supervisor
  beat:
    resources:
      requests:
        memory: "128Mi"
        cpu: "64m"
      limits:
        memory: "256Mi"
        cpu: "250m"
```

(Place `recoveryDriver` and `beat` alongside the existing `workers.image`,
`workers.configmap`, etc. — do not remove existing keys.)

- [ ] **Step 3: Create the beat-recovery template**

Create `modules/lex-instance/chart/templates/celery_beat_recovery.yaml`:

```yaml
# Embedded-beat recovery driver (alternative to recovery_supervisor.yaml).
#
# A singleton, always-on Celery worker with embedded beat (-B) that consumes
# ONLY the dedicated "recovery" queue. Beat fires sweep_dead_workers on a
# schedule (visible in the Django admin via django_celery_beat); the scan runs
# in-process here and re-dispatches any recovered task to its MAIN queue, which
# raises KEDA's listLength and scales real workers 0->N. Recovered work never
# loops back here because this pod subscribes only to -Q recovery.
#
# Renders only when workers.recoveryDriver == "beat". Same singleton shape and
# identity (image/configmap/secret/volumes) as recovery_supervisor.yaml.
{{ if and .Values.workers.enabled (eq .Values.workers.recoveryDriver "beat") }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: recovery-beat-{{ include "instance.fullname" . }}
  labels:
    {{- toYaml .Values.resourceLabels | nindent 4 }}
    layer: "recovery-beat"
  annotations:
    configmap.reloader.stakater.com/reload: {{ .Values.workers.configmap }}
    secret.reloader.stakater.com/reload: {{ .Values.workers.appEnvSecret }}
    cluster-autoscaler.kubernetes.io/safe-to-evict: "false"
spec:
  # Singleton: embedded beat must never double-fire. Recreate avoids two
  # overlapping schedulers during rollout.
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      {{- toYaml .Values.resourceLabels | nindent 6 }}
      layer: "recovery-beat"
  template:
    metadata:
      labels:
        {{- toYaml .Values.resourceLabels | nindent 8 }}
        layer: "recovery-beat"
    spec:
      serviceAccountName: {{ .Values.serviceAccount.name }}
{{ if .Values.workers.nodeSelector.enabled }}
      nodeSelector:
        cloud.google.com/gke-nodepool: {{ .Values.workers.nodeSelector.nodepool }}
{{ end }}
{{ if .Values.workers.toleration.enabled }}
      tolerations:
        - key: {{ .Values.workers.toleration.key | quote }}
          operator: {{ .Values.workers.toleration.operator | quote }}
          value: {{ .Values.workers.toleration.value | quote }}
          effect: {{ .Values.workers.toleration.effect | quote }}
{{ end }}
      terminationGracePeriodSeconds: 30
      containers:
        - name: recovery-beat
          image: {{ .Values.workers.image }}
          command: ["lex-recovery-beat"]
          resources:
            requests:
              memory: {{ .Values.workers.beat.resources.requests.memory }}
              cpu: {{ .Values.workers.beat.resources.requests.cpu }}
            limits:
              memory: {{ .Values.workers.beat.resources.limits.memory }}
              cpu: {{ .Values.workers.beat.resources.limits.cpu }}
          envFrom:
            - configMapRef:
                name: {{ .Values.workers.configmap }}
            - secretRef:
                name: {{ .Values.workers.appEnvSecret }}
          env:
            # Belt-and-suspenders: gates default true, but be explicit so the
            # pod can never silently no-op if the configmap omits them.
            - name: CELERY_ACTIVE
              value: "TRUE"
            - name: LEX_TASK_RECOVERY_ENABLED
              value: "true"
          imagePullPolicy: IfNotPresent
          # settings.py reads /app/django-storages/gcpCredentials.json at import
          # time when STORAGE_TYPE=GCS (inherited from the worker configmap);
          # without these volumes the pod crash-loops on FileNotFoundError before
          # any recovery code runs. Identical to the supervisor.
          volumeMounts:
            - name: {{ include "instance.fullname" . }}-iam-sa-key
              mountPath: "/app/django-storages"
              readOnly: true
            - name: sp-api-cert-{{ include "instance.fullname" . }}
              mountPath: "/app/sharepoint-cert"
              readOnly: true
      volumes:
        - name: {{ include "instance.fullname" . }}-iam-sa-key
          secret:
            secretName: {{ .Values.djangoProcessAdminGeneric.iamSaKeySecret }}
        - name: sp-api-cert-{{ include "instance.fullname" . }}
          secret:
            secretName: {{ .Values.djangoProcessAdminGeneric.sharepointCertSecret }}
{{ end }}
```

- [ ] **Step 4: Render the chart with recoveryDriver=beat and verify the beat pod appears**

```bash
cd ~/LUND_IT/LEX_TERRAFORM_MODULES
helm template t modules/lex-instance/chart \
  --set workers.enabled=true \
  --set workers.recoveryDriver=beat \
  --set keda.enabled=true 2>/dev/null \
  | grep -E "kind: Deployment|recovery-beat|recovery-supervisor|command:|lex-recovery"
```

Expected: a `recovery-beat-...` Deployment with `command: ["lex-recovery-beat"]`
is present, and NO `recovery-supervisor-...` Deployment (Task 6 gates it).
If Task 6 isn't done yet, the supervisor may still appear — that's fine for this
step; the key assertion is that `recovery-beat` renders.

- [ ] **Step 5: Commit (chart repo)**

```bash
cd ~/LUND_IT/LEX_TERRAFORM_MODULES
git add modules/lex-instance/chart/templates/celery_beat_recovery.yaml \
        modules/lex-instance/chart/values.yaml
git commit -m "Add embedded-beat recovery driver template (recoveryDriver=beat)"
```

---

## Task 6: Gate the supervisor template behind `recoveryDriver == "supervisor"`

**Context:** Only one driver should render per instance. The supervisor template
currently renders whenever `workers.enabled`. Add the driver condition so that
`beat` and `supervisor` are mutually exclusive, defaulting to `supervisor`.

**Files:**
- Modify: `modules/lex-instance/chart/templates/recovery_supervisor.yaml`

- [ ] **Step 1: Add the driver condition to the supervisor guard**

In `modules/lex-instance/chart/templates/recovery_supervisor.yaml`, change the
opening guard from:

```yaml
{{ if .Values.workers.enabled }}
```

to:

```yaml
{{ if and .Values.workers.enabled (eq (.Values.workers.recoveryDriver | default "supervisor") "supervisor") }}
```

(Leave the closing `{{ end }}` as-is. The `| default "supervisor"` keeps
existing instances — whose values predate `recoveryDriver` — rendering the
supervisor unchanged.)

- [ ] **Step 2: Verify default (supervisor) renders supervisor only**

```bash
cd ~/LUND_IT/LEX_TERRAFORM_MODULES
helm template t modules/lex-instance/chart \
  --set workers.enabled=true --set keda.enabled=true 2>/dev/null \
  | grep -E "recovery-supervisor|recovery-beat"
```

Expected: `recovery-supervisor-...` present, `recovery-beat-...` absent.

- [ ] **Step 3: Verify beat renders beat only**

```bash
helm template t modules/lex-instance/chart \
  --set workers.enabled=true --set workers.recoveryDriver=beat \
  --set keda.enabled=true 2>/dev/null \
  | grep -E "recovery-supervisor|recovery-beat"
```

Expected: `recovery-beat-...` present, `recovery-supervisor-...` absent.

- [ ] **Step 4: Commit (chart repo)**

```bash
cd ~/LUND_IT/LEX_TERRAFORM_MODULES
git add modules/lex-instance/chart/templates/recovery_supervisor.yaml
git commit -m "Gate supervisor template behind recoveryDriver=supervisor (default)"
```

---

## Task 7: Full regression + docs note

**Context:** Confirm the app-side change set is green end-to-end and leave a
short operator note pointing at the new driver.

**Files:**
- Modify: `docs/celery-worker-recovery/README.md` (add a short "Driver: beat vs supervisor" note)

- [ ] **Step 1: Run the full celery-recovery unit suite**

```bash
cd ~/Documents/lex
set -a && source .env 2>/dev/null && set +a
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- \
  lex/tests/unit/infra/test_celery_recovery.py -q
```

Expected: all tests pass (the original 11 + the new `BeatScheduleWiringTests`,
`RequeueRoutingInvariantTests`, `RecoveryBeatEntrypointTests`).

- [ ] **Step 2: Run the celery_async cluster (prove the async path is untouched)**

```bash
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" .venv-test/bin/python -m lex pytest -- \
  lex/test_project/tests/celery_async -q
```

Expected: same pass/skip profile as before this branch (no new failures).

- [ ] **Step 3: Add the operator note**

Append to `docs/celery-worker-recovery/README.md` a short subsection:

```markdown
## Recovery driver: supervisor (default) vs beat

Two interchangeable drivers run the identical `scan_and_recover()` engine; pick
one per instance via the Helm value `workers.recoveryDriver`:

- **`supervisor`** (default) — the dedicated `lex-recovery-supervisor` pod runs
  the scan loop in-process. No broker round-trip; nothing to schedule.
- **`beat`** — a singleton `lex-recovery-beat` pod runs `celery worker -B -Q
  recovery`: embedded beat fires `sweep_dead_workers` on the
  `django_celery_beat` DatabaseScheduler (schedule visible/editable in the
  Django admin), and the same pod consumes the dedicated `recovery` queue.
  Recovered tasks are still re-dispatched to their main queue, so KEDA scales
  real workers 0→N exactly as with the supervisor.

Both preserve the non-circular property (the scan runs in an always-on pod, not
in a scale-to-0 worker). Run exactly one; running both is safe (per-task Redis
lock) but wasteful.
```

- [ ] **Step 4: Commit**

```bash
git add docs/celery-worker-recovery/README.md
git commit -m "Document beat vs supervisor recovery driver selection"
```

---

## Self-review notes (for the executor)

- **Spec coverage:** app-change #1 (route sweep to recovery queue) → Task 2;
  app-change #2 (exclude sweep from heartbeat) → already in code, verified in
  Task 1 Step 1's second test; `_requeue` unchanged → Task 3; infra selector +
  beat template + supervisor gating → Tasks 5–6; the dangling-task-path bug
  (discovered during planning, not in the original spec) → Task 1.
- **Cross-repo:** Tasks 1–4 and 7 are in `~/Documents/lex`; Tasks 5–6 are in
  `~/LUND_IT/LEX_TERRAFORM_MODULES`. Commit in the correct repo per task.
- **Staging:** use the exact `git add <paths>` shown — the working tree has many
  unrelated uncommitted changes; never `git add -A` and never stage `.venv-test`.
- **Name consistency:** the registered task name
  `lex.lex_app.celery_recovery.supervisor.sweep_dead_workers`, the queue name
  `recovery`, the console script `lex-recovery-beat`, and the entrypoint function
  `beat_main` are used identically across all tasks.
