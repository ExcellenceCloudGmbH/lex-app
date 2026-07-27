# Pytest Cutover Hotfixes — 27 May 2026

> **Status:** Landed across two hotfix commits on `pytest-migration/hotfix-db-setup-and-parser` (PR #514).
> **Audience:** Anyone debugging showcase CI runs after the pytest migration, or touching `lex pytest` / `run_showcase_suite.py`.
> **See also:** [showcase-ci.md](showcase-ci.md), [copilot-test-bot.md](copilot-test-bot.md), [`run_showcase_suite.py`](../../.github/scripts/run_showcase_suite.py), [`lex/bin/lex.py`](../../lex/bin/lex.py), [`lex/tools/test_groups.py`](../../lex/tools/test_groups.py).

## Background

The pytest cutover (PRs #510, #511, #512, #513) replaced Django's `manage.py test` runner with pytest as the entry point for the showcase suite and CI gates. The intent was simple: keep the same tests, swap the orchestrator. The tests themselves all live under [`lex/test_project/tests/`](../../lex/test_project/tests/) — that path is the single `tests_entrypoint` declared in [`lex_test_config.yaml`](../../lex_test_config.yaml) and is the only thing [`lex/tools/test_groups.py`](../../lex/tools/test_groups.py) collects from. Nothing outside `test_project/` runs as part of the showcase suite; the per-cluster folders (`init/`, `crud_api/`, `permissions/`, …) become pytest groups via module-level `pytestmark = pytest.mark.<group>`, and the canonical group list lives in `lex_test_config.yaml`.

After all four migration PRs merged, the showcase pipeline went green — but the green was a lie: zero tests were actually running. Two hotfix commits were needed to make CI honest again, each one fixing **two bugs** (one structural, one observability). They land together in PR #514. Read this end-to-end before touching either `lex pytest` or the showcase orchestrator; the bugs hid each other in a way that's easy to re-introduce.

| Commit | Symptom | Structural bug | Observability bug |
|--------|---------|----------------|-------------------|
| [`af2a6ea`](#first-hotfix--af2a6ea) | "0 errors, ✓" while 37 tests failed to even start | Django test DB never created (`db_<repo>` doesn't exist) | `_PYTEST_SUMMARY_RE` was order-dependent — "1 warning, 37 errors" didn't match |
| [`60f423b`](#second-hotfix--60f423b) | "0 passed, 0 failed, 0 errors, ✓" while every cluster crashed during bootstrap | `setup_databases()` tried every `DATABASES` alias, including the CI-unset `GCP` host | Parser reported success when pytest never printed a summary; `proc.returncode` was ignored |
| [Third hotfix](#third-hotfix--register_converter-collision) | `ValueError: Converter 'model' is already registered.` during the first cross-cluster `reverse()` in a pytest session | `processAdminSite._get_urls()` calls `register_converter(..., "model")` unconditionally on every access | (none — the symptom was loud, but only because of the first two hotfixes) |
| [Fourth hotfix](#fourth-hotfix--summary-line-regex-missed-long-runs-and-subtests) | Cluster row read `0 passed, 0 failed, 0 errors, 0 skipped` for a cluster whose pytest output ended in `151 passed, 1 warning, 19 subtests passed in 99.26s (0:01:39)` | (none — pytest behaved correctly) | `_PYTEST_SUMMARY_LINE_RE` didn't allow the ` (H:MM:SS)` suffix pytest appends past ~60s, and `subtests` wasn't in the recognised-token map |

The pattern is identical both times: a structural failure stopped pytest from running, and a parser/orchestrator gap reported the resulting silence as success. Each fix had to address both halves — otherwise the next regression hides itself the same way.

---

## First hotfix — `af2a6ea`

### Symptom

CI failed with **37 collection errors**. Every `unittest.TestCase`-derived test (notably `E2ETestCase`, which extends `TransactionTestCase`) errored at `setUp` trying to connect to `db_lex-app` — the production database name from the repo's `.env`, which doesn't exist in the postgres service container. The showcase manifest then reported the whole run as `0 errors / outcome=success` and the platform-health email said "all green".

### Bug 1a — test database was never created

#### Root cause

`manage.py test` does a lot of setup before it ever invokes test collection. The relevant pieces are inside [`DiscoverRunner.run_tests`](https://github.com/django/django/blob/main/django/test/runner.py):

1. `setup_test_environment()` — installs the test client, patches `RequestFactory`, sets deprecation filters.
2. `setup_databases(...)` — creates the `test_<dbname>` database (or `test_<NAME>` from `DATABASES[alias]["TEST"]`), runs migrations, and re-points `connection.settings_dict["NAME"]` so any ORM call during tests hits the test DB, not production.

The pytest cutover replaced `DiscoverRunner.run_tests(...)` with a direct `pytest.main(...)` call but **didn't replicate that setup**. `connection.settings_dict["NAME"]` therefore still pointed at `db_lex-app` from the project's `.env`, and the postgres service container in CI only ships an empty `postgres` database. Result: 37 `OperationalError: database "db_lex-app" does not exist` at `setUp`, before a single test body ran.

#### Fix

[`lex/bin/lex.py`](../../lex/bin/lex.py) now does what `DiscoverRunner.run_tests` does, around `pytest.main(...)`:

```python
from django.test.runner import DiscoverRunner
from django.test.utils import setup_test_environment, teardown_test_environment

setup_test_environment()
_db_runner = DiscoverRunner(verbosity=1, interactive=False, keepdb=False)
_old_db_config = _db_runner.setup_databases(aliases={"default"})  # see Bug 2a
try:
    exit_code = _pytest.main(forwarded, plugins=[plugin])
finally:
    try:
        _db_runner.teardown_databases(_old_db_config)
    finally:
        teardown_test_environment()
```

This is the load-bearing piece for every `TestCase`-derived test in `test_project/`. If you ever bypass it (e.g. a future "skip DB setup for pure-unit clusters" flag), every `TransactionTestCase`-derived test will silently break at `setUp`.

### Bug 1b — `_PYTEST_SUMMARY_RE` was order-dependent

#### Root cause

[`.github/scripts/run_showcase_suite.py`](../../.github/scripts/run_showcase_suite.py) parsed pytest's final summary line with a single regex that hard-coded the order of `passed`, `failed`, `errors`, `warnings`, `skipped`. Pytest, however, emits whichever categories actually occurred, in its own order. The CI failure printed:

```
==== 1 warning, 37 errors in 4.21s ====
```

`warning` came *before* `errors`, the regex didn't accept that order, the match returned `None`, the parser defaulted to `errors=0 / outcome=success`, and the manifest aggregator agreed.

#### Fix

`_parse_summary` is now a two-step parse:

1. Match the boxed summary line itself (`==== … in Xs ====`) with a permissive regex.
2. Tokenise the comma-separated body into `<N> <word>` pairs and fold each pair into the right bucket by keyword (`passed`, `failed`, `error`/`errors`, `warning`/`warnings`, `skipped`, `xfailed`, `xpassed`, `deselected`).

Order-agnostic by construction. Verified against five pytest output shapes including the exact CI failure line.

> **The two halves of this commit reinforce each other.** The DB fix is what makes the test DB exist; the parser fix is what makes a future "warning-before-error" line visible if anything else regresses. Either one alone would have left the system half-blind.

---

## Second hotfix — `60f423b`

After `af2a6ea` landed, the run still came back green — and still wrong. CI now reported `0 passed, 0 failed, 0 errors, 0 skipped` per cluster with a `✓ all green — 4/4 clusters passing` banner, because pytest was still aborting before a single test ran, and the orchestrator was still treating "no summary" as success.

### Bug 2a — `setup_databases()` tried every `DATABASES` alias

#### Symptom

Every cluster aborted with:

```
psycopg2.OperationalError: could not translate host name "envvar_not_existing"
to address: Temporary failure in name resolution
```

…during `Creating test database for alias 'GCP'...`. No tests ran.

#### Root cause

[`lex/lex_app/settings.py:325-362`](../../lex/lex_app/settings.py) declares four `DATABASES` aliases — `default`, `GCP`, `DOCKER-COMPOSE`, `K8S`. The three non-default aliases pull host/credentials from env vars and fall back to the literal sentinel `"envvar_not_existing"` when those aren't set (which is the CI case).

Django's own `manage.py test` path avoids this because [`DiscoverRunner.run_tests`](https://github.com/django/django/blob/main/django/test/runner.py) inspects every collected `TestCase.databases` (defaults to `{"default"}`) and passes that set into `setup_databases(aliases=…)`. We bypassed that discovery — **pytest** owns collection now, not the Django runner — so the `setup_databases()` call we added in Bug 1a, with no `aliases` argument, defaulted to "all aliases" and tried to `CREATE DATABASE test_<name>` on every entry, blowing up on the GCP host lookup.

#### Fix

[`lex/bin/lex.py`](../../lex/bin/lex.py) now passes `aliases={"default"}` explicitly:

```python
_old_db_config = _db_runner.setup_databases(aliases={"default"})
```

If a future test needs a second alias, mirror Django's pattern: read the suite's collected `TestCase.databases` (or hard-code the set) and pass it in. Do **not** drop the `aliases=` argument — that's the trip wire that caused this bug.

### Bug 2b — Showcase reported "✓ all green" when zero tests ran

#### Symptom

```
[Manual] [✓ all green — 4/4 clusters passing]
Excellence Cloud — Platform Health Report, 27 May 2026
```

…while every cluster row showed `0 passed, 0 failed, 0 errors, 0 skipped`. The platform-health email said the release was good. It wasn't — every cluster had crashed during DB bootstrap (Bug 2a).

#### Root cause

Two-layer failure in [`run_showcase_suite.py`](../../.github/scripts/run_showcase_suite.py):

1. **`_parse_summary`** returned `outcome="success"` whenever pytest emitted no summary line, because the success check was `failed == 0 and errors == 0` — and both are zero when pytest never ran.
2. **`_run_cluster`** never inspected `proc.returncode`. The subprocess could exit non-zero from a Django traceback before pytest even started collecting, and the orchestrator wouldn't notice.

Combine the two and a hard bootstrap crash looked indistinguishable from a clean test run. Note this is **distinct** from Bug 1b: that one was a regex that *had* a match but folded the categories wrong; this one is the entire summary line being absent because pytest never reached the print stage.

#### Fix

`_parse_summary` now exposes a `summary_found` flag (set to `True` only when the trailing `==== N passed in Xs ====` line was actually present). `_run_cluster` captures `proc.returncode` and applies two new guard clauses:

```python
if not parsed.pop("summary_found", False) and returncode != 0:
    # pytest never printed a summary AND the subprocess died.
    # Synthesize errors=1 + outcome="failure" + setup_error message.
    ...
elif returncode != 0 and parsed["outcome"] == "success":
    # Summary said all good, but the process still died. Defensive.
    ...
```

When either path fires, the cluster entry in the manifest gets a `setup_error` field that the platform-health report surfaces — instead of the row reading `0 errors, ✓`.

---

## Third hotfix — `register_converter` collision

After the first two hotfixes landed and CI was reporting honest counts again, the very next showcase run produced a fresh failure that the orchestrator now (correctly) flagged as a cluster failure:

```
ValueError: Converter 'model' is already registered.
  …
  lex/lex_app/urls.py:38: in <module>
      path(process_admin_route, processAdminSite.urls),
  lex/process_admin/sites/process_admin_site.py:160: in _get_urls
      register_converter(
          create_model_converter(self.model_collection), "model"
      )
```

The failing test was `lex/test_project/tests/init/test_1p_settings_urls_views.py::TestCluster01p_UrlConfResolves::test_1_131_health_view_reverses` — a plain `TestCase` whose body is just `reverse("health_view")`.

### Why `lex test` never saw it

`lex test --verbosity=2 --noinput lex.test_project.tests.crud_api` ran **one cluster per invocation**. Inside that one cluster, every test was an `E2ETestCase` subclass, and [`lex/tests/e2e/_e2e_test_case.py:_rebuild_urls`](../../lex/tests/e2e/_e2e_test_case.py) already installs a local monkey-patch that makes `register_converter` idempotent for the duration of its own `setUp` (it pops the existing `"model"` entry from `REGISTERED_CONVERTERS` before re-registering, then reloads `lex_app.urls`). Three other legacy test files do the same dance (`lex/tests/integration/test_api_user_journey.py`, `lex/tests/integration/test_bitemporal.py`, `lex/tests/unit/infra/test_user_model_registration.py`) — the workaround is older than the pytest cutover.

### Why pytest surfaced it

`lex pytest` runs every cluster in one process. As soon as an `E2ETestCase` test runs anywhere in the session, its `_rebuild_urls()` calls `reload(sys.modules["lex_app.urls"])`, which re-executes the top of `lex_app/urls.py` (`path(process_admin_route, processAdminSite.urls)`) — which re-enters `_get_urls()` and re-registers `"model"`. The local patch covers that reload, then exits. Any **plain `TestCase`** that later calls `reverse()` and triggers a fresh `lex_app.urls` import (for example after a `clear_url_caches()` from a prior `_rebuild_urls()`) hits the unpatched `register_converter` and aborts. With the showcase suite collecting `init/`, `crud_api/`, `permissions/`, `history/`, … in the same process, the cross-cluster interaction surfaces every run.

### Fix

Install the same idempotent `register_converter` patch **at the test-runner level**, once, around `pytest.main(...)` in [`lex/bin/lex.py`](../../lex/bin/lex.py). This covers every test class — `E2ETestCase` subclasses and plain `TestCase`s alike — without touching production framework code in `lex/process_admin/sites/process_admin_site.py`:

```python
from django.urls import converters as _django_converters
from django.urls.converters import REGISTERED_CONVERTERS as _REGISTERED_CONVERTERS
from unittest.mock import patch as _patch

_real_register_converter = _django_converters.register_converter

def _idempotent_register_converter(converter, type_name):
    _REGISTERED_CONVERTERS.pop(type_name, None)
    return _real_register_converter(converter, type_name)

_converter_patch = _patch(
    "lex.process_admin.sites.process_admin_site.register_converter",
    new=_idempotent_register_converter,
)
_converter_patch.start()
try:
    exit_code = _pytest.main(forwarded, plugins=[plugin])
finally:
    _converter_patch.stop()
    # …existing DB / env teardown…
```

The patch is scoped to the patch target (`lex.process_admin.sites.process_admin_site.register_converter`), not Django's global, so production runtime behaviour is unchanged — outside `lex pytest`, `register_converter` is still the strict Django original. The framework's `_get_urls()` stays exactly as written.

### Why not fix `_get_urls()` instead

Tempting, but wrong scope. Production framework code should not have its API surface widened (to "idempotent registration") just to accommodate a test-runner-specific call pattern. The dual-import / reload pattern only exists in tests — the legacy test infrastructure already encodes that, four times over, with the same monkey-patch we're now hoisting one level up. Hoisting it to the runner removes four duplicated patches without changing what the framework promises to do at runtime.

### Follow-up worth doing later

The four legacy `_rebuild_urls`-style patches in `lex/tests/e2e/_e2e_test_case.py`, `lex/tests/integration/test_api_user_journey.py`, `lex/tests/integration/test_bitemporal.py`, `lex/tests/unit/infra/test_user_model_registration.py` are now redundant under `lex pytest` (the runner-level patch covers them). They are kept for now because (a) those test trees are out-of-scope for `lex pytest` and may still run via `lex test` in isolation, (b) deleting them is a behaviour-changing refactor that belongs in its own PR. If/when those test trees are retired, the duplicates can go.

---

## Fourth hotfix — summary-line regex missed long runs and `subtests`

After the third hotfix landed and CI was running every cluster cleanly, the orchestrator started showing a new, narrower inconsistency: one cluster's row read `0 passed, 0 failed, 0 errors, 0 skipped` even though pytest's own output ended with a healthy `151 passed`:

```
======== 151 passed, 1 warning, 19 subtests passed in 99.26s (0:01:39) =========
  ✓ calculations: 0 passed, 0 failed, 0 errors, 0 skipped, 0 xfailed — 119.18s
```

The other three clusters reported correct counts. The only thing different about the `calculations` cluster was wall time — it crossed ~60s.

### Root cause

Two separate gaps in [`run_showcase_suite.py`](../../.github/scripts/run_showcase_suite.py)'s summary parser, both surfaced by the same line:

1. **`_PYTEST_SUMMARY_LINE_RE` didn't allow pytest's HMS suffix.** When the run exceeds ~60s, pytest appends ` (H:MM:SS)` between `in <N>s` and the trailing `=`s. The old regex required `<duration>s\s+=+`, so the line failed to match, `summary_found` stayed `False`, and every count defaulted to `0`. With the third hotfix in place the cluster wasn't crashing anymore, so this surfaced as silent under-counting instead of an outright "all zero, ✓".
2. **`subtests` wasn't in `_PYTEST_CATEGORY_TO_BUCKET`.** Even with a relaxed line regex, `19 subtests passed` would tokenise as `count=19 word=subtests`. An unknown word would silently fall through, which is what we want here (subtests are nested under their parent test methods already counted in `passed` — double-counting would be wrong), but the token should be in the recognised-but-ignored list alongside `warning` and `deselected` so it stays signal, not future noise.

### Fix

Two narrowly scoped edits in `run_showcase_suite.py`:

```python
_PYTEST_SUMMARY_LINE_RE = re.compile(
    r"^=+\s+(?P<body>.+?)\s+in\s+(?P<duration>[\d.]+)s"
    r"(?:\s+\([^)]+\))?"     # optional " (0:01:39)" HMS suffix on long runs
    r"\s+=+\s*$",
    re.MULTILINE,
)

_PYTEST_CATEGORY_TO_BUCKET = {
    # …existing entries…
    "subtest":    None,   # pytest-subtests "N subtests passed" — already
    "subtests":   None,   # counted under the parent test in "passed"
}
```

### Verification

Five real shapes confirmed against the fixed parser:

| Input | `passed` | `errors` | `wall_s` | `outcome` | `summary_found` |
|-------|---------:|---------:|---------:|-----------|:----------------|
| `151 passed, 1 warning, 19 subtests passed in 99.26s (0:01:39)` (the failing case) | 151 | 0 | 99.26 | success | True |
| `1 warning, 37 errors in 4.21s` (af2a6ea regression case) | 0 | 37 | 4.21 | failure | True |
| `25 passed in 3.10s` | 25 | 0 | 3.10 | success | True |
| `10 passed, 2 failed, 1 skipped in 45.50s` | 10 | 0 | 45.50 | failure | True |
| `200 passed, 5 skipped, 3 warnings in 125.00s (0:02:05)` | 200 | 0 | 125.00 | success | True |
| (no summary) | 0 | 0 | 0.0 | success | False (handled by the Bug 2b guards) |

### Why this kept hiding behind the other hotfixes

It couldn't show up until the previous three were in place. Before the second hotfix, every cluster crashed in bootstrap and there was no summary line to parse. Before the third hotfix, the cross-cluster `register_converter` collision aborted clusters before they finished. Once those were fixed and the suite started actually completing long-running clusters, the missing HMS branch in the regex became the next visible thing.

---



The two structural bugs (1a and 2a) are sequential — fixing 1a (call `setup_databases`) is what exposed 2a (the alias scoping problem). The two observability bugs (1b and 2b) are the reason each structural bug shipped silently. If either parser/orchestrator gap hadn't been there, the very first CI run after the pytest cutover would have flagged the structural bug visibly. Instead we shipped a "green" pipeline twice.

The general rule the cutover exposed: **any time you replace a runner that does its own bookkeeping (collection, DB setup, summary printing), audit the orchestrator that consumes the new runner's output before you trust the pipeline.** Pytest is happy to exit with no summary line; the orchestrator has to treat that as a failure, not a default.

---

## What is and isn't in scope for `lex pytest`

Worth restating because both hotfixes touched it:

- **In scope:** everything under [`lex/test_project/tests/`](../../lex/test_project/tests/). That's the `tests_entrypoint` in [`lex_test_config.yaml`](../../lex_test_config.yaml). Each cluster folder name doubles as a pytest group; the canonical group list lives in the same YAML and is enforced by [`lex/tools/test_groups.py`](../../lex/tools/test_groups.py) at collection time (any `pytest.mark.<group>` whose name isn't in the YAML raises `LexTestConfigError`).
- **Not in scope:** anything else in the framework tree — historical `lex/core/tests/`, `lex/audit_logging/tests/`, `lex/process_admin/tests/`, `lex/lex_app/tests/`, `lex/tests/` shims. Those still exist for backward-compat imports and local exploration but are deliberately not collected by `lex pytest`. They are not part of the showcase suite and are not gated.

If a future PR widens `tests_entrypoint`, the DB-alias decision in Bug 2a needs revisiting — any test that declares `databases = {"GCP", …}` will silently be skipped because the alias set is hard-coded to `{"default"}`.

---

## Verification

After both hotfix commits land in PR #514:

- A successful run shows `✓` per cluster with real, non-zero `passed` / (intentional) `skipped` / `xfailed` counts.
- A run where DB bootstrap fails shows `✗` per cluster with `setup_error` populated, and the platform-health email reports the failure correctly.
- A run where pytest finishes but with a non-success summary (`1 warning, 37 errors`) parses correctly regardless of category order.
- The unit-level smoke tests for `_parse_summary` (warning-first, pure-pass, no-summary, mixed-warning-after-error cases) live inline in the commit messages of `af2a6ea` and `60f423b`; if you change the parser, re-run those snippets.

---

## Related work

- **First hotfix (`af2a6ea`)** — added `DiscoverRunner.setup_databases()` / `teardown_databases()` around `pytest.main()` (fixed the `db_<repo-name>` connection error) and rewrote the parser to be category-order-agnostic.
- **Second hotfix (`60f423b`)** — scoped DB setup to the `default` alias and made the orchestrator surface bootstrap failures.
- **Third hotfix (`register_converter` collision)** — hoisted the legacy per-test idempotent `register_converter` monkey-patch into the `lex pytest` runner itself, so plain `TestCase` subclasses survive cross-cluster `reverse()` calls in the same pytest process.
- **Fourth hotfix (summary regex + `subtests`)** — allowed the optional ` (H:MM:SS)` suffix that pytest appends past ~60s and added `subtests` to the recognised-but-ignored token map, so long-running clusters report real counts instead of `0 passed`.
- **Pytest migration design** — [`docs/superpowers/specs/2026-05-27-test-project-pytest-migration-design.md`](../superpowers/specs/2026-05-27-test-project-pytest-migration-design.md).
- **Pytest migration plan** — [`docs/superpowers/plans/2026-05-27-test-project-pytest-migration.md`](../superpowers/plans/2026-05-27-test-project-pytest-migration.md).
- **Cluster catalogue + golden rule** — [`lex/test_project/test-plan/index.md`](../../lex/test_project/test-plan/index.md), [`lex/test_project/test-plan/test-clusters.md`](../../lex/test_project/test-plan/test-clusters.md).






