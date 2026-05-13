"""Tests for copilot_validate_pr_shape — per-mode PR-shape gating."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from copilot_validate_pr_shape import (  # noqa: E402
    PRFile,
    ValidationResult,
    validate_pr_shape,
)


def _files(*pairs: tuple[str, int]) -> list[PRFile]:
    """Build PRFile list from (path, additions) tuples. Deletions default to 0."""
    return [PRFile(path=p, additions=a, deletions=0) for p, a in pairs]


# ----- happy paths -------------------------------------------------

def test_regression_happy_path_ok() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_7/test_7m_thing.py", 80),
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    pr_body = "Closes the gap on scenario 7.NN.\n\nFixes #42"
    result = validate_pr_shape(mode="regression", files=files, pr_body=pr_body)
    assert result.ok, result.errors


def test_bug_repro_happy_path_ok() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
    )
    pr_body = "Reproduces BUG-099.\n\nFixes #43"
    result = validate_pr_shape(mode="bug-repro", files=files, pr_body=pr_body)
    assert result.ok, result.errors


def test_fix_and_test_happy_path_ok() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 12),  # source fix, under 50 lines
    )
    pr_body = (
        "Fixes BUG-100.\n\n"
        "### Source changes\n- `lex/core/services/foo.py`: short rationale.\n\n"
        "Fixes #44"
    )
    result = validate_pr_shape(mode="fix-and-test", files=files, pr_body=pr_body)
    assert result.ok, result.errors


# ----- shape failures ---------------------------------------------

def test_missing_test_file_fails() -> None:
    files = _files(
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    result = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("no new test file" in e.lower() for e in result.errors)


def test_missing_session_log_append_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_7/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 3),
    )
    result = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("session-log.md" in e for e in result.errors)


def test_missing_known_bugs_row_fails_bug_repro() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    result = validate_pr_shape(mode="bug-repro", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("known-bugs.md" in e for e in result.errors)


def test_test_filename_regex_violation_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_7/test_thing_no_scenario_id.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    result = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("test_<Nx>_" in e or "naming" in e.lower() for e in result.errors)


def test_files_outside_allowed_set_fail_for_test_only_modes() -> None:
    """A regression-mode PR may not touch source."""
    files = _files(
        ("lex/test_project/tests/cluster_7/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/core/services/foo.py", 5),
    )
    result = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("lex/core/services/foo.py" in e for e in result.errors)


def test_fix_and_test_source_diff_above_50_lines_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 30),
        ("lex/core/services/bar.py", 25),
    )
    pr_body = (
        "### Source changes\n"
        "- `lex/core/services/foo.py`: a\n"
        "- `lex/core/services/bar.py`: b\n"
        "\nFixes #99"
    )
    result = validate_pr_shape(mode="fix-and-test", files=files, pr_body=pr_body)
    assert not result.ok
    assert any("source diff" in e.lower() for e in result.errors)


def test_fix_and_test_source_file_not_listed_in_body_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 12),
    )
    pr_body = "no source changes section\n\nFixes #1"
    result = validate_pr_shape(mode="fix-and-test", files=files, pr_body=pr_body)
    assert not result.ok
    assert any("source changes" in e.lower() for e in result.errors)


def test_missing_fixes_link_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_7/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    result = validate_pr_shape(
        mode="regression", files=files, pr_body="No issue link here."
    )
    assert not result.ok
    assert any("Fixes #" in e for e in result.errors)


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        validate_pr_shape(mode="banana", files=[], pr_body="")
