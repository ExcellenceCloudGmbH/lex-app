## 11. Stress & Performance

**What it tests:** How the framework behaves **at volume** — not with 3 seeded rows, but with realistic production-scale data (~20k rows in key tables, with foreign-key fan-out). Everything in clusters 1–10 is correctness; this cluster is **scalability and regression detection on execution time**.

**Why it matters:** every customer-visible bug we've hit on staging in the last 18 months has shown up at scale first — the 500-row demo dataset passes, the real dataset of 20k invoices stalls or times out. Correctness tests will never catch:

- a save path that drifts from `O(n)` to `O(n²)` because someone added a `.objects.get()` inside a loop;
- an export that fits in memory at 500 rows and crashes the worker at 20k;
- a period calculation whose dependency graph causes re-fetches of the same rows 100 times;
- a bulk API call whose serializer runs one query per row (the classic N+1 on write).

This cluster runs rarely (nightly + release gate, not on every PR) and asserts on **time budgets** and **query counts**, not just final state.

**Why last:** needs everything else to work first. You can't stress-test a broken save or a broken calculation.

**Data volume conventions:**

| Label | Rows | Purpose |
|-------|------|---------|
| `SMALL` | 500 | Smoke — runs on every PR, catches gross regressions fast |
| `MEDIUM` | 5 000 | Nightly — catches algorithmic drift early |
| `LARGE` | 20 000 | Release gate — proof of production readiness |

Each test declares the volume it targets; CI decides which to run via a pytest/Django test tag.

**Models needed** (dedicated to this cluster — do NOT reuse the lightweight cluster 2/5 models, stress data pollutes them):

- `StressInvoice` — `LexModel` with ~15 fields (mix of `CharField`, `DecimalField`, `DateField`, one `ForeignKey` to `StressCounterparty`). This is the "wide row" under test — mirrors the shape of the real customer invoice table.
- `StressCounterparty` — ~200 rows, referenced by `StressInvoice.counterparty`. Provides the FK fan-out so export joins are realistic.
- `StressPeriod` — `LexModel` with a `valid_from` / `valid_to` pair. 20k rows spread across rolling 12-month windows; the period-calc test walks these.
- `PeriodAggregateCalc` — `CalculationModel` whose `calculate()` aggregates all `StressInvoice` rows inside a `StressPeriod` and writes a derived total. This is the work unit for the period-calculation scenarios.
- `DependentPeriodCalc` — `CalculationModel` that depends on `PeriodAggregateCalc` outputs for the previous 3 periods, so the dependency graph has real depth.

**Test harness** (will live in `lex/test_project/tests/stress/`):

- **`StressTestCase` base class** extending `E2ETestCase`, adding:
  - `bulk_seed(model, n, batch_size=1000)` — uses `bulk_create` with `ignore_conflicts=False` and chunked batches so we don't blow memory seeding 20k rows.
  - `assert_runtime_under(seconds, label)` — wraps `time.perf_counter()` and fails with a clear `"<label>: took X.XXs, budget Y.YYs"` message.
  - `assert_query_count_at_most(n, label)` — uses `CaptureQueriesContext` so we catch N+1 regressions with a concrete upper bound.
  - `measure(label)` — context manager that records `(duration, query_count, peak_memory_mb)` into a per-run JSON report written to `test_report/stress/<run-id>.json`. Budgets are asserted inside the CM; the JSON is for trend analysis.
- **Seed once, reuse across tests.** Stress data is seeded in `setUpClass`, not `setUp`, and wrapped in `TransactionTestCase` with `available_apps` discipline so the 20k rows survive per-test cleanup. The class tears everything down at the end.
- **Deterministic data.** Seeder uses a fixed `random.seed(42)` so runtime trends across CI runs reflect code changes, not input variance.

**Test scenarios:**

| # | Scenario | Volume | What We Measure / Assert |
|---|----------|--------|--------------------------|
| 11.1 | `bulk_create` seed — baseline insertion | LARGE (20k) | **Time < 30s**, no per-row save signals firing, history rows NOT written (documented bulk-path behaviour) |
| 11.2 | ORM `.save()` loop — single-row insert at scale | MEDIUM (5k) | Time budget asserts linear scaling; query count ≤ `2×n` (insert + history insert), catches signal-handler drift |
| 11.3 | List endpoint paginated read | LARGE | **p95 < 500ms per page of 100**, query count ≤ 4 per page (select + count + 2 FK prefetch), no N+1 on `counterparty` join |
| 11.4 | List endpoint filter + sort | LARGE | Filter returns correct subset; time < 1s; confirms the indexed path is used (`EXPLAIN` assertion — query plan contains `Index Scan`, not `Seq Scan`) |
| 11.5 | Full-table export (`export_url` / `/many/` GET) | LARGE | **Time < 15s end-to-end**, streaming response (not a memory-resident list), peak memory < 200MB, exported row count == 20k exactly |
| 11.6 | Excel / CSV writer loop | LARGE | Writer doesn't do per-row DB hits; one query to read, one pass to write; catches the "iterate queryset without `.iterator()`" regression |
| 11.7 | `PeriodAggregateCalc` over a single period | LARGE invoices in one period | Time < 5s; query count < 10 regardless of invoice count (one aggregate query, not one per invoice) |
| 11.8 | `PeriodAggregateCalc` over **all 12 periods** | LARGE across 12 periods | **Time < 60s total**, confirms no cross-period re-fetch, history rows correct for every period |
| 11.9 | `DependentPeriodCalc` — 3-period dependency chain × 12 periods | LARGE | Dependency resolution does each underlying period once, not once per dependent. Total time < 90s; query count grows `O(periods)`, not `O(periods²)` |
| 11.10 | Bulk API DELETE (`DELETE /many/?ids=…`) at volume | MEDIUM/LARGE selected subset | Selected rows deleted within budget; rows outside the explicit id set survive |
| 11.11 | ORM filtered `QuerySet.update()` baseline | MEDIUM | Time < 5s; exactly 1 UPDATE query (not n); affected row count and DB state match |
| 11.12 | Concurrent writes — 10 parallel clients each patching its own subset | MEDIUM | No deadlocks, no lost updates; audit rows from all 10 clients present; `created_by` correct per row |
| 11.13 | History-table growth — 20k updates to one row | LARGE (1 row, 20k revisions) | History query paginates in < 500ms at p95; `history_date` index actually used (`EXPLAIN` check); no blow-up on `.history.all()` when the queryset is consumed lazily |
| 11.14 | Permission filtering at scale | LARGE | `permission_read` invoked **once per list request**, not once per row; list endpoint time < 1s even with a custom `permission_read` that does work |
| 11.15 | Audit-log write throughput | LARGE | 20k API writes produce 20k audit rows in < 60s total; no audit-log writer contention (bounded connection count) |

### FK fan-out sub-cluster (11.16 – 11.20)

A dedicated dataset — ``FKHeavyInvoice`` with **four FK relationships**
(``counterparty``, ``period``, ``category``, ``currency``) — at **25 000
rows** proves the framework's behaviour under the classic production
workload: "a wide row with lots of joins, multiplied by a year of data".
Four joins is the threshold where the naive ORM path (one lazy SELECT
per FK attribute access) goes from annoying to catastrophic —
25 000 × 4 = 100 000 round trips on a well-intentioned-but-wrong loop.

Every scenario in this sub-cluster targets the customer-facing
regression: "Export last year's data takes 5 minutes and 2 GB of RAM".

| # | Scenario | Volume | What We Measure / Assert |
|---|----------|--------|--------------------------|
| 11.16 | Full REST ``list`` export of 25k × 4-FK rows | 25k default (2.5k on SMALL) | Exact row count, all 4 FKs present in serialized body, tier time budget. Generous budget until BUG-011 is fixed |
| 11.17 | Documented CSV export — ``select_related`` + ``.iterator()`` over 4 FKs | 25k | **Query count ≤ 8** regardless of row count — 1 main SELECT with four JOINs + session/auth noise |
| 11.18 | Anti-baseline: naive iteration, no ``select_related``, no ``.iterator()`` | 2 000 rows scanned | Query count is **at least** ``2.5 × rows`` (clear N+1 signal). Records the delta vs 11.17 in the trend report — a 1-query-vs-8001-query gap. If a future optimisation makes this bounded, the test fails and we update the reference docs |
| 11.19 | ``.iterator()`` vs materialised ``list()`` peak memory | 25k | ``tracemalloc``-measured iterator peak ≤ 50% of list peak (typically 1/10). Skipped at SMALL — delta too small to distinguish reliably |
| 11.20 | Aggregated JOIN — ``Sum(invoices__amount_net)`` grouped by counterparty | 25k | Exactly 1 SELECT (+ ≤ 4 session noise). Aggregate sum matches the raw table aggregate — proves the JOIN covers every row and the GROUP BY doesn't drop partitions |

Observed on first run (SMALL, 2.5k rows):

* **11.17**: 1 query for the entire export (documented path).
* **11.18**: 8 001 queries for the same work done naively — a clear **8 000× query-count delta** between the documented and naive paths. That is the cost the team now has a regression gate on.
* **11.20**: 1 query for the full grouped aggregate over the FK join.

**Budgets are hard gates.** A scenario that goes 10% over budget is a failure, not a warning. Budgets are tuned once on the CI runner baseline (recorded in `test_report/stress/baseline.json`) and tightened — never loosened — with each release. If a scenario legitimately needs more time (new feature, new work in the hot path), the PR must update the baseline **in the same commit** that adds the work, so the change is reviewable.

---
