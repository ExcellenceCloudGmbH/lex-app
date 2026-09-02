# Release notes: how they work

**For:** anyone who cuts a lex-app release, or wonders why a note says what it says.

---

## What gets produced

Every release produces **two** things, because two different people read them.

| | Who reads it | What it looks like |
|---|---|---|
| **The release body** on GitHub | customers, support | Plain prose. *"Date columns now show the date only, with the full timestamp on hover."* Never mentions repositories, files, or PR numbers. |
| **`CHANGELOG.md`** | engineers | Mechanical. Every line links a commit. Frontend entries tagged `**frontend**`. |

Both are generated from the same list of changes, so they can never disagree about *what*
shipped — only about how much detail to give.

---

## Where the content comes from

lex-app is two codebases. The backend lives here. The frontend lives in a separate repo
(`process-admin-general-client`, "PAC") and arrives as a **pre-built bundle** copied into
`lex/react/build/`.

So to describe a release we need commits from both. The backend half is easy — it's this repo's
own git log. The frontend half needs one extra fact: **which PAC commit produced the bundle we're
shipping.**

That fact is recorded in a small file next to the bundle:

```
lex/react/build/.frontend-version.json
{"repo": "…/process-admin-general-client", "branch": "…", "sha": "22c16f9…", "built_at": "…"}
```

Two of those — the one in the previous release and the one in this release — give a range of PAC
commits. That range is the frontend half of the note.

---

## The two moments in a release

### 1. You publish a **prerelease**

A note is drafted into the release body. Nothing is published anywhere else — no PyPI, no Docker.
This is your review step.

Read the note. Edit the prose if you want; it's yours, and nothing downstream overwrites it.

### 2. You promote it to a **full release**

Now things happen: PyPI, the Docker image, the docs pipeline, and `CHANGELOG.md` gets its entry
committed.

---

## When the frontend can't be worked out

Sometimes the range can't be resolved — an old release with no record, a missing credential, a
checkout that failed.

The old behaviour was to quietly leave the frontend out. That was the actual bug: **a release that
lost the information looked identical to a release with no frontend changes.** Same silence, two
completely different meanings.

Now it says so:

```markdown
## [2.1.8] - 2026-09-01

> **Frontend changes for this release are not yet recorded.**

### Fixed
- **backend** stop the grid dropping rows
```

Nothing breaks. The release ships. The marker is a note-to-self that something is worth going back
for — and because it lives in `CHANGELOG.md`, you can find every one of them with a command.

---

## Backfill: fixing things after the fact

**Backfill re-generates a release's changelog entry.** That's all it is. It's used for two jobs.

### Job 1: releases that came before any of this existed

We have releases going back to `v2.1.1` with no notes at all. Backfill writes them:

```bash
python -m release_notes backfill --from v2.1.1 --to v2.1.7 --pac-checkout ../process-admin-general-client
```

It walks the tags oldest-first, generates each entry, and prepends it. Already-written entries are
**skipped**, so if it dies halfway you just run it again.

### Job 2: closing a gap

Find them, then fix one:

```bash
python -m release_notes list-gaps          # → 2.1.8
python -m release_notes backfill --tag v2.1.8 --force
```

`--force` is what lets it overwrite an entry that already exists. Without it, backfill would see
the existing entry and skip.

The rewrite replaces **only that release's section**. Other releases' entries — and their markers —
are untouched.

### One thing backfill won't do

It won't rewrite a **published release body**. That's prose a human read and edited; overwriting it
to add a frontend line would throw that work away. So for a release that's already out, there's a
separate command that **appends**:

```bash
python -m release_notes append-frontend-note --tag v2.1.6
```

`CHANGELOG.md` is mechanical, so it can be replaced. A release body is written by a person, so it
can only be added to. That asymmetry is deliberate.

---

## Stories

### Melih ships a sidebar fix

He fixes the sidebar in PAC and merges it. Then he opens Actions → **Push PAC Build to dpag-pip** →
Run.

The workflow builds PAC, copies the bundle into lex-app, and — because it's the job doing the
building, it's the only thing that knows — writes down its own commit. It opens a PR. Melih merges
it.

He never typed a commit hash. He couldn't have got it wrong.

### Hazem cuts v2.1.8

He publishes the prerelease. A minute later the release body has a drafted note in it, mixing
Melih's sidebar fix with the backend work, described by what a user would notice. He tightens one
sentence and promotes it.

`CHANGELOG.md` picks up the same changes, with Melih's entry tagged `**frontend**`.

### A release goes out with a broken credential

The token that lets the release read PAC has expired. The checkout fails.

The release still ships. The gate shows a warning; the changelog entry carries the marker. Two days
later someone fixes the token and runs `list-gaps`, sees `2.1.8`, runs
`backfill --tag v2.1.8 --force`, and the entry is complete.

Nothing was lost — it was just late.

### Filling in the back catalogue

Nobody has notes for `v2.1.1` through `v2.1.7`. Hazem runs the backfill for that range and gets
seven entries.

He reads them **as a batch**, not one at a time — that's how you notice a systematic problem rather
than seven individual oddities. Then he commits.

### Someone spots a missing frontend line on a shipped release

`v2.1.6` went out three weeks ago and its body doesn't mention a frontend change. Rewriting it
would destroy the wording someone edited at the time.

So: `append-frontend-note --tag v2.1.6 --dry-run` first, to see what it would add. Then the same
without `--dry-run`. The original text is untouched; the frontend detail is appended below it.

---

## Why it's hard to get wrong

- **The build records its own commit.** Nobody types a hash, so nobody mistypes one.
- **A bundle without that record can't merge.** A guard blocks the PR — and it checks the record is
  *valid*, not just present.
- **Nothing here can fail a release.** No token, no PAC, a broken checkout, a model that won't
  answer — worst case you get a marker and fix it later.
- **A guess is never stored as a fact.** Historical bundles were matched by rebuilding PAC and
  comparing the output filenames, which are content hashes. Each record says whether it was proven
  that way or merely inferred.

---

## Commands

| Command | Does | `--pac-checkout` | `--dry-run` |
|---|---|---|---|
| `list-gaps` | which releases are missing frontend notes | — | — |
| `backfill --from A --to B` | write entries for a range; skips ones already written | yes | yes |
| `backfill --tag T --force` | rewrite one release's entry — the repair path | yes | yes |
| `verify-frontend --tag T` | check a release before publishing; never fails | — | — |
| `append-frontend-note --tag T` | add frontend detail to an already-published release body | yes | yes |
| `check-manifest` | validate the provenance file; runs in the bundle-PR guard | — | — |

The flags are not universal, so the columns above are worth trusting over habit.
`verify-frontend` needs no PAC checkout because it only asks whether the provenance
*resolves* — a question answered entirely from this repo's manifest and side-car.
Reading the frontend commit log is a separate step, and that one does need PAC.

---

## Still outstanding

**Shipping a new frontend bundle.** PAC's build workflow is currently disabled in GitHub Actions.
Until someone enables it, no new bundle can be shipped — existing ones are unaffected.

**A model key for local backfill.** Drafting works in CI, but `backfill` runs on a workstation and
needs its own key.
