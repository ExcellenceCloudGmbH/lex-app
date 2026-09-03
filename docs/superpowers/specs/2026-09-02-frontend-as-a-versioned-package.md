# Proposal: ship the frontend as a versioned pip package

> **Status:** proposal, for decision
> **Date:** 2026-09-02, rewritten 2026-09-03 to match what was built
> **Decided already:** the 2026-09-02 Hazem/Melih catch-up adopted "versioned front-end
> dependencies … to simplify the build process and remove the complex manifest and sidecar system."
> **This document answers the follow-up action item:** *select the publication path and present the
> chosen approach with supporting reasoning.*

---

## 1. The proposal, in full

**Publish the compiled frontend to PyPI as a package called `lex-frontend`. At lex-app release
time, install it and copy its files into `lex/react/build/` before the wheel is built. Record which
version was used.**

Concretely, four changes:

| # | Where | Change |
|---|---|---|
| 1 | `process-admin-general-client` | `package.json` gets a real version (`1.12.0`), the single source of truth for both the wheel version and the git tag. |
| 2 | `process-admin-general-client` | A `workflow_dispatch` job builds the frontend, wraps the output in a Python wheel, publishes it to PyPI, and tags the repo. |
| 3 | `lex-app` | `frontend-version.txt` holds `latest` or an exact version. |
| 4 | `lex-app` | The release pipeline installs that version and vendors it into `lex/react/build/` before `python -m build`. |

**Nothing else changes.** `pyproject.toml` still packages `lex/react/**`. `settings.py` still serves
`lex/react/build`. Customers still run `pip install lex-app`. The frontend still ships inside the
lex-app wheel. What changes is only **how those files get into the tree**: installed from a
versioned package instead of committed by hand.

### What is being asked

Four decisions, none of them reversible-by-accident:

1. **Approve the approach** — a published package instead of a committed bundle.
2. **Approve `latest` as the default** in `frontend-version.txt`, accepting that an unpinned release
   is not attributable from git alone (see §6).
3. **Create the `lex-frontend` PyPI project and a `PYPI_API_TOKEN_FRONTEND` secret.** The existing
   `PYPI_API_TOKEN` is scoped to `lex-app` and cannot publish a different project.
4. **Decide PAC's default branch.** `main` is 312 commits behind `lex-app-v2-pac-latest`, and
   `workflow_dispatch` is only offered for workflows on the *default* branch — so the publish job
   cannot be run until either the default moves or the workflow is cherry-picked to `main`.

---

## 2. Where we are, measured

| | |
|---|---|
| Frontend bundle committed into lex-app | 15 files, **6.3 MB** at `lex/react/build/` |
| Commits that have touched it | 76 |
| What its history weighs, packed | 154 blob versions, **42.1 MB** |
| Provenance machinery built to label it | **319 lines** — `ranges.py` 267, `manifest.py` 35, side-car 17 |
| lex-app tags carrying no frontend label | 226 |
| PAC's own version line | 226 tags, highest `v1.11.2`, **dead since 2025-11-20** |
| `package.json` version | `0.2.0` — never tracked the tags; 4 commits ever touched the line |

### Why the current design needs 319 lines

A compiled bundle cannot say where it came from. `index-BD1SQUOi.js` is content-addressed output
with no origin. Every mechanism we built is archaeology against that one fact:

- a **manifest** to label new bundles,
- a **guard** so a bad label cannot merge,
- a **side-car plus hash-proof rebuilds** to label the bundles already in the tree,
- **gap markers, `list-gaps`, `backfill --force`, `append-frontend-note`** for when the label is
  missing or unreadable.

A version number does the same job in one line, and does it *before* the release is built rather
than after.

---

## 3. The candidate paths, and why this one

**Chosen — publish a Python wheel; lex-app installs it at release time and vendors it.**

**Rejected — publish to npm; lex-app runs npm during its build.** This puts Node, a lockfile and
registry auth into the sequence that runs tests → PyPI → Docker → docs. That path is
release-critical and is already where our failures happen.

**Rejected — publish a wheel; lex-app depends on it at runtime** (`lex-frontend==1.10.0` in
`requirements.txt`). This was the previous version of this proposal and it was **wrong**: that line
is a hard dependency, so merging it before the package is published breaks *every*
`pip install lex-app`. Build-time vendoring has no such ordering trap.

**Rejected — npm only, resolved at container build or runtime.** The pip package must be
self-contained; 47 of 53 production instances are plain installs with no build step available.

### Why pip and not npm

1. **The consumer is Python.** lex-app is a pip package published to PyPI, and the Dockerfile
   installs it with a bare `pip install`. Whatever delivers the frontend must be reachable from a
   Python build. pip already is.
2. **This is not a module, it is a bag of files.** The wheel contains **one** Python file and
   **15 static assets**. Nothing imports it; Django serves the directory. npm's value —
   dependency resolution, transitive deps, tree-shaking — does not apply to compiled output handed
   to a browser.
3. **Consumption needs no credential.** PAC needs 10 paid `@react-admin/*` packages from
   `registry.marmelab.com` to *build*; they are bundled into the output, so consumers never need
   them — but reading a *private npm registry* still requires auth. A wheel on public PyPI requires
   none, and declares no dependencies. This is what makes `NPM_MARMELAB_TOKEN` stop being every
   consumer's problem rather than merely moving it.
4. **One channel to mirror, monitor and audit.** Customers already reach PyPI for lex-app. A second
   registry is a second thing to authenticate, monitor, and — for any enterprise mirroring PyPI
   internally — a second mirror to stand up.
5. **A 6.3 MB minified diff is unreviewable.** Nobody reviews one. A one-line version bump is
   genuinely reviewable.

### Where npm would win, stated fairly

If a **JavaScript consumer** ever needs the frontend — `lex-components`, or embedding it in another
JS app — npm is the natural home and a wheel is useless to it. Publishing to both from one version
number is roughly twenty lines of workflow. The recommendation is to add it *when such a consumer
exists*, not before.

---

## 4. What this does to release-notes complexity

This is the point of the change.

| Mechanism | Why it exists today | After |
|---|---|---|
| `.frontend-version.json` manifest | compiled output cannot identify itself | **gone** for new releases — the version identifies it |
| `frontend_manifest_guard.yml` | a malformed label must not merge | **gone** — an unpublished version fails the build outright |
| `ranges.py` bundle→commit resolution | must work backwards from artifact to commit | **reduced to reading two version files** |
| hash-proof archaeology | 226 tags predate any label | **never needed again** |
| gaps, `list-gaps`, `--force`, `append-frontend-note` | provenance can fail *after* a successful release | **impossible for a pinned release** |

The last row is the structural win. Today provenance can fail after a release has already shipped,
and that single possibility is what forces the whole eventual-consistency layer: record a gap, find
it later, repair it — with a *different command* depending on whether a human has since edited the
release body. With a pinned version there is no "resolved later". It resolves before the build, or
the build fails.

### What survives, stated plainly

This does not delete the provenance work; it **bounds** it.

- The side-car **freezes** into a read-only lookup for the 226 pre-version tags. It never grows
  again, and the code that *writes* it goes.
- Gap markers remain meaningful for those historical tags, and for any release built from `latest`.
- `backfill` stays, for history.

---

## 5. Versioning scheme

**Resume the existing line at `v1.12.0`.**

- It is iterative, which is what was decided.
- It does not collide with lex-app's `2.x`, which is precisely why `2.0.0` was rejected for the
  frontend.
- It reuses the line already in the repository instead of inventing a third numbering.
- It is the next free number. The highest existing tag is **`v1.11.2`**, so `1.10.0` and `1.11.x`
  are taken — a first draft of this proposal said `1.10.0`, which the publish job's
  already-tagged check would have correctly refused.

Two corrections to make at the same time:

1. **`package.json` becomes the source of truth**, and the publish job derives the tag from it. The
   current `0.2.0`-versus-`v1.9.0` split is exactly the failure to design out — a version field
   nobody increments and nothing reads.
2. The first published note should say what it is: **the first version published as a package,
   covering roughly nine months of untagged work** — the line has been idle since 2025-11-20, and
   the branch that actually feeds releases (`lex-app-v2-pac-latest`) diverged even earlier, at
   `v1.7.0`. Calling that a routine minor bump without explanation misleads.

---

## 6. The one real trade-off: `latest`

`frontend-version.txt` defaults to `latest`, which is what the team asked for and needs no
maintenance — a frontend fix reaches customers on the next release with nothing to update by hand.

**It is also not reproducible from a git tag.** Six months on, `git show` says the release asked for
"latest"; it cannot say what that meant on the day. Left alone, that reintroduces exactly the
problem this proposal removes.

So the resolved version — **read back from what pip actually installed**, not assumed — is written
to three places that outlive the run:

- inside the bundle as `.frontend-version.json`, and therefore inside the published wheel;
- on the GitHub release, as a `lex:frontend-version` marker;
- in the workflow log.

Release notes read the version file at the tag. An exact version resolves a real frontend range;
**`latest` reports a gap rather than guessing.** That is the honest answer, and it is the reason to
pin any release that may need reconstructing.

**Recommendation:** `latest` for ordinary releases, an exact version when a release matters enough
that someone may need to rebuild it. If you would rather every release be attributable from git,
the alternative is to pin by default and accept a one-line edit per release — say so and I will
flip the default.

---

## 7. Costs and risks, honestly

- **The release pipeline gains a network dependency.** If PyPI is unreachable, the release stops.
  Deliberate: shipping a release with no frontend, or a stale one, is worse. Three failure
  modes — version absent, install left nothing, package carries no usable bundle — all fail loudly
  and all fail *before* anything is published.
- **A new PyPI project and token.** One-time setup, listed in §1.
- **PAC's default branch.** Not caused by this change, but it blocks running the publish job. lex-app
  solved the same problem by switching its default to `lex-app-v2`.
- **Local development is unaffected** — the committed bundle stays in the tree for now and continues
  to serve. Removing it from git is a separate, later step, gated on a real release having shipped
  through the new path.
- **The history does not shrink.** The 6.3 MB stops growing, but the 42.1 MB already in the pack
  stays unless the repository is rewritten — which is **not** recommended with 226 tags and shared
  clones.
- **Timing argues for now.** The happy path has never run in production: zero manifests committed,
  PAC's build workflow disabled. We have not yet paid the maintenance cost of the copy-in design, so
  switching costs almost nothing in sunk work. In a year there will be real manifests, real gaps,
  real repairs, and habits built on them.

---

## 8. Status of the implementation

Written and open for review, so this document describes something that exists rather than something
imagined:

| PR | What |
|---|---|
| `process-admin-general-client#475` | the wheel, the assembler, the publish workflow |
| `lex-app#746` | `frontend-version.txt`, release-time vendoring, version recording, docs |
| `lex-app#744` | the task-by-task implementation plan |

Verified rather than asserted: a real `vite build` emits `index-BD1SQUOi.js` and
`index-isLX8XQ_.css` — byte-identical to the bundle lex-app ships today — so the branch produces the
frontend currently in production. Historical release-note resolution is unchanged.

Operator documentation: `docs/ci-cd/frontend-versioning.md`.
