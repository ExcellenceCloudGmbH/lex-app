# Copilot Test-Bot — Handoff Snapshot (2026-05-26)

> **Status:** mid-implementation. Presentation today. Pipeline mostly wired end-to-end; the first real PR-Gate execution surfaced a cairo system-deps gap that's been pushed but not yet re-verified.
>
> **Read this top-to-bottom before touching anything.** The "How to continue" section at the bottom has the exact next-action sequence.

---

## What the pipeline is supposed to do (one paragraph)

A maintainer files an issue via the Copilot Test Request form. The form has a required Mode dropdown (`regression` / `bug-repro` / `fix-and-test`) and an optional "Publish on merge" checkbox. The system then:

1. Bridges the form's Mode dropdown to a `copilot:<mode>` GitHub label.
2. The label fires the test-bot, which assembles a prompt from `lex/test_project/test-plan/*` and creates a `[copilot-task]` issue assigned to the Copilot coding agent.
3. Copilot writes the test, opens a PR linked via `Fixes #N`.
4. A PR-Gate validates the PR shape, runs the new test in isolation, and on green calls `gh pr merge --auto --squash`.
5. On merge, if `publish-on-merge` is also set and the repo var `COPILOT_AUTO_PUBLISH_ENABLED=='true'`, a draft `rc` GitHub release is cut.
6. The draft release triggers `pip_publish.yml` (`release: [created]` fires on draft save) which uploads to PyPI.
7. PyPI upload completion triggers `update_docs.yml` which dispatches `release-published` to `lex-app-docs`.
8. `lex-app-docs/auto-update-docs.yml` creates a docs issue, assigns Copilot, Copilot opens a docs PR.
9. `lex-app-docs/copilot_docs_pr_gate.yml` validates the docs PR and on green auto-merges it.

Two repos involved: `lex-app` (this one) and `lex-app-docs` (at `~/LUND_IT/lex-app-docs/`).

---

## State of the world right now

### lex-app `lex-app-v2` — last commits (this session, newest first)

| SHA | Subject | Why it matters |
|---|---|---|
| `64ebed7` | fix(ci): install cairo system deps in PR gate before pip install | **Pushed but unverified.** PR #65 gate run at 12:03 UTC still hit `Package 'cairo' not found` — needs eyeballing. See "Open issue 1" below. |
| `6b782e7` | fix(ci): match Copilot PR author login as 'Copilot' (not display name) | Critical bug fix — gate was skipping every Copilot PR because `user.login` is `Copilot`, not `copilot-swe-agent[bot]`. |
| `ff9693a` | fix(ci): use COPILOT_PAT in label bridge so labeled event propagates | Critical bug fix — events from `GITHUB_TOKEN` don't fire other workflows. Bridge now uses PAT. |
| `341ab7a` | docs(ci): add docs PR gate + auto-merge to automated pipeline | Adds `docs/ci-cd/docs-pr-gate.yml` stub + updates `automated-docs-pipeline.md`. |
| `e237cd1` | feat(ci): bridge issue-form Mode dropdown to GitHub label | The label-on-open bridge workflow. |
| `93fde11` | feat(ci): teach Copilot multi-cluster test decomposition | Prompt + gate updated to handle features spanning multiple clusters. |

### lex-app-docs `main` — last commits

| SHA | Subject |
|---|---|
| `60ba02d` | fix(ci): match Copilot PR author login as 'Copilot' (not display name) |
| `8537099` | feat(ci): add Copilot docs PR gate with auto-merge |

### Open Copilot PRs

| PR | State | Draft | Title |
|---|---|---|---|
| **#65** | OPEN | yes | Add regression test to enforce canonical calculation status values in API responses |
| #62 | CLOSED | yes | Add cluster 7m: regression gate for `is_calculated` never holding invalid status values |
| #56 | CLOSED | yes | Add serializer-contract regression coverage for calculation status values |

PR #65 is the active one for the demo. It's draft. Its gate run `26224324228` failed at 12:03 UTC with cairo-not-found despite the fix being pushed at 12:01 UTC.

### Open issues

| # | Labels | Title |
|---|---|---|
| 64 | (none) | `[copilot-task]` Sometimes I receive "No" in my calculation status |
| 63 | `copilot:regression` | Sometimes I receive "No" in my calculation status |

Issue #63 is the form-filed one (the one user files); #64 is the assembled prompt that the test-bot created and assigned Copilot to. This is the working pattern — both should always exist when the bot ran successfully.

---

## What works (verified green)

- ✅ **Issue form → label**: form-filed issue → `copilot_label_on_issue_open.yml` parses body → applies `copilot:<mode>`. Verified on issue #63 (`copilot:regression` was applied).
- ✅ **Label → test-bot fires**: with COPILOT_PAT in the bridge, the resulting `labeled` event triggers `copilot_test_bot.yml`. Verified — run `26221797506` succeeded and created issue #64.
- ✅ **Test-bot → Copilot agent**: copilot-swe-agent picks up the assigned task and starts coding. Verified — Copilot opened PR #65 (and earlier #62, #56).
- ✅ **PR-Gate `if:` matches Copilot author**: prior bug was comparing `user.login` to the wrong string; fix landed in `6b782e7` and the gate job now actually runs (not skipped).
- ✅ **Draft release auto-publishes to PyPI**: `pip_publish.yml:60` `release: [created]` fires on draft save (verified by code reading; not exercised end-to-end yet because no `publish-on-merge` PR has merged).
- ✅ **Lex-app gate auto-merge call exists**: `copilot_pr_gate.yml:267` calls `gh pr merge --auto --squash` (verified by code reading; not yet exercised because no PR has reached green yet).

## What's broken or pending verification

### 🔴 Open issue 1 — cairo deps fix may not be taking effect

PR #65's gate run `26224324228` (started 12:03:32 UTC) failed with `Package 'cairo' not found` even though the cairo-deps step was pushed at 12:01:48 UTC — fully 1m44s before the run started. `pull_request` workflows use the base-branch workflow file at event-processing time, so the fix *should* be in effect.

**Investigation steps:**
1. Confirm the gate workflow on origin really has the new step:
   ```bash
   gh api repos/:owner/:repo/contents/.github/workflows/copilot_pr_gate.yml --jq .content | base64 -d | grep -A4 "system dependencies"
   ```
2. Re-trigger PR #65 (close + reopen) to fire a fresh `pull_request: reopened` event:
   ```bash
   gh pr close 65 && gh pr reopen 65
   ```
3. If gate run still fails on cairo, check the run's "Install system dependencies" step explicitly — it should be step ~4 in the gate job. If it doesn't appear, the workflow was cached or the YAML didn't parse the new step.
4. As a sanity check, `showcase_tests.yml:154-159` has the exact same step that does work in prod. Copy it byte-for-byte if needed.

### 🟠 Open issue 2 — first-time-contributor approval gate

GitHub queues every Copilot PR's first workflow run in "approval required" state because `copilot-swe-agent` is a "new contributor." Each PR needs:
```bash
gh api repos/:owner/:repo/actions/runs/<RUN_ID>/approve -X POST
```
**Permanent fix:** Repo → Settings → Actions → General → "Approval for first-time contributors" → switch to "Do not require approval for any outside collaborators" (or allowlist `copilot-swe-agent[bot]`).

### 🟠 Open issue 3 — auto-merge requires PR ready-for-review

`gh pr merge --auto` rejects draft PRs. PR #65 is currently draft. Once the gate goes green, the PR has to be marked Ready for Review before auto-merge engages. Copilot usually flips it when its last push lands — if it doesn't, `gh pr ready 65`.

### 🟠 Open issue 4 — repo-level "Allow auto-merge" toggle

Both repos need: Settings → General → Pull Requests → tick **Allow auto-merge**. Without it, `gh pr merge --auto` is rejected outright. Confirm enabled on both `lex-app` AND `lex-app-docs`.

### 🟠 Open issue 5 — branch protection on `lex-app-v2`

For auto-merge to actually fire on green, the required-checks list on the branch protection rule must include the gate's check (`Copilot PR Gate / gate`). If no required checks are set, auto-merge merges immediately on creation — usually not what you want. If stale required checks exist (e.g. from an old workflow), they never report and auto-merge waits forever.

### 🟠 Open issue 6 — `COPILOT_AUTO_PUBLISH_ENABLED` repo variable

For the publish-on-merge path to fire, repo variable (not secret) `COPILOT_AUTO_PUBLISH_ENABLED` must be set to literal string `"true"`. Confirm in Settings → Secrets and variables → Actions → Variables.

### 🟠 Open issue 7 — docs PR gate untested end-to-end

`copilot_docs_pr_gate.yml` is pushed to `lex-app-docs/main` but has never actually fired (no PyPI publish has happened this session). The first end-to-end test will happen when a `publish-on-merge` PR merges and the chain fires through PyPI → dispatch → docs issue → Copilot → docs PR → gate.

---

## Required pre-flight checklist (BEFORE the presentation)

Tick each item before the demo:

- [ ] Repo `lex-app` → Settings → Actions → Approval for first-time contributors → "Do not require"
- [ ] Repo `lex-app` → Settings → General → Pull Requests → "Allow auto-merge" enabled
- [ ] Repo `lex-app` → Settings → Branches → branch protection rule for `lex-app-v2` includes `Copilot PR Gate / gate` as required check (or no required checks at all if you want immediate merge)
- [ ] Repo `lex-app` → Settings → Secrets and variables → Variables → `COPILOT_AUTO_PUBLISH_ENABLED` = `"true"` (only needed for the publish-on-merge demo)
- [ ] Repo `lex-app` → Settings → Secrets → `COPILOT_PAT` exists (used by both `copilot_test_bot.yml` and now `copilot_label_on_issue_open.yml`)
- [ ] Repo `lex-app-docs` → all four of the above mirrored (auto-merge, branch protection, COPILOT_PAT)
- [ ] Copilot coding agent enabled at org level AND on both repos
- [ ] PR #65 gate run is green (or a freshly-filed demo issue takes its place)
- [ ] **Verify cairo fix is actually in effect — see Open issue 1**

---

## Files map — where each piece lives

### Workflow files (production)
| File | What it does | Triggered by |
|---|---|---|
| `.github/workflows/copilot_label_on_issue_open.yml` | Bridges form Mode → label | `issues: opened` |
| `.github/workflows/copilot_test_bot.yml` | Assembles prompt, files copilot-task issue, assigns Copilot | `issues: labeled` (copilot:*) or `workflow_dispatch` |
| `.github/workflows/copilot_pr_gate.yml` | PR shape + test run + auto-merge or human-review label | `pull_request: [opened, synchronize, reopened, ready_for_review]` |
| `.github/workflows/copilot_publish_after_merge.yml` | Cuts draft rc release after a `publish-on-merge` PR merges | `pull_request: closed` (merged=true) |
| `.github/workflows/pip_publish.yml` | Builds + uploads to PyPI | `release: [created]` (fires on draft save) |
| `.github/workflows/update_docs.yml` | Dispatches `release-published` to lex-app-docs | `workflow_run` of "Publish to PyPI" |
| `.github/workflows/showcase_tests.yml` | Reference for cairo deps + Postgres setup — copy patterns from here | (not part of test-bot path) |

### Workflow files in lex-app-docs
| File | What it does |
|---|---|
| `.github/workflows/auto-update-docs.yml` | Receives dispatch, creates docs issue, assigns Copilot |
| `.github/workflows/copilot_docs_pr_gate.yml` | NEW — validates docs PR, auto-merges on green |
| `.github/workflows/auto-close-empty-pr.yml` | NOT YET DEPLOYED — stub is in lex-app `docs/ci-cd/docs-auto-close-empty-pr.yml` |

### Scripts
| File | Purpose |
|---|---|
| `.github/scripts/copilot_assemble_prompt.py` | Reads test-plan, builds the issue body Copilot sees |
| `.github/scripts/copilot_validate_pr_shape.py` | Static checks on what Copilot's PR contains (must include test file, etc.) |
| `.github/scripts/tests/test_copilot_assemble_prompt.py` | Pytest for the prompt assembler (40 tests passing) |

### Issue templates
| File | Purpose |
|---|---|
| `.github/ISSUE_TEMPLATE/copilot-test-request.yml` | The form the user fills in to kick everything off |

### Docs
| File | Purpose |
|---|---|
| `docs/ci-cd/copilot-test-bot.md` | Primary public doc for the test-bot |
| `docs/ci-cd/automated-docs-pipeline.md` | Primary public doc for the docs-update pipeline |
| `docs/ci-cd/ci-overview.md` | High-level map of all CI workflows |
| `docs/ci-cd/docs-receiver-workflow.yml` | Stub copy of `lex-app-docs/auto-update-docs.yml` |
| `docs/ci-cd/docs-auto-close-empty-pr.yml` | Stub copy — not yet deployed to lex-app-docs |
| `docs/ci-cd/docs-pr-gate.yml` | Stub copy of `lex-app-docs/copilot_docs_pr_gate.yml` |
| **`docs/ci-cd/copilot-test-bot-handoff-2026-05-26.md`** | **This file** |

### Test-plan files (Copilot reads these via pointer, not inline)
| File | Purpose |
|---|---|
| `lex/test_project/test-plan/index.md` | Project structure + naming conventions |
| `lex/test_project/test-plan/test-clusters.md` | Cluster registry (1=init, 2=crud_api, ..., 15=others) |
| `lex/test_project/test-plan/test-writing-plan.md` | Per-batch test plan rows |
| `lex/test_project/test-plan/progress/conventions.md` | Test intent / writing rules |
| `lex/test_project/test-plan/known-bugs.md` | Output of bug-repro mode |
| `lex/test_project/test-plan/progress/session-log.md` | Auto-appended by Copilot per session |

---

## Key facts to remember (gotchas hardcoded in your head)

1. **The Copilot bot's `user.login` is literally `Copilot`** — not `copilot-swe-agent[bot]`. The latter is a display name. Every `if: user.login ==` check must accept both forms.
2. **`GITHUB_TOKEN`-triggered events do not fire other workflows.** Cross-workflow chains need a PAT (we use `COPILOT_PAT`).
3. **Issue form's Mode dropdown can't apply a label directly** — GitHub limitation. Hence the bridge workflow.
4. **`release: [created]` fires on draft save** — not `published`. We deliberately don't listen on `published` because flipping a draft to published later would double-fire.
5. **`pull_request` workflows use the base-branch's workflow YAML at event time** — not the PR head's. So pushing a fix to `lex-app-v2` immediately affects all subsequent PR events; you don't need the PR to rebase.
6. **`gh pr merge --auto` rejects draft PRs.** PR must be ready-for-review for auto-merge to engage.
7. **Form-filed issue title prefix `[copilot-test] ` is not required** by the bridge anymore — it gates on the body containing `### Mode` instead, so users can edit the title freely.
8. **Each cluster has its own `models.py`** — there is no central `lex/test_project/models/` directory. Test files inside a cluster folder follow `test_<Nx>_<slug>.py` (e.g. `test_7m_valid_statuses.py`).

---

## How to continue (exact next actions)

### Step 1 — Verify the cairo fix actually landed in the gate
```bash
cd ~/Documents/lex/.claude/worktrees/cool-herschel
gh api repos/:owner/:repo/contents/.github/workflows/copilot_pr_gate.yml --jq .content | base64 -d | grep -A4 "system dependencies"
```
You should see the `libcairo2-dev pkg-config libpango-1.0-0 libpangoft2-1.0-0` block. If you don't, something went wrong with the push — re-edit and re-push.

### Step 2 — Re-trigger PR #65 to pick up the cairo fix
```bash
gh pr close 65 && gh pr reopen 65
```
Then in another shell, watch:
```bash
gh run watch $(gh run list --workflow=copilot_pr_gate.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```
If approval gate prompts, approve:
```bash
gh api repos/:owner/:repo/actions/runs/<NEW_RUN_ID>/approve -X POST
```

### Step 3 — If the gate goes green, mark PR ready, watch it merge
```bash
gh pr ready 65
gh pr view 65 --json mergeStateStatus,mergedAt
```
On merge, `copilot_publish_after_merge.yml` will fire IF the PR has `auto-merge` + `publish-on-merge` labels. PR #65 only has `auto-merge` (no publish-on-merge box was ticked), so the publish chain won't fire for this PR. That's fine for the demo — you can demo the test-bot path and the publish path separately.

### Step 4 — Run the publish-on-merge end-to-end demo
File a fresh form issue:
- Mode: `regression`
- Behaviour: anything trivial (e.g. "verify SimpleItem CRUD works")
- **Tick "Cut a draft `rc` release after merge"**

Watch the whole chain fire. If it works, you've got the demo for Tuesday.

### Step 5 — Docs pipeline demo (lex-app-docs side)
The docs path only fires when PyPI receives a publish. So step 4 must complete first. Then:
```bash
# Watch the docs repo for the auto-created issue
gh -R ExcellenceCloudGmbH/lex-app-docs issue list --limit 3 --label automated
# And the docs PR
gh -R ExcellenceCloudGmbH/lex-app-docs pr list --state all --limit 3
```

---

## Presentation talking points (matched to the manager's notes)

The German tasks list emphasized FOUR things, mapped here:

1. **"Update Documentation via Automation"** — demo this with step 5 above. The lex-app-docs path is fully wired but UN-EXERCISED. Run the publish-on-merge demo (step 4) to trigger it live during the talk. Backup if it fails: the `docs/ci-cd/automated-docs-pipeline.md` arch diagram + the existing merged docs PR #18 in lex-app-docs (`8537099`) demonstrate prior successful runs.

2. **"Create Test Automation Examples"** — point at PR #65 (the active one) + PR #56 / #62 (closed but show prior iterations). Each PR includes the Copilot-authored test file under `lex/test_project/tests/<cluster>/test_<Nx>_<slug>.py`. For "catch small commits from the last few weeks", show the form filing a regression issue against a recent commit.

3. **"Local Development Alignment"** — limited progress this session. Note: the agent's prompt is assembled from `lex/test_project/test-plan/*` files via `copilot_assemble_prompt.py`. Same files are readable locally; same conventions are enforced by the PR gate's `copilot_validate_pr_shape.py`. No "local Copilot" exists — Copilot only runs in GitHub Actions.

4. **"Complete Copilot PR Check"** — the gate (`copilot_pr_gate.yml`) is fully implemented:
   - PR-shape check → must include a test file at the right path
   - Discovers mode from linked issue
   - Runs new test in isolation via `lex test <module>` (multi-module supported for regression mode)
   - On green → applies `auto-merge` label + `gh pr merge --auto --squash`
   - On failure → applies `copilot:invalid` + posts a comment

   What's UNVERIFIED is whether it green-passes a real test run because of the cairo gap. Step 1-2 above must succeed before you can claim "block PR if test fails" works end-to-end.

---

## Known unknowns / deferred items

From prior sessions, never addressed:
- **B2/B3 in test-bot plan**: `gh release list --limit 50` lookback window + `createdAt`-vs-semver rc ordering edge cases.
- **T8 H1 hygiene**, **T10/T12 Importants** in `docs/superpowers/plans/2026-05-13-copilot-test-bot.md` — these are sub-items in the original implementation plan that were never completed.
- **Contract test fixture rename** `cluster_5/`/`cluster_7/` → real names — there's at least one test still using the placeholder names.
- **The two existing untracked files in `lex-app-docs/.github/workflows/`** — `auto-close-empty-pr.yml` stub exists in `lex-app/docs/ci-cd/` but was never deployed to lex-app-docs.

---

## Quick contact map for "I can't find X"

- **Memory file index:** `~/.claude/projects/-home-syscall-Documents-lex/memory/MEMORY.md`
- **Prior session transcript:** `~/.claude/projects/-home-syscall-Documents-lex--claude-worktrees-cool-herschel/92d64972-c214-4260-ac66-6d10c0631b65.jsonl`
- **Original plan:** [`docs/superpowers/plans/2026-05-13-copilot-test-bot.md`](../superpowers/plans/2026-05-13-copilot-test-bot.md)
- **Original spec:** [`docs/superpowers/specs/2026-05-13-copilot-test-bot-design.md`](../superpowers/specs/2026-05-13-copilot-test-bot-design.md)
- **lex-app-docs path:** `~/LUND_IT/lex-app-docs/`
