"""Bump an rc-suffixed tag by one. Refuses anything that isn't already an rc."""

from __future__ import annotations

import argparse
import re
import sys

_RC_RE = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)rc(?P<n>\d+)$")


def compute_next_rc(tag: str) -> str:
    m = _RC_RE.match(tag or "")
    if not m:
        raise ValueError(
            f"not an rc tag: {tag!r} — expected vX.Y.ZrcN (e.g. v2.0.0rc124)"
        )
    next_n = int(m.group("n")) + 1
    return f"v{m.group('base')}rc{next_n}"


def _cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True, help="The latest existing rc tag.")
    args = parser.parse_args()
    print(compute_next_rc(args.current))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
