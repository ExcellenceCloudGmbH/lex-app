# Frontend provenance for release notes — design

> **Status:** design, approved 2026-09-01. Successor to
> [`2026-08-05-release-notes-automation-design.md`](2026-08-05-release-notes-automation-design.md),
> which built the drafter and deliberately left frontend entries out of scope.
>
> **Problem it solves:** when lex-app is released, the frontend release notes must exist and be
> correct. Today they are always absent, and an absent frontend section is indistinguishable from
> a release that genuinely had no frontend changes.

## Contents

- [The requirement](#the-requirement)
- [Why frontend notes are absent today](#why-frontend-notes-are-absent-today)
- [Decisions](#decisions)
- [Architecture — three guarantees](#architecture--three-guarantees)
- [Components](#components)
- [Data flow](#data-flow)
- [Quality: two tiers, two readers](#quality-two-tiers-two-readers)
- [Model provider](#model-provider)
- [Error handling](#error-handling)
- [Testing](#testing)
- [Out of scope](#out-of-scope)
- [Open questions](#open-questions)

---

## The requirement

1. When **lex-app** is released, frontend release notes exist and are **correct**.
2. Frontend notes also exist at a **frontend release** (when a bundle ships).
3. Backfill the **7 stable `v2.1.x`** releases, backend and frontend.
4. A gap must be **recoverable later** — a release must never be blocked because provenance is
   missing.
5. Both tiers must be right: business prose for customers, technical detail for engineers.

## Why frontend notes are absent today

`ranges.frontend_range()` needs a PAC commit SHA at **both** ends of a release range. It reads
`lex/react/build/.frontend-version.json` via `git show <ref>:<path>`. Three measured facts:

| Fact | Measurement |
|---|---|
| `.frontend-version.json` present at any release tag | **absent at all 206** |
| Workflows passing `--pac-checkout` (accepted at `__main__.py:181`) | **zero** |
| PAC releases in the v2 era (its workflow triggers `on: release`) | **none since `v1.11.2`, 2025-11-27** |

So the resolver has no data, the CLI is never given PAC history, and the workflow that would
record provenance has never executed. Bundles are hand-committed — 76 commits touch
`lex/react/build/`, none carrying provenance.

**A structural consequence:** the in-tree manifest can never answer for the past. You cannot add a
file to an existing tag's tree without rewriting history, so a `git show <tag>:…` lookup is
incapable of serving a backfill. History needs a second read path.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Frontend identity | **PAC commit SHA. No version.** | `package.json` has read `0.2.0` on every branch and changed **once ever** (0.1.0 → 0.2.0). PAC's 225 tags are all `v1.x`. A version field here identifies nothing and would rot again. The commit is the only thing that varies. |
| Who records provenance | **PAC's own workflow, from `$GITHUB_SHA`** | Only the job that built the bundle knows which commit produced it. lex-app receives content-hashed Vite output with no provenance. Recording it there makes it correct *by construction* — no human types a SHA. |
| Bundle trigger | **`workflow_dispatch` in PAC**, fresh PR branch per dispatch | Explicitly **not** on push — shipping the frontend is a deliberate act. Correctness does not depend on the trigger (see G1–G3), so this can change later freely. `lint.yml` already runs Code Quality & Tests on push to every branch, so nothing is lost. |
| Historical provenance | **Committed side-car map**, `frontend-history.json` | The only mechanism that can answer for existing tags. Append-only, frozen after backfill, and confined to one function in `ranges.py`. |
| Missing provenance at release | **Record a marker, never block** | Per requirement 4. The existing principle "a changelog generator must never block a release" survives intact. |
| Correction mechanism | **`backfill` — the same tool as history** | One tool, two uses: fill history, and repair any future gap. Avoids a second half-parallel code path. |
| Where notes appear | **lex-app repo only** — release body + `CHANGELOG.md` | An in-app "What's new" surface is a product feature needing its own spec and cluster tests. Deliberately deferred. |
| Backfill scope | **7 stable `v2.1.x`** tags | 200 of 206 release tags are release candidates. Nobody reads rc notes. Matches the predecessor spec's decision. |

## Architecture — three guarantees

Correctness comes from three properties, not from a trigger.

### G1 — Correct by construction

`.frontend-version.json` is written by the same job that produced the bundle, from `$GITHUB_SHA`.
The process that knows the answer is the process that records it. Hand-committed bundles are the
only way a wrong SHA can enter the system, which is why the automated path must become the only
path.

### G2 — No bundle enters lex-app without provenance

`frontend_manifest_guard.yml` already fails any PR touching `lex/react/build/**` where
`.frontend-version.json` did not change with it. **Extension:** it must also validate content —
parseable JSON, `sha` present and matching `^[0-9a-f]{40}$`. Today it proves a file moved, not
that it says something true, and `ranges.py` already warns that a hand-written manifest can carry
a blank SHA.

### G3 — Verified at release time, recorded when missing

Detection and recording happen at **two different events**, because the two artifacts are produced
by two different workflows. Neither ever fails.

**At prerelease** (`prerelease_gate.yml`, on `prereleased`) — `verify-frontend --tag T` runs before
drafting and only *reports*: `::warning::` in the run summary, plus a visible line in the drafted
body. It cannot mark `CHANGELOG.md`, because that file has no section for `T` yet. Its job is to
tell the human reviewing the prerelease that frontend notes will be missing, while they can still
act.

**At publish** (`publish_release_notes.yml`, on `released`) — `render-changelog` writes the section,
and when the range is unresolvable it writes the marker into it:

```markdown
## v2.1.8 — 2026-09-01
...
> **Frontend changes for this release are not yet recorded.**
```

Putting the marker in the renderer keeps it mechanical: the marker is derived from the same digest
as everything else in that section, so it cannot disagree with the entries beside it.

The marker **is** the durable record. No separate state file: `CHANGELOG.md` is already the
mechanical record, the marker is visible to any human reading it, and it is greppable — so
`list-gaps` needs no storage that can drift from reality.

Together: G1 and G2 keep the gap list short, G3 is the net beneath them, and `backfill` empties it.

## Components

### PAC — `.github/workflows/push-build-to-pip-package.yml`

Jobs `gate-tests`, `gate-e2e`, `build-and-deploy` already exist. Changes to `build-and-deploy`:

| Current | Change |
|---|---|
| `on: release` | `on: workflow_dispatch` with a `dry_run` boolean input |
| `actions/checkout@v2` (×2) | `@v4` |
| `repository: LundIT/lex-app` | `ExcellenceCloudGmbH/lex-app` (the old name still redirects; do not rely on it) |
| `token: ${{ secrets.PAT }}` | `lex-docs-bot` App token — **requires extending the installation to PAC**, which it does not currently cover. Fall back to a scoped PAT if that is refused. |
| `branch: "PAC-build-update"` | `PAC-build-update-<shortsha>` — one reviewable PR per shipped bundle |
| — | **new step** writing the manifest |

```yaml
- name: Record frontend provenance
  run: |
    printf '{"sha":"%s","ref":"%s","built_at":"%s"}\n' \
      "$GITHUB_SHA" "$GITHUB_REF_NAME" "$(date -u +%FT%TZ)" \
      > lex-app/lex/react/build/.frontend-version.json
```

The path is the exact string already hardcoded as `ranges.MANIFEST_PATH`. Under `dry_run` the
step prints the manifest and the PR is not created.

**Requirement 2** — the dispatch *is* the frontend release. At that moment the workflow knows the
previously shipped SHA (from the manifest already in lex-app) and its own, so it drafts a
frontend-only note into the bundle PR body. The frontend note is reviewed while the change is
fresh, and the later lex-app release reuses the same resolved range.

### lex-app — `.github/scripts/release_notes/ranges.py`

Two additions. Nothing else in the package changes.

```python
def bundle_commit_at(ref: str) -> str | None:
    """The lex-app commit that last touched the vendored bundle as of `ref`."""
    # git rev-list -1 <ref> -- lex/react/build

def frontend_sha_at(ref, *, show=git_show, history=load_history) -> str | None:
    """In-tree manifest first; committed side-car for pre-manifest tags."""
```

`frontend_sha_at` keeps its exact signature, so `digest.py`, `notes.py` and `changelog.py` are
untouched. That isolation is the architectural claim: an entire historical provenance path lands
in one function in one file.

### lex-app — `.github/scripts/release_notes/frontend-history.json` *(new)*

Keyed by lex-app bundle commit, which `bundle_commit_at` derives. **Exactly three entries** — every
release tag in scope maps to one of three bundles:

| tags | bundle commit | date |
|---|---|---|
| `v2.0.0rc221`, `v2.1.1`, `v2.1.2` | `e56f7557` | 2026-06-30 |
| `v2.1.3` | `d56c70e7` | 2026-07-14 |
| `v2.1.4` – `v2.1.7` | `a388985a` | 2026-07-22 |

```json
{ "e56f7557": { "pac_sha": "…", "method": "hash-proof" } }
```

`method` is `hash-proof` or `date-window`. **A guess must never be stored looking like a proof.**

*Attribution procedure.* Vite asset filenames are content hashes — `v2.1.7` ships
`assets/index-BD1SQUOi.js`. Check out a candidate PAC commit, build, compare the emitted filename.
A match **proves** the attribution (`hash-proof`). If toolchain drift prevents reproduction, infer
from the date window and record `date-window`.

### lex-app — `.github/scripts/release_notes/__main__.py`

Three new subcommands alongside `draft-notes` and `render-changelog`:

```
verify-frontend --tag <T>                      # warn + marker, never fails
backfill --from <A> --to <B> [--skip-existing] [--force] [--dry-run] [--pac-checkout P]
list-gaps                                      # tags whose CHANGELOG carries a marker
```

`backfill --tag <T> --force` is the correction path for a single release.

### lex-app — `prerelease_gate.yml`, `publish_release_notes.yml`

Add a PAC checkout step and pass `--pac-checkout`. Add `verify-frontend` to the gate before
drafting.

### Unchanged

`digest.py`, `notes.py`, `changelog.py`, `quackback.py`. And `frontend_build.yml` becomes dead
weight for this purpose — it needs `FRONTEND_REPO_TOKEN` and `NPM_MARMELAB_TOKEN`, neither ever
configured, and G1 makes it unnecessary.

## Data flow

**Frontend ship (PAC, on dispatch)**

```
dispatch → gates → yarn build → cp build/* → lex-app/lex/react/build/
        → write .frontend-version.json  ($GITHUB_SHA)
        → draft frontend-only note (prev manifest SHA → own SHA)
        → PR: PAC-build-update-<shortsha>  →  guard validates manifest
```

**lex-app prerelease**

```
verify-frontend --tag T
   frontend_sha_at(prev) ─┬─ in-tree manifest
   frontend_sha_at(T)   ──┘   else side-car
        │
        ├─ both resolved → frontend_range → PAC log → digest
        └─ either None   → ::warning:: + visible line in drafted body, no FE section
                                │
draft-notes → one digest ───────┴──→ business note (woven)     → release body

lex-app publish  (on: released)
render-changelog ───────────────────→ technical entry (**frontend**) → CHANGELOG.md
                └─ range unresolvable → writes the marker into the section
```

**Correction, later**

```
list-gaps → backfill --tag T --force
   CHANGELOG.md      → section REPLACED, marker dropped
   GitHub release    → frontend addendum APPENDED via `gh release edit`
```

The asymmetry is load-bearing. `CHANGELOG.md` is mechanical and never hand-edited, so replacing a
section is safe. The release body contains **human-approved prose someone edited after drafting** —
rewriting it to fix a frontend gap would destroy that review. Correction appends there, never
replaces.

## Quality: two tiers, two readers

The same digest renders twice, and each tier has a different notion of correct.

**Business note → release body.** Grouped by what a user notices, never by component or
repository. Frontend and backend changes woven together thematically — a business reader does not
know PAC is a separate repo, and splitting by repo fragments their view of the release. No class
names, file paths, commit hashes, PR numbers. Ends with `**Upgrade note:**`. Entries flagged
`internal: true` are omitted; "No user-facing changes in this release" is a complete and
acceptable answer.

**Technical entry → `CHANGELOG.md`.** Mechanical, rendered from the digest rather than from the
release body, so a human rewriting prose cannot corrupt the technical record. Frontend entries
carry an explicit `**frontend**` prefix — in this tier provenance is the point.

Enforcement already exists and is retained: `notes.validate()` rejects an empty response, a
missing heading, or a heading with nothing under it; the prompt's anti-hallucination section names
the exact false claim a previous release published (*"LEX can now connect to Gemini and OpenAI"*
— drafted from a change to the release-note tool itself).

Context quality depends on **PR #702** (`feat/notes-richer-context`), currently open. `digest._entry`
in HEAD emits no body, so the model drafts from ~9-word subjects. #702 attaches each PR body
(stripped, capped at 4,000 chars, omitted for internal entries) and budgets the prompt at 60 KB
trimming longest-first — **17,233 characters of author-written explanation for `v2.1.7rc1` where
there were a few hundred**. This spec assumes #702 is merged first.

## Model provider

**Measured 2026-09-01:** `https://models.github.ai/inference/chat/completions` returns
**HTTP 410**, `github_models_retirement_brownout`. The catalogue endpoint returns 410 as well.

Therefore:

- **GitHub Models stays a first-class selectable provider.** `notes.github_models()` is retained
  unchanged, `LEX_NOTES_PROVIDER=models` selects it, and `prerelease_gate.yml` keeps
  `permissions: models: read`. If GitHub restores the service it works with no code change.
- **Anthropic is the default**, already so at `notes.py:57` (`claude-sonnet-5`). It is the only
  provider verified to function.
- **Backfill overrides to a stronger model.** `LEX_NOTES_MODEL=claude-opus-5` — the backfill is a
  one-off over 7 releases where quality dominates cost.
- **A retired provider must not fail silently.** Selecting an unreachable provider currently
  degrades to `fallback()` plus `FAILURE_MARKER`, which is an HTML comment and therefore invisible
  in rendered markdown. The marker must become a **visible** line in the body.

*Alternative, not chosen:* route drafting through the **Copilot coding agent** (issue → agent → PR),
the pattern already used by the docs receiver and `copilot_test_bot.yml`, adopted because Models
API calls counted against Copilot billing anyway. It would use existing entitlement rather than a
new key. Rejected here because it replaces a synchronous 2-second API call with an asynchronous
agent run inside a release gate, and the gate needs a body *now*. Worth revisiting if the
`ANTHROPIC_API_KEY` cost is the objection.

## Error handling

Nothing in this design fails a release.

| Condition | Behaviour |
|---|---|
| No `--pac-checkout` | `frontend_range → None`; FE section omitted; `verify-frontend` warns + marks |
| Manifest absent at a tag | side-car fallback; if also absent → warn + mark |
| Manifest malformed or blank `sha` | treated as absent (existing behaviour); **guard rejects it at PR time** |
| Side-car unparseable | treated as empty; warn. Never raises — a corrupt lookup table must not break drafting |
| PAC log fails (bad SHA) | FE section omitted, warn + mark |
| Model unreachable / retired provider | `fallback()` body + **visible** failure marker |
| `backfill` interrupted | idempotent: `--skip-existing` skips completed tags; nothing partially written |
| Two releases racing on `CHANGELOG.md` | `publish_release_notes.yml` already handles the push race; unchanged |

**The invariant:** an unresolvable range produces *no frontend claims*, never a guessed one. A note
describing frontend changes that may not be in the shipped bundle is worse than a note that stays
quiet — and G3 makes the silence visible rather than indistinguishable from "nothing changed".

## Testing

Home: `.github/scripts/tests/`, already gated by `scripts_tests.yml` on push/PR touching
`.github/scripts/**`. Test files already exist for all four modules
(`test_release_notes_{ranges,digest,notes,changelog}.py`), so new cases extend rather than
introduce structure.

**Not required:** cluster tests under `lex/test_project/`. AGENTS.md directive 2 applies to changes
under `lex/`; this change touches `.github/scripts/` and workflows only.

### 1. Unit — no API key, no network

Every test injects a fake model. `notes.draft()` already takes `model: Callable[[str], str]` and
`ranges.frontend_sha_at` already takes an injectable `show`, so this needs no refactor.

`test_release_notes_ranges.py` (extend):
- in-tree manifest present → returns its `sha`
- manifest absent, side-car hit → returns `pac_sha`
- manifest absent, side-car miss → `None`
- malformed JSON, missing `sha`, blank `sha`, non-40-hex `sha` → `None` (each asserted separately)
- side-car file absent entirely → `None`, no raise
- side-car unparseable → `None`, no raise
- `bundle_commit_at` for a tag with no bundle history → `None`
- `frontend_range`: both ends resolved; only `to` resolved; only `from` resolved → `None`

`test_release_notes_verify.py` (new):
- unresolvable range → warning emitted, **exit code 0**, no `CHANGELOG.md` write
- resolvable range → no warning
- `render-changelog` with an unresolvable range → section carries the marker
- `render-changelog` with a resolvable range → no marker
- marker is idempotent (re-rendering the same tag does not duplicate it)

`test_release_notes_backfill.py` (new):
- range iteration yields tags in order and excludes non-release tags (`v0.0.0-hazem`,
  `recovery-supervisor-on-demand.0`)
- `--skip-existing` skips a tag already in `CHANGELOG.md`
- `--force` replaces an existing section and drops its marker
- `--dry-run` writes nothing
- `list-gaps` returns exactly the marked tags

### 2. Golden files — the quality gate

Commit a digest fixture and its expected `CHANGELOG.md` rendering; assert byte equality. This
catches structural regressions in the technical tier (heading shape, `**frontend**` prefixes,
ordering, date format) **without calling a model**, which is what makes it usable in CI.

The business note cannot be golden-tested — it is model output. `validate()` is the machine check;
a human batch review is the quality check.

### 3. Provenance-schema test

Assert the guard's validation logic rejects: absent file, invalid JSON, missing `sha`, blank
`sha`, short/non-hex `sha`. Extracted into a small importable function so it is testable rather
than living only in shell inside the workflow.

### 4. End-to-end rehearsal — no release required

The only way to assess prose quality. Run locally against a real historical tag:

```bash
export ANTHROPIC_API_KEY=…
PYTHONPATH=.github/scripts python -m release_notes backfill \
  --from v2.1.6 --to v2.1.6 --dry-run \
  --pac-checkout /path/to/process-admin-general-client
```

`v2.1.6` is a good rehearsal target: it has a known frontend bundle (`a388985a`) and a substantial
backend range. Read the output; do not commit it.

### 5. PAC workflow verification

A workflow cannot be unit tested. Verify with **one `workflow_dispatch` run with `dry_run: true`**,
asserting the log shows the manifest it would write with a 40-hex `sha` matching the dispatch
commit, and that no PR was created.

### 6. Acceptance criteria

The design is done when:

1. `list-gaps` is empty for `v2.1.1` … `v2.1.7`
2. all three `frontend-history.json` entries record `method`, and any `date-window` entry is
   deliberate rather than a failed proof
3. a `dry_run` PAC dispatch prints a valid manifest
4. a real dispatch produces a bundle PR that the guard passes
5. the next lex-app prerelease shows a frontend section in the release body **and**
   `**frontend**`-prefixed entries in `CHANGELOG.md`
6. `scripts_tests.yml` is green

## Out of scope

- **In-app "What's new" surface.** Requires notes shipped inside the pip package, an endpoint, and
  a frontend surface. Product feature; own spec; would need cluster tests.
- **Quackback / Hub publishing.** `quackback.publish()` stays `NotImplementedError`. Tracked as
  LEX-590; blocked on one unknown — whether Quackback exposes a create-post API, or accepts
  inbound email per board, which would make it a one-line config change.
- **Reviving PAC semver releases** and the v1 `versions_override: {"process_admin_client": …}`
  channel into instance-controller provisioning. Contradicts the identity decision.
- **Backfilling the 200 release candidates**, and the other 55 of 58 historical bundles.
- **Repairing `frontend_build.yml`.** Superseded by G1.
- **Fixing PAC's `dependabot.yml`**, which has failed on 14 consecutive pushes. Unrelated.

## Open questions

1. **`lex-docs-bot` App is not installed on PAC.** Extend the installation, or keep a scoped PAT in
   `build-and-deploy`? Recommendation: extend the App — a PAT is tied to a person.
2. **Which PAC branch does dispatch build from?** `lex-app-v2-pac-latest` is the integration line,
   but the new UI and its tests live on `feat/frontend-test-plan`, which PR #442 merged in and
   **PR #450 reverted out**. Soft dependency on **LEX-594**. Keep the branch in one named place.
3. **Does `--force` correction also need to touch the drafted business note in the release body**,
   or is a frontend addendum sufficient? Spec assumes addendum.
