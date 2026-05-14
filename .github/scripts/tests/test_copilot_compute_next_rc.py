"""Tests for copilot_compute_next_rc.compute_next_rc."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from copilot_compute_next_rc import compute_next_rc  # noqa: E402


def test_basic_rc_bump() -> None:
    assert compute_next_rc("v2.0.0rc124") == "v2.0.0rc125"


def test_two_digit_rc_bump() -> None:
    assert compute_next_rc("v2.0.0rc99") == "v2.0.0rc100"


def test_three_digit_minor_with_rc() -> None:
    assert compute_next_rc("v10.15.7rc3") == "v10.15.7rc4"


def test_zero_padded_rc_is_normalised() -> None:
    # If a human ever pushed rc007 manually, we still bump cleanly.
    assert compute_next_rc("v2.0.0rc007") == "v2.0.0rc8"


def test_non_rc_tag_rejected() -> None:
    with pytest.raises(ValueError, match=r"(?i)rc"):
        compute_next_rc("v2.0.0")


def test_completely_wrong_shape_rejected() -> None:
    with pytest.raises(ValueError):
        compute_next_rc("release-2.0.0")


def test_empty_string_rejected() -> None:
    with pytest.raises(ValueError):
        compute_next_rc("")


def test_pre_release_other_than_rc_rejected() -> None:
    # The publish path is explicitly rc-only — anything else is a human concern.
    with pytest.raises(ValueError, match=r"(?i)rc"):
        compute_next_rc("v2.0.0a1")
    with pytest.raises(ValueError, match=r"(?i)rc"):
        compute_next_rc("v2.0.0b5")


def test_uppercase_rc_rejected() -> None:
    # Tag space discipline: only lowercase `rc` is allowed. Forbid
    # silent normalisation — uppercase usually means a typo upstream
    # (manual `git tag`), and silently accepting it would hide that.
    with pytest.raises(ValueError):
        compute_next_rc("v2.0.0RC1")


def test_whitespace_input_rejected() -> None:
    # Reject leading/trailing whitespace + newlines outright. A workflow
    # capturing the previous tag from `gh release view` may include a
    # stray newline; silently `.strip()`-ing it would mask that bug. The
    # caller must hand us a clean tag.
    with pytest.raises(ValueError):
        compute_next_rc(" v2.0.0rc1\n")
