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
