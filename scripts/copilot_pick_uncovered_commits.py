"""Find recent merged commits in ``lex-app-v2`` that lack test coverage.

Walks ``git log`` for the configured window, drops commits that already
shipped tests (or that are pure docs / bot-authored noise), and prints
one ready-to-paste ``[copilot-test]`` issue body per surviving commit.

Operator workflow:

    python3 scripts/copilot_pick_uncovered_commits.py --days 14
    # → review the suggestions, pick a few, paste into the issue form.

The output deliberately mirrors the issue-form fields
(``.github/ISSUE_TEMPLATE/copilot-test-request.yml``) so the operator
can drop each block into the form with no rephrasing.

Purely local — no GitHub API calls, no network. The script never files
issues itself; that is the operator's call.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


# Commit authors whose merges we never retrofit tests for. The bot's own
# merges (regression PRs etc.) already ship the test they're meant to;
# retrofitting on top would be both noise and an infinite-loop hazard if
# someone later wires this into automation.
_BOT_AUTHORS = frozenset({"Copilot", "copilot-swe-agent[bot]", "copilot-swe-agent"})

# A commit that already touches any of these path prefixes is treated as
# "ships its own tests" and skipped. Mirrors the cluster folder layout
# under ``lex/test_project/tests/``.
_TEST_PATH_PREFIX = "lex/test_project/tests/"

# A commit whose every changed file matches one of these globs is treated
# as docs-only and skipped (no source behaviour to test).
_DOCS_ONLY_SUFFIXES = (".md", ".rst", ".txt")
_DOCS_ONLY_PREFIXES = ("docs/", ".github/ISSUE_TEMPLATE/", ".github/PULL_REQUEST_TEMPLATE")

# Source files we want a regression test for. The Feature 4 coverage check
# enforces this on every PR going forward; this script applies the same
# filter retroactively to past commits.
_SOURCE_PREFIX = "lex/lex_app/"

# Allowlist mirrors §7.3 of the spec — files where coverage is not
# meaningful (config, migrations, etc.). Kept in sync with the workflow
# so retrospective picks match what the PR check enforces going forward.
_ALLOWLIST_SUFFIXES = ("__init__.py", "apps.py")
_ALLOWLIST_PREFIXES_UNDER_SOURCE = ("migrations/",)
_ALLOWLIST_NAME_PREFIXES = ("settings",)  # settings.py, settings_dev.py, ...


@dataclass
class Commit:
    sha: str
    subject: str
    author: str
    files: list[str] = field(default_factory=list)


def _git(*args: str, cwd: Path) -> str:
    """Run a git command, returning stdout. Stderr is forwarded for diagnostics."""
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout


def _resolve_repo(start: Path) -> Path:
    """Walk up from ``start`` to the enclosing git working tree."""
    out = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(out.stdout.strip())


def _is_docs_only(files: list[str]) -> bool:
    if not files:
        return False
    for f in files:
        if any(f.startswith(p) for p in _DOCS_ONLY_PREFIXES):
            continue
        if f.endswith(_DOCS_ONLY_SUFFIXES):
            continue
        return False
    return True


def _is_test_file(path: str) -> bool:
    """A file counts as a test if it's under the cluster tree OR its
    basename starts with ``test_``. Spec §5.2 says ``test_*.py``; we
    apply that broadly so app-internal tests (e.g. management commands)
    also count as coverage."""
    if path.startswith(_TEST_PATH_PREFIX):
        return True
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") and name.endswith(".py")


def _touches_tests(files: list[str]) -> bool:
    return any(_is_test_file(f) for f in files)


def _is_allowlisted_source(path: str) -> bool:
    """Replicates the Feature 4 §7.3 allowlist for lex/lex_app/** files."""
    if not path.startswith(_SOURCE_PREFIX):
        return False
    rel = path[len(_SOURCE_PREFIX):]
    if any(rel.startswith(pfx) for pfx in _ALLOWLIST_PREFIXES_UNDER_SOURCE):
        return True
    name = rel.rsplit("/", 1)[-1]
    if name in _ALLOWLIST_SUFFIXES:
        return True
    # name-prefix match handles settings.py, settings_dev.py, settings_local.py
    if any(name.startswith(pfx) for pfx in _ALLOWLIST_NAME_PREFIXES) and name.endswith(".py"):
        return True
    return False


def _source_files_worth_testing(files: list[str]) -> list[str]:
    return [
        f for f in files
        if f.startswith(_SOURCE_PREFIX)
        and f.endswith(".py")
        and not _is_test_file(f)
        and not _is_allowlisted_source(f)
    ]


def collect(repo: Path, branch: str, days: int) -> list[Commit]:
    """Read merges from ``branch`` in the last ``days``, returning structured rows."""
    # %x09 = TAB. Using TAB-separated fields keeps subjects (which may
    # contain commas, colons, quotes) safe to split.
    log_fmt = "%H%x09%s%x09%an"
    raw = _git(
        "log", branch, f"--since={days} days ago", "--merges",
        f"--pretty=format:{log_fmt}",
        cwd=repo,
    )

    commits: list[Commit] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        sha, subject, author = parts[0], parts[1], parts[2]
        # --name-only on a merge commit reports the merge's combined diff
        # against its first parent — exactly what we want (files brought
        # in by the merge).
        names_raw = _git(
            "show", "--no-renames", "--first-parent", "--name-only",
            "--pretty=format:", sha,
            cwd=repo,
        )
        files = [f for f in names_raw.splitlines() if f.strip()]
        commits.append(Commit(sha=sha, subject=subject, author=author, files=files))
    return commits


def filter_uncovered(commits: list[Commit]) -> list[Commit]:
    """Keep only commits worth retrofitting a test for."""
    keep: list[Commit] = []
    for c in commits:
        if c.author in _BOT_AUTHORS:
            continue
        if _touches_tests(c.files):
            continue
        if _is_docs_only(c.files):
            continue
        if not _source_files_worth_testing(c.files):
            continue
        keep.append(c)
    return keep


def render(commit: Commit) -> str:
    """Emit a ready-to-paste issue-form block for one commit.

    Field names match ``.github/ISSUE_TEMPLATE/copilot-test-request.yml``.
    The operator pastes the body into the form's textarea, then ticks the
    Mode dropdown to ``regression`` by hand (forms don't accept pre-set
    dropdown values via plain markdown).
    """
    src_files = _source_files_worth_testing(commit.files)
    files_line = ", ".join(src_files) if src_files else "(none — review by hand)"
    # Short SHA + subject is what humans recognise; keeps the operator
    # oriented when they cross-check against `git log` before filing.
    short = commit.sha[:12]

    return f"""\
--- BEGIN SUGGESTION ({short}) ---

# Mode (set in form dropdown): regression

# Behaviour description:
Codify the behaviour introduced by commit {short} ("{commit.subject}").
Write a regression test that exercises the contract this change put in
place, so a future edit that breaks it fails the suite.

# Reproducer / steps:
(not required for regression — derive scenarios from the commit diff and
the test-plan conventions)

# Cluster hint:
(blank — let Copilot route via test-clusters.md)

# Files involved:
{files_line}

# Publish on merge: (your call — tick if you want this retrofit to ship)

--- END SUGGESTION ({short}) ---
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pick recent commits lacking test coverage and emit issue-form suggestions.",
    )
    parser.add_argument(
        "--days", type=int, default=14,
        help="Look back this many days of merges (default: 14).",
    )
    parser.add_argument(
        "--branch", default="lex-app-v2",
        help="Branch to walk (default: lex-app-v2).",
    )
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(),
        help="Path inside the repo to scan (default: cwd).",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Cap the number of suggestions emitted (0 = no cap).",
    )
    args = parser.parse_args()

    if shutil.which("git") is None:
        print("error: `git` is not on PATH", file=sys.stderr)
        return 2

    try:
        repo = _resolve_repo(args.repo)
    except subprocess.CalledProcessError:
        print(f"error: {args.repo} is not inside a git repository", file=sys.stderr)
        return 2

    commits = collect(repo, args.branch, args.days)
    uncovered = filter_uncovered(commits)

    if args.limit > 0:
        uncovered = uncovered[: args.limit]

    print(
        f"# {len(uncovered)} commit(s) without test coverage in the last "
        f"{args.days} day(s) on {args.branch} "
        f"(scanned {len(commits)} merges).",
        file=sys.stderr,
    )

    if not uncovered:
        print(
            "# Nothing to retrofit — every recent merge already shipped tests "
            "or only touched docs/config.",
            file=sys.stderr,
        )
        return 0

    for c in uncovered:
        print(render(c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
