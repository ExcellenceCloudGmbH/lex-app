"""Drift guard: every agent front-end must share ONE surface-enumeration rule.

There are four prompt surfaces that tell an agent how to decompose a change into
test surfaces and clusters:

  1. `lex/test_project/test-plan/progress/conventions.md` — the **canonical**
     source. The cloud Copilot prompt inlines this file verbatim
     (see `copilot_assemble_prompt.assemble_prompt`), so whatever lands here is
     what the cloud agent reads.
  2. `.github/instructions/testing.instructions.md` — VS Code Copilot / cloud IDE.
  3. `.claude/skills/lex-testing/SKILL.md` — Claude Code skill.
  4. `AGENTS.md` — JetBrains Copilot / Cursor / Codex (load-bearing in PyCharm).

These drifted once: the cloud assembler taught a weaker "primary/secondary"
decomposition while the local surfaces taught full surface enumeration, and none
of them taught the *execution-path* dimension — which is why an agent enumerated
the entry points of a feature but missed that one operation can run by more than
one route, each route its own surface. This test pins that the shared rule — and
specifically its execution-path clause — is present in all four, so the cloud and
local paths can never silently diverge again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

CANONICAL = REPO_ROOT / "lex" / "test_project" / "test-plan" / "progress" / "conventions.md"

SURFACES = {
    "conventions.md (canonical)": CANONICAL,
    "testing.instructions.md": REPO_ROOT / ".github" / "instructions" / "testing.instructions.md",
    "lex-testing SKILL.md": REPO_ROOT / ".claude" / "skills" / "lex-testing" / "SKILL.md",
    "AGENTS.md": REPO_ROOT / "AGENTS.md",
}


def test_canonical_source_owns_the_surface_rule() -> None:
    """conventions.md is the single source the cloud prompt inlines. It must
    carry the rule's canonical heading and the execution-path clause."""
    text = CANONICAL.read_text()
    assert "Enumerate Behaviour Surfaces Before Allocating Clusters" in text, (
        "conventions.md must own the canonical surface-enumeration section — the "
        "cloud Copilot prompt inlines this file, so the rule must live here."
    )
    assert "execution path" in text.lower(), (
        "The canonical rule must teach the execution-path dimension (one operation, "
        "multiple routes). This is the clause that makes an agent derive the "
        "multi-route surface without being handed a concrete example."
    )


@pytest.mark.parametrize("name", list(SURFACES))
def test_every_surface_carries_the_execution_path_rule(name: str) -> None:
    """All four agent front-ends must teach the execution-path surface dimension.
    If any one drops or never gained it, cloud and local agents drift — exactly
    the failure this guard exists to prevent."""
    path = SURFACES[name]
    text = path.read_text().lower()
    assert "execution path" in text, (
        f"{name} is missing the execution-path surface rule. Every prompt surface "
        f"must teach it (one operation may run by more than one route; each route is "
        f"its own surface). Update {path.relative_to(REPO_ROOT)} to match the "
        f"canonical rule in conventions.md."
    )


@pytest.mark.parametrize("name", [n for n in SURFACES if n != "conventions.md (canonical)"])
def test_local_surfaces_point_at_the_canonical_source(name: str) -> None:
    """Each local surface must name conventions.md as the authoritative source, so
    future edits have one obvious home and reviewers know the satellites mirror it."""
    text = SURFACES[name].read_text()
    assert "conventions.md" in text, (
        f"{name} must reference conventions.md as the canonical source of the "
        f"surface rule, so the four surfaces stay anchored to one source of truth."
    )


def test_execution_path_rule_stays_domain_neutral() -> None:
    """The whole point is that the agent *derives* the concrete routes itself. The
    canonical rule must describe routes by abstract category (configuration /
    runtime mode / dispatch) and must NOT name the held-out eval's concrete
    cancellation mechanics — naming them would hand the agent the answer and leak
    the evaluation domain."""
    # Collapse whitespace so line-wrapping in the markdown can't break the match.
    text = " ".join(CANONICAL.read_text().lower().split())
    # The section states routes vary by abstract category, not a named subsystem.
    assert "configuration, runtime mode, or how the work is dispatched" in text
    # Guard against re-introducing concrete cancellation/dispatch giveaways into
    # the *rule statement*. (These tokens may legitimately appear elsewhere in the
    # repo — this only checks the canonical conventions rule file.)
    for leak in ("revoke", "terminate=true", "setasyncexc", "cooperative cancellation"):
        assert leak not in text, (
            f"The canonical surface rule leaked a concrete eval-domain mechanic "
            f"({leak!r}). Keep the rule domain-neutral — name the category, not the "
            f"answer, so the agent figures out the routes itself."
        )
