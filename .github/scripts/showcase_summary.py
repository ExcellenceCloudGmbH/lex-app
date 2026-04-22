#!/usr/bin/env python3
"""
Render a business-friendly summary of the two "Showcase Tests" runs.

Reads each test's GitHub Actions step outcome ("success" / "failure" /
"cancelled" / "skipped") from argv, looks the outcome up in a business
label table, and writes a markdown block to ``$GITHUB_STEP_SUMMARY``
(or, if that env var is not set, to stdout — useful for local dry runs).

Usage:
    python showcase_summary.py <init_outcome> <crud_outcome>
        [--commit-sha <sha>] [--run-url <url>]

The output is the FIRST thing a stakeholder sees on the workflow-run
page, so it is deliberately jargon-light: no dotted module paths, no
tracebacks, no pytest vocabulary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Showcase:
    """One row in the stakeholder summary."""
    key: str                 # argv slot — "init" | "crud"
    label: str               # business-facing title (bold in the table)
    description: str         # one-line plain-English summary
    technical_id: str        # dotted test id — shown only in the footer
    pass_blurb: str          # "when green, this proves …"
    fail_blurb: str          # "when red, this means …"


# ── Mapping table — single source of truth for test → business label ─
SHOWCASES: dict[str, Showcase] = {
    "init": Showcase(
        key="init",
        label="Project initialization — migrations & access sync",
        description=(
            "Pressing **Init** on a new project detects model changes, "
            "generates migrations, applies them to the database, and "
            "registers the project with Keycloak for access management."
        ),
        technical_id=(
            "lex.test_project.tests.init.test_1b_lex_init."
            "TestCluster01b_LexInit.test_1_6b_init_runs_full_pipeline"
        ),
        pass_blurb=(
            "A customer can press **Init** and end up with a fully "
            "prepared database and working access-management."
        ),
        fail_blurb=(
            "The first-run onboarding is broken. New customers cannot "
            "reliably initialise their project."
        ),
    ),
    "crud": Showcase(
        key="crud",
        label="Create record via REST API",
        description=(
            "A record posted to the public REST API is accepted, stored, "
            "and readable on subsequent requests."
        ),
        technical_id=(
            "lex.test_project.tests.crud_api.test_2a_create."
            "TestCluster02a_Create.test_2_1_post_creates_record"
        ),
        pass_blurb=(
            "The core \"create a record\" capability works end-to-end "
            "through the public HTTP API."
        ),
        fail_blurb=(
            "Creating records via the API is broken. Any customer-facing "
            "flow that creates data will also be broken."
        ),
    ),
}


_OUTCOME_ICON = {
    "success": "✅",
    "failure": "❌",
    "cancelled": "⚠️",
    "skipped": "⏭️",
}

_OUTCOME_WORD = {
    "success": "PASSED",
    "failure": "FAILED",
    "cancelled": "CANCELLED",
    "skipped": "SKIPPED",
}


def _icon(outcome: str) -> str:
    return _OUTCOME_ICON.get(outcome, "❔")


def _word(outcome: str) -> str:
    return _OUTCOME_WORD.get(outcome, outcome.upper())


def render(init_outcome: str, crud_outcome: str,
           commit_sha: str | None, run_url: str | None) -> str:
    """Return the markdown block to paste into GITHUB_STEP_SUMMARY."""
    init = SHOWCASES["init"]
    crud = SHOWCASES["crud"]
    outcomes = {"init": init_outcome, "crud": crud_outcome}

    overall_ok = all(o == "success" for o in outcomes.values())
    overall_icon = "✅" if overall_ok else "❌"
    overall_word = (
        "All showcase capabilities are working"
        if overall_ok
        else "One or more showcase capabilities are broken"
    )

    lines: list[str] = []
    lines.append(f"# {overall_icon} Showcase test results")
    lines.append("")
    lines.append(
        f"**{overall_word}** against this version of the platform."
    )
    if commit_sha:
        lines.append(f"_Commit:_ `{commit_sha[:8]}`")
    lines.append(
        "_Run at:_ "
        + dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    )
    lines.append("")

    # Results table
    lines.append("| | Capability | Result | What it proves |")
    lines.append("|---|---|---|---|")
    for sc in (init, crud):
        outcome = outcomes[sc.key]
        lines.append(
            f"| {_icon(outcome)} | **{sc.label}** | "
            f"{_word(outcome)} | {sc.description} |"
        )
    lines.append("")

    # Per-test narrative for any failures — helps stakeholders know what to do
    failures = [
        (sc, outcomes[sc.key])
        for sc in (init, crud)
        if outcomes[sc.key] != "success"
    ]
    if failures:
        lines.append("## What the red checks mean")
        lines.append("")
        for sc, outcome in failures:
            lines.append(f"- {_icon(outcome)} **{sc.label}** — {sc.fail_blurb}")
        lines.append("")
        if run_url:
            lines.append(
                f"Full test output: [open the workflow run logs]({run_url})."
            )
        else:
            lines.append(
                "Full test output is available in the workflow run logs."
            )
        lines.append("")

    # Footer — technical details collapsed so stakeholders can skip them
    lines.append("<details>")
    lines.append("<summary>Technical details (for engineers)</summary>")
    lines.append("")
    lines.append("| Capability | Test id | Outcome |")
    lines.append("|---|---|---|")
    for sc in (init, crud):
        outcome = outcomes[sc.key]
        lines.append(
            f"| {sc.label} | `{sc.technical_id}` | "
            f"`{outcome}` |"
        )
    lines.append("")
    lines.append(
        "See `docs/ci-cd/showcase-ci.md` for what this workflow covers "
        "and how to read these results."
    )
    lines.append("")
    lines.append("</details>")

    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("init_outcome", help="Outcome of the init test step")
    parser.add_argument("crud_outcome", help="Outcome of the crud test step")
    parser.add_argument("--commit-sha", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument(
        "--run-url",
        default=(
            f"{os.environ.get('GITHUB_SERVER_URL', '')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID', '')}"
            if os.environ.get("GITHUB_RUN_ID")
            else None
        ),
    )
    args = parser.parse_args(argv)

    md = render(
        args.init_outcome,
        args.crud_outcome,
        commit_sha=args.commit_sha,
        run_url=args.run_url,
    )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(md)
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


