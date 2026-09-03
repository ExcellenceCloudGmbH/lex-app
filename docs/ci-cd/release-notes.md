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

That fact is a version number. `frontend-version.txt` in the repository root says which frontend
a release ships — an exact version, or `latest`. The release pipeline installs it and copies it
into `lex/react/build/` before building the wheel. See
[`frontend-versioning.md`](frontend-versioning.md) for how that works.

For the notes, two of those versions — the previous release's and this one's — give a range of
frontend releases, and the frontend half of the note is the log between them.

**A release that said `latest` cannot be attributed from git**, because nothing in the tag records
what "latest" meant that day. Those report a gap rather than a guess. The resolved version is still
recorded inside the shipped bundle and on the GitHub release, so it is recoverable by hand — it is
just not something the notes can read.

**Releases cut before this mechanism** are served by a committed side-car mapping each old bundle
to the frontend revision that built it. Those three entries were established by rebuilding each
candidate and comparing the compiled output, which is content-addressed — so the attribution is
proven rather than inferred. The side-car never grows again.

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

**This can no longer happen for a release cut after the pin landed.** A pin resolves before
anything is built, or the build fails — there is no state where a release ships successfully and
its frontend identity turns out to be unknown afterwards. The marker, and the repair commands that
go with it, now apply only to the older releases.

---

## What the drafter is told

The prose is written by a model, so what reaches its prompt decides the quality of the note.
Three kinds of context go in, and each one exists because a note came out wrong without it.

**The author's own explanation.** Each entry carries a `detail` — the pull request body, or the
commit message body when there was no pull request. That second half matters more than it sounds:
every timezone change in `v2.1.4` landed without a pull request, so the drafter used to see nothing
but one-line subjects while the root cause, who was affected and what to run sat unread in the
commit messages.

**Release facts, computed from the diff.** Whether the release adds a migration, whether it adds a
command an operator has to run, and whether it introduces new configuration. A model cannot work
these out from prose, and it should not try — `v2.1.3`'s note told customers to expect a migration
that had actually shipped two releases earlier. These are now handed over as facts, and the upgrade
note has to agree with them.

**Whether the interface changed.** Not just *what* changed but which of three answers applies: it
changed and here is how, it genuinely did not change, or we could not determine it. The drafter is
told to state the middle case explicitly and forbidden from claiming it in the third — which is the
same distinction the gap marker draws in the changelog, carried through into the prose.

A release whose every entry is internal never reaches the model at all. It returns "no user-facing
changes" directly, because asking a model to write a customer note from thirty-four release-tooling
commits invites it to promote our own machinery into a feature.

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
