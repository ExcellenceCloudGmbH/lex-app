---
description: "Use when: writing or modifying tests for lex-app, OR changing framework source under lex/ (which requires paired tests in the same change). Activates for framework source and all test files."
applyTo: "lex/lex_app/**,lex/core/**,lex/api/**,lex/audit_logging/**,lex/process_admin/**,lex/utilities/**,lex/tests/**,lex/test_project/tests/**,**/test_*.py,**/tests/**/*.py,frontend/src/__test__/**"
---

# LEX Testing Rules — Read Before Writing Tests (or Changing Framework Source)

The lex-app test suite is **release-gating**: nothing ships to PyPI unless the full backend run passes and coverage stays above the configured threshold. Tests double as living documentation. These rules exist so generated tests pass review, satisfy the coverage gate, and don't get bounced back for style fixes.

**Why this file loads when you edit framework source (not just test files):** any change under `lex/` needs a paired test in the *same* change — the CI coverage gate enforces it (see §4). So the moment you touch framework code, the testing rules are already in context: write the test alongside the code, automatically, the same way the cloud Copilot agent does. You do not need to be asked.

When these rules conflict with prior knowledge, **these rules win**. They reflect decisions documented in [`docs/testing-methodology.md`](../../docs/testing-methodology.md) and the active plan in [`lex/test_project/test-plan/`](../../lex/test_project/test-plan/).

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

## 5. Cluster test-plan — naming & allocation

Project-cluster tests under `lex/test_project/tests/` follow the plan in [`lex/test_project/test-plan/`](../../lex/test_project/test-plan/). The allocation rules are strict — **follow them, don't improvise**:

- **Test files:** `test_<module_stem>.py` (default) or `test_<cluster><letter>_<short_description>.py` (when extending the cluster plan).
- **Test classes:** `Test<Topic>` for ad-hoc tests; `TestCluster<NN><letter>_<Description>` when contributing to the cluster plan (e.g. `TestCluster01p_SettingsConstants`).
- **Test methods:** `test_<behaviour_under_test>` — verb-led, describes the assertion, not the setup.
- **Cluster numbers are never renumbered.** Extend a cluster with the next free **letter**; scenario IDs continue from the cluster's current max. The authoritative source is [`test-writing-plan.md`](../../lex/test_project/test-plan/test-writing-plan.md) — read it before allocating, never guess the next letter.
- **One batch = one sub-cluster = one PR.**

Claude Code users: the [`lex-testing` skill](../../.claude/skills/lex-testing/SKILL.md) walks this allocation automatically.

## 6. After writing tests — keep the plan honest (mandatory)

Writing the test is only half the job. Tests are the project's living record, so you update the plan in the **same change**:

1. **Append/update the batch row** in [`test-writing-plan.md`](../../lex/test_project/test-plan/test-writing-plan.md), under the right cluster, matching the table shape of the most recent batch. Fill scenario range, type (U/I/E), files covered, test file path, test classes, fixtures. Set *Tests landed* and *Coverage gain* to `pending — measured after run` until you have real numbers, then update them and flip *Status* to ✅ Complete.

2. **If a test surfaces broken framework behaviour, record the bug — don't weaken the test.** Per the workflow in [`known-bugs.md`](../../lex/test_project/test-plan/known-bugs.md): write the test asserting the *correct* behaviour (from docs/intent), mark it `@unittest.expectedFailure`, and add a `BUG-NNN` row (description, severity, cluster, test, status). When the framework is later fixed, the marker is dropped and the test passes naturally — a permanent regression gate. **Never** soften an assertion to make a real bug "pass".

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
