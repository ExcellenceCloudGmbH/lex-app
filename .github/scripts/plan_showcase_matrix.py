#!/usr/bin/env python3
"""
Compute the GitHub Actions matrix for the showcase suite.

Parses the same cluster CSV selector grammar as
``run_showcase_suite.py --only`` (each entry is ``<cluster_key>`` or
``<cluster_key>:<test_suffix>``) and emits a JSON array of
``{"key": ..., "test_suffix": ...}`` objects on stdout — one per
selected cluster, in ``showcase_clusters.CLUSTERS`` declaration order.

The showcase workflow's ``plan`` job consumes this array as its
``strategy.matrix.cluster`` so that one matrix entry (one runner) runs
per cluster. The selector grammar lives in ``run_showcase_suite.py``
(``parse_selectors``); this script imports it so the matrix job and the
runner can never drift.

A blank/empty selector (or omitting the argument) expands to the
default set: every cluster in ``CLUSTERS`` declaration order, each with
no test suffix — matching ``run_showcase_suite.py``'s default.

Unknown cluster keys are dropped (they don't correspond to any real
cluster folder) but a ``::warning::`` is emitted to stderr, mirroring
the runner's handling.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_showcase_suite import parse_selectors  # noqa: E402
from showcase_clusters import CLUSTERS  # noqa: E402


def build_matrix(only: str | None) -> list[dict[str, str | None]]:
    """Return the ordered matrix entries for a selector string.

    Filters to selected keys, preserving ``CLUSTERS`` declaration order
    (so the matrix — and therefore the report row order after
    aggregation — is stable regardless of how the caller ordered the
    CSV). Unknown keys are dropped with a warning to stderr.
    """
    selectors = parse_selectors(only)
    known = {c.key for c in CLUSTERS}
    unknown = [k for k in selectors if k not in known]
    if unknown:
        print(
            f"::warning::Unknown cluster keys in selector: "
            f"{', '.join(sorted(unknown))}",
            file=sys.stderr,
        )
    return [
        {"key": c.key, "test_suffix": selectors[c.key]}
        for c in CLUSTERS
        if c.key in selectors
    ]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "clusters",
        nargs="?",
        default=None,
        help="Comma-separated cluster selector (same grammar as "
             "run_showcase_suite.py --only). Blank/omitted → default set "
             "(all clusters).",
    )
    args = p.parse_args(argv)
    matrix = build_matrix(args.clusters)
    json.dump(matrix, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
