# Publishing the frontend as a versioned package

> **Status:** proposal, for decision
> **Date:** 2026-09-02
> **Decided already:** the 2026-09-02 Hazem/Melih catch-up adopted "versioned front-end
> dependencies … to simplify the build process and remove the complex manifest and sidecar system."
> **This document answers the follow-up action item:** *select the publication path and present the
> chosen approach with supporting reasoning.*

---

## 1. Where we are, measured today

| | |
|---|---|
| Frontend bundle committed into lex-app | 15 files, **6.3 MB** at `lex/react/build/` |
| Commits that have touched it | 76 |
| What its history weighs, packed | 154 blob versions, **42.1 MB** |
| Provenance machinery built to label it | **319 lines** — `ranges.py` 267, `manifest.py` 35, side-car 17 |
| lex-app tags carrying no frontend label | 225 |
| PAC's own version line | 225 tags, **dead since 2025-06-18** (`v1.9.0`) |
| `package.json` version | `0.2.0` — never tracked the tags; 4 commits ever touched the line |
| Private packages needed to **build** PAC | 10 × `@react-admin/*` from `registry.marmelab.com` |

Two of those rows carry more weight than they look, and both are argued below: the **dead version
line**, and the **10 private build-time packages**.

### Why the current design needs 319 lines

A compiled bundle cannot say where it came from. `index-BD1SQUOi.js` is content-addressed output
with no origin. Every mechanism we built is archaeology against that one fact:

- a **manifest** to label new bundles,
- a **guard** so a bad label cannot merge,
- a **side-car plus hash-proof rebuilds** to label the bundles already in the tree,
- **gap markers, `list-gaps`, `backfill --force`, `append-frontend-note`** for when the label is
  missing or unreadable.

## 2. The candidate paths

**A — npm publish, lex-app fetches at wheel-build time.** PAC publishes a built package. lex-app
records the version in a plain-text pin and its wheel build runs npm to unpack `dist/` into
`lex/react/build/` before packaging.

**B — npm publish plus a companion Python wheel; lex-app pins it as a dependency.** One PAC release
publishes both artifacts from one version number. lex-app declares
`dependencies = ["lex-frontend==1.10.0"]` and pip does the rest.

**C — npm only, resolved at container build or runtime.** Rejected: the pip package must be
self-contained. 47 of 53 production instances are plain installs with no build step available to
them.

## 3. Recommendation — B, with a single publish job producing both artifacts

`package.json`'s version is the source of truth; the npm package and the wheel take the same
number; one workflow in PAC emits both.

**Why B over A:**

1. **lex-app's release path stays a pure Python build.** Path A puts node and registry
   authentication into the sequence that runs PyPI → Docker → docs. That path is release-critical
   and is already where our failures happen. B adds nothing to it: lex-app gains one line of text.
2. **The 10 private `@react-admin` packages stop being everyone's problem.** They are needed to
   *build* PAC, not to *consume* its output — the built bundle already contains them. Today
   anything that wants the frontend needs `NPM_MARMELAB_TOKEN`; afterwards only PAC's publish job
   does. That credential has repeatedly blocked work on both repos.
3. **The pin is the provenance record.** Plain text, in the PR diff, reviewable — not a record
   *about* an artifact stored beside it, but the thing you had to write down in order to build.
4. **npm gives the version line the team asked for**, and serves the JS-side consumers (the
   `lex-components` work, local development) that a wheel alone would not.
5. **Today's frontend update is an unreviewable 6.3 MB minified diff.** Nobody reviews that. A
   one-line version bump is genuinely reviewable.

## 4. What this does to release-notes complexity

This is the point of the change, so it is worth being precise about it.

| Mechanism | Why it exists today | After the pin |
|---|---|---|
| `.frontend-version.json` | compiled output cannot identify itself | **gone** — the pin identifies it |
| `frontend_manifest_guard.yml` | a malformed label must not merge | **gone** — pip is the guard; an unpublished version fails the build outright |
| `ranges.py` bundle→commit resolution | must work backwards from artifact to commit | **reduced to reading two pins** and `git log` between PAC's own tags |
| hash-proof archaeology | 225 tags predate any label | **never needed again** — no new unlabelled release can exist |
| gaps, `list-gaps`, `--force`, `append-frontend-note` | provenance can fail *after* a successful release | **impossible for new releases** |

The last row is the structural win. Today provenance can fail after a release has already shipped,
and that single possibility is what forces the entire eventual-consistency layer: record a gap,
find it later, repair it — with a *different command* depending on whether a human has since edited
the release body. With a pin there is no "resolved later". It resolves at build time or there is no
build. No partial state to reconcile.

**A second simplification comes free.** Once PAC is a released product it has its own
`CHANGELOG.md`. lex-app's note can then *compose* the frontend's own notes for the versions
crossed, rather than deriving prose from commit archaeology. That also removes the PAC checkout —
and with it `FRONTEND_REPO_TOKEN` — from the notes path entirely.

### What survives, stated plainly

This does not delete the provenance work; it **bounds** it.

- The side-car **freezes** into a read-only lookup table for the 225 pre-pin tags. It never grows
  again, and the code that *writes* it goes.
- Gap markers remain meaningful for those historical tags only.
- `backfill` stays, for history.

## 5. Versioning scheme

**Resume the existing line at `v1.10.0`.**

- It is iterative, which is what was decided.
- It does not collide with lex-app's `2.x`, which is precisely why `2.0.0` was rejected for the
  frontend.
- It reuses the line already in the repository instead of inventing a third numbering.

Two corrections to make at the same time:

1. **`package.json` becomes the source of truth**, and the publish job derives the tag from it. The
   current `0.2.0`-versus-`v1.9.0` split is exactly the failure mode to design out — a version
   field nobody increments and nothing reads.
2. The first published release note should say what it is: **the first published version, covering
   15 months of previously unversioned work.** Calling that a minor bump without explanation would
   be misleading.

## 6. Sequence

0. **Ship the pending release through today's pipeline first.** It is needed either way, and
   PR #739 makes the drafter survive a rejected draft rather than silently mislabelling it.
1. **PAC:** set a real `version`, `publishConfig` and `files`; add the publish workflow; publish
   `v1.10.0` to both targets. This largely *replaces* `push-build-to-pip-package.yml` — comparable
   work, different destination — and folds in the already-agreed rename of that workflow.
2. **lex-app:** add the dependency pin; point `settings.py:234` at the installed package; remove
   `lex/react/build` from the tree and from `[tool.setuptools.package-data]`.
3. **Release notes:** replace `frontend_sha_at` with pin-reading; keep the side-car read-only for
   tags below the first pinned release.
4. **Retire:** `frontend_manifest_guard.yml`, the manifest writer, `check-manifest`.

## 7. Costs and risks, honestly

- **Django's static path changes.** One settings line plus a test — but it is release-critical, so
  it must be verified on a real installed wheel, not only in CI.
- **Local development changes habit.** The frontend arrives via pip rather than a clone. Frontend
  developers need a documented escape hatch (an editable install pointed at a local checkout).
  This is the change most likely to annoy people day to day, and it needs writing down before the
  switch, not after.
- **Two registries.** Mitigated by one job and one version number, but it is still two places a
  publish can fail.
- **The history does not shrink.** The 6.3 MB leaves the working tree, but the 42.1 MB already in
  the pack stays unless the repository is rewritten — which is **not** recommended with 225 tags
  and shared clones. The win is that it *stops growing*, not that it reverses.
- **Timing.** The argument for deciding now rather than later is that the happy path has never run
  in production: zero manifests committed, PAC's build workflow disabled. We have not yet paid the
  maintenance cost of the copy-in design, so switching costs almost nothing in sunk work. In a
  year there will be real manifests, real gaps, real repairs, and real habits built on them.

## 8. Decisions still needed

1. **Registry** — GitHub Packages npm, or npmjs private? *Recommend GitHub Packages*: same org
   auth, no new billing relationship, and `LEX_PACKAGES_TOKEN` already exists.
2. **Package name** — `@excellencecloudgmbh/lex-frontend`?
3. **Who bumps the version** — automatic on merge to PAC's default branch, or a deliberate release
   action? A deliberate action matches the decision that the frontend is not released on push.
4. **Note composition** — ship PAC's `CHANGELOG.md` inside the package (removes the PAC checkout
   and its token from the notes path), or keep reading PAC's git log for richer prose? These can be
   sequenced: ship the changelog now, keep the git-log path as an option.
