---
description: "Use when: writing, modifying, or generating tests for the lex-app framework. Activates for any file under lex/tests/, lex/test_project/tests/, or when the user asks Copilot to write a test."
applyTo: "lex/tests/**,lex/test_project/tests/**,**/test_*.py,**/tests/**/*.py,frontend/src/__test__/**"
---

# LEX Testing Rules — Read Before Writing Tests

The lex-app test suite is **release-gating**: nothing ships to PyPI unless the full backend run passes and coverage stays above the configured threshold. Tests double as living documentation. These rules exist so generated tests pass review, satisfy the coverage gate, and don't get bounced back for style fixes.

When these rules conflict with prior knowledge, **these rules win**. They reflect decisions documented in [`docs/testing-methodology.md`](../../docs/testing-methodology.md) and the active migration plan in [`lex/test_project/test-plan/`](../../lex/test_project/test-plan/).

---

## 1. Where tests live

| Layer | Path | Use for |
| --- | --- | --- |
| Framework unit | `lex/tests/unit/<topic>/test_*.py` | Pure logic, single-class behaviour, no DB if possible. Topic subdirs: `api/`, `audit/`, `auth/`, `calculation/`, `cli/`, `core/`, `crud/`, `grid/`, `infra/`, `serialization/`, `temporal/`. |
| Framework integration | `lex/tests/integration/test_*.py` | Multi-component flows, bitemporal chains, audit recovery. |
| Framework E2E | `lex/tests/e2e/test_*.py` | Full user journeys through REST `APIClient`. |
| Project-cluster tests | `lex/test_project/tests/<topic>/test_<cluster>.py` | Cluster-based test plan (see §5). |
| Frontend | `frontend/src/__test__/*.test.{ts,tsx}` | Vitest, jsdom env. |

**Do not invent new top-level directories.** If unsure where a new test belongs, place it under `lex/tests/unit/<existing-topic>/` and ask the reviewer.

## 2. Test runner

- **Backend:** pytest (cutover in progress — see [`docs/ci-cd/pytest-cutover-hotfixes-2026-05-27.md`](../../docs/ci-cd/pytest-cutover-hotfixes-2026-05-27.md)). Run via `lex test <labels>` or `pytest <path>`. **Do not** use `manage.py test`.
- **Frontend:** Vitest with `globals: true`. Use `vi.mock`/`vi.fn` in new code; `jest.mock` is aliased for legacy compat but not for new tests.

## 3. Base-class selection

| Need | Class |
| --- | --- |
| Pure functions, no DB | `SimpleTestCase` (fastest — use whenever possible) |
| ORM, per-test transaction rollback | `TestCase` |
| Schema editing, multi-transaction behaviour, raw connection use | `TransactionTestCase` (slowest — only when truly needed) |
| REST endpoint testing | `APITestCase` (DRF) |

Pytest tests can use plain `def test_...` functions with fixtures — prefer this for new pure-logic tests under `lex/tests/unit/`.

## 4. The coverage-pairing rule (critical)

The coverage gate ([`copilot_coverage_check.yml`](../workflows/copilot_coverage_check.yml)) blocks PRs that touch `lex/lex_app/**` source without a paired test in the **same diff**. A test is "paired" if it satisfies at least one of:

1. **Imports the changed module** — e.g. `from lex.lex_app.fast_health import match_health_request_path`.
2. **Has the source filename stem in its name** — e.g. changing `fast_health.py` → pair with `test_fast_health.py`.

When you write a test for new code, ensure **at least one** of those is true. The detector is `.github/scripts/copilot_coverage_detect.py` — read it if you need to verify the exact logic.

## 5. The test-plan workflow (cluster-aligned tests)

Tests under `lex/test_project/tests/` follow the **cluster plan** in [`lex/test_project/test-plan/test-writing-plan.md`](../../lex/test_project/test-plan/test-writing-plan.md). This plan is the source of truth — **read it before writing any test under `lex/test_project/tests/`**.

### Allocation rules (follow exactly — never improvise)

1. **Cluster numbers are never renumbered.** They map 1:1 to coverage tracking and reports.
2. **Sub-clusters use letters in alphabetical order.** Find the highest letter currently in use for the cluster; allocate the next free one. Example: cluster 1 has 1a–1n → next is `1o`. Cluster 6 has 6a–6f → next is `6g`.
3. **Scenario IDs continue from the cluster's current max.** Don't restart numbering or reuse IDs.
4. **One batch = one sub-cluster = one PR.** Keeps reviews bounded.
5. **Don't re-slot files already in an in-flight Tier-A cluster.** Check the "in-flight" section of the test-writing-plan before allocating.

### File naming

- Test file: `lex/test_project/tests/<topic>/test_<cluster><letter>_<short_description>.py`
- Test class: `TestCluster<NN><letter>_<Description>` — e.g. `TestCluster01o_ProcessAdminLazyGetattr`
- Type letter on the batch metadata: **U** (`SimpleTestCase`, no DB), **I** (`TestCase`, per-test transaction), **E** (REST via `APIClient`).

### Required batch metadata

Every batch in the test-plan documents: scenario range, files covered, test classes, fixtures, file path, est. tests, est. coverage gain, prerequisite PRs. When adding a new batch, append a row that matches this shape — copy the format of the most recent batch.

### Slash command

For test-plan-aligned work, invoke `/write-cluster-test` in Copilot Chat. The prompt walks through the lookup-and-allocate workflow step by step and asks you to confirm the cluster/letter before scaffolding.

## 6. Naming conventions (everywhere else)

- **Test files:** `test_<module_stem>.py` (default outside the cluster plan).
- **Test methods:** `test_<behaviour_under_test>` — verb-led, describes the assertion, not the setup.

## 7. What NOT to do

These are linter or review failures every time:

- **No `print()`** for debugging — use `self.subTest()` or assertions with descriptive messages.
- **No bare `except:` or `except: pass`** — always name the exception: `except SpecificError:`.
- **No reliance on `.env`** — tests that depend on environment variables must `patch.dict("os.environ", {...})` explicitly. Specifically `CELERY_ACTIVE` — never assume it's set.
- **No DB mocks for `TestCase`-style work** — integration tests must hit a real Postgres. Mocking the ORM masked a broken migration once; don't repeat it.
- **No silent `self.skipTest()` on a 404 or unexpected status** — that hides regressions. Assert, don't skip.
- **No duplicate assertions** — if a fact is asserted in `setUp`, don't re-assert it in every test method.
- **No new docstring-only files.** Tests without assertions are dead weight.

## 8. Docstrings (mandatory)

Every new test file and every `TestCase` subclass needs a docstring. They are read by reviewers and by future contributors who treat the test suite as documentation. Minimum:

```python
"""Tests for <module>.

Covers: <one-line behaviour summary>.
Run: lex test lex.tests.unit.<topic>.test_<module> --verbosity=2 --noinput
"""
```

## 9. If this PR is a coverage-task PR (auto-opened by the gate)

Coverage-task issues are tagged `coverage-task` and are auto-opened by [`copilot_coverage_check.yml`](../workflows/copilot_coverage_check.yml). When writing a PR to close one:

1. **Base branch:** target the **parent PR's head branch** (named in the issue body under "Required base branch"). **Never** target `lex-app-v2` directly — that breaks the pairing rule and the parent PR stays blocked.
2. **Body footer:** end the PR body with `Fixes #<issue-number>` so the gate can link the test PR back to the coverage task.
3. **Scope:** add only the missing tests. Do not refactor the source, rename files, or fix unrelated issues — that gets the PR labelled `copilot:invalid` by the gate's shape check.

## 10. Reference docs (read these for full detail)

- [`docs/testing-methodology.md`](../../docs/testing-methodology.md) — full methodology, base-class guide, coverage strategy, CI integration.
- [`lex/test_project/test-plan/`](../../lex/test_project/test-plan/) — active cluster plan, naming rules, scenario IDs. **This is the source of truth for cluster allocation.**
- [`CLAUDE.md`](../../CLAUDE.md) §15 ("Test Framework — How It Works") — runner setup, isolation rules, local-vs-CI differences.
- [`docs/superpowers/specs/2026-05-26-copilot-test-automation-pipeline-design.md`](../../docs/superpowers/specs/2026-05-26-copilot-test-automation-pipeline-design.md) — coverage-task automation spec.

## 11. When in doubt

Read the docs above before writing. Don't guess at framework APIs, base-class semantics, or fixture conventions — the docs are authoritative and the tests already in `lex/tests/unit/` and `lex/test_project/tests/` are the worked examples.
