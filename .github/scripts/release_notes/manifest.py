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
    if not SHA_RE.fullmatch(sha):
        return f"manifest 'sha' is not a 40-character lowercase hex commit: {sha!r}"
    return None
