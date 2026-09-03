# How the frontend gets into a release

**For:** anyone cutting a LEX release, or wondering which frontend a release shipped.

---

## The shape of it

LEX is one product written in two languages:

- **The backend** is Python / Django. This *is* `lex-app` — the thing customers install with
  `pip install lex-app`.
- **The frontend** is React / TypeScript. It gets compiled down to fifteen plain files — some
  JavaScript, a stylesheet, an HTML page, a few icons. The browser runs those. Python never
  executes any of them; Django just serves them as static files.

Those fifteen files have always shipped *inside* the Python package. That is what this line in
`pyproject.toml` means:

```toml
"lex.react" = ["**/*"]
```

What changed is not *that* they ship inside it — it is **how they get there**.

| | Before | Now |
|---|---|---|
| Where the files come from | committed into lex-app's git, 6.3 MB per update | published once to PyPI as `lex-frontend` |
| How they reach a release | someone opens a PR that copies them in | the release pipeline installs them |
| Which frontend shipped | nothing recorded it | recorded in three places (below) |

The frontend is published as a **Python wheel** for one reason: `pip` is what already delivers LEX
to customers, and `pip` only knows how to fetch Python packages. A wheel is just a zip file with a
name and a version — this one holds the compiled frontend plus a few lines of Python that say where
the files are. Nothing about the frontend becomes Python.

---

## What happens when you publish a release

You publish a release in the GitHub releases page. Then, before the lex-app wheel is built:

1. The pipeline reads **`frontend-version.txt`** in the repository root.
2. It runs `pip install lex-frontend` — at that version, or the newest one if the file says
   `latest`.
3. It copies the installed files into **`lex/react/build/`**.
4. It writes down which version it actually used.

Then the wheel is built as usual, with the frontend inside it, and published to PyPI.

You do not have to do anything for this to happen. The default works.

---

## Choosing a frontend version

`frontend-version.txt` holds one line:

```
latest
```

or

```
1.10.0
```

**`latest`** takes whatever is newest on PyPI at the moment the release is built. It is the
default, and it means a frontend fix reaches customers as soon as you cut a release, with nothing
to update by hand.

**An exact version** takes that one and nothing else. Use it when you want a release you can
rebuild identically later, or when the newest frontend is not one you want to ship yet.

### The trade-off worth understanding

`latest` is convenient, and it is **not reproducible from git**. Six months from now, `git show`
on the tag will tell you the release asked for "latest" — it cannot tell you what "latest" meant
that day.

So the pipeline writes the resolved version down in three places:

- **inside the bundle**, as `lex/react/build/.frontend-version.json` — which means it is inside the
  published wheel, so you can always ask an installed copy what it has;
- **on the GitHub release**, as a hidden marker in the release body;
- **in the workflow log**, for the run itself.

That keeps an unpinned release attributable. But only an **exact version** in
`frontend-version.txt` makes it answerable from git alone — and that is what the release notes
read. A release built from `latest` will report its frontend section as *not yet recorded* rather
than guessing.

**Rule of thumb:** `latest` for ordinary releases, an exact version when the release matters enough
that someone may need to reconstruct it.

---

## Publishing a new frontend

In the frontend repository (`process-admin-general-client`):

1. Bump `version` in `package.json`. That one field is the source of truth — the wheel version and
   the git tag are both derived from it.
2. Actions → **Publish frontend** → Run workflow.
3. Leave **dry run** ticked the first time. It builds everything and publishes nothing, so you can
   see what would ship.
4. Run it again with dry run unticked.

The job runs the unit tests and the coverage gate first, builds, packages, publishes to PyPI, and
tags the repository last — deliberately last, because a tag pointing at a version that failed to
publish is worse than no tag.

It refuses to run if the version has already been tagged. Bump `package.json` and try again.

---

## Answering "which frontend is in this release?"

**If the release pinned a version** — the fastest answer:

```bash
git show v2.3.0:frontend-version.txt
```

**Otherwise**, ask the artifact. In any environment with lex-app installed:

```bash
cat "$(python -c 'import lex.react, pathlib; print(pathlib.Path(lex.react.__file__).parent)')/build/.frontend-version.json"
```

That file records the version, what was requested (`latest` or a pin), and when it was vendored.

**Or look at the release** on GitHub — the pipeline leaves a `lex:frontend-version` marker in the
body.

---

## When something goes wrong

The vendoring step **stops the release** rather than shipping something broken. It fails if:

- the requested version does not exist on PyPI — check `frontend-version.txt` against what has
  actually been published;
- pip reports success but installs nothing recognisable — a broken or partial upload;
- the installed package contains no usable frontend — the wheel was published from a build that did
  not produce one.

All three are loud, and all three happen before anything is published. A release that gets past
this step has a real frontend in it.

---

## What this replaced

Before, the compiled frontend was committed straight into lex-app. Nothing recorded which frontend
source produced it, so working that out afterwards took a manifest file, a merge guard, a lookup
table for historical releases built by rebuilding candidates and comparing compiled output, and a
repair path for when it could not be worked out at all.

A version number does the same job in one line, and does it *before* the release is built rather
than after. Those older mechanisms still exist, and still serve releases cut before this change —
they just do not grow any more.

Related: [`release-notes.md`](release-notes.md) for how the notes themselves are produced.
