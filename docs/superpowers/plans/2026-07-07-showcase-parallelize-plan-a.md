# Plan A — Matrix per cluster + aggregate job

Parallelization plan for `lex-app`'s `pip_publish.yml` release gate, which currently runs all 13 test clusters sequentially via `showcase_tests.yml` → `run_showcase_suite.py`.

---

## Current layout

- `pip_publish.yml:135` calls the reusable `showcase_tests.yml` as a single gate
- `showcase_tests.yml:100` runs one `showcase` job that calls `python .github/scripts/run_showcase_suite.py --only <cluster_csv>`
- `run_showcase_suite.py:573` iterates the 13 clusters in a plain `for` loop, each one shelling out to `coverage run -a -m lex pytest <path>`
- A single `manifest.json` + accumulated `.coverage` is produced, then one PDF + one SendGrid email goes out

So today there is zero parallelism: clusters run strictly one-after-another inside one runner.

---

## Workflow changes — `.github/workflows/showcase_tests.yml`

Replace the single `showcase` job with three:

```
jobs:
  plan:        # compute the cluster matrix from inputs.clusters
  showcase:    # matrix(cluster) — one runner per cluster, own Postgres+Redis
  aggregate:   # merge artifacts, build PDF, send email, gate
```

### `plan` job

Runs in seconds. Parses `inputs.clusters` (CSV with optional `key:test_suffix` entries — current syntax stays unchanged) into a JSON list of `{key, test_suffix}` objects and exposes it as `outputs.matrix`. Done in pure bash + python (the existing parser already lives in `run_showcase_suite.py`'s `main`; we move it to a tiny new helper `.github/scripts/plan_showcase_matrix.py` so both stay in sync).

### `showcase` job

`strategy.matrix.cluster: ${{ fromJson(needs.plan.outputs.matrix) }}` with `fail-fast: false` so one red cluster doesn't cancel the rest (and the email still reports them all). Each matrix entry:

- spins its own `postgres` + `redis` services (copy current `services:` block as-is — already isolated per runner)
- installs deps (same as today, with the cache)
- runs `python .github/scripts/run_showcase_suite.py --only "<key>[:suffix]" --out manifest.partial.json --coverage-data .coverage.<key>` — the runner already handles a single-cluster `--only`, we just add `--coverage-data` to parameterise the `.coverage` filename so artifacts don't collide on download
- uploads two artifacts: `manifest-partial-<key>` (manifest.partial.json) and `coverage-data-<key>` (`.coverage.<key>`)
- `continue-on-error: true` — never short-circuits the matrix; the aggregate job is the gate

### `aggregate` job

`needs: [showcase]`, `if: always()`. Steps:

1. Download every `manifest-partial-*` and `coverage-data-*` artifact (`actions/download-artifact@v4` with pattern globs)
2. Install `coverage`, `weasyprint`, `cairosvg`, `sendgrid` (only what the aggregator + report + email need — no lex install needed here)
3. Run new script `.github/scripts/aggregate_showcase_manifests.py --partials ./partials/ --out manifest.json` that:
   - Loads every `manifest.partial.json`
   - Concatenates `clusters` lists in `CLUSTERS` declaration order (single source of truth — preserves the existing report row order)
   - Recomputes `overall` (sum of counts, max/sum of `wall_s`, `clusters_total`, `clusters_passing`, `outcome = success iff every cluster.outcome == success`)
4. `coverage combine .coverage.*` → `coverage xml --rcfile=.coveragerc -o coverage.xml` → set `overall.coverage_pct` from `coverage report` (same regex used today at `run_showcase_suite.py:341`)
5. Upload `backend-coverage-report` artifact (same name → downstream consumers unchanged)
6. `build_showcase_report.py` → unchanged, consumes the merged `manifest.json`
7. `send_showcase_email.py` → unchanged
8. Final `Fail job if any cluster failed` — same logic as today, against the merged manifest

### Job outputs

(`workflow_call` contract) — `overall_ok` and `clusters_passing` move from `showcase` to `aggregate`. `pip_publish.yml`'s gate-release dependency continues to work because it only consumes the workflow's success/failure, not these outputs.

---

## Script changes

### `.github/scripts/run_showcase_suite.py`

Small surgery, no behaviour change for the single-cluster case:

- Add `--coverage-data <path>` arg, default `.coverage` (today's behaviour). Pass it through to the subprocess env as `COVERAGE_FILE=<path>` so `coverage run -a` writes to the parameterised file. The existing `coverage erase` at line 569 uses the same file (already respects `COVERAGE_FILE` via the env passthrough).
- The per-cluster coverage attribution at line 598 / `_overall_coverage_pct` becomes meaningless in a single-cluster invocation. Already removed for the cluster rows (lines 587-597 comment confirms it's now a single project-wide value). For partials we set `coverage_pct = None` and let the aggregator overwrite all rows with the combined project-wide percentage after `coverage combine`. Two-line change in the per-cluster loop.
- No other logic changes — `--only one_cluster` already works today, and the whole single-cluster code path is already tested.

### `.github/scripts/aggregate_showcase_manifests.py`

New ~100-line script. Pure manifest-merge + coverage-combine wrapper. Mirrors the aggregation block currently at `run_showcase_suite.py:598-625`.

### `.github/scripts/plan_showcase_matrix.py`

New ~30-line script. Parses the cluster CSV (same syntax as `--only`) and emits a JSON array on stdout for the matrix step. Keeps the parser in one place — the matrix job and the runner read the same selector grammar.

### Unchanged

- `build_showcase_report.py` — already consumes the final merged shape
- `send_showcase_email.py` — already consumes the final merged shape
- `showcase_clusters.py` — remains the single source of truth for the row order (the aggregator sorts partials by `CLUSTERS` declaration order, exactly like today)
- `pip_publish.yml` — gate semantics unchanged

---

## Behavioural invariants preserved

1. One email per release, with the same PDF, sent always (pass or fail) — moves to the aggregate job
2. One `coverage.xml` artifact named `backend-coverage-report` — produced by the aggregate job
3. One `platform-health-report` artifact with html+pdf+manifest — produced by the aggregate job
4. Gate semantics: `pip_publish.yml`'s `needs.gate-release.result == 'success' || 'skipped'` continues to work — the reusable workflow still succeeds iff every cluster passed
5. Cluster row order in the PDF identical to today (driven by `CLUSTERS` declaration order)
6. The `lex:gate-passed` skip marker logic in `pip_publish.yml` is untouched
7. Manual `workflow_dispatch` selector grammar is unchanged — `init,crud_api,api_layer:test_x.TestClassX.test_y,...` still works in both jobs

---

## Wall-clock impact

13 clusters today run sequentially in one runner. With matrix:

- Spin-up cost (install deps, lex install, postgres+redis health) is paid per runner — call it ~90s. This is the new floor.
- Test time becomes `max(cluster_walls)` instead of `sum(cluster_walls)`.
- GitHub-hosted runners on the standard plan give 20 concurrent jobs on free-tier orgs / 60 on Team — all 13 will run truly in parallel.
- Net effect: total CI wall-clock drops from ≈ `sum(cluster_walls) + ~90s` to ≈ `max(cluster_walls) + ~90s + ~60s aggregate`. For the current suite (where calculations + stress + 8k Redis tend to dominate), realistic 3-5× speedup.

---

## Cost

- Runner-minutes go UP (each cluster pays the ~90s install + service spin-up). On a free org this is offset by the much shorter wall clock; on a paid plan this is a real line-item increase. Acceptable trade-off for release-day responsiveness, but call it out.
- Mitigation: the `actions/cache@v3` keyed on `hashFiles('**/requirements.txt')` is already in place — once warm, install drops to ~20s per runner.

---

## Risks / things to watch

- **Postgres template-DB race**: each cluster gets its own Postgres service → no cross-cluster collision possible. Win.
- **Redis DB 15 collision**: same — own Redis per cluster.
- **`coverage combine` across runners**: requires every partial `.coverage.<key>` file to have been produced with consistent source paths. Already the case (all runners check out the same commit and run from `REPO_ROOT`).
- **Service-only clusters**: the Redis service is needed only by `celery_async`. Today it's spun for every run because the workflow is monolithic. In the matrix world we could declare services per-cluster to save runner spin-up time on the 12 clusters that don't need Redis — but that adds a `services:` switch keyed on `matrix.cluster.key` which GitHub Actions doesn't support natively (services are static per-job). Cleanest: keep both services on every matrix job (~5s cost) and revisit later if needed.
- **`fail-fast: false`**: critical — without it, a single red cluster cancels in-flight peers and the email becomes incomplete.
- **The `gate_selftest` cluster** at `showcase_clusters.py:484` always fails on purpose. It's left out of default selectors today and the matrix plan preserves that — the selector grammar passes through verbatim.

---

## Rollback path

If the matrix design causes problems on the first release, reverting is one PR: restore `showcase_tests.yml` from git, delete the two new aggregator/planner scripts, drop the `--coverage-data` arg from `run_showcase_suite.py`. No script API breaks for anything else.

---

## Implementation order

1. Write `plan_showcase_matrix.py` and `aggregate_showcase_manifests.py`
2. Patch `run_showcase_suite.py` for `--coverage-data`
3. Rewrite `showcase_tests.yml` to the 3-job shape
4. Leave `pip_publish.yml`, `build_showcase_report.py`, `send_showcase_email.py`, `showcase_clusters.py` untouched
