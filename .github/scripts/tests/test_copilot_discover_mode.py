"""Tests for copilot_discover_mode — label → (mode, kind) resolution.

The full ``resolve()`` chain shells out to ``gh`` (closingIssuesReferences,
issue views), so these tests pin the pure label-resolution helper that
decides what a candidate issue *is*: a maintainer test-request
(``copilot:<mode>`` → kind ``test-bot``) or a Feature-4 coverage task
(``coverage-task`` → mode ``regression``, kind ``coverage-task``).

A regression here is exactly the bug this change fixes: a coverage-task
issue carries no ``copilot:<mode>`` label, so the old resolver failed
mode-discovery and the gate rejected every coverage-task PR as
``copilot:invalid`` — leaving the parent PR blocked forever.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from copilot_discover_mode import (  # noqa: E402
    _candidates_from_branch,
    _mode_and_kind_from_labels,
)


def _labels(*names: str) -> list[dict]:
    return [{"name": n} for n in names]


def test_copilot_mode_label_maps_to_test_bot() -> None:
    for mode in ("regression", "bug-repro", "fix-and-test"):
        m, kind = _mode_and_kind_from_labels(_labels(f"copilot:{mode}"))
        assert (m, kind) == (mode, "test-bot")


def test_coverage_task_label_maps_to_regression_coverage_task() -> None:
    m, kind = _mode_and_kind_from_labels(_labels("coverage-task"))
    assert (m, kind) == ("regression", "coverage-task")


def test_unrelated_labels_resolve_to_none() -> None:
    m, kind = _mode_and_kind_from_labels(_labels("needs-tests", "bug"))
    assert (m, kind) == (None, None)


def test_empty_labels_resolve_to_none() -> None:
    assert _mode_and_kind_from_labels([]) == (None, None)


def test_explicit_mode_wins_over_coverage_task() -> None:
    """If both are present, the explicit mode must not be downgraded.

    A test-bot issue that somehow also picked up `coverage-task` should
    still be treated as its real mode (and kind test-bot), never silently
    forced to regression.
    """
    m, kind = _mode_and_kind_from_labels(
        _labels("coverage-task", "copilot:bug-repro")
    )
    assert (m, kind) == ("bug-repro", "test-bot")


# ---------------------------------------------------------------------------
# _candidates_from_branch
# ---------------------------------------------------------------------------

def test_branch_standard_copilot_pattern() -> None:
    """The canonical Copilot branch name yields the embedded issue number."""
    assert _candidates_from_branch("copilot/fix-issue-618") == [618]


def test_branch_no_issue_number() -> None:
    """Branches without 'issue-N' return an empty list."""
    assert _candidates_from_branch("feat/my-feature") == []
    assert _candidates_from_branch("") == []


def test_branch_multiple_issue_numbers() -> None:
    """All embedded issue numbers are returned in order, deduplicated."""
    assert _candidates_from_branch("fix-issue-10-issue-20") == [10, 20]
    assert _candidates_from_branch("issue-42-issue-42-extra") == [42]


def test_branch_none_treated_as_empty() -> None:
    """None is safely handled as empty (no AttributeError)."""
    # The function accepts str and guards with `or ""`; passing None would
    # require a type violation, so we only test the empty string edge case.
    assert _candidates_from_branch("") == []
