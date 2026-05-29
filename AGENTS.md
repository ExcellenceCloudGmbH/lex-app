# AGENTS.md — lex-app

Cross-tool guidance for AI coding agents (Claude Code, Copilot coding agent, Cursor, Codex)
working in this repository. These are defaults, not overrides: explicit user instructions and
`CLAUDE.md` win where they conflict.

`lex-app` is a Django framework shipped as a pip package. The frontend lives in a separate repo
(`process-admin-general-client`); the docs live in `lex-app-docs`.

## Three things to know before you touch anything

1. **The docs are authoritative.** Read the relevant files under [`docs/`](docs/) before
   implementing a feature. If your training data disagrees with the docs, the docs win. Start
   from [`docs/index.md`](docs/index.md) when unsure which file applies.

2. **Changing framework source means writing tests — automatically.** When you add or modify code
   under `lex/` (the framework: `lex/lex_app/`, `lex/core/`, `lex/api/`, `lex/audit_logging/`,
   `lex/process_admin/`, …), you write the paired tests in the **same change**, following the
   cluster-based test-plan. This is not optional and you do not need to be asked — the CI coverage
   gate (`copilot_coverage_check.yml`) blocks any source change that arrives without a paired test,
   so local work mirrors what the cloud agent does. The full rules live in
   [`.github/instructions/testing.instructions.md`](.github/instructions/testing.instructions.md)
   and the authoritative plan is [`lex/test_project/test-plan/`](lex/test_project/test-plan/).

3. **Tests keep the plan honest.** After writing tests you update the batch row in
   [`test-writing-plan.md`](lex/test_project/test-plan/test-writing-plan.md), and if a test
   surfaces broken framework behaviour you record it in
   [`known-bugs.md`](lex/test_project/test-plan/known-bugs.md) (assert the *correct* behaviour,
   mark `@unittest.expectedFailure`, add a `BUG-NNN` row) rather than weakening the test.

## What you'll be asked to do here

- **Develop a feature in lex-app** → read docs, implement, write paired cluster tests, run them,
  update the plan. See directive 2 above.
- **Develop in a downstream Lex project** (not this framework repo) → follow the project's own
  conventions and [`docs/`](docs/); the cluster test-plan rules are framework-internal and do
  **not** apply to downstream app code.
- **Run the tests** → `python -m lex pytest <path>` (or `lex test <labels>`). Never `manage.py test`.
- **Answer a question** → the answer is usually in [`docs/`](docs/) or `lex-app-docs`. Read before
  asserting; don't guess at framework APIs.

## Pointers

| Topic | Where |
| --- | --- |
| Testing rules (always read before writing a test) | [`.github/instructions/testing.instructions.md`](.github/instructions/testing.instructions.md) |
| Authoritative test-plan (clusters, allocation, bug tracker) | [`lex/test_project/test-plan/`](lex/test_project/test-plan/) |
| Framework conventions & feature docs | [`docs/`](docs/) |
| Session history & CI/CD architecture | [`CLAUDE.md`](CLAUDE.md), [`docs/ci-cd/`](docs/ci-cd/) |
| Claude Code skill for cluster tests | [`.claude/skills/lex-testing/SKILL.md`](.claude/skills/lex-testing/SKILL.md) |
