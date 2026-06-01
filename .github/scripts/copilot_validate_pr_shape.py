"""Validate the shape of a Copilot test-bot PR (file set, naming, body markers).

Used by ``copilot_pr_gate.yml`` to apply the per-mode contract from
``docs/superpowers/specs/2026-05-13-copilot-test-bot-design.md`` §7.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

VALID_MODES = ("regression", "bug-repro", "fix-and-test")

# File-set rules ------------------------------------------------------

TEST_FILE_RE = re.compile(
    r"^lex/test_project/tests/(?P<cluster>[A-Za-z0-9_]+)/test_(?P<scenario>\d+[a-z]?)_[A-Za-z0-9_]+\.py$"
)

ALLOWED_TEST_ONLY_PREFIXES = (
    "lex/test_project/",
    ".github/workflows/",
    ".github/scripts/showcase_clusters.py",
)

MAX_FIX_AND_TEST_SOURCE_LINES = 50
SOURCE_CHANGES_HEADING = "### Source changes"
FIXES_LINK_RE = re.compile(r"(?im)^Fixes\s+#\d+\s*$")


@dataclass
class PRFile:
    path: str
    additions: int
    deletions: int

    @property
    def delta(self) -> int:
        return self.additions + self.deletions


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def _is_test_file(path: str) -> bool:
    return bool(TEST_FILE_RE.match(path))


def _is_allowed_for_test_only(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in ALLOWED_TEST_ONLY_PREFIXES)


def validate_pr_shape(
    *,
    mode: str,
    files: list[PRFile],
    pr_body: str,
    linked_issue: int | None = None,
) -> ValidationResult:
    """Validate a Copilot PR's file set, naming, and body markers.

    ``linked_issue`` — when the caller (the PR gate) has already resolved
    the originating issue via the PR's verified closing-issue link
    (GitHub's Development sidebar / ``closingIssuesReferences``), the
    literal ``Fixes #N`` line in the body is redundant and not required.
    Copilot's coding agent routinely links the issue via the sidebar
    *instead of* writing ``Fixes #N`` (see ``copilot_discover_mode.py``),
    so requiring the text would reject otherwise-valid PRs. When
    ``linked_issue`` is ``None`` (e.g. local/manual validation with no
    resolved link) the body must still carry ``Fixes #N``.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"unknown mode {mode!r}; valid: {VALID_MODES}")

    errors: list[str] = []
    paths = [f.path for f in files]

    # 1. New test file present + name matches Nx_slug pattern.
    test_files = [p for p in paths if _is_test_file(p)]
    if not test_files:
        # Distinguish "no test file at all" from "test file with bad name".
        bad_named = [
            p for p in paths
            if p.startswith("lex/test_project/tests/") and p.endswith(".py")
            and not _is_test_file(p)
        ]
        if bad_named:
            errors.append(
                "Test file naming violates `test_<Nx>_<slug>.py`: "
                + ", ".join(bad_named)
            )
        else:
            errors.append("no new test file under `lex/test_project/tests/<cluster>/`")

    # 2. test-clusters.md modified.
    if "lex/test_project/test-plan/test-clusters.md" not in paths:
        errors.append("`lex/test_project/test-plan/test-clusters.md` was not modified")

    # 3. Session log appended.
    if "lex/test_project/test-plan/progress/session-log.md" not in paths:
        errors.append("`lex/test_project/test-plan/progress/session-log.md` was not appended")

    # 4. Mode-B / Mode-C: known-bugs.md row added.
    if mode in ("bug-repro", "fix-and-test"):
        if "lex/test_project/test-plan/known-bugs.md" not in paths:
            errors.append(
                "`lex/test_project/test-plan/known-bugs.md` was not modified — "
                "modes bug-repro and fix-and-test require a new BUG-NNN row"
            )

    # 5. Source-file rules.
    test_only = mode in ("regression", "bug-repro")
    forbidden_source: list[str] = []
    source_files: list[PRFile] = []
    for f in files:
        if _is_test_file(f.path):
            continue
        if f.path.startswith("lex/test_project/test-plan/"):
            continue
        if _is_allowed_for_test_only(f.path):
            continue
        # Anything else is source.
        if test_only:
            forbidden_source.append(f.path)
        else:
            source_files.append(f)

    if forbidden_source:
        errors.append(
            "PR touches files outside the allowed set for mode "
            f"{mode}: {', '.join(sorted(forbidden_source))}"
        )

    # 6. fix-and-test: source-diff cap + body listing.
    if mode == "fix-and-test":
        total_source_delta = sum(f.delta for f in source_files)
        if total_source_delta > MAX_FIX_AND_TEST_SOURCE_LINES:
            errors.append(
                "source diff is "
                f"{total_source_delta} lines (cap {MAX_FIX_AND_TEST_SOURCE_LINES})"
            )
        if SOURCE_CHANGES_HEADING not in pr_body:
            errors.append(
                f"PR body must include a `{SOURCE_CHANGES_HEADING}` section listing every "
                "source file touched"
            )
        else:
            tail = pr_body.split(SOURCE_CHANGES_HEADING, 1)[1]
            for f in source_files:
                if f.path not in tail:
                    errors.append(
                        f"source file `{f.path}` not listed under `{SOURCE_CHANGES_HEADING}`"
                    )

    # 7. PR body links the originating issue. A verified closing-issue
    #    link (passed as linked_issue by the gate) satisfies this without
    #    the literal text — Copilot links via the Development sidebar.
    if linked_issue is None and not FIXES_LINK_RE.search(pr_body or ""):
        errors.append("PR body must contain `Fixes #N` (the originating issue)")

    return ValidationResult(ok=not errors, errors=errors)


def _cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--files-json", required=True, type=Path,
                        help='JSON: [{"path":..., "additions":N, "deletions":N}, ...]')
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument(
        "--linked-issue", type=int, default=None,
        help="Originating issue number, if the gate already verified the "
             "PR's closing-issue link. When set, the body's `Fixes #N` line "
             "is not required.",
    )
    args = parser.parse_args()

    raw = json.loads(args.files_json.read_text())
    files = [
        PRFile(path=r["path"], additions=int(r.get("additions", 0)), deletions=int(r.get("deletions", 0)))
        for r in raw
    ]
    body = args.body_file.read_text()
    result = validate_pr_shape(
        mode=args.mode, files=files, pr_body=body, linked_issue=args.linked_issue
    )
    if result.ok:
        print("OK")
        return 0
    for e in result.errors:
        print(f"- {e}")
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
