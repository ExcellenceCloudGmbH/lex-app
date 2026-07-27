# Presentation Brief — Release Pipeline & Copilot Test-Bot

> **Date:** 28 May 2026
> **Audience:** Internal review of the release automation and the Copilot test authorship feature.
> **Scope:** What actually ships today (workflow code, not design intent). Sections 1–2 are the things to know cold; section 3 is the honest "what's still soft".

---

## 1. Release pipeline — when does it run, in which mode, and why

### 1.1 GitHub's four release "modes" (and why they aren't actually four)

GitHub's Releases UI exposes two orthogonal checkboxes when you cut a release:

| Checkbox | Meaning | Affects PyPI publish? |
|---|---|---|
| **Set as a pre-release** | Pure metadata flag — surfaces a "pre-release" badge in the UI and excludes the release from "latest". | **No.** GitHub fires the same `release.created` event either way. |
| **Set as a draft** | The release exists but is not visible publicly and no release event is emitted. | **Yes — silence.** No event fires until the draft is flipped to published. |

So there are really only **two states that matter to our pipeline**:

1. **Non-draft** (whether or not it's marked pre-release) → `release.created` fires immediately.
2. **Draft** → no event; sits silent until a maintainer clicks *Publish release*, which then fires `release.created`.

Both [`pip_publish.yml`](../../.github/workflows/pip_publish.yml) (lines 53–60) and [`update_docs.yml`](../../.github/workflows/update_docs.yml) document this contract explicitly. The pre-release flag is **invisible to the pipeline** — it ships to PyPI either way.

### 1.2 What actually runs when

| Action in GitHub UI | `release.created`? | PyPI publish? | Docker image build? | Docs PR? |
|---|---|---|---|---|
| Create non-draft release (stable, e.g. `v2.1.0`) | ✓ | ✓ | ✓ on success | ✓ on success |
| Create non-draft pre-release (e.g. `v2.0.0rc125`) | ✓ | ✓ | ✓ on success | ✓ on success |
| **Create draft release (any kind)** | ✗ | ✗ | ✗ | ✗ |
| Edit existing draft → click *Publish release* | ✓ | ✓ | ✓ on success | ✓ on success |
| Flip an existing published pre-release to "latest" | `released` only, not `created` | ✗ | ✗ | ✗ |

Source of truth: [`pip_publish.yml:53-60`](../../.github/workflows/pip_publish.yml). Filter `types: [created]` is deliberate — it means each release ships to PyPI **exactly once**, at the moment it becomes a real (non-draft) release.

### 1.3 Why we landed on this trigger choice

The relevant decision is documented in [`pip_publish.yml`](../../.github/workflows/pip_publish.yml) (header) and [`copilot_publish_after_merge.yml`](../../.github/workflows/copilot_publish_after_merge.yml) (header). Earlier the auto-publish path created the release with `--draft`. The chain silently never ran, because draft creation emits no event. Fix: cut releases **non-draft** programmatically, even when the intent is "low risk / rc-level".

### 1.4 The downstream chain — once `release.created` fires

```
GitHub release (non-draft)
        │  release.created
        ▼
pip_publish.yml
   ├─ gate-release  → showcase_tests.yml (tests + Platform Health email — always sent)
   └─ publish       → resolve version from tag, write lex/_version.py, build, upload to PyPI
        │           (skipped if gate-release fails)
        │
        │  workflow_run: completed
        ▼
   ├─ custom-image.yml   (Docker build + push to GCP Artifact Registry; tags release tag + `latest` if v2)
   └─ update_docs.yml    (repository_dispatch to lex-app-docs → Copilot opens docs PR)
        (both gated on `workflow_run.conclusion == 'success'`)
```

Concretely: **one click in GitHub Releases triggers all four downstream steps**. There is no manual gate between "release created" and "PyPI upload begins". If you create a release with the wrong tag, you have **no abort window**.

### 1.5 Likely questions and answers

| Q | A |
|---|---|
| *"What's the difference between draft and pre-release?"* | Draft = invisible + no event fires. Pre-release = visible with a badge + same event as a stable release. The pipeline ignores the pre-release flag. |
| *"Then why use pre-release at all?"* | UX: the GitHub Releases page shows a clear "pre-release" label and excludes the release from "latest", so customers checking the latest stable release won't accidentally pick up an rc. |
| *"Can we cut a release without publishing to PyPI?"* | Yes — create as draft. Nothing fires. To then publish, flip it to non-draft and the full chain runs. |
| *"What if I create a draft pre-release?"* | Same as draft — silent. The pre-release flag doesn't override draft. |
| *"Can I publish to PyPI without cutting a GitHub release?"* | Yes — `workflow_dispatch` on `pip_publish.yml` accepts a `version` input and runs the same `publish` job. Skips the `release.created` path entirely. Used for ad-hoc reruns. |
| *"What about the Copilot auto-publish path?"* | [`copilot_publish_after_merge.yml`](../../.github/workflows/copilot_publish_after_merge.yml) bumps the latest `rc` suffix and calls `gh release create` non-draft. So a Copilot PR with both `auto-merge` + `publish-on-merge` labels can ship to PyPI without any human in the loop — gated by repo variable `COPILOT_AUTO_PUBLISH_ENABLED` (default `"false"`). |
| *"How is the version determined?"* | The GitHub release tag, stripped of the leading `v` ([`pip_publish.yml:135-153`](../../.github/workflows/pip_publish.yml)). The version in `pyproject.toml` is dynamic and reads `lex/_version.py`, which the workflow overwrites at build time. So you control the version by naming the tag — nowhere else. |

---

## 2. Copilot test-bot — what works, what's soft, what to demo

### 2.1 What's actually shipped on disk

All three workflows exist and are wired:

| File | Triggers on | Status |
|---|---|---|
| [`copilot_test_bot.yml`](../../.github/workflows/copilot_test_bot.yml) | `issues: labeled` (mode label) or `workflow_dispatch` | Functional — validates issue, builds prompt, files Copilot-task issue and assigns `copilot-swe-agent`. |
| [`copilot_pr_gate.yml`](../../.github/workflows/copilot_pr_gate.yml) | `pull_request` on PRs authored by `Copilot` | Functional — discovers mode, runs PR-shape validator, runs the new test, applies merge labels. |
| [`copilot_publish_after_merge.yml`](../../.github/workflows/copilot_publish_after_merge.yml) | `pull_request: closed` with `merged == true` + both labels | Functional but **gated off** by `COPILOT_AUTO_PUBLISH_ENABLED == "true"`. |

Supporting pieces also shipped:

- Issue template: [`.github/ISSUE_TEMPLATE/copilot-test-request.yml`](../../.github/ISSUE_TEMPLATE/copilot-test-request.yml) ✓
- Scripts: `copilot_assemble_prompt.py`, `copilot_validate_pr_shape.py`, `copilot_compute_next_rc.py`, `copilot_coverage_detect.py` — all present under [`.github/scripts/`](../../.github/scripts/).
- User-facing doc: [`docs/ci-cd/copilot-test-bot.md`](copilot-test-bot.md).
- Design spec: [`docs/superpowers/specs/2026-05-13-copilot-test-bot-design.md`](../superpowers/specs/2026-05-13-copilot-test-bot-design.md).
- Plan: [`docs/superpowers/plans/2026-05-13-copilot-test-bot.md`](../superpowers/plans/2026-05-13-copilot-test-bot.md).

### 2.2 The three modes — what each one does

| Mode | Label | Copilot writes | Gate runs | Auto-merge? |
|---|---|---|---|---|
| **A — regression** | `copilot:regression` | A passing test for the described behaviour | Runs new test → must **pass** | ✓ on green |
| **B — bug-repro** | `copilot:bug-repro` | An `@expectedFailure`-decorated test + a `known-bugs.md` row | Strips the decorator on a temp copy → must **FAIL** (else the bug isn't reproducible) | ✓ on green |
| **C — fix-and-test** | `copilot:fix-and-test` | A failing test + a `known-bugs.md` row + the smallest source change that makes it pass | Runs new test → must **pass** (after the fix) | ✗ — `needs-human-review` label + reviewer request |

### 2.3 What clearly works

1. **End-to-end flow for mode A.** Label an issue `copilot:regression`, Copilot opens a PR, PR-gate runs the new test, merge label applied. This is the demo-ready happy path.
2. **The mode-B negative test.** [`copilot_pr_gate.yml:222-270`](../../.github/workflows/copilot_pr_gate.yml) strips `@expectedFailure` on a temp copy and asserts the test then fails for the right reason (not import error). This is the design's most defensible piece — it catches "Copilot wrote the wrong test" deterministically.
3. **PR-shape validator.** `copilot_validate_pr_shape.py` enforces filename regex, required artifacts per mode, and per-mode constraints (single new test for B/C, multi allowed for A). Failure → comment + `copilot:invalid` label.
4. **Mode discovery from any issue link Copilot emits.** The gate calls [`copilot_discover_mode.py`](../../.github/scripts/copilot_discover_mode.py), which walks (a) GraphQL `closingIssuesReferences` (the Development-sidebar link Copilot actually sets), (b) close-keyword regex in the PR body, (c) bare `#N` references — then dereferences task issues to the source issue via `Assembled from issue #M` (max 2 hops). Refuses to run only when none of those signals lead to a `copilot:<mode>` label.
5. **The version-bump path** (`copilot_publish_after_merge.yml`) is logically correct — non-draft release, rc-only bump, kill switch via repo variable. The earlier draft-creation bug is fixed in the comments and the code.

### 2.4 What's soft / needs a question prepared

| Risk | Why it matters | Mitigation today | Question to expect |
|---|---|---|---|
| **Branch protection on `lex-app-v2`** must include `showcase_tests` as a required check AND allow `copilot-swe-agent[bot]` to bypass required reviews. | Without bypass, mode-A auto-merge sits on "waiting for reviewer". | This is a one-time repo Settings configuration — not in the workflow YAML. Confirm before the demo. | *"What happens if branch protection isn't set up?"* → mode A auto-merge stalls; gate still passes. |
| **`lex-maintainers` team** must exist for mode-C reviewer assignment. | If absent, mode C still labels `needs-human-review` but no team gets pinged. | Workflow uses `|| echo ::warning` so it doesn't block — silently degrades. | *"How do we know mode-C PRs aren't getting lost?"* → check the `needs-human-review` label is the source of truth, not the reviewer field. |
| **Cluster routing is Copilot's call.** The PR-shape validator only enforces *that the filename matches the regex*, not *that the cluster is the right one*. | A test for an audit-logging bug could end up under `crud_api/`. | The PR description's "Placed under cluster Nx because…" sentence is the human checkpoint. Mode C is human-reviewed anyway. | *"Who catches wrong-cluster placements?"* → reviewers; the validator can't because there's no machine-readable "right cluster" for free-form prose. |
| **The `mode_b.out` parser.** Mode-B re-run distinguishes "test asserted FAIL" from "test errored on import" by grepping pytest output. | If pytest's stdout format changes, the gate could either pass a non-reproduction or fail a real one. | The grep is permissive (`^FAILED ` OR `==== FAILURES ====`). Verified against current pytest output. | *"What if pytest's output format changes?"* → loud test failure on next CI run; the parser stays a small surface (~10 lines). |
| **The publish-on-merge path has no abort window.** Once the workflow calls `gh release create` non-draft, `pip_publish.yml` picks up `release.created` immediately. | A bad Copilot PR with both labels lands on PyPI before anyone reads it. | `COPILOT_AUTO_PUBLISH_ENABLED == "false"` by default. The doc explicitly says flip it on only after a regression-mode round-trip succeeds end-to-end. | *"What's the kill switch?"* → repo variable. *"Why not a draft?"* → because GitHub never fires `release.*` for drafts; the previous draft attempt produced silent no-ops. |
| **No retry / rerun semantics.** If a Copilot PR's CI is red, the gate labels `copilot:invalid` and stops. There's no auto-loop. | Means a regressed Copilot PR sits open until a human deals with it — same as any other failing PR. | Acceptable for v1; this is in §14 "Out of scope" of the design. | *"Can it retry?"* → no, by design. Re-add the mode label to dispatch a fresh attempt. |
| **`COPILOT_PAT` is a classic PAT.** Required because GitHub does not allow `GITHUB_TOKEN` or App tokens to assign `copilot-swe-agent[bot]`. | Person-bound credential; PAT rotation will silently break the dispatch. | Documented in [`copilot-test-bot.md` §Configuration](copilot-test-bot.md) and §11 of the design spec. | *"Why isn't this a GitHub App?"* → it would be if GitHub supported it; the dispatch piece uses an App token. Only the bot assignment needs PAT. |

### 2.5 What's deliberately out of scope (v1)

From [§14 of the design spec](../superpowers/specs/2026-05-13-copilot-test-bot-design.md):

1. Auto-comment on red CI (Copilot pinged when its own PR fails).
2. Re-running Copilot on label re-add when a previous PR already exists.
3. Cross-repo coordination (`lex-app-frontend`, `lex-app-docs`).
4. Mode D ("improve a flaky test").
5. Web dashboard. Audit trail = GitHub issues + PRs + `progress/session-log.md`.

State this up front — it's the difference between "the v1 scope is too small" (a fair critique to plan against) and "we shipped something with gaps" (which would be wrong).

### 2.6 Suggested demo script

1. **Open an issue** in `lex-app` using the *Copilot Test Request* template. Pick a small, clear-cut behaviour — e.g. *"crud_api: PATCH on a TrackedItem should update `edited_at`."*
2. **Add the label** `copilot:regression`. The `copilot_test_bot.yml` run appears in Actions.
3. **Wait for Copilot to file a PR** (this is the variable-latency step — minutes to tens of minutes; have a pre-cooked PR open in another tab as a backup).
4. **Show the PR-gate run** — three checks pass, `auto-merge` label applied, branch protection lets the PR merge.
5. **Show `showcase_tests` as the release-gate check** — same green run, no special handling needed.
6. **Show the kill switch** — repo variable `COPILOT_AUTO_PUBLISH_ENABLED = "false"`. Without flipping it, the merge does not produce a release.

If a live Copilot run takes too long, have a screenshot of a previous successful round-trip ready.

---

## 3. The honest "still soft" list

Things I'd flag proactively rather than be asked about:

1. **No regression-mode round-trip has happened in production yet** (per the design's own §5 of the implementation plan). The publish-on-merge gate is therefore off by design. Recommend keeping it off through the next two cycles.
2. **PR-gate mode discovery was broken until 28 May** (fixed in commit `fd4f903`). The inline-bash resolver only matched `Fixes #N`, but Copilot links its source issue via the Development sidebar (`closingIssuesReferences`) instead — so every real Copilot PR failed at the very first gate step. Replaced with a Python resolver that walks every signal and dereferences task issues to source issues. Verified against PR #520 (`mode=regression, issue=518`). Worth showing because it's the exact kind of "we discovered the contract by running it" lesson §3 is for.
3. **The hotfix to the pytest cutover** ([`docs/ci-cd/pytest-cutover-hotfixes-2026-05-27.md`](pytest-cutover-hotfixes-2026-05-27.md)) lands across PR #514. Two bugs (silent-success + GCP-alias) reached default branch and were fixed in two commits. Worth showing because it demonstrates the test-of-the-tests discipline.
3. **The `progress/` decomposition** (extracting `conventions.md`, `session-log.md`, `coverage-tracker.md` out of `progress.md`) shipped via PRs #510–#513 — the Copilot prompt depends on `progress/conventions.md`, so this was a hard pre-req. All four PRs are on `lex-app-v2`.
4. **`lex test` is gone from CI;** the showcase suite runs `python -m lex pytest` per cluster via [`run_showcase_suite.py`](../../.github/scripts/run_showcase_suite.py). The `lex_test_config.yaml` file is the single source of truth for cluster names + the pytest-marker contract.
5. **The default branch is `lex-app-v2`,** not `main`. `workflow_run` triggers only fire for workflows that live on the default branch, so anything new has to be merged to `lex-app-v2` before it can cascade.

---

## 4. One-slide summary (for the talk)

> The release pipeline runs whenever a **non-draft** GitHub release is created. Draft = silent; pre-release flag = cosmetic. One click → tests + PyPI + Docker + docs PR, no abort window.
>
> The Copilot test-bot is fully wired across three workflows and ships today. Modes A (regression) and B (bug-repro) auto-merge on green; mode C (fix-and-test) routes to human review. The auto-publish path is off by default and stays off until we've seen at least one A-mode round-trip end-to-end.

---

## References

- [`pip_publish.yml`](../../.github/workflows/pip_publish.yml) — release-gated PyPI publish.
- [`custom-image.yml`](../../.github/workflows/custom-image.yml) — Docker image build (workflow_run on PyPI success).
- [`update_docs.yml`](../../.github/workflows/update_docs.yml) — docs dispatch (workflow_run on PyPI success).
- [`copilot_test_bot.yml`](../../.github/workflows/copilot_test_bot.yml) — entry point.
- [`copilot_pr_gate.yml`](../../.github/workflows/copilot_pr_gate.yml) — PR gate.
- [`copilot_publish_after_merge.yml`](../../.github/workflows/copilot_publish_after_merge.yml) — auto-publish (gated off).
- [`copilot-test-bot.md`](copilot-test-bot.md) — user-facing how-to.
- [Design spec](../superpowers/specs/2026-05-13-copilot-test-bot-design.md) and [Plan](../superpowers/plans/2026-05-13-copilot-test-bot.md).
- [Pytest cutover hotfixes](pytest-cutover-hotfixes-2026-05-27.md).
