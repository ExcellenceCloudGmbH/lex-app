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
