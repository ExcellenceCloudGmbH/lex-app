# Copilot Test Automation Pipeline — Feature Spec

> **Status:** design accepted, implementation underway.
> **Date:** 2026-05-26
> **Owner:** Hazem Sahbani
> **Reviewer (target):** Supervisor (presentation Tuesday 2026-05-26)
> **Supersedes:** none (extends [`2026-05-13-copilot-test-bot-design.md`](2026-05-13-copilot-test-bot-design.md))

---

## 1. Context & goals

The supervisor delivered four test-automation tasks (German original, English summary in [§1.1](#11-supervisors-four-tasks)). This spec is the single document mapping each task to a concrete feature, the architecture behind it, the user stories it serves, and the demo plan that proves it works.

This spec covers **only** the test-automation, docs-automation, and PR-coverage workstream. The parallelization, DB-optimization, and Marco-collaboration tasks from the same email are tracked separately.

### 1.1 Supervisor's four tasks

| # | German (verbatim) | English | This spec's feature |
|---|---|---|---|
| 1 | *"Die aktuelle Dokumentation mithilfe von Automatisierungs-Workflows aktualisieren."* | Update documentation via automation workflows. | [Feature 1](#4-feature-1--automated-docs-update) |
| 2 | *"Mehrere Beispiele für die Testautomatisierung erstellen, die fehlende Tests schreiben (z. B. kleine Commits im lexap der letzten Wochen abfangen)."* | Create multiple test-automation examples — e.g. retrofit tests for small commits from the last few weeks. | [Feature 2](#5-feature-2--retrospective-test-coverage) |
| 3 | *"Sicherstellen, dass das lokale Entwicklungssystem mit der AI-Automatisierung übereinstimmt."* | Make sure local dev mirrors what the Copilot agents do in CI. | [Feature 3](#6-feature-3--local-dev-alignment) |
| 4 | *"Die Implementierung abschließen, bei der ein Pull Request geprüft wird, ob Tests für neue Änderungen vorhanden sind. Falls nicht, sollen Tests erstellt, ausgeführt und der PR blockiert werden, wenn die Tests fehlschlagen."* | Finish the PR-check workflow: check if tests exist for new changes; if not, create + run them; block the PR if they fail. | [Feature 4](#7-feature-4--pr-coverage-check-new) |

### 1.2 What done looks like

The demo on Tuesday will exercise Features 1, 2, and 4 live; Feature 3 will be shown via documentation only. Each feature lands as a working green chain visible in GitHub Actions, with the supervisor watching.

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Copilot coding agent** | The autonomous Copilot mode that can be assigned issues and opens PRs. Distinct from Copilot Chat. |
| **`Copilot` (login)** | The actual GitHub user.login of the coding agent. The familiar form `copilot-swe-agent[bot]` is only a UI display name. Every workflow `if:` check on author must accept both spellings. |
| **test-bot** | The chain of GitHub Actions workflows in `.github/workflows/copilot_*.yml` that turns a maintainer's test-request issue into a merged regression test. |
| **gate** | A workflow that validates a Copilot-authored PR (shape check, isolated test run) and either auto-merges it or labels it for human review. Two exist: `copilot_pr_gate.yml` (in lex-app) and `copilot_docs_pr_gate.yml` (in lex-app-docs). |
| **rc release** | Release candidate tag, format `vX.Y.ZrcN`. The bot bumps `N` and creates a non-draft GitHub release; `pip_publish.yml` then ships to PyPI. |
| **`[copilot-test]` issue** | The issue a maintainer files via the form. Carries a `copilot:<mode>` label after the bridge workflow runs. |
| **`[copilot-task]` issue** | An issue the test-bot assembles from `[copilot-test]` + the test-plan, assigned to Copilot. This is what Copilot actually reads. |
| **coverage-task issue** | NEW (Feature 4). An issue the PR coverage check opens, asking Copilot to write tests for a specific source file that a PR touched without test coverage. |
| **lex-app** | This repo. Code + tests + the test-automation workflows. Default branch: `lex-app-v2`. |
| **lex-app-docs** | Sister repo at `~/LUND_IT/lex-app-docs/`. Receives `release-published` dispatches and runs its own Copilot docs-update flow. |
| **PAT** (`COPILOT_PAT`) | Personal access token stored as a secret. Required because events triggered by `GITHUB_TOKEN` are deliberately not propagated by GitHub (loop prevention), and our chain depends on cross-workflow propagation. |

---

## 3. User personas

| Persona | Who | What they care about |
|---|---|---|
| **Maintainer** | Hazem | Reliable, observable chain. Demo plan that works on stage. Low cost-per-test. |
| **Reviewer** | Supervisor, Marco | Trustworthy auto-merges. No surprises in PyPI. Coverage doesn't silently regress. |
| **PR Author** | Any contributor (human or bot) | Fast feedback. Actionable, not vague. Doesn't have to read implementation YAML to understand why their PR is blocked. |

---

## 4. Feature 1 — Automated docs update

### 4.1 User story

> *As the maintainer, when I cut an `rc` release, the docs site (`lex-app-docs`) should automatically open a documentation PR that updates content to match the release, gated by the same quality checks human PRs face, and auto-merged on green — without me filing anything by hand.*

### 4.2 Architecture (existing chain)

```
lex-app:                                            lex-app-docs:
─────────                                           ─────────────
copilot_publish_after_merge.yml                     auto-update-docs.yml
  (merged PR with publish-on-merge → creates          (receives dispatch →
   non-draft GitHub release)                           creates issue → assigns
        │                                              Copilot → Copilot opens PR)
        ▼
pip_publish.yml                                                  │
  (release.created → builds + uploads to PyPI)                   ▼
        │                                            copilot_docs_pr_gate.yml
        ▼                                              (frontmatter check, npm
update_docs.yml                                        check + test, quartz build →
  (Publish to PyPI ran → repository_dispatch           auto-merge or human review)
   release-published → lex-app-docs)
        └──────────────────────────────────────────────────────►
```

### 4.3 Status

**Implemented but never exercised end-to-end this session.** All five workflows exist on `main`/`lex-app-v2`. The chain was unable to fire previously due to a structural bug in `copilot_publish_after_merge.yml` (used `--draft`, which prevented `release.created` from firing) — fixed in commit `c3b00bb` on 2026-05-26.

### 4.4 Demo plan

A live demo of Feature 1 is folded into the [end-to-end demo script (§8)](#8-end-to-end-demo-script-for-tuesday): one of the Feature-2 retrospective PRs will carry `publish-on-merge` and exercise the full chain, including docs update.

Backup if the live chain fails: the [`automated-docs-pipeline.md`](../../ci-cd/automated-docs-pipeline.md) architecture doc + the most recent merged docs PR (`lex-app-docs#18`) demonstrate prior successful runs.

---

## 5. Feature 2 — Retrospective test coverage

### 5.1 User story

> *As the maintainer, I can point a script at the last N days of merged commits in `lex-app-v2`, pick the ones that lack test coverage, file one test-request issue per pick, and watch the test-bot retrofit a regression test for each — raising coverage without writing any test by hand.*

### 5.2 Architecture

Reuses the existing test-bot end-to-end. The new piece is a single helper script:

`scripts/copilot_pick_uncovered_commits.py`
- Reads `git log --since='<N> days ago' --merges --pretty='%h%x09%s%x09%an' lex-app-v2`
- Filters: skip merges authored by `Copilot`/`copilot-swe-agent[bot]`, skip pure-docs commits, skip commits that already touched a `test_*.py` file
- For each surviving commit, emits a suggested ticket body in the form expected by `.github/ISSUE_TEMPLATE/copilot-test-request.yml`:
  - **Mode:** `regression`
  - **Behaviour description:** derived from the commit message (first line) — operator can edit before submitting
  - **Files involved:** the commit's modified source files (excluding test files)
  - **Cluster hint:** blank (Copilot decides)
- Output is markdown blocks ready to paste into the form, ordered most-recent first.

The maintainer reviews the output, picks N commits worth demoing, and files those issues. The rest of the chain runs unmodified.

### 5.3 Status

Script doesn't exist yet — implemented as part of this spec's plan. Other infrastructure (test-bot, gate, auto-merge) is already in place.

### 5.4 Demo plan

For Tuesday:
1. Run `python3 scripts/copilot_pick_uncovered_commits.py --days 14`
2. Show the output (N suggested tickets).
3. File **two** of them by hand (cost-conscious — proves the pattern, doesn't burn cycles).
4. Both should green through the chain and merge during the call.
5. Slide: *"Retrofitted N tests for commits from the last 14 days, all merged via auto-merge, total maintainer touch time: form submission."*

---

## 6. Feature 3 — Local dev alignment

### 6.1 User story

> *As a developer, I can dry-run the exact prompt the Copilot agent will see — against the same test-plan files, with the same assembler logic — before I file a real issue, so I can iterate on phrasing locally instead of paying for cloud Copilot cycles to discover my prompt was wrong.*

### 6.2 Design

Two artifacts:

**`scripts/copilot_dry_run.py`** — wraps `copilot_assemble_prompt.py`:
- Reads a form-style YAML or JSON on stdin (same fields as the issue form)
- Resolves the same test-plan files the CI workflow does (`lex/test_project/test-plan/*`)
- Prints the assembled `[copilot-task]` issue body to stdout
- No GitHub API calls — purely local

**`docs/ci-cd/local-dev.md`** — cookbook:
- "How to dry-run a test request before filing it"
- "How to find your cluster" (pointer to `test-clusters.md`)
- "What Copilot's agents read — the prompt assembly contract"
- "How to verify a test you wrote matches Copilot's PR-shape rules" (using `copilot_validate_pr_shape.py`)

### 6.3 Status

Both artifacts are net-new. Not blockers for the Tuesday demo (Feature 3 is shown as documentation, not live), but expected to ship in the same implementation plan as Feature 4.

---

## 7. Feature 4 — PR coverage check (NEW)

### 7.1 User stories

> *As a PR author, when I open a PR that changes source in `lex/lex_app/**`, I want immediate feedback that names the specific files lacking tests, with the bot offering to write them — so I'm not stuck on a vague "needs tests" comment three days later.*

> *As a reviewer, when I see a green merge, I want to know coverage didn't silently drop, so the suite remains a trustworthy regression net.*

> *As the maintainer, I want one uniform workflow enforcing coverage on every PR, so I don't have to manually chase contributors.*

### 7.2 Detection — Approach D (heuristic + Copilot)

**Phase 1: Heuristic file-pairing (synchronous, ~10s)**

For each file in the PR diff with status `added` or `modified` under `lex/lex_app/**`:
- **Allowlist (skip):** `__init__.py`, `settings*.py`, anything under `migrations/`, anything under `apps.py`.
- **Match rule:** the file is considered tested if the PR also touches any `lex/test_project/tests/**/test_*.py` whose source either (a) imports the source module path, or (b) contains the source filename stem.
- If every non-allowlisted source file has a matched touched test → ✅ Phase 1 passes, workflow exits success.
- Else → emit list of untested source files.

**Phase 2: Copilot writes on miss (asynchronous, minutes)**

For the list of untested files (grouped into one issue if >5 files):
1. Open a `[copilot-task]` issue labelled `coverage-task`, body:
   > *"PR #N modifies the following files without test coverage:*
   > - *<file 1>*
   > - *<file 2>*
   > - *…*
   >
   > *Write regression tests for the behaviour these files now express. Open your PR targeting branch `<pr-head-ref>`, not `lex-app-v2`."*
2. Apply label `needs-tests` to the original PR.
3. Post a failing required check named `coverage / required` on the original PR.
4. The original PR is now blocked by GitHub's branch protection.

When Copilot's coverage-task PR merges into the original PR's head branch, GitHub fires `pull_request: synchronize` on the original PR. This workflow re-runs Phase 1, which now passes, drops `needs-tests`, posts a green `coverage / required` check, and normal CI proceeds.

### 7.3 Trigger + guards

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
    branches: [lex-app-v2]
```

Skip conditions (any → exit success without checking):
1. `github.event.pull_request.user.login` in (`Copilot`, `copilot-swe-agent[bot]`) — their PRs go through `copilot_pr_gate.yml`.
2. PR has label `coverage-task` — it IS the bot's response.
3. PR has label `copilot:regression`, `copilot:bug-repro`, or `copilot:fix-and-test` — it's an output of the existing test-bot path, which has its own gate.
4. PR title or body contains `[skip-coverage-check]` — escape hatch for pure refactors, build-system changes, etc. (Reviewer should verify the skip is justified.)
5. All changed files are under `docs/**`, `*.md`, the allowlist, or detected pure renames (`git diff --find-renames=90%`).

### 7.4 State machine

```
                         ┌─────────────────────────┐
                         │   PR opened/updated     │
                         └────────────┬────────────┘
                                      │
                            ┌─────────▼──────────┐
                            │ Skip guards fire?  │── yes ──► exit success
                            └─────────┬──────────┘
                                      │ no
                            ┌─────────▼──────────┐
                            │  Phase 1 (heur.)   │
                            └─────┬───────┬──────┘
                                  │       │
                         pass ────┘       └──── miss
                           │                      │
                           ▼                      ▼
                   drop `needs-tests`   open coverage-task issue(s)
                   post green check     apply `needs-tests` label
                   done                 post red `coverage / required`
                                                │
                                                ▼
                                    Copilot opens coverage-task PR
                                    (base: parent PR's head ref)
                                                │
                                                ▼
                                    copilot_pr_gate.yml validates,
                                    auto-merges into parent PR branch
                                                │
                                                ▼
                                    parent PR fires `synchronize`
                                                │
                                                └─► loop back to Phase 1
                                                    (now passes, exits green)
```

### 7.5 Recursion safety — three independent guards

Recursion would be catastrophically expensive (one Copilot agent + one gate run per loop). Three independent guards:

1. **Author exclusion** — Copilot's PRs never trigger this workflow (guard #1 in §7.3).
2. **Label exclusion** — coverage-task PRs carry `coverage-task`, this workflow skips them (guard #2).
3. **Branch-targeting filter** — `branches: [lex-app-v2]` on the trigger means coverage-task PRs (which target the parent PR's feature branch, not `lex-app-v2`) don't fire this workflow at all.

Any single guard is sufficient. Three is intentional belt-and-braces.

### 7.6 Edge cases

| Case | Handling |
|---|---|
| Mass rename / pure refactor | `git diff --find-renames=90%` excludes pure renames; otherwise author uses `[skip-coverage-check]` with reasoning, reviewer verifies. |
| Test-only PR | Trivially passes Phase 1 (no source changes under `lex/lex_app/**`). |
| Large PR (>5 untested files) | One grouped coverage-task issue, not per-file (avoids issue spam). |
| Copilot fails to write tests | Coverage-task PR's gate fails → it doesn't auto-merge → parent PR stays blocked. Author can take over manually or use `[skip-coverage-check]` after triage. |
| Coverage-task PR conflicts with parent | Copilot asked to rebase via the standard PR-comment loop; if it can't, human escalation. Same path the existing gate uses. |
| Author squash-merges before coverage-task lands | Required check stays red, GitHub blocks merge natively. No special handling. |
| Source file deleted | Skipped by Phase 1's `status in (added, modified)` filter. |

### 7.7 Resolved decisions (confirmed 2026-05-26)

1. **Required-check name:** `coverage / required`. Branch protection must list this exact name. Hard-coded in `copilot_coverage_check.yml`'s three check-run posts.
2. **Coverage-task PRs auto-merge into parent on green:** yes. Default behaviour of `copilot_pr_gate.yml`, no change needed.
3. **Allowlist scope:** `__init__.py`, `settings*.py`, `migrations/**`, `apps.py`. Kept deliberately minimal — `utils/**` and `management/commands/**` contain behaviour worth testing; broad allowlists are how coverage rots silently.
4. **Test-bot output labels** (`copilot:regression`, `copilot:bug-repro`, `copilot:fix-and-test`) on skip list: confirmed. Those PRs have their own gate (`copilot_pr_gate.yml`); rechecking them here would create a "test-PR needs tests" loop the label-exclusion guard alone wouldn't catch.

---

## 8. End-to-end demo script for Tuesday

Run in this order. Each step has a fallback.

### 8.1 Pre-flight (do this BEFORE the call)

```bash
# 1. Verify Copilot-author setting is option 2 (not option 3)
#    GitHub UI: Settings → Actions → General → Approval for first-time contributors
#    Required, otherwise every Copilot run stalls

# 2. Verify "Allow auto-merge" is enabled
#    UI: Settings → General → Pull Requests → Allow auto-merge ✓

# 3. Verify required checks on lex-app-v2 branch protection:
#    `Copilot PR Gate / gate` AND (once Feature 4 ships) `coverage / required`

# 4. Verify on lex-app-docs: same three settings + COPILOT_PAT secret exists
```

### 8.2 Demo Feature 4 (PR coverage check) — 5 min

```bash
# Open a tiny PR that modifies a source file but touches no test.
# Pick a real source file that's small + has clear behaviour (e.g. add a
# docstring or rename a private variable inside a method body — the
# heuristic only cares that no matching test file is in the diff).
git checkout -b demo/coverage-check-untested
# (edit one file under lex/lex_app/** without touching any test_*.py)
git add lex/lex_app/<chosen-file>.py
git commit -m "demo: change without tests"
gh pr create --base lex-app-v2 --title "Demo: change without tests" \
  --body "Intentionally missing tests to demo Feature 4."
# Watch the coverage-check workflow:
#   1. Phase 1 detects miss, opens coverage-task issue, posts red
#      `coverage / required` check, applies `needs-tests` label.
#   2. Copilot picks up the coverage-task, opens a PR targeting THIS
#      branch (not lex-app-v2), gate auto-merges it into the branch.
#   3. The merge fires `synchronize` on this PR, Phase 1 re-runs and
#      passes, check goes green, PR unblocks.
# Expected wall-clock: ~5–8 min depending on Copilot queue.
```

### 8.3 Demo Features 1 + 2 (retrospective + docs chain) — 10 min

```bash
# Generate suggestions from the last 14 days
python3 scripts/copilot_pick_uncovered_commits.py --days 14 | head -50

# Pick 2 commits, file two test-request issues via the form,
# tick "Publish on merge" on one of them (this exercises Feature 1 too).

# Watch the chain:
gh run watch                                            # gate
gh pr list --label auto-merge                           # auto-merge in flight
gh release list --limit 3                              # new rc cut
gh -R ExcellenceCloudGmbH/lex-app-docs issue list \
   --label automated --limit 3                          # docs issue created
gh -R ExcellenceCloudGmbH/lex-app-docs pr list \
   --state all --limit 3                                # docs PR opened + gated
```

### 8.4 Show Feature 3 — 2 min (no live execution)

```bash
# Show the cookbook
cat docs/ci-cd/local-dev.md

# Live-run the dry-runner on a hypothetical request
cat <<EOF | python3 scripts/copilot_dry_run.py
mode: regression
behaviour: |
  Verify SimpleItem CRUD round-trip
cluster_hint: 2b
EOF
# Shows the exact prompt Copilot would receive — no GH calls.
```

### 8.5 Fallbacks

| Step | If it fails | Fallback |
|---|---|---|
| 8.2 | coverage-task PR doesn't open | Show the workflow logs proving Phase 1 detected the miss; explain Phase 2 is the bottleneck (Copilot wait time). |
| 8.3 | Gate fails on cairo or other infra | Show prior merged PR (`8537099` in lex-app-docs) as evidence the chain shipped before. |
| 8.3 | Docs chain doesn't reach lex-app-docs | Open the `automated-docs-pipeline.md` arch diagram and walk through it. |
| 8.4 | dry-run script throws | Show the test-plan files Copilot reads + `copilot_assemble_prompt.py` source. |

---

## 9. Out of scope / deferred

- **Parallelization to four / DB load** — separate supervisor task, separate workstream.
- **Collaboration with Marco on infrastructure** — separate task.
- **Coverage-delta detection** (rejected) — too slow, false-positives on refactors, can't tell Copilot *which behaviour* to test.
- **LLM-judge detection** (rejected) — non-deterministic, can't defend "the bot said no" to a frustrated author.
- **Local-runner CI parity beyond the prompt assembler** — deferred to Phase 2; the cookbook is a stepping stone, not a full local-CI environment.
- **`auto-close-empty-pr.yml`** in lex-app-docs — stub exists in `docs/ci-cd/docs-auto-close-empty-pr.yml`, not yet deployed. Tracked separately.

---

## 10. Appendix A — Changes this session

Commits on `lex-app-v2` (newest first):

| SHA | Subject | Relevance |
|---|---|---|
| `c3b00bb` | fix(ci): publish chain — bot must create non-draft release for pip_publish to fire | Unblocks Feature 1's chain end-to-end. |
| `37902d8` | fix(issue-form): drop "[copilot-test] " title prefix so users write real titles | Cosmetic but affects every form submission. |
| `64ebed7` | fix(ci): install cairo system deps in PR gate before pip install | Required for any gate run to install lex-app on the runner. |
| `6b782e7` | fix(ci): match Copilot PR author login as 'Copilot' (not display name) | Critical — gate was skipping every Copilot PR before this. |
| `ff9693a` | fix(ci): use COPILOT_PAT in label bridge so labeled event propagates | Required for the bridge → test-bot trigger chain. |
| `341ab7a` | docs(ci): add docs PR gate + auto-merge to automated pipeline | Documents Feature 1's gate. |
| `e237cd1` | feat(ci): bridge issue-form Mode dropdown to GitHub label | The Mode-dropdown → label bridge workflow. |

Open issues + PRs:

| Item | State | Notes |
|---|---|---|
| Issue #66 | OPEN | Form-filed regression demo ticket (renamed during this session). |
| Issue #67 | OPEN | Auto-assembled `[copilot-task]` for #66 — Copilot is working on it. |
| PR #65 | CLOSED | Prior demo PR; failed on cairo before the fix landed. |
| PR #62, #56 | CLOSED | Earlier demo iterations. |

---

## 11. Implementation plan handoff

The next step is the `writing-plans` skill, which will turn this spec into a phased implementation plan covering:

1. `scripts/copilot_pick_uncovered_commits.py` (Feature 2)
2. `scripts/copilot_dry_run.py` + `docs/ci-cd/local-dev.md` (Feature 3)
3. `.github/workflows/copilot_coverage_check.yml` + branch-protection update (Feature 4)
4. Pre-flight checklist execution (manual UI clicks tracked as a task)
5. Live end-to-end demo dry-run (everything in §8) before the presentation

Implementation begins immediately after that plan is approved.
