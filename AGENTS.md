# AGENTS.md — lex-app

Cross-tool guidance for AI coding agents (Claude Code, Copilot coding/agent mode, Cursor, Codex)
working in this repository. These are defaults, not overrides: explicit user instructions and
`CLAUDE.md` win where they conflict. **Read this file fully before starting any task** — it is the
contract for how work is done here.

`lex-app` is a Django framework shipped as a pip package. The frontend lives in a separate repo
(`process-admin-general-client`); the docs live in `lex-app-docs`.

## Prime directive 1 — Research before you write code

**Do not start implementing from your first instinct.** Framework code here has bugs, workarounds,
and non-obvious lifecycle rules; guessing produces plausible-looking code that is wrong in
production. Before writing feature code or tests:

1. **Read the docs that describe the intent**, not just the code: [`docs/`](docs/) — especially
   [`docs/features/`](docs/features/), [`docs/reference/`](docs/reference/),
   [`docs/tutorial/`](docs/tutorial/). The docs describe what the framework is *trying to achieve*;
   the source is an incomplete story. Start at [`docs/index.md`](docs/index.md) if unsure.
2. **Read the public API docstrings** of the classes/functions you'll touch, and skim the existing
   tests for that area (under `lex/test_project/tests/<topic>/`) to see the established patterns.
3. **Check for existing mechanisms before inventing new ones.** Before adding a new store, cache,
   registry, or helper, search the codebase for one that already does the job — a second, parallel
   home for state that already has one is a common source of subtle divergence bugs. Reuse the
   established mechanism rather than reinventing it.
4. **Restate the request before designing, then ask with a recommendation — never a blank
   question.** Put the request in your own words and pin down its *scope and propagation* first:
   words like "every / all / always / each" hide a boundary — does the change apply at the **entry
   point only**, or **recursively** to everything it triggers downstream? State the interpretation
   you'll build to. If more than one design is genuinely defensible, **STOP and ask the developer** —
   but **lead every question with the option you recommend and *why*, plus the trade-off of the
   alternative**, so they confirm or correct rather than doing the design work themselves. A bare "X
   or Y?" pushes the thinking back on them; "I'd do X because Z — the cost is W — confirm?" does not.
   A wrong assumption baked into a feature + its tests is far more expensive than one question.
   (Claude Code: use the `superpowers:brainstorming` skill for this.)

When your prior knowledge conflicts with the docs, **the docs win.**

## Prime directive 2 — Changing framework source means writing cluster tests, automatically

When you add or modify code under `lex/` (`lex/lex_app/`, `lex/core/`, `lex/api/`,
`lex/audit_logging/`, `lex/process_admin/`, …), you write the paired tests in the **same change**,
following the **cluster test-plan** — without being asked. The CI coverage gate
(`copilot_coverage_check.yml`) blocks any source change that arrives without a paired test, so local
work must mirror what the cloud agent does.

**Tests are derived from intent (docs), never by mirroring the implementation you just wrote.**
Writing a test that asserts whatever the code happens to do locks in bugs — this is the single most
common failure here (see the Golden Rule in
[`lex/test_project/test-plan/test-clusters.md`](lex/test_project/test-plan/test-clusters.md)).

Where the tests go and how they're named is **strict** — do not improvise:

- **Location:** `lex/test_project/tests/<cluster_slug>/` (the cluster system). **Not**
  `lex/tests/unit/` — that is the legacy audit tree; do not add new feature tests there.
- **Surfaces:** first enumerate *every externally-observable behaviour* the change creates — each
  new public entry point (method, classmethod, REST endpoint, management command), **each distinct
  execution path the same operation can take** (one operation may run by more than one route
  depending on configuration, runtime mode, or how the work is dispatched — those routes can have
  entirely different machinery and ways of being stopped, so each is its own surface and covering one
  tells you nothing about the others), state transition, persisted/emitted side-effect, and
  error/edge path. A surface is only *covered* when a test drives it **end-to-end through the public
  entry point**, not via the helper or in-memory data structure underneath. Exercising only the easy
  internal layer (and the no-op/error returns) is the single most common way a change *looks* tested
  but isn't. The authoritative statement of this rule lives in
  [`conventions.md` → "Enumerate Behaviour Surfaces Before Allocating Clusters"](lex/test_project/test-plan/progress/conventions.md)
  — the one source the cloud and local agents share.
- **Allocation:** map **each surface** to its owning cluster by *what the surface is*, not where the
  source line lives (a REST surface belongs to the API cluster even if the code sits on a model) —
  often more than one cluster, and surfaces in different clusters become separate batches. Take the
  **next free letter** for each cluster and continue scenario IDs from its current max. The
  authoritative source is
  [`test-writing-plan.md`](lex/test_project/test-plan/test-writing-plan.md) — **read it to allocate;
  never guess the letter.** If you can't confidently place a surface, or it genuinely can't be
  exercised in the test environment, **stop and ask** — never silently omit it and still call the
  change tested.
- **Names:** file `test_<NN><letter>_<short>.py`, class `TestCluster<NN><letter>_<Description>`,
  module-level `pytestmark = pytest.mark.<cluster_slug>`, methods `test_<NN>_<NN>_<behaviour>` with a
  `Scenario X.Y: … / Given / When / Then` docstring, and an `Intent` header on the file.

Full rules: [`.github/instructions/testing.instructions.md`](.github/instructions/testing.instructions.md).
Claude Code: the [`lex-testing` skill](.claude/skills/lex-testing/SKILL.md) automates the allocation.

## Prime directive 3 — A task is not done until the test-plan is consistent

After the feature + tests, you bring the plan into sync **in the same change** — this is part of
finishing, not optional cleanup. This is the **same checklist the cloud Copilot agent satisfies on
every PR** (`.github/scripts/copilot_assemble_prompt.py` → "Required deliverables"); local work
matches it so the two paths don't drift:

1. **Append a row to [`session-log.md`](lex/test_project/test-plan/progress/session-log.md)** — the
   universal per-PR record ("the Copilot test-bot writes here as part of every PR"). Append-only,
   bottom row, never re-order.
2. **Update the cluster status / scenario range** in
   [`test-clusters.md`](lex/test_project/test-plan/test-clusters.md) for each touched (sub-)cluster,
   and bump [`dashboard.md`](lex/test_project/test-plan/progress/dashboard.md).
3. **If the work maps to a planned batch**, append/update the batch row in
   [`test-writing-plan.md`](lex/test_project/test-plan/test-writing-plan.md): scenario range, type
   (U/I/E), files covered, test file path, test classes, fixtures, status.
4. **Run the tests** (`python -m lex pytest <path>`) and record real results (`N pass / 0 fail`,
   coverage gain) in the rows above.
5. **If a test surfaced broken framework behaviour**, record it in
   [`known-bugs.md`](lex/test_project/test-plan/known-bugs.md) (assert the *correct* behaviour, mark
   `@unittest.expectedFailure` / `@pytest.mark.xfail(strict=True)`, add a `BUG-NNN` row) — **never
   weaken the test to make it pass.**

If the plan and the tests on disk disagree, the task is not finished.

## Prime directive 4 — When everything is complete and verified, offer a pull request

Once the feature/bugfix, its cluster tests, **and** the test-plan are all consistent (directives 2–3)
and the tests actually pass, **ask the developer whether to open a pull request** before touching any
branch. Do **not** open a PR unsolicited.

- **Only offer when the work is genuinely finished** — never mid-task, and never while tests are
  failing or the change is partial. If it isn't done, keep going or report the blocker instead.
- This is the **local agent's** completion step. It is unrelated to the cloud GitHub Copilot coding
  agent that produces docs PRs in `lex-app-docs`.

If the developer says **yes**:

1. Create a **new branch** with a **meaningful, descriptive name** (`feat/…`, `fix/…`, e.g.
   `feat/instant-cancel-calculations`, `fix/coverage-gate-all-apps`). **Never push to the default
   `lex-app-v2` branch directly** — the repository ruleset blocks direct pushes, so every change lands
   via a branch + PR.
2. Move the completed commits onto that branch and push it with `git push -u`.
3. Open the PR with `gh pr create`: a concise title (< 70 chars) and a body covering **what changed
   and why**, plus a short test-plan / verification checklist (the same evidence directives 2–3
   already produced).
4. Return the PR URL.

If the developer says **no**, leave the commits as they are and continue.

## What you'll be asked to do here

- **Develop a feature/bugfix in lex-app** → directives 1→2→3→4, in that order.
- **Develop in a downstream Lex project** (not this framework repo) → follow that project's own
  conventions and [`docs/`](docs/); the cluster test-plan rules are framework-internal and do **not**
  apply to downstream app code.
- **Run the tests** → `python -m lex pytest <path>` (or `lex test <labels>`). Never `manage.py test`.
- **Answer a question** → the answer is usually in [`docs/`](docs/) or `lex-app-docs`. Read before
  asserting; don't guess at framework APIs.

## Pointers

| Topic | Where |
| --- | --- |
| Testing rules (read before writing any test) | [`.github/instructions/testing.instructions.md`](.github/instructions/testing.instructions.md) |
| Authoritative test-plan (clusters, allocation, Golden Rule, bug tracker) | [`lex/test_project/test-plan/`](lex/test_project/test-plan/) |
| Test conventions (naming, run commands, quality gates) | [`lex/test_project/test-plan/progress/conventions.md`](lex/test_project/test-plan/progress/conventions.md) |
| Framework conventions & feature docs | [`docs/`](docs/) |
| Session history & CI/CD architecture | [`CLAUDE.md`](CLAUDE.md), [`docs/ci-cd/`](docs/ci-cd/) |
| Claude Code skill for cluster tests | [`.claude/skills/lex-testing/SKILL.md`](.claude/skills/lex-testing/SKILL.md) |
