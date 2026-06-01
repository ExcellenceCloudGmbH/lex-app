"""Tests for stage_release_draft — the create/re-target/error decision.

The network-touching parts (find_release, create_draft, retarget) all shell
out to ``gh``; these tests pin the pure ``decide_action`` helper that decides
what to do with the existing release for a version:

  * nothing staged yet      -> create a fresh DRAFT
  * a draft already exists   -> re-point it at the newly validated commit
  * the version already shipped (published, draft=false) -> hard error

The error case is the important guard: it stops us from silently mutating an
already-published release, which would be a no-op on a tagged commit and could
mask a mistaken attempt to "re-stage" a version that is already out.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from stage_release_draft import CREATE, ERROR, RETARGET, decide_action  # noqa: E402


def test_no_existing_release_creates_draft():
    action, _ = decide_action(None)
    assert action == CREATE


def test_existing_draft_is_retargeted():
    action, _ = decide_action({"id": 42, "draft": True, "tag_name": "v2.0.0rc185"})
    assert action == RETARGET


def test_published_release_is_an_error():
    action, msg = decide_action({"id": 42, "draft": False, "tag_name": "v2.0.0rc185"})
    assert action == ERROR
    assert "already published" in msg


def test_missing_draft_key_treated_as_published():
    # A release dict without an explicit ``draft`` key is, by GitHub's model,
    # a published release — must not be silently re-targeted.
    action, _ = decide_action({"id": 42, "tag_name": "v2.0.0rc185"})
    assert action == ERROR
