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


def test_assembled_prompt_does_not_inline_large_test_plan_files(fake_test_plan: Path) -> None:
    """Regression: production failed 2026-05-21 with `GraphQL: Body is too long`
    because the assembler inlined test-clusters.md (~160KB) directly into the
    issue body. The fix is to reference those files by path; Copilot reads
    them from the repo. Pin the new contract: their contents must NOT appear
    in the output, but their paths MUST."""
    # Pad both big files past 64KB so this test catches a regression even on a
    # small repo. If the assembler ever re-inlines them, the size guard would
    # fire first; this test pins the design intent independently.
    (fake_test_plan / "test-clusters.md").write_text("CLUSTERS_SENTINEL\n" + ("x" * 70_000))
    (fake_test_plan / "test-writing-plan.md").write_text("WRITING_PLAN_SENTINEL\n" + ("y" * 70_000))
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    assert "CLUSTERS_SENTINEL" not in out
    assert "WRITING_PLAN_SENTINEL" not in out
    assert "lex/test_project/test-plan/test-clusters.md" in out
    assert "lex/test_project/test-plan/test-writing-plan.md" in out


def test_assembled_prompt_fits_under_github_issue_body_cap(fake_test_plan: Path) -> None:
    """GitHub's createIssue mutation caps issue body at 65,536 bytes. The
    assembler enforces a 60,000-byte margin. Even with realistic-ish
    conventions content, the body must fit."""
    # Realistic-ish conventions.md size — currently ~5KB; pad to 20KB headroom.
    (fake_test_plan / "progress" / "conventions.md").write_text("# Conventions\n\n" + ("c" * 20_000))
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    assert len(out.encode("utf-8")) <= 60_000


def test_size_guard_raises_when_conventions_exceeds_cap(fake_test_plan: Path) -> None:
    """If a future edit to conventions.md or the Golden Rule paragraph pushes
    the body past the cap, fail loud in CI — not at `gh issue create` time."""
    (fake_test_plan / "progress" / "conventions.md").write_text("# Conventions\n\n" + ("z" * 80_000))
    with pytest.raises(ValueError, match="exceeds .* cap"):
        assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)


def test_missing_test_clusters_file_raises(fake_test_plan: Path) -> None:
    """The pointer pattern doesn't read test-clusters.md, but its existence is
    still a precondition — a missing file would leave Copilot with a dead
    pointer. Verify the existence check fires."""
    (fake_test_plan / "test-clusters.md").unlink()
    with pytest.raises(FileNotFoundError, match="test-clusters.md"):
        assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)


def test_missing_test_writing_plan_file_raises(fake_test_plan: Path) -> None:
    (fake_test_plan / "test-writing-plan.md").unlink()
    with pytest.raises(FileNotFoundError, match="test-writing-plan.md"):
        assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)


def test_assembled_prompt_teaches_multi_cluster_decomposition(fake_test_plan: Path) -> None:
    """A feature's contract often spans clusters (e.g. an audit-log entry
    produced as a side-effect belongs in audit_logging/, even if the
    headline behaviour lives in another cluster). Pin that the prompt
    teaches this — single-cluster placement is the easy default; the
    prompt must explicitly call out the multi-cluster pattern with at
    least one concrete cross-cluster example so Copilot doesn't default
    to single-file."""
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    # Heading: the decomposition section is its own block, not buried.
    assert "Cluster decomposition" in out
    # Concept: primary vs secondary clusters.
    assert "primary" in out.lower() and "secondary" in out.lower()
    # Concrete cross-cluster example: audit log is the canonical case.
    assert "audit_logging" in out


def test_assembled_prompt_includes_research_first_block(fake_test_plan: Path) -> None:
    """The PyCharm live test exposed that a Copilot agent will code from first
    instinct (reinventing cross-process state instead of reusing the existing
    cache-backed store) when the prompt only says 'don't mirror the code'. The
    prompt must also tell the agent what to do FIRST: read docs for intent and
    check for an existing mechanism before inventing one."""
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    assert "Research before you write code" in out
    # Reads docs for intent.
    assert "docs/features/" in out
    # The concrete anti-pattern from the live failure: reinventing existing state.
    assert "ActiveCalculationStateStore" in out
    # Research must come before the mode block (front-loaded, like the Golden Rule).
    assert out.index("Research before you write code") < out.index("## Mode: regression")


def test_assembled_prompt_lists_full_done_doc_set(fake_test_plan: Path) -> None:
    """Definition-of-Done parity with the local AGENTS.md rules: the deliverables
    must name the universal session-log row, the dashboard status bump, AND the
    test-writing-plan batch row — so the cloud and local paths update the same
    docs and don't drift."""
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    assert "progress/session-log.md" in out
    assert "progress/dashboard.md" in out
    assert "test-writing-plan.md" in out


def test_bug_repro_and_fix_and_test_constrained_to_single_file(fake_test_plan: Path) -> None:
    """Multi-cluster decomposition is REGRESSION-only. Bug-repro and
    fix-and-test stay single-file (a multi-file bug-repro dilutes the
    @expectedFailure strip-and-fail contract; a multi-file fix-and-test
    makes the 50-line source-diff cap meaningless). The prompt must
    state this explicitly per mode."""
    out_bug = assemble_prompt(_basic_issue(Mode.BUG_REPRO), test_plan_dir=fake_test_plan)
    out_fix = assemble_prompt(_basic_issue(Mode.FIX_AND_TEST), test_plan_dir=fake_test_plan)
    # Both prompts must constrain to single-file. Match the literal phrase
    # used in the assembled prompt's mode-scope block.
    assert "exactly **one**" in out_bug
    assert "exactly **one**" in out_fix
