# Local dev that mirrors what Copilot does in CI

> Supervisor task 3: *"Make sure the local development system matches the AI automation."*
> This is the developer-facing cookbook for that alignment. The architecture is documented in [`copilot-test-bot.md`](copilot-test-bot.md); this file is the *how-to*.

The Copilot test-bot reads a small, fixed set of files at CI time to build the prompt it gives the coding agent. You can read the same files locally, run the same assembler locally, and validate your PRs against the same shape rules locally — without paying for cloud Copilot cycles to discover your prompt was wrong.

## Quick reference

| You want to… | Run this |
|---|---|
| See exactly what Copilot will receive for a request | `cat req.yaml \| python3 scripts/copilot_dry_run.py` |
| Find candidate commits worth retrofitting tests for | `python3 scripts/copilot_pick_uncovered_commits.py --days 14` |
| Verify a hand-written test matches Copilot PR-shape rules | `python3 .github/scripts/copilot_validate_pr_shape.py …` |
| Locate the right cluster for a new test | open `lex/test_project/test-plan/test-clusters.md` |
| Read the test-writing rules Copilot follows | open `lex/test_project/test-plan/test-writing-plan.md` |

## 1. Dry-run a test request before filing it

`scripts/copilot_dry_run.py` wraps the same `assemble_prompt` function `copilot_test_bot.yml` calls in CI. It reads a form-style YAML or JSON document on stdin and prints the full `[copilot-task]` body Copilot would receive.

Minimal request:

```bash
cat <<EOF | python3 scripts/copilot_dry_run.py > /tmp/preview.md
mode: regression
behaviour: |
  Codify SimpleItem CRUD round-trip: create → save → re-read returns
  the same field values and one history row.
EOF
less /tmp/preview.md
```

All form fields are supported (`mode`, `behaviour`, `reproducer`, `cluster_hint`, `files`, `title`, `number`). `files` may be a list or a comma-separated string. American spelling `behavior` is accepted as a courtesy.

The script prints a stats summary to stderr (body bytes, mode, file count) so you know whether your request will hit GitHub's 64KB issue body cap before you submit.

**Do not paste dry-run output into a real issue.** It carries `issue #0` and `Fixes #0`, which the gate workflow can't resolve. The dry-run is for *inspection* — once you're happy with the phrasing, file the request through the form so the real `copilot_test_bot.yml` chain runs.

## 2. Find candidates for retrospective coverage

`scripts/copilot_pick_uncovered_commits.py` walks recent merges on `lex-app-v2`, drops bot-authored commits and pure docs / config / migration changes, and prints one ready-to-paste issue-form block per surviving commit.

```bash
# Default: last 14 days, all matches
python3 scripts/copilot_pick_uncovered_commits.py --days 14

# Cap the noise during a demo
python3 scripts/copilot_pick_uncovered_commits.py --days 30 --limit 5

# Different branch (rarely needed)
python3 scripts/copilot_pick_uncovered_commits.py --branch main
```

For each commit you want to file, copy the block, open *Issues → New → Copilot Test Request* in the GitHub UI, paste the body, and tick the `Mode` dropdown to match the suggestion (forms can't set dropdowns from issue body content).

## 3. Validate a hand-written test matches the gate's PR-shape rules

The PR gate (`copilot_pr_gate.yml`) calls `.github/scripts/copilot_validate_pr_shape.py` to enforce file-set and naming rules on every Copilot PR. Use the same script locally before opening any test PR:

```bash
python3 .github/scripts/copilot_validate_pr_shape.py --help
```

The validator is the source of truth: if your PR passes locally, it will pass the gate's shape check. If you're adding a test cluster, register it in `.github/scripts/showcase_clusters.py` first — the validator reads cluster names from there.

## 4. What Copilot reads — the prompt assembly contract

The assembler (`.github/scripts/copilot_assemble_prompt.py`) inlines two files into every `[copilot-task]` body and references two more by path:

| File | Inlined? | Why |
|---|---|---|
| `lex/test_project/test-plan/index.md` | Inlined (Golden Rule paragraph only) | Anti-overfitting discipline must lead. |
| `lex/test_project/test-plan/progress/conventions.md` | Inlined | Fits comfortably under the byte cap. |
| `lex/test_project/test-plan/test-clusters.md` | Referenced by path | ~160KB — would blow the 64KB issue cap. |
| `lex/test_project/test-plan/test-writing-plan.md` | Referenced by path | Same reason. |

If you edit any of those files, the next dry-run picks the change up automatically — there is no separate "cached copy" in the workflow.

The assembled body is hard-capped at 60,000 bytes (GitHub's limit is 65,536). If the assembler ever raises `ValueError: assembled prompt is N bytes`, trim `conventions.md` or the Golden Rule paragraph in `index.md`. Do **not** raise the cap.

## 5. Day-to-day alignment checklist

When in doubt, mirror what CI does:

- Same Python: project targets `3.12.0`. The PR gate uses that exact version.
- Same test-plan files: never make a local edit to `index.md` / `conventions.md` and not commit it — the dry-runner will show output that CI cannot reproduce.
- Same branch base: file test-request issues against `lex-app-v2` (the actual default branch), not `main`.
- Same author rules: PR-gate workflows accept both `Copilot` and `copilot-swe-agent[bot]` as the bot's login. If you script around this, do the same.

## 6. What's deferred

A full local-CI environment (Postgres in Docker, services container parity, full coverage run) is out of scope for this cookbook. The dry-runner + validator combination covers the *prompt-level* alignment supervisor task 3 asked for; runtime parity is a separate workstream.
