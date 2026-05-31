# Docs Mirror Sync (inbound)

> **Owner:** CI/CD
> **Repos involved:** `lex-app`, `lex-app-docs`
> **Last updated:** 2026-05-31
> **Design:** [`docs/superpowers/specs/2026-05-31-docs-mirror-sync-design.md`](../superpowers/specs/2026-05-31-docs-mirror-sync-design.md)

---

## What it does

`lex-app-docs/content/` is the **canonical** published documentation. This pipeline mirrors the
published subset **down** into this repo's `docs/` so the local copy is always fresh.

Why we keep a local copy at all:

- The **test-plan derives test intent from local `docs/`** (the Golden Rule — `test-clusters.md`
  cites paths like `docs/features/tracking/tracking tables.md`).
- We want the docs on hand as **context when implementing features** (e.g. calculations) without a
  round-trip to the docs repo.

This is the **inbound** half of the docs automation. The **outbound** half
([`automated-docs-pipeline.md`](automated-docs-pipeline.md)) authors docs upstream via Copilot.
Together they form one loop:

```
release in lex-app
   → update_docs.yml dispatch → Copilot authors + merges docs PR in lex-app-docs   (outbound)
   → sync_docs_from_lex_app_docs.yml mirrors content/ down → PR against lex-app-v2  (inbound)
```

## Read-only contract

The mirrored subset is **read-only in this repo**. Do not edit it here — the next sync overwrites
your changes. Edit upstream in `lex-app-docs/content/` instead; the mirror brings it back down.

`docs_mirror_guard.yml` enforces this: any PR (other than the sync bot's `docs-sync/*` branches)
that edits a mirror-owned path fails with a message pointing you upstream.

## The manifest

[`docs/.docs-sync.yml`](../.docs-sync.yml) is the single declaration of which `docs/` paths are
mirror-owned. Everything not listed there is internal-only and owned by this repo. Add a path to
`managed_paths` only when a brand-new published section appears upstream.

## Workflow files

| File | Repo | Purpose |
|---|---|---|
| `.github/workflows/sync_docs_from_lex_app_docs.yml` | `lex-app` | Mirror managed paths down; open PR |
| `.github/workflows/docs_mirror_guard.yml` | `lex-app` | Reject local edits to mirror-owned docs |
| `.github/scripts/docs_mirror.py` | `lex-app` | `sync` + `check-readonly` (reads the manifest) |
| `notify-lex-app-docs-sync.yml` | `lex-app-docs` | (optional) instant-sync trigger — reference copy below |

Reference copy for the `lex-app-docs` side:
[`docs/ci-cd/docs-content-sync-dispatch.yml`](docs-content-sync-dispatch.yml) →
`lex-app-docs/.github/workflows/notify-lex-app-docs-sync.yml`.

## Triggers

- **Daily cron (06:00 UTC)** — backstop; works with zero changes to `lex-app-docs`.
- **Manual** — `workflow_dispatch` from the Actions tab (use for the first run / on demand).
- **Inbound `repository_dispatch` (`docs-content-updated`)** — near-instant; requires installing the
  reference dispatch workflow above in `lex-app-docs`.

## Auth

Reuses the existing **lex-docs-bot** GitHub App (`DOCS_APP_ID` / `DOCS_APP_PRIVATE_KEY` on
`lex-app`), which already has contents:read on `lex-app-docs`. No new secrets on the `lex-app` side.
The optional inbound dispatch needs a token on `lex-app-docs` that can write `repository_dispatch`
to `lex-app` (same App, or a scoped PAT).

## First run / verification

1. Trigger `sync_docs_from_lex_app_docs.yml` manually (`workflow_dispatch`).
2. Confirm the opened PR touches **only** paths under `docs/.docs-sync.yml`'s `managed_paths`.
3. Confirm the PR title/body records the upstream `content/` short SHA.
4. Merge — local `docs/` is now in sync.
