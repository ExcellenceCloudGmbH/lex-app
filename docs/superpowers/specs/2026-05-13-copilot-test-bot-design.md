# Copilot Test-Bot — Design Spec

> **Status:** Approved (brainstorming session, 2026-05-13)
> **Owner:** CI/CD
> **Repos involved:** `lex-app` (single-repo design — no cross-repo dispatch)
> **Implements:** issue-driven Copilot test authorship with auto-merge for low-risk modes and an opt-in publish path

---

## 1. Goal

Let any contributor file an issue describing a behavior or bug, and have GitHub Copilot:

1. Read the issue and the project's existing test conventions.
2. Write a test that codifies the behavior, following the rules in `lex/test_project/test-plan/`.
3. Run the test against current code.
4. Open a PR.
5. Auto-merge the PR when CI is green (test-only modes), or hand off to a human reviewer (fix-and-test mode).
6. Optionally trigger a draft release that flows through the existing `pip_publish.yml` to PyPI.

The workflow exists to **scale test authorship without sacrificing the test plan's anti-overfitting discipline**. The Golden Rule from `test-plan/index.md` — *test what the framework is trying to achieve, not what the current code happens to do* — is not negotiable; the prompt enforces it explicitly.

## 2. Modes

Three modes, selected per issue via a label. The mode is mandatory; the workflow rejects issues with no mode label or two mode labels.

| Mode | Label | What Copilot does | Auto-merge? | Source changes? |
|---|---|---|---|---|
| **A — regression** | `copilot:regression` | Writes a passing test for the described behavior | yes (on green CI) | none |
| **B — bug-repro** | `copilot:bug-repro` | Writes a `@expectedFailure`-decorated test that reproduces a bug; appends a row to `known-bugs.md` | yes (on green CI) | none |
| **C — fix-and-test** | `copilot:fix-and-test` | Writes a failing test, then makes the smallest source change to pass it | **no — human review required** | minimal, listed in PR body |

### Mode-A safeguard (test must actually pass)

Mode A's "if green, merge" semantics depend on the new test being non-trivially passing. The PR-gate workflow runs the new test in isolation as part of its checks (not just relies on `showcase_tests`).

### Mode-B safeguard (test must actually fail)

Mode B is structurally weird: the merged commit contains a test that's expected to fail. The PR-gate workflow strips the `@expectedFailure` decorator in a temp copy, runs the test, and asserts it **fails**. If a mode-B test passes without the decorator, the bug being claimed doesn't exist (or Copilot wrote the wrong test). Block the merge.

### Mode-C safeguard (no auto-merge)

Mode C's PR ships a behavior change. Auto-merge would mean "Copilot fix lands in `lex-app-v2` with no human reading the diff". That's the wrong default. The PR-gate workflow applies `needs-human-review` instead of `auto-merge`, and requests review from the maintainers team.

## 3. Architecture

```
lex-app                                   lex-app
───────                                   ───────
Issue opened/labeled                     workflow_dispatch
        │                                        │
        └──────────────┬─────────────────────────┘
                       ▼
            copilot_test_bot.yml         (entry point)
                       │
                       ├─ Validate trigger + mode
                       ├─ Resolve target cluster
                       ├─ Assemble prompt from test-plan/* docs
                       └─ Create Copilot-task issue, assign Copilot
                                         │
                                         ▼
                            Copilot writes test, opens PR
                                         │
                       ┌─────────────────┴─────────────────┐
                       ▼                                   ▼
            copilot_pr_gate.yml                 showcase_tests (required check)
                       │                                   │
                       ├─ Discover mode from issue link    │
                       ├─ Static PR-shape checks           │
                       ├─ Run new test (mode A/C)          │
                       ├─ Strip @expectedFailure + run     │
                       │   test asserting failure (mode B) │
                       └─ Apply auto-merge or              │
                          needs-human-review label         │
                                         │                 │
                                         └────► PR merges when both gates green
                                                           │
                       ┌───────────────────────────────────┘
                       │  publish-on-merge label set?
                       ▼
            copilot_publish_after_merge.yml
                       │
                       ├─ Compute next rc tag
                       ├─ Create draft release
                       └─ pip_publish.yml fires (release: created)
                                         │
                                         └─► full release pipeline → PyPI
```

**Three workflows in lex-app:**

- `copilot_test_bot.yml` — entry point, async hand-off to Copilot. Ends after issue creation.
- `copilot_pr_gate.yml` — runs on `pull_request` events from `copilot-swe-agent[bot]`. Validates shape, runs the new test, applies merge labels.
- `copilot_publish_after_merge.yml` — runs on `pull_request: closed` events when `merged == true` and the right labels are present. Creates a draft release.

Three separate workflows because each is event-driven on a different GitHub event and they can't be folded into one without polling.

## 4. Trigger and issue template

### Trigger shape

```yaml
on:
  issues:
    types: [labeled]
  workflow_dispatch:
    inputs:
      issue_number: { required: false, type: string }
      mode:         { required: true,  type: choice, options: [regression, bug-repro, fix-and-test] }
      publish:      { required: false, type: boolean, default: false }
```

The `issues: [labeled]` event fires for any label add. The first job filters down to the three mode labels. `workflow_dispatch` is the escape hatch for re-runs and operator-driven invocations.

### Issue template

`.github/ISSUE_TEMPLATE/copilot-test-request.yml` requires:

- **Title** (free-form)
- **Mode label** (one of three; workflow rejects if missing or ambiguous)
- **Behavior description** (free-form prose; required)
- **Reproducer / steps** (required for B and C, optional for A)
- **Cluster hint** (optional — `7`, `7g`, `new`, `others`, or blank)
- **Files involved** (optional)
- **Publish on merge** (checkbox, default off)

### Validation step

Before the prompt is assembled, the workflow checks:

- Exactly one mode label present.
- Behavior description is non-empty.
- Reproducer is non-empty for modes B and C.
- Cluster hint, if present, references an existing cluster number/letter or one of the literal keywords.

Failed validation → label issue `copilot:invalid` + post a comment listing what's missing. Issue stays open. Adding a fresh mode label after the fix re-triggers the workflow.

## 5. Cluster routing

The hint resolution rule, applied in order, first match wins:

1. Hint names an **existing cluster letter** (e.g. `7g`) → use it. If that letter is already taken, advance to the next free letter (rule from `test-writing-plan.md` §"Conventions").
2. Hint names just a **cluster number** (e.g. `7`) → allocate the next free sub-cluster letter inside it.
3. Hint is **`new`** → create a new cluster. Pick the next free cluster number, add a `test-clusters.md` entry, register in `.github/scripts/showcase_clusters.py`, update default selectors in `pip_publish.yml` + `showcase_tests.yml`.
4. Hint is **`others`** or **blank** AND no existing cluster fits → place under the `others/` directory (created if missing) with a generic numbering scheme.

Copilot must document the placement choice in the PR description with one sentence: *"Placed under cluster Nx because …"*. The reviewer (human for mode C, none for A/B) can correct via a re-trigger.

## 6. The prompt

The prompt is **assembled at runtime** by `copilot_assemble_prompt.py`, not hard-coded in the workflow YAML. The script reads four files and builds an issue body for the Copilot-task issue:

| Source file | Purpose |
|---|---|
| `lex/test_project/test-plan/index.md` (the Golden Rule box) | Anti-overfitting discipline, front-loaded |
| `lex/test_project/test-plan/test-clusters.md` | Cluster definitions + the testing philosophy section |
| `lex/test_project/test-plan/test-writing-plan.md` | File naming, scenario IDs, sub-cluster rules |
| `lex/test_project/test-plan/progress/conventions.md` | Style + structure rules (the stable bits — see §8) |

Plus per-mode instructions injected as one of three blocks (full text in §3 of the brainstorm transcript; lives in the script as a constant).

Plus the issue's own behavior description, reproducer, and cluster hint.

**Key prompt rules (mandatory, called out in the prompt):**

- Derive expectations from the test-plan docs and the issue, not from current source. Source is *evidence*, never *authority*.
- File naming: `lex/test_project/tests/<cluster_dir>/test_<Nx>_<slug>.py`.
- Test class naming: `TestCluster<NN><x>_<Thing>`.
- Scenario IDs continue from the cluster's current max; cluster numbers are never renumbered.
- Pick test type U / I / E from the criteria in `test-writing-plan.md`.
- Required deliverables in the PR (test file, `test-clusters.md` update, `progress/session-log.md` append, optional `known-bugs.md` row, optional new-cluster wiring).
- Touch nothing outside the allowed file set, except the source fix in mode C.

**Why the prompt assembly script and not a YAML here-doc:**

When the test-plan rules evolve, edit the test-plan docs. The next workflow run reads the new wording automatically. No prompt-versioning headache, no workflow-file edit needed for documentation tweaks.

## 7. PR-gate logic

`copilot_pr_gate.yml` runs on `pull_request: [opened, synchronize, reopened, ready_for_review]` for PRs authored by `copilot-swe-agent[bot]`.

Three checks, in order. Any failure → label PR `copilot:invalid`, post a comment, do not apply merge labels.

### Check 1 — Discover the mode

Read the linked issue (parsed from `Fixes #N` in the PR body). Extract the `copilot:*` label → mode. Fail if no link or no mode.

### Check 2 — PR-shape gate

Static checks against the PR diff (parsed via `gh pr diff` — no checkout needed). Per mode:

| Required artifact | A | B | C |
|---|---|---|---|
| New file under `lex/test_project/tests/<cluster>/test_<Nx>_*.py` | ✓ | ✓ | ✓ |
| `test-plan/test-clusters.md` modified (status / scenario range) | ✓ | ✓ | ✓ |
| `test-plan/progress/session-log.md` appended (no edits to other lines) | ✓ | ✓ | ✓ |
| `test-plan/known-bugs.md` has a new BUG-NNN row | — | ✓ | ✓ |
| **No** files changed outside `lex/test_project/`, `test-plan/`, `.github/workflows/`, `.github/scripts/showcase_clusters.py` | ✓ | ✓ | ✗ (source fix allowed) |
| For new-cluster placement: `showcase_clusters.py` + selectors in `pip_publish.yml` + `showcase_tests.yml` updated | conditional | conditional | conditional |
| Test class name matches `TestCluster<NN><x>_<Thing>` regex † | ✓ | ✓ | ✓ |
| `@unittest.expectedFailure` + `BUG-NNN` decorator present on the new test ‡ | — | ✓ | — |
| Source diff ≤ 50 changed lines AND listed in PR description | — | — | ✓ |

> **†** Convention only — `copilot_validate_pr_shape.py` does not parse test-class names; this is enforced by code review.
> **‡** Not a static check. Enforced indirectly by Check 3 mode B's strip-and-assert-failure pass: a missing decorator means the strip is a no-op and the test runs as-written; the gate fails if it does not assert its own failure.

### Check 3 — Run the new test

- **Mode A:** run the new test file in isolation. Must pass.
- **Mode B:** strip the `@expectedFailure` decorator in a temp copy, run, assert **failure**.
- **Mode C:** run the new test file in isolation. Must pass (after the source fix).

Then `showcase_tests` runs as the existing required check on `lex-app-v2` PRs, covering the full cluster suite. The PR-gate workflow does **not** invoke `showcase_tests` itself — branch protection enforces it as a required status check.

### Auto-merge label assignment

After all three checks pass:

```python
if mode in ("regression", "bug-repro"):
    apply_label("auto-merge")
    enable_auto_merge(squash=True)
elif mode == "fix-and-test":
    apply_label("needs-human-review")
    request_review_from(team="lex-maintainers")
```

Squash-merge keeps `lex-app-v2` history clean — one commit per Copilot batch.

## 8. progress.md decomposition

Today's `progress.md` is 288 lines of mixed-volatility content with two stale sibling files (`session-log.md`, `progress-session-log.md`) that look like earlier split attempts. The Copilot workflow's append discipline only works if the high-churn sections are isolated.

**Target layout:**

```
lex/test_project/test-plan/
├── index.md                  (existing — TOC, unchanged)
├── test-clusters.md          (existing — cluster definitions, unchanged)
├── test-writing-plan.md      (existing — naming/scenario rules, unchanged)
├── known-bugs.md             (existing — high-churn; Copilot appends in modes B/C)
├── progress.md               (REWRITTEN — thin index pointing to progress/)
└── progress/                 (NEW directory)
    ├── conventions.md        (extracted: stable methodology / organization / UX / how-to-run / quality gates)
    ├── session-log.md        (extracted: Session Log; replaces both stale siblings)
    └── coverage-tracker.md   (extracted: KPIs, % targets, ratchet rules)
```

The two stale sibling files are deleted in the same PR after their content is merged into `progress/session-log.md`. Single source of truth.

**Migration is part of v1, not deferred.** The Copilot prompt references `progress/conventions.md`. Shipping the workflow before the split would either reference a missing file or force Copilot to write to the monolithic `progress.md` and live with merge conflicts.

## 9. Optional publish path

`copilot_publish_after_merge.yml` watches `pull_request: closed` events with `merged == true` and the labels `auto-merge` + `publish-on-merge` both present.

Steps:

1. Read the latest release tag from `gh release list`. Refuse if it's not an rc (e.g. `v2.0.0` with no rc suffix) — manual release required.
2. Bump the rc suffix: `v2.0.0rc124` → `v2.0.0rc125`. **Only rc bumps from this path; never x/y/z.**
3. Resolve release notes by listing PRs merged since the previous release tag. Mark Copilot-bot PRs explicitly.
4. Create a **draft** GitHub release via `gh release create --draft`.
5. `pip_publish.yml` (already configured with `release: created` from the 2026-05-12 change) takes over.

**Default off.** The `publish-on-merge` label is opt-in per issue. A repo variable `COPILOT_AUTO_PUBLISH_ENABLED` (default `"false"`) is the kill switch — flip it to disable the whole path without editing YAML.

**Why draft, not direct publish:** the draft surfaces in the GitHub Releases UI with the auto-generated notes — useful as an audit artifact for after-the-fact review. Note: `pip_publish.yml` triggers on `release: created`, which fires for draft releases too, so PyPI publish begins immediately and there is no reliable abort window between draft creation and upload. Per-run aborts must happen by flipping `COPILOT_AUTO_PUBLISH_ENABLED` to `"false"` before merging the PR; the draft itself is for visibility, not a manual gate.

## 10. Configuration prerequisites

| Setting | Where | Value |
|---|---|---|
| Branch protection on `lex-app-v2` | Repo Settings → Branches | Required check: `showcase_tests / Run cluster showcase & send report`; required: 1 review for non-bot PRs; auto-merge enabled at repo level |
| Bot bypass for required reviews | Branch protection rule | Add `copilot-swe-agent[bot]` to "Allow specified actors to bypass required pull requests" |
| Labels created | Repo Settings → Labels | `copilot:regression`, `copilot:bug-repro`, `copilot:fix-and-test`, `copilot:invalid`, `auto-merge`, `needs-human-review`, `publish-on-merge` |
| Issue template | `.github/ISSUE_TEMPLATE/copilot-test-request.yml` | New file |
| Copilot coding agent enabled on `lex-app` | Org → Copilot → Policies | Currently only on `lex-app-docs`; mirror to `lex-app` |
| Repo variable `COPILOT_AUTO_PUBLISH_ENABLED` | Repo Settings → Variables | Default `"false"` |

## 11. Secrets

Reuse, don't add. Single new secret: `COPILOT_PAT` mirrored from `lex-app-docs` to `lex-app` — same restriction as the docs flow (Copilot agent assignment requires a PAT today; neither `GITHUB_TOKEN` nor a GitHub App token works for that one operation).

## 12. File inventory

```
.github/workflows/
   copilot_test_bot.yml                     (NEW)
   copilot_pr_gate.yml                      (NEW)
   copilot_publish_after_merge.yml          (NEW)

.github/ISSUE_TEMPLATE/
   copilot-test-request.yml                 (NEW)

.github/scripts/
   copilot_assemble_prompt.py               (NEW)
   copilot_validate_pr_shape.py             (NEW)
   copilot_compute_next_rc.py               (NEW)

lex/test_project/test-plan/
   progress.md                              (REWRITTEN — thin index)
   progress/
     conventions.md                         (NEW)
     session-log.md                         (NEW — replaces both stale siblings)
     coverage-tracker.md                    (NEW)
   session-log.md                           (DELETED)
   progress-session-log.md                  (DELETED)

docs/ci-cd/
   copilot-test-bot.md                      (NEW — user-facing how-it-works doc)
```

## 13. The user-facing doc

`docs/ci-cd/copilot-test-bot.md` is a deliverable, not optional. It mirrors the structure of `automated-docs-pipeline.md` so it sits alongside the existing CI docs. Contents:

- What the bot does (modes A/B/C, when to use each).
- How to file an issue (the template fields, the labels, examples).
- The cluster routing fallback chain explained.
- The publish toggle and when to use it.
- Configuration prerequisites (mirror of §10).
- Failure modes and recovery (the four named in §15 below).
- Why each design choice was made — the trade-off reasoning from this brainstorm verbatim. The "why" is the part that survives code changes.

## 14. Out of scope for v1

- Auto-comment-on-failure (Copilot pinged when CI goes red on its own PR).
- Re-running Copilot on label re-add when a previous run already produced a PR.
- Cross-repo coordination (no `lex-app-frontend` or `lex-app-docs` involvement here).
- Mode D ("Copilot improves an existing flaky test").
- A web dashboard. The audit trail is GitHub issues + PRs + the `progress/session-log.md` append log.

## 15. Failure modes

1. **Wrong cluster placement** → caught at PR-gate Check 2 (file path regex) → label `copilot:invalid` + comment with the right cluster → re-trigger.
2. **Mode-B test that doesn't actually fail** → caught at PR-gate Check 3 (decorator-stripped test must fail). Block merge.
3. **Two simultaneous Copilot runs** → both append to `progress/session-log.md`. Append-only + decomposition mean text-level conflicts only when both runs land in the same second. Acceptable; second run retries.
4. **Test depends on missing fixture** → `showcase_tests` red → PR sits open for human triage. Same as today's manual process.

## 16. Implementation order

The implementation plan (out of scope for this spec — produced separately by `writing-plans`) should sequence:

1. **progress.md decomposition** — must land first; later workflows reference `progress/conventions.md`.
2. **Issue template + labels + repo config** — pre-requisites for the workflows to behave correctly.
3. **`copilot_test_bot.yml` + `copilot_assemble_prompt.py`** — entry-point workflow + prompt assembly. Test by manual `workflow_dispatch` against a sample issue before turning on label triggers.
4. **`copilot_pr_gate.yml` + `copilot_validate_pr_shape.py`** — gate workflow. Test against a synthetic Copilot-shaped PR.
5. **`copilot_publish_after_merge.yml` + `copilot_compute_next_rc.py`** — publish path. Land with `COPILOT_AUTO_PUBLISH_ENABLED=false` and only flip on after at least one full A→merge cycle has been observed.
6. **`docs/ci-cd/copilot-test-bot.md`** — user-facing doc. Lands with the workflows, not after.
