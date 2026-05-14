"""Tests for copilot_assemble_prompt.assemble_prompt — contract pinned before script lands."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

# Make .github/scripts importable when pytest runs from repo root.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from copilot_assemble_prompt import IssueInput, Mode, assemble_prompt  # noqa: E402


@pytest.fixture
def fake_test_plan(tmp_path: Path) -> Path:
    """Build a minimal test-plan/ tree with the four files the assembler reads."""
    plan = tmp_path / "test-plan"
    (plan / "progress").mkdir(parents=True)
    (plan / "index.md").write_text(
        "# Index\n\n## Golden Rule\n\nTest what the framework is **trying** to achieve, "
        "not what the current code happens to do.\n"
    )
    (plan / "test-clusters.md").write_text("# Clusters\n\nCluster 7 — calculation state machine.\n")
    (plan / "test-writing-plan.md").write_text(
        "# Writing plan\n\nFile naming: `tests/<cluster>/test_<Nx>_<slug>.py`.\n"
    )
    (plan / "progress" / "conventions.md").write_text(
        "# Conventions\n\nAppend rows to `progress/session-log.md`.\n"
    )
    return plan


def _basic_issue(mode: Mode) -> IssueInput:
    return IssueInput(
        number=42,
        title="Add regression test for X",
        mode=mode,
        behaviour="Framework should reset state on success.",
        reproducer="1. Do A\n2. Observe B",
        cluster_hint="7g",
        files=["lex/foo.py"],
    )


def test_assembled_prompt_includes_golden_rule_block(fake_test_plan: Path) -> None:
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    assert "Golden Rule" in out
    assert "trying" in out


def test_assembled_prompt_includes_per_mode_instruction_block(fake_test_plan: Path) -> None:
    out_reg = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    out_bug = assemble_prompt(_basic_issue(Mode.BUG_REPRO), test_plan_dir=fake_test_plan)
    out_fix = assemble_prompt(_basic_issue(Mode.FIX_AND_TEST), test_plan_dir=fake_test_plan)
    # Each mode injects a distinct, identifiable header.
    assert "## Mode: regression" in out_reg
    assert "## Mode: bug-repro" in out_bug
    assert "## Mode: fix-and-test" in out_fix
    # Mode-B uniquely instructs the @expectedFailure decorator + known-bugs.md row.
    assert "@unittest.expectedFailure" in out_bug
    assert "known-bugs.md" in out_bug
    # Mode-C uniquely allows source edits.
    assert "smallest source change" in out_fix


def test_assembled_prompt_substitutes_issue_data(fake_test_plan: Path) -> None:
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    assert "Add regression test for X" in out
    assert "Framework should reset state on success." in out
    assert "1. Do A" in out
    assert "7g" in out
    assert "lex/foo.py" in out


def test_assembled_prompt_section_order_is_stable(fake_test_plan: Path) -> None:
    """Golden Rule must come first so Copilot reads it before anything else."""
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    golden_pos = out.index("Golden Rule")
    mode_pos = out.index("## Mode: regression")
    issue_pos = out.index("Add regression test for X")
    assert golden_pos < mode_pos < issue_pos


def test_missing_test_plan_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=tmp_path / "nope")


def test_blank_reproducer_allowed_for_regression(fake_test_plan: Path) -> None:
    issue = IssueInput(
        number=1, title="t", mode=Mode.REGRESSION, behaviour="b",
        reproducer="", cluster_hint="", files=[],
    )
    out = assemble_prompt(issue, test_plan_dir=fake_test_plan)
    assert "Reproducer" in out  # heading still present
    # Body says "(none provided)" so Copilot doesn't get a silently empty section.
    assert "(none provided)" in out


def test_blank_reproducer_rejected_for_bug_repro(fake_test_plan: Path) -> None:
    issue = IssueInput(
        number=1, title="t", mode=Mode.BUG_REPRO, behaviour="b",
        reproducer="", cluster_hint="", files=[],
    )
    with pytest.raises(ValueError, match="reproducer is required"):
        assemble_prompt(issue, test_plan_dir=fake_test_plan)


def test_blank_reproducer_rejected_for_fix_and_test(fake_test_plan: Path) -> None:
    issue = IssueInput(
        number=1, title="t", mode=Mode.FIX_AND_TEST, behaviour="b",
        reproducer="", cluster_hint="", files=[],
    )
    with pytest.raises(ValueError, match="reproducer is required"):
        assemble_prompt(issue, test_plan_dir=fake_test_plan)
