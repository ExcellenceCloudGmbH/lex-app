#!/usr/bin/env python3
"""Ensure a DRAFT GitHub release exists for a version and point it at a
validated commit — the heart of the "draft staging pointer" release flow.

Why a draft? A draft release stores a version *name* and a *target
commitish*, but GitHub does NOT mint the git tag until the draft is
published. That property is the whole point:

  * We can keep ONE draft for the next version and re-point its
    ``target_commitish`` at the latest validated commit as many times as we
    like — there is no git tag yet, so there is no "duplicate tag" wall and
    no stale tag when code is fixed.
  * When the operator finally publishes the draft as a full (non-pre-release)
    release, GitHub mints the tag at exactly that commit and fires
    ``release: released`` → pip_publish.yml ships precisely what was
    validated.

Decision table (given the existing release for <version>, if any):

  * none exists          -> create a new DRAFT (prerelease=false) at <sha>
  * exists & is a draft  -> re-point its target_commitish to <sha>
  * exists & published    -> error: that version already shipped; pick a new one

Usage:
  stage_release_draft.py --repo OWNER/REPO --version vX.Y.ZrcN --sha <commit>

Requires GH_TOKEN in the environment with contents:write.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Optional, Tuple

CREATE = "create"
RETARGET = "retarget"
ERROR = "error"


def decide_action(release: Optional[dict]) -> Tuple[str, str]:
    """Pure decision: what to do with the (possibly missing) existing release.

    Kept free of any I/O so it can be unit-tested without a network or gh.
    """
    if release is None:
        return CREATE, "no release for this version yet — creating a fresh draft"
    if release.get("draft"):
        return RETARGET, f"draft release #{release.get('id')} exists — re-pointing its target"
    return ERROR, (
        "release for this version is already published (draft=false) — "
        "a shipped version cannot be re-staged; pick a new version"
    )


def _gh_json(args: list) -> object:
    out = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out) if out.strip() else None


def find_release(repo: str, version: str) -> Optional[dict]:
    # The /releases/tags/{tag} endpoint hides drafts (no tag exists yet),
    # so list all releases and match on tag_name instead.
    releases = _gh_json(["api", "--paginate", f"repos/{repo}/releases"]) or []
    for rel in releases:
        if rel.get("tag_name") == version:
            return rel
    return None


def create_draft(repo: str, version: str, sha: str) -> None:
    subprocess.run(
        [
            "gh", "api", "-X", "POST", f"repos/{repo}/releases",
            "-f", f"tag_name={version}",
            "-f", f"target_commitish={sha}",
            "-f", f"name={version}",
            "-F", "draft=true",
            "-F", "prerelease=false",
            "-f",
            "body=Release candidate staged by stage_release_candidate.yml. "
            "Publish this draft as a full release to ship it to PyPI.",
        ],
        check=True,
    )


def retarget(repo: str, release_id: int, sha: str) -> None:
    subprocess.run(
        [
            "gh", "api", "-X", "PATCH", f"repos/{repo}/releases/{release_id}",
            "-f", f"target_commitish={sha}",
        ],
        check=True,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="OWNER/REPO")
    p.add_argument("--version", required=True, help="tag name, e.g. v2.0.0rc185")
    p.add_argument("--sha", required=True, help="validated commit to target")
    args = p.parse_args(argv)

    rel = find_release(args.repo, args.version)
    action, msg = decide_action(rel)
    print(f"{action}: {msg}")

    if action == CREATE:
        create_draft(args.repo, args.version, args.sha)
        print(f"Created draft {args.version} targeting {args.sha}.")
    elif action == RETARGET:
        retarget(args.repo, rel["id"], args.sha)
        print(f"Re-pointed draft {args.version} to {args.sha}.")
    else:
        print(f"::error::{msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
