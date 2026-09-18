#!/usr/bin/env python3
"""Publish a release's approved note to quackback, from a workflow.

The body is read from the GitHub release rather than regenerated: by this point
a human has reviewed and possibly edited it, and the help centre should carry
what they approved, not what the drafter wrote.

Exits 0 whatever happens. See release_notes/quackback.py for why.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_notes import quackback  # noqa: E402


def release_body(tag: str, repo: str) -> str | None:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "body", "--jq", ".body"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"could not read the release body for {tag}: "
              f"{result.stderr.strip()[:200]}", file=sys.stderr)
        return None
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)

    body = release_body(args.tag, args.repo)
    if not body or not body.strip():
        print(f"no release body for {args.tag} — nothing to publish", file=sys.stderr)
        return 0

    print(quackback.publish_from_env(args.tag, body))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
