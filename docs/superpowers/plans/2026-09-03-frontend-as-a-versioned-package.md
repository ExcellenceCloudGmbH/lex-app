# Frontend as a Versioned Package — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the LEX frontend as a versioned package that lex-app depends on by name, so a release's frontend identity is a version pin in plain text instead of an unlabelled 6.3 MB bundle committed into git.

**Architecture:** One publish job in PAC emits two artifacts from one version number — an npm package (the version line the team asked for, and the artifact JS consumers can use) and a companion Python wheel (`lex-frontend`, which is what lex-app actually depends on). lex-app pins that wheel in `requirements.txt`; `settings.py` resolves the bundle from the installed package, falling back to the in-tree copy so nothing breaks mid-migration. The release-notes pipeline then reads the pin at two tags and takes `git log` between PAC's own tags, which retires the manifest, the guard and all of the gap machinery for new releases.

**Tech Stack:** Node 20 / yarn 1.22 / Vite (PAC), Python 3.12 / setuptools (both wheels), GitHub Packages npm registry, PyPI, GitHub Actions.

---

## Context an implementer will not have

**The two repositories.** `lex-app` is the Django framework, published to PyPI, default branch `lex-app-v2`. `process-admin-general-client` ("PAC") is the React frontend; its default branch is `main`, but the branch that feeds releases is `lex-app-v2-pac-latest`. PAC is *not* released today — `yarn build` output is committed into `lex-app` at `lex/react/build/` and rides along in the pip package.

**Why this is being changed.** A compiled bundle cannot say which source revision produced it, so 319 lines of machinery exist to work it out backwards: a manifest, a merge guard, a hash-proof side-car for the 225 tags that predate the manifest, and gap markers with a repair command for when resolution fails after a release has already shipped. A version pin answers the same question in one line of plain text, in the PR diff, before the build. See `docs/superpowers/specs/2026-09-02-frontend-as-a-versioned-package.md`.

**Measured starting state (2026-09-03).**

| | |
|---|---|
| Committed bundle | 15 files, 6.3 MB at `lex/react/build/` |
| Commits that have touched it | 76 |
| Its packed history | 154 blob versions, 42.1 MB |
| PAC `package.json` version | `0.2.0` — never tracked the tags; 4 commits ever touched the line |
| PAC git tags | 225, **dead since 2025-06-18** at `v1.9.0` |
| Private deps needed to *build* PAC | 10 × `@react-admin/*` from `registry.marmelab.com` |

**The whole consumer surface in lex-app is three places.** Verified by grep, not assumed:

- `lex/lex_app/settings.py:233-235` defines `REACT_APP_BUILD_PATH`
- `lex/lex_app/urls.py:63` is its only consumer — serves the SPA
- `lex/test_project/tests/init/test_1p_settings_urls_views.py:167-176` (scenario 1.130) asserts it resolves

Plus `pyproject.toml:41` (`"lex.react" = ["**/*"]`), which packages the bundle into the wheel.

**Version decision.** Resume PAC's existing line at **`1.12.0`**. The highest existing tag is
`v1.11.2` (2025-11-20), so `1.10.0` and `1.11.x` are taken — an earlier draft of this plan said
`1.10.0`, which Task 4's already-tagged check would have correctly refused. Substitute `1.12.0`
wherever a task below writes `1.10.0`. It is iterative, which is what the 2026-09-02 catch-up decided; it does not collide with lex-app's `2.x`, which is why `2.0.0` was rejected for the frontend; and it reuses the line already in the repo rather than inventing a third numbering. `package.json` becomes the single source of truth and the tag is derived from it — the current `0.2.0`-versus-`v1.9.0` split is precisely the failure this designs out.

**Honest scoping note.** The npm package is not on lex-app's critical path — the wheel is what lex-app installs. npm publishing earns its place by giving the version line an immutable artifact, `npm view` history, and something the `lex-components` work can consume. Do not let an npm problem block the wheel.

**Standing rules for every task.**

- Never `git add -A` or `git add .` in either repository. `lex-app` carries ~140 untracked files; stage only the paths a task names.
- `NPM_MARMELAB_TOKEN` must be set to any non-empty value for `yarn` to run at all — it hard-fails while normalising `.npmrc` even when nothing needs downloading.
- Run the release-notes suite with `PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests -q` from the `lex-app` root.

---

## File Structure

**Created in PAC:**

| Path | Responsibility |
|---|---|
| `packaging/pyproject.toml` | Declares the `lex-frontend` wheel. Nothing but packaging. |
| `packaging/lex_frontend/__init__.py` | The wheel's only code: `build_path()` returns the bundle directory. |
| `packaging/README.md` | What the wheel is, for the PyPI project page. |
| `.github/workflows/publish-frontend.yml` | The one publish job: gate → version → build → npm → wheel → tag. |
| `scripts/assemble_wheel.py` | Copies `build/` into `packaging/lex_frontend/build/` and stamps the version. |

**Modified in PAC:**

| Path | Change |
|---|---|
| `package.json` | Real `version`, drop `private`, add `files` + `publishConfig`. |
| `.npmrc` | Add the publish scope's registry alongside the existing `@react-admin` line. |
| `.github/workflows/push-build-to-pip-package.yml` | Deleted in Task 12, replaced by `publish-frontend.yml`. |

**Modified in lex-app:**

| Path | Change |
|---|---|
| `lex/lex_app/settings.py` | `REACT_APP_BUILD_PATH` resolves the installed package, falling back in-tree. |
| `requirements.txt` | Pin `lex-frontend==1.10.0`. This pin *is* the provenance record. |
| `pyproject.toml` | Drop `"lex.react" = ["**/*"]` (Task 12). |
| `.github/scripts/release_notes/ranges.py` | Read the pin at a ref; resolve the range from PAC tags. |
| `.github/scripts/release_notes/__main__.py` | Pass PAC tags rather than shas. |
| `.github/workflows/frontend_manifest_guard.yml` | Deleted in Task 13. |

---

## Task 1: PAC declares a real package identity

**Files:**
- Modify: `package.json` (PAC)
- Modify: `.npmrc` (PAC)
- Test: `scripts/__tests__/package-identity.test.ts` (PAC)

- [ ] **Step 1: Write the failing test**

Create `scripts/__tests__/package-identity.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import pkg from '../../package.json'

// package.json is the single source of truth for the version: the publish job
// derives the npm version, the wheel version and the git tag from this one
// field. The repo previously carried `0.2.0` while its tags said `v1.9.0`,
// which is the drift this test exists to prevent.
describe('package identity', () => {
  it('is publishable', () => {
    expect(pkg.private).toBeUndefined()
    expect(pkg.name).toBe('@excellencecloudgmbh/lex-frontend')
  })

  it('carries a real semver version that continues the existing tag line', () => {
    expect(pkg.version).toMatch(/^\d+\.\d+\.\d+$/)
    const [major] = pkg.version.split('.').map(Number)
    expect(major).toBeGreaterThanOrEqual(1)
  })

  it('ships only the compiled bundle', () => {
    // Publishing src/ would ship the paid @react-admin sources to anyone who
    // can read the registry. The bundle already contains what consumers need.
    expect(pkg.files).toEqual(['build'])
  })

  it('publishes to GitHub Packages, not npmjs', () => {
    expect(pkg.publishConfig?.registry).toBe('https://npm.pkg.github.com')
  })
})
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
NPM_MARMELAB_TOKEN=x npx vitest run scripts/__tests__/package-identity.test.ts
```

Expected: FAIL — `pkg.private` is `true`, name is `process-admin-general-client`, version is `0.2.0`, `files` and `publishConfig` are undefined.

- [ ] **Step 3: Make the change**

In `package.json`, replace the first lines:

```json
{
  "name": "@excellencecloudgmbh/lex-frontend",
  "version": "1.10.0",
  "files": ["build"],
  "publishConfig": {
    "registry": "https://npm.pkg.github.com",
    "access": "restricted"
  },
```

Delete the `"private": true` line entirely.

- [ ] **Step 4: Add the publish registry to `.npmrc`**

Append to `.npmrc`, keeping the existing `@react-admin` lines untouched:

```
@excellencecloudgmbh:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}
```

`NODE_AUTH_TOKEN` is what `actions/setup-node` populates. It is a different variable from `NPM_MARMELAB_TOKEN`, and both must be present during a publish.

- [ ] **Step 5: Run the test to confirm it passes**

```bash
NPM_MARMELAB_TOKEN=x npx vitest run scripts/__tests__/package-identity.test.ts
```

Expected: PASS, 4 tests.

- [ ] **Step 6: Confirm nothing else read the old name**

```bash
grep -rn "process-admin-general-client" --include="*.ts" --include="*.tsx" --include="*.json" src/ scripts/ | grep -v node_modules
```

Expected: no output. If anything matches, it is a code reference to the package name and must be updated in this task.

- [ ] **Step 7: Commit**

```bash
git add package.json .npmrc scripts/__tests__/package-identity.test.ts
git commit -m "feat(packaging): give PAC a publishable identity at 1.10.0

package.json becomes the single source of truth for the version. The repo
carried 0.2.0 while its tags said v1.9.0 — a field nobody incremented and
nothing read. 1.10.0 resumes the existing line rather than inventing a third
numbering, and does not collide with lex-app's 2.x.

files is restricted to build/ so a publish cannot ship the paid @react-admin
sources to anyone who can read the registry."
```

---

## Task 2: The wheel's only code — locating the bundle

**Files:**
- Create: `packaging/lex_frontend/__init__.py` (PAC)
- Create: `packaging/pyproject.toml` (PAC)
- Create: `packaging/README.md` (PAC)
- Test: `packaging/tests/test_build_path.py` (PAC)

- [ ] **Step 1: Write the failing test**

Create `packaging/tests/test_build_path.py`:

```python
"""The wheel exists to answer one question: where is the bundle?

lex-app's settings.py calls build_path() and serves whatever it returns. If it
returns a path that does not exist, every page 404s — so it fails loudly here
rather than serving an empty directory.
"""

from pathlib import Path

import pytest

import lex_frontend


def test_build_path_returns_a_real_directory():
    path = lex_frontend.build_path()
    assert isinstance(path, Path)
    assert path.is_dir(), f"{path} is not a directory"


def test_the_bundle_contains_an_index_html():
    # The SPA entry point. Its absence means the wheel was assembled from an
    # empty or partial build, which must not pass as a working package.
    assert (lex_frontend.build_path() / "index.html").is_file()


def test_the_version_is_importable_and_matches_the_distribution():
    from importlib.metadata import version
    assert lex_frontend.__version__ == version("lex-frontend")


def test_build_path_raises_when_the_bundle_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(lex_frontend, "_PACKAGE_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="bundle is missing"):
        lex_frontend.build_path()
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd packaging && python -m pytest tests/test_build_path.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'lex_frontend'`.

- [ ] **Step 3: Write the module**

Create `packaging/lex_frontend/__init__.py`:

```python
"""The compiled LEX frontend, as an installable package.

This package is data, not code: one function that says where the bundle is.
It is published from process-admin-general-client alongside the npm package of
the same version, so the version installed here identifies the exact frontend
source revision that produced the bundle.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent

try:
    __version__ = version("lex-frontend")
except PackageNotFoundError:      # running from a source tree, not installed
    __version__ = "0.0.0.dev0"

__all__ = ["build_path", "__version__"]


def build_path() -> Path:
    """The directory holding the compiled bundle.

    Raises rather than returning a missing path: the caller serves this
    directory as the whole single-page app, and a silently absent bundle turns
    into every page 404ing with nothing in the logs to explain it.
    """
    path = _PACKAGE_DIR / "build"
    if not path.is_dir():
        raise FileNotFoundError(
            f"the frontend bundle is missing from {path} — the wheel was built "
            "without running `yarn build`, or assembled from an empty tree"
        )
    return path
```

- [ ] **Step 4: Declare the wheel**

Create `packaging/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "lex-frontend"
description = "The compiled LEX frontend bundle, served by lex-app."
readme = "README.md"
requires-python = ">=3.10"
license = {text = "Proprietary"}
dynamic = ["version"]

# No dependencies, deliberately. The bundle is self-contained: the ten paid
# @react-admin packages are needed to BUILD it, never to consume it.
dependencies = []

[tool.setuptools.dynamic]
version = {attr = "lex_frontend._VERSION"}

[tool.setuptools.packages.find]
include = ["lex_frontend*"]

[tool.setuptools.package-data]
"lex_frontend" = ["build/**/*"]
```

- [ ] **Step 5: Add the version attribute setuptools reads**

`dynamic = ["version"]` above reads `lex_frontend._VERSION`, which must be a
plain literal — setuptools evaluates it without installing the package, so the
`importlib.metadata` lookup in `__version__` cannot serve this purpose.

Insert into `packaging/lex_frontend/__init__.py`, directly below `_PACKAGE_DIR`:

```python
# Overwritten by scripts/assemble_wheel.py at publish time from package.json.
# setuptools reads this literal to set the distribution version, so it cannot
# be computed — see [tool.setuptools.dynamic] in pyproject.toml.
_VERSION = "0.0.0.dev0"
```

- [ ] **Step 6: Write the README**

Create `packaging/README.md`:

```markdown
# lex-frontend

The compiled LEX frontend bundle. Installed by `lex-app`, which serves the
directory returned by `lex_frontend.build_path()`.

This package is built and published from
`ExcellenceCloudGmbH/process-admin-general-client`, from the same version number
as the `@excellencecloudgmbh/lex-frontend` npm package. The version you have
installed identifies the frontend source revision exactly.

Not intended for direct use.
```

- [ ] **Step 7: Run the tests**

```bash
cd packaging
mkdir -p lex_frontend/build && echo '<!doctype html>' > lex_frontend/build/index.html
pip install -e .
python -m pytest tests/test_build_path.py -q
```

Expected: PASS, 4 tests. (The `mkdir`/`echo` stands in for a real `yarn build`; Task 3 automates it.)

- [ ] **Step 8: Commit**

```bash
git add packaging/pyproject.toml packaging/lex_frontend/__init__.py \
        packaging/README.md packaging/tests/test_build_path.py
git commit -m "feat(packaging): a lex-frontend wheel that locates the bundle

One function, because that is the whole contract: lex-app calls build_path()
and serves what it returns. It raises on a missing bundle rather than handing
back a path that does not exist — the failure mode there is every page 404ing
with nothing in the logs to say why.

No dependencies, deliberately: the ten paid @react-admin packages are needed
to build the bundle, never to consume it. That is what stops every downstream
consumer needing NPM_MARMELAB_TOKEN."
```

---

## Task 3: Assemble the wheel from a real build

**Files:**
- Create: `scripts/assemble_wheel.py` (PAC)
- Test: `packaging/tests/test_assemble_wheel.py` (PAC)

- [ ] **Step 1: Write the failing test**

Create `packaging/tests/test_assemble_wheel.py`:

```python
"""Assembling the wheel: copy the bundle in, stamp the version from package.json.

The version is taken from package.json rather than passed in, because a second
place to type it is a second place for it to disagree — which is exactly how
the repo came to hold 0.2.0 while its tags said v1.9.0.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import assemble_wheel  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"version": "1.10.0"}))
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("<!doctype html>")
    (build / "assets").mkdir()
    (build / "assets" / "index-ABC123.js").write_text("console.log(1)")
    (tmp_path / "packaging" / "lex_frontend").mkdir(parents=True)
    (tmp_path / "packaging" / "lex_frontend" / "__init__.py").write_text(
        '_VERSION = "0.0.0.dev0"\n'
    )
    return tmp_path


def test_the_bundle_is_copied_into_the_package(repo):
    assemble_wheel.assemble(repo)
    dest = repo / "packaging" / "lex_frontend" / "build"
    assert (dest / "index.html").is_file()
    assert (dest / "assets" / "index-ABC123.js").is_file()


def test_the_version_is_stamped_from_package_json(repo):
    assemble_wheel.assemble(repo)
    text = (repo / "packaging" / "lex_frontend" / "__init__.py").read_text()
    assert '_VERSION = "1.10.0"' in text
    assert "0.0.0.dev0" not in text


def test_assembling_twice_does_not_accumulate_stale_assets(repo):
    assemble_wheel.assemble(repo)
    stale = repo / "packaging" / "lex_frontend" / "build" / "assets" / "index-OLD.js"
    stale.write_text("stale")
    assemble_wheel.assemble(repo)
    # A content-hashed asset from a previous build must not ship inside a later
    # wheel: it would make the package's contents depend on build order.
    assert not stale.exists()


def test_an_empty_build_is_refused(repo):
    for child in (repo / "build").iterdir():
        child.unlink() if child.is_file() else __import__("shutil").rmtree(child)
    with pytest.raises(SystemExit, match="no build output"):
        assemble_wheel.assemble(repo)
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd packaging && python -m pytest tests/test_assemble_wheel.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'assemble_wheel'`.

- [ ] **Step 3: Write the script**

Create `scripts/assemble_wheel.py`:

```python
#!/usr/bin/env python3
"""Copy `build/` into the wheel and stamp its version from package.json.

Run between `yarn build` and `python -m build`. package.json is the only place
the version is written; everything else derives from it.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path


def assemble(repo: Path) -> str:
    """Assemble the wheel's package directory. Returns the version stamped."""
    source = repo / "build"
    if not source.is_dir() or not any(source.iterdir()):
        sys.exit(f"no build output at {source} — run `yarn build` first")
    if not (source / "index.html").is_file():
        sys.exit(f"no index.html in {source} — the build did not produce an SPA")

    version = json.loads((repo / "package.json").read_text())["version"]

    dest = repo / "packaging" / "lex_frontend" / "build"
    # Removed rather than merged: asset filenames are content hashes, so a
    # merge leaves every previous build's assets in place and the wheel's
    # contents start depending on the order it was built in.
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)

    init = repo / "packaging" / "lex_frontend" / "__init__.py"
    text = init.read_text()
    stamped, count = re.subn(
        r'^_VERSION = ".*"$', f'_VERSION = "{version}"', text, count=1, flags=re.M
    )
    if count != 1:
        sys.exit(f"could not find the _VERSION line to stamp in {init}")
    init.write_text(stamped)

    files = sum(1 for _ in dest.rglob("*") if _.is_file())
    print(f"assembled lex-frontend {version}: {files} files from {source}")
    return version


if __name__ == "__main__":
    assemble(Path(__file__).resolve().parents[1])
```

- [ ] **Step 4: Run the tests**

```bash
cd packaging && python -m pytest tests/test_assemble_wheel.py -q
```

Expected: PASS, 4 tests.

- [ ] **Step 5: Prove it against a real build**

```bash
cd .. && NPM_MARMELAB_TOKEN=x yarn build && python scripts/assemble_wheel.py
```

Expected: `assembled lex-frontend 1.10.0: 15 files from .../build`. The count should match `find build -type f | wc -l`.

- [ ] **Step 6: Keep the assembled copy out of git**

Append to `.gitignore`:

```
# Assembled at publish time by scripts/assemble_wheel.py — never committed.
# Committing it would reintroduce exactly the 6.3 MB-per-update history growth
# this whole change removes.
packaging/lex_frontend/build/
```

- [ ] **Step 7: Commit**

```bash
git add scripts/assemble_wheel.py packaging/tests/test_assemble_wheel.py .gitignore
git commit -m "feat(packaging): assemble the wheel from a real build

Copies build/ into the package and stamps _VERSION from package.json, so the
npm version, the wheel version and the git tag all come from one field.

The destination is removed rather than merged: asset filenames are content
hashes, so merging leaves every previous build's assets in place and the
wheel's contents start depending on the order it was built in. There is a
test for that specifically.

The assembled copy is gitignored — committing it would reintroduce the
6.3 MB-per-update history growth this change exists to remove."
```

---

## Task 4: The publish job — one version, two registries, one tag

**Files:**
- Create: `.github/workflows/publish-frontend.yml` (PAC)

**New secret required on PAC:** `PYPI_API_TOKEN_FRONTEND`, scoped to the new `lex-frontend` PyPI project. `lex-app` already has a `PYPI_API_TOKEN`, but it is scoped to `lex-app` and cannot publish a different project. Create the PyPI project and its token before running this workflow.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/publish-frontend.yml`:

```yaml
# Publish the frontend as a versioned package.
#
# Deliberately workflow_dispatch only. Merging UI work must not ship it: a
# release is a decision, and the 2026-09-02 catch-up settled that the frontend
# is not released on push.
#
# One version number (package.json) produces three things that must agree: the
# npm package, the lex-frontend wheel, and the git tag. Any of them derived
# separately is a place for them to drift — which is how the repo came to hold
# 0.2.0 while its tags said v1.9.0.
name: Publish frontend

on:
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Build and assemble, publish nothing, tag nothing."
        required: false
        default: true
        type: boolean

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: write   # create the tag
      packages: write   # publish to GitHub Packages
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # the tag check below needs the tag list

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          registry-url: https://npm.pkg.github.com
          scope: "@excellencecloudgmbh"

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Read the version from package.json
        id: version
        run: |
          set -euo pipefail
          V=$(node -p "require('./package.json').version")
          echo "value=$V" >> "$GITHUB_OUTPUT"
          echo "Publishing version $V"

      # Refuse to reuse a version. npm and PyPI both reject a duplicate, but
      # they reject it AFTER the build, and a half-published pair (npm accepted,
      # PyPI refused) is the state worth never reaching.
      - name: Refuse a version that has already been tagged
        run: |
          set -euo pipefail
          TAG="v${{ steps.version.outputs.value }}"
          if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
            echo "::error::$TAG already exists. Bump the version in package.json."
            exit 1
          fi

      - name: Install dependencies
        run: yarn --frozen-lockfile
        env:
          NPM_MARMELAB_TOKEN: ${{ secrets.NPM_MARMELAB_TOKEN }}
          NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Unit tests with the coverage gate
        run: yarn test --run --coverage --coverage.thresholds.lines=74
        env:
          NPM_MARMELAB_TOKEN: ${{ secrets.NPM_MARMELAB_TOKEN }}

      - name: Build
        run: yarn build
        env:
          NPM_MARMELAB_TOKEN: ${{ secrets.NPM_MARMELAB_TOKEN }}
          CI: false

      - name: Assemble the wheel
        run: python scripts/assemble_wheel.py

      - name: Build the wheel
        run: |
          set -euo pipefail
          python -m pip install --quiet build
          cd packaging && python -m build

      # Everything above is safe to run on a dry run. Everything below is not.
      - name: Publish to GitHub Packages
        if: ${{ inputs.dry_run != true }}
        run: npm publish
        env:
          NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          NPM_MARMELAB_TOKEN: ${{ secrets.NPM_MARMELAB_TOKEN }}

      - name: Publish the wheel to PyPI
        if: ${{ inputs.dry_run != true }}
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          password: ${{ secrets.PYPI_API_TOKEN_FRONTEND }}
          packages-dir: packaging/dist

      # Tag LAST. A tag pointing at a version that failed to publish is worse
      # than no tag: the release-notes range resolution treats a tag as proof
      # the version exists, and would then resolve a range nobody can install.
      - name: Tag the release
        if: ${{ inputs.dry_run != true }}
        run: |
          set -euo pipefail
          TAG="v${{ steps.version.outputs.value }}"
          git tag -a "$TAG" -m "lex-frontend $TAG"
          git push origin "$TAG"

      - name: Summarise
        if: ${{ always() }}
        run: |
          {
            printf '## lex-frontend %s\n\n' "${{ steps.version.outputs.value }}"
            if [ "${{ inputs.dry_run }}" = "true" ]; then
              printf 'DRY RUN — nothing was published or tagged.\n\n'
            fi
            printf 'Wheel contents:\n\n```\n'
            ls -la packaging/dist 2>/dev/null || printf '(no dist)\n'
            printf '```\n\nPin in lex-app with:\n\n```\nlex-frontend==%s\n```\n' \
              "${{ steps.version.outputs.value }}"
          } >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 2: Validate the YAML parses**

```bash
python -c "import yaml; d=yaml.safe_load(open('.github/workflows/publish-frontend.yml')); print('ok:', list(d['jobs']))"
```

Expected: `ok: ['publish']`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/publish-frontend.yml
git commit -m "ci(packaging): publish the frontend as a versioned package

workflow_dispatch only — merging UI work must not ship it. Defaults to a dry
run, so the first invocation cannot publish by accident.

Ordering matters in two places. The tag is created LAST, because a tag
pointing at a version that failed to publish is worse than no tag: the
release-notes range resolution treats a tag as proof the version exists and
would resolve a range nobody can install. And an already-tagged version is
refused up front, because npm and PyPI both reject a duplicate only after the
build, and a half-published pair is the state worth never reaching.

Needs a new secret: PYPI_API_TOKEN_FRONTEND. lex-app's PYPI_API_TOKEN is
scoped to lex-app and cannot publish a different project."
```

- [ ] **Step 4: Dry-run it**

This workflow must be on PAC's default branch before GitHub offers it — `workflow_dispatch` is only available for workflows that live there. Merge, then:

Actions → **Publish frontend** → Run workflow → leave `dry_run` ticked → Run.

Expected in the summary: `DRY RUN — nothing was published or tagged`, a `packaging/dist` listing containing `lex_frontend-1.10.0-py3-none-any.whl`, and the pin line.

- [ ] **Step 5: Real run**

Same, with `dry_run` unticked. Then confirm all three landed:

```bash
npm view @excellencecloudgmbh/lex-frontend version --registry=https://npm.pkg.github.com
pip download lex-frontend==1.10.0 --no-deps -d /tmp/verify
git ls-remote --tags origin | grep v1.10.0
```

Expected: `1.10.0`, a downloaded wheel, and the tag.

---

## Task 5: lex-app resolves the bundle from the installed package

**Files:**
- Modify: `lex/lex_app/settings.py:233-235` (lex-app)
- Test: `lex/test_project/tests/init/test_1p_settings_urls_views.py` (lex-app)

The fallback is what makes this migration safe: an instance with no `lex-frontend` installed keeps working from the in-tree copy, so this task can merge before Task 4 has ever published anything.

- [ ] **Step 1: Write the failing test**

Append to `lex/test_project/tests/init/test_1p_settings_urls_views.py`:

```python
    def test_1_131_react_build_path_prefers_the_installed_frontend_package(self):
        """1.131: an installed lex-frontend wins over the in-tree bundle.

        The pinned wheel is the release's frontend identity. If the in-tree
        copy won, an instance would serve whatever happened to be committed
        rather than the version its release pinned.
        """
        from lex.lex_app import settings as lex_settings

        sentinel = Path(self.__class__._tmp_frontend.name)
        (sentinel / "index.html").write_text("<!doctype html>")

        class _FakePackage:
            @staticmethod
            def build_path():
                return sentinel

        resolved = lex_settings._resolve_react_build_path(package=_FakePackage)
        self.assertEqual(Path(resolved), sentinel)

    def test_1_132_react_build_path_falls_back_to_the_in_tree_bundle(self):
        """1.132: no installed package means the committed bundle is used.

        This is what lets the migration land before anything is published, and
        what keeps a source checkout runnable.
        """
        from lex.lex_app import settings as lex_settings

        resolved = lex_settings._resolve_react_build_path(package=None)
        self.assertTrue(
            resolved.endswith("react/build"),
            f"expected the in-tree bundle, got {resolved}",
        )

    def test_1_133_a_broken_frontend_package_falls_back_rather_than_crashing(self):
        """1.133: a package that cannot locate its bundle must not stop boot.

        An exception here would take the whole instance down at import time.
        Serving the in-tree bundle is strictly better than not starting.
        """
        from lex.lex_app import settings as lex_settings

        class _BrokenPackage:
            @staticmethod
            def build_path():
                raise FileNotFoundError("the frontend bundle is missing")

        resolved = lex_settings._resolve_react_build_path(package=_BrokenPackage)
        self.assertTrue(resolved.endswith("react/build"))
```

Add to the same class's setup, alongside the existing fixtures:

```python
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import tempfile
        cls._tmp_frontend = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls._tmp_frontend.cleanup()
        super().tearDownClass()
```

If the class already defines `setUpClass`/`tearDownClass`, add the two `_tmp_frontend` lines to the existing bodies instead of writing new methods.

- [ ] **Step 2: Run to confirm it fails**

```bash
lex test lex.test_project.tests.init.test_1p_settings_urls_views --verbosity=2 --noinput --keepdb
```

Expected: FAIL — `module 'lex.lex_app.settings' has no attribute '_resolve_react_build_path'`.

- [ ] **Step 3: Implement it**

Replace `lex/lex_app/settings.py:233-235`, which currently reads:

```python
REACT_APP_BUILD_PATH = (
    Path(__file__).resolve().parent.parent / Path("react/build")
).as_posix()
```

with:

```python
def _resolve_react_build_path(package="unset") -> str:
    """Where the single-page app is served from.

    The pinned `lex-frontend` wheel is the release's frontend identity, so an
    installed one wins. The in-tree bundle at lex/react/build remains the
    fallback, which is what lets a source checkout run and what let this change
    land before anything was published.

    A package that is installed but cannot find its own bundle falls back too:
    raising here happens at import time and takes the whole instance down,
    which is strictly worse than serving the committed copy.

    `package` is injected by tests; "unset" means "import it yourself".
    """
    if package == "unset":
        try:
            import lex_frontend as package  # type: ignore[no-redef]
        except ImportError:
            package = None

    if package is not None:
        try:
            return Path(package.build_path()).as_posix()
        except Exception as exc:      # noqa: BLE001 — see the docstring
            print(
                f"lex-frontend is installed but its bundle could not be located "
                f"({exc}); falling back to the in-tree bundle.",
                file=sys.stderr,
            )

    return (Path(__file__).resolve().parent.parent / Path("react/build")).as_posix()


REACT_APP_BUILD_PATH = _resolve_react_build_path()
```

Confirm `sys` is already imported at the top of `settings.py`; if not, add `import sys` alongside the existing `import os`.

- [ ] **Step 4: Run to confirm it passes**

```bash
lex test lex.test_project.tests.init.test_1p_settings_urls_views --verbosity=2 --noinput --keepdb
```

Expected: PASS, including the pre-existing 1.130.

- [ ] **Step 5: Confirm the only consumer still works**

`lex/lex_app/urls.py:63` passes `REACT_APP_BUILD_PATH` as `document_root`. It takes a string and is unchanged by this, but verify rather than assume:

```bash
grep -n "REACT_APP_BUILD_PATH" lex/lex_app/urls.py
```

Expected: line 63, unchanged.

- [ ] **Step 6: Commit**

```bash
git add lex/lex_app/settings.py lex/test_project/tests/init/test_1p_settings_urls_views.py
git commit -m "feat(settings): serve the frontend from the installed package

An installed lex-frontend wins over the in-tree bundle, because the pinned
wheel is the release's frontend identity — if the committed copy won, an
instance would serve whatever happened to be in the tree rather than the
version its release pinned.

The in-tree fallback stays, and it is what makes the migration safe: this can
merge before anything has been published, and a source checkout still runs.

A package that is installed but cannot locate its bundle also falls back,
rather than raising. This runs at import time, so an exception takes the whole
instance down — strictly worse than serving the committed copy. Scenarios
1.131-1.133."
```

---

## Task 6: Pin the version

**Files:**
- Modify: `requirements.txt` (lex-app)
- Test: `.github/scripts/tests/test_release_notes_pin.py` (lex-app)

`pyproject.toml` declares `dependencies = {file = ["requirements.txt"]}`, so a line here becomes a real wheel dependency. **This pin is the provenance record** — the whole point of the change.

- [ ] **Step 1: Write the failing test**

Create `.github/scripts/tests/test_release_notes_pin.py`:

```python
"""The frontend pin is the provenance record, so its shape is load-bearing.

`ranges.frontend_version_at` parses this line at two tags to derive a PAC tag
range. A loose specifier — a range, a caret, an unpinned name — cannot identify
one revision, so it is not provenance and must be rejected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import ranges  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_requirements_pins_the_frontend_to_an_exact_version():
    text = (REPO_ROOT / "requirements.txt").read_text()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("lex-frontend")]
    assert len(lines) == 1, f"expected exactly one lex-frontend line, got {lines}"
    assert ranges.PIN_RE.fullmatch(lines[0]), (
        f"{lines[0]!r} is not an exact pin — a range cannot identify one revision"
    )


@pytest.mark.parametrize("line,ok", [
    ("lex-frontend==1.10.0", True),
    ("lex-frontend==1.10.0rc1", True),
    ("lex-frontend>=1.10.0", False),
    ("lex-frontend~=1.10.0", False),
    ("lex-frontend", False),
    ("lex-frontend==1.10.*", False),
], ids=["exact", "exact-rc", "gte", "compatible", "bare", "wildcard"])
def test_only_an_exact_pin_is_accepted(line, ok):
    assert bool(ranges.PIN_RE.fullmatch(line)) is ok
```

- [ ] **Step 2: Run to confirm it fails**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests/test_release_notes_pin.py -q
```

Expected: FAIL — `module 'release_notes.ranges' has no attribute 'PIN_RE'`.

- [ ] **Step 3: Add the pattern**

Append to `.github/scripts/release_notes/ranges.py`:

```python
# The pin is the provenance record, so only an exact version counts. A range
# ("&gt;=1.10.0") or a wildcard ("1.10.*") resolves to different revisions at
# different times, which is not provenance — it is the ambiguity this design
# removes, wearing a version number.
PIN_RE = re.compile(r"^lex-frontend==(?P<version>\d+\.\d+\.\d+[A-Za-z0-9.]*)$")
```

- [ ] **Step 4: Add the pin**

Append to `requirements.txt`:

```
# The compiled frontend, published from process-admin-general-client. This pin
# IS the release's frontend provenance — see docs/ci-cd/release-notes.md.
lex-frontend==1.10.0
```

- [ ] **Step 5: Run to confirm it passes**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests/test_release_notes_pin.py -q
```

Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .github/scripts/release_notes/ranges.py \
        .github/scripts/tests/test_release_notes_pin.py
git commit -m "feat(release-notes): pin the frontend, and require the pin be exact

pyproject reads dependencies from requirements.txt, so this line is a real
wheel dependency — and it is the release's frontend provenance record, which
is the whole point of the change.

PIN_RE accepts only an exact ==. A range or a wildcard resolves to different
revisions at different times, which is not provenance; it is the same
ambiguity this design removes, wearing a version number."
```

---

## Task 7: Prove it on a real installed wheel

**Files:** none — this is a verification task with no code.

CI installs from a source checkout, where the in-tree fallback masks a broken package path. This is the one failure mode that would reach production silently, so it is checked against a real install rather than a test double.

- [ ] **Step 1: Build both wheels**

```bash
cd <PAC>            && NPM_MARMELAB_TOKEN=x yarn build \
                    && python scripts/assemble_wheel.py \
                    && (cd packaging && python -m build)
cd <lex-app>        && python -m build
```

- [ ] **Step 2: Install both into a clean environment, with no source tree**

```bash
python -m venv /tmp/verify-frontend
/tmp/verify-frontend/bin/pip install --quiet \
    <PAC>/packaging/dist/lex_frontend-1.10.0-py3-none-any.whl \
    <lex-app>/dist/lex_app-*.whl
```

- [ ] **Step 3: Confirm the resolved path is the installed package**

```bash
cd /tmp && /tmp/verify-frontend/bin/python -c "
import lex_frontend, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lex.lex_app.settings')
print('package build_path:', lex_frontend.build_path())
print('index.html present:', (lex_frontend.build_path() / 'index.html').is_file())
"
```

Expected: a path under `site-packages/lex_frontend/build`, and `True`.

Run it from `/tmp`, not from a checkout — `cwd` on `sys.path` would import the source tree and defeat the check.

- [ ] **Step 4: Confirm the bundle is actually served**

Boot a project against this environment and request the root URL:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8000/
curl -sS http://localhost:8000/ | head -c 200
```

Expected: `200`, and HTML containing a `<script src="/assets/index-…js">` tag whose filename matches one in `lex_frontend/build/assets/`. A `200` with an empty body means the path resolved but the directory is empty — which is the exact failure `build_path()` raises on, so investigate rather than accept it.

- [ ] **Step 5: Record the result**

Note the wheel version, the resolved path and the asset filename in the pull request. There is nothing to commit; the evidence is the point.

---

## Task 8: Read the pin at a ref

**Files:**
- Modify: `.github/scripts/release_notes/ranges.py`
- Test: `.github/scripts/tests/test_release_notes_pin.py`

- [ ] **Step 1: Write the failing test**

Append to `.github/scripts/tests/test_release_notes_pin.py`:

```python
REQUIREMENTS = "requirements.txt"


def test_the_pinned_version_is_read_from_requirements_at_a_ref():
    def show(ref, path):
        assert path == REQUIREMENTS, f"read the wrong file: {path}"
        return "django==5.0\nlex-frontend==1.10.0\ncelery==5.3\n"

    assert ranges.frontend_version_at("v2.2.0", show=show) == "1.10.0"


def test_a_tag_with_no_pin_returns_none():
    # Every tag before this change has no pin. That is not an error — it is
    # what sends resolution to the side-car path instead.
    show = lambda ref, path: "django==5.0\ncelery==5.3\n"
    assert ranges.frontend_version_at("v2.1.4", show=show) is None


def test_a_missing_requirements_file_returns_none():
    assert ranges.frontend_version_at("v1.0.0", show=lambda r, p: None) is None


def test_a_loose_specifier_is_not_treated_as_provenance():
    show = lambda ref, path: "lex-frontend>=1.10.0\n"
    assert ranges.frontend_version_at("v2.2.0", show=show) is None


def test_a_commented_out_pin_is_ignored():
    # A commented pin is not a dependency, and reading it would attribute a
    # release to a frontend it does not ship.
    show = lambda ref, path: "# lex-frontend==9.9.9\nlex-frontend==1.10.0\n"
    assert ranges.frontend_version_at("v2.2.0", show=show) == "1.10.0"


def test_the_pinned_version_becomes_a_pac_tag():
    assert ranges.pac_tag_for("1.10.0") == "v1.10.0"
```

- [ ] **Step 2: Run to confirm it fails**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests/test_release_notes_pin.py -q
```

Expected: FAIL — no attribute `frontend_version_at`.

- [ ] **Step 3: Implement**

Append to `.github/scripts/release_notes/ranges.py`:

```python
REQUIREMENTS_PATH = "requirements.txt"


def frontend_version_at(ref: str, *, show=git_show) -> str | None:
    """The pinned frontend version as of `ref`, or None.

    None is the normal answer for every tag cut before the pin existed, and it
    is what sends `frontend_range` to the manifest and side-car path instead.
    """
    blob = show(ref, REQUIREMENTS_PATH)
    if not blob:
        return None
    for line in blob.splitlines():
        match = PIN_RE.fullmatch(line.strip())
        if match:
            return match["version"]
    return None


def pac_tag_for(version: str) -> str:
    """The PAC tag that published `version`.

    Publishing derives the tag from package.json's version, so the mapping is
    exactly this one prefix — see .github/workflows/publish-frontend.yml in
    process-admin-general-client.
    """
    return f"v{version}"
```

- [ ] **Step 4: Run to confirm it passes**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests/test_release_notes_pin.py -q
```

Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/ranges.py .github/scripts/tests/test_release_notes_pin.py
git commit -m "feat(release-notes): read the frontend pin at a ref

Two pins, at two tags, give a PAC tag range — which is the whole of frontend
provenance once the bundle is a dependency instead of a committed artifact.

None is the normal answer, not an error: every tag cut before the pin existed
returns it, and that is what routes resolution to the side-car path instead.
A commented-out pin is ignored, because reading one would attribute a release
to a frontend it does not ship."
```

---

## Task 9: Resolve the range from PAC tags, with the side-car as fallback

**Files:**
- Modify: `.github/scripts/release_notes/ranges.py:245-` (`frontend_range`)
- Test: `.github/scripts/tests/test_release_notes_ranges.py`

The precedence is the point of this task: **pin wins, side-car serves history.** A release with pins at both ends never touches the manifest, the side-car, or the gap machinery.

- [ ] **Step 1: Write the failing test**

Append to `.github/scripts/tests/test_release_notes_ranges.py`:

```python
# ── Pin-based resolution ──────────────────────────────────────────────
#
# Once the frontend is a pinned dependency, provenance is two lines of text at
# two tags. These tests pin the precedence: a pin at BOTH ends wins outright,
# and anything less falls back to the pre-pin machinery rather than guessing.

def _pins(mapping):
    """A `show` that serves a requirements.txt per ref."""
    def show(ref, path):
        if path != "requirements.txt":
            return None
        version = mapping.get(ref)
        return f"lex-frontend=={version}\n" if version else "django==5.0\n"
    return show


def test_pins_at_both_ends_resolve_to_a_pac_tag_range():
    got = ranges.frontend_range(
        "v2.2.0", "v2.3.0",
        show=_pins({"v2.2.0": "1.10.0", "v2.3.0": "1.11.0"}),
    )
    assert (got.from_sha, got.to_sha) == ("v1.10.0", "v1.11.0")


def test_an_unchanged_pin_resolves_to_an_empty_range_not_a_gap():
    # "The frontend did not change" is a real answer and must be reported as
    # one. Before the pin this was indistinguishable from a lost range.
    got = ranges.frontend_range(
        "v2.2.0", "v2.2.1",
        show=_pins({"v2.2.0": "1.10.0", "v2.2.1": "1.10.0"}),
    )
    assert (got.from_sha, got.to_sha) == ("v1.10.0", "v1.10.0")


def test_a_pin_on_only_one_end_falls_back_to_the_side_car():
    # The release that introduces the pin has none at its previous tag. It must
    # use the old path rather than inventing a starting point.
    called = []
    ranges.frontend_range(
        "v2.1.9", "v2.2.0",
        show=_pins({"v2.2.0": "1.10.0"}),
        history=lambda: called.append("history") or {},
        bundle=lambda ref, **k: called.append(ref) or None,
    )
    assert called, "expected the side-car path to be consulted"


def test_no_pin_at_either_end_leaves_historical_behaviour_untouched():
    got = ranges.frontend_range(
        "v2.1.3", "v2.1.4",
        show=_pins({}),
        history=lambda: {"a" * 40: {"pac_sha": "b" * 40}},
        bundle=lambda ref, **k: "a" * 40,
    )
    assert got is not None
    assert got.from_sha == "b" * 40
```

- [ ] **Step 2: Run to confirm it fails**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests/test_release_notes_ranges.py -q -k pin
```

Expected: FAIL — the first assertion returns shas, not `v1.10.0`/`v1.11.0`.

- [ ] **Step 3: Implement**

At the top of `frontend_range`'s body in `.github/scripts/release_notes/ranges.py`, before the existing manifest/side-car logic, insert:

```python
    # The pin wins, and only when BOTH ends have one. Two pins are two exact
    # versions, so the range is known before anything is built — no manifest,
    # no side-car, and no way for it to fail after a release has shipped,
    # which is what retires the gap machinery for new releases.
    #
    # One pin is not enough: the release that introduces the pin has none at
    # its previous tag, and inventing a starting point would attribute every
    # frontend commit in PAC's history to that one release.
    previous_version = frontend_version_at(previous_tag, show=show) if previous_tag else None
    current_version = frontend_version_at(current_tag, show=show)
    if previous_version and current_version:
        return Range(
            from_sha=pac_tag_for(previous_version),
            to_sha=pac_tag_for(current_version),
        )
```

- [ ] **Step 4: Run to confirm it passes**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests/test_release_notes_ranges.py -q
```

Expected: PASS, including every pre-existing side-car test — the historical path must be untouched.

- [ ] **Step 5: Run the whole suite**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests -q
```

Expected: all pass.

- [ ] **Step 6: Verify against real history**

```bash
PYTHONPATH=.github/scripts python -c "
from release_notes import ranges
for a, b in [('v2.1.3','v2.1.4'), ('v2.1.8','v2.1.9')]:
    r = ranges.frontend_range(a, b)
    print(f'{a} -> {b}: {r}')
"
```

Expected: both still resolve through the side-car to 40-character shas, unchanged. No tag has a pin yet, so pin resolution must not have altered anything historical.

- [ ] **Step 7: Commit**

```bash
git add .github/scripts/release_notes/ranges.py .github/scripts/tests/test_release_notes_ranges.py
git commit -m "feat(release-notes): resolve the frontend range from the pins

Pin wins, side-car serves history. A release with pins at both ends never
touches the manifest, the side-car or the gap machinery: two exact versions
are two exact PAC tags, known before anything is built, with no way to fail
after the release has already shipped. That last property is what retires the
whole record-then-repair layer for new releases.

One pin is deliberately not enough. The release that introduces the pin has
none at its previous tag, and inventing a starting point would attribute
every frontend commit in PAC's history to that single release.

An unchanged pin resolves to an empty range rather than a gap — 'the frontend
did not change' is a real answer and now reports as one."
```

---

## Task 10: Read PAC's log between tags

**Files:**
- Modify: `.github/scripts/release_notes/__main__.py` (`_digest_for`, `_pac_log`)
- Test: `.github/scripts/tests/test_release_notes_main.py`

`_pac_log` runs `git log` inside the PAC checkout. It already takes two refs and does not care whether they are shas or tags — but the checkout must have the tags, which a default `actions/checkout` does not guarantee.

- [ ] **Step 1: Write the failing test**

Append to `.github/scripts/tests/test_release_notes_main.py`:

```python
def test_the_pac_log_is_asked_for_a_tag_range_when_the_pins_resolve(monkeypatch, tmp_path):
    """Tag refs must reach `git log` unaltered — PAC tags are the range now."""
    from release_notes import __main__ as main, digest as digest_mod, ranges, facts

    seen = {}

    monkeypatch.setattr(main, "_previous_tag_for", lambda tag: "v2.2.0", raising=False)
    monkeypatch.setattr(
        ranges, "frontend_range",
        lambda p, c, **k: ranges.Range(from_sha="v1.10.0", to_sha="v1.11.0"),
    )
    monkeypatch.setattr(facts, "collect", lambda a, b, **k: {
        "migrations": [], "commands": [], "env_vars": [], "needs_migration": False,
    })

    def fake_collect(a, b, run_log=None, **k):
        seen["range"] = (a, b)
        return []

    monkeypatch.setattr(digest_mod, "collect_commits", fake_collect)

    main._digest_for("v2.3.0", pac_checkout=tmp_path)
    assert seen["range"] == ("v1.10.0", "v1.11.0")
```

- [ ] **Step 2: Run to confirm it fails or passes**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests/test_release_notes_main.py -q -k pac_log
```

If it **passes** immediately, `_pac_log` is already ref-agnostic and no code change is needed — record that in the commit message and skip to Step 4. If it fails, continue.

- [ ] **Step 3: Make the refs pass through unaltered**

Inspect `_pac_log` in `.github/scripts/release_notes/__main__.py`. If it validates its arguments as 40-character shas, relax that: a ref is a ref. Replace any such check with:

```python
    # Refs, not shas: since the frontend became a pinned dependency the range
    # is a pair of PAC TAGS (v1.10.0..v1.11.0). git log treats both alike, and
    # a sha check here would reject the normal case.
```

- [ ] **Step 4: Ensure the workflow fetches PAC's tags**

Modify the PAC checkout step in `.github/workflows/prerelease_gate.yml`, `.github/workflows/publish_release_notes.yml` and `.github/workflows/draft_notes_dry_run.yml`. Each already sets `fetch-depth: 0`; add the tags flag explicitly:

```yaml
        with:
          repository: ExcellenceCloudGmbH/process-admin-general-client
          token: ${{ secrets.FRONTEND_REPO_TOKEN }}
          path: pac
          fetch-depth: 0
          fetch-tags: true   # the range is now a pair of PAC TAGS, not shas.
                             # fetch-depth: 0 brings branch history; without
                             # this the tags may be absent and `git log
                             # v1.10.0..v1.11.0` fails with a bad revision.
```

- [ ] **Step 5: Run the whole suite**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add .github/scripts/release_notes/__main__.py \
        .github/scripts/tests/test_release_notes_main.py \
        .github/workflows/prerelease_gate.yml \
        .github/workflows/publish_release_notes.yml \
        .github/workflows/draft_notes_dry_run.yml
git commit -m "feat(release-notes): read PAC's log between the pinned tags

The range is a pair of PAC tags now, so the checkouts fetch tags explicitly:
fetch-depth: 0 brings branch history but does not guarantee tags, and without
them 'git log v1.10.0..v1.11.0' fails with a bad revision — which would
degrade every release to a gap for a reason nobody would look for."
```

---

## Task 11: Point the release notes doc at the new mechanism

**Files:**
- Modify: `docs/ci-cd/release-notes.md` (lex-app)

- [ ] **Step 1: Replace the "Where the content comes from" section**

The current section explains the manifest beside the bundle. Replace its body with:

```markdown
That fact is now a version pin. `requirements.txt` carries one line:

    lex-frontend==1.10.0

The frontend is published from PAC as a versioned package — an npm package and
a Python wheel, from one version number — and lex-app depends on the wheel like
any other dependency. So "which frontend is in v2.3.0?" is answered by:

    git show v2.3.0:requirements.txt | grep lex-frontend

Two pins, at two tags, give a range of PAC tags, and the frontend half of the
note is `git log` between them.

**Releases before this mechanism** are served by a committed side-car that maps
each pre-pin bundle to the PAC revision that built it. Those three entries were
established by rebuilding each candidate and comparing the compiled output,
which is content-addressed — so the attribution is proven rather than inferred.
The side-car never grows again.
```

- [ ] **Step 2: Add a note to the gap section**

Append to the "When the frontend can't be worked out" section:

```markdown
**This can no longer happen for a new release.** A pin resolves before anything
is built, or the build fails — there is no state where a release ships
successfully and its frontend identity is unknown afterwards. The marker, and
the repair commands that go with it, now apply only to releases cut before the
pin existed.
```

- [ ] **Step 3: Commit**

```bash
git add docs/ci-cd/release-notes.md
git commit -m "docs(ci-cd): the frontend is a pinned dependency now

Records what actually changed: provenance is a line of text in
requirements.txt, and the gap machinery applies only to releases cut before
the pin existed — a new release cannot ship with an unknown frontend, because
the pin resolves before the build or the build fails."
```

---

## Task 12: Retire the copy-in path

**Do not start this task until Task 7 has passed on a real installed wheel and one release has shipped with the pin.** Until then the in-tree bundle is the working fallback, and removing it converts any packaging mistake into an instance that serves nothing.

**Files:**
- Delete: `lex/react/build/**` (lex-app)
- Modify: `pyproject.toml:41` (lex-app)
- Delete: `.github/workflows/push-build-to-pip-package.yml` (PAC)

- [ ] **Step 1: Confirm the release actually shipped with the pin**

```bash
git show <released-tag>:requirements.txt | grep lex-frontend
pip download lex-app==<released-version> --no-deps -d /tmp/shipped
```

Expected: the pin present in the tag, and the wheel downloadable. Do not proceed otherwise.

- [ ] **Step 2: Remove the bundle from the tree**

```bash
git rm -r --quiet lex/react/build
```

`lex/react/apps.py`, `models.py`, `views.py`, `admin.py` and `migrations/` stay: `lex.react` remains a registered Django app (`settings.py:259`) and removing the app is a separate change with its own blast radius.

- [ ] **Step 3: Stop packaging it**

In `pyproject.toml`, delete this line from `[tool.setuptools.package-data]`:

```toml
"lex.react" = ["**/*"]
```

Leave `"lex.assets"` and the streamlit component entries untouched.

- [ ] **Step 4: Confirm the wheel no longer carries the bundle**

```bash
python -m build --wheel
python -c "
import zipfile, glob
w = sorted(glob.glob('dist/lex_app-*.whl'))[-1]
names = zipfile.ZipFile(w).namelist()
bundle = [n for n in names if 'react/build' in n]
print('wheel:', w)
print('bundle entries:', len(bundle))
assert not bundle, bundle[:5]
print('OK — the bundle is gone from the wheel')
"
```

Expected: `bundle entries: 0`.

- [ ] **Step 5: Confirm a fresh install still serves the app**

Repeat Task 7 Steps 2-4 with the newly built lex-app wheel. This is now the *only* path — there is no fallback left, which is exactly why it is re-verified here.

- [ ] **Step 6: Delete PAC's old workflow**

In PAC:

```bash
git rm .github/workflows/push-build-to-pip-package.yml
git commit -m "ci: remove the copy-in build workflow

Replaced by publish-frontend.yml. This workflow opened a pull request that
copied 6.3 MB of compiled output into lex-app; the frontend is now a published
package that lex-app depends on by version.

It also folds in the rename agreed on 2026-09-02 — 'Push PAC Build to
dpag-pip' described a destination that no longer exists."
```

- [ ] **Step 7: Commit the lex-app side**

```bash
git add pyproject.toml
git commit -m "chore(packaging): stop shipping the frontend bundle in the wheel

The bundle arrives as the pinned lex-frontend dependency. 15 files and 6.3 MB
leave the working tree; the 42.1 MB already in the pack stays, because
rewriting 225 tags of shared history is not worth it. The win is that it stops
growing.

lex.react remains a registered Django app — only its build directory goes.
Removing the app is a separate change with its own blast radius."
```

---

## Task 13: Retire the manifest guard

**Do not start until Task 12 is merged.** While the copy-in path exists, the guard is still the thing stopping an unlabelled bundle merging.

**Files:**
- Delete: `.github/workflows/frontend_manifest_guard.yml` (lex-app)
- Modify: `.github/scripts/release_notes/__main__.py` (remove `cmd_check_manifest`)
- Delete: `.github/scripts/release_notes/manifest.py`, `.github/scripts/tests/test_release_notes_manifest.py`

- [ ] **Step 1: Confirm nothing still calls it**

```bash
grep -rn "check-manifest\|manifest.validate\|from .manifest\|import manifest" \
    .github/ docs/ | grep -v "^docs/superpowers/"
```

Expected: only the files this task deletes. Anything else is a caller that must be handled first.

- [ ] **Step 2: Delete them**

```bash
git rm .github/workflows/frontend_manifest_guard.yml \
       .github/scripts/release_notes/manifest.py \
       .github/scripts/tests/test_release_notes_manifest.py
```

- [ ] **Step 3: Remove the CLI command**

In `.github/scripts/release_notes/__main__.py`, delete the `cmd_check_manifest` function and its subparser registration, and remove `manifest` from the import line at the top.

- [ ] **Step 4: Run the suite**

```bash
PYTHONPATH=.github/scripts python -m pytest .github/scripts/tests -q
```

Expected: all pass, with 20 fewer tests than before (the manifest suite).

- [ ] **Step 5: Confirm the CLI no longer offers it**

```bash
PYTHONPATH=.github/scripts python -m release_notes --help
```

Expected: `check-manifest` absent; every other command still listed.

- [ ] **Step 6: Commit**

```bash
git add -u .github/
git commit -m "chore(release-notes): retire the manifest and its guard

Both existed to label a compiled artifact that could not identify itself. The
artifact is now a versioned dependency, so the label is the version pin and
pip is the guard: an unpublished version fails the build outright.

The side-car stays, read-only, for the 225 tags cut before the pin. It never
grows again — the code that WROTE it is what goes here."
```

---

## Self-review

**Spec coverage.** Every section of `2026-09-02-frontend-as-a-versioned-package.md` maps to a task: the recommendation (Tasks 1-4), the release-notes simplification (Tasks 8-10), what survives (side-car kept in Tasks 9 and 13), the versioning scheme (Task 1), the sequence (task order), and the costs — Django's static path (Tasks 5, 7), two registries (Task 4), history not shrinking (Task 12's commit message). The spec's four open decisions are settled in this plan as: GitHub Packages, `@excellencecloudgmbh/lex-frontend`, `workflow_dispatch` (a deliberate action, not on merge), and the changelog question deferred rather than answered.

**Not covered, deliberately.** The spec floats shipping PAC's `CHANGELOG.md` inside the package to remove the PAC checkout and `FRONTEND_REPO_TOKEN` from the notes path. That is a separate change with its own design question — whether composed changelog entries read as well as commit-derived prose — and it is not needed for anything here. Left out rather than half-specified.

**Local dev.** Task 5's in-tree fallback means a source checkout keeps working with no new steps. A frontend developer who wants their own build served installs their local wheel over the pin (`pip install -e <PAC>/packaging` after `assemble_wheel.py`). Worth documenting in `docs/ci-cd/local-dev.md` once Task 12 lands; not blocking.

**Type consistency.** `build_path()` is the name in Tasks 2, 5 and 7. `_VERSION` is the stamped literal in Tasks 2 and 3; `__version__` is the `importlib.metadata` lookup, and only Task 2 uses it. `frontend_version_at`, `pac_tag_for`, `PIN_RE` and `REQUIREMENTS_PATH` are defined in Tasks 6 and 8 and used in Task 9. `Range(from_sha=…, to_sha=…)` matches the existing dataclass — Task 9 carries PAC *tags* in those fields, which the field names now under-describe; renaming them touches every historical call site and is not worth it, so Task 9's comment says so explicitly.

**Ordering risk.** Tasks 1-6 and 8-11 are safe to merge in any order — the pin resolution is inert until a tag actually carries a pin, and the settings fallback is inert until a wheel is installed. Tasks 7, 12 and 13 are gated, in that order, and Task 12 carries the only irreversible step in the plan.
