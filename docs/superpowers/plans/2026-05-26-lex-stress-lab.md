# LexStressLab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone lex-app stress test project at `~/LUND_IT/LexStressLab/` with 23 models across 5 concern folders, tunable via env vars and a UI config model.

**Architecture:** Standalone Django project using editable `lex-app` install. Models use `app_label = "lex_app"` for migration routing. Layered by concern (Parallelization, DBStress, WorkerRecovery, Orchestration) with a master StressConfig + StressRun orchestrator.

**Tech Stack:** Django/lex-app, Celery 5.x, Redis broker, PostgreSQL, `CalculationModel`, `CalculatedModelMixin`, `WaitForTasks`/`FireAndForget`

**Spec:** `docs/superpowers/specs/2026-05-26-lex-stress-lab-design.md`

---

## File Map

```
~/LUND_IT/LexStressLab/
├── __init__.py                          # empty
├── lex_config.py                        # INITIAL_DATA=None, PROJECT_GROUPS
├── model_structure.yaml                 # model registry (auto-discovery works but explicit is better)
├── _authentication_settings.py          # initial_data_load stub
├── _Django_settings.py                  # Django compatibility stub
├── requirements.txt                     # lex-app, psycopg2-binary, redis
├── .env.example                         # all env vars documented
├── A_Config/
│   ├── __init__.py
│   ├── StressConfig.py
│   └── StressRun.py
├── B_Parallelization/
│   ├── __init__.py
│   ├── DimensionA.py
│   ├── DimensionB.py
│   ├── DimensionC.py
│   ├── Period.py
│   ├── CartesianCalc.py
│   ├── FanOutCalc.py
│   ├── FanOutLeaf.py
│   └── BulkVsNaiveCalc.py
├── C_DBStress/
│   ├── __init__.py
│   ├── OutputRow.py
│   ├── SharedCounter.py
│   ├── HeavyDBCalc.py
│   ├── ContentionCalc.py
│   └── IdempotencyCalc.py
├── D_WorkerRecovery/
│   ├── __init__.py
│   ├── SlowCalc.py
│   ├── FailingCalc.py
│   └── MemoryHogCalc.py
├── E_Orchestration/
│   ├── __init__.py
│   ├── ChainedCalc.py
│   ├── TransferSource.py
│   ├── TransferTarget.py
│   ├── MixedAtomicCalc.py
│   └── MixedNonAtomicCalc.py
└── _Helpers/
    ├── __init__.py
    ├── metrics.py
    └── generators.py
```

---

### Task 1: Scaffold project directory and config files

**Files:**
- Create: `~/LUND_IT/LexStressLab/__init__.py`
- Create: `~/LUND_IT/LexStressLab/lex_config.py`
- Create: `~/LUND_IT/LexStressLab/_Django_settings.py`
- Create: `~/LUND_IT/LexStressLab/_authentication_settings.py`
- Create: `~/LUND_IT/LexStressLab/requirements.txt`
- Create: `~/LUND_IT/LexStressLab/.env.example`
- Create: all `__init__.py` for subfolders

- [ ] **Step 1: Create project directory and all subdirs**

```bash
mkdir -p ~/LUND_IT/LexStressLab/{A_Config,B_Parallelization,C_DBStress,D_WorkerRecovery,E_Orchestration,_Helpers}
```

- [ ] **Step 2: Create `__init__.py` files (all empty)**

Create empty `__init__.py` in: root, A_Config, B_Parallelization, C_DBStress, D_WorkerRecovery, E_Orchestration, _Helpers.

- [ ] **Step 3: Create `lex_config.py`**

```python
INITIAL_DATA = None
PROJECT_GROUPS = ["lex_stress_lab"]
```

- [ ] **Step 4: Create `_Django_settings.py`**

```python
# This file is only here to prevent Django Model related errors.
```

- [ ] **Step 5: Create `_authentication_settings.py`**

```python
initial_data_load = None
```

- [ ] **Step 6: Create `requirements.txt`**

```
lex-app
psycopg2-binary
redis
```

- [ ] **Step 7: Create `.env.example`**

```bash
# --- Django / Lex ---
DJANGO_SETTINGS_MODULE=lex_app.settings
DATABASE_DEPLOYMENT_TARGET=default
SECRET_KEY=stress-lab-dev-key

# --- Celery ---
CELERY_ACTIVE=true
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1

# --- Worker Recovery ---
LEX_TASK_RECOVERY_ENABLED=true
LEX_TASK_HEARTBEAT_INTERVAL=5
LEX_TASK_HB_TTL_MULTIPLIER=3
LEX_TASK_SUPERVISOR_SCAN_INTERVAL=10

# --- Stress Knobs (defaults, override in StressConfig UI) ---
STRESS_DIM_A=10
STRESS_DIM_B=10
STRESS_DIM_C=0
STRESS_PERIODS=4
STRESS_FANOUT=100
STRESS_ROWS_PER_CALC=50
STRESS_CONTENTION_WORKERS=3
STRESS_CONTENTION_INCREMENTS=100
STRESS_SLEEP=30
STRESS_FAILURE_RATE=0
STRESS_MEMORY_MB=0
STRESS_CHAIN_DEPTH=3
STRESS_CHAIN_BREADTH=2
```

- [ ] **Step 8: Commit scaffold**

```bash
cd ~/LUND_IT/LexStressLab && git init
git add -A && git commit -m "scaffold: project directory, config files, .env.example"
```

---

### Task 2: _Helpers — metrics.py and generators.py

**Files:**
- Create: `~/LUND_IT/LexStressLab/_Helpers/metrics.py`
- Create: `~/LUND_IT/LexStressLab/_Helpers/generators.py`

- [ ] **Step 1: Create `_Helpers/metrics.py`**

```python
"""
Instrumentation helpers for stress test scenarios.

Provides:
- query_counter: context manager that counts Django DB queries
- timed: decorator that logs elapsed time
"""
import logging
import time
from contextlib import contextmanager
from functools import wraps

from django.db import connection, reset_queries
from django.conf import settings

logger = logging.getLogger("stress_lab.metrics")


class query_counter:
    """Context manager that counts Django DB queries executed within its scope.

    Usage:
        with query_counter() as qc:
            MyModel.objects.all()
        print(qc.count)

    Requires DEBUG=True in Django settings for connection.queries to populate.
    Falls back to connection-level tracking if DEBUG is False.
    """

    def __enter__(self):
        self._force_debug = not settings.DEBUG
        if self._force_debug:
            settings.DEBUG = True
        reset_queries()
        self._start_count = len(connection.queries)
        self.count = 0
        return self

    def __exit__(self, *exc):
        self.count = len(connection.queries) - self._start_count
        if self._force_debug:
            settings.DEBUG = False
        return False


def timed(label=None):
    """Decorator that logs elapsed time for a function call.

    Usage:
        @timed("my_operation")
        def do_work():
            ...
    """
    def decorator(func):
        tag = label or func.__qualname__

        @wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.info("stress_lab timing %s elapsed_ms=%.1f", tag, elapsed_ms)

        return wrapper
    return decorator
```

- [ ] **Step 2: Create `_Helpers/generators.py`**

```python
"""
Seed data factories for stress test scenarios.

Creates DimensionA/B/C and Period rows based on a StressConfig instance.
Uses bulk_create for efficiency.
"""
import logging

logger = logging.getLogger("stress_lab.generators")


def ensure_seed_data(config):
    """Create dimension and period rows for a config if they don't already exist.

    Idempotent: skips creation if the correct number of rows already exist.
    Cleans and recreates if counts don't match (config was changed).
    """
    from B_Parallelization.DimensionA import DimensionA
    from B_Parallelization.DimensionB import DimensionB
    from B_Parallelization.DimensionC import DimensionC
    from B_Parallelization.Period import Period

    _ensure_axis(DimensionA, config, config.dim_a_size, "A")
    _ensure_axis(DimensionB, config, config.dim_b_size, "B")
    if config.dim_c_size > 0:
        _ensure_axis(DimensionC, config, config.dim_c_size, "C")
    else:
        DimensionC.objects.filter(config=config).delete()

    _ensure_periods(Period, config)


def _ensure_axis(model_cls, config, target_size, prefix):
    """Create or recreate axis rows for a given dimension model."""
    current = model_cls.objects.filter(config=config).count()
    if current == target_size:
        return

    logger.info(
        "stress_lab seeding %s: %d -> %d rows",
        model_cls.__name__, current, target_size,
    )
    model_cls.objects.filter(config=config).delete()
    model_cls.objects.bulk_create([
        model_cls(config=config, code=f"{prefix}-{i:04d}")
        for i in range(target_size)
    ])


def _ensure_periods(model_cls, config):
    """Create period rows (0..period_count-1) for a config."""
    current = model_cls.objects.filter(config=config).count()
    if current == config.period_count:
        return

    logger.info(
        "stress_lab seeding periods: %d -> %d rows",
        current, config.period_count,
    )
    model_cls.objects.filter(config=config).delete()
    model_cls.objects.bulk_create([
        model_cls(config=config, offset=i)
        for i in range(config.period_count)
    ])
```

- [ ] **Step 3: Commit helpers**

```bash
git add _Helpers/ && git commit -m "feat: add metrics and generators helpers"
```

---

### Task 3: A_Config — StressConfig and StressRun

**Files:**
- Create: `~/LUND_IT/LexStressLab/A_Config/StressConfig.py`
- Create: `~/LUND_IT/LexStressLab/A_Config/StressRun.py`

- [ ] **Step 1: Create `A_Config/StressConfig.py`**

```python
"""
Master knobs model for all stress test scenarios.

Environment variables provide defaults; the UI lets you override per-run.
"""
import os

from django.db import models
from lex.core.models.LexModel import LexModel
from lex.core.models.PermissionResult import PermissionResult


def _env_int(key, default):
    return int(os.getenv(key, default))


class StressConfig(LexModel):
    name = models.CharField(max_length=100, default="default")

    # -- Parallelization --
    dim_a_size = models.IntegerField(default=_env_int("STRESS_DIM_A", 10))
    dim_b_size = models.IntegerField(default=_env_int("STRESS_DIM_B", 10))
    dim_c_size = models.IntegerField(default=_env_int("STRESS_DIM_C", 0))
    period_count = models.IntegerField(default=_env_int("STRESS_PERIODS", 4))
    fanout_count = models.IntegerField(default=_env_int("STRESS_FANOUT", 100))

    # -- DB Stress --
    rows_per_calc = models.IntegerField(default=_env_int("STRESS_ROWS_PER_CALC", 50))
    use_bulk_create = models.BooleanField(default=True)
    contention_workers = models.IntegerField(default=_env_int("STRESS_CONTENTION_WORKERS", 3))
    contention_increments = models.IntegerField(default=_env_int("STRESS_CONTENTION_INCREMENTS", 100))

    # -- Worker Recovery --
    sleep_seconds = models.IntegerField(default=_env_int("STRESS_SLEEP", 30))
    failure_rate_pct = models.IntegerField(default=_env_int("STRESS_FAILURE_RATE", 0))
    memory_mb = models.IntegerField(default=_env_int("STRESS_MEMORY_MB", 0))

    # -- Orchestration --
    chain_depth = models.IntegerField(default=_env_int("STRESS_CHAIN_DEPTH", 3))
    chain_breadth = models.IntegerField(default=_env_int("STRESS_CHAIN_BREADTH", 2))

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"StressConfig({self.name})"

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 2: Create `A_Config/StressRun.py`**

```python
"""
Top-level orchestrator. FK to StressConfig, boolean flags per concern folder.

Create a StressRun, set which scenario groups to run, and save.
The calculate() method dispatches selected scenarios via WaitForTasks.
"""
import logging

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult

logger = logging.getLogger("stress_lab.run")


class StressRun(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="runs",
    )
    run_parallelization = models.BooleanField(default=True)
    run_db_stress = models.BooleanField(default=True)
    run_worker_recovery = models.BooleanField(default=True)
    run_orchestration = models.BooleanField(default=True)

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"StressRun(config={self.config.name})"

    def calculate(self):
        from lex.lex_app.celery_tasks import WaitForTasks
        from _Helpers.generators import ensure_seed_data

        cfg = self.config
        ensure_seed_data(cfg)

        with WaitForTasks():
            if self.run_parallelization:
                self._run_parallelization(cfg)
            if self.run_db_stress:
                self._run_db_stress(cfg)
            if self.run_worker_recovery:
                self._run_worker_recovery(cfg)
            if self.run_orchestration:
                self._run_orchestration(cfg)

    def _run_parallelization(self, cfg):
        from B_Parallelization.CartesianCalc import CartesianCalc
        from B_Parallelization.FanOutCalc import FanOutCalc
        from B_Parallelization.BulkVsNaiveCalc import BulkVsNaiveCalc

        logger.info("stress_lab starting parallelization scenarios")
        CartesianCalc.objects.create(config=cfg)
        FanOutCalc.objects.create(config=cfg)
        BulkVsNaiveCalc.objects.create(config=cfg, method="bulk")
        BulkVsNaiveCalc.objects.create(config=cfg, method="naive")

    def _run_db_stress(self, cfg):
        import uuid
        from C_DBStress.HeavyDBCalc import HeavyDBCalc
        from C_DBStress.ContentionCalc import ContentionCalc
        from C_DBStress.SharedCounter import SharedCounter
        from C_DBStress.IdempotencyCalc import IdempotencyCalc

        logger.info("stress_lab starting DB stress scenarios")
        HeavyDBCalc.objects.create(config=cfg, batch_id=str(uuid.uuid4())[:8])

        SharedCounter.objects.get_or_create(name="stress", defaults={"value": 0, "version": 0})
        for i in range(cfg.contention_workers):
            ContentionCalc.objects.create(config=cfg, worker_index=i)

        IdempotencyCalc.objects.create(config=cfg, run_number=1)

    def _run_worker_recovery(self, cfg):
        from D_WorkerRecovery.SlowCalc import SlowCalc
        from D_WorkerRecovery.FailingCalc import FailingCalc
        from D_WorkerRecovery.MemoryHogCalc import MemoryHogCalc

        logger.info("stress_lab starting worker recovery scenarios")
        SlowCalc.objects.create(config=cfg, task_label="slow-1")

        for i in range(10):
            FailingCalc.objects.create(config=cfg, index=i)

        if cfg.memory_mb > 0:
            MemoryHogCalc.objects.create(config=cfg)

    def _run_orchestration(self, cfg):
        from lex.lex_app.celery_tasks import WaitForTasks
        from E_Orchestration.ChainedCalc import ChainedCalc
        from E_Orchestration.TransferSource import TransferSource
        from E_Orchestration.TransferTarget import TransferTarget
        from E_Orchestration.MixedAtomicCalc import MixedAtomicCalc
        from E_Orchestration.MixedNonAtomicCalc import MixedNonAtomicCalc

        logger.info("stress_lab starting orchestration scenarios")
        ChainedCalc.objects.create(config=cfg, depth=0, label="root")

        sources = []
        with WaitForTasks():
            for i in range(3):
                sources.append(TransferSource.objects.create(config=cfg))

        with WaitForTasks():
            for src in sources:
                TransferTarget.objects.create(config=cfg, source=src)

        MixedAtomicCalc.objects.create(config=cfg, should_fail=True)
        MixedNonAtomicCalc.objects.create(config=cfg, should_fail=True)

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 3: Commit A_Config**

```bash
git add A_Config/ && git commit -m "feat: add StressConfig and StressRun orchestrator"
```

---

### Task 4: B_Parallelization — Dimension models and CartesianCalc

**Files:**
- Create: `B_Parallelization/DimensionA.py`, `DimensionB.py`, `DimensionC.py`, `Period.py`
- Create: `B_Parallelization/CartesianCalc.py`

- [ ] **Step 1: Create `B_Parallelization/DimensionA.py`**

```python
"""Seed data axis A. Rows created by generators.ensure_seed_data()."""
from django.db import models
from lex.core.models.LexModel import LexModel
from lex.core.models.PermissionResult import PermissionResult


class DimensionA(LexModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="dim_a_rows",
    )
    code = models.CharField(max_length=50)

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return self.code

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 2: Create `B_Parallelization/DimensionB.py`**

Same pattern as DimensionA — change class name to `DimensionB`, related_name to `"dim_b_rows"`.

- [ ] **Step 3: Create `B_Parallelization/DimensionC.py`**

Same pattern — class name `DimensionC`, related_name `"dim_c_rows"`.

- [ ] **Step 4: Create `B_Parallelization/Period.py`**

```python
"""Time axis. Rows created by generators.ensure_seed_data()."""
from django.db import models
from lex.core.models.LexModel import LexModel
from lex.core.models.PermissionResult import PermissionResult


class Period(LexModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="periods",
    )
    offset = models.IntegerField()

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"P{self.offset}"

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 5: Create `B_Parallelization/CartesianCalc.py`**

```python
"""
Combination explosion test using CalculatedModelMixin.

Tests: parallelizable_fields grouping, Celery task fan-out, cartesian product generation.

Scale examples:
  dim_a=10, dim_b=10, periods=4  -> 400 combinations, 10 Celery tasks
  dim_a=50, dim_b=50, periods=4  -> 10,000 combinations, 50 tasks
  +dim_c=10                      -> 100,000 combinations
"""
from django.db import models
from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task


class CartesianCalc(CalculatedModelMixin):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="cartesian_calcs",
    )
    dim_a = models.ForeignKey(
        "B_Parallelization.DimensionA",
        on_delete=models.CASCADE,
        null=True, blank=True,
    )
    dim_b = models.ForeignKey(
        "B_Parallelization.DimensionB",
        on_delete=models.CASCADE,
        null=True, blank=True,
    )
    dim_c = models.ForeignKey(
        "B_Parallelization.DimensionC",
        on_delete=models.CASCADE,
        null=True, blank=True,
    )
    period = models.ForeignKey(
        "B_Parallelization.Period",
        on_delete=models.CASCADE,
        null=True, blank=True,
    )
    result_value = models.FloatField(default=0)

    defining_fields = ["dim_a", "dim_b", "period"]
    parallelizable_fields = ["dim_a"]
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        parts = [str(self.dim_a), str(self.dim_b)]
        if self.dim_c:
            parts.append(str(self.dim_c))
        parts.append(str(self.period))
        return "-".join(parts)

    def get_selected_key_list(self, key):
        from B_Parallelization.DimensionA import DimensionA
        from B_Parallelization.DimensionB import DimensionB
        from B_Parallelization.DimensionC import DimensionC
        from B_Parallelization.Period import Period

        if key == "dim_a":
            return list(DimensionA.objects.filter(config=self.config))
        if key == "dim_b":
            return list(DimensionB.objects.filter(config=self.config))
        if key == "dim_c":
            if self.config.dim_c_size > 0:
                return list(DimensionC.objects.filter(config=self.config))
            return []
        if key == "period":
            return list(Period.objects.filter(config=self.config))
        return []

    @lex_shared_task
    def calculate(self):
        a_code = self.dim_a.code if self.dim_a else "?"
        b_code = self.dim_b.code if self.dim_b else "?"
        p_off = self.period.offset if self.period else 0
        self.result_value = hash(f"{a_code}-{b_code}-{p_off}") % 10000

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 6: Commit dimension models + CartesianCalc**

```bash
git add B_Parallelization/DimensionA.py B_Parallelization/DimensionB.py \
        B_Parallelization/DimensionC.py B_Parallelization/Period.py \
        B_Parallelization/CartesianCalc.py
git commit -m "feat: add dimension models and CartesianCalc (CalculatedModelMixin)"
```

---

### Task 5: B_Parallelization — FanOutCalc, FanOutLeaf, BulkVsNaiveCalc

**Files:**
- Create: `B_Parallelization/FanOutCalc.py`, `FanOutLeaf.py`, `BulkVsNaiveCalc.py`

- [ ] **Step 1: Create `B_Parallelization/FanOutLeaf.py`**

```python
"""Leaf task for FanOutCalc. Trivial work — the stress is in task count."""
from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task


class FanOutLeaf(CalculationModel):
    parent = models.ForeignKey(
        "B_Parallelization.FanOutCalc",
        on_delete=models.CASCADE,
        related_name="leaves",
    )
    index = models.IntegerField()
    result = models.CharField(max_length=50, blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"FanOutLeaf({self.index})"

    @lex_shared_task
    def calculate(self):
        self.result = f"leaf-{self.index}"

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 2: Create `B_Parallelization/FanOutCalc.py`**

```python
"""
Queue saturation test. Single parent dispatches N leaf tasks.

Tests: broker pressure, worker prefetch behavior, queue drain rate.
"""
import logging
import time

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult

logger = logging.getLogger("stress_lab.fanout")


class FanOutCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="fanout_calcs",
    )
    elapsed_ms = models.FloatField(default=0)
    leaf_count = models.IntegerField(default=0)

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"FanOutCalc(n={self.config.fanout_count})"

    def calculate(self):
        from lex.lex_app.celery_tasks import WaitForTasks
        from B_Parallelization.FanOutLeaf import FanOutLeaf

        n = self.config.fanout_count
        t0 = time.monotonic()

        with WaitForTasks():
            for i in range(n):
                FanOutLeaf.objects.create(parent=self, index=i)

        self.leaf_count = n
        self.elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "stress_lab fanout completed leaves=%d elapsed_ms=%.1f",
            n, self.elapsed_ms,
        )

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 3: Create `B_Parallelization/BulkVsNaiveCalc.py`**

```python
"""
A/B benchmark: bulk_create vs individual .save() loop.

The orchestrator creates TWO instances per run: method="bulk" and method="naive".
Both write rows_per_calc OutputRow records. Results (elapsed_ms, query_count)
are stored on the model for side-by-side comparison in the UI.
"""
import logging
import time

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task

logger = logging.getLogger("stress_lab.bulk_vs_naive")


class BulkVsNaiveCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="bulk_vs_naive_calcs",
    )
    method = models.CharField(max_length=10)  # "bulk" or "naive"
    row_count = models.IntegerField(default=0)
    elapsed_ms = models.FloatField(default=0)
    query_count = models.IntegerField(default=0)

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"BulkVsNaiveCalc({self.method})"

    @lex_shared_task
    def calculate(self):
        from _Helpers.metrics import query_counter
        from C_DBStress.OutputRow import OutputRow

        n = self.config.rows_per_calc
        batch_id = f"bvn-{self.pk}"
        t0 = time.monotonic()

        with query_counter() as qc:
            if self.method == "bulk":
                OutputRow.objects.bulk_create([
                    OutputRow(source="bulk", batch_id=batch_id, index=i, value=float(i))
                    for i in range(n)
                ])
            else:
                for i in range(n):
                    OutputRow(source="naive", batch_id=batch_id, index=i, value=float(i)).save()

        self.row_count = n
        self.elapsed_ms = (time.monotonic() - t0) * 1000
        self.query_count = qc.count
        logger.info(
            "stress_lab bulk_vs_naive method=%s rows=%d queries=%d elapsed_ms=%.1f",
            self.method, n, qc.count, self.elapsed_ms,
        )

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 4: Commit FanOut + BulkVsNaive**

```bash
git add B_Parallelization/FanOutLeaf.py B_Parallelization/FanOutCalc.py \
        B_Parallelization/BulkVsNaiveCalc.py
git commit -m "feat: add FanOutCalc (queue saturation) and BulkVsNaiveCalc (A/B benchmark)"
```

---

### Task 6: C_DBStress — All models

**Files:**
- Create: `C_DBStress/OutputRow.py`, `SharedCounter.py`, `HeavyDBCalc.py`, `ContentionCalc.py`, `IdempotencyCalc.py`

- [ ] **Step 1: Create `C_DBStress/OutputRow.py`**

```python
"""Shared write target for DB stress scenarios."""
from django.db import models
from lex.core.models.LexModel import LexModel
from lex.core.models.PermissionResult import PermissionResult


class OutputRow(LexModel):
    source = models.CharField(max_length=50)
    batch_id = models.CharField(max_length=50, db_index=True)
    index = models.IntegerField()
    value = models.FloatField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"OutputRow({self.source}/{self.batch_id}/{self.index})"

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 2: Create `C_DBStress/SharedCounter.py`**

```python
"""Single-row contention target for ContentionCalc."""
from django.db import models
from lex.core.models.LexModel import LexModel
from lex.core.models.PermissionResult import PermissionResult


class SharedCounter(LexModel):
    name = models.CharField(max_length=50, unique=True)
    value = models.IntegerField(default=0)
    version = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"SharedCounter({self.name}={self.value})"

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 3: Create `C_DBStress/HeavyDBCalc.py`**

```python
"""
N+1 query pattern reproduction (mimics ACP VehiclePosting).

Phase 1: Write N rows individually (.save() loop)
Phase 2: For each row, aggregate query + update (N+1 pattern)
Total queries: ~3N
"""
import logging
import time

from django.db import models
from django.db.models import Sum
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task

logger = logging.getLogger("stress_lab.heavy_db")


class HeavyDBCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="heavy_db_calcs",
    )
    batch_id = models.CharField(max_length=50)
    total_queries = models.IntegerField(default=0)
    elapsed_ms = models.FloatField(default=0)

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"HeavyDBCalc({self.batch_id})"

    @lex_shared_task
    def calculate(self):
        from _Helpers.metrics import query_counter
        from C_DBStress.OutputRow import OutputRow

        n = self.config.rows_per_calc
        t0 = time.monotonic()

        with query_counter() as qc:
            # Phase 1: N individual inserts (the naive pattern)
            for i in range(n):
                OutputRow(
                    source="heavy", batch_id=self.batch_id, index=i, value=float(i)
                ).save()

            # Phase 2: N+1 aggregate reads + updates
            for i in range(n):
                agg = OutputRow.objects.filter(
                    batch_id=self.batch_id, index__lte=i
                ).aggregate(total=Sum("value"))
                OutputRow.objects.filter(
                    batch_id=self.batch_id, index=i
                ).update(value=agg["total"] or 0)

        self.total_queries = qc.count
        self.elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "stress_lab heavy_db batch=%s rows=%d queries=%d elapsed_ms=%.1f",
            self.batch_id, n, qc.count, self.elapsed_ms,
        )

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 4: Create `C_DBStress/ContentionCalc.py`**

```python
"""
Row-level lock contention test.

Multiple workers all target the same SharedCounter row with select_for_update.
Records succeeded/deadlock counts for analysis.
"""
import logging

from django.db import models, transaction, OperationalError
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task

logger = logging.getLogger("stress_lab.contention")


class ContentionCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="contention_calcs",
    )
    worker_index = models.IntegerField()
    succeeded = models.IntegerField(default=0)
    deadlocks = models.IntegerField(default=0)

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"ContentionCalc(worker={self.worker_index})"

    @lex_shared_task
    def calculate(self):
        from C_DBStress.SharedCounter import SharedCounter

        increments = self.config.contention_increments
        for _ in range(increments):
            try:
                with transaction.atomic():
                    counter = SharedCounter.objects.select_for_update().get(name="stress")
                    counter.value += 1
                    counter.version += 1
                    counter.save()
                self.succeeded += 1
            except OperationalError:
                self.deadlocks += 1

        logger.info(
            "stress_lab contention worker=%d succeeded=%d deadlocks=%d",
            self.worker_index, self.succeeded, self.deadlocks,
        )

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 5: Create `C_DBStress/IdempotencyCalc.py`**

```python
"""
Duplicate handling test using CalculatedModelMixin.

Tests: framework behavior when create() is called twice on the same defining fields.
After second run, row count should stay the same and run_number should update.
"""
from django.db import models
from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task


class IdempotencyCalc(CalculatedModelMixin):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="idempotency_calcs",
    )
    dim_a = models.ForeignKey(
        "B_Parallelization.DimensionA",
        on_delete=models.CASCADE,
        null=True, blank=True,
    )
    period = models.ForeignKey(
        "B_Parallelization.Period",
        on_delete=models.CASCADE,
        null=True, blank=True,
    )
    run_number = models.IntegerField(default=1)
    result_value = models.FloatField(default=0)

    defining_fields = ["dim_a", "period"]
    parallelizable_fields = ["dim_a"]
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"IdempotencyCalc(run={self.run_number})"

    def get_selected_key_list(self, key):
        from B_Parallelization.DimensionA import DimensionA
        from B_Parallelization.Period import Period

        if key == "dim_a":
            return list(DimensionA.objects.filter(config=self.config))
        if key == "period":
            return list(Period.objects.filter(config=self.config))
        return []

    @lex_shared_task
    def calculate(self):
        self.result_value = self.run_number * 100 + (self.dim_a.pk if self.dim_a else 0)

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 6: Commit C_DBStress**

```bash
git add C_DBStress/ && git commit -m "feat: add DB stress scenarios (HeavyDB, Contention, Idempotency)"
```

---

### Task 7: D_WorkerRecovery — All models

**Files:**
- Create: `D_WorkerRecovery/SlowCalc.py`, `FailingCalc.py`, `MemoryHogCalc.py`

- [ ] **Step 1: Create `D_WorkerRecovery/SlowCalc.py`**

```python
"""
Long-running task for heartbeat/recovery testing.

Start this, wait for heartbeat keys in Redis, then SIGKILL a worker.
Observe: requeue by supervisor, max retries, MaxRequeueExceeded on cap.
"""
import logging
import time

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task

logger = logging.getLogger("stress_lab.slow")


class SlowCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="slow_calcs",
    )
    task_label = models.CharField(max_length=50)

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"SlowCalc({self.task_label})"

    @lex_shared_task(lex_max_retries=2)
    def calculate(self):
        duration = self.config.sleep_seconds
        logger.info("SlowCalc %s sleeping %ds", self.task_label, duration)
        time.sleep(duration)
        logger.info("SlowCalc %s completed", self.task_label)

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 2: Create `D_WorkerRecovery/FailingCalc.py`**

```python
"""
Configurable failure rate for error handling tests.

With failure_rate_pct=30, roughly 30% of instances will raise RuntimeError.
Tests: error propagation, CallbackTask.on_failure, partial success reporting.
"""
import logging
import random

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task

logger = logging.getLogger("stress_lab.failing")


class FailingCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="failing_calcs",
    )
    index = models.IntegerField()

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"FailingCalc({self.index})"

    @lex_shared_task
    def calculate(self):
        rate = self.config.failure_rate_pct
        if random.randint(1, 100) <= rate:
            msg = f"FailingCalc[{self.index}] deliberate failure (rate={rate}%)"
            logger.warning("stress_lab %s", msg)
            raise RuntimeError(msg)
        logger.info("stress_lab FailingCalc[%d] succeeded", self.index)

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 3: Create `D_WorkerRecovery/MemoryHogCalc.py`**

```python
"""
OOM simulation. Allocates memory_mb of touched memory, holds it, then releases.

Set memory_mb near the worker's cgroup limit to trigger OOM-kill.
Recovery system detects dead heartbeat and requeues (lex_max_retries=1).
"""
import logging
import time

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task

logger = logging.getLogger("stress_lab.memory_hog")


class MemoryHogCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="memory_hog_calcs",
    )

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"MemoryHogCalc({self.config.memory_mb}MB)"

    @lex_shared_task(lex_max_retries=1)
    def calculate(self):
        mb = self.config.memory_mb
        if mb <= 0:
            logger.info("stress_lab MemoryHogCalc skipped (memory_mb=0)")
            return

        logger.info("stress_lab MemoryHogCalc allocating %d MB", mb)
        data = bytearray(mb * 1024 * 1024)
        # Touch every page so the OS actually commits the memory
        for i in range(0, len(data), 4096):
            data[i] = 0xFF
        logger.info("stress_lab MemoryHogCalc holding %d MB for 5s", mb)
        time.sleep(5)
        del data
        logger.info("stress_lab MemoryHogCalc released %d MB", mb)

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 4: Commit D_WorkerRecovery**

```bash
git add D_WorkerRecovery/ && git commit -m "feat: add worker recovery scenarios (Slow, Failing, MemoryHog)"
```

---

### Task 8: E_Orchestration — All models

**Files:**
- Create: `E_Orchestration/ChainedCalc.py`, `TransferSource.py`, `TransferTarget.py`, `MixedAtomicCalc.py`, `MixedNonAtomicCalc.py`

- [ ] **Step 1: Create `E_Orchestration/ChainedCalc.py`**

```python
"""
Self-referential calculation tree for nested WaitForTasks testing.

chain_depth=3, chain_breadth=2 -> 1 root -> 2 children -> 4 grandchildren = 7 tasks.
Tests: nested scope isolation, failure propagation up the hierarchy.
"""
import logging

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task

logger = logging.getLogger("stress_lab.chained")


class ChainedCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="chained_calcs",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    depth = models.IntegerField(default=0)
    label = models.CharField(max_length=200)

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"ChainedCalc({self.label} d={self.depth})"

    @lex_shared_task
    def calculate(self):
        if self.depth >= self.config.chain_depth:
            logger.info("stress_lab chained leaf %s", self.label)
            return

        from lex.lex_app.celery_tasks import WaitForTasks

        logger.info(
            "stress_lab chained %s spawning %d children at depth %d",
            self.label, self.config.chain_breadth, self.depth + 1,
        )
        with WaitForTasks():
            for i in range(self.config.chain_breadth):
                ChainedCalc.objects.create(
                    config=self.config,
                    parent=self,
                    depth=self.depth + 1,
                    label=f"{self.label}.{i}",
                )

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 2: Create `E_Orchestration/TransferSource.py`**

```python
"""
Source calculation that must complete before TransferTarget reads its output.
Tests: cross-calculation data dependency, stale-read hazards.
"""
import logging

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task

logger = logging.getLogger("stress_lab.transfer")


class TransferSource(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="transfer_sources",
    )
    output_value = models.FloatField(default=0)

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"TransferSource(out={self.output_value})"

    @lex_shared_task
    def calculate(self):
        self.output_value = float(sum(range(1000)))
        logger.info("stress_lab TransferSource computed %f", self.output_value)

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 3: Create `E_Orchestration/TransferTarget.py`**

```python
"""
Target calculation that depends on TransferSource's output.
Reads source.output_value via refresh_from_db().
Tests: ordering correctness, stale-read detection.
"""
import logging

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task

logger = logging.getLogger("stress_lab.transfer")


class TransferTarget(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="transfer_targets",
    )
    source = models.ForeignKey(
        "E_Orchestration.TransferSource",
        on_delete=models.CASCADE,
        related_name="targets",
    )
    received_value = models.FloatField(default=0)

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"TransferTarget(received={self.received_value})"

    @lex_shared_task
    def calculate(self):
        self.source.refresh_from_db()
        self.received_value = self.source.output_value
        logger.info("stress_lab TransferTarget received %f", self.received_value)

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 4: Create `E_Orchestration/MixedAtomicCalc.py`**

```python
"""
Atomic calculation that writes a side effect then fails.
The side effect (OutputRow) should be rolled back by the transaction.
"""
from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task


class MixedAtomicCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="mixed_atomic_calcs",
    )
    should_fail = models.BooleanField(default=False)

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"MixedAtomicCalc(fail={self.should_fail})"

    @lex_shared_task
    def calculate(self):
        from C_DBStress.OutputRow import OutputRow

        OutputRow(source="mixed-atomic", batch_id=str(self.pk), index=0, value=1).save()
        if self.should_fail:
            raise RuntimeError("MixedAtomicCalc deliberate failure")

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 5: Create `E_Orchestration/MixedNonAtomicCalc.py`**

```python
"""
Non-atomic calculation that writes a side effect then fails.
The side effect (OutputRow) should persist despite the failure.
"""
from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.PermissionResult import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task


class MixedNonAtomicCalc(CalculationModel):
    config = models.ForeignKey(
        "A_Config.StressConfig",
        on_delete=models.CASCADE,
        related_name="mixed_nonatomic_calcs",
    )
    should_fail = models.BooleanField(default=False)

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"MixedNonAtomicCalc(fail={self.should_fail})"

    @lex_shared_task
    def calculate(self):
        from C_DBStress.OutputRow import OutputRow

        OutputRow(source="mixed-nonatomic", batch_id=str(self.pk), index=0, value=1).save()
        if self.should_fail:
            raise RuntimeError("MixedNonAtomicCalc deliberate failure")

    def permission_read(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("stress_lab")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True
```

- [ ] **Step 6: Commit E_Orchestration**

```bash
git add E_Orchestration/ && git commit -m "feat: add orchestration scenarios (Chained, Transfer, MixedAtomicity)"
```

---

### Task 9: model_structure.yaml and final integration

**Files:**
- Create: `~/LUND_IT/LexStressLab/model_structure.yaml`

- [ ] **Step 1: Create `model_structure.yaml`**

```yaml
model_structure:
  Configuration:
    stressconfig: null
    stressrun: null
  Parallelization:
    Seed Data:
      dimensiona: null
      dimensionb: null
      dimensionc: null
      period: null
    Scenarios:
      cartesiancalc: null
      fanoutcalc: null
      fanoutleaf: null
      bulkvsnaivecalc: null
  DB Stress:
    Data Tables:
      outputrow: null
      sharedcounter: null
    Scenarios:
      heavydbcalc: null
      contentioncalc: null
      idempotencycalc: null
  Worker Recovery:
    slowcalc: null
    failingcalc: null
    memoryhogcalc: null
  Orchestration:
    chainedcalc: null
    transfersource: null
    transfertarget: null
    mixedatomiccalc: null
    mixednonatomiccalc: null

tracked_models:
  - stressrun
  - cartesiancalc
  - heavydbcalc
  - contentioncalc
  - slowcalc
  - chainedcalc
```

- [ ] **Step 2: Commit model_structure.yaml**

```bash
git add model_structure.yaml && git commit -m "feat: add model_structure.yaml registry"
```

---

### Task 10: Bootstrap, migrate, and smoke test

- [ ] **Step 1: Create .env from .env.example and set up venv**

```bash
cd ~/LUND_IT/LexStressLab
python -m venv .venv
source .venv/bin/activate
pip install -e /home/syscall/Documents/lex
pip install psycopg2-binary redis
cp .env.example .env
# Edit .env: set DATABASE_DEPLOYMENT_TARGET=default and real DB credentials
```

- [ ] **Step 2: Create database**

```bash
createdb lex_stress_lab  # or via psql: CREATE DATABASE lex_stress_lab;
```

- [ ] **Step 3: Run migrations**

```bash
set -a && source .env && set +a
lex migrate
```

Expected: Django creates tables for all 23 models.

- [ ] **Step 4: Smoke test — sync mode (CELERY_ACTIVE=false)**

```bash
CELERY_ACTIVE=false lex shell -c "
from A_Config.StressConfig import StressConfig
from A_Config.StressRun import StressRun
cfg = StressConfig.objects.create(name='smoke', dim_a_size=2, dim_b_size=2, period_count=2, fanout_count=3, rows_per_calc=5, contention_workers=1, chain_depth=2, chain_breadth=1, sleep_seconds=1, failure_rate_pct=0)
run = StressRun.objects.create(config=cfg, run_worker_recovery=False)
print('StressRun status:', run.is_calculated)
"
```

Expected: StressRun completes synchronously (no Celery), `is_calculated=SUCCESS`.

- [ ] **Step 5: Commit .gitignore and final state**

```bash
echo -e ".venv/\n.env\n__pycache__/\n*.pyc\nmigrations/\ncelerybeat-schedule*\n" > .gitignore
git add .gitignore && git commit -m "chore: add .gitignore, project ready for testing"
```
