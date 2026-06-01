"""Resolve the source issue + mode for a Copilot-authored PR.

The PR-gate workflow needs three things from a Copilot PR:

* the **mode** (``regression`` / ``bug-repro`` / ``fix-and-test``), so it
  knows which checks to apply;
* the **source issue number** (the user-filed issue that carries the
  ``copilot:<mode>`` label, or the auto-opened ``coverage-task`` issue),
  so it can post comments back to the right place when things go wrong;
* the **kind** of task (``test-bot`` vs ``coverage-task``), so the gate
  knows whether the PR was opened in response to a maintainer's
  test-request (test-bot path) or the Feature-4 coverage gate. A
  coverage-task PR needs extra handling — its base branch must be the
  parent feature branch, not ``lex-app-v2`` — and its originating issue
  carries the ``coverage-task`` label instead of a ``copilot:<mode>`` one,
  so it is mapped to ``regression`` (test-only, multi-file, auto-merge).

The previous inline-bash resolver only matched ``Fixes #N`` in the PR
body. Copilot's coding agent doesn't reliably emit that line — it links
the issue via GitHub's "Development" sidebar instead, which surfaces as
``closingIssuesReferences`` in the GraphQL API but is invisible to a
plain text grep.

Resolver chain (first match wins):

1. ``closingIssuesReferences`` on the PR — the canonical link Copilot
   sets. Returns the **task issue** (``[copilot-task] …``), not the
   source issue, so we still have to dereference.
2. Body grep for any close-keyword form (``Fixes / Closes / Resolves
   #N``, case-insensitive, optional URL form).
3. Body grep for any bare ``#N`` — last resort.

For each candidate we then walk:

* if it has a ``copilot:<mode>`` label → it's the source issue, done
  (kind ``test-bot``);
* else if it has the ``coverage-task`` label → it's a Feature-4 coverage
  task, done (mode ``regression``, kind ``coverage-task``);
* else if its body says ``Assembled from issue #M`` → fetch #M and try
  the label check on that.

This means we work whether Copilot links the task issue (the common
case), the source issue (the documented case), or nothing at all (we
fall back to body text).

Usage::

    python copilot_discover_mode.py \\
        --repo owner/name --pr 520 \\
        > $GITHUB_OUTPUT

prints three ``key=value`` lines (``mode``, ``issue``, ``kind``) on
success, exits non-zero with a human-readable explanation on failure.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Optional


_MODE_LABEL_RE = re.compile(r"^copilot:(regression|bug-repro|fix-and-test)$")
# Feature-4 coverage-task issues carry this label instead of a
# copilot:<mode> one. They are treated as regression PRs (test-only,
# multi-file allowed, auto-merge) but flagged with kind="coverage-task"
# so the gate can retarget the PR's base onto the parent feature branch.
_COVERAGE_TASK_LABEL = "coverage-task"
_CLOSE_KEYWORD_RE = re.compile(
    r"(?i)(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)"
)
_BARE_HASH_RE = re.compile(r"#(\d+)")
_ASSEMBLED_FROM_RE = re.compile(
    r"Assembled from issue #(\d+)", re.IGNORECASE
)


def _gh(args: list[str]) -> str:
    """Call ``gh`` and return stdout; raise on non-zero."""
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def _issue_view(repo: str, number: int) -> Optional[dict]:
    """Fetch an issue's labels + body. Returns ``None`` if not found."""
    try:
        raw = _gh(
            [
                "issue", "view", str(number),
                "--repo", repo,
                "--json", "body,labels",
            ]
        )
    except RuntimeError:
        return None
    return json.loads(raw)


def _mode_and_kind_from_labels(
    labels: list[dict],
) -> tuple[Optional[str], Optional[str]]:
    """Resolve an issue's labels to ``(mode, kind)``.

    * a ``copilot:<mode>`` label → ``(<mode>, "test-bot")``;
    * the ``coverage-task`` label → ``("regression", "coverage-task")``
      (the coverage gate's PRs are test-only/multi-file/auto-merge, i.e.
      the regression contract);
    * neither → ``(None, None)``.

    A ``copilot:<mode>`` label wins over ``coverage-task`` on the off
    chance both are present, so an explicitly-moded issue is never
    silently downgraded to regression.
    """
    for label in labels:
        m = _MODE_LABEL_RE.match(label.get("name", ""))
        if m:
            return m.group(1), "test-bot"
    for label in labels:
        if label.get("name", "") == _COVERAGE_TASK_LABEL:
            return "regression", "coverage-task"
    return None, None


def _closing_issue_references(repo: str, pr: int) -> list[int]:
    """Return PR's ``closingIssuesReferences`` issue numbers."""
    owner, name = repo.split("/", 1)
    query = (
        '{ repository(owner:"' + owner + '", name:"' + name + '") '
        '{ pullRequest(number: ' + str(pr) + ') '
        '{ closingIssuesReferences(first:10) { nodes { number } } } } }'
    )
    try:
        raw = _gh(["api", "graphql", "-f", f"query={query}"])
    except RuntimeError:
        return []
    data = json.loads(raw)
    try:
        nodes = data["data"]["repository"]["pullRequest"][
            "closingIssuesReferences"]["nodes"]
    except (KeyError, TypeError):
        return []
    return [n["number"] for n in nodes if "number" in n]


def _candidates_from_pr_body(body: str) -> list[int]:
    """Extract issue numbers referenced by the PR body, ordered by reliability."""
    seen: list[int] = []
    def _push(n: int) -> None:
        if n not in seen:
            seen.append(n)

    for m in _CLOSE_KEYWORD_RE.finditer(body or ""):
        _push(int(m.group(1)))
    for m in _BARE_HASH_RE.finditer(body or ""):
        _push(int(m.group(1)))
    return seen


def resolve(repo: str, pr: int) -> tuple[str, int, str]:
    """Return ``(mode, source_issue_number, kind)``.

    ``kind`` is ``"test-bot"`` for maintainer test-request issues and
    ``"coverage-task"`` for Feature-4 coverage tasks. Walks the resolver
    chain documented in the module docstring. Raises ``RuntimeError`` with
    a human-readable message if no candidate yields a ``copilot:<mode>``
    or ``coverage-task`` label, even after dereferencing task issues.
    """
    # Step 1 — gather candidates from every available signal.
    candidates: list[int] = list(_closing_issue_references(repo, pr))

    pr_body_raw = _gh(
        ["pr", "view", str(pr), "--repo", repo, "--json", "body"]
    )
    pr_body = json.loads(pr_body_raw).get("body") or ""
    for n in _candidates_from_pr_body(pr_body):
        if n not in candidates:
            candidates.append(n)

    if not candidates:
        raise RuntimeError(
            "PR has no closingIssuesReferences and no #N in body — "
            "cannot determine source issue."
        )

    # Step 2 — for each candidate, check labels; if it's a task issue,
    # dereference via "Assembled from issue #M".
    visited: set[int] = set()
    for cand in candidates:
        # Walk at most two hops (task → source). Anything deeper is a
        # signal of breakage we shouldn't silently follow.
        for _hop in range(2):
            if cand in visited:
                break
            visited.add(cand)
            issue = _issue_view(repo, cand)
            if issue is None:
                break

            mode, kind = _mode_and_kind_from_labels(issue.get("labels") or [])
            if mode:
                return mode, cand, kind

            # No mode / coverage-task label. If the body declares it was
            # assembled from a parent, hop to that and retry.
            m = _ASSEMBLED_FROM_RE.search(issue.get("body") or "")
            if not m:
                break
            next_cand = int(m.group(1))
            if next_cand in visited:
                break
            cand = next_cand

    raise RuntimeError(
        f"None of the candidate issues ({candidates}) carry a copilot:<mode> "
        "or coverage-task label, and none dereference to a source issue that "
        "does. The PR is either not Copilot-triggered or the source issue lost "
        "its label."
    )


def _cli() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--pr", required=True, type=int, help="PR number")
    args = p.parse_args()

    try:
        mode, issue, kind = resolve(args.repo, args.pr)
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    # GitHub Actions step output format.
    print(f"mode={mode}")
    print(f"issue={issue}")
    print(f"kind={kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
