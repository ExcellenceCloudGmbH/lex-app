# LexStressLab — Design Spec

> **Date:** 2026-05-26
> **Author:** Claude (Opus 4.6) + Hazem
> **Status:** Approved
> **Location:** `~/LUND_IT/LexStressLab/`

## 1. Goal

Build a standalone lex-app project that serves as both a **diagnostic reproduction** of the ACP_IPT_DI_EXC database overload and a **benchmark harness** for the lex-app framework's parallelization, DB performance, worker recovery, and orchestration. The project uses abstract/synthetic models (no domain pretense) with tunable knobs exposed as environment variables with UI overrides.

### Success criteria

- Run `lex celery worker -n w1@%h` through `w5@%h` against the project and observe behavior under load.
- Reproduce the ACP "400K tasks / N+1 queries / queue saturation" problem with explicit knobs.
- Test worker recovery (SIGKILL a worker mid-task, observe requeue).
- A/B compare `bulk_create` vs individual `.save()` with measured query counts and timings.
- All scenarios selectable independently via `StressRun` boolean flags or direct `.create()` calls.

---

## 2. Project Structure

```
~/LUND_IT/LexStressLab/
├── A_Config/
│   ├── StressConfig.py              # Master knobs model (env defaults + UI overrides)
│   └── StressRun.py                 # Top-level orchestrator (CalculationModel)
├── B_Parallelization/
│   ├── DimensionA.py                # LexModel — seed data axis
│   ├── DimensionB.py                # LexModel — seed data axis
│   ├── DimensionC.py                # LexModel — optional 3rd axis
│   ├── Period.py                    # LexModel — time axis
│   ├── CartesianCalc.py             # CalculatedModelMixin — combination explosion
│   ├── FanOutCalc.py                # CalculationModel — queue saturation
│   ├── FanOutLeaf.py                # CalculationModel — leaf task for FanOutCalc
│   └── BulkVsNaiveCalc.py          # CalculationModel — A/B query benchmark
├── C_DBStress/
│   ├── OutputRow.py                 # LexModel — target table for bulk writes
│   ├── SharedCounter.py             # LexModel — contention target
│   ├── HeavyDBCalc.py              # CalculationModel — N+1 / bulk patterns
│   ├── ContentionCalc.py           # CalculationModel — lock contention
│   └── IdempotencyCalc.py          # CalculatedModelMixin — duplicate handling
├── D_WorkerRecovery/
│   ├── SlowCalc.py                  # CalculationModel — sleep(N), heartbeat test
│   ├── FailingCalc.py              # CalculationModel — configurable failure rate
│   └── MemoryHogCalc.py            # CalculationModel — OOM simulation
├── E_Orchestration/
│   ├── ChainedCalc.py              # CalculationModel — self-referential tree (variable depth)
│   ├── TransferSource.py           # CalculationModel — must finish first
│   ├── TransferTarget.py           # CalculationModel — depends on source output
│   ├── MixedAtomicCalc.py          # CalculationModel — is_atomic=True, fails
│   └── MixedNonAtomicCalc.py       # CalculationModel — is_atomic=False, fails
├── _Helpers/
│   ├── metrics.py                   # Query counter, timing decorator, summary reporter
│   └── generators.py               # Seed data factories (create N dimensions, periods)
├── model_structure.yaml             # Lex model registry
├── lex_config.py                    # Lex project config (INITIAL_DATA, PROJECT_GROUPS)
├── requirements.txt                 # lex-app + test deps
└── .env.example                     # Template with all STRESS_* env vars documented
```

---

## 3. A_Config — Configuration & Orchestration

### StressConfig (LexModel)

One model with grouped fields. Environment variables provide defaults; UI overrides per-run.

| Group | Field | Type | Env Var | Default | Purpose |
|-------|-------|------|---------|---------|---------|
| Parallelization | `dim_a_size` | int | `STRESS_DIM_A` | 10 | Size of DimensionA axis |
| | `dim_b_size` | int | `STRESS_DIM_B` | 10 | Size of DimensionB axis |
| | `dim_c_size` | int | `STRESS_DIM_C` | 0 | Size of DimensionC axis (0=skip) |
| | `period_count` | int | `STRESS_PERIODS` | 4 | Number of time periods |
| | `fanout_count` | int | `STRESS_FANOUT` | 100 | Leaf tasks for FanOutCalc |
| DB Stress | `rows_per_calc` | int | `STRESS_ROWS_PER_CALC` | 50 | Rows written per HeavyDBCalc/BulkVsNaiveCalc |
| | `use_bulk_create` | bool | — | True | Toggle for BulkVsNaiveCalc default method |
| | `contention_workers` | int | `STRESS_CONTENTION_WORKERS` | 3 | Concurrent ContentionCalc instances |
| | `contention_increments` | int | `STRESS_CONTENTION_INCREMENTS` | 100 | Increments per ContentionCalc worker |
| Recovery | `sleep_seconds` | int | `STRESS_SLEEP` | 30 | Duration for SlowCalc |
| | `failure_rate_pct` | int | `STRESS_FAILURE_RATE` | 0 | 0-100, percentage of FailingCalc tasks that raise |
| | `memory_mb` | int | `STRESS_MEMORY_MB` | 0 | MB to allocate in MemoryHogCalc (0=skip) |
| Orchestration | `chain_depth` | int | `STRESS_CHAIN_DEPTH` | 3 | Nesting depth for ChainedCalc |
| | `chain_breadth` | int | `STRESS_CHAIN_BREADTH` | 2 | Children per node in chain |

### StressRun (CalculationModel)

Top-level orchestrator. FK to `StressConfig`. Boolean flags per concern folder.

```python
class StressRun(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    run_parallelization = BooleanField(default=True)
    run_db_stress = BooleanField(default=True)
    run_worker_recovery = BooleanField(default=True)
    run_orchestration = BooleanField(default=True)
    is_atomic = False

    def calculate(self):
        cfg = self.config
        generators.ensure_seed_data(cfg)  # create DimensionA/B/C/Period rows if missing
        with WaitForTasks():
            if self.run_parallelization:
                self._run_parallelization(cfg)
            if self.run_db_stress:
                self._run_db_stress(cfg)
            if self.run_worker_recovery:
                self._run_worker_recovery(cfg)
            if self.run_orchestration:
                self._run_orchestration(cfg)
```

Each `_run_*` method creates the relevant scenario model instances. They all execute inside the same `WaitForTasks` scope so StressRun blocks until everything completes.

You can also bypass StressRun entirely and call any scenario directly:
```python
CartesianCalc.create(config=my_config, dim_a=..., dim_b=..., period=...)
```

---

## 4. B_Parallelization — Combination & Queue Scenarios

### DimensionA, DimensionB, DimensionC, Period (LexModel)

Seed data. Created by `generators.ensure_seed_data(cfg)` which bulk-creates N rows per axis based on config. Each has `config` FK + `code` CharField.

### CartesianCalc (CalculatedModelMixin)

**Tests:** Combination explosion, parallelizable_fields grouping, Celery task fan-out.

- `defining_fields = ["dim_a", "dim_b", "period"]` (3rd axis added dynamically if `dim_c_size > 0`)
- `parallelizable_fields = ["dim_a"]` — one Celery task per DimensionA value
- `get_selected_key_list(key)` queries dimension tables filtered by `config`
- `calculate()` does light math — the stress is in combination count, not per-calc work
- Stores `result_value` for verification

**Scale examples:**
| dim_a | dim_b | periods | Combinations | Celery tasks |
|-------|-------|---------|-------------|-------------|
| 10 | 10 | 4 | 400 | 10 |
| 50 | 50 | 4 | 10,000 | 50 |
| 50 | 50 | 4 (+dim_c=10) | 100,000 | 50 |

### FanOutCalc + FanOutLeaf (CalculationModel)

**Tests:** Broker queue saturation (the ACP 400K-task problem).

- `FanOutCalc.calculate()` creates `config.fanout_count` FanOutLeaf instances inside `WaitForTasks`
- Each FanOutLeaf has `@lex_shared_task calculate()` doing trivial work
- Measures total elapsed time and tasks-per-second throughput

### BulkVsNaiveCalc (CalculationModel)

**Tests:** Direct A/B comparison of `bulk_create` vs individual `.save()`.

- The orchestrator creates TWO instances per run: one with `method="bulk"`, one with `method="naive"`
- Both write `config.rows_per_calc` OutputRow records
- Records `elapsed_ms` and `query_count` via `metrics.query_counter()`
- Results visible in the UI for side-by-side comparison

---

## 5. C_DBStress — Database Load Scenarios

### OutputRow (LexModel)

Shared write target. Fields: `source`, `batch_id`, `index`, `value`.

### SharedCounter (LexModel)

Single-row contention target. Fields: `name` (unique), `value`, `version`.

### HeavyDBCalc (CalculationModel)

**Tests:** N+1 query pattern (reproduces ACP VehiclePosting).

- Phase 1: Write N rows individually (`.save()` loop)
- Phase 2: For each row, run an aggregate query (`filter(index__lte=i).aggregate(Sum)`) then update
- Total queries: ~3N (N inserts + N aggregates + N updates)
- Records `total_queries` and `elapsed_ms`

### ContentionCalc (CalculationModel)

**Tests:** Row-level lock contention, deadlock detection.

- `config.contention_workers` instances all target the same SharedCounter row
- Each does `config.contention_increments` iterations of `select_for_update` + increment + save
- `is_atomic = False` — manages its own `transaction.atomic()` per increment
- Records `succeeded` and `deadlocks` counts
- **Verification:** after all workers finish, `SharedCounter.value` should equal `contention_workers * contention_increments` minus lost updates

### IdempotencyCalc (CalculatedModelMixin)

**Tests:** Framework duplicate handling when `create()` called twice.

- `defining_fields = ["dim_a", "period"]`
- `parallelizable_fields = ["dim_a"]`
- Orchestrator calls `.create()` with `run_number=1`, then again with `run_number=2`
- After second run: row count unchanged, `run_number=2` on all rows

---

## 6. D_WorkerRecovery — Failure & Recovery Scenarios

### SlowCalc (CalculationModel)

**Tests:** Heartbeat expiry, worker recovery requeue, `lex_max_retries`.

```python
class SlowCalc(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    task_label = CharField(max_length=50)
    is_atomic = True

    @lex_shared_task(lex_max_retries=2)
    def calculate(self):
        duration = self.config.sleep_seconds
        logger.info("SlowCalc %s sleeping %ds", self.task_label, duration)
        time.sleep(duration)
        logger.info("SlowCalc %s completed", self.task_label)
```

**Usage:** Start SlowCalc, wait for heartbeat keys to appear in Redis, then `pkill -9 -f 'victim@'`. Observe:
- Heartbeat thread wrote `lex:wrk:victim@...` and `lex:task:<id>`
- Supervisor sweep detects stale heartbeat after TTL expires
- Task requeued to surviving worker
- After `lex_max_retries` kills: `MaxRequeueExceeded` written to result backend

### FailingCalc (CalculationModel)

**Tests:** Error handling, partial success in batch scenarios, retry behavior.

```python
class FailingCalc(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    index = IntegerField()
    is_atomic = True

    @lex_shared_task
    def calculate(self):
        if random.randint(1, 100) <= self.config.failure_rate_pct:
            raise RuntimeError(f"FailingCalc[{self.index}] deliberate failure")
```

The orchestrator creates N instances (e.g. 20). With `failure_rate_pct=30`, roughly 6 will fail. Lets you observe:
- Error state propagation to parent
- Which tasks succeed vs fail
- CallbackTask.on_failure behavior
- Whether WaitForTasks raises or collects errors

### MemoryHogCalc (CalculationModel)

**Tests:** OOM-kill behavior, worker eviction, recovery after pod crash.

```python
class MemoryHogCalc(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    is_atomic = True

    @lex_shared_task(lex_max_retries=1)
    def calculate(self):
        mb = self.config.memory_mb
        if mb <= 0:
            return
        logger.info("MemoryHogCalc allocating %d MB", mb)
        # Allocate and touch memory so it's not just virtual
        data = bytearray(mb * 1024 * 1024)
        for i in range(0, len(data), 4096):
            data[i] = 0xFF
        time.sleep(5)  # hold the allocation
```

**Usage:** Set `memory_mb` to something near the worker's cgroup limit. Worker gets OOM-killed by the kernel. Recovery system detects dead heartbeat and requeues — but with `lex_max_retries=1`, it fails permanently on the second OOM, writing `MaxRequeueExceeded` to the result backend.

---

## 7. E_Orchestration — Dependency & Nesting Scenarios

### ChainedCalc: ParentCalc / ChildCalc / GrandchildCalc

**Tests:** Nested `WaitForTasks`, failure propagation up the hierarchy.

The chain depth and breadth are configurable:
- `chain_depth=3, chain_breadth=2` → 1 parent → 2 children → 4 grandchildren = 7 tasks
- `chain_depth=4, chain_breadth=3` → 1 → 3 → 9 → 27 = 40 tasks

```python
class ParentCalc(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    depth = IntegerField(default=0)  # current depth in tree
    label = CharField(max_length=100)
    is_atomic = False

    def calculate(self):
        if self.depth >= self.config.chain_depth:
            return  # leaf — no children
        with WaitForTasks():
            for i in range(self.config.chain_breadth):
                ChildCalc.objects.create(
                    config=self.config,
                    parent=self,
                    depth=self.depth + 1,
                    label=f"{self.label}.{i}",
                )
```

Actually, since depth is variable, we use a single self-referential model instead of three separate classes:

```python
class ChainedCalc(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    parent = ForeignKey('self', null=True, blank=True, ...)
    depth = IntegerField(default=0)
    label = CharField(max_length=200)
    is_atomic = False

    @lex_shared_task
    def calculate(self):
        if self.depth >= self.config.chain_depth:
            return
        with WaitForTasks():
            for i in range(self.config.chain_breadth):
                ChainedCalc.objects.create(
                    config=self.config,
                    parent=self,
                    depth=self.depth + 1,
                    label=f"{self.label}.{i}",
                )
```

**What you can test:**
- Nested WaitForTasks scoping (each level waits only for its direct children)
- Failure at depth=2 propagating up to depth=0
- Total task tree size with different breadth/depth combinations

### TransferSource + TransferTarget

**Tests:** Cross-calculation data dependency (mimics ACP's vehicle transfer pattern).

```python
class TransferSource(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    output_value = FloatField(default=0)
    is_atomic = True

    @lex_shared_task
    def calculate(self):
        # Simulate heavy computation that produces a result
        self.output_value = sum(range(1000))

class TransferTarget(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    source = ForeignKey(TransferSource, ...)
    received_value = FloatField(default=0)
    is_atomic = True

    @lex_shared_task
    def calculate(self):
        # Must read source's output — if source hasn't finished, gets stale/zero data
        self.source.refresh_from_db()
        self.received_value = self.source.output_value
```

The orchestrator runs sources first (in `WaitForTasks`), then targets:

```python
def _run_orchestration(self, cfg):
    sources = []
    with WaitForTasks():
        for i in range(3):
            sources.append(TransferSource.objects.create(config=cfg))
    # Sources are done — now targets can safely read their output
    with WaitForTasks():
        for src in sources:
            TransferTarget.objects.create(config=cfg, source=src)
```

**What you can test:**
- Correct ordering: targets read source output after completion
- Stale-read hazard: what happens if you skip the ordering and run both in the same WaitForTasks scope?
- With recovery: kill a source worker — does the target eventually get the right value after requeue?

### MixedAtomicCalc + MixedNonAtomicCalc

**Tests:** Partial rollback behavior when some children use `is_atomic=True` and others `is_atomic=False`.

Two separate model classes (the framework reads `is_atomic` as a class attribute, not per-instance):

```python
class MixedAtomicCalc(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    should_fail = BooleanField(default=False)
    is_atomic = True

    @lex_shared_task
    def calculate(self):
        OutputRow(source="mixed-atomic", batch_id=str(self.pk), index=0, value=1).save()
        if self.should_fail:
            raise RuntimeError("MixedAtomicCalc deliberate failure")

class MixedNonAtomicCalc(CalculationModel):
    config = ForeignKey(StressConfig, ...)
    should_fail = BooleanField(default=False)
    is_atomic = False

    @lex_shared_task
    def calculate(self):
        OutputRow(source="mixed-nonatomic", batch_id=str(self.pk), index=0, value=1).save()
        if self.should_fail:
            raise RuntimeError("MixedNonAtomicCalc deliberate failure")
```

The orchestrator creates one of each with `should_fail=True`. After both fail:
- The atomic one should have rolled back its OutputRow
- The non-atomic one should have left its OutputRow in the DB

---

## 8. _Helpers — Instrumentation & Seed Data

### metrics.py

```python
class query_counter:
    """Context manager that counts Django DB queries executed within its scope."""

    def __enter__(self):
        self.count = 0
        # Hook into Django's connection.queries or use django.test.utils.CaptureQueriesContext
        return self

    def __exit__(self, *exc):
        # Finalize count
        pass

def timed(label):
    """Decorator that logs elapsed time for a function call."""
    pass

def summary_report(stress_run):
    """Collect all scenario results for a StressRun and log a summary table."""
    pass
```

### generators.py

```python
def ensure_seed_data(config: StressConfig):
    """Create DimensionA/B/C/Period rows for a config if they don't exist yet."""
    if DimensionA.objects.filter(config=config).count() >= config.dim_a_size:
        return  # already seeded

    DimensionA.objects.filter(config=config).delete()  # clean slate
    DimensionA.objects.bulk_create([
        DimensionA(config=config, code=f"A-{i:04d}")
        for i in range(config.dim_a_size)
    ])
    # Same for B, C (if dim_c_size > 0), Period
```

---

## 9. Project Configuration Files

### lex_config.py

```python
INITIAL_DATA = None  # No seed JSON — generators.py creates seed data dynamically
PROJECT_GROUPS = ["lex_stress_lab"]
```

### model_structure.yaml

Standard lex model registry declaring all models from all folders. Groups mirror the folder layout.

### .env.example

```bash
# --- Django / Lex ---
DJANGO_SETTINGS_MODULE=lex_app.settings
DATABASE_URL=postgres://lex:lex@localhost:5432/lex_stress_lab
SECRET_KEY=stress-lab-dev-key

# --- Celery ---
CELERY_ACTIVE=true
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=db+postgresql://lex:lex@localhost:5432/lex_stress_lab

# --- Worker Recovery ---
LEX_TASK_HEARTBEAT_INTERVAL=5
LEX_TASK_HB_TTL_MULTIPLIER=3
LEX_TASK_SUPERVISOR_SCAN_INTERVAL=10
LEX_TASK_RECOVERY_ENABLED=true

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

### requirements.txt

```
lex-app
psycopg2-binary
redis
```

Installed as editable: `pip install -e /home/syscall/Documents/lex` (local lex-app source).

---

## 10. How to Run

### Setup

```bash
cd ~/LUND_IT/LexStressLab
python -m venv .venv && source .venv/bin/activate
pip install -e /home/syscall/Documents/lex
pip install -r requirements.txt
cp .env.example .env  # edit DB credentials
set -a && source .env && set +a
lex migrate
```

### Single-worker smoke test (synchronous)

```bash
# CELERY_ACTIVE=false in .env
lex shell  # then create StressConfig + StressRun in Python
```

### Multi-worker stress test

```bash
# Terminal 1: beat
lex celery beat --scheduler django_celery_beat.schedulers:DatabaseScheduler -l info

# Terminal 2-6: workers
lex celery worker -n w1@%h --concurrency=1 -Q celery -l info
lex celery worker -n w2@%h --concurrency=1 -Q celery -l info
# ... up to w5

# Terminal 7: trigger
PYTHONPATH=. DJANGO_SETTINGS_MODULE=lex_app.settings python -c "
import django; django.setup()
from A_Config.StressConfig import StressConfig
from A_Config.StressRun import StressRun
cfg = StressConfig.objects.create(name='run-1', dim_a_size=20, dim_b_size=20)
run = StressRun.objects.create(config=cfg, run_db_stress=False, run_worker_recovery=False)
"
```

### Worker recovery test

```bash
# Start 2+ workers, trigger SlowCalc (sleep=60), then:
pkill -9 -f 'w1@'
# Watch w2's logs for: lex_recovery action=requeue ...
```

---

## 11. Model Count Summary

| Folder | Models | LexModel | CalculationModel | CalculatedModelMixin |
|--------|--------|----------|-----------------|---------------------|
| A_Config | 2 | 1 (StressConfig) | 1 (StressRun) | 0 |
| B_Parallelization | 8 | 4 (DimA/B/C, Period) | 2 (FanOut, BulkVsNaive) | 1 (Cartesian) |
| C_DBStress | 5 | 2 (OutputRow, SharedCounter) | 2 (HeavyDB, Contention) | 1 (Idempotency) |
| D_WorkerRecovery | 3 | 0 | 3 (Slow, Failing, MemoryHog) | 0 |
| E_Orchestration | 4 | 0 | 4 (Chained, TransferSrc/Tgt, MixedAtomic, MixedNonAtomic) | 0 |
| **Total** | **22** | **7** | **13** | **2** |
| _Helpers | 2 files (not models) | — | — | — |

Plus `FanOutLeaf` (CalculationModel) — **23 models total**.

**Note:** All models must declare `class Meta: app_label = "lex_app"` for Django discovery.
