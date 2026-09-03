#!/usr/bin/env python3
"""Publish a release's approved note to quackback and to Linear Pulse.

Two destinations, two formats, one source. The body is read from the GitHub
release rather than regenerated: by now a human has reviewed and possibly
edited it, and both destinations should carry what they approved.

  quackback     a help-centre article for customers — the full note
  Linear Pulse  a project update for colleagues — headlines and what to do

Exits 0 whatever happens, and reports each destination separately so a failure
in one is visible without hiding the other.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_notes import linear_pulse, quackback  # noqa: E402

# Written onto the release by pip_publish.yml when it vendors the frontend.
FRONTEND_MARKER = re.compile(r"<!--\s*lex:frontend-version\s+(?P<version>[^\s>]+)\s*-->")


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
    parser.add_argument("--only", choices=["quackback", "pulse"],
                        help="Publish to one destination only.")
    args = parser.parse_args(argv)

    body = release_body(args.tag, args.repo)
    if not body or not body.strip():
        print(f"no release body for {args.tag} — nothing to publish", file=sys.stderr)
        return 0

    match = FRONTEND_MARKER.search(body)
    frontend = match["version"] if match else None

    if args.only in (None, "quackback"):
        print(f"quackback: {quackback.publish_from_env(args.tag, body)}")
    if args.only in (None, "pulse"):
        print(f"linear pulse: {linear_pulse.publish_from_env(args.tag, body, repo=args.repo, frontend=frontend)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
