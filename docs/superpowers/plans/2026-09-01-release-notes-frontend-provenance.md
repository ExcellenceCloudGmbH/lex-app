# Frontend Provenance for Release Notes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make frontend release notes exist and be correct when lex-app is released, with any gap recorded so it can be corrected later instead of blocking the release.

**Architecture:** Three guarantees. (G1) PAC's own workflow writes `lex/react/build/.frontend-version.json` from `$GITHUB_SHA` — the job that built the bundle is the only one that knows which commit produced it. (G2) `frontend_manifest_guard.yml` refuses any bundle PR whose manifest is absent or schema-invalid. (G3) `verify-frontend` warns at the prerelease gate and `render-changelog` writes a recoverable marker at publish; `backfill` later fills history and repairs gaps. Historical tags are served by a committed side-car map, because a file cannot be added to a tag that already exists.

**Tech Stack:** Python 3.12 (stdlib only — `subprocess`, `json`, `re`, `pathlib`), pytest, GitHub Actions. No new runtime dependencies.

**Spec:** [`docs/superpowers/specs/2026-09-01-release-notes-frontend-provenance-design.md`](../specs/2026-09-01-release-notes-frontend-provenance-design.md)

---

## File Structure

| File | Responsibility |
|---|---|
| `.github/scripts/release_notes/ranges.py` *(modify)* | Resolve commit ranges. Gains `bundle_commit_at`, `load_history`, and a side-car fallback inside `frontend_sha_at`. |
| `.github/scripts/release_notes/manifest.py` *(create)* | **One job:** decide whether a manifest blob is valid, and say why not. Imported by the guard and by tests. Kept out of `ranges.py` because it is validation, not range resolution. |
| `.github/scripts/release_notes/frontend-history.json` *(create)* | Data. Bundle-commit → PAC SHA for pre-manifest tags. Three entries, frozen after backfill. |
| `.github/scripts/release_notes/changelog.py` *(modify)* | Render a digest as a changelog section. Gains the gap marker and `find_gaps`. Stays pure. |
| `.github/scripts/release_notes/notes.py` *(modify)* | One-line change: make the drafting-failure notice visible in rendered markdown. |
| `.github/scripts/release_notes/__main__.py` *(modify)* | CLI. Gains `verify-frontend`, `backfill`, `list-gaps`, `check-manifest`, and tag-span helpers. |
| `.github/workflows/prerelease_gate.yml` *(modify)* | Run `verify-frontend`; pass `--pac-checkout` to `draft-notes`. |
| `.github/workflows/publish_release_notes.yml` *(modify)* | Pass `--pac-checkout` to `render-changelog`. |
| `.github/workflows/frontend_manifest_guard.yml` *(modify)* | Call `check-manifest` in addition to the existing did-it-move check. |
| PAC `.github/workflows/push-build-to-pip-package.yml` *(modify)* | Dispatch trigger, `dry_run` input, `checkout@v4`, correct repo name, per-dispatch PR branch, and the manifest write. |

Tests mirror the modules: `.github/scripts/tests/test_release_notes_{ranges,changelog,manifest,backfill,verify}.py`, gated by `scripts_tests.yml` on any push touching `.github/scripts/**`.

**No cluster tests.** AGENTS.md directive 2 applies to changes under `lex/`; nothing here touches `lex/`.

---

## Task 1: Confirm prerequisites

**Files:** none — verification only.

- [ ] **Step 1: Confirm PR #702 is merged**

The drafter must carry PR-body context or every note is written from ~9-word subjects.

```bash
cd /home/syscall/Documents/lex
gh pr view 702 --json state,mergedAt -q '.state + " " + (.mergedAt // "unmerged")'
grep -c '"detail"' .github/scripts/release_notes/digest.py
```

Expected: `MERGED <timestamp>` and a count of `1` or more.

**If the count is `0`, stop.** #702 is not merged. Either merge it or accept that every note in this plan drafts from subject lines. Do not work around it here.

- [ ] **Step 2: Confirm the provider registry needs no work**

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, ".github/scripts")
from release_notes import notes
assert "github-models" in notes.PROVIDERS, notes.PROVIDERS
assert notes.PROVIDER_ORDER[-1] == "github-models", notes.PROVIDER_ORDER
assert notes.ANTHROPIC_MODEL == "claude-sonnet-5", notes.ANTHROPIC_MODEL
print("provider registry OK:", notes.PROVIDER_ORDER)
EOF
```

Expected: `provider registry OK: ('anthropic', 'gemini', 'openai', 'github-models')`

- [ ] **Step 3: Confirm the baseline suite is green**

```bash
python3 -m pytest .github/scripts/tests/ -q
```

Expected: all pass. Record the count; every later task must keep it at or above this number.

---

## Task 2: `bundle_commit_at` — find the bundle commit for a ref

The side-car is keyed by the lex-app commit that last changed the vendored bundle. This computes that key.

**Files:**
- Modify: `.github/scripts/release_notes/ranges.py`
- Test: `.github/scripts/tests/test_release_notes_ranges.py`

- [ ] **Step 1: Write the failing tests**

Append to `.github/scripts/tests/test_release_notes_ranges.py`:

```python
def test_bundle_commit_at_returns_the_sha():
    def fake_run(ref: str) -> str | None:
        assert ref == "v2.1.6"
        return "a388985a1111111111111111111111111111aaaa"

    got = ranges.bundle_commit_at("v2.1.6", run=fake_run)
    assert got == "a388985a1111111111111111111111111111aaaa"


def test_bundle_commit_at_returns_none_when_no_bundle_history():
    assert ranges.bundle_commit_at("v1.0.0", run=lambda ref: None) is None


def test_bundle_commit_at_treats_blank_output_as_none():
    assert ranges.bundle_commit_at("v2.1.6", run=lambda ref: "") is None
    assert ranges.bundle_commit_at("v2.1.6", run=lambda ref: "   ") is None
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_ranges.py -k bundle_commit_at -v
```

Expected: FAIL — `AttributeError: module 'release_notes.ranges' has no attribute 'bundle_commit_at'`

- [ ] **Step 3: Implement**

In `.github/scripts/release_notes/ranges.py`, directly below the existing `git_show` function:

```python
# The directory whose last-changing commit identifies a vendored bundle. Used
# as the key into the historical provenance map, because pre-manifest tags
# carry no manifest and one cannot be added to a tag that already exists.
BUNDLE_PATH = "lex/react/build"


def _run_rev_list(ref: str) -> str | None:
    """The last commit touching BUNDLE_PATH as of `ref`, or None."""
    result = subprocess.run(
        ["git", "rev-list", "-1", ref, "--", BUNDLE_PATH],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def bundle_commit_at(
    ref: str, *, run: Callable[[str], str | None] = _run_rev_list
) -> str | None:
    """The lex-app commit that last changed the vendored bundle as of `ref`.

    None when the ref predates the bundle or git cannot answer. `run` is
    injectable so tests need no repository history.
    """
    sha = run(ref)
    if sha is None:
        return None
    return sha.strip() or None
```

- [ ] **Step 4: Run to verify they pass**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_ranges.py -k bundle_commit_at -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/ranges.py .github/scripts/tests/test_release_notes_ranges.py
git commit -m "feat(release-notes): resolve the bundle commit for a ref"
```

---

## Task 3: Side-car fallback in `frontend_sha_at`

**Files:**
- Modify: `.github/scripts/release_notes/ranges.py`
- Test: `.github/scripts/tests/test_release_notes_ranges.py`

- [ ] **Step 1: Write the failing tests**

Append to `.github/scripts/tests/test_release_notes_ranges.py`:

```python
_FULL_A = "a388985a1111111111111111111111111111aaaa"
_PAC_A = "1a2b3c4d5e6f70811111111111111111111bbbb"


def test_frontend_sha_at_falls_back_to_the_side_car():
    # No in-tree manifest, but the bundle commit is in the history map.
    got = ranges.frontend_sha_at(
        "v2.1.6",
        show=lambda ref, path: None,
        history=lambda: {_FULL_A: {"pac_sha": _PAC_A, "method": "hash-proof"}},
        bundle=lambda ref: _FULL_A,
    )
    assert got == _PAC_A


def test_in_tree_manifest_wins_over_the_side_car():
    got = ranges.frontend_sha_at(
        "v2.1.6",
        show=lambda ref, path: '{"sha": "fromtree"}',
        history=lambda: {_FULL_A: {"pac_sha": _PAC_A}},
        bundle=lambda ref: _FULL_A,
    )
    assert got == "fromtree"


def test_side_car_is_matched_by_short_key_too():
    # Entries may be written with an abbreviated key; the lookup must still hit.
    got = ranges.frontend_sha_at(
        "v2.1.6",
        show=lambda ref, path: None,
        history=lambda: {"a388985a": {"pac_sha": _PAC_A}},
        bundle=lambda ref: _FULL_A,
    )
    assert got == _PAC_A


def test_side_car_miss_returns_none():
    got = ranges.frontend_sha_at(
        "v2.1.6",
        show=lambda ref, path: None,
        history=lambda: {"deadbeef": {"pac_sha": _PAC_A}},
        bundle=lambda ref: _FULL_A,
    )
    assert got is None


def test_side_car_is_ignored_when_the_bundle_commit_is_unknown():
    got = ranges.frontend_sha_at(
        "v1.0.0",
        show=lambda ref, path: None,
        history=lambda: {_FULL_A: {"pac_sha": _PAC_A}},
        bundle=lambda ref: None,
    )
    assert got is None


@pytest.mark.parametrize(
    "entry",
    [
        {},                       # no pac_sha
        {"pac_sha": ""},          # blank is as untruthful as missing
        {"pac_sha": None},
        "not-a-dict",
        None,
    ],
)
def test_side_car_entries_that_say_nothing_are_none(entry):
    got = ranges.frontend_sha_at(
        "v2.1.6",
        show=lambda ref, path: None,
        history=lambda: {_FULL_A: entry},
        bundle=lambda ref: _FULL_A,
    )
    assert got is None


def test_load_history_returns_empty_on_a_missing_file(tmp_path):
    assert ranges.load_history(path=tmp_path / "nope.json") == {}


def test_load_history_returns_empty_on_unparseable_json(tmp_path):
    bad = tmp_path / "frontend-history.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ranges.load_history(path=bad) == {}


def test_load_history_returns_empty_when_the_top_level_is_not_an_object(tmp_path):
    bad = tmp_path / "frontend-history.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    assert ranges.load_history(path=bad) == {}
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_ranges.py -k "side_car or load_history or in_tree" -v
```

Expected: FAIL — `frontend_sha_at() got an unexpected keyword argument 'history'` and `no attribute 'load_history'`.

- [ ] **Step 3: Implement**

In `.github/scripts/release_notes/ranges.py`, add below `bundle_commit_at`:

```python
# Provenance for tags that predate the manifest. Committed, append-only, and
# frozen once the backfill lands. Each entry records how it was established so
# an inference is never stored looking like a proof.
HISTORY_PATH = Path(__file__).resolve().parent / "frontend-history.json"


def load_history(*, path: Path = HISTORY_PATH) -> dict:
    """The bundle-commit -> {pac_sha, method} map. Empty on any problem.

    A corrupt or missing lookup table must never break drafting: the caller
    treats an empty map exactly as it treats an absent manifest, which omits
    the frontend section rather than guessing.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
```

Then **replace** the existing `frontend_sha_at` with:

```python
def frontend_sha_at(
    ref: str,
    *,
    show: Callable[[str, str], str | None] = git_show,
    history: Callable[[], dict] = load_history,
    bundle: Callable[[str], str | None] = bundle_commit_at,
) -> str | None:
    """The PAC SHA that produced the bundle at `ref`, or None.

    In-tree manifest first — it is written by the job that built the bundle and
    is therefore correct by construction. The committed side-car answers for
    tags that predate the manifest, which no in-tree file can ever do: a file
    cannot be added to a tag that already exists.
    """
    blob = show(ref, MANIFEST_PATH)
    if blob is not None:
        try:
            sha = json.loads(blob)["sha"]
        except (json.JSONDecodeError, KeyError, TypeError):
            sha = None
        # A blank or null sha is exactly as untruthful as a missing one, and
        # the manifest was hand-written before G1, so a typo can produce it.
        if sha:
            return sha

    table = history()
    if not table:
        return None
    key = bundle(ref)
    if not key:
        return None
    entry = table.get(key)
    if entry is None:
        # Entries may carry an abbreviated key.
        entry = next((v for k, v in table.items() if key.startswith(k)), None)
    if not isinstance(entry, dict):
        return None
    return entry.get("pac_sha") or None
```

- [ ] **Step 4: Run the whole ranges suite**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_ranges.py -v
```

Expected: all pass, including the pre-existing tests — the in-tree path is unchanged for callers that pass only `show`.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/ranges.py .github/scripts/tests/test_release_notes_ranges.py
git commit -m "feat(release-notes): serve pre-manifest tags from a committed side-car"
```

---

## Task 4: Populate `frontend-history.json`

Data task. Three bundles cover every tag in scope.

**Files:**
- Create: `.github/scripts/release_notes/frontend-history.json`

- [ ] **Step 1: Resolve the three full bundle SHAs**

```bash
cd /home/syscall/Documents/lex
for t in v2.0.0rc221 v2.1.1 v2.1.2 v2.1.3 v2.1.4 v2.1.5 v2.1.6 v2.1.7; do
  printf "%-14s %s\n" "$t" "$(git rev-list -1 "$t" -- lex/react/build)"
done | sort -k2 -u
```

Expected: exactly three distinct SHAs, abbreviating to `e56f7557`, `d56c70e7`, `a388985a`.

- [ ] **Step 2: Find each bundle's candidate PAC commits**

For each bundle commit, get its date, then list PAC commits just before it:

```bash
BUNDLE=e56f7557
D=$(git log -1 --format=%aI "$BUNDLE")
echo "bundle $BUNDLE committed $D"
git -C /home/syscall/LUND_IT/process-admin-general-client log \
  --until="$D" --format='%H %ad %s' --date=short -n 8 origin/lex-app-v2-pac-latest
```

Repeat for `d56c70e7` and `a388985a`.

- [ ] **Step 3: Prove a candidate by content hash**

Vite asset filenames are content hashes, so a match is proof rather than inference. Get the target hash:

```bash
git show v2.1.7:lex/react/build/index.html | grep -oE 'assets/index-[A-Za-z0-9_-]+\.js'
```

Expected for `v2.1.7`: `assets/index-BD1SQUOi.js`

Then, in a PAC worktree at the candidate commit:

```bash
cd /home/syscall/LUND_IT/process-admin-general-client
git worktree add /tmp/pac-probe <candidate-sha>
cd /tmp/pac-probe
NPM_MARMELAB_TOKEN=x yarn install --frozen-lockfile
NPM_MARMELAB_TOKEN=x yarn build
ls build/assets/ | grep -E '^index-.*\.js$'
```

A filename equal to the target proves the attribution. Record `"method": "hash-proof"`.

**If no candidate reproduces the hash** (node or yarn version drift changes it), take the newest PAC commit at or before the bundle's commit date and record `"method": "date-window"`. Do not record `hash-proof` for an unproven match — the distinction is the point of the field.

Clean up: `git worktree remove /tmp/pac-probe --force`

- [ ] **Step 4: Write the file**

Create `.github/scripts/release_notes/frontend-history.json`, substituting the SHAs resolved above:

```json
{
  "<full sha of e56f7557>": {
    "pac_sha": "<proven PAC sha>",
    "method": "hash-proof",
    "note": "bundle of 2026-06-30; covers v2.0.0rc221, v2.1.1, v2.1.2"
  },
  "<full sha of d56c70e7>": {
    "pac_sha": "<proven PAC sha>",
    "method": "hash-proof",
    "note": "bundle of 2026-07-14; covers v2.1.3"
  },
  "<full sha of a388985a>": {
    "pac_sha": "<proven PAC sha>",
    "method": "hash-proof",
    "note": "bundle of 2026-07-22; covers v2.1.4 through v2.1.7"
  }
}
```

- [ ] **Step 5: Verify every in-scope tag now resolves**

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, ".github/scripts")
from release_notes import ranges
for t in ["v2.0.0rc221","v2.1.1","v2.1.2","v2.1.3","v2.1.4","v2.1.5","v2.1.6","v2.1.7"]:
    print(f"{t:14s} {ranges.frontend_sha_at(t)}")
EOF
```

Expected: a non-`None` SHA on every line.

- [ ] **Step 6: Commit**

```bash
git add .github/scripts/release_notes/frontend-history.json
git commit -m "feat(release-notes): attribute the three pre-manifest frontend bundles"
```

---

## Task 5: Manifest schema validation

**Files:**
- Create: `.github/scripts/release_notes/manifest.py`
- Test: `.github/scripts/tests/test_release_notes_manifest.py`

- [ ] **Step 1: Write the failing tests**

Create `.github/scripts/tests/test_release_notes_manifest.py`:

```python
"""Tests for release_notes.manifest — provenance-file validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import manifest  # noqa: E402

VALID_SHA = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"


def test_a_well_formed_manifest_is_valid():
    blob = (
        '{"repo": "ExcellenceCloudGmbH/process-admin-general-client",'
        ' "branch": "lex-app-v2-pac-latest",'
        f' "sha": "{VALID_SHA}",'
        ' "built_at": "2026-09-01T10:00:00Z"}'
    )
    assert manifest.validate(blob) is None


def test_absent_manifest_is_reported():
    assert "absent" in manifest.validate(None)


@pytest.mark.parametrize("blob", ["{not json", "", "   "])
def test_unparseable_manifest_is_reported(blob):
    reason = manifest.validate(blob)
    assert reason is not None and "JSON" in reason


@pytest.mark.parametrize("blob", ["[1, 2]", '"a string"', "42"])
def test_non_object_manifest_is_reported(blob):
    assert "object" in manifest.validate(blob)


@pytest.mark.parametrize("blob", ['{}', '{"sha": ""}', '{"sha": null}', '{"sha": 7}'])
def test_missing_or_empty_sha_is_reported(blob):
    assert "sha" in manifest.validate(blob)


@pytest.mark.parametrize(
    "sha",
    [
        "a388985a",                                    # abbreviated
        "A1B2C3D4E5F6708192A3B4C5D6E7F8091A2B3C4D",    # uppercase
        "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",    # not hex
        "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4",     # 39 chars
    ],
)
def test_sha_must_be_forty_lowercase_hex(sha):
    reason = manifest.validate('{"sha": "%s"}' % sha)
    assert reason is not None and "40" in reason
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_manifest.py -v
```

Expected: FAIL — `ImportError: cannot import name 'manifest'`

- [ ] **Step 3: Implement**

Create `.github/scripts/release_notes/manifest.py`:

```python
#!/usr/bin/env python3
"""Validate the frontend provenance manifest.

Separate from `ranges` on purpose: `ranges` answers "what shipped", this
answers "is this file trustworthy". The guard workflow imports it so the rule
lives in one testable place rather than in shell.
"""

from __future__ import annotations

import json
import re

# A full commit sha, lowercase. An abbreviation is rejected deliberately: the
# manifest is written by CI from $GITHUB_SHA, so anything shorter means it was
# edited by hand, which is the case this validation exists to catch.
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def validate(blob: str | None) -> str | None:
    """None when `blob` is a valid manifest, else a human-readable reason."""
    if blob is None:
        return "manifest is absent"
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        return f"manifest is not valid JSON ({exc.msg})"
    if not isinstance(data, dict):
        return "manifest is not a JSON object"
    sha = data.get("sha")
    if not isinstance(sha, str) or not sha.strip():
        return "manifest has no 'sha'"
    if not SHA_RE.match(sha):
        return f"manifest 'sha' is not a 40-character lowercase hex commit: {sha!r}"
    return None
```

- [ ] **Step 4: Run to verify they pass**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_manifest.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/manifest.py .github/scripts/tests/test_release_notes_manifest.py
git commit -m "feat(release-notes): validate the frontend provenance manifest"
```

---

## Task 6: `check-manifest` subcommand and guard wiring

**Files:**
- Modify: `.github/scripts/release_notes/__main__.py`
- Modify: `.github/workflows/frontend_manifest_guard.yml`

- [ ] **Step 1: Add the subcommand**

In `.github/scripts/release_notes/__main__.py`, add after `cmd_render_changelog`:

```python
def cmd_check_manifest(args: argparse.Namespace) -> int:
    """Validate the on-disk provenance manifest. Non-zero when invalid."""
    path = ranges.REPO_ROOT / ranges.MANIFEST_PATH
    blob = path.read_text(encoding="utf-8") if path.exists() else None
    reason = manifest.validate(blob)
    if reason is None:
        print(f"{ranges.MANIFEST_PATH}: OK", file=sys.stderr)
        return 0
    print(
        f"::error title=Invalid frontend manifest::{ranges.MANIFEST_PATH}: {reason}"
    )
    return 1
```

Extend the import at the top of the file:

```python
from release_notes import changelog, digest, manifest, notes, ranges
```

And register it in `build_parser`, immediately before `return parser`:

```python
    check = sub.add_parser(
        "check-manifest", help="Validate lex/react/build/.frontend-version.json."
    )
    check.set_defaults(func=cmd_check_manifest)
```

- [ ] **Step 2: Verify both outcomes by hand**

```bash
cd /home/syscall/Documents/lex
PYTHONPATH=.github/scripts python -m release_notes check-manifest; echo "exit=$?"
```

Expected now: `::error title=Invalid frontend manifest::... manifest is absent` and `exit=1` — correct, no bundle has landed under G1 yet.

```bash
mkdir -p lex/react/build
echo '{"sha":"1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"}' > lex/react/build/.frontend-version.json
PYTHONPATH=.github/scripts python -m release_notes check-manifest; echo "exit=$?"
rm lex/react/build/.frontend-version.json
```

Expected: `OK` and `exit=0`.

- [ ] **Step 3: Wire it into the guard**

In `.github/workflows/frontend_manifest_guard.yml`, after the existing "Check the manifest moved with the bundle" step, add:

```yaml
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Validate the manifest contents
        run: |
          set -euo pipefail
          PYTHONPATH=.github/scripts python -m release_notes check-manifest
```

The existing step proves the file moved with the bundle; this one proves it says something true. `ranges.py` already warns that a hand-written manifest can carry a blank sha — this is where that becomes impossible to merge.

- [ ] **Step 4: Run the full suite**

```bash
python3 -m pytest .github/scripts/tests/ -q
```

Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/__main__.py .github/workflows/frontend_manifest_guard.yml
git commit -m "feat(release-notes): reject a bundle whose manifest says nothing true"
```

---

## Task 7: The changelog gap marker

**Files:**
- Modify: `.github/scripts/release_notes/changelog.py`
- Modify: `.github/scripts/release_notes/__main__.py`
- Test: `.github/scripts/tests/test_release_notes_changelog.py`

- [ ] **Step 1: Write the failing tests**

Append to `.github/scripts/tests/test_release_notes_changelog.py`:

```python
def _digest(**over):
    base = {
        "tag": "v2.1.8",
        "previous_tag": "v2.1.7",
        "changes": [
            {
                "sha": "abc1234", "component": "backend", "type": "fix",
                "scope": None, "breaking": False,
                "subject": "stop the grid dropping rows",
                "pr_number": 900, "internal": False,
            }
        ],
    }
    base.update(over)
    return base


def test_marker_is_written_when_frontend_is_unrecorded():
    out = changelog.render(
        _digest(frontend_recorded=False), date="2026-09-01", repo="o/r"
    )
    assert changelog.GAP_MARKER in out


def test_no_marker_when_frontend_is_recorded():
    out = changelog.render(
        _digest(frontend_recorded=True), date="2026-09-01", repo="o/r"
    )
    assert changelog.GAP_MARKER not in out


def test_absent_flag_means_recorded():
    # Callers that never resolved a frontend range at all must not be marked.
    out = changelog.render(_digest(), date="2026-09-01", repo="o/r")
    assert changelog.GAP_MARKER not in out


def test_marker_sits_directly_under_the_version_heading():
    out = changelog.render(
        _digest(frontend_recorded=False), date="2026-09-01", repo="o/r"
    ).splitlines()
    assert out[0].startswith("## [2.1.8]")
    assert out[2] == changelog.GAP_MARKER


def test_find_gaps_lists_only_marked_versions():
    text = (
        "# Changelog\n\n"
        "## [2.1.8] - 2026-09-01\n\n" + changelog.GAP_MARKER + "\n\n"
        "### Fixed\n- **backend** a\n\n"
        "## [2.1.7] - 2026-08-14\n\n"
        "### Fixed\n- **backend** b\n\n"
        "## [2.1.6] - 2026-07-23\n\n" + changelog.GAP_MARKER + "\n"
    )
    assert changelog.find_gaps(text) == ["2.1.8", "2.1.6"]


def test_find_gaps_is_empty_when_nothing_is_marked():
    text = "# Changelog\n\n## [2.1.7] - 2026-08-14\n\n### Fixed\n- **backend** b\n"
    assert changelog.find_gaps(text) == []
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_changelog.py -k "marker or find_gaps or recorded" -v
```

Expected: FAIL — `no attribute 'GAP_MARKER'`.

- [ ] **Step 3: Implement in `changelog.py`**

Add below `EXCLUDED_TYPES`:

```python
# Written into a release's section when the frontend range could not be
# resolved. It is the durable record of the gap: visible to a human reading
# the changelog, and greppable by `list-gaps`, so no separate state file can
# drift away from reality.
GAP_MARKER = "> **Frontend changes for this release are not yet recorded.**"
```

In `render`, replace the `parts` initialisation:

```python
    version = digest["tag"].lstrip("v")
    parts = [f"## [{version}] - {date}", ""]
    if digest.get("frontend_recorded", True) is False:
        parts.extend([GAP_MARKER, ""])
```

Add at the end of the module:

```python
def find_gaps(existing: str, *, marker: str = GAP_MARKER) -> list[str]:
    """Versions whose section carries the gap marker, newest first.

    Reads the changelog rather than a side file so the work queue cannot
    disagree with the record it is derived from.
    """
    gaps: list[str] = []
    current: str | None = None
    for line in existing.splitlines():
        if line.startswith("## ["):
            current = line[len("## ["):].split("]", 1)[0]
        elif current and marker in line:
            gaps.append(current)
            current = None
    return gaps
```

- [ ] **Step 4: Set the flag in `__main__.py`**

In `_digest_for`, the three branches already distinguish the cases. Track them and attach the result. Replace the body from `frontend: list[digest.Commit] = []` through the `return`:

```python
    frontend: list[digest.Commit] = []
    frontend_recorded = True
    fe_range = ranges.frontend_range(previous, tag)
    if fe_range is None:
        # Unresolvable is NOT the same as "no frontend changes", and the
        # changelog must not let a reader confuse them.
        frontend_recorded = False
        print("No frontend provenance at one or both ends — omitting the frontend section.",
              file=sys.stderr)
    elif pac_checkout is None:
        frontend_recorded = False
        print(f"Frontend range {fe_range.from_sha}..{fe_range.to_sha} resolved, but no PAC "
              "checkout was supplied — omitting the frontend section.", file=sys.stderr)
    else:
        frontend = digest.collect_commits(
            fe_range.from_sha, fe_range.to_sha, run_log=_pac_log(pac_checkout)
        )
        print(f"Frontend: {len(frontend)} commits in "
              f"{fe_range.from_sha}..{fe_range.to_sha}", file=sys.stderr)

    built = digest.build_digest(tag, previous, backend, frontend)
    built["frontend_recorded"] = frontend_recorded
    return built
```

`notes.build_prompt` serialises only `digest["changes"]`, so this extra top-level key does not reach the model prompt.

- [ ] **Step 5: Run to verify they pass**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_changelog.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add .github/scripts/release_notes/changelog.py .github/scripts/release_notes/__main__.py \
        .github/scripts/tests/test_release_notes_changelog.py
git commit -m "feat(release-notes): record an unresolved frontend range in the changelog"
```

---

## Task 8: `verify-frontend` and `list-gaps`

**Files:**
- Modify: `.github/scripts/release_notes/__main__.py`
- Test: `.github/scripts/tests/test_release_notes_verify.py`

- [ ] **Step 1: Write the failing tests**

Create `.github/scripts/tests/test_release_notes_verify.py`:

```python
"""Tests for verify-frontend and list-gaps — reporting, never failing."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import __main__ as cli  # noqa: E402
from release_notes import changelog  # noqa: E402


def test_verify_frontend_warns_and_succeeds_when_unresolved(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8", "v2.1.7"])
    monkeypatch.setattr(cli.ranges, "frontend_range", lambda prev, tag: None)
    monkeypatch.setattr(cli.ranges, "frontend_sha_at", lambda ref: None)

    rc = cli.main(["verify-frontend", "--tag", "v2.1.8"])

    out = capsys.readouterr().out
    assert rc == 0                       # never blocks a release
    assert "::warning" in out
    assert "v2.1.8" in out
    assert "backfill" in out             # tells the reader how to repair it


def test_verify_frontend_is_quiet_when_resolved(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8", "v2.1.7"])
    monkeypatch.setattr(
        cli.ranges, "frontend_range",
        lambda prev, tag: cli.ranges.Range(from_sha="aaa", to_sha="bbb"),
    )

    rc = cli.main(["verify-frontend", "--tag", "v2.1.8"])

    assert rc == 0
    assert "::warning" not in capsys.readouterr().out


def test_verify_frontend_names_only_the_missing_end(monkeypatch, capsys):
    # The current tag resolves; only the previous one is missing. The warning
    # must say which, so a reader knows whether the gap is old or new.
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8", "v2.1.7"])
    monkeypatch.setattr(cli.ranges, "frontend_range", lambda prev, tag: None)
    monkeypatch.setattr(
        cli.ranges, "frontend_sha_at",
        lambda ref: "aaa" if ref == "v2.1.8" else None,
    )

    cli.main(["verify-frontend", "--tag", "v2.1.8"])

    out = capsys.readouterr().out
    assert "No frontend provenance for v2.1.7." in out


def test_list_gaps_prints_marked_versions(monkeypatch, capsys, tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [2.1.8] - 2026-09-01\n\n" + changelog.GAP_MARKER + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "CHANGELOG_PATH", path)

    rc = cli.main(["list-gaps"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "2.1.8"


def test_list_gaps_is_silent_and_succeeds_with_no_changelog(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "CHANGELOG_PATH", tmp_path / "absent.md")

    rc = cli.main(["list-gaps"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == ""
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_verify.py -v
```

Expected: FAIL — `argument command: invalid choice: 'verify-frontend'`.

- [ ] **Step 3: Implement**

Add to `.github/scripts/release_notes/__main__.py` after `cmd_check_manifest`:

```python
def cmd_verify_frontend(args: argparse.Namespace) -> int:
    """Report whether frontend provenance resolves. Never fails.

    Runs at the prerelease gate, where a human is already reviewing and can
    still act. It deliberately does not touch CHANGELOG.md: that file has no
    section for this tag yet — `render-changelog` writes the marker at publish.
    """
    previous = ranges.previous_release_tag(args.tag, tags=_all_tags(args.tag))
    if ranges.frontend_range(previous, args.tag) is not None:
        print(f"Frontend provenance resolves for {args.tag}.", file=sys.stderr)
        return 0

    missing = [
        ref for ref in (previous, args.tag)
        if ref is not None and ranges.frontend_sha_at(ref) is None
    ]
    detail = ", ".join(missing) or "an unknown end of the range"
    print(
        f"::warning title=Frontend notes unavailable::No frontend provenance for "
        f"{detail}. This release note will omit frontend changes. Repair later with: "
        f"python -m release_notes backfill --tag {args.tag} --force"
    )
    print(f"Frontend provenance missing for: {detail}", file=sys.stderr)
    return 0


def cmd_list_gaps(args: argparse.Namespace) -> int:
    """Print versions whose changelog section carries the gap marker."""
    if not CHANGELOG_PATH.exists():
        return 0
    for version in changelog.find_gaps(CHANGELOG_PATH.read_text(encoding="utf-8")):
        print(version)
    return 0
```

Register both in `build_parser`, before `return parser`:

```python
    verify = sub.add_parser(
        "verify-frontend",
        help="Report whether frontend provenance resolves for a tag. Never fails.",
    )
    verify.add_argument("--tag", required=True)
    verify.set_defaults(func=cmd_verify_frontend)

    gaps = sub.add_parser(
        "list-gaps", help="Versions whose changelog carries the frontend gap marker."
    )
    gaps.set_defaults(func=cmd_list_gaps)
```

- [ ] **Step 4: Run to verify they pass**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_verify.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/__main__.py .github/scripts/tests/test_release_notes_verify.py
git commit -m "feat(release-notes): verify-frontend reports gaps without blocking"
```

---

## Task 9: `backfill`

**Files:**
- Modify: `.github/scripts/release_notes/__main__.py`
- Test: `.github/scripts/tests/test_release_notes_backfill.py`

- [ ] **Step 1: Write the failing tests**

Create `.github/scripts/tests/test_release_notes_backfill.py`:

```python
"""Tests for the backfill span helper and the backfill command."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import __main__ as cli  # noqa: E402

ORDERED = ["v2.0.0rc221", "v2.1.1", "v2.1.2", "v2.1.3", "v2.1.4", "v2.1.5", "v2.1.6", "v2.1.7"]


def test_tag_span_is_inclusive_at_both_ends():
    assert cli._tag_span("v2.1.2", "v2.1.4", tags=ORDERED) == ["v2.1.2", "v2.1.3", "v2.1.4"]


def test_tag_span_of_a_single_tag_is_that_tag():
    assert cli._tag_span("v2.1.6", "v2.1.6", tags=ORDERED) == ["v2.1.6"]


def test_tag_span_rejects_a_reversed_range():
    with pytest.raises(SystemExit) as exc:
        cli._tag_span("v2.1.6", "v2.1.2", tags=ORDERED)
    assert "newer than" in str(exc.value)


def test_tag_span_rejects_an_unknown_tag():
    with pytest.raises(SystemExit) as exc:
        cli._tag_span("v9.9.9", "v2.1.6", tags=ORDERED)
    assert "v9.9.9" in str(exc.value)


def test_already_rendered_detects_an_existing_section():
    text = "# Changelog\n\n## [2.1.6] - 2026-07-23\n\n### Fixed\n- a\n"
    assert cli._already_rendered(text, "v2.1.6") is True
    assert cli._already_rendered(text, "v2.1.5") is False


def test_backfill_dry_run_writes_nothing(monkeypatch, capsys, tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CHANGELOG_PATH", path)
    monkeypatch.setattr(cli, "_release_tags_in_order", lambda: ORDERED)
    monkeypatch.setattr(cli, "_render_one", lambda tag, pac: f"## [{tag[1:]}] - x\n")

    rc = cli.main(["backfill", "--from", "v2.1.6", "--to", "v2.1.6", "--dry-run"])

    assert rc == 0
    assert path.read_text(encoding="utf-8") == "# Changelog\n"
    assert "2.1.6" in capsys.readouterr().out


def test_backfill_skip_existing_leaves_a_rendered_tag_alone(monkeypatch, tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n## [2.1.6] - 2026-07-23\n\n### Fixed\n- keep me\n",
                    encoding="utf-8")
    monkeypatch.setattr(cli, "CHANGELOG_PATH", path)
    monkeypatch.setattr(cli, "_release_tags_in_order", lambda: ORDERED)

    calls = []
    monkeypatch.setattr(cli, "_render_one",
                        lambda tag, pac: calls.append(tag) or "## [x] - y\n")

    cli.main(["backfill", "--from", "v2.1.6", "--to", "v2.1.6", "--skip-existing"])

    assert calls == []
    assert "keep me" in path.read_text(encoding="utf-8")


def test_backfill_force_replaces_an_existing_section(monkeypatch, tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n## [2.1.6] - 2026-07-23\n\n### Fixed\n- stale\n",
                    encoding="utf-8")
    monkeypatch.setattr(cli, "CHANGELOG_PATH", path)
    monkeypatch.setattr(cli, "_release_tags_in_order", lambda: ORDERED)
    monkeypatch.setattr(
        cli, "_render_one",
        lambda tag, pac: "## [2.1.6] - 2026-07-23\n\n### Fixed\n- fresh\n",
    )

    cli.main(["backfill", "--from", "v2.1.6", "--to", "v2.1.6", "--force"])

    text = path.read_text(encoding="utf-8")
    assert "fresh" in text
    assert "stale" not in text
    assert text.count("## [2.1.6]") == 1     # replaced, not duplicated
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_backfill.py -v
```

Expected: FAIL — `no attribute '_tag_span'`.

- [ ] **Step 3: Implement**

Add to `.github/scripts/release_notes/__main__.py` after `_all_tags`:

```python
def _release_tags_in_order() -> list[str]:
    """Every release tag in this repository, oldest first."""
    result = subprocess.run(
        ["git", "tag", "--sort=creatordate"],
        cwd=ranges.REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "Could not list tags: "
            f"{result.stderr.strip() or 'git exited ' + str(result.returncode)}"
        )
    return [t for t in result.stdout.splitlines() if ranges.is_release_tag(t)]


def _tag_span(start: str, end: str, *, tags: list[str]) -> list[str]:
    """The inclusive run of release tags from `start` to `end`."""
    for tag in (start, end):
        if tag not in tags:
            raise SystemExit(
                f"{tag!r} is not a release tag in this repository. "
                "Backfill only spans tags matching vX.Y.Z or vX.Y.ZrcN."
            )
    i, j = tags.index(start), tags.index(end)
    if i > j:
        raise SystemExit(f"{start!r} is newer than {end!r} — pass them oldest first.")
    return tags[i:j + 1]


def _already_rendered(existing: str, tag: str) -> bool:
    """True when the changelog already carries a section for `tag`."""
    return f"## [{tag.lstrip('v')}]" in existing
```

Add after `cmd_list_gaps`:

```python
def _render_one(tag: str, pac_checkout: Path | None) -> str:
    """The changelog section for `tag`, dated from the tag itself."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%as", tag],
        cwd=ranges.REPO_ROOT, capture_output=True, text=True,
    )
    date = result.stdout.strip() or "unknown"
    repo = os.environ.get("GITHUB_REPOSITORY", "ExcellenceCloudGmbH/lex-app")
    return changelog.render(
        _digest_for(tag, pac_checkout=pac_checkout), date=date, repo=repo
    )


def cmd_backfill(args: argparse.Namespace) -> int:
    """Render changelog sections for a span of tags.

    Also the repair path for a single release: `--tag T --force` re-renders one
    tag, which `changelog.prepend` replaces in place rather than duplicating.
    """
    tags = _tag_span(args.start, args.end, tags=_release_tags_in_order())
    pac = _pac_arg(args)
    existing = CHANGELOG_PATH.read_text(encoding="utf-8") if CHANGELOG_PATH.exists() else ""

    for tag in tags:
        if args.skip_existing and _already_rendered(existing, tag):
            print(f"{tag}: already rendered, skipping.", file=sys.stderr)
            continue
        section = _render_one(tag, pac)
        if args.dry_run:
            print(f"----- {tag} -----")
            sys.stdout.write(section)
            continue
        existing = changelog.prepend(existing or None, section)
        CHANGELOG_PATH.write_text(existing, encoding="utf-8")
        print(f"{tag}: written.", file=sys.stderr)

    if args.dry_run:
        print(f"Dry run — {len(tags)} tag(s) rendered, nothing written.", file=sys.stderr)
    return 0
```

Register in `build_parser`, before `return parser`:

```python
    back = sub.add_parser(
        "backfill",
        help="Render changelog sections for a span of tags, or repair one with --force.",
    )
    # Not `required`: `--tag T` is the shorthand for `--from T --to T`, and
    # `main` expands it before the command runs. cmd_backfill rejects neither
    # form being given.
    back.add_argument("--from", dest="start", default=None)
    back.add_argument("--to", dest="end", default=None)
    back.add_argument("--tag", dest="single", default=None,
                     help="Shorthand for --from T --to T.")
    back.add_argument("--pac-checkout", default=None)
    back.add_argument("--skip-existing", action="store_true",
                     help="Leave tags already present in CHANGELOG.md untouched.")
    back.add_argument("--force", action="store_true",
                     help="Re-render even when a section exists (replaces it).")
    back.add_argument("--dry-run", action="store_true",
                     help="Print sections without writing.")
    back.set_defaults(func=cmd_backfill)
```

Support the `--tag` shorthand in `main`:

```python
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    single = getattr(args, "single", None)
    if single:
        args.start = args.end = single
    return args.func(args)
```

Validate in `cmd_backfill`'s first line, before `_tag_span`:

```python
    if not args.start or not args.end:
        raise SystemExit("pass either --tag T, or both --from A and --to B.")
```

- [ ] **Step 4: Run to verify they pass**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_backfill.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/__main__.py .github/scripts/tests/test_release_notes_backfill.py
git commit -m "feat(release-notes): backfill a span of tags and repair single gaps"
```

---

## Task 10: Make the drafting-failure notice visible

A failed draft currently announces itself only in an HTML comment, which renders as nothing.

**Files:**
- Modify: `.github/scripts/release_notes/notes.py`
- Test: `.github/scripts/tests/test_release_notes_notes.py`

- [ ] **Step 1: Write the failing test**

Append to `.github/scripts/tests/test_release_notes_notes.py`:

```python
def test_fallback_body_is_visible_in_rendered_markdown():
    digest = {"tag": "v2.1.8", "previous_tag": "v2.1.7", "changes": [
        {"sha": "abc1234", "component": "backend", "type": "fix", "scope": None,
         "breaking": False, "subject": "a fix", "pr_number": 1, "internal": False},
    ]}
    body = notes.fallback(digest, reason="ValueError: boom")

    # The machine marker stays for tooling...
    assert notes.FAILURE_MARKER in body
    # ...but a human reading the rendered release must also see it.
    assert notes.FAILURE_NOTICE in body
    assert not notes.FAILURE_NOTICE.startswith("<!--")
    assert "boom" in body
```

- [ ] **Step 2: Run to verify it fails**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_notes.py -k visible -v
```

Expected: FAIL — `no attribute 'FAILURE_NOTICE'`.

- [ ] **Step 3: Implement**

In `.github/scripts/release_notes/notes.py`, below `FAILURE_MARKER`:

```python
# The marker above is an HTML comment, so it renders as nothing. A release
# whose note failed to draft must say so where a human will actually see it.
FAILURE_NOTICE = (
    "> ⚠️ **Automatic release-note drafting failed — this body needs a human rewrite.**"
)
```

In `fallback`, replace the opening lines:

```python
    lines = [
        FAILURE_MARKER,
        "",
        FAILURE_NOTICE,
        "",
        f"Automatic release-note drafting failed ({reason}).",
```

- [ ] **Step 4: Run to verify it passes**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_notes.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/release_notes/notes.py .github/scripts/tests/test_release_notes_notes.py
git commit -m "fix(release-notes): make a failed draft visible in the release body"
```

---

## Task 11: Golden-file test for the technical tier

Structural regressions in the changelog must fail CI without calling a model.

**Files:**
- Create: `.github/scripts/tests/fixtures/digest_v2.1.8.json`
- Create: `.github/scripts/tests/fixtures/changelog_v2.1.8.md`
- Test: `.github/scripts/tests/test_release_notes_changelog.py`

- [ ] **Step 1: Write the fixture digest**

Create `.github/scripts/tests/fixtures/digest_v2.1.8.json`:

```json
{
  "tag": "v2.1.8",
  "previous_tag": "v2.1.7",
  "frontend_recorded": true,
  "changes": [
    {"sha": "aaa1111", "component": "backend", "type": "feat", "scope": "calc",
     "breaking": false, "subject": "schedule activations without a timer",
     "pr_number": 695, "internal": false},
    {"sha": "bbb2222", "component": "frontend", "type": "fix", "scope": "grid",
     "breaking": false, "subject": "grouping by a foreign key shows the name",
     "pr_number": 441, "internal": false},
    {"sha": "ccc3333", "component": "backend", "type": "fix", "scope": "as_of",
     "breaking": true, "subject": "reject a naive anchor with 400",
     "pr_number": 725, "internal": false},
    {"sha": "ddd4444", "component": "backend", "type": "ci", "scope": "gate",
     "breaking": false, "subject": "unbreak the prerelease gate",
     "pr_number": 722, "internal": true}
  ]
}
```

- [ ] **Step 2: Write the failing test**

Append to `.github/scripts/tests/test_release_notes_changelog.py`:

```python
import json

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_changelog_rendering_matches_the_golden_file():
    digest = json.loads((FIXTURES / "digest_v2.1.8.json").read_text(encoding="utf-8"))
    expected = (FIXTURES / "changelog_v2.1.8.md").read_text(encoding="utf-8")

    got = changelog.render(digest, date="2026-09-01", repo="ExcellenceCloudGmbH/lex-app")

    assert got == expected


def test_golden_file_excludes_internal_and_keeps_frontend_attribution():
    expected = (FIXTURES / "changelog_v2.1.8.md").read_text(encoding="utf-8")
    assert "**frontend**" in expected      # provenance is the point in this tier
    assert "unbreak the prerelease gate" not in expected   # ci type is excluded
    assert "### Breaking" in expected
```

Ensure `from pathlib import Path` is present at the top of the file.

- [ ] **Step 3: Run to verify it fails**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_changelog.py -k golden -v
```

Expected: FAIL — the golden file does not exist.

- [ ] **Step 4: Generate the golden file, then read it before trusting it**

```bash
cd /home/syscall/Documents/lex
python3 - <<'EOF'
import json, sys
sys.path.insert(0, ".github/scripts")
from pathlib import Path
from release_notes import changelog
d = json.loads(Path(".github/scripts/tests/fixtures/digest_v2.1.8.json").read_text())
out = changelog.render(d, date="2026-09-01", repo="ExcellenceCloudGmbH/lex-app")
Path(".github/scripts/tests/fixtures/changelog_v2.1.8.md").write_text(out)
print(out)
EOF
```

**Read the printed output.** A golden file records whatever the code does, including a bug. Confirm: `## [2.1.8] - 2026-09-01` heading; a `### Breaking` section containing the `as_of` entry; `### Added` with the backend `feat`; `### Fixed` with the **frontend** grid entry; the `ci` entry absent; every line carrying a commit link. If any of that is wrong, fix `changelog.py` — not the fixture.

- [ ] **Step 5: Run to verify it passes**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_changelog.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add .github/scripts/tests/fixtures/ .github/scripts/tests/test_release_notes_changelog.py
git commit -m "test(release-notes): pin the changelog rendering with a golden file"
```

---

## Task 12: Wire the lex-app workflows

**Files:**
- Modify: `.github/workflows/prerelease_gate.yml`
- Modify: `.github/workflows/publish_release_notes.yml`

- [ ] **Step 1: Add a PAC checkout to the drafting job**

In `.github/workflows/prerelease_gate.yml`, in the `draft-notes` job, before the drafting step:

```yaml
      - name: Checkout the frontend repository
        uses: actions/checkout@v4
        with:
          repository: ExcellenceCloudGmbH/process-admin-general-client
          token: ${{ secrets.LEX_PACKAGES_TOKEN }}
          path: pac
          fetch-depth: 0          # release-notes needs full history for a range
```

First check the secret exists, since PAC is a private repo and the default `GITHUB_TOKEN` cannot read it:

```bash
gh secret list --repo ExcellenceCloudGmbH/lex-app | grep -E 'LEX_PACKAGES_TOKEN|DOCS_APP'
```

If `LEX_PACKAGES_TOKEN` is absent, generate a `lex-docs-bot` App token in this job instead and use its output — but note the App is **not currently installed on PAC**, so that route needs the installation extended first (spec Open Question 1).

`fetch-depth: 0` is not optional — a shallow clone cannot answer `git log from..to`.

- [ ] **Step 2: Run `verify-frontend` before drafting**

Immediately before the `draft-notes` run step:

```yaml
      - name: Verify frontend provenance
        env:
          TAG: ${{ github.event.release.tag_name }}
        run: |
          set -euo pipefail
          PYTHONPATH=.github/scripts python -m release_notes verify-frontend --tag "$TAG"
```

It exits 0 by design. A missing provenance surfaces as a `::warning::` in the run summary while a human is still reviewing the prerelease.

- [ ] **Step 3: Pass `--pac-checkout` when drafting**

Change the existing command (around line 231) from:

```
PYTHONPATH=.github/scripts python -m release_notes draft-notes --tag "$TAG" > drafted.md
```

to:

```
PYTHONPATH=.github/scripts python -m release_notes draft-notes \
  --tag "$TAG" --pac-checkout ./pac > drafted.md
```

- [ ] **Step 4: Do the same in the publish workflow**

In `.github/workflows/publish_release_notes.yml`, add the same PAC checkout step, then change the command (around line 51) to:

```
PYTHONPATH=.github/scripts python -m release_notes render-changelog \
  --tag "$TAG" --date "$DATE" --pac-checkout ./pac
```

- [ ] **Step 5: Validate the YAML**

```bash
cd /home/syscall/Documents/lex
python3 -c "
import yaml
for f in ['.github/workflows/prerelease_gate.yml',
          '.github/workflows/publish_release_notes.yml',
          '.github/workflows/frontend_manifest_guard.yml']:
    yaml.safe_load(open(f))
    print('ok', f)
"
```

Expected: `ok` for all three.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/prerelease_gate.yml .github/workflows/publish_release_notes.yml
git commit -m "ci(release-notes): supply PAC history and verify frontend provenance"
```

---

## Task 13: PAC — dispatch trigger and the provenance write

This is the change that makes provenance correct by construction.

**Files:**
- Modify: `/home/syscall/LUND_IT/process-admin-general-client/.github/workflows/push-build-to-pip-package.yml`

- [ ] **Step 1: Branch in PAC**

```bash
cd /home/syscall/LUND_IT/process-admin-general-client
git checkout -b ci/frontend-provenance origin/lex-app-v2-pac-latest
```

- [ ] **Step 2: Replace the trigger**

Replace the `on:` block at the top of the file:

```yaml
on:
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Build and print the provenance manifest without opening a PR"
        type: boolean
        required: false
        default: false
```

Shipping the frontend is a deliberate act. `lint.yml` already runs Code Quality & Tests on push to every branch, so removing the push trigger here costs no test coverage.

- [ ] **Step 3: Modernise the two checkouts and the target repo**

In `build-and-deploy`, change `uses: actions/checkout@v2` to `@v4` (both occurrences), and change the lex-app checkout:

```yaml
      - name: Checkout lex-app repository
        uses: actions/checkout@v4
        with:
          repository: ExcellenceCloudGmbH/lex-app
          token: ${{ secrets.PAT }}
          path: 'lex-app'
```

`LundIT/lex-app` currently works only because GitHub redirects the old organisation name. Do not depend on a redirect.

- [ ] **Step 4: Add the provenance write**

Directly after the "Copy build to lex-app repository" step:

```yaml
      - name: Record frontend provenance
        run: |
          set -euo pipefail
          MANIFEST=lex-app/lex/react/build/.frontend-version.json
          printf '{"repo":"%s","branch":"%s","sha":"%s","built_at":"%s"}\n' \
            "$GITHUB_REPOSITORY" "$GITHUB_REF_NAME" "$GITHUB_SHA" \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$MANIFEST"
          echo "Wrote $MANIFEST:"
          cat "$MANIFEST"
```

This job is the only place in either repository that knows which PAC commit produced this bundle. lex-app receives content-hashed Vite output and cannot recover it. The path and key names match `ranges.MANIFEST_PATH` and the existing manifest test.

- [ ] **Step 5: Make the PR step per-dispatch and skippable**

Replace the "Create Pull Request" step:

```yaml
      - name: Create Pull Request
        if: ${{ inputs.dry_run != true }}
        uses: peter-evans/create-pull-request@v6
        with:
          token: ${{ secrets.PAT }}
          path: './lex-app'
          commit-message: "build(frontend): update bundle to ${{ github.sha }}"
          branch: "PAC-build-update-${{ github.sha }}"
          title: "Update PAC build files"
          body: |
            Automated frontend bundle update.

            - Source: `${{ github.repository }}@${{ github.sha }}` (`${{ github.ref_name }}`)
            - Updated `lex/react/build/` and `.frontend-version.json`.
```

A branch per dispatch means each shipped bundle gets its own reviewable PR, rather than one PR whose contents change underneath a reviewer.

The commit message keeps the `build(frontend): update bundle` prefix — `digest.is_noise` filters exactly that prefix, so bundle commits stay out of the changelog while their *contents* are described by the PAC range.

- [ ] **Step 6: Validate and push**

```bash
cd /home/syscall/LUND_IT/process-admin-general-client
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/push-build-to-pip-package.yml')); print('yaml ok')"
git add .github/workflows/push-build-to-pip-package.yml
git commit -m "ci: record frontend provenance and ship bundles on dispatch"
git push -u origin ci/frontend-provenance
```

- [ ] **Step 7: Verify with a dry run**

Open a PR for `ci/frontend-provenance`, merge it, then:

```bash
gh workflow run push-build-to-pip-package.yml \
  --repo ExcellenceCloudGmbH/process-admin-general-client \
  --ref lex-app-v2-pac-latest -f dry_run=true
gh run watch --repo ExcellenceCloudGmbH/process-admin-general-client
```

Expected: the "Record frontend provenance" step prints a manifest whose `sha` is 40 lowercase hex and equals the dispatched commit; no PR is created.

---

## Task 14: Backfill the seven releases and confirm acceptance

**Files:** `CHANGELOG.md`, `docs/releases/` (output only)

- [ ] **Step 1: Rehearse one release without writing**

```bash
cd /home/syscall/Documents/lex
export ANTHROPIC_API_KEY=<key>
export LEX_NOTES_MODEL=claude-opus-5
PYTHONPATH=.github/scripts python -m release_notes backfill \
  --tag v2.1.6 --dry-run \
  --pac-checkout /home/syscall/LUND_IT/process-admin-general-client
```

Expected: a `## [2.1.6]` section including `**frontend**` entries, and stderr reporting a frontend commit count rather than "omitting the frontend section".

- [ ] **Step 2: Read one drafted business note and judge its quality**

```bash
PYTHONPATH=.github/scripts python -m release_notes draft-notes \
  --tag v2.1.6 \
  --pac-checkout /home/syscall/LUND_IT/process-admin-general-client
```

Check against the spec's quality bar: no repository or component names, no class names or PR numbers, frontend and backend woven together, ends with `**Upgrade note:**`. If the prose is thin, confirm Task 1 Step 1 — thin output usually means the model is drafting from subject lines because #702 is not merged.

- [ ] **Step 3: Backfill all seven**

```bash
PYTHONPATH=.github/scripts python -m release_notes backfill \
  --from v2.1.1 --to v2.1.7 --skip-existing \
  --pac-checkout /home/syscall/LUND_IT/process-admin-general-client
```

- [ ] **Step 4: Confirm no gaps remain**

```bash
PYTHONPATH=.github/scripts python -m release_notes list-gaps
```

Expected: no output.

- [ ] **Step 5: Run the full suite**

```bash
python3 -m pytest .github/scripts/tests/ -q
```

Expected: green, and at least the count recorded in Task 1 Step 3.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(release-notes): backfill v2.1.1 through v2.1.7"
```

- [ ] **Step 7: Check acceptance criteria**

| # | Criterion | How |
|---|---|---|
| 1 | `list-gaps` empty for v2.1.1–v2.1.7 | Step 4 |
| 2 | All 3 side-car entries record `method`, and any `date-window` is deliberate | read `frontend-history.json` |
| 3 | A `dry_run` PAC dispatch prints a valid manifest | Task 13 Step 7 |
| 4 | A real dispatch produces a bundle PR the guard passes | dispatch with `dry_run=false` |
| 5 | The next prerelease shows a frontend section in the body **and** `**frontend**` entries in `CHANGELOG.md` | next release |
| 6 | `scripts_tests.yml` green | push the branch |

- [ ] **Step 8: Open the PR**

```bash
cd /home/syscall/Documents/lex
git push -u origin docs/frontend-provenance-spec
gh pr create --base lex-app-v2 \
  --title "feat(release-notes): frontend provenance, backfill and gap recovery" \
  --body "Implements docs/superpowers/specs/2026-09-01-release-notes-frontend-provenance-design.md

Frontend notes now exist and are correct at lex-app release time.

- PAC's workflow writes .frontend-version.json from \$GITHUB_SHA — correct by construction
- The manifest guard rejects a schema-invalid manifest
- verify-frontend warns at the gate; render-changelog records a recoverable marker
- backfill fills history and repairs gaps; list-gaps is the work queue
- Three pre-manifest bundles attributed by Vite content-hash proof

Criteria 1-3 and 6 verified. 4 and 5 verify on the next dispatch and release."
```

---

## Task 15: Append a frontend addendum to an already-published release

The spec's correction flow has two halves with different rules. `backfill --force` covers
`CHANGELOG.md` (mechanical, replaceable). This covers the release body, which must be **appended to,
never rewritten** — it contains human-approved prose.

**Files:**
- Modify: `.github/scripts/release_notes/notes.py`
- Modify: `.github/scripts/release_notes/__main__.py`
- Test: `.github/scripts/tests/test_release_notes_notes.py`

- [ ] **Step 1: Write the failing tests**

Append to `.github/scripts/tests/test_release_notes_notes.py`:

```python
def test_append_addendum_preserves_the_original_body():
    body = "## Main changes\n\n- **New sidebar.** More room for your data.\n"
    out = notes.append_addendum(body, "### Frontend changes\n\n- a fix\n")

    assert body.rstrip() in out          # human prose survives verbatim
    assert "a fix" in out
    assert notes.ADDENDUM_MARKER in out


def test_append_addendum_is_idempotent():
    body = "## Main changes\n\n- something\n"
    once = notes.append_addendum(body, "### Frontend changes\n\n- a fix\n")
    twice = notes.append_addendum(once, "### Frontend changes\n\n- a fix\n")

    assert once == twice
    assert twice.count(notes.ADDENDUM_MARKER) == 1


def test_append_addendum_never_drops_content_on_a_second_different_call():
    body = "## Main changes\n\n- something\n"
    once = notes.append_addendum(body, "### Frontend changes\n\n- first\n")
    # A later call with different text must not silently replace the first.
    twice = notes.append_addendum(once, "### Frontend changes\n\n- second\n")

    assert "first" in twice
    assert "second" not in twice          # refuses rather than overwrites
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_notes.py -k addendum -v
```

Expected: FAIL — `no attribute 'append_addendum'`.

- [ ] **Step 3: Implement in `notes.py`**

Add below `FAILURE_NOTICE`:

```python
# Marks a frontend addendum added after publication. Its presence makes the
# append idempotent, which matters because the alternative — rewriting the body
# — would destroy prose a human reviewed and edited.
ADDENDUM_MARKER = "<!-- lex:frontend-addendum -->"


def append_addendum(body: str, addendum: str, *, marker: str = ADDENDUM_MARKER) -> str:
    """Append `addendum` to a published release body, exactly once.

    Returns `body` unchanged when an addendum is already present. Refusing is
    deliberate: a second, different addendum means someone is trying to correct
    a correction, and doing that automatically would silently discard the first.
    """
    if marker in body:
        return body
    return f"{body.rstrip()}\n\n{marker}\n\n{addendum.strip()}\n"
```

- [ ] **Step 4: Add the subcommand**

In `__main__.py`, after `cmd_backfill`:

```python
def cmd_append_frontend_note(args: argparse.Namespace) -> int:
    """Add a frontend addendum to a published release body.

    The addendum is mechanical rather than model-drafted: it lists the frontend
    subjects from the digest. Re-drafting prose here would clash in tone with a
    body a human has already edited, and cost a model call to say what the
    digest already says plainly.
    """
    built = _digest_for(args.tag, pac_checkout=_pac_arg(args))
    frontend = [c for c in built["changes"] if c["component"] == "frontend"]
    if not frontend:
        print(f"{args.tag}: no frontend changes to append.", file=sys.stderr)
        return 0

    addendum = "\n".join(
        ["### Frontend changes (added after publication)", ""]
        + [f"- {c['subject']}" for c in frontend]
    )

    read = subprocess.run(
        ["gh", "release", "view", args.tag, "--json", "body", "-q", ".body"],
        cwd=ranges.REPO_ROOT, capture_output=True, text=True,
    )
    if read.returncode != 0:
        print(f"Could not read release {args.tag}: {read.stderr.strip()}", file=sys.stderr)
        return 1

    updated = notes.append_addendum(read.stdout, addendum)
    if updated == read.stdout:
        print(f"{args.tag}: an addendum is already present, leaving it alone.", file=sys.stderr)
        return 0

    if args.dry_run:
        sys.stdout.write(updated)
        return 0

    write = subprocess.run(
        ["gh", "release", "edit", args.tag, "--notes-file", "-"],
        cwd=ranges.REPO_ROOT, input=updated, capture_output=True, text=True,
    )
    if write.returncode != 0:
        print(f"Could not update release {args.tag}: {write.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"{args.tag}: frontend addendum appended.", file=sys.stderr)
    return 0
```

Register it in `build_parser`, before `return parser`:

```python
    add = sub.add_parser(
        "append-frontend-note",
        help="Append a frontend addendum to a published release body. Never rewrites it.",
    )
    add.add_argument("--tag", required=True)
    add.add_argument("--pac-checkout", default=None)
    add.add_argument("--dry-run", action="store_true")
    add.set_defaults(func=cmd_append_frontend_note)
```

- [ ] **Step 5: Run to verify they pass**

```bash
python3 -m pytest .github/scripts/tests/test_release_notes_notes.py -v
```

Expected: all pass.

- [ ] **Step 6: Verify against a real release without writing**

```bash
PYTHONPATH=.github/scripts python -m release_notes append-frontend-note \
  --tag v2.1.6 --dry-run \
  --pac-checkout /home/syscall/LUND_IT/process-admin-general-client
```

Expected: the existing v2.1.6 body printed unchanged, followed by the marker and a frontend list. Confirm the original text is byte-identical above the marker before ever running without `--dry-run`.

- [ ] **Step 7: Commit**

```bash
git add .github/scripts/release_notes/notes.py .github/scripts/release_notes/__main__.py \
        .github/scripts/tests/test_release_notes_notes.py
git commit -m "feat(release-notes): append a frontend addendum without rewriting a body"
```

---

## Notes for the implementer

**Two repositories.** Tasks 1–12 and 14 are in `/home/syscall/Documents/lex` on `docs/frontend-provenance-spec`. Task 13 is in `/home/syscall/LUND_IT/process-admin-general-client` on `ci/frontend-provenance`. Neither repo accepts direct pushes to its default branch.

**Do not `git add -A`** in the lex-app repo. It has substantial unrelated uncommitted state — deleted `lex/react/build/assets/*` files and many untracked files. Add only the paths each step names.

**Task 13 Step 4 writes into `lex-app/lex/react/build/`** — the checkout path inside the PAC workflow, not a local directory. Do not create that file by hand in lex-app; G1's whole value is that no human writes a SHA.

**Three open questions from the spec** are unresolved and safe to defer:

1. `lex-docs-bot` is not installed on PAC, so Task 13 keeps `secrets.PAT`. Extending the App is the better end state.
2. The dispatch branch is `lex-app-v2-pac-latest`, but the new UI lives on `feat/frontend-test-plan`, which PR #442 merged and **PR #450 reverted**. Soft dependency on LEX-594.
3. Correction is split across two tasks by design: `backfill --force` (Task 9) repairs `CHANGELOG.md` by replacement, and `append-frontend-note` (Task 15) appends to a published release body without rewriting it. Task 15 refuses a second, different addendum rather than overwriting the first — if that turns out to be too strict in practice, it is a one-line change, but the default protects human-edited prose.
