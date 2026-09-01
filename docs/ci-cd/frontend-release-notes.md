# Frontend release notes — how it works, and what it needs from you

**Audience:** whoever cuts a lex-app release, or wonders why a release note never mentions the frontend.

---

## The problem, in one paragraph

lex-app ships the frontend as a **built bundle copied in from another repository** —
`process-admin-general-client`, "PAC". So when we release lex-app, the release note should describe
both halves: the backend changes from this repo, and the frontend changes from PAC.

It never has. Every release note we have ever published has an empty frontend section.

Worse, you could not tell. A release that lost the frontend information looked exactly like a
release that genuinely had no frontend changes. Same silence, two completely different meanings.

This is the fix.

---

## Why it was broken

The tooling was always *able* to describe frontend changes. Three things stopped it.

**Nobody told it where PAC was.** The command has accepted a `--pac-checkout` argument for months.
No workflow ever passed it. So the frontend half of every release was computed from an empty list.

**Nothing recorded which frontend build shipped.** A release contains `lex/react/build/` — a folder
of minified JavaScript with content-hashed filenames like `index-BD1SQUOi.js`. Nothing in it says
which PAC commit produced it. Without that, "what changed in the frontend between v2.1.6 and
v2.1.7" is unanswerable, because you do not know where to start or stop looking.

**The bundles were committed by hand.** 76 times. Each one lost the only piece of information that
could have answered the question.

---

## How it works now

### 1. The build records where it came from

When a new frontend bundle is shipped into lex-app, PAC's own workflow writes one small file
alongside it:

```json
{
  "repo":     "ExcellenceCloudGmbH/process-admin-general-client",
  "branch":   "lex-app-v2-pac-latest",
  "sha":      "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d",
  "built_at": "2026-09-01T10:00:00Z"
}
```

`lex/react/build/.frontend-version.json`.

The important part is **who writes it**. PAC's workflow is the only place in either repository that
knows which commit produced this bundle — it is the job doing the building. lex-app just receives a
folder of hashed files. So the answer is recorded by the one process that has it, automatically,
from `$GITHUB_SHA`. Nobody types a commit hash, and nobody can forget to.

A guard backs that up: any pull request that changes `lex/react/build/` without a valid manifest
**fails**. Not just "a manifest is present" — the sha must be a real 40-character commit. A blank
one, an abbreviation, or a stray newline all block the merge, because those are the fingerprints of
a hand edit.

> **There is no frontend "version number".** PAC's `package.json` has said `0.2.0` since forever and
> has changed exactly once in its history, so it identifies nothing. The frontend's identity is its
> **commit**. That was a deliberate decision, not an omission.

### 2. Releasing reads both repositories

At release time the workflows check out PAC next to lex-app and pass `--pac-checkout`. The tool then
asks one question: *which frontend commit shipped in the previous release, and which ships in this
one?* Two commits define a range, and PAC's git log for that range becomes the frontend half of the
digest.

One digest, two outputs, because two different people read them:

| | Who reads it | What it looks like |
|---|---|---|
| **Release body** | customers, support | Frontend and backend woven together by what a *user* notices. Never mentions repositories or components — a business reader does not know PAC exists. |
| **`CHANGELOG.md`** | engineers | Mechanical. Every frontend entry prefixed `**frontend**`, because here knowing *where* a change came from is the whole point. |

They come from the same digest, so they can never describe different sets of changes.

### 3. When it cannot be answered, it says so

This is the part that matters most, and it is the reason the old behaviour was dangerous.

Old releases have no manifest, and one cannot be added retroactively — you cannot put a file into a
git tag that already exists. So for those, provenance comes from a small committed lookup table
mapping each vendored bundle to the PAC commit that built it.

If neither source can answer, the release's changelog section gets a visible line:

```markdown
## [2.1.8] - 2026-09-01

> **Frontend changes for this release are not yet recorded.**

### Fixed
- **backend** stop the grid dropping rows
```

That single line is the whole point. **"We do not know" now looks different from "nothing
changed."** And because it lives in `CHANGELOG.md` rather than a side file, it cannot drift out of
sync with the release it describes — and you can find every one of them with a command.

Nothing about this blocks a release. A prerelease gets a warning while a human is still reviewing
it; a published release gets the marker; and either way the note ships. A gap is a thing to come
back to, not a thing to stop for.

---

## What you actually do

### Shipping a frontend change

Run the **"Push PAC Build to dpag-pip"** workflow in PAC. It builds, copies the bundle into lex-app,
records the provenance, and opens a pull request. Merge it. That is all — provenance is not a step
you perform.

It is deliberately a button rather than automatic on every push: shipping the frontend is a
decision, not a side effect of committing.

### Cutting a release

Nothing new. Publish the prerelease, read the drafted note, promote it.

Watch for one thing in the gate log:

```
::warning title=Frontend notes unavailable::No frontend provenance for v2.1.7 …
```

That tells you the note will not mention frontend changes, while you can still decide whether to
care.

### Finding and fixing gaps

```bash
python -m release_notes list-gaps
```

Prints the versions carrying the marker — your repair queue. To close one:

```bash
python -m release_notes backfill --tag v2.1.6 --force --pac-checkout ../process-admin-general-client
```

That re-renders just that release's changelog section and drops its marker, leaving every other
release alone.

For a release already published, you can add the frontend detail without touching the prose someone
wrote:

```bash
python -m release_notes append-frontend-note --tag v2.1.6 --dry-run
```

It **appends** and never rewrites. `CHANGELOG.md` is mechanical so it can be replaced wholesale; a
published release body contains prose a human reviewed and edited, and overwriting that to fix a
frontend gap would destroy their work. Drop `--dry-run` when you are happy.

---

## What this needs from you

Three things, none of which can be done from inside the code. **The first one gates everything
else.**

### 1. A token that can read PAC — *blocking*

PAC is a private repository, so the workflows need credentials to check it out.

| Token | State |
|---|---|
| `FRONTEND_REPO_TOKEN` | What `frontend_build.yml` documents as **mandatory** for reading PAC — and it has **never been configured** |
| `LEX_PACKAGES_TOKEN` | Configured, and what the workflows currently use. Its only other use targets a *different* repository for artifact downloads, so its access to PAC is **unverified** |
| `lex-docs-bot` App | Installed on `lex-app` and `lex-app-docs` — **not on PAC** |

That absence explains something that has looked broken for months: `frontend_build.yml` has failed
its secrets preflight on every run since rc218. It was not malfunctioning. It was correctly
reporting a missing token that nobody configured.

**Pick one:** configure `FRONTEND_REPO_TOKEN` with Contents:read on PAC; confirm
`LEX_PACKAGES_TOKEN` already has it; or extend the `lex-docs-bot` App to PAC and use its token.

If the checkout fails, the prerelease gate degrades gracefully — you still get the warning and the
note. The publish job will go red with a clear authentication error.

### 2. Merge PR #702 — *this branch is stacked on it*

#702 gives the drafter the **PR bodies**, not just the one-line subjects. The difference is not
subtle: for `v2.1.7rc1` it is 17,233 characters of author-written explanation where there were a few
hundred.

Your PR descriptions are essays. #675 records the actual customer report; #678 explains that
embedded sessions used to die at the original deadline regardless. That *is* the release note,
already written by the person who made the change. Without #702 the drafter guesses from nine words.

This branch is based on #702's branch, so it merges after it.

### 3. Populate the historical lookup table — *one afternoon, then never again*

Old releases need their bundles attributed to PAC commits. It is smaller than it sounds: **206
release tags collapse to 58 distinct bundles**, and within the `v2.1.x` range there are only
**three**.

| Releases | Bundle committed |
|---|---|
| `v2.0.0rc221`, `v2.1.1`, `v2.1.2` | 2026-06-30 |
| `v2.1.3` | 2026-07-14 |
| `v2.1.4` – `v2.1.7` | 2026-07-22 |

And it can be **proven** rather than guessed. Vite asset filenames are content hashes, so rebuilding
PAC at a candidate commit and comparing the emitted filename to the committed one is proof of a
match. Needs `NPM_MARMELAB_TOKEN` to build.

Each entry records whether it was proven or inferred, so a guess is never stored looking like a
certainty.

---

## Once all three are done

```bash
python -m release_notes backfill --from v2.1.1 --to v2.1.7 \
  --pac-checkout ../process-admin-general-client
```

Review the seven notes as one batch — reading them together is how you notice a systematic problem
rather than seven individual oddities — then commit. From then on it maintains itself.

---

## Deliberately not built

- **An in-app "What's new" screen.** Would need the notes shipped inside the pip package plus a UI.
  A product feature; its own design.
- **Publishing to the Hub (Quackback).** The interface is stubbed and the call site written. The one
  unknown is whether Quackback exposes an API for creating posts — worth checking whether it accepts
  inbound email per board first, because the test report is already emailed on every release and that
  would make this a one-line change.
- **Reviving PAC release versions.** Contradicts the decision that the frontend is a build artifact
  identified by its commit.
- **Backfilling the 200 release candidates.** Nobody reads rc notes.

---

## Commands

| Command | Does |
|---|---|
| `verify-frontend --tag T` | Reports whether provenance resolves. Never fails. Runs in the gate. |
| `list-gaps` | Versions whose changelog carries the marker — the repair queue. |
| `backfill --from A --to B` | Renders changelog sections. Skips ones already written. |
| `backfill --tag T --force` | Re-renders one release, clearing its marker. The repair path. |
| `append-frontend-note --tag T` | Appends frontend detail to a published release body. Never rewrites. |
| `check-manifest` | Validates the provenance file. Runs in the bundle-PR guard. |

All take `--pac-checkout PATH`; the write commands take `--dry-run`.
