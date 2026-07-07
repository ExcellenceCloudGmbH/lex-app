"""Validate the shape of a Copilot test-bot PR (file set, naming, body markers).

Used by ``copilot_pr_gate.yml`` to apply the per-mode contract from
``docs/superpowers/specs/2026-05-13-copilot-test-bot-design.md`` §7.

The test-plan is a sharded layout: ``clusters/NN-<slug>/{allocation.yaml,
batches.md}``, session fragments under ``progress/sessions/YYYY-MM-DD-<slug>.md``,
and a generated ``progress/dashboard.md``. Every PR must (1) add a new test
file, (2) edit the cluster shard that claims the test file's folder (resolved
via each cluster's ``allocation.yaml`` ``test_dirs``), (3) add a new session
fragment, and (4) leave the retired monoliths (``test-clusters.md``,
``test-writing-plan.md``, ``progress/session-log.md``) untouched.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

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

RETIRED_MONOLITHS = (
    "lex/test_project/test-plan/test-clusters.md",
    "lex/test_project/test-plan/test-writing-plan.md",
    "lex/test_project/test-plan/progress/session-log.md",
)
SESSION_FRAGMENT_RE = re.compile(
    r"^lex/test_project/test-plan/progress/sessions/\d{4}-\d{2}-\d{2}-[A-Za-z0-9._-]+\.md$"
)
CLUSTER_SHARD_PREFIX = "lex/test_project/test-plan/clusters/"


def _slug_to_shard_dir(plan_dir: Path) -> dict[str, str]:
    """Map every claimed test-dir slug -> its clusters/NN-<slug>/ dir name,
    by reading allocation.yaml files (test_dirs aliases included)."""
    out: dict[str, str] = {}
    for yml in sorted((plan_dir / "clusters").glob("*/allocation.yaml")):
        data = yaml.safe_load(yml.read_text())
        for d in data.get("test_dirs") or [data["slug"]]:
            out[d] = yml.parent.name
    return out


@dataclass
class PRFile:
    path: str
    additions: int
    deletions: int
    status: str = ""

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
    plan_dir: Path | None = None,
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

    ``plan_dir`` — root of the sharded test-plan (defaults to
    ``lex/test_project/test-plan``). Used to resolve each new test file's
    owning cluster shard via ``clusters/*/allocation.yaml``'s ``test_dirs``.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"unknown mode {mode!r}; valid: {VALID_MODES}")

    plan_dir = plan_dir or Path("lex/test_project/test-plan")

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

    # 2. Each added test file's cluster shard was edited.
    shard_by_slug = _slug_to_shard_dir(plan_dir)
    for p in test_files:
        slug = p.split("/")[3]  # lex/test_project/tests/<slug>/...
        shard = shard_by_slug.get(slug)
        if shard is None:
            errors.append(
                f"test folder {slug!r} is not claimed by any cluster's "
                "allocation.yaml (test_dirs)"
            )
        elif not any(x.startswith(f"{CLUSTER_SHARD_PREFIX}{shard}/") for x in paths):
            errors.append(
                f"`{CLUSTER_SHARD_PREFIX}{shard}/` was not updated for new test `{p}` "
                "(allocation.yaml + batches.md at minimum)"
            )

    # 3. At least one NEW session fragment.
    fragments = [
        f for f in files
        if SESSION_FRAGMENT_RE.match(f.path) and f.status == "added"
    ]
    if not fragments:
        errors.append(
            "no new session fragment added under "
            "`lex/test_project/test-plan/progress/sessions/` "
            "(named YYYY-MM-DD-<slug>.md)"
        )

    # 3b. Retired monoliths are frozen.
    for p in paths:
        if p in RETIRED_MONOLITHS:
            errors.append(f"`{p}` is retired (pointer stub) — edit the sharded plan instead")

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
    parser.add_argument(
        "--plan-dir", type=Path, default=Path("lex/test_project/test-plan"),
        help="Root of the sharded test-plan (default: lex/test_project/test-plan).",
    )
    args = parser.parse_args()

    raw = json.loads(args.files_json.read_text())
    files = [
        PRFile(
            path=r["path"],
            additions=int(r.get("additions", 0)),
            deletions=int(r.get("deletions", 0)),
            status=r.get("status", ""),
        )
        for r in raw
    ]
    body = args.body_file.read_text()
    result = validate_pr_shape(
        mode=args.mode, files=files, pr_body=body, linked_issue=args.linked_issue,
        plan_dir=args.plan_dir,
    )
    if result.ok:
        print("OK")
        return 0
    for e in result.errors:
        print(f"- {e}")
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
