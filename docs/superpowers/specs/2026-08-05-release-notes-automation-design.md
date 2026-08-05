# Release notes and changelog automation — design

**Date:** 2026-08-05
**Branch:** `docs/release-notes-automation` (off `lex-app-v2` @ `2daa140`)
**Status:** design approved, not yet planned

## Problem

There are 222 tags in this repository and no release notes for any of them. The
body of `v2.1.6` — the current latest release — is this, in full:

```
Prerelease gate passed for `v2.1.6` via Prerelease Gate.
Promote this prerelease to a full release (untick "pre-release")
to ship it. ...
<!-- lex:gate-passed tag=v2.1.6 commit=lex-app-v2 -->
```

Gate boilerplate. Nothing about what changed.

The one set of real release notes, `docs/releases/RELEASE_NOTES_2.1.3.md` and its
`_github.md` sibling, was written by hand for a single version and never repeated.
It is good writing and it is the template this design automates, not replaces.

Two consequences follow. Nobody outside the team can tell what a release contains,
and nobody inside it can answer "which version shipped the timezone fix?" without
reading git log. There is no machine-readable record of what shipped when.

## Scope

**In:** two artifacts per release — a mechanical `CHANGELOG.md` and an LLM-drafted
business-facing note — generated at the prerelease gate, reviewed by the human who
already promotes the release, and published on promotion.

**Out:**

- **Quackback publishing.** Only its widget-token endpoint exists in this repo
  (`lex/lex_app/views.py:44`). Whether it can ingest a changelog is unknown. This
  design defines the interface and stops there; a follow-up spec fills it in.
- **Backfilling the 222 historical tags.** A later, optional phase, and only for
  the six `v2.1.x` releases — not 200 release candidates.
- **A Conventional Commits enforcement campaign.** Conformance is 57% and this
  design is built to work at that number (see [Input](#input-commits-enriched-with-prs)).

**Prerequisite, not in scope:** `frontend_build.yml` is broken and must be repaired
before frontend entries can be truthful. See [Prerequisite](#prerequisite-frontend_buildyml-is-dead).

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Where notes are drafted | `prerelease_gate.yml`, after the gate goes green | A human already reviews the prerelease before promoting it. Drafting there gets human-in-the-loop review for free, with no new step and no new approval surface |
| Number of artifacts | Two — mechanical `CHANGELOG.md` + drafted business note | The technical digest exists as the LLM's input anyway. Committing it costs almost nothing and finally answers "which release contained fix X" |
| Frontend in the business note | Woven in thematically, **not** given its own section | `RELEASE_NOTES_2.1.3.md` proves this: "New sidebar" sits beside "Lower memory on heavy operations". A business reader does not know PAC is a separate repository, and splitting by repo fragments their view of the release |
| Frontend in `CHANGELOG.md` | Explicit `**frontend**` prefix per entry | In the technical tier provenance is the point. Different tier, different reader, different answer |
| LLM transport | **Pluggable** — `LEX_NOTES_PROVIDER` selects anthropic / gemini / openai / github-models, default `auto` | **Revised twice.** GitHub Models was chosen to need no new secret, then began returning `410 github_models_retirement_brownout`. Rather than swap one hardcoded vendor for another, the transport is now a registry: each provider is ~15 lines, selection is configuration, and `auto` takes the first provider whose key is present (anthropic → gemini → openai → github-models). An explicit choice whose key is missing raises rather than silently drafting with a different vendor. Gemini authenticates via the `x-goog-api-key` header, never the `?key=` query parameter, to keep the credential out of request and proxy logs. |
| Input | Commits **enriched with PR metadata** | Commit conformance is 57%; PR titles are consistently well-formed (`perf(init): skip history registration in migration-only processes`). Enrichment makes the input usable without an enforcement campaign |
| Draft failure | Fail **open** with a visible marker | A changelog generator must never block a release. A red gate for a prose problem is a worse outcome than a stub |
| `CHANGELOG.md` editing | Never edited by hand | It is mechanical output. Only the business note is reviewed, so a human editing the release body cannot desync the technical record |
| Frontend section | Gated on `.frontend-version.json` existing | Without a recorded SHA there is no truthful source. Generating from PAC's log anyway would produce notes about changes that may not be in the shipped bundle — confidently wrong, worse than absent |

## Architecture

```
prerelease created
      │
      ▼
prerelease_gate.yml  (existing — 13 clusters)
      │ green
      ▼
draft-notes job  (new)
      ├─ ranges.py    → backend range; frontend range if manifest present
      ├─ digest.py    → structured JSON of changes
      └─ notes.py     → business note via GitHub Models
      │
      └─ writes the business note into the prerelease body
      │
  ◄── HUMAN reads, edits in the UI, promotes ──►
      │
      ▼
release:released
      │
      ▼
publish_release_notes.yml  (new)
      ├─ ranges.py + digest.py   (re-run — deterministic)
      ├─ changelog.py → Keep a Changelog section
      ├─ commits it to CHANGELOG.md
      └─ publish_quackback()  → interface only, see Out of scope
```

The split is load-bearing: **the changelog is mechanical and never edited; the
business note is drafted and always reviewed.** Because the changelog is rendered
from the digest rather than from the release body, a human rewriting the prose
cannot corrupt the technical record.

The digest is rebuilt at release time rather than carried over from the gate run.
Workflow artifacts are scoped to the run that produced them, so passing one between
two workflows triggered by two different events would need cross-run download
plumbing and a token. Since the tag is fixed once the prerelease exists, the commit
range is fixed too, and re-running costs one API call sequence and no model call —
cheaper than the plumbing it replaces.

**The rebuild is reproducible, not strictly deterministic**, and the difference
matters to anyone treating the changelog as a replay of the approved note:

- `draft-notes` checks out the tag (a `release` event sets `GITHUB_REF` to
  `refs/tags/<tag>`), while `publish_release_notes.yml` pins
  `ref: default_branch`. Promotion can happen well after the gate, so a change to
  the parsing logic landing in between means the two artifacts were produced by
  two versions of the tool.
- `enrich_with_prs` queries the live API on both runs. A PR title edited between
  them, or a PR that transitions from open to merged, changes the text — and since
  `parse_subject` runs on the PR title in preference to the commit subject, it can
  move an entry into a different section.

Neither breaks anything: the changelog is regenerated rather than reviewed, and is
explicitly not a transcript of the note a human approved. But do not build anything
on top of an assumption of byte-identical replay.

## Module layout

Following the precedent set by `.github/scripts/docs_mirror.py`, which is CI-side
Python with tests in `.github/scripts/tests/` — not framework code, so not part of
the cluster test-plan.

```
.github/scripts/release_notes/
    __init__.py
    ranges.py       # tag → (backend range, frontend range | None)
    digest.py       # ranges → digest JSON
    changelog.py    # digest → Keep a Changelog section
    notes.py        # digest → business note via GitHub Models
    quackback.py    # publish interface — raises NotImplementedError
    __main__.py     # CLI the workflows call
.github/scripts/tests/
    test_release_notes_ranges.py
    test_release_notes_digest.py
    test_release_notes_changelog.py
    test_release_notes_notes.py
```

Modules are named for their domain rather than for a verb, so the call sites read
as `changelog.render(...)` and `notes.draft(...)` rather than
`render_changelog.render(...)`.

### `ranges.py`

Given the current tag, returns the backend commit range and, when available, the
frontend one.

The backend range is *previous release tag* → *current tag*. "Previous" means the
most recent tag reachable from the current tag's commit that matches
`^v\d+\.\d+\.\d+(rc\d+)?$` — a filter, not an ordering convenience. The repository
contains `v0.0.0-hazem` and `recovery-supervisor-on-demand.0`, and picking either
as a baseline would silently produce a nonsense range.

The frontend range is read from `lex/react/build/.frontend-version.json` at both
tags. If the file is absent at either end, the function returns `None` for the
frontend range and the frontend section is omitted entirely. There is no fallback
and no guess.

### `digest.py`

Turns ranges into a structured digest — the single source of truth for both
downstream renderers.

```json
{
  "tag": "v2.1.7",
  "previous_tag": "v2.1.6",
  "changes": [
    {
      "sha": "705850d", "component": "backend",
      "type": "fix", "scope": "calc", "breaking": false,
      "subject": "never stamp edited_at/edited_by for a calculation-owned save",
      "pr_number": 675,
      "pr_title": "fix(calc): never stamp edited_at/edited_by for a calculation-owned save",
      "url": "https://github.com/.../commit/705850d"
    }
  ]
}
```

#### Input: commits enriched with PRs

Each commit is looked up via `gh api repos/{owner}/{repo}/commits/{sha}/pulls`. Where
a merged PR is found, its title and body lead; the commit subject is the fallback.

This is what makes 57% commit conformance survivable. The non-conforming 43% is
`Lex docs change`, `publishable`, `Mode change is a beauty` — but those changes
generally arrived through PRs whose titles are well-formed.

Dropped from the digest: merge commits, bundle-update commits, and commits whose
message is empty after stripping the conventional prefix.

### `changelog.py`

A pure function: digest in, markdown out. No network, no model, golden-file testable.

Conventional types map onto Keep a Changelog headings — `feat` → Added,
`fix` → Fixed, `refactor`/`perf`/`style` → Changed, `revert` → Removed, anything
marked breaking gets a `### Breaking` block first. `docs`, `test`, `ci`, `chore`,
and `build` are excluded: they are real work but not changes to the shipped product.

```markdown
## [2.1.7] - 2026-08-05

### Fixed
- **backend** never stamp `edited_at` for a calculation-owned save ([705850d]) (#675)
- **frontend** send the viewer's timezone on export requests ([a3f91c2]) (#452)
```

The result is prepended to `CHANGELOG.md` at the repository root, below the
`Keep a Changelog` preamble. That file does not exist yet; the first run creates it,
preamble included.

### `notes.py`

Sends the digest plus `docs/releases/RELEASE_NOTES_2.1.3_github.md` — as a style
exemplar, not a content template — to GitHub Models, and requires back the house
format: `## Main changes`, `## Optimizations`, `## Bug fixes`, and a closing
`**Upgrade note:**`.

The calling job needs `permissions: models: read` in addition to its existing
`contents: write`. Transport is the GitHub Models inference endpoint authenticated
with the job's `GITHUB_TOKEN` — no new secret, and no dependency on the `gh models`
CLI extension being installed on the runner.

The prompt states the audience explicitly: a business user who has never read the
codebase, and a technical user who wants to know what actually changed, in one
document. It instructs the model to group by what the change *means* rather than
by which repository it came from, and to omit any section that would be empty
rather than emit a heading with nothing under it.

Output is validated for the required headings. On a failed call, malformed output,
or an empty digest, it emits a stub containing the raw digest and stamps
`<!-- lex:notes-draft-failed -->` into the body. The gate stays green.

## The frontend manifest

`lex/react/build/.frontend-version.json`:

```json
{
  "repo": "ExcellenceCloudGmbH/process-admin-general-client",
  "branch": "lex-app-v2-pac-latest",
  "sha": "a3f91c2e4b1d8f60c2a5e7b9d3f1a4c6e8b0d2f4",
  "built_at": "2026-08-05T09:14:22Z"
}
```

Two writers, in sequence:

**Now — by hand.** Whoever commits a rebuilt bundle updates the manifest in the same
commit. A CI check fails any PR that changes files under `lex/react/build/` without
changing the manifest, so it cannot be quietly skipped. This is not elegant, but it
is truthful, and it matches how bundles actually reach the repository today.

**Later — by `frontend_build.yml`.** Once that workflow runs (see below), it writes
the manifest itself from the SHA it built and the manual discipline retires.

## Prerequisite: `frontend_build.yml` is dead

It has never succeeded. Every run since `v2.0.0rc218` — ten of ten — failed at
`Preflight: Validate Secrets`, and every downstream job was skipped. Neither
`FRONTEND_REPO_TOKEN` nor `NPM_MARMELAB_TOKEN` exists in the repository's secrets.
There is not a single `build(frontend)` commit in the history.

The bundle in `lex/react/build/` is therefore hand-committed, with messages like
`updated frontend`, `Adding the FE patch to local FE`, and `Add(Frontend)`. Nothing
links it to a PAC commit.

This design does **not** repair that. It is a broken pipeline that deserves fixing
on its own merits, and switching on an automated build that has never once run —
which would start replacing hand-built bundles — needs its own validation before it
runs on a release. It is named here because it is the reason the frontend section is
gated, and because repairing it is what eventually makes that section durable.

Until then, frontend entries appear if and only if the manual manifest is present at
both ends of the range.

### What repairing it actually takes (investigated 2026-08-05, deferred)

Less than it looks. Everything downstream of the preflight is ready; only the two
secrets are missing.

**Ruled out:** the `"test": "vite test"` bug recorded in `CLAUDE.md` is **stale**. On
`lex-app-v2-pac-latest` — the branch this workflow builds — it is `"test": "vitest"`,
and `vitest`, `@vitest/coverage-v8`, `jsdom`, `@playwright/test`, `e2e/`,
`playwright.config.ts` and `vitest.setup.ts` are all present. That bug survives only
on the v1 default branch.

**`NPM_MARMELAB_TOKEN`** — external, longest lead time. PAC depends on four paid
`@react-admin/*` packages and its `.npmrc` reads
`//registry.marmelab.com/:_authToken=${NPM_MARMELAB_TOKEN}`. No engineering
workaround: without it `yarn install` cannot resolve the tree, so unit tests, E2E and
build all fail. Someone with the react-admin Enterprise subscription must issue it.

**`FRONTEND_REPO_TOKEN`** — check `LEX_PACKAGES_TOKEN` first. It was added
2026-08-03, is referenced only by this workflow, and is already the primary in the
tokens-freshness step (`secrets.LEX_PACKAGES_TOKEN || secrets.FRONTEND_REPO_TOKEN`).
If it already carries repo-read scope on PAC, reuse it in the three `actions/checkout`
steps and no new secret is needed. Otherwise prefer the existing GitHub App —
`DOCS_APP_ID`/`DOCS_APP_PRIVATE_KEY` with `repositories: "process-admin-general-client"`,
the same pattern `update_docs.yml` already uses — over a classic PAT, per the
org-owned-credentials rationale in `CLAUDE.md`.

**Unvalidated once the secrets land.** No step past the preflight has ever executed.
`COVERAGE_THRESHOLD: "76"` is an untested guess (178 test files against 211 source
files makes it plausible, not proven), and the E2E job boots a real Django backend via
`globalSetup` (migrate + seed) driving Chromium. The workflow triggers on
`release: released`, so the first real execution would otherwise happen *during a
release* — use the existing `workflow_dispatch` first:

```bash
gh workflow run frontend_build.yml -f frontend_branch=lex-app-v2-pac-latest
```

**Then close the loop with this design.** Add a manifest write immediately before the
existing bundle commit in `build-and-commit`, and `frontend_range()` starts returning
a real range instead of `None`:

```yaml
- name: Record the PAC commit this bundle came from
  run: |
    printf '{\n  "repo": "%s",\n  "branch": "%s",\n  "sha": "%s",\n  "built_at": "%s"\n}\n' \
      "$FRONTEND_REPO" "$FRONTEND_BRANCH" \
      "$(cd frontend && git rev-parse HEAD)" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      > lex/react/build/.frontend-version.json
```

## Error handling

| Condition | Behaviour |
| --- | --- |
| `gh models` call fails or times out | Stub body + `lex:notes-draft-failed` marker. Gate stays green |
| Model returns malformed output | Same as above. Validation is on headings, not prose quality |
| No previous release tag (first release) | Range is the repository root → current tag. Works, produces a large digest |
| Only junk tags precede this one | Filter excludes them; falls back to the first-release behaviour above |
| Manifest missing at either end | Frontend section omitted. Backend notes still generated |
| Digest is empty (no shippable changes) | Body records "no user-facing changes in this release". `CHANGELOG.md` is not touched |
| `CHANGELOG.md` conflicts on commit | `publish_release_notes.yml` retries once on a fresh pull, then fails loudly — the release itself has already shipped, so this is safe to fail |

## Test plan

Tests live in `.github/scripts/tests/`, run with pytest, following `test_docs_mirror.py`.

**No workflow currently runs that directory.** It holds 158 passing tests that CI has
never executed. Adding `.github/workflows/scripts_tests.yml` is therefore the first
implementation step, not an afterthought — without it the tests below would join the
existing pile of never-run coverage, and every task after it would be unverified.

**`ranges`** — junk tags (`v0.0.0-hazem`, `recovery-supervisor-on-demand.0`)
are excluded; rc tags are valid baselines; a missing manifest at either end yields
`None`; the first-release case resolves to the root commit.

**`digest`** — merge commits and bundle commits are dropped; PR enrichment
prefers the PR title over the commit subject; a commit with no associated PR still
produces an entry; non-conforming subjects are classified as `other` rather than
discarded.

**`changelog`** — golden-file comparison across a fixture digest covering
every type, both components, and a breaking change; `docs`/`ci`/`chore` are
excluded; an empty section emits no heading.

**`notes`** — a stubbed model client returning valid output passes validation;
malformed output produces the stub and the failure marker; the model client is never
called for an empty digest.

The model's prose is not asserted. Shape and fallback are testable; writing quality
is what the human review step exists for.

## Out of scope

- **Quackback publishing.** `quackback.py` defines the interface and raises
  `NotImplementedError`. Its capabilities need confirming first.
- **Backfilling historical releases.** Optional later phase, `v2.1.x` only.
- **Pinning the frontend SHA at prerelease time.** Only meaningful once
  `frontend_build.yml` runs — while bundles are hand-committed, a pinned SHA records
  intent that may disagree with what shipped.
- **Conventional Commits enforcement.** The design works at 57%.
