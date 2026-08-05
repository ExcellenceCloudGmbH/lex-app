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
