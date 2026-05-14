# Copilot Test-Bot

> **Owner:** CI/CD
> **Repos involved:** `lex-app` (single-repo — no cross-repo dispatch)
> **Last updated:** 2026-05-13

---

## What it does

File an issue describing a behaviour or bug, label it with a `copilot:<mode>` label, and Copilot will:

1. Read the issue + the project's existing test conventions (`lex/test_project/test-plan/`).
2. Write a test that codifies the behaviour, following the Golden Rule (*test what the framework is **trying** to achieve, not what the current code happens to do*).
3. Run the test against current code.
4. Open a PR.
5. **Auto-merge** the PR when CI is green (modes `regression` and `bug-repro`), or **route to human review** (mode `fix-and-test`).
6. Optionally cut a draft `rc` release after merge — which flows into the existing `pip_publish.yml` → PyPI.

---

## Modes

| Mode | Label | What Copilot does | Auto-merge? | Source changes? |
|---|---|---|---|---|
| **regression** | `copilot:regression` | Writes a **passing** test for the described behaviour | yes (green CI) | none |
| **bug-repro** | `copilot:bug-repro` | Writes an `@expectedFailure` test that reproduces a bug; appends a row to `known-bugs.md` | yes (green CI) | none |
| **fix-and-test** | `copilot:fix-and-test` | Writes a failing test, then makes the smallest source change that makes it pass | **no — human review** | ≤ 50 lines, listed in PR body |

---

## How to file an issue

1. Open a new issue using the *"Copilot Test Request"* template (`.github/ISSUE_TEMPLATE/copilot-test-request.yml`).
2. Pick a mode in the dropdown — the workflow rejects issues with no mode or two modes.
3. Fill the **Behaviour description** (required) and **Reproducer** (required for `bug-repro`/`fix-and-test`).
4. *(Optional)* **Cluster hint** — `7g`, `7`, `new`, `others`, or blank.
5. *(Optional)* **Files involved** — comma-separated paths.
6. *(Optional)* Check the **Publish on merge** box to cut a draft `rc` release after auto-merge.
7. Submit the issue. The workflow reads the label and dispatches Copilot.

If validation fails (e.g. blank reproducer in `bug-repro`), the workflow labels the issue `copilot:invalid` and comments with what's missing. Edit the issue and re-add the mode label to retry.

---

## Cluster routing (fallback chain)

The hint resolution rule, applied in order:

1. **Existing letter** (`7g`, `12e`) → use it; if taken, advance to the next free letter.
2. **Number only** (`7`) → allocate the next free sub-cluster letter inside it.
3. **`new`** → create a brand-new cluster; pick the next free number; register in `test-clusters.md`, `showcase_clusters.py`, and the `pip_publish.yml` + `showcase_tests.yml` default selectors.
4. **`others` or blank** AND no existing cluster fits → place under `lex/test_project/tests/others/`.

Copilot writes a one-sentence justification in the PR description: *"Placed under cluster Nx because …"*.

---

## PR-gate checks

`copilot_pr_gate.yml` runs three checks on every PR Copilot opens; failure on any one of them blocks merge:

1. **Mode discovery** — the PR body must contain `Fixes #N`; the linked issue must have exactly one `copilot:<mode>` label.
2. **PR-shape contract** (`copilot_validate_pr_shape.py`) — file set, naming, body markers; per-mode required artifacts (see §7 of [the design spec](../superpowers/specs/2026-05-13-copilot-test-bot-design.md)).
3. **Run the new test** — modes A/C must pass; mode B is re-run with `@expectedFailure` stripped on a temp copy and must FAIL (otherwise the claimed bug is not reproducible).

Branch protection separately requires `showcase_tests / Run cluster showcase & send report` — the gate workflow does not invoke that itself.

---

## Publish on merge

`copilot_publish_after_merge.yml` watches for merged Copilot PRs with both `auto-merge` and `publish-on-merge` labels. When both are set AND the repo variable `COPILOT_AUTO_PUBLISH_ENABLED == "true"`:

1. Find the latest `vX.Y.ZrcN` release tag.
2. Bump to `vX.Y.Zrc(N+1)` (rc-only — never major/minor/patch).
3. Create a **draft** release with release notes listing every PR merged since the previous tag.
4. The existing `pip_publish.yml` fires on `release: created` and finishes the publish.

Default is **off** — flip `COPILOT_AUTO_PUBLISH_ENABLED` to `"true"` only after at least one regression-mode round-trip has been observed end-to-end. The draft step exists as an audit artifact in the GitHub Releases UI; `pip_publish.yml` triggers on `release: created` (which fires for drafts too), so PyPI upload begins immediately and there is no reliable abort window between draft creation and publish. Per-run aborts must happen by flipping `COPILOT_AUTO_PUBLISH_ENABLED` to `"false"` before merging the PR.

---

## Configuration prerequisites

| Setting | Where | Value |
|---|---|---|
| Issue template | `.github/ISSUE_TEMPLATE/copilot-test-request.yml` | Shipped with this PR |
| Labels | Repo Settings → Labels | `copilot:regression`, `copilot:bug-repro`, `copilot:fix-and-test`, `copilot:invalid`, `auto-merge`, `needs-human-review`, `publish-on-merge` |
| Copilot coding agent enabled on `lex-app` | Org → Copilot → Policies | On |
| Branch protection on `lex-app-v2` | Repo Settings → Branches | Required check: `showcase_tests`; `copilot-swe-agent[bot]` in the bypass list; auto-merge enabled at repo level |
| Repo secret `COPILOT_PAT` | Repo Settings → Secrets | PAT able to assign `copilot-swe-agent[bot]` (mirror from `lex-app-docs`) |
| Repo variable `COPILOT_AUTO_PUBLISH_ENABLED` | Repo Settings → Variables | `"false"` initially |

---

## Failure modes and recovery

| Symptom | Cause | Recovery |
|---|---|---|
| Issue labeled `copilot:invalid` immediately | Two mode labels, blank behaviour, or missing reproducer for B/C | Edit the issue, fix the field, re-add the mode label |
| Copilot's PR labeled `copilot:invalid` | PR-shape check failed — see the PR comment for the list | Re-trigger by closing the PR and re-adding the mode label to the original issue, OR fix manually |
| Mode-B PR blocked: "test passed without @expectedFailure" | The bug being claimed is no longer reproducible | Close the issue (the bug may already be fixed) or re-file as `copilot:regression` |
| `showcase_tests` red on auto-merge PR | New test depends on a missing fixture or surfaces a flake | PR sits open; same triage as any human-authored PR |
| Two Copilot runs append `progress/session-log.md` simultaneously | Append-only conflict | Second run retries cleanly after the first lands |

---

## Design choices — why each piece looks the way it does

### Why three workflows instead of one?

Each workflow listens to a different GitHub event (`issues: labeled`, `pull_request`, `pull_request: closed`). Folding them into one would require polling. Splitting them keeps each workflow's `on:` block narrow and its permission set minimal.

### Why assemble the prompt at runtime?

`copilot_assemble_prompt.py` reads four files from `lex/test_project/test-plan/` every run. When the test-plan rules evolve, edit the test-plan docs — the next workflow run sees the new wording automatically. No prompt-versioning to maintain in YAML.

### Why split `progress.md`?

The original `progress.md` mixed a high-churn dashboard table + Known Bugs Tracker with stable methodology + run instructions. Every session edit was a merge-conflict candidate, and the Copilot PR-shape check could not enforce "append one row" mechanically. The split (`progress/conventions.md`, `progress/dashboard.md`, `progress/session-log.md`) mirrors the volatility — each PR touches the smallest file.

### Why does mode B strip the decorator and assert failure?

A `@expectedFailure` test is reported as passing whether the body raises or not — so a Copilot mistake (e.g. an `assert True`) would land as a "reproduces BUG-NNN" gate-green merge. Re-running the body with the decorator stripped forces an actual reproduction check.

### Why no auto-merge on mode C?

Mode C's PR ships a behaviour change. Auto-merge would mean "Copilot fix lands with no human reading the diff". The 50-line cap + required `### Source changes` body section reduce review effort but never replace the human read.

### Why a draft release on the publish path?

The draft surfaces in the GitHub Releases UI with the auto-generated notes — an audit artifact for after-the-fact review. Note: `pip_publish.yml` triggers on `release: created`, which fires for draft releases too, so PyPI publish begins immediately and there is no reliable abort window between draft and upload. Per-run aborts must happen by flipping `COPILOT_AUTO_PUBLISH_ENABLED` to `"false"` before merging the PR; the draft is for visibility, not a manual gate.

### Why rc-only on publish bumps?

x/y/z bumps are product decisions — a release with no humans reading the diff is not the right place to make them. The publish path explicitly refuses any non-rc current tag (`compute_next_rc` raises `ValueError`).

### Why a PAT for Copilot assignment?

The Copilot coding agent requires a PAT to be assigned to an issue — `GITHUB_TOKEN` and GitHub App tokens both lack the needed scope today. This is the same restriction `lex-app-docs` already lives with for `auto-update-docs.yml`. Reused, not added.

---

> **See also:** [Automated Documentation Pipeline](automated-docs-pipeline.md) | [CI Overview](ci-overview.md) | [Design Spec](../superpowers/specs/2026-05-13-copilot-test-bot-design.md)
