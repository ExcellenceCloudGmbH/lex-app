"""Tests for copilot_validate_pr_shape — per-mode PR-shape gating."""

from __future__ import annotations

import sys
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


def _plan_dir_for(tmp_path: Path, cluster: str = "07-calculations",
                   slug: str = "calculations", test_dirs=None) -> Path:
    """Build a synthetic sharded plan_dir with one cluster's allocation.yaml."""
    test_dirs = test_dirs if test_dirs is not None else [slug]
    d = tmp_path / "test-plan" / "clusters" / cluster
    d.mkdir(parents=True)
    dirs_yaml = "[" + ", ".join(test_dirs) + "]"
    d.joinpath("allocation.yaml").write_text(
        f"cluster: 7\nslug: {slug}\ntitle: Calc\n"
        f"test_dirs: {dirs_yaml}\n"
        "max_scenario: 10\nletters: {a: {status: complete}}\n"
    )
    return tmp_path / "test-plan"


# ----- happy paths -------------------------------------------------

def test_regression_happy_path_ok(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/tests/calculations/test_7m_thing.py", 80),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 3),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-batch-7m.md", 1),
    )
    files[-1].status = "added"
    pr_body = "Closes the gap on scenario 7.NN.\n\nFixes #42"
    result = validate_pr_shape(
        mode="regression", files=files, pr_body=pr_body,
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert isinstance(result, ValidationResult)
    assert result.ok, result.errors


def test_bug_repro_happy_path_ok(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/tests/calculations/test_5m_thing.py", 60),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 2),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-batch-5m.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
    )
    files[2].status = "added"
    pr_body = "Reproduces BUG-099.\n\nFixes #43"
    result = validate_pr_shape(
        mode="bug-repro", files=files, pr_body=pr_body,
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert result.ok, result.errors


def test_fix_and_test_happy_path_ok(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/tests/calculations/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 2),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-batch-5n.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 12),  # source fix, under 50 lines
    )
    files[2].status = "added"
    pr_body = (
        "Fixes BUG-100.\n\n"
        "### Source changes\n- `lex/core/services/foo.py`: short rationale.\n\n"
        "Fixes #44"
    )
    result = validate_pr_shape(
        mode="fix-and-test", files=files, pr_body=pr_body,
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert result.ok, result.errors


# ----- shape failures ---------------------------------------------

def test_missing_test_file_fails(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 3),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
    )
    files[-1].status = "added"
    result = validate_pr_shape(
        mode="regression", files=files, pr_body="Fixes #1",
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("no new test file" in e.lower() for e in result.errors)


def test_missing_session_fragment_fails(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/tests/calculations/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 3),
    )
    result = validate_pr_shape(
        mode="regression", files=files, pr_body="Fixes #1",
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("sessions/" in e for e in result.errors)


def test_missing_cluster_shard_edit_fails(tmp_path: Path) -> None:
    """All modes require the owning cluster's shard (clusters/NN-slug/) to be edited."""
    files = _files(
        ("lex/test_project/tests/calculations/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
    )
    files[-1].status = "added"
    result = validate_pr_shape(
        mode="regression", files=files, pr_body="Fixes #1",
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("07-calculations" in e for e in result.errors)


def test_missing_known_bugs_row_fails_bug_repro(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/tests/calculations/test_5m_thing.py", 60),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 2),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
    )
    files[-1].status = "added"
    result = validate_pr_shape(
        mode="bug-repro", files=files, pr_body="Fixes #1",
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("known-bugs.md" in e for e in result.errors)


def test_test_filename_regex_violation_fails(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/tests/calculations/test_thing_no_scenario_id.py", 60),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 3),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
    )
    files[-1].status = "added"
    result = validate_pr_shape(
        mode="regression", files=files, pr_body="Fixes #1",
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("test_<Nx>_" in e or "naming" in e.lower() for e in result.errors)


def test_files_outside_allowed_set_fail_for_test_only_modes(tmp_path: Path) -> None:
    """A regression-mode PR may not touch source."""
    files = _files(
        ("lex/test_project/tests/calculations/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 3),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
        ("lex/core/services/foo.py", 5),
    )
    files[2].status = "added"
    result = validate_pr_shape(
        mode="regression", files=files, pr_body="Fixes #1",
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("lex/core/services/foo.py" in e for e in result.errors)


def test_fix_and_test_source_diff_above_50_lines_fails(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/tests/calculations/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 2),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 30),
        ("lex/core/services/bar.py", 25),
    )
    files[2].status = "added"
    pr_body = (
        "### Source changes\n"
        "- `lex/core/services/foo.py`: a\n"
        "- `lex/core/services/bar.py`: b\n"
        "\nFixes #99"
    )
    result = validate_pr_shape(
        mode="fix-and-test", files=files, pr_body=pr_body,
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("source diff" in e.lower() for e in result.errors)


def test_fix_and_test_source_file_not_listed_in_body_fails(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/tests/calculations/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 2),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 12),
    )
    files[2].status = "added"
    pr_body = "no source changes section\n\nFixes #1"
    result = validate_pr_shape(
        mode="fix-and-test", files=files, pr_body=pr_body,
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("source changes" in e.lower() for e in result.errors)


def test_fix_and_test_source_path_mismatched_in_body_fails(tmp_path: Path) -> None:
    """The `### Source changes` section lists a different path than the actual diff."""
    files = _files(
        ("lex/test_project/tests/calculations/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 2),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 12),
    )
    files[2].status = "added"
    pr_body = (
        "Fixes BUG-100.\n\n"
        "### Source changes\n- `lex/core/services/bar.py`: wrong path.\n\n"
        "Fixes #44"
    )
    result = validate_pr_shape(
        mode="fix-and-test", files=files, pr_body=pr_body,
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("lex/core/services/foo.py" in e for e in result.errors)


def test_missing_fixes_link_fails(tmp_path: Path) -> None:
    files = _files(
        ("lex/test_project/tests/calculations/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 3),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
    )
    files[-1].status = "added"
    result = validate_pr_shape(
        mode="regression", files=files, pr_body="No issue link here.",
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("Fixes #" in e for e in result.errors)


def test_missing_fixes_link_fails_bug_repro(tmp_path: Path) -> None:
    """Check 1 of §7 fails any mode with no issue link — pin that bug-repro is not special-cased."""
    files = _files(
        ("lex/test_project/tests/calculations/test_5m_thing.py", 60),
        ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", 2),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
    )
    files[2].status = "added"
    result = validate_pr_shape(
        mode="bug-repro", files=files, pr_body="No issue link here.",
        plan_dir=_plan_dir_for(tmp_path),
    )
    assert not result.ok
    assert any("Fixes #" in e for e in result.errors)


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        validate_pr_shape(mode="banana", files=[], pr_body="")


# ----- linked-issue (coverage-task / sidebar-link) ----------------

def test_linked_issue_satisfies_fixes_link_without_body_text(tmp_path: Path) -> None:
    """A gate-verified closing-issue link replaces the literal `Fixes #N`.

    Copilot's coding agent routinely links the originating issue via the
    Development sidebar instead of writing `Fixes #N` in the body. The
    gate resolves that link (copilot_discover_mode.py) and passes the
    issue number as linked_issue — the body text is then not required.
    """
    files = _files(
        ("lex/test_project/tests/signals_ws/test_9e_thing.py", 80),
        ("lex/test_project/test-plan/clusters/09-signals-ws/allocation.yaml", 3),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
    )
    files[-1].status = "added"
    pr_body = "Adds coverage for the consumer/signal sync. No Fixes line here."
    result = validate_pr_shape(
        mode="regression", files=files, pr_body=pr_body, linked_issue=556,
        plan_dir=_plan_dir_for(tmp_path, cluster="09-signals-ws", slug="signals_ws"),
    )
    assert result.ok, result.errors


def test_missing_fixes_link_still_fails_when_no_linked_issue(tmp_path: Path) -> None:
    """Without a resolved link, the body must still carry `Fixes #N`."""
    files = _files(
        ("lex/test_project/tests/signals_ws/test_9e_thing.py", 80),
        ("lex/test_project/test-plan/clusters/09-signals-ws/allocation.yaml", 3),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", 1),
    )
    files[-1].status = "added"
    result = validate_pr_shape(
        mode="regression", files=files, pr_body="No link.", linked_issue=None,
        plan_dir=_plan_dir_for(tmp_path, cluster="09-signals-ws", slug="signals_ws"),
    )
    assert not result.ok
    assert any("Fixes #" in e for e in result.errors)


def test_coverage_task_regression_pr_passes_shape(tmp_path: Path) -> None:
    """A Feature-4 coverage-task PR is shaped exactly like a regression PR.

    It maps to mode=regression (test-only, multi-file allowed) and links
    its coverage-task issue via the sidebar — so with linked_issue set it
    must pass shape validation end-to-end.
    """
    files = _files(
        ("lex/test_project/tests/signals_ws/test_9e_consumer_signal_sync.py", 251),
        ("lex/test_project/test-plan/clusters/09-signals-ws/allocation.yaml", 16),
        ("lex/test_project/test-plan/progress/sessions/2026-07-07-9e-coverage.md", 1),
        ("lex/test_project/test-plan/progress/dashboard.md", 3),
    )
    files[2].status = "added"
    pr_body = "Adds cluster 9e coverage for the PR #555 source files."
    result = validate_pr_shape(
        mode="regression", files=files, pr_body=pr_body, linked_issue=556,
        plan_dir=_plan_dir_for(tmp_path, cluster="09-signals-ws", slug="signals_ws"),
    )
    assert result.ok, result.errors


# ----- sharded plan rules ------------------------------------------

class TestShardedPlanRules:
    def _plan_dir(self, tmp_path):
        d = tmp_path / "test-plan" / "clusters" / "07-calculations"
        d.mkdir(parents=True)
        d.joinpath("allocation.yaml").write_text(
            "cluster: 7\nslug: calculations\ntitle: Calc\n"
            "test_dirs: [calculations, calculation_logging]\n"
            "max_scenario: 10\nletters: {a: {status: complete}}\n"
        )
        return tmp_path / "test-plan"

    def _files(self, *paths_status):
        return [PRFile(path=p, additions=5, deletions=0, status=s) for p, s in paths_status]

    def test_valid_sharded_pr(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculations/test_7b_new.py", "added"),
            ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", "modified"),
            ("lex/test_project/test-plan/clusters/07-calculations/batches.md", "modified"),
            ("lex/test_project/test-plan/progress/sessions/2026-07-07-batch-7b.md", "added"),
            ("lex/test_project/test-plan/progress/dashboard.md", "modified"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert r.ok, r.errors

    def test_missing_cluster_shard_edit(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculations/test_7b_new.py", "added"),
            ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", "added"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert not r.ok
        assert any("07-calculations" in e for e in r.errors)

    def test_missing_session_fragment(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculations/test_7b_new.py", "added"),
            ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", "modified"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert not r.ok
        assert any("sessions/" in e for e in r.errors)

    def test_test_dirs_alias_maps_to_owning_cluster(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculation_logging/test_7c_log.py", "added"),
            ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", "modified"),
            ("lex/test_project/test-plan/progress/sessions/2026-07-07-y.md", "added"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert r.ok, r.errors

    def test_edit_to_retired_monolith_rejected(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculations/test_7b_new.py", "added"),
            ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", "modified"),
            ("lex/test_project/test-plan/progress/sessions/2026-07-07-z.md", "added"),
            ("lex/test_project/test-plan/test-clusters.md", "modified"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert not r.ok
        assert any("retired" in e for e in r.errors)
