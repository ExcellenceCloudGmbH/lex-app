# Docs Mirror Sync — Design

> **Date:** 2026-05-31
> **Status:** Approved (brainstorming) → implementation
> **Repos involved:** `lex-app` (this repo), `lex-app-docs`

---

## Problem

The published documentation lives in two places that have drifted apart:

- **`lex-app-docs/content/`** — the canonical Quartz site. The existing release pipeline
  (`update_docs.yml` → Copilot) *authors* doc changes here, and humans review/merge them here.
- **local `docs/`** — a copy of the same published subset (`features/`, `interface/`, `reference/`,
  `tutorial/`, `migration/`, `videos/`, `workshop/`, `images/`, plus root files like
  `getting started.md`), **intermixed** with a large pile of internal-only engineering docs
  (`ci-cd/`, `superpowers/`, `planning/`, `implementation/`, `bugs/`, …).

This matters beyond tidiness: the **test-plan derives test intent from local `docs/`** (the Golden
Rule — e.g. `test-clusters.md` cites `docs/features/tracking/tracking tables.md`). A stale local
mirror means tests are written against stale intent. We also want the docs on hand locally as
context when implementing features (e.g. the calculation work).

## Decision

**Model A — `lex-app-docs` is canonical; mirror *down* into local `docs/` (one-way, read-only here).**

- Authoring of published docs stays upstream (and via the existing Copilot pipeline).
- A CI job pulls the published subset down into `docs/` so the local copy is always fresh.
- Internal-only dirs are never touched.
- The shared subset is **read-only locally** — edits happen upstream; a guard rejects local edits.

Rejected alternatives:
- **B (author locally, publish up):** would collide with / require relocating the existing Copilot
  writer. Not what the team wants.
- **C (git submodule/subtree):** `lex-app-docs` is a whole Quartz site (build tooling around
  `content/`); a submodule mounts the entire repo at one dir → forces `docs/_published/content/...`
  paths (churn across every test `Intent` citation and prompt), can't overlay the intermixed top
  level, and clones as empty dirs without `--recursive` (defeats "docs available locally").

## Boundary: explicit path manifest (A1)

A checked-in manifest `docs/.docs-sync.yml` is the single source of truth for *which* paths under
`docs/` are owned by the mirror. The sync overwrites only those; everything else is off-limits.
Keeps every existing `docs/features/...` citation valid (no path churn).

```yaml
source_repo: ExcellenceCloudGmbH/lex-app-docs
source_ref: main
source_root: content
managed_paths:
  - features
  - interface
  - reference
  - tutorial
  - migration
  - videos
  - workshop
  - images
  - "getting started.md"
  - "installation.md"
  - "project structure.md"
  - "running your app.md"
  - "index.md"
```

## Mechanism: pull-based mirror + PR

`.github/workflows/sync_docs_from_lex_app_docs.yml`:

1. **Triggers:** daily `schedule` (cron, backstop), `workflow_dispatch` (manual), and an optional
   `repository_dispatch` (type `docs-content-updated`) fired by `lex-app-docs` when `content/**`
   merges — for instant sync. Pull-based primary means **zero changes needed in `lex-app-docs` to
   start working**.
2. **Read upstream:** generate a GitHub App token (existing `DOCS_APP_ID` / `DOCS_APP_PRIVATE_KEY`,
   App already has contents:read on `lex-app-docs`), checkout `lex-app-docs` `content/`.
3. **Mirror:** `.github/scripts/docs_mirror.py sync` reads the manifest and `rsync -a --delete`s each
   managed path from upstream `content/<path>` → local `docs/<path>`.
4. **PR:** if there's a diff, create a fresh branch `docs-sync/<upstream-short-sha>` and open a PR
   against `lex-app-v2`. The branch-ruleset forbids direct pushes to protected refs and to existing
   PR branches, so each sync is a **new branch + new PR**. The PR body records the exact upstream
   `content/` SHA mirrored (our cheap substitute for submodule version-pinning).

This is **complementary** to `update_docs.yml`. Full loop:

```
release in lex-app → update_docs.yml dispatch → lex-app-docs Copilot authors+merges docs PR
   → (new) lex-app-docs fires docs-content-updated OR daily cron
   → sync_docs_from_lex_app_docs.yml mirrors content/ down → PR against lex-app-v2 → merge
```

## Read-only guard

`.github/workflows/docs_mirror_guard.yml` runs on `pull_request`. `docs_mirror.py check-readonly`
takes the PR's changed files and fails if any fall under a managed path **unless** the PR head
branch is `docs-sync/*` (the sync bot). This enforces "don't author mirrored docs locally" with a
clear failure message pointing the author to `lex-app-docs`.

## Affected prompts / dependencies (must stay consistent)

The shared paths are cited as the intent source in several agent prompts; none of the paths move, so
citations stay valid, but each gets a one-line note that the subset is a read-only mirror:

- `.claude/skills/lex-testing/SKILL.md` (Step 0 intent sources)
- `.github/instructions/testing.instructions.md` (Golden-Rule intent sources)
- `CLAUDE.md` (new "Docs mirror sync (inbound)" subsection)
- `docs/ci-cd/automated-docs-pipeline.md` (document the inbound leg completing the loop)
- new `docs/ci-cd/docs-sync-mirror.md` (operator-facing description) + reference inbound-dispatch
  workflow `docs/ci-cd/docs-content-sync-dispatch.yml` for `lex-app-docs`.

No test files need editing — their `docs/...` `Intent` citations remain valid under A1.

## Components & boundaries

| Unit | Purpose | Depends on |
|---|---|---|
| `docs/.docs-sync.yml` | Single declaration of managed paths + source | — |
| `.github/scripts/docs_mirror.py` | `sync` (mirror) + `check-readonly` (guard); one manifest parser | PyYAML, rsync |
| `sync_docs_from_lex_app_docs.yml` | Orchestrate read → mirror → PR | App token, docs_mirror.py |
| `docs_mirror_guard.yml` | Reject local edits to managed paths | docs_mirror.py |
| `docs/ci-cd/docs-content-sync-dispatch.yml` | (reference, lives in lex-app-docs) instant-sync trigger | App/PAT in lex-app-docs |

## Testing

- Unit-test `docs_mirror.py` manifest parsing + `check-readonly` classification with a tmp manifest.
- Manual `workflow_dispatch` dry-run for the first real mirror; verify the PR contains only managed
  paths and records the upstream SHA.

## Out of scope

- Changing the outbound Copilot pipeline.
- Publishing local internal docs anywhere.
- Migrating doc paths.
