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


def frontend_sha_at(ref: str, *, show: Callable[[str, str], str | None] = git_show) -> str | None:
    """The PAC SHA recorded in the manifest as of `ref`, or None."""
    blob = show(ref, MANIFEST_PATH)
    if blob is None:
        return None
    try:
        sha = json.loads(blob)["sha"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    # A blank or null SHA is exactly as untruthful as a missing one, and the
    # manifest is hand-written today, so a typo can produce it. Collapse both
    # to None rather than letting `Range(to_sha="")` reach a renderer.
    return sha or None


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
