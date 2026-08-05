# Release Notes and Changelog Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a mechanical `CHANGELOG.md` and an LLM-drafted business-facing release note for every release, drafted at the prerelease gate where a human already reviews before promoting.

**Architecture:** Five small Python modules under `.github/scripts/release_notes/`, each a pure function over injectable inputs so they test without a git repo or network. A new job in `prerelease_gate.yml` drafts the business note into the prerelease body; a new `publish_release_notes.yml` re-derives the deterministic changelog on promotion and commits it. The frontend section is gated on a `.frontend-version.json` manifest that does not exist yet.

**Tech Stack:** Python 3.12, pytest, `gh` CLI, GitHub Models inference endpoint, GitHub Actions.

**Spec:** [`docs/superpowers/specs/2026-08-05-release-notes-automation-design.md`](../specs/2026-08-05-release-notes-automation-design.md)

---

## File Structure

| File | Responsibility |
| --- | --- |
| `.github/scripts/release_notes/__init__.py` | Package marker, empty |
| `.github/scripts/release_notes/ranges.py` | Tag → backend commit range; manifest → frontend commit range |
| `.github/scripts/release_notes/digest.py` | Ranges → structured JSON digest of changes |
| `.github/scripts/release_notes/changelog.py` | Digest → Keep a Changelog markdown (pure, no network) |
| `.github/scripts/release_notes/notes.py` | Digest → business note via GitHub Models, with validation and fallback |
| `.github/scripts/release_notes/quackback.py` | Publish interface — raises `NotImplementedError` |
| `.github/scripts/release_notes/__main__.py` | CLI entrypoints the workflows call |
| `.github/scripts/tests/test_release_notes_*.py` | Tests, one file per module |
| `.github/workflows/scripts_tests.yml` | Runs `.github/scripts/tests/` — **new; nothing runs them today** |
| `.github/workflows/publish_release_notes.yml` | On `release: released` — commit changelog, call quackback |
| `.github/workflows/frontend_manifest_guard.yml` | Fail a PR that changes the bundle without the manifest |
| `.github/workflows/prerelease_gate.yml` | Modified — add the `draft-notes` job |

**Clarification of the spec:** the spec says the drafter "requires back" all three headings *and* that empty sections should be omitted. Those conflict. This plan resolves it: validation requires **at least one** of the three headings, and rejects a heading with no content under it. Task 9 implements exactly that.

---

### Task 1: Run the script tests in CI

`.github/scripts/tests/` holds 158 passing tests and **no workflow executes them**. Wire them up first so every task after this one is actually verified.

**Files:**
- Create: `.github/workflows/scripts_tests.yml`

- [ ] **Step 1: Confirm the existing tests pass locally**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/ -q`
Expected: `158 passed` (the count may be higher if other work landed; no failures is what matters)

- [ ] **Step 2: Create the workflow**

```yaml
# ──────────────────────────────────────────────────────────────────────
#  Tests for the Python helpers under .github/scripts/.
#
#  These are CI-side scripts, not framework code, so they are not part of
#  the test-plan clusters and do not need Django. Plain pytest, no DB.
#
#  Until this workflow existed the directory held 158 passing tests that
#  nothing ever ran.
# ──────────────────────────────────────────────────────────────────────
name: Script Tests

on:
  push:
    paths:
      - ".github/scripts/**"
      - ".github/workflows/scripts_tests.yml"
  pull_request:
    paths:
      - ".github/scripts/**"
      - ".github/workflows/scripts_tests.yml"
  workflow_dispatch:

jobs:
  pytest:
    name: "pytest .github/scripts/tests"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install test dependencies
        run: python -m pip install --quiet pytest pyyaml

      - name: Run tests
        # -p no:cacheprovider keeps the runner from writing .pytest_cache.
        # --rootdir pins config discovery to the repo root so pyproject's
        # `-p no:django` addopt applies (pytest-django is not installed here,
        # and disabling a missing plugin is a no-op, not an error).
        run: python -m pytest .github/scripts/tests/ -q -p no:cacheprovider
```

- [ ] **Step 3: Verify the workflow file parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/scripts_tests.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/scripts_tests.yml
git commit -m "ci(scripts): run the .github/scripts test suite

158 tests lived in .github/scripts/tests/ and no workflow ever ran them."
```

---

### Task 2: Release-tag filtering

**Files:**
- Create: `.github/scripts/release_notes/__init__.py`
- Create: `.github/scripts/release_notes/ranges.py`
- Test: `.github/scripts/tests/test_release_notes_ranges.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for release_notes.ranges — tag classification and range resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make .github/scripts importable when pytest runs from the repo root.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import ranges  # noqa: E402


@pytest.mark.parametrize(
    "tag",
    ["v2.1.6", "v2.0.0rc215", "v10.0.1", "v2.0.0rc1"],
)
def test_release_tags_are_recognised(tag: str):
    assert ranges.is_release_tag(tag) is True


@pytest.mark.parametrize(
    "tag",
    [
        "v0.0.0-hazem",                      # a real tag in this repo
        "recovery-supervisor-on-demand.0",   # also real
        "2.1.6",                             # no v prefix
        "v2.1",                              # not three components
        "v2.1.6-rc1",                        # hyphenated rc is not our scheme
        "",
        None,
    ],
)
def test_non_release_tags_are_rejected(tag):
    assert ranges.is_release_tag(tag) is False


def test_previous_release_tag_skips_junk_tags():
    # Newest first, as `git tag --sort=-creatordate` returns them.
    tags = ["v2.1.6", "v0.0.0-hazem", "recovery-supervisor-on-demand.0", "v2.1.5"]
    assert ranges.previous_release_tag("v2.1.6", tags=tags) == "v2.1.5"


def test_previous_release_tag_accepts_an_rc_as_a_baseline():
    tags = ["v2.1.1", "v2.0.0rc221", "v2.0.0rc220"]
    assert ranges.previous_release_tag("v2.1.1", tags=tags) == "v2.0.0rc221"


def test_previous_release_tag_is_none_when_only_the_tag_itself_is_present():
    assert ranges.previous_release_tag("v2.1.6", tags=["v2.1.6"]) is None


def test_previous_release_tag_is_none_when_only_junk_precedes_it():
    tags = ["v2.1.6", "v0.0.0-hazem", "recovery-supervisor-on-demand.0"]
    assert ranges.previous_release_tag("v2.1.6", tags=tags) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_ranges.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'release_notes'`

- [ ] **Step 3: Write minimal implementation**

Create `.github/scripts/release_notes/__init__.py` as an empty file, then `.github/scripts/release_notes/ranges.py`:

```python
#!/usr/bin/env python3
"""Resolve the commit ranges a release covers.

Backend range: previous release tag -> current tag.
Frontend range: the PAC SHA recorded in the build manifest at each of those
two tags. Absent at either end means there is no truthful frontend range, and
the caller omits the frontend section rather than guessing.
"""

from __future__ import annotations

import re

# vX.Y.Z or vX.Y.ZrcN and nothing else. This repository also carries
# `v0.0.0-hazem` and `recovery-supervisor-on-demand.0`; picking either as a
# baseline would silently produce a range spanning the wrong history.
RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:rc\d+)?$")


def is_release_tag(tag: str | None) -> bool:
    """True if `tag` is one of our release tags."""
    if not tag:
        return False
    return bool(RELEASE_TAG_RE.match(tag))


def previous_release_tag(tag: str, *, tags: list[str]) -> str | None:
    """The most recent release tag before `tag`.

    `tags` is newest-first, as `git tag --merged <tag> --sort=-creatordate`
    returns it. Passing it in keeps this function pure and testable.
    Returns None when `tag` is the first release.
    """
    for candidate in tags:
        candidate = candidate.strip()
        if candidate and candidate != tag and is_release_tag(candidate):
            return candidate
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_ranges.py -q`
Expected: PASS — 15 passed (4 + 7 parametrised cases, plus 4 resolution tests)

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/__init__.py \
        .github/scripts/release_notes/ranges.py \
        .github/scripts/tests/test_release_notes_ranges.py
git commit -m "feat(release-notes): classify release tags and find the previous one"
```

---

### Task 3: Frontend manifest reading

**Files:**
- Modify: `.github/scripts/release_notes/ranges.py`
- Test: `.github/scripts/tests/test_release_notes_ranges.py`

- [ ] **Step 1: Write the failing test**

Append to `.github/scripts/tests/test_release_notes_ranges.py`:

```python
def test_frontend_sha_at_reads_the_manifest():
    def fake_show(ref: str, path: str) -> str | None:
        assert path == ranges.MANIFEST_PATH
        return '{"repo": "x/y", "branch": "b", "sha": "a3f91c2", "built_at": "2026-08-05T09:14:22Z"}'

    assert ranges.frontend_sha_at("v2.1.6", show=fake_show) == "a3f91c2"


def test_frontend_sha_at_returns_none_when_the_manifest_is_absent():
    assert ranges.frontend_sha_at("v2.1.6", show=lambda ref, path: None) is None


def test_frontend_sha_at_returns_none_on_malformed_manifest():
    assert ranges.frontend_sha_at("v2.1.6", show=lambda ref, path: "not json") is None
    assert ranges.frontend_sha_at("v2.1.6", show=lambda ref, path: '{"no_sha": 1}') is None


def test_frontend_range_is_none_when_the_manifest_is_missing_at_either_end():
    # Missing at the current tag.
    shas = {"v2.1.5": "aaa1111"}
    got = ranges.frontend_range("v2.1.5", "v2.1.6", show=_shas(shas))
    assert got is None

    # Missing at the previous tag.
    shas = {"v2.1.6": "bbb2222"}
    got = ranges.frontend_range("v2.1.5", "v2.1.6", show=_shas(shas))
    assert got is None


def test_frontend_range_resolves_when_present_at_both_ends():
    shas = {"v2.1.5": "aaa1111", "v2.1.6": "bbb2222"}
    got = ranges.frontend_range("v2.1.5", "v2.1.6", show=_shas(shas))
    assert got == ranges.Range(from_sha="aaa1111", to_sha="bbb2222")


def test_frontend_range_on_the_first_release_has_no_lower_bound():
    shas = {"v2.1.6": "bbb2222"}
    got = ranges.frontend_range(None, "v2.1.6", show=_shas(shas))
    assert got == ranges.Range(from_sha=None, to_sha="bbb2222")


def _shas(mapping: dict[str, str]):
    """Build a `show` stub that serves manifests for the given refs only."""
    import json

    def show(ref: str, path: str) -> str | None:
        if ref not in mapping:
            return None
        return json.dumps({"repo": "x/y", "branch": "b", "sha": mapping[ref]})

    return show
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_ranges.py -q`
Expected: FAIL — `AttributeError: module 'release_notes.ranges' has no attribute 'frontend_sha_at'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports at the top of `.github/scripts/release_notes/ranges.py`:

```python
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
```

Then append to the module:

```python
REPO_ROOT = Path(__file__).resolve().parents[3]

# Written by whoever commits a rebuilt frontend bundle, and later by
# frontend_build.yml once that workflow is repaired. See the spec's
# "Prerequisite" section — it has never run.
MANIFEST_PATH = "lex/react/build/.frontend-version.json"


@dataclass(frozen=True)
class Range:
    """A commit range. `from_sha` of None means "from the root commit"."""

    from_sha: str | None
    to_sha: str


def git_show(ref: str, path: str) -> str | None:
    """Contents of `path` as of `ref`, or None if it does not exist there."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def frontend_sha_at(ref: str, *, show: Callable[[str, str], str | None] = git_show) -> str | None:
    """The PAC SHA recorded in the manifest as of `ref`, or None."""
    blob = show(ref, MANIFEST_PATH)
    if blob is None:
        return None
    try:
        return json.loads(blob)["sha"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def frontend_range(
    previous_tag: str | None,
    current_tag: str,
    *,
    show: Callable[[str, str], str | None] = git_show,
) -> Range | None:
    """The frontend commit range, or None when it cannot be established.

    Returning None is a deliberate outcome, not an error: without a recorded
    SHA at both ends there is no truthful frontend range, and inventing one
    would produce notes about changes that may not be in the shipped bundle.
    """
    to_sha = frontend_sha_at(current_tag, show=show)
    if to_sha is None:
        return None
    if previous_tag is None:
        return Range(from_sha=None, to_sha=to_sha)
    from_sha = frontend_sha_at(previous_tag, show=show)
    if from_sha is None:
        return None
    return Range(from_sha=from_sha, to_sha=to_sha)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_ranges.py -q`
Expected: PASS — 21 passed (15 from Task 2 plus 6 new)

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/ranges.py \
        .github/scripts/tests/test_release_notes_ranges.py
git commit -m "feat(release-notes): resolve the frontend range from the build manifest"
```

---

### Task 4: Conventional-commit parsing

**Files:**
- Create: `.github/scripts/release_notes/digest.py`
- Test: `.github/scripts/tests/test_release_notes_digest.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for release_notes.digest — commit parsing, filtering, PR enrichment."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import digest  # noqa: E402


def test_parses_type_scope_and_subject():
    got = digest.parse_subject("fix(calc): never stamp edited_at for a calculation-owned save")
    assert got == digest.Parsed(
        type="fix",
        scope="calc",
        breaking=False,
        subject="never stamp edited_at for a calculation-owned save",
    )


def test_parses_a_type_without_a_scope():
    got = digest.parse_subject("feat: add the widget")
    assert got.type == "feat"
    assert got.scope is None
    assert got.subject == "add the widget"


def test_detects_a_breaking_marker():
    got = digest.parse_subject("feat(api)!: drop the v1 endpoint")
    assert got.breaking is True
    assert got.type == "feat"
    assert got.subject == "drop the v1 endpoint"


def test_non_conforming_subjects_become_other_and_keep_their_text():
    got = digest.parse_subject("Mode change is a beauty")
    assert got == digest.Parsed(
        type="other", scope=None, breaking=False, subject="Mode change is a beauty"
    )


def test_an_unknown_prefix_is_not_treated_as_a_type():
    # "wibble" is not in the conventional set, so this is not a typed commit.
    got = digest.parse_subject("wibble(core): something")
    assert got.type == "other"
    assert got.subject == "wibble(core): something"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_digest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'release_notes.digest'`

- [ ] **Step 3: Write minimal implementation**

Create `.github/scripts/release_notes/digest.py`:

```python
#!/usr/bin/env python3
"""Turn commit ranges into a structured digest of changes.

The digest is the single source of truth for both renderers: the mechanical
changelog and the LLM-drafted business note. Keeping one digest means the two
artifacts can never describe different sets of changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONVENTIONAL_TYPES = (
    "feat", "fix", "docs", "style", "refactor",
    "perf", "test", "build", "ci", "chore", "revert",
)

_SUBJECT_RE = re.compile(
    r"^(?P<type>" + "|".join(CONVENTIONAL_TYPES) + r")"
    r"(?:\((?P<scope>[^)]+)\))?"
    r"(?P<breaking>!)?: "
    r"(?P<subject>.+)$"
)


@dataclass(frozen=True)
class Parsed:
    type: str
    scope: str | None
    breaking: bool
    subject: str


def parse_subject(subject: str) -> Parsed:
    """Split a commit or PR subject into its conventional parts.

    A subject that does not conform is classified `other` with its text
    preserved. Discarding it would lose real changes: 43% of non-merge
    commits in this repository do not conform, and some of them ship
    user-visible work.
    """
    subject = (subject or "").strip()
    match = _SUBJECT_RE.match(subject)
    if match is None:
        return Parsed(type="other", scope=None, breaking=False, subject=subject)
    return Parsed(
        type=match.group("type"),
        scope=match.group("scope"),
        breaking=match.group("breaking") == "!",
        subject=match.group("subject"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_digest.py -q`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/digest.py \
        .github/scripts/tests/test_release_notes_digest.py
git commit -m "feat(release-notes): parse conventional commit subjects"
```

---

### Task 5: Building the digest with noise filtering

**Files:**
- Modify: `.github/scripts/release_notes/digest.py`
- Test: `.github/scripts/tests/test_release_notes_digest.py`

- [ ] **Step 1: Write the failing test**

Append to `.github/scripts/tests/test_release_notes_digest.py`:

```python
def test_build_digest_maps_commits_to_entries():
    commits = [
        digest.Commit(sha="705850d", subject="fix(calc): stop stamping edited_at"),
        digest.Commit(sha="81edcbc", subject="feat(setup): onboard any agentic IDE"),
    ]
    got = digest.build_digest(
        tag="v2.1.7",
        previous_tag="v2.1.6",
        backend_commits=commits,
        frontend_commits=[],
    )
    assert got["tag"] == "v2.1.7"
    assert got["previous_tag"] == "v2.1.6"
    assert [c["sha"] for c in got["changes"]] == ["705850d", "81edcbc"]
    assert got["changes"][0]["component"] == "backend"
    assert got["changes"][0]["type"] == "fix"


def test_build_digest_labels_frontend_commits():
    got = digest.build_digest(
        tag="v2.1.7",
        previous_tag="v2.1.6",
        backend_commits=[],
        frontend_commits=[digest.Commit(sha="a3f91c2", subject="fix(export): send the viewer timezone")],
    )
    assert got["changes"][0]["component"] == "frontend"


def test_merge_commits_are_dropped():
    commits = [
        digest.Commit(sha="1111111", subject="Merge pull request #678 from Excellence/fix/x"),
        digest.Commit(sha="2222222", subject="Merge branch 'lex-app-v2' into feature"),
        digest.Commit(sha="3333333", subject="fix(core): a real change"),
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    assert [c["sha"] for c in got["changes"]] == ["3333333"]


def test_bundle_update_commits_are_dropped():
    commits = [
        digest.Commit(sha="4444444", subject="build(frontend): update bundle from Excellence/pac@abc123"),
        digest.Commit(sha="5555555", subject="feat(core): a real change"),
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    assert [c["sha"] for c in got["changes"]] == ["5555555"]


def test_empty_subjects_are_dropped():
    commits = [
        digest.Commit(sha="6666666", subject="   "),
        digest.Commit(sha="7777777", subject="fix(core): real"),
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    assert [c["sha"] for c in got["changes"]] == ["7777777"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_digest.py -q`
Expected: FAIL — `AttributeError: module 'release_notes.digest' has no attribute 'Commit'`

- [ ] **Step 3: Write minimal implementation**

Append to `.github/scripts/release_notes/digest.py`:

```python
@dataclass(frozen=True)
class Commit:
    """One commit as read from git, before enrichment."""

    sha: str
    subject: str
    pr_number: int | None = None
    pr_title: str | None = None


# Commits that are real work but say nothing about the shipped product.
_MERGE_PREFIXES = ("Merge pull request ", "Merge branch ", "Merge remote-tracking ")
_BUNDLE_PREFIX = "build(frontend): update bundle"


def is_noise(subject: str) -> bool:
    """True for commits that must never reach a changelog."""
    subject = (subject or "").strip()
    if not subject:
        return True
    if subject.startswith(_MERGE_PREFIXES):
        return True
    if subject.startswith(_BUNDLE_PREFIX):
        return True
    return False


def _entry(commit: Commit, component: str) -> dict:
    # The PR title leads when there is one: PR titles in this repository are
    # consistently well-formed, while only 57% of non-merge commit subjects
    # conform. Enrichment is what makes the input usable at that number.
    subject = commit.pr_title or commit.subject
    parsed = parse_subject(subject)
    return {
        "sha": commit.sha,
        "component": component,
        "type": parsed.type,
        "scope": parsed.scope,
        "breaking": parsed.breaking,
        "subject": parsed.subject,
        "pr_number": commit.pr_number,
    }


def build_digest(
    tag: str,
    previous_tag: str | None,
    backend_commits: list[Commit],
    frontend_commits: list[Commit],
) -> dict:
    """Assemble the digest both renderers consume."""
    changes: list[dict] = []
    for component, commits in (("backend", backend_commits), ("frontend", frontend_commits)):
        for commit in commits:
            if is_noise(commit.pr_title or commit.subject):
                continue
            changes.append(_entry(commit, component))
    return {"tag": tag, "previous_tag": previous_tag, "changes": changes}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_digest.py -q`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/digest.py \
        .github/scripts/tests/test_release_notes_digest.py
git commit -m "feat(release-notes): build the digest and drop merge/bundle noise"
```

---

### Task 6: Collecting commits from git and enriching with PRs

**Files:**
- Modify: `.github/scripts/release_notes/digest.py`
- Test: `.github/scripts/tests/test_release_notes_digest.py`

- [ ] **Step 1: Write the failing test**

Append to `.github/scripts/tests/test_release_notes_digest.py`:

```python
def test_collect_commits_parses_the_git_log_format():
    raw = "705850d\x1ffix(calc): stop stamping edited_at\n81edcbc\x1ffeat(setup): onboard IDEs"
    got = digest.collect_commits("v2.1.6", "v2.1.7", run_log=lambda a, b: raw)
    assert got == [
        digest.Commit(sha="705850d", subject="fix(calc): stop stamping edited_at"),
        digest.Commit(sha="81edcbc", subject="feat(setup): onboard IDEs"),
    ]


def test_collect_commits_returns_empty_for_an_empty_log():
    assert digest.collect_commits("v2.1.6", "v2.1.7", run_log=lambda a, b: "") == []


def test_enrich_prefers_the_pr_title():
    commits = [digest.Commit(sha="705850d", subject="publishable")]

    def fake_lookup(sha: str):
        assert sha == "705850d"
        return (675, "fix(calc): never stamp edited_at for a calculation-owned save")

    got = digest.enrich_with_prs(commits, lookup=fake_lookup)
    assert got[0].pr_number == 675
    assert got[0].pr_title == "fix(calc): never stamp edited_at for a calculation-owned save"
    # The original subject is preserved, not overwritten.
    assert got[0].subject == "publishable"


def test_enrich_leaves_a_commit_without_a_pr_untouched():
    commits = [digest.Commit(sha="deadbee", subject="fix(core): direct push")]
    got = digest.enrich_with_prs(commits, lookup=lambda sha: None)
    assert got[0].pr_number is None
    assert got[0].pr_title is None
    assert got[0].subject == "fix(core): direct push"


def test_enrich_survives_a_lookup_failure():
    def boom(sha: str):
        raise RuntimeError("gh api rate limited")

    commits = [digest.Commit(sha="deadbee", subject="fix(core): x")]
    got = digest.enrich_with_prs(commits, lookup=boom)
    assert got[0].pr_number is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_digest.py -q`
Expected: FAIL — `AttributeError: module 'release_notes.digest' has no attribute 'collect_commits'`

- [ ] **Step 3: Write minimal implementation**

Add these imports to the top of `.github/scripts/release_notes/digest.py`:

```python
import json
import subprocess
from pathlib import Path
from typing import Callable
```

and this constant beside the others:

```python
REPO_ROOT = Path(__file__).resolve().parents[3]

# ASCII unit separator: cannot appear in a commit subject, unlike any
# punctuation a human might type.
_FIELD_SEP = "\x1f"
```

Then append:

```python
def _run_log(from_ref: str | None, to_ref: str) -> str:
    spec = f"{from_ref}..{to_ref}" if from_ref else to_ref
    result = subprocess.run(
        ["git", "log", "--no-merges", f"--pretty=%h{_FIELD_SEP}%s", spec],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def collect_commits(
    from_ref: str | None,
    to_ref: str,
    *,
    run_log: Callable[[str | None, str], str] = _run_log,
) -> list[Commit]:
    """Read `from_ref..to_ref` into Commit records."""
    raw = run_log(from_ref, to_ref)
    commits: list[Commit] = []
    for line in raw.splitlines():
        if _FIELD_SEP not in line:
            continue
        sha, subject = line.split(_FIELD_SEP, 1)
        commits.append(Commit(sha=sha.strip(), subject=subject.strip()))
    return commits


def _lookup_pr(sha: str) -> tuple[int, str] | None:
    """The merged PR a commit belongs to, via the GitHub API."""
    result = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}/pulls",
         "--jq", ".[0] | select(.merged_at != null) | [.number, .title] | @json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    number, title = json.loads(result.stdout.strip())
    return int(number), title


def enrich_with_prs(
    commits: list[Commit],
    *,
    lookup: Callable[[str], tuple[int, str] | None] = _lookup_pr,
) -> list[Commit]:
    """Attach PR number and title where a commit came through a PR.

    A lookup failure is not fatal. Release notes are worth having with
    partial enrichment; they are not worth failing a release for.
    """
    enriched: list[Commit] = []
    for commit in commits:
        try:
            found = lookup(commit.sha)
        except Exception:
            found = None
        if found is None:
            enriched.append(commit)
        else:
            number, title = found
            enriched.append(
                Commit(sha=commit.sha, subject=commit.subject, pr_number=number, pr_title=title)
            )
    return enriched
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_digest.py -q`
Expected: PASS — 15 passed

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/digest.py \
        .github/scripts/tests/test_release_notes_digest.py
git commit -m "feat(release-notes): read commits from git and enrich them with PR titles"
```

---

### Task 7: Rendering the changelog

**Files:**
- Create: `.github/scripts/release_notes/changelog.py`
- Test: `.github/scripts/tests/test_release_notes_changelog.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for release_notes.changelog — deterministic Keep a Changelog output."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import changelog  # noqa: E402

REPO = "ExcellenceCloudGmbH/lex-app"


def _change(**kw):
    base = {
        "sha": "abc1234", "component": "backend", "type": "fix",
        "scope": None, "breaking": False, "subject": "a thing", "pr_number": None,
    }
    base.update(kw)
    return base


def test_renders_the_version_heading_with_the_date():
    out = changelog.render({"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": []},
                           date="2026-08-05", repo=REPO)
    assert out.startswith("## [2.1.7] - 2026-08-05")


def test_groups_feat_under_added_and_fix_under_fixed():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(type="feat", subject="add the widget", sha="1111111"),
        _change(type="fix", subject="stop the crash", sha="2222222"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "### Added" in out
    assert "### Fixed" in out
    assert out.index("### Added") < out.index("### Fixed")


def test_marks_the_component_and_links_the_commit():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(component="frontend", type="fix", subject="send the viewer timezone", sha="a3f91c2"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "**frontend** send the viewer timezone" in out
    assert f"https://github.com/{REPO}/commit/a3f91c2" in out


def test_includes_the_pr_number_when_present():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(pr_number=675, subject="stop stamping edited_at"),
    ]}
    assert "(#675)" in changelog.render(d, date="2026-08-05", repo=REPO)


def test_breaking_changes_come_first_and_appear_once():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(type="feat", subject="normal feature", sha="1111111"),
        _change(type="feat", breaking=True, subject="drop the v1 endpoint", sha="2222222"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert out.index("### Breaking") < out.index("### Added")
    assert out.count("drop the v1 endpoint") == 1


def test_housekeeping_types_are_excluded():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(type="docs", subject="update the README"),
        _change(type="ci", subject="bump the runner"),
        _change(type="chore", subject="tidy up"),
        _change(type="test", subject="add a case"),
        _change(type="build", subject="pin a dep"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "update the README" not in out
    assert "bump the runner" not in out
    assert "### " not in out          # no section headings at all


def test_non_conforming_commits_land_under_changed():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(type="other", subject="Mode change is a beauty"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "### Changed" in out
    assert "Mode change is a beauty" in out


def test_an_empty_section_emits_no_heading():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [_change(type="feat", subject="only a feature")]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "### Added" in out
    assert "### Fixed" not in out


def test_prepend_creates_the_file_with_a_preamble():
    out = changelog.prepend(None, "## [2.1.7] - 2026-08-05\n")
    assert out.startswith("# Changelog")
    assert "## [2.1.7] - 2026-08-05" in out


def test_prepend_puts_the_newest_release_directly_below_the_preamble():
    existing = changelog.prepend(None, "## [2.1.6] - 2026-07-23\n")
    out = changelog.prepend(existing, "## [2.1.7] - 2026-08-05\n")
    assert out.count("# Changelog") == 1
    assert out.index("## [2.1.7]") < out.index("## [2.1.6]")


def test_prepend_tolerates_a_file_without_the_expected_preamble():
    out = changelog.prepend("## [2.1.6] - 2026-07-23\n", "## [2.1.7] - 2026-08-05\n")
    assert out.index("## [2.1.7]") < out.index("## [2.1.6]")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_changelog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'release_notes.changelog'`

- [ ] **Step 3: Write minimal implementation**

Create `.github/scripts/release_notes/changelog.py`:

```python
#!/usr/bin/env python3
"""Render a digest as a Keep a Changelog section.

Pure: no network, no model, no clock. The date is passed in so the output is
reproducible, which is what lets publish_release_notes.yml re-derive the same
section at promotion time instead of carrying an artifact between workflows.
"""

from __future__ import annotations

PREAMBLE = """\
# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries are generated from commits and pull requests at release time; see
`.github/scripts/release_notes/`.
"""

# Types that are real work but not changes to the shipped product.
EXCLUDED_TYPES = frozenset({"docs", "test", "ci", "chore", "build"})

# Order matters: it is the order sections appear. `other` joins Changed so
# that non-conforming commits are reported rather than silently dropped.
_SECTIONS: tuple[tuple[str, frozenset[str]], ...] = (
    ("Added", frozenset({"feat"})),
    ("Fixed", frozenset({"fix"})),
    ("Changed", frozenset({"refactor", "perf", "style", "other"})),
    ("Removed", frozenset({"revert"})),
)


def _line(change: dict, repo: str) -> str:
    url = f"https://github.com/{repo}/commit/{change['sha']}"
    suffix = f" (#{change['pr_number']})" if change.get("pr_number") else ""
    return f"- **{change['component']}** {change['subject']} ([{change['sha']}]({url})){suffix}"


def render(digest: dict, *, date: str, repo: str) -> str:
    """Render one release section. Returns the heading alone if nothing shipped."""
    version = digest["tag"].lstrip("v")
    parts = [f"## [{version}] - {date}", ""]

    shippable = [c for c in digest["changes"] if c["type"] not in EXCLUDED_TYPES]

    breaking = [c for c in shippable if c.get("breaking")]
    if breaking:
        parts.append("### Breaking")
        parts.extend(_line(c, repo) for c in breaking)
        parts.append("")

    for heading, types in _SECTIONS:
        rows = [c for c in shippable if c["type"] in types and not c.get("breaking")]
        if not rows:
            continue
        parts.append(f"### {heading}")
        parts.extend(_line(c, repo) for c in rows)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def prepend(existing: str | None, section: str) -> str:
    """Insert `section` directly below the preamble."""
    if not existing:
        return f"{PREAMBLE}\n{section}"
    marker = PREAMBLE.rstrip()
    if existing.startswith(marker):
        rest = existing[len(marker):].lstrip("\n")
        return f"{marker}\n\n{section}\n{rest}"
    return f"{section}\n{existing}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_changelog.py -q`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/changelog.py \
        .github/scripts/tests/test_release_notes_changelog.py
git commit -m "feat(release-notes): render the digest as a Keep a Changelog section"
```

---

### Task 8: Drafting the business note — prompt and validation

**Files:**
- Create: `.github/scripts/release_notes/notes.py`
- Test: `.github/scripts/tests/test_release_notes_notes.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for release_notes.notes — prompt assembly, validation, fallback."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import notes  # noqa: E402

GOOD = """\
## Main changes

- **New sidebar.** A full-height side navigation.

## Bug fixes

- **Timezone bug.** Times now display correctly.

**Upgrade note:** run database migrations on upgrade.
"""


def _digest(changes=None):
    return {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": changes or [
        {"sha": "1111111", "component": "backend", "type": "fix", "scope": "tz",
         "breaking": False, "subject": "correct the offset", "pr_number": 661},
    ]}


def test_prompt_contains_the_digest_and_the_style_exemplar():
    prompt = notes.build_prompt(_digest(), exemplar="EXEMPLAR-TEXT")
    assert "correct the offset" in prompt
    assert "EXEMPLAR-TEXT" in prompt
    assert "v2.1.7" in prompt


def test_validation_accepts_output_with_one_required_heading():
    # The spec asks for empty sections to be omitted, so one heading is enough.
    assert notes.validate(GOOD) is None


def test_validation_rejects_output_with_no_required_heading():
    assert notes.validate("Some prose with no headings at all.") is not None


def test_validation_rejects_a_heading_with_nothing_under_it():
    bad = "## Main changes\n\n## Bug fixes\n\n- **A fix.** Text.\n"
    assert notes.validate(bad) is not None


def test_fallback_carries_the_digest_and_the_failure_marker():
    out = notes.fallback(_digest(), reason="model call timed out")
    assert notes.FAILURE_MARKER in out
    assert "model call timed out" in out
    assert "correct the offset" in out


def test_empty_digest_short_circuits_without_calling_the_model():
    called = []

    def model(prompt: str) -> str:
        called.append(prompt)
        return GOOD

    out = notes.draft(_digest(changes=[]), exemplar="X", model=model)
    assert called == []
    assert "no user-facing changes" in out.lower()


def test_draft_returns_model_output_when_valid():
    out = notes.draft(_digest(), exemplar="X", model=lambda p: GOOD)
    assert "New sidebar" in out
    assert notes.FAILURE_MARKER not in out


def test_draft_falls_back_when_the_model_raises():
    def boom(prompt: str) -> str:
        raise RuntimeError("502 from the inference endpoint")

    out = notes.draft(_digest(), exemplar="X", model=boom)
    assert notes.FAILURE_MARKER in out
    assert "502" in out


def test_draft_falls_back_when_the_model_returns_junk():
    out = notes.draft(_digest(), exemplar="X", model=lambda p: "I cannot help with that.")
    assert notes.FAILURE_MARKER in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_notes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'release_notes.notes'`

- [ ] **Step 3: Write minimal implementation**

Create `.github/scripts/release_notes/notes.py`:

```python
#!/usr/bin/env python3
"""Draft the business-facing release note from a digest.

A changelog generator must never block a release, so every failure path here
produces a usable body rather than an exception: the raw digest plus a marker
a human can act on.
"""

from __future__ import annotations

import json
from typing import Callable

FAILURE_MARKER = "<!-- lex:notes-draft-failed -->"

REQUIRED_HEADINGS = ("## Main changes", "## Optimizations", "## Bug fixes")

_INSTRUCTIONS = """\
You are writing the release note for a business application called LEX.

Audience: two readers at once. A business user who has never seen the codebase
and wants to know what changed for them, and a technical user who wants to know
what actually changed. One document must satisfy both.

Rules:
- Use only these headings, in this order: "## Main changes", "## Optimizations",
  "## Bug fixes". Omit any heading you have nothing to put under it. Never emit
  a heading with no entries.
- Each entry is a bullet starting with a bold summary phrase, then one or two
  plain sentences. Example: "- **New sidebar.** A full-height side navigation."
- Group entries by what the change means to a user, NOT by which repository or
  component it came from. Never mention "backend", "frontend", or repository
  names.
- Do not invent changes. Every entry must trace to an item in the digest.
- Do not mention internal class names, file paths, or commit hashes.
- End with a line starting "**Upgrade note:**" describing any action needed on
  upgrade, or stating that none is needed.

Match the tone and shape of this previous release note exactly:

<exemplar>
{exemplar}
</exemplar>

Here is the digest of what changed in {tag}:

<digest>
{digest}
</digest>

Return only the markdown release note. No preamble, no explanation.
"""


def build_prompt(digest: dict, *, exemplar: str) -> str:
    """Assemble the model prompt from the digest and a style exemplar."""
    return _INSTRUCTIONS.format(
        exemplar=exemplar,
        tag=digest["tag"],
        digest=json.dumps(digest["changes"], indent=2),
    )


def validate(text: str) -> str | None:
    """Return a reason string if `text` is unusable, else None."""
    if not text or not text.strip():
        return "empty response"

    present = [h for h in REQUIRED_HEADINGS if h in text]
    if not present:
        return "no recognised section heading"

    # A heading followed immediately by another heading, or by nothing, means
    # the model emitted an empty section.
    for heading in present:
        body = text.split(heading, 1)[1]
        for other in REQUIRED_HEADINGS:
            if other != heading and other in body:
                body = body.split(other, 1)[0]
        if not body.strip():
            return f"empty section: {heading}"

    return None


def fallback(digest: dict, *, reason: str) -> str:
    """A usable body for when drafting fails. Never raises."""
    lines = [
        FAILURE_MARKER,
        "",
        f"Automatic release-note drafting failed ({reason}).",
        "The changes below are the raw digest — please rewrite this section",
        "before promoting, or promote as-is and edit the release afterwards.",
        "",
        "## Main changes",
        "",
    ]
    for change in digest["changes"]:
        pr = f" (#{change['pr_number']})" if change.get("pr_number") else ""
        lines.append(f"- {change['subject']}{pr}")
    return "\n".join(lines) + "\n"


def draft(digest: dict, *, exemplar: str, model: Callable[[str], str]) -> str:
    """Draft the note, degrading to a fallback body on any failure."""
    if not digest["changes"]:
        return (
            "No user-facing changes in this release.\n\n"
            "**Upgrade note:** no action needed.\n"
        )

    prompt = build_prompt(digest, exemplar=exemplar)
    try:
        text = model(prompt)
    except Exception as exc:
        return fallback(digest, reason=f"{type(exc).__name__}: {exc}")

    problem = validate(text)
    if problem is not None:
        return fallback(digest, reason=problem)
    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_notes.py -q`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/notes.py \
        .github/scripts/tests/test_release_notes_notes.py
git commit -m "feat(release-notes): draft the business note with validation and fallback"
```

---

### Task 9: GitHub Models transport

**Files:**
- Modify: `.github/scripts/release_notes/notes.py`
- Test: `.github/scripts/tests/test_release_notes_notes.py`

- [ ] **Step 1: Write the failing test**

Append to `.github/scripts/tests/test_release_notes_notes.py`:

```python
def test_transport_posts_to_the_models_endpoint_and_returns_the_content():
    captured = {}

    def fake_post(url, *, headers, json_body):
        captured["url"] = url
        captured["headers"] = headers
        captured["json_body"] = json_body
        return {"choices": [{"message": {"content": "RESULT"}}]}

    got = notes.github_models("PROMPT", token="tok123", post=fake_post)
    assert got == "RESULT"
    assert captured["url"] == notes.MODELS_ENDPOINT
    assert captured["headers"]["Authorization"] == "Bearer tok123"
    assert captured["json_body"]["model"] == notes.MODEL
    assert captured["json_body"]["messages"][0]["content"] == "PROMPT"


def test_transport_raises_on_a_malformed_response():
    import pytest

    with pytest.raises(ValueError, match="unexpected response"):
        notes.github_models("P", token="t", post=lambda u, *, headers, json_body: {"oops": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_notes.py -q`
Expected: FAIL — `AttributeError: module 'release_notes.notes' has no attribute 'github_models'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports at the top of `.github/scripts/release_notes/notes.py`:

```python
import urllib.request
```

Then append to the module:

```python
MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
MODEL = "openai/gpt-4o"


def _post(url: str, *, headers: dict, json_body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(json_body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def github_models(prompt: str, *, token: str, post: Callable[..., dict] = _post) -> str:
    """Call GitHub Models with the job's GITHUB_TOKEN. No new secret needed.

    The calling workflow job must declare `permissions: models: read`.
    """
    payload = post(
        MODELS_ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json_body={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
    )
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected response from GitHub Models: {payload!r}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/test_release_notes_notes.py -q`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/notes.py \
        .github/scripts/tests/test_release_notes_notes.py
git commit -m "feat(release-notes): call GitHub Models with the job token"
```

---

### Task 10: The quackback publish interface

**Files:**
- Create: `.github/scripts/release_notes/quackback.py`

- [ ] **Step 1: Write the module**

No test: the module has no behaviour to assert beyond raising, and a test that
asserts `NotImplementedError` only pins a placeholder in place.

```python
#!/usr/bin/env python3
"""Publish a release note to quackback.

Not implemented. Only quackback's widget-token endpoint is visible from this
repository (`lex/lex_app/views.py:44`); whether it can ingest a changelog at
all is unconfirmed. This module exists so the call site in
publish_release_notes.yml is written once, against a stable signature, and a
follow-up spec fills in the body without touching anything upstream.

When implementing: the note passed here is the human-approved release body,
not the drafted one. Read it from the release at publish time.
"""

from __future__ import annotations


def publish(tag: str, body: str, *, token: str) -> None:
    """Publish `body` as the release note for `tag`."""
    raise NotImplementedError(
        "quackback publishing is not implemented — see "
        "docs/superpowers/specs/2026-08-05-release-notes-automation-design.md"
    )
```

- [ ] **Step 2: Verify it imports**

Run: `.venv-test/bin/python -c "import sys; sys.path.insert(0,'.github/scripts'); from release_notes import quackback; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add .github/scripts/release_notes/quackback.py
git commit -m "feat(release-notes): stub the quackback publish interface"
```

---

### Task 11: CLI entrypoints

**Files:**
- Create: `.github/scripts/release_notes/__main__.py`

- [ ] **Step 1: Write the module**

Two subcommands, matching the two workflow stages. This is glue over already-tested
units, so it is covered by the workflow smoke run in Task 12 rather than by unit tests.

```python
#!/usr/bin/env python3
"""CLI for the release-notes pipeline.

    python -m release_notes draft-notes    --tag v2.1.7 > body.md
    python -m release_notes render-changelog --tag v2.1.7 --date 2026-08-05

Both subcommands rebuild the digest from scratch. It is deterministic given a
fixed tag, which is why the changelog can be re-derived at promotion time
instead of being carried between two workflow runs as an artifact.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from release_notes import changelog, digest, notes, ranges

EXEMPLAR_PATH = ranges.REPO_ROOT / "docs/releases/RELEASE_NOTES_2.1.3_github.md"
CHANGELOG_PATH = ranges.REPO_ROOT / "CHANGELOG.md"


def _all_tags(tag: str) -> list[str]:
    result = subprocess.run(
        ["git", "tag", "--merged", tag, "--sort=-creatordate"],
        cwd=ranges.REPO_ROOT, check=True, capture_output=True, text=True,
    )
    return result.stdout.splitlines()


def _pac_log(pac_checkout: Path):
    """A `run_log` bound to a PAC working copy instead of this repository."""

    def run_log(from_ref: str | None, to_ref: str) -> str:
        spec = f"{from_ref}..{to_ref}" if from_ref else to_ref
        result = subprocess.run(
            ["git", "log", "--no-merges", f"--pretty=%h{digest._FIELD_SEP}%s", spec],
            cwd=pac_checkout, check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()

    return run_log


def _digest_for(tag: str, *, pac_checkout: Path | None = None) -> dict:
    previous = ranges.previous_release_tag(tag, tags=_all_tags(tag))

    backend = digest.enrich_with_prs(digest.collect_commits(previous, tag))

    frontend: list[digest.Commit] = []
    fe_range = ranges.frontend_range(previous, tag)
    if fe_range is None:
        print("No frontend manifest at one or both ends — omitting the frontend section.",
              file=sys.stderr)
    elif pac_checkout is None:
        print(f"Frontend range {fe_range.from_sha}..{fe_range.to_sha} resolved, but no PAC "
              "checkout was supplied — omitting the frontend section.", file=sys.stderr)
    else:
        frontend = digest.collect_commits(
            fe_range.from_sha, fe_range.to_sha, run_log=_pac_log(pac_checkout)
        )
        print(f"Frontend: {len(frontend)} commits in "
              f"{fe_range.from_sha}..{fe_range.to_sha}", file=sys.stderr)

    return digest.build_digest(tag, previous, backend, frontend)


def _pac_arg(args: argparse.Namespace) -> Path | None:
    return Path(args.pac_checkout) if args.pac_checkout else None


def cmd_draft_notes(args: argparse.Namespace) -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN is not set", file=sys.stderr)
        return 1
    exemplar = EXEMPLAR_PATH.read_text(encoding="utf-8")
    body = notes.draft(
        _digest_for(args.tag, pac_checkout=_pac_arg(args)),
        exemplar=exemplar,
        model=lambda prompt: notes.github_models(prompt, token=token),
    )
    sys.stdout.write(body)
    return 0


def cmd_render_changelog(args: argparse.Namespace) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "ExcellenceCloudGmbH/lex-app")
    section = changelog.render(
        _digest_for(args.tag, pac_checkout=_pac_arg(args)), date=args.date, repo=repo
    )
    existing = CHANGELOG_PATH.read_text(encoding="utf-8") if CHANGELOG_PATH.exists() else None
    CHANGELOG_PATH.write_text(changelog.prepend(existing, section), encoding="utf-8")
    print(f"Wrote {CHANGELOG_PATH}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    # --pac-checkout is accepted now and supplied later. Wiring the PAC
    # checkout into the workflows needs FRONTEND_REPO_TOKEN, which does not
    # exist — see the spec's Prerequisite section. Without it the frontend
    # section is omitted rather than guessed.
    for name, help_text, handler in (
        ("draft-notes", "Draft the business note to stdout.", cmd_draft_notes),
        ("render-changelog", "Prepend a section to CHANGELOG.md.", cmd_render_changelog),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--tag", required=True)
        p.add_argument(
            "--pac-checkout",
            default=None,
            help="Path to a process-admin-general-client working copy. "
                 "Omit to skip the frontend section.",
        )
        if name == "render-changelog":
            p.add_argument("--date", required=True, help="ISO date, e.g. 2026-08-05")
        p.set_defaults(func=handler)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify the CLI parses and the digest builds against real history**

Run: `cd /home/syscall/Documents/lex && PYTHONPATH=.github/scripts .venv-test/bin/python -m release_notes render-changelog --tag v2.1.6 --date 2026-08-05`
Expected: `Wrote /home/syscall/Documents/lex/CHANGELOG.md` on stderr, and a `CHANGELOG.md` containing a `## [2.1.6] - 2026-08-05` section with real entries from between `v2.1.5` and `v2.1.6`.

- [ ] **Step 3: Inspect the output, then discard it**

Run: `head -30 CHANGELOG.md && rm CHANGELOG.md`
Expected: the preamble, the version heading, and plausible `### Fixed` / `### Added` entries. The file is removed because the first real one should be written by the workflow.

- [ ] **Step 4: Commit**

```bash
git add .github/scripts/release_notes/__main__.py
git commit -m "feat(release-notes): CLI entrypoints for drafting and rendering"
```

---

### Task 12: Draft the note at the prerelease gate

**Files:**
- Modify: `.github/workflows/prerelease_gate.yml` (append a job after `mark-validated`)

- [ ] **Step 1: Add the job**

Append to the `jobs:` block of `.github/workflows/prerelease_gate.yml`, after `mark-validated`:

```yaml
  # ── GREEN: draft the business-facing release note into the prerelease
  #    body, below the gate marker. The human who promotes the release is
  #    already reading this page, so review costs no extra step.
  #
  #    Never fails the gate. A changelog generator that can turn a release
  #    red is worse than one that occasionally emits a stub.
  draft-notes:
    name: "Draft release notes (green)"
    needs: [gate, mark-validated]
    if: ${{ needs.gate.outputs.overall_ok == 'true' }}
    runs-on: ubuntu-latest
    permissions:
      contents: write   # edit the prerelease body
      models: read      # GitHub Models inference
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # tags + full history are the input

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Draft the note
        id: draft
        continue-on-error: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          TAG: ${{ github.event.release.tag_name }}
        run: |
          set -uo pipefail
          PYTHONPATH=.github/scripts python -m release_notes draft-notes --tag "$TAG" > drafted.md
          echo "Drafted $(wc -l < drafted.md) lines."

      - name: Append the note to the prerelease body
        if: ${{ always() && steps.draft.outcome == 'success' }}
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          TAG: ${{ github.event.release.tag_name }}
        run: |
          set -euo pipefail
          BODY=$(gh release view "$TAG" --repo "$GITHUB_REPOSITORY" --json body --jq '.body')
          if printf '%s' "$BODY" | grep -q 'lex:notes-drafted'; then
            echo "Notes already drafted for $TAG — leaving the human's edits alone."
            exit 0
          fi
          {
            printf '%s\n' "$BODY"
            printf '\n---\n\n'
            cat drafted.md
            printf '\n<!-- lex:notes-drafted tag=%s -->\n' "$TAG"
          } > combined.md
          gh release edit "$TAG" --repo "$GITHUB_REPOSITORY" --notes-file combined.md
          echo "Drafted release notes onto $TAG — review and edit before promoting."
```

- [ ] **Step 2: Verify the workflow still parses**

Run: `python -c "import yaml; d=yaml.safe_load(open('.github/workflows/prerelease_gate.yml')); print(sorted(d['jobs']))"`
Expected: `['cleanup-on-red', 'draft-notes', 'gate', 'mark-validated', 'select-clusters']`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/prerelease_gate.yml
git commit -m "ci(release-notes): draft the business note into the prerelease body

Runs after mark-validated on green. continue-on-error so a drafting
failure can never turn the gate red. Re-running is idempotent: the
lex:notes-drafted marker stops it overwriting a human's edits."
```

---

### Task 13: Commit the changelog on promotion

**Files:**
- Create: `.github/workflows/publish_release_notes.yml`

- [ ] **Step 1: Create the workflow**

```yaml
# ──────────────────────────────────────────────────────────────────────
#  On promotion to a full release: commit the mechanical CHANGELOG.md
#  entry, and (later) publish the approved body to quackback.
#
#  The digest is rebuilt here rather than carried over from the gate run.
#  Workflow artifacts are scoped to the run that produced them, and the
#  gate runs on `prereleased` while this runs on `released` — two runs,
#  two events. Since the tag is fixed once the prerelease exists, the
#  digest is deterministic, so re-deriving it is cheaper than cross-run
#  artifact plumbing and needs no extra token.
#
#  CHANGELOG.md is mechanical and is never hand-edited. Only the business
#  note in the release body is reviewed, so a human rewriting the prose
#  cannot desync the technical record.
# ──────────────────────────────────────────────────────────────────────
name: Publish Release Notes

on:
  release:
    types: [released]
  workflow_dispatch:
    inputs:
      tag:
        description: "Release tag to (re)generate the changelog entry for"
        required: true
        type: string

jobs:
  changelog:
    name: "Commit the CHANGELOG entry"
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          ref: ${{ github.event.repository.default_branch }}

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Render and prepend the entry
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          TAG: ${{ inputs.tag || github.event.release.tag_name }}
        run: |
          set -euo pipefail
          DATE=$(git log -1 --format=%cs "refs/tags/$TAG")
          PYTHONPATH=.github/scripts python -m release_notes render-changelog \
            --tag "$TAG" --date "$DATE"

      - name: Commit
        env:
          TAG: ${{ inputs.tag || github.event.release.tag_name }}
        run: |
          set -euo pipefail
          if git diff --quiet -- CHANGELOG.md; then
            echo "No changelog change for $TAG — nothing to commit."
            exit 0
          fi
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add CHANGELOG.md
          git commit -m "docs(changelog): ${TAG}"
          # The release has already shipped by this point, so a push race is
          # safe to retry once and then fail loudly.
          git push || { git pull --rebase && git push; }
```

- [ ] **Step 2: Verify the workflow parses**

Run: `python -c "import yaml; d=yaml.safe_load(open('.github/workflows/publish_release_notes.yml')); print(sorted(d['jobs']))"`
Expected: `['changelog']`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/publish_release_notes.yml
git commit -m "ci(release-notes): commit the CHANGELOG entry on promotion"
```

---

### Task 14: Guard the frontend manifest

**Files:**
- Create: `.github/workflows/frontend_manifest_guard.yml`

Without this, the manifest is a convention nobody remembers, and the frontend
section stays permanently empty.

- [ ] **Step 1: Create the workflow**

```yaml
# ──────────────────────────────────────────────────────────────────────
#  A rebuilt frontend bundle must say which PAC commit produced it.
#
#  frontend_build.yml would do this automatically, but it has never run:
#  every execution since v2.0.0rc218 failed at its secrets preflight
#  (FRONTEND_REPO_TOKEN and NPM_MARMELAB_TOKEN are not configured), so
#  bundles are hand-committed with no provenance at all.
#
#  Until that is repaired, this guard makes the manual manifest update
#  impossible to forget. Release notes cannot report frontend changes
#  truthfully without it.
# ──────────────────────────────────────────────────────────────────────
name: Frontend Manifest Guard

on:
  pull_request:
    paths:
      - "lex/react/build/**"

jobs:
  manifest:
    name: "Bundle change requires a manifest update"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Check the manifest moved with the bundle
        env:
          BASE: ${{ github.event.pull_request.base.sha }}
          HEAD: ${{ github.event.pull_request.head.sha }}
        run: |
          set -euo pipefail
          MANIFEST="lex/react/build/.frontend-version.json"
          CHANGED=$(git diff --name-only "$BASE" "$HEAD" -- lex/react/build/ | grep -v "^${MANIFEST}$" || true)
          if [ -z "$CHANGED" ]; then
            echo "No bundle files changed apart from the manifest — nothing to check."
            exit 0
          fi
          if git diff --name-only "$BASE" "$HEAD" -- "$MANIFEST" | grep -q .; then
            echo "Bundle and manifest both updated."
            exit 0
          fi
          echo "::error::This PR changes lex/react/build/ without updating ${MANIFEST}."
          echo "Add the PAC commit the bundle was built from, e.g.:"
          echo '  {"repo": "ExcellenceCloudGmbH/process-admin-general-client",'
          echo '   "branch": "lex-app-v2-pac-latest", "sha": "<pac sha>",'
          echo '   "built_at": "<ISO 8601 UTC>"}'
          echo "Without it, release notes cannot report frontend changes truthfully."
          exit 1
```

- [ ] **Step 2: Verify the workflow parses**

Run: `python -c "import yaml; d=yaml.safe_load(open('.github/workflows/frontend_manifest_guard.yml')); print(sorted(d['jobs']))"`
Expected: `['manifest']`

- [ ] **Step 3: Run the whole script suite one last time**

Run: `.venv-test/bin/python -m pytest .github/scripts/tests/ -q`
Expected: PASS — 158 pre-existing plus the new tests, no failures

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/frontend_manifest_guard.yml
git commit -m "ci(release-notes): require a manifest update when the bundle changes"
```

---

## Manual verification

Automated tests cover the units. These two need a human, because they exercise the
model and the GitHub API and cannot be asserted meaningfully offline.

- [ ] **Dry-run the drafter against real history.** With `gh auth status` green:

```bash
PYTHONPATH=.github/scripts GITHUB_TOKEN=$(gh auth token) \
  .venv-test/bin/python -m release_notes draft-notes --tag v2.1.6
```

Read the output. It should look like `docs/releases/RELEASE_NOTES_2.1.3_github.md` —
bold lead-ins, plain language, no repository names, no commit hashes. If it reads
like a commit log, tune `_INSTRUCTIONS` in `notes.py` before shipping.

- [ ] **Cut a throwaway prerelease** on a scratch tag, confirm the gate drafts notes
into its body, edit the body by hand, promote it, and confirm `CHANGELOG.md` gains
an entry that reflects the commits rather than your edits. Delete the tag afterwards.

---

## Out of scope, and why

- **Repairing `frontend_build.yml`.** Its own piece of work — switching on a build
  that has never succeeded, and which would start replacing hand-built bundles,
  needs validating before it runs on a release. Until then Task 14's guard keeps
  the manifest honest by hand.
- **Populating `frontend` commits in `_digest_for`.** The branch exists and logs
  what it would do, but it cannot return real commits until a manifest exists at
  both ends of a range. Wiring the PAC checkout into the job belongs with the
  workflow repair above.
- **Quackback publishing.** Interface only — see Task 10.
- **Backfilling the 222 historical tags.** `render-changelog` accepts any tag, so
  a backfill is a loop over the six `v2.1.x` releases whenever someone wants it.
