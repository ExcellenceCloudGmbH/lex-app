# Frontend release notes

**For:** anyone who cuts a lex-app release.

---

## The problem

lex-app ships the frontend as a built bundle copied in from another repo (`process-admin-general-client`, "PAC").

Every release note we've published has an **empty frontend section** — and you couldn't tell,
because a release that *lost* the information looked exactly like one with no frontend changes.

## The fix, in three sentences

1. When a frontend bundle is copied into lex-app, the build **writes down which PAC commit made it**.
2. At release time we read that, so the note can describe frontend changes alongside backend ones.
3. When we *can't* work it out, the changelog says so out loud instead of going quiet.

That third one is the important bit. **"We don't know" now looks different from "nothing changed."**

---

## What you do

### Shipping a frontend change

Run **"Push PAC Build to dpag-pip"** in PAC. It builds, copies the bundle into lex-app, records
where it came from, and opens a PR. Merge it. You never type a commit hash.

### Cutting a lex-app release

Nothing new.

- **Publish the prerelease** → the note is drafted into the release body. Read it, edit the prose if
  you like.
- **Promote it to a full release** → PyPI, Docker image, and `CHANGELOG.md` all follow.

Two outputs, because two audiences:

| | Reader | Style |
|---|---|---|
| Release body | customers | frontend and backend mixed together by what a user notices |
| `CHANGELOG.md` | engineers | frontend entries tagged `**frontend**` |

### If something's missing

You'll see a warning in the prerelease gate, and the changelog entry will say:

> **Frontend changes for this release are not yet recorded.**

Nothing breaks — the release ships. Fix it whenever:

```bash
python -m release_notes list-gaps                    # what's missing
python -m release_notes backfill --tag v2.1.6 --force  # fix one
```

---

## Why it's hard to get wrong

- **The build records its own commit.** The job that made the bundle is the only thing that knows
  which commit it came from, so it writes it. Nobody can forget.
- **A bundle without that record can't merge.** A guard blocks the PR.
- **Nothing here can fail a release.** No token, no PAC, a broken checkout — worst case you get the
  marker and fix it later.

---

## Still needed

| | Why |
|---|---|
| **PAC workflow writes the record** | Not built yet. Until it is, frontend bundle PRs fail the guard. |
| **Fill in the history table** | Old releases predate the record. Three entries cover all of `v2.1.x`. |

A token (`FRONTEND_REPO_TOKEN`) lets the release workflows read PAC. It's configured but not yet
proven on a real run.

---

## Commands

| Command | Does |
|---|---|
| `list-gaps` | which releases are missing frontend notes |
| `backfill --tag T --force` | fix one release |
| `backfill --from A --to B` | fill a range |
| `verify-frontend --tag T` | check a release before publishing |
| `append-frontend-note --tag T` | add frontend detail to an already-published release |

All take `--pac-checkout PATH`. Add `--dry-run` to see without writing.
