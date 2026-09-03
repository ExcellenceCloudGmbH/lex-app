#!/usr/bin/env python3
"""Resolve the commit ranges a release covers.

Backend range: previous release tag -> current tag.
Frontend range: the PAC SHA recorded in the build manifest at each of those
two tags. Absent at either end means there is no truthful frontend range, and
the caller omits the frontend section rather than guessing.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


# The directory whose last-changing commit identifies a vendored bundle. Used
# as the key into the historical provenance map, because pre-manifest tags
# carry no manifest and one cannot be added to a tag that already exists.
BUNDLE_PATH = "lex/react/build"


def _run_rev_list(ref: str) -> str:
    """Raw `git rev-list` output for BUNDLE_PATH as of `ref`.

    Returns stdout unmodified — normalising is the caller's job, matching
    `git_show`. Returns "" when git exits non-zero, and reports why: a bad ref
    exits 128 with a message that names the problem, and discarding it turns a
    misconfiguration into a silent empty result.

    A shallow clone is NOT caught here: it exits 0 and returns the clone's
    boundary commit, because a grafted parentless commit appears to add every
    file it contains. `bundle_commit_at` warns about that separately.
    """
    result = subprocess.run(
        ["git", "rev-list", "-1", ref, "--", BUNDLE_PATH],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        print(f"Could not resolve the bundle commit at {ref!r}: {detail}",
              file=sys.stderr)
        return ""
    return result.stdout


def _is_shallow() -> bool:
    """True when this clone lacks full history.

    `git rev-list -1 <ref> -- <path>` reports the clone's own starting commit
    as having introduced every file, so in a shallow clone the bundle commit
    resolves to HEAD rather than to the commit that actually changed it.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "true"


def bundle_commit_at(
    ref: str,
    *,
    run: Callable[[str], str | None] = _run_rev_list,
    shallow: Callable[[], bool] = _is_shallow,
) -> str | None:
    """The lex-app commit that last changed the vendored bundle as of `ref`.

    The **full 40-character** SHA. The historical-provenance lookup matches a
    possibly-abbreviated table key against this value, so an abbreviated
    result here would silently miss.

    None when the ref predates the bundle or git cannot answer. `run` and
    `shallow` are injectable so tests need no repository history.
    """
    sha = run(ref)
    if sha is None:
        return None
    sha = sha.strip() or None
    if sha and shallow():
        print(
            f"Warning: shallow clone — the bundle commit resolved at {ref!r} "
            f"({sha[:8]}) is this clone's starting commit, not the commit that "
            "changed the bundle. Re-run with fetch-depth: 0 for a true answer.",
            file=sys.stderr,
        )
    return sha


# Provenance for tags that predate the manifest. Committed, append-only, and
# frozen once the backfill lands. Each entry records how it was established so
# an inference is never stored looking like a proof.
HISTORY_PATH = Path(__file__).resolve().parent / "frontend-history.json"

# git's minimum unambiguous abbreviation. The table is hand-authored, so a
# shorter key — or an empty one — would answer for refs it knows nothing
# about, since `key.startswith("")` is always true.
MIN_HISTORY_KEY = 7

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def load_history(*, path: Path | None = None) -> dict:
    """The bundle-commit -> {pac_sha, method} map. Empty on any problem.

    A corrupt or missing lookup table must never break drafting: the caller
    treats an empty map exactly as it treats an absent manifest, which omits
    the frontend section rather than guessing.

    `path` defaults to the module-level `HISTORY_PATH` looked up here, at call
    time, rather than being bound as the parameter's default value — matching
    how `_run_rev_list`/`_is_shallow` read `REPO_ROOT`. A default of
    `path: Path = HISTORY_PATH` would freeze today's `HISTORY_PATH` into this
    function's signature at import time, so `monkeypatch.setattr(ranges,
    "HISTORY_PATH", ...)` would silently do nothing.
    """
    target = HISTORY_PATH if path is None else path
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _history_entry(table: dict, key: str) -> dict | None:
    """The entry for `key` — exact match first, then longest usable prefix.

    Longest wins so a specific entry cannot be shadowed by a vaguer one
    whichever order the JSON happens to be written in.
    """
    entry = table.get(key)
    if entry is None:
        candidates = [
            (k, v) for k, v in table.items()
            if isinstance(k, str) and len(k) >= MIN_HISTORY_KEY and key.startswith(k)
        ]
        if candidates:
            entry = max(candidates, key=lambda kv: len(kv[0]))[1]
    return entry if isinstance(entry, dict) else None


def frontend_sha_at(
    ref: str,
    *,
    show: Callable[[str, str], str | None] = git_show,
    history: Callable[[], dict] = load_history,
    bundle: Callable[[str], str | None] = bundle_commit_at,
) -> str | None:
    """The PAC SHA that produced the bundle at `ref`, or None.

    In-tree manifest first — it is written by the job that built the bundle
    and is therefore correct by construction. The committed side-car answers
    for tags that predate the manifest, which no in-tree file can ever do: a
    file cannot be added to a tag that already exists.
    """
    blob = show(ref, MANIFEST_PATH)
    if blob is not None:
        try:
            sha = json.loads(blob)["sha"]
        except (json.JSONDecodeError, KeyError, TypeError):
            sha = None
        # A blank or null sha is exactly as untruthful as a missing one, and
        # the manifest was hand-written before provenance was automated.
        if sha:
            return sha

    table = history()
    if not table:
        return None
    key = bundle(ref)
    # bundle_commit_at promises a full 40-character sha. Anything else is
    # contamination — multi-line output would still satisfy startswith() and
    # return a wrong pac_sha, which is the one outcome this module forbids.
    if not key or not _FULL_SHA_RE.match(key):
        return None
    entry = _history_entry(table, key)
    if entry is None:
        return None
    return entry.get("pac_sha") or None


def frontend_range(
    previous_tag: str | None,
    current_tag: str,
    *,
    show: Callable[[str, str], str | None] = git_show,
    history: Callable[[], dict] = load_history,
    bundle: Callable[[str], str | None] = bundle_commit_at,
) -> Range | None:
    """The frontend commit range, or None when it cannot be established.

    Returning None is a deliberate outcome, not an error: without a recorded
    SHA at both ends there is no truthful frontend range, and inventing one
    would produce notes about changes that may not be in the shipped bundle.
    """
    # The pin wins, and only when BOTH ends have one. Two pins are two exact
    # versions, so the range is known before anything is built — no manifest,
    # no side-car, and no way for it to fail after a release has already
    # shipped, which is what retires the gap machinery for new releases.
    #
    # One pin is deliberately not enough: the release that INTRODUCES the pin
    # has none at its previous tag, and inventing a starting point would
    # attribute every frontend commit in PAC's history to that one release.
    previous_version = (
        frontend_version_at(previous_tag, show=show) if previous_tag else None
    )
    current_version = frontend_version_at(current_tag, show=show)
    if previous_version and current_version:
        # These carry PAC TAGS rather than SHAs. The field names under-describe
        # that, but renaming them touches every historical call site for no
        # behavioural gain — `git log` treats a tag and a sha alike.
        return Range(
            from_sha=pac_tag_for(previous_version),
            to_sha=pac_tag_for(current_version),
        )

    to_sha = frontend_sha_at(current_tag, show=show, history=history, bundle=bundle)
    if to_sha is None:
        return None
    if previous_tag is None:
        return Range(from_sha=None, to_sha=to_sha)
    from_sha = frontend_sha_at(previous_tag, show=show, history=history, bundle=bundle)
    if from_sha is None:
        return None
    return Range(from_sha=from_sha, to_sha=to_sha)


# ── The pin, once the frontend is a published dependency ─────────────
#
# The pin IS the provenance record, so only an exact version counts. A range
# (">=1.10.0") or a wildcard ("1.10.*") resolves to different revisions at
# different times, which is not provenance — it is the same ambiguity this
# design removes, wearing a version number.
PIN_RE = re.compile(r"^lex-frontend==(?P<version>\d+\.\d+\.\d+[A-Za-z0-9.]*)$")

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
