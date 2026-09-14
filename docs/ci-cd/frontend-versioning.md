# How the frontend gets into a release

**For:** anyone cutting a LEX release, or wondering which frontend a release shipped.

---

## The shape of it

LEX is one product written in two languages. The backend is Python — that is `lex-app`, the thing
customers install with `pip install lex-app`. The frontend is React, compiled down to fifteen plain
files that the browser runs and Django serves as static content.

Those files are published separately, as a Python package called **`lex-app-frontend`**, and
`lex-app` depends on it like any other dependency:

```
# requirements.txt
lex-app-frontend~=1.12.0
```

That one line is both the dependency pip installs **and** the release's provenance record. It is
plain text, readable from any tag, and it shows up in the pull request that changed it — unlike a
6.3 MB minified diff, which nobody reviews.

The frontend is packaged as a **Python** wheel for one reason: pip is what already delivers LEX, and
pip only fetches Python packages. A wheel is a zip file with a name and a version; this one holds
compiled JavaScript and a few lines of Python saying where it is. Nothing about the frontend becomes
Python.

---

## First-time setup

Once, before the first frontend release.

**The PyPI project.** There is no "create project" button — a project exists the moment something
is uploaded under its name. So creating `lex-app-frontend` means publishing it. PyPI normalises
names, so `lex-app-frontend` and `lex_app_frontend` are the same project; registering one takes
both.

**The token.** This is the part with a chicken-and-egg. A *project-scoped* token can only be minted
for a project that already exists, so the first upload needs something broader. Two ways:

- **An account-scoped token.** PyPI → Account settings → API tokens → "Entire account". Store it on
  `process-admin-general-client` as `PYPI_API_TOKEN_FRONTEND`. Publish once. Then mint a token
  scoped to `lex-app-frontend`, replace the secret, and **delete the account-scoped one** — left in
  a repository it can publish anything we own, `lex-app` included.

- **Trusted Publishing, with no token at all.** PyPI supports a *pending publisher*: register the
  trust relationship before the project exists, and the first upload creates it. PyPI → Your
  projects → Publishing, with project `lex-app-frontend`, owner `ExcellenceCloudGmbH`, repository
  `process-admin-general-client`, workflow `publish-frontend.yml`. Then add `id-token: write` to the
  job's permissions and drop the `password:` line.

  lex-app tried OIDC once and fell back to an API token after an `invalid-publisher` error. That
  error means the publisher was never registered on PyPI's side — a configuration gap, not a verdict
  on OIDC. A pending publisher is exactly the mechanism for a project that does not exist yet.

Until the secret exists, the publish workflow refuses a real run in its first ten seconds rather
than failing at the upload twenty minutes in. Dry runs are unaffected and exercise everything up to
the publish step.

## Releasing the frontend

In `process-admin-general-client`, publish a GitHub Release tagged `v1.12.0` — the same gesture as
releasing lex-app.

The publish workflow derives `1.12.0` from the tag, runs the tests and the coverage gate, builds,
wraps the output in a wheel, and publishes it to PyPI. The tag is the source of truth for the
version, exactly as `lex/_version.py` is for lex-app.

Nothing in lex-app changes yet. The frontend is available; no customer sees it.

## Picking it up

One line, one pull request:

```diff
- lex-app-frontend~=1.11.2
+ lex-app-frontend~=1.12.0
```

That commit is what makes the release note possible. It is a decision with a date, an author and a
reviewer.

## Releasing lex-app

Publish a prerelease, read the drafted note, promote it. Nothing frontend-specific happens: pip
resolves the pin at install time, on the customer's machine.

---

## Why `~=` and not `==`

`~=1.12.0` means "1.12.anything, at least 1.12.0". It exists so a project can take a patch release
without waiting for a lex-app release:

```
# a project's own requirements.txt
lex-app==2.3.0
lex-app-frontend~=1.12.3
```

An exact `==` in lex-app would make that impossible. pip treats every requirement as a constraint
that must hold at once, so two different `==` pins are unsatisfiable and the install fails outright:

```
ERROR: Cannot install lex-app-frontend==1.12.0 and lex-app-frontend==1.12.3
ERROR: ResolutionImpossible
```

**The consequence, worth knowing:** a release note describes the version lex-app *declared*, not
necessarily the one a given instance resolved. To see what an instance actually has:

```bash
pip show lex-app-frontend | grep Version
```

---

## Answering "which frontend is in this release?"

```bash
git show v2.3.0:requirements.txt | grep lex-app-frontend
```

That is the whole answer. Two of those — the previous release's and this one's — give a range of
frontend releases, and the frontend half of the note is `git log` between those tags.

**Releases cut before the pin existed** are served by a committed side-car mapping each old bundle to
the frontend revision that built it. Those entries were established by rebuilding each candidate and
comparing the compiled output, which is content-addressed, so the attribution is proven rather than
inferred. The side-car never grows again.

---

## Rolling back

Actions → **Roll back the frontend** → give it a version and a reason.

It checks the version exists on PyPI, repins `requirements.txt`, and opens a pull request. It
deliberately does not release — you merge and cut a patch release, so the rollback is reviewed and
lands in history like anything else.

It refuses a version that cannot be installed. Pinning something that does not exist turns a
rollback into a broken release, which is the worst outcome for a change made in a hurry.

**The release note must say the frontend was rolled back, and why.** `v2.1.3` shipped the redesign
and `v2.1.4` pulled it back out with no note at either end — customers were told about a feature and
then silently lost it. The reason you give the workflow is the text to use.

---

## When something goes wrong

Before lex-app is built, the pipeline reads the pin and asks PyPI whether that version exists. A
pin naming an unpublished version fails the release there, which is the last point where the answer
is a red job: the pin ships in the wheel's metadata, so once published every `pip install lex-app`
fails to resolve and nothing can be taken back. No pin at all passes, and does not call PyPI.

After publishing, the pipeline installs the release from PyPI into a throwaway environment and
checks it. It fails loudly if:

- the release is not installable — usually the pin names a version that was never published;
- `lex-app-frontend` did not come with it — usually the pin fell out of `requirements.txt`, which is
  what `pyproject.toml` reads as the dependency list;
- the installed frontend carries no usable bundle — it was published from a build that produced no
  SPA.

A resolution failure is reported immediately rather than retried, because a pin naming a version
that does not exist will not fix itself by waiting.

If neither an installed package nor an in-tree bundle can be found at boot, LEX says so on stderr.
The alternative is every page 404ing with nothing in the logs to explain it.

---

## What this replaced

The compiled frontend used to be committed straight into lex-app. Nothing recorded which source
produced it, so working that out afterwards took a manifest file, a merge guard, a lookup table
built by rebuilding candidates and comparing compiled output, and a repair path for when it could
not be worked out at all.

A version number does the same job in one line, before the release is built rather than after.

Related: [`release-notes.md`](release-notes.md) for how the notes themselves are produced.
