#!/usr/bin/env python3
"""
Merge per-cluster partial manifests into the single showcase manifest.

In the parallelised showcase workflow each cluster runs on its own
runner and writes a ``manifest.partial.json`` (via
``run_showcase_suite.py --only <key> --out manifest.partial.json``).
This script collects every partial, concatenates their ``clusters``
lists in ``showcase_clusters.CLUSTERS`` declaration order, and
recomputes the ``overall`` block — producing exactly the
``manifest.json`` shape that ``build_showcase_report.py`` and
``send_showcase_email.py`` already consume. The sequential
``run_showcase_suite.py`` full-run output and this merged output are
byte-compatible in schema.

Coverage is recomputed project-wide: ``coverage combine`` merges every
per-runner ``.coverage.<key>`` file, then ``coverage report`` yields
one project-wide percentage that overwrites the (``None``) per-cluster
``coverage_pct`` values — matching the sequential runner's "same
project-wide coverage on every row" behaviour.

Missing partials are a HARD ERROR: if a cluster was selected but its
partial never arrived (lost/failed upload), the gate must NOT silently
pass. The script exits non-zero and names every missing cluster.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from showcase_clusters import CLUSTERS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# Same regex the sequential runner uses to pull the TOTAL row percentage
# out of ``coverage report`` (run_showcase_suite.py:_overall_coverage_pct).
_COVERAGE_TOTAL_RE = re.compile(
    r"^TOTAL\s+\d+\s+\d+(?:\s+\d+\s+\d+)?\s+([\d.]+)%", re.MULTILINE
)


def _load_partials(partials_dir: Path) -> dict[str, dict[str, Any]]:
    """Load every ``manifest.partial*.json`` under ``partials_dir``.

    Returns ``{cluster_key: cluster_row}``. Each partial is expected to
    hold exactly one cluster row (single-cluster ``--only`` run), but we
    tolerate multiple rows per file — every row is indexed by its key.
    Recurses so ``actions/download-artifact`` layouts (one subdir per
    artifact) work without a pre-flatten step.
    """
    rows: dict[str, dict[str, Any]] = {}
    files = sorted(
        glob.glob(str(partials_dir / "**" / "manifest.partial*.json"),
                  recursive=True)
    )
    for path in files:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        for row in manifest.get("clusters", []):
            key = row.get("key")
            if key is None:
                continue
            rows[key] = row
    return rows


def _combine_coverage(coverage_glob: str | None) -> float | None:
    """``coverage combine`` the per-runner data files, then report %.

    ``coverage_glob`` points at the directory holding the downloaded
    ``.coverage.<key>`` files. Returns the project-wide TOTAL percentage,
    or ``None`` if coverage isn't installed / no data files are present.
    """
    if not coverage_glob:
        return None
    data_files = sorted(glob.glob(coverage_glob, recursive=True))
    if not data_files:
        print(f"::warning::No coverage data files matched {coverage_glob}; "
              f"coverage_pct will be null", file=sys.stderr)
        return None
    try:
        # Combine into REPO_ROOT/.coverage. `coverage combine` deletes the
        # inputs by default; copy them in first so the source paths line up
        # with `.coveragerc` (all runners checked out the same commit).
        subprocess.run(
            ["coverage", "combine", "--keep", *data_files],
            cwd=REPO_ROOT, check=False,
        )
        subprocess.run(
            ["coverage", "xml", "--rcfile=.coveragerc", "-o", "coverage.xml"],
            cwd=REPO_ROOT, check=False,
        )
        out = subprocess.run(
            ["coverage", "report", "--rcfile=.coveragerc"],
            cwd=REPO_ROOT, check=False, capture_output=True, text=True,
        )
    except FileNotFoundError:
        return None
    m = _COVERAGE_TOTAL_RE.search(out.stdout)
    return float(m.group(1)) if m else None


def aggregate(
    rows_by_key: dict[str, dict[str, Any]],
    coverage_pct: float | None,
) -> dict[str, Any]:
    """Build the merged manifest from a ``{key: row}`` mapping.

    Rows are emitted in ``CLUSTERS`` declaration order — the single
    source of truth for report row order. A selected-but-missing cluster
    is a hard error (raised by the caller after this returns), so here we
    only assemble what's present. ``overall`` is recomputed from the
    merged rows; ``coverage_pct`` overwrites every row and ``overall``.
    """
    results: list[dict[str, Any]] = []
    for c in CLUSTERS:
        if c.key not in rows_by_key:
            continue
        row = dict(rows_by_key[c.key])
        row["coverage_pct"] = coverage_pct
        results.append(row)

    overall = {
        "ran":      sum(r.get("ran", 0) for r in results),
        "passed":   sum(r.get("passed", 0) for r in results),
        "failed":   sum(r.get("failed", 0) for r in results),
        "errors":   sum(r.get("errors", 0) for r in results),
        "skipped":  sum(r.get("skipped", 0) for r in results),
        "xfailed":  sum(r.get("xfailed", 0) for r in results),
        # wall_s: sum of per-cluster wall times — matches the sequential
        # runner's schema ("total compute time"). Parallel wall-clock is
        # visible in the Actions UI; the report keeps the aggregate figure.
        "wall_s":   round(sum(r.get("wall_s", 0.0) for r in results), 2),
        "clusters_total":   len(results),
        "clusters_passing": sum(1 for r in results
                                if r.get("outcome") == "success"),
        "coverage_pct":     coverage_pct,
        "outcome": "success" if all(r.get("outcome") == "success"
                                    for r in results) else "failure",
    }
    return {
        "generated_at": int(time.time()),
        "overall": overall,
        "clusters": results,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--partials", required=True,
                   help="Directory containing the downloaded "
                        "manifest.partial*.json files (searched recursively).")
    p.add_argument("--out", default="manifest.json",
                   help="Where to write the merged manifest.")
    p.add_argument("--coverage-glob", default=None,
                   help="Glob for the downloaded .coverage.<key> data files "
                        "(e.g. './coverage-data/**/.coverage.*'). When given, "
                        "they are `coverage combine`d and the project-wide "
                        "percentage overwrites every row's coverage_pct.")
    p.add_argument("--expected", default=None,
                   help="Comma-separated cluster keys that MUST be present. "
                        "A missing one is a hard error so a lost partial "
                        "never silently passes the gate. When omitted, no "
                        "presence check is enforced.")
    args = p.parse_args(argv)

    partials_dir = Path(args.partials)
    rows_by_key = _load_partials(partials_dir)

    if args.expected:
        expected = [k.strip() for k in args.expected.split(",") if k.strip()]
        missing = [k for k in expected if k not in rows_by_key]
        if missing:
            print(f"::error::Missing partial manifest(s) for cluster(s): "
                  f"{', '.join(missing)}. A lost partial must not silently "
                  f"pass the gate — failing.", file=sys.stderr)
            return 1

    if not rows_by_key:
        print(f"::error::No partial manifests found under {partials_dir}.",
              file=sys.stderr)
        return 1

    coverage_pct = _combine_coverage(args.coverage_glob)
    manifest = aggregate(rows_by_key, coverage_pct)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    ov = manifest["overall"]
    print(f"Wrote {args.out}: {ov['clusters_passing']}/{ov['clusters_total']} "
          f"clusters passing, coverage {ov['coverage_pct']}%")
    # Always exit 0 on a successful merge — the workflow gates on
    # manifest.overall.outcome, exactly as with the sequential runner.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
