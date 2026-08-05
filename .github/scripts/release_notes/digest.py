#!/usr/bin/env python3
"""Turn commit ranges into a structured digest of changes.

The digest is the single source of truth for both renderers: the mechanical
changelog and the LLM-drafted business note. Keeping one digest means the two
artifacts can never describe different sets of changes.
"""

from __future__ import annotations

import json
import re
import sys
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CONVENTIONAL_TYPES = (
    "feat", "fix", "docs", "style", "refactor",
    "perf", "test", "build", "ci", "chore", "revert",
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# ASCII unit separator: cannot appear in a commit subject, unlike any
# punctuation a human might type.
_FIELD_SEP = "\x1f"

_SUBJECT_RE = re.compile(
    r"^(?P<type>" + "|".join(CONVENTIONAL_TYPES) + r")"
    r"(?:\((?P<scope>[^)]+)\))?"
    r"(?P<breaking>!)?: "
    r"(?P<subject>.+)$"
)


@dataclass(frozen=True)
class Parsed:
    type: str
    scope: str | None
    breaking: bool
    subject: str


def parse_subject(subject: str) -> Parsed:
    """Split a commit or PR subject into its conventional parts.

    A subject that does not conform is classified `other` with its text
    preserved. Discarding it would lose real changes: 43% of non-merge
    commits in this repository do not conform, and some of them ship
    user-visible work.
    """
    subject = (subject or "").strip()
    match = _SUBJECT_RE.match(subject)
    if match is None:
        return Parsed(type="other", scope=None, breaking=False, subject=subject)
    return Parsed(
        type=match.group("type"),
        scope=match.group("scope"),
        breaking=match.group("breaking") == "!",
        subject=match.group("subject"),
    )


@dataclass(frozen=True)
class Commit:
    """One commit as read from git, before enrichment."""

    sha: str
    subject: str
    pr_number: int | None = None
    pr_title: str | None = None


# Commits that are real work but say nothing about the shipped product.
_MERGE_PREFIXES = ("Merge pull request ", "Merge branch ", "Merge remote-tracking ")
_BUNDLE_PREFIX = "build(frontend): update bundle"

# Work on the toolchain rather than on the product. Kept in the digest — the
# changelog is a record of everything that shipped — but flagged so the note
# drafter can leave it out. Without this flag a model reads
# `feat(release-notes): pluggable model provider` as a LEX feature and writes
# "LEX can now connect to Gemini", which is what v2.1.7rc1 published.
INTERNAL_TYPES = frozenset({"ci", "build", "chore", "test", "docs"})
INTERNAL_SCOPES = frozenset({
    "release-notes", "test-plan", "ci", "gate", "showcase", "plan", "spec",
    "setup-with-ai", "agent", "copilot",
})


def is_internal(type_: str, scope: str | None) -> bool:
    """True when a change is about how we build LEX, not about LEX."""
    if type_ in INTERNAL_TYPES:
        return True
    return (scope or "").strip().lower() in INTERNAL_SCOPES


def is_noise(subject: str) -> bool:
    """True for commits that must never reach a changelog."""
    subject = (subject or "").strip()
    if not subject:
        return True
    if subject.startswith(_MERGE_PREFIXES):
        return True
    if subject.startswith(_BUNDLE_PREFIX):
        return True
    return False


def _entry(commit: Commit, component: str) -> dict:
    # The PR title leads when there is one: PR titles in this repository are
    # consistently well-formed, while only 57% of non-merge commit subjects
    # conform. Enrichment is what makes the input usable at that number.
    subject = commit.pr_title or commit.subject
    parsed = parse_subject(subject)
    return {
        "sha": commit.sha,
        "component": component,
        "type": parsed.type,
        "scope": parsed.scope,
        "breaking": parsed.breaking,
        "subject": parsed.subject,
        "pr_number": commit.pr_number,
        "internal": is_internal(parsed.type, parsed.scope),
    }


def build_digest(
    tag: str,
    previous_tag: str | None,
    backend_commits: list[Commit],
    frontend_commits: list[Commit],
) -> dict:
    """Assemble the digest both renderers consume.

    One entry per pull request, not per commit. Enrichment gives every commit
    in a PR that PR's title, so a branch of seventeen commits otherwise renders
    as seventeen identical lines — which is exactly what v2.1.7rc1 shipped.
    Commits that came in without a PR are kept individually, since each is the
    only record of itself.
    """
    changes: list[dict] = []
    seen_prs: set[tuple[str, int]] = set()

    for component, commits in (("backend", backend_commits), ("frontend", frontend_commits)):
        for commit in commits:
            if is_noise(commit.pr_title or commit.subject):
                continue
            if commit.pr_number is not None:
                # Scoped by component: backend #12 and frontend #12 are
                # different pull requests in different repositories.
                key = (component, commit.pr_number)
                if key in seen_prs:
                    continue
                seen_prs.add(key)
            changes.append(_entry(commit, component))

    return {"tag": tag, "previous_tag": previous_tag, "changes": changes}


def _run_log(from_ref: str | None, to_ref: str) -> str:
    spec = f"{from_ref}..{to_ref}" if from_ref else to_ref
    result = subprocess.run(
        ["git", "log", "--no-merges", f"--pretty=%h{_FIELD_SEP}%s", spec],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def collect_commits(
    from_ref: str | None,
    to_ref: str,
    *,
    run_log: Callable[[str | None, str], str] = _run_log,
) -> list[Commit]:
    """Read `from_ref..to_ref` into Commit records."""
    raw = run_log(from_ref, to_ref)
    commits: list[Commit] = []
    for line in raw.splitlines():
        if _FIELD_SEP not in line:
            continue
        sha, subject = line.split(_FIELD_SEP, 1)
        commits.append(Commit(sha=sha.strip(), subject=subject.strip()))
    return commits


# Prefer a merged PR, but fall back to any PR the commit is associated with.
#
# Filtering on `merged_at` alone loses real titles: commits reach lex-app-v2
# while their tracking PR is still open, so `2dfc626`, `8d7705a` and `6f9e67a`
# all sit on the branch under PR #692 with `merged_at: null`. Taking `.[0]`
# *before* the filter made that produce nothing at all — indistinguishable
# from "this commit has no PR" — which is exactly the enrichment the 57%
# commit conformance depends on.
_PR_JQ = (
    "[.[] | select(.merged_at != null)] + . "
    "| .[0] | select(. != null) | [.number, .title] | @json"
)


def _lookup_pr(sha: str) -> tuple[int, str] | None:
    """The PR a commit belongs to, via the GitHub API. Merged ones win."""
    result = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}/pulls",
         "--jq", _PR_JQ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    number, title = json.loads(result.stdout.strip())
    return int(number), title


def enrich_with_prs(
    commits: list[Commit],
    *,
    lookup: Callable[[str], tuple[int, str] | None] = _lookup_pr,
) -> list[Commit]:
    """Attach PR number and title where a commit came through a PR.

    A lookup failure is not fatal. Release notes are worth having with
    partial enrichment; they are not worth failing a release for.

    The tally is logged because a *systemic* failure — a missing
    `pull-requests: read` permission, say — degrades every entry to its raw
    commit subject while looking exactly like "these commits had no PRs".
    A count makes that visible instead of silent.
    """
    enriched: list[Commit] = []
    hits = 0
    for commit in commits:
        try:
            found = lookup(commit.sha)
        except Exception:
            found = None
        if found is None:
            enriched.append(commit)
        else:
            hits += 1
            number, title = found
            enriched.append(
                Commit(sha=commit.sha, subject=commit.subject, pr_number=number, pr_title=title)
            )
    if commits:
        print(f"PR enrichment: {hits}/{len(commits)} commits matched a pull request.",
              file=sys.stderr)
    return enriched
