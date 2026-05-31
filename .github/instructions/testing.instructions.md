---
description: "Use when: writing or modifying tests for lex-app, OR changing framework source under lex/ (which requires paired tests in the same change). Activates for framework source and all test files."
applyTo: "lex/lex_app/**,lex/core/**,lex/api/**,lex/audit_logging/**,lex/process_admin/**,lex/utilities/**,lex/tests/**,lex/test_project/tests/**,**/test_*.py,**/tests/**/*.py,frontend/src/__test__/**"
---

# LEX Testing Rules — Read Before Writing Tests (or Changing Framework Source)

The lex-app test suite is **release-gating**: nothing ships to PyPI unless the full backend run passes and coverage stays above the configured threshold. Tests double as living documentation. These rules exist so generated tests pass review, satisfy the coverage gate, and don't get bounced back for style fixes.

**Why this file loads when you edit framework source (not just test files):** any change under `lex/` needs a paired test in the *same* change — the CI coverage gate enforces it (see §4). So the moment you touch framework code, the testing rules are already in context: write the test alongside the code, automatically, the same way the cloud Copilot agent does. You do not need to be asked.

When these rules conflict with prior knowledge, **these rules win**. They reflect decisions documented in [`docs/testing-methodology.md`](../../docs/testing-methodology.md) and the active plan in [`lex/test_project/test-plan/`](../../lex/test_project/test-plan/).

---

## 0. Before you write code or tests — research first (the Golden Rule)

**Do not write code or tests from your first instinct, and never derive a test by mirroring the implementation.** The source code is an incomplete story — it has bugs and workarounds; a test that asserts "whatever the code does" locks those bugs in. This is the single most common failure mode here. Before writing anything:

1. **Read the docs that describe intent** — [`docs/features/`](../../docs/features/), [`docs/reference/`](../../docs/reference/), [`docs/tutorial/`](../../docs/tutorial/). They say what the framework is *trying to achieve*. Derive the test from that, then *"What would a customer reasonably expect?"* (full statement: the Golden Rule in [`test-clusters.md`](../../lex/test_project/test-plan/test-clusters.md#testing-philosophy)). These published-doc paths are a **read-only mirror** of `lex-app-docs`, kept fresh by the inbound docs mirror sync ([`docs/ci-cd/docs-sync-mirror.md`](../../docs/ci-cd/docs-sync-mirror.md)) — trust them as current intent, but don't hand-edit them here; fix docs upstream in `lex-app-docs`.
2. **Read the public API docstrings** of what you'll touch, and skim the existing cluster tests for that topic to match established patterns.
3. **Check for an existing mechanism before inventing one.** Before adding a new store, cache, registry, or helper, search the codebase for one that already does the job — a second, parallel home for state that already has one is a common source of subtle divergence bugs. Reuse the established mechanism rather than reinventing it.
4. **If the request is ambiguous or has more than one defensible design, STOP and ask the developer** before coding — surface the trade-offs. A wrong assumption baked into a feature + its tests costs far more than one question. (Claude Code: use the `superpowers:brainstorming` skill.)

If a test fails because the code is genuinely buggy, that is the test doing its job → §6.

## 1. Where tests live

**Feature/bugfix work on framework source goes into the cluster system** — this is the active, plan-tracked, release-gating suite:

| Layer | Path | Use for |
| --- | --- | --- |
| **Cluster tests (default for feature work)** | `lex/test_project/tests/<cluster_slug>/test_<NN><letter>_<short>.py` | Any test paired with a framework source change. Plan-tracked (see §5, §6). |
| Frontend | `frontend/src/__test__/*.test.{ts,tsx}` | Vitest, jsdom env. |
| Legacy framework audit | `lex/tests/unit/<topic>/`, `lex/tests/integration/`, `lex/tests/e2e/` | **Pre-existing** suite. **Do not add new feature tests here** — it bypasses the cluster plan and the coverage-task tracking. Only touch when extending an existing file in it. |

**Do not invent new top-level directories**, and **do not** drop a feature test into `lex/tests/unit/` to avoid cluster allocation — that is exactly the escape hatch that breaks plan consistency.

## 2. Test runner

- **Backend:** pytest (cutover in progress — see [`docs/ci-cd/pytest-cutover-hotfixes-2026-05-27.md`](../../docs/ci-cd/pytest-cutover-hotfixes-2026-05-27.md)). Run via `python -m lex pytest <path>` or `lex test <labels>`. **Do not** use `manage.py test`.
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

**This is the local mirror of the cloud paradigm:** the cloud coverage gate opens a `coverage-task` issue and assigns Copilot to write the missing test against the parent PR's head branch. Locally, you pre-empt that by writing the paired test in the same change — so the gate stays green and no follow-up task is needed.

## 5. Cluster test-plan — strict naming & allocation

Cluster tests follow the plan in [`lex/test_project/test-plan/`](../../lex/test_project/test-plan/) and the conventions in [`progress/conventions.md`](../../lex/test_project/test-plan/progress/conventions.md). These rules are **strict — follow them exactly, don't improvise**:

1. **First, enumerate the behaviour surfaces your change creates or changes.** Before picking any cluster, list *every externally-observable behaviour* the change introduces — don't reason about "the feature" as one thing. Each of these is a distinct surface that needs its own scenario:
   - every new public entry point a caller can invoke (instance method, classmethod, REST endpoint, management command, anything a customer or the framework dispatches),
   - every state transition or status change the change can produce,
   - every persisted or emitted side-effect (a written audit/history row, a signal or broadcast, a row that lands in a different terminal state),
   - every error/edge path (not-found, no-op, permission-denied, already-finished, failure-during-X).

   **A surface is only "covered" when a test drives it the way a real caller reaches it — end-to-end through the public entry point, not by exercising the private helper or in-memory data structure underneath it.** Testing the easy internal layer while leaving the public entry points and their integration paths unexercised is the single most common way a change *looks* tested but isn't. If your scenarios only touch the data structure and the no-op/error returns, you have not tested the feature — go back and add the happy-path and integration surfaces.

2. **Map each surface to its owning cluster — this is often more than one cluster.** A single change frequently spans clusters: a behaviour that adds, say, a state transition *and* a new REST endpoint *and* a new persisted side-effect has surfaces in three different domains. Map each surface to its cluster via [`test-clusters.md`](../../lex/test_project/test-plan/test-clusters.md) by **what the surface is**, not where the source line lives — a REST surface belongs to the API-layer cluster even if the code lives on a model; a persisted side-effect belongs to the cluster that owns that record type. Put each surface in the cluster that owns its domain; do **not** bundle an out-of-domain surface into whichever cluster is most convenient. Surfaces in different clusters become separate batches/PRs (see step 8). If you can't confidently place a surface, that's a §0.4 *stop-and-ask*. And if a surface genuinely cannot be exercised in the test environment, that is a decision to surface explicitly — gate it with a documented reason (or ask), **never** silently omit it and still call the change tested.
3. **Allocate from `test-writing-plan.md` — never guess.** Cluster numbers are **never renumbered**. Take the **next free letter** for that cluster (read the plan to find the highest in use), and **continue scenario IDs from the cluster's current max**. If the source file is already slotted in an in-flight batch, defer to it — don't duplicate.
4. **File:** `lex/test_project/tests/<cluster_slug>/test_<NN><letter>_<short>.py` (e.g. `test_7m_cancellation.py`).
5. **Module header:** an `Intent` docstring section (what the framework is *trying to achieve* + why a regression matters + the scenario range), plus module-level `pytestmark = pytest.mark.<cluster_slug>`. See [`test_7k_exceptions_restrictions_xlsx.py`](../../lex/test_project/tests/calculations/test_7k_exceptions_restrictions_xlsx.py) as the gold standard.
6. **Class:** `TestCluster<NN><letter>_<Description>` (e.g. `TestCluster07m_Cancellation`). For E-type, inherit `E2ETestCase` and declare `e2e_models`.
7. **Methods:** `test_<NN>_<NN>_<behaviour>` with a docstring `Scenario X.Y: <one-line> / Given: … / When: … / Then: …`. Every assertion carries a human-readable failure message.
8. **One batch = one sub-cluster = one PR.** When a change's surfaces span multiple clusters (step 2), each cluster gets its own batch — don't collapse cross-domain surfaces into a single batch to save a PR.

Claude Code users: the [`lex-testing` skill](../../.claude/skills/lex-testing/SKILL.md) walks this allocation automatically.

## 6. Definition of Done — the task is not finished until the plan is consistent

Writing the test is only half the job. **A feature/test task is NOT done until the test-plan on disk matches the tests you wrote.** This is the **same checklist the cloud Copilot agent satisfies on every PR** (see `.github/scripts/copilot_assemble_prompt.py` → "Required deliverables") — local work must match it so the two paths stay consistent. Do all of this in the **same change**:

1. **Append a row to [`session-log.md`](../../lex/test_project/test-plan/progress/session-log.md)** — this is the *universal* per-PR record (its header: "the Copilot test-bot writes here as part of every PR"). Append-only: new row at the bottom, never re-order. Columns: date, session, what was done, clusters affected, tests added, tests passing.

2. **Update the cluster's status / scenario range** in [`test-clusters.md`](../../lex/test_project/test-plan/test-clusters.md) for **each** (sub-)cluster you touched, and bump the matching row in [`dashboard.md`](../../lex/test_project/test-plan/progress/dashboard.md) (the high-churn per-cluster status view).

3. **If your work maps to a planned batch, append/update the batch row** in [`test-writing-plan.md`](../../lex/test_project/test-plan/test-writing-plan.md), under the right cluster, matching the table shape of the most recent batch (scenario range, type U/I/E, files covered, test file path, test classes, fixtures, status). Ad-hoc work that isn't a planned COMPLETE-bucket batch still needs steps 1–2; this step is for batches the plan already forecasts.

4. **Run the tests** (`python -m lex pytest <path>`) and record the **real** results (`N pass / 0 fail`, measured coverage gain) in the rows above — flip *Status* to ✅ Complete. Don't leave `pending` placeholders in a finished change.

5. **If a test surfaces broken framework behaviour, record the bug — don't weaken the test.** Per the workflow in [`known-bugs.md`](../../lex/test_project/test-plan/known-bugs.md): write the test asserting the *correct* behaviour (from docs/intent), mark it `@unittest.expectedFailure` (or `@pytest.mark.xfail(strict=True)`), and add a `BUG-NNN` row (description, severity, cluster, test, status). When the framework is later fixed, the marker is dropped and the test passes naturally — a permanent regression gate. **Never** soften an assertion to make a real bug "pass".

If the plan and the tests on disk disagree, you are not done.

## 7. What NOT to do

These are linter or review failures every time:

- **No `print()`** for debugging — use `self.subTest()` or assertions with descriptive messages.
- **No bare `except:` or `except: pass`** — always name the exception: `except SpecificError:`.
- **No reliance on `.env`** — tests depending on env vars must `patch.dict("os.environ", {...})` explicitly. Specifically `CELERY_ACTIVE` — never assume it's set.
- **No DB mocks for `TestCase`-style work** — integration tests must hit a real Postgres. Mocking the ORM masked a broken migration once; don't repeat it.
- **No `_make_model_stub`** — use real DB models. Mock only true external boundaries (Keycloak HTTP, Celery broker, channel layer, S3, SharePoint).
- **No silent `self.skipTest()` on a 404 or unexpected status** — that hides regressions. Assert, don't skip.
- **No duplicate assertions** — if a fact is asserted in `setUp`, don't re-assert it in every test method.
- **No new docstring-only files.** Tests without assertions are dead weight.

## 8. Docstrings (mandatory)

Every new test file and every `TestCase` subclass needs a docstring — reviewers and future contributors read the suite as documentation. Minimum:

```python
"""Tests for <module>.

Covers: <one-line behaviour summary>.
Run: python -m lex pytest lex/tests/unit/<topic>/test_<module>.py -v
"""
```

## 9. If this PR is a coverage-task PR (auto-opened by the gate)

Coverage-task issues are tagged `coverage-task` and auto-opened by [`copilot_coverage_check.yml`](../workflows/copilot_coverage_check.yml). When writing a PR to close one:

1. **Base branch:** target the **parent PR's head branch** (named in the issue body under "Required base branch"). **Never** target `lex-app-v2` directly — that breaks the pairing rule and the parent PR stays blocked.
2. **Body footer:** end the PR body with `Fixes #<issue-number>` so the gate can link the test PR back to the coverage task.
3. **Scope:** add only the missing tests. Do not refactor the source, rename files, or fix unrelated issues — that gets the PR labelled `copilot:invalid` by the gate's shape check.

## 10. Reference docs (read these for full detail)

- [`docs/testing-methodology.md`](../../docs/testing-methodology.md) — full methodology, base-class guide, coverage strategy, CI integration.
- [`lex/test_project/test-plan/`](../../lex/test_project/test-plan/) — active cluster plan, naming rules, scenario IDs, [`known-bugs.md`](../../lex/test_project/test-plan/known-bugs.md).
- [`CLAUDE.md`](../../CLAUDE.md) §15 ("Test Framework — How It Works") — runner setup, isolation rules, local-vs-CI differences.

## 11. When in doubt

Read the docs above before writing. Don't guess at framework APIs, base-class semantics, or fixture conventions — the docs are authoritative and the tests already in `lex/tests/unit/` are the worked examples.
