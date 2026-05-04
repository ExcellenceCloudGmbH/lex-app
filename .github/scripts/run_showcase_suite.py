#!/usr/bin/env python3
"""
Run the Platform Health showcase suite and emit a JSON manifest.

For every cluster declared in ``showcase_clusters.CLUSTERS`` this
script:

  1. runs the test label ``lex.test_project.tests.<key>`` through the
     Django test runner, under ``coverage run -a`` so coverage
     accumulates across clusters;
  2. parses Django's trailing summary from stderr to extract
     ``passed``, ``failed``, ``errors``, ``skipped`` and ``xfailed``
     counts;
  3. runs ``coverage report --include <cluster globs>`` to compute a
     cluster-scoped coverage percentage (if any globs are configured).

At the end the script writes a single JSON manifest that
``build_showcase_report.py`` consumes to render the report.

The script **always exits 0**. The orchestrating workflow is
responsible for inspecting ``overall.failed`` in the manifest to
decide whether to gate the downstream pipeline — this is deliberate
so that the email step ("always send") always has a report to build.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Allow running either as ``python .github/scripts/run_showcase_suite.py``
# or via ``python -m``.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from showcase_clusters import (  # noqa: E402
    CLUSTERS,
    Cluster,
    RELEASE_GATE_TOKEN,
    release_gate_keys,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DJANGO_ENV = {
    "DJANGO_SETTINGS_MODULE": "lex_app.settings",
    "DATABASE_DEPLOYMENT_TARGET": "default",
    "CELERY_ACTIVE": "False",
}


# ── Output parsing ──────────────────────────────────────────────────
_SUMMARY_RE = re.compile(r"Ran\s+(\d+)\s+tests?\s+in\s+([\d.]+)s", re.MULTILINE)
_DETAIL_RE = re.compile(
    r"(?:FAILED|OK)\s*(?:\((?P<kv>[^)]*)\))?", re.MULTILINE
)


def _parse_summary(stderr: str) -> dict[str, int | float | str]:
    """
    Parse Django's test-runner trailing summary::

        Ran 23 tests in 4.512s

        OK
        OK (skipped=2)
        FAILED (failures=1, errors=0, skipped=2, expected failures=1)
    """
    ran = 0
    wall_s = 0.0
    kv: dict[str, int] = {}
    ok = False

    m = _SUMMARY_RE.search(stderr)
    if m:
        ran = int(m.group(1))
        wall_s = float(m.group(2))

    # The detail line — OK or FAILED followed by an optional (k=v, …)
    m = _DETAIL_RE.search(stderr)
    if m:
        ok = stderr.rfind("\nOK") > stderr.rfind("\nFAILED")
        if m.group("kv"):
            for part in m.group("kv").split(","):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    try:
                        kv[k.strip()] = int(v.strip())
                    except ValueError:
                        pass

    failures = kv.get("failures", 0)
    errors = kv.get("errors", 0)
    skipped = kv.get("skipped", 0)
    xfailed = kv.get("expected failures", 0)
    # "ran" includes skipped and expectedFailure in Django's count;
    # passed = ran - (failures + errors + skipped + xfailed).
    passed = max(0, ran - failures - errors - skipped - xfailed)

    return {
        "ran": ran,
        "passed": passed,
        "failed": failures,
        "errors": errors,
        "skipped": skipped,
        "xfailed": xfailed,
        "wall_s": wall_s,
        "outcome": "success" if (ok and failures == 0 and errors == 0) else "failure",
    }


# ── Coverage ────────────────────────────────────────────────────────
_COVERAGE_TOTAL_RE = re.compile(
    r"^TOTAL\s+\d+\s+\d+(?:\s+\d+\s+\d+)?\s+([\d.]+)%", re.MULTILINE
)


def _per_cluster_coverage_from_contexts(
    cluster_keys: list[str],
) -> dict[str, float | None]:
    """Compute per-cluster coverage using ``coverage.py``'s per-test
    contexts (enabled via ``dynamic_context = test_function`` in
    ``.coveragerc``).

    The ratio answers: **of the framework lines that actually run
    during any test, what fraction did this cluster execute?**

    Why both sides are context-filtered
    ------------------------------------
    A naive "% of executable lines in files the cluster touched" is
    dominated by module-level imports — a Django app loads the same
    thousands of module-level lines at boot under every cluster, so
    every cluster's denominator is roughly identical and the numbers
    converge on the framework-wide total. That's technically true
    but useless for differentiating clusters.

    Contexts fix this. With ``dynamic_context = test_function``:

    * module-level / import-time lines carry only the empty context
      (``""``);
    * lines executed during a test carry a context like
      ``lex.test_project.tests.<key>.<module>.<TestClass>.<method>|run``.

    So we define:

    * **universe** = the set of lines that have at least one
      *non-empty* context — i.e. lines that actually ran inside some
      test, not just at import time.
    * **cluster hits** = lines whose context set contains at least one
      entry starting with ``lex.test_project.tests.<key>.``.

    The percentage is ``cluster_hits / universe`` over all files the
    cluster touched. A cluster owning a small, well-tested slice can
    legitimately score high while another cluster exercising a huge
    but partially-tested surface scores lower — the numbers rank
    clusters by *how thoroughly* they cover the code paths they reach.
    """
    try:
        import coverage as _cov_mod  # type: ignore
    except ImportError:
        return {k: None for k in cluster_keys}

    cov = _cov_mod.Coverage(
        data_file=str(REPO_ROOT / ".coverage"),
        config_file=str(REPO_ROOT / ".coveragerc"),
    )
    try:
        cov.load()
    except Exception:
        return {k: None for k in cluster_keys}

    data = cov.get_data()
    prefixes = {k: f"lex.test_project.tests.{k}." for k in cluster_keys}
    counts: dict[str, tuple[int, int]] = {k: (0, 0) for k in cluster_keys}

    for filename in data.measured_files():
        ctxs_by_line = data.contexts_by_lineno(filename) or {}
        if not ctxs_by_line:
            continue

        # Universe: lines with at least one NON-EMPTY context — i.e.
        # lines exercised inside at least one test (excludes import-
        # time module-level noise, which carries only "").
        universe_lines: set[int] = {
            ln for ln, ctxs in ctxs_by_line.items()
            if any(c for c in ctxs)  # any truthy context
        }
        if not universe_lines:
            continue

        for key, prefix in prefixes.items():
            cluster_hits = {
                ln for ln, ctxs in ctxs_by_line.items()
                if ln in universe_lines
                and any(c and c.startswith(prefix) for c in ctxs)
            }
            if not cluster_hits:
                continue
            hits, total = counts[key]
            counts[key] = (
                hits + len(cluster_hits),
                total + len(universe_lines),
            )

    out: dict[str, float | None] = {}
    for key, (hits, total) in counts.items():
        out[key] = round(100.0 * hits / total, 2) if total else None
    return out


def _overall_coverage_pct() -> float | None:
    try:
        out = subprocess.run(
            ["coverage", "report", "--rcfile=.coveragerc"],
            capture_output=True, text=True, cwd=REPO_ROOT, check=False,
        )
    except FileNotFoundError:
        return None
    m = _COVERAGE_TOTAL_RE.search(out.stdout)
    return float(m.group(1)) if m else None


# ── Cluster execution ──────────────────────────────────────────────
def _run_cluster(
    cluster: Cluster,
    *,
    quiet: bool,
    keepdb: bool,
    test_suffix: str | None = None,
) -> dict[str, Any]:
    """Run one cluster's tests.

    ``test_suffix`` — optional dotted path appended to the cluster's
    base label, letting a caller run a single test inside the cluster
    instead of the whole folder. Example::

        test_suffix="test_1b_lex_init.TestCluster01b_LexInit.test_1_6b_init_runs_full_pipeline"

    produces::

        lex.test_project.tests.init.test_1b_lex_init.TestCluster01b_LexInit.test_1_6b_init_runs_full_pipeline

    When omitted, the full cluster folder is run as before.
    """
    base_label = f"lex.test_project.tests.{cluster.key}"
    label = f"{base_label}.{test_suffix}" if test_suffix else base_label
    env = {**os.environ, **DJANGO_ENV}

    cmd = [
        "coverage", "run", "-a",
        "--rcfile=.coveragerc",
        "-m", "lex", "test",
        label,
        "--verbosity=2", "--noinput",
    ]
    if keepdb:
        cmd.append("--keepdb")

    subset_note = "  [single test]" if test_suffix else ""
    print(f"\n──── {cluster.key}{subset_note} ────  (running: {label})",
          flush=True)
    t0 = time.time()

    if quiet:
        # Capture so we can parse the summary, but log a "still alive"
        # heartbeat every 15 s so the operator knows the process is
        # making progress on slow clusters (e.g. stress).
        proc = subprocess.Popen(
            cmd, cwd=REPO_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        last_heartbeat = time.time()
        stderr_chunks: list[str] = []
        while proc.poll() is None:
            try:
                if proc.stderr is not None:
                    err = proc.stderr.read1(4096)
                    if err:
                        stderr_chunks.append(err)
            except Exception:
                pass
            now = time.time()
            if now - last_heartbeat >= 15:
                elapsed = int(now - t0)
                print(f"  … {cluster.key} still running ({elapsed}s)", flush=True)
                last_heartbeat = now
            time.sleep(0.25)
        tail_out, tail_err = proc.communicate()
        stderr = "".join(stderr_chunks) + (tail_err or "")
    else:
        # Stream stderr live; still capture it so we can parse.
        proc = subprocess.Popen(
            cmd, cwd=REPO_ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        stderr_chunks = []
        assert proc.stderr is not None
        for line in proc.stderr:
            sys.stderr.write(line)
            sys.stderr.flush()
            stderr_chunks.append(line)
        proc.wait()
        stderr = "".join(stderr_chunks)

    wall_s = time.time() - t0
    parsed = _parse_summary(stderr)
    # Prefer the wall-clock measurement over Django's internal one —
    # includes DB setup cost the stakeholder also pays.
    parsed["wall_s"] = round(wall_s, 2)

    # Per-cluster coverage is computed in one pass after all clusters
    # finish (see _per_cluster_coverage_from_contexts). Leave the key
    # present here with None so callers don't have to special-case it.
    parsed["coverage_pct"] = None

    mark = "✓" if parsed["outcome"] == "success" else "✗"
    print(
        f"  {mark} {cluster.key}: "
        f"{parsed['passed']} passed, "
        f"{parsed['failed']} failed, "
        f"{parsed['errors']} errors, "
        f"{parsed['skipped']} skipped, "
        f"{parsed['xfailed']} xfailed "
        f"— {parsed['wall_s']}s",
        flush=True,
    )
    return parsed


def main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="manifest.json",
                   help="Where to write the JSON manifest.")
    p.add_argument("--only", default=None,
                   help="Comma-separated cluster selector. Each entry is "
                        "either ``<cluster_key>`` (run every test in the "
                        "cluster), ``<cluster_key>:<test_suffix>`` (run "
                        "a single test — the suffix is appended to "
                        "``lex.test_project.tests.<cluster_key>.``), or "
                        "the magic token ``release-gate`` which expands "
                        "to every cluster flagged ``release_gate=True`` "
                        "in showcase_clusters.py. The token can be mixed "
                        "with explicit keys. Example: "
                        "``release-gate`` (full release subset), or "
                        "``init:test_1b_lex_init.TestCluster01b_LexInit."
                        "test_1_6b_init_runs_full_pipeline,crud_api:"
                        "test_2a_create.TestCluster02a_Create."
                        "test_2_1_post_creates_record``.")
    p.add_argument("--quiet", action="store_true",
                   help="Capture test output per cluster (heartbeat "
                        "printed every 15s). Default streams live.")
    p.add_argument("--keepdb", action="store_true",
                   help="Reuse the test database across clusters — "
                        "big speed-up on local runs (don't use in CI).")
    args = p.parse_args(argv)

    # Parse --only: each entry is "<key>", "<key>:<test_suffix>", or the
    # magic token ``release-gate`` which expands to every cluster flagged
    # ``release_gate=True`` in showcase_clusters.py. Mixing the token with
    # explicit keys is allowed — the union is run.
    # ``selectors`` preserves the caller's order and maps key → suffix (or None).
    selectors: dict[str, str | None]
    if args.only:
        selectors = {}
        for raw in args.only.split(","):
            entry = raw.strip()
            if not entry:
                continue
            if entry == RELEASE_GATE_TOKEN:
                # Expand the token in declaration order; do not overwrite
                # an explicit suffix already set for one of these keys.
                for key in release_gate_keys():
                    selectors.setdefault(key, None)
                continue
            if ":" in entry:
                key, suffix = entry.split(":", 1)
                selectors[key.strip()] = suffix.strip() or None
            else:
                selectors[entry] = None
        # Preserve CLUSTERS declaration order for a stable report.
        clusters = tuple(c for c in CLUSTERS if c.key in selectors)
        missing = set(selectors) - {c.key for c in clusters}
        if missing:
            print(f"::warning::Unknown cluster keys in --only: "
                  f"{', '.join(sorted(missing))}", file=sys.stderr)
    else:
        selectors = {c.key: None for c in CLUSTERS}
        clusters = CLUSTERS

    # Clean slate so `coverage run -a` accumulates only our runs.
    if shutil.which("coverage"):
        subprocess.run(["coverage", "erase"], cwd=REPO_ROOT, check=False)

    results = []
    for c in clusters:
        r = _run_cluster(
            c,
            quiet=args.quiet,
            keepdb=args.keepdb,
            test_suffix=selectors.get(c.key),
        )
        results.append({
            "key": c.key,
            "label": c.label,
            "test_suffix": selectors.get(c.key),
            **r,
        })

    # Per-cluster coverage attribution (via coverage.py contexts)
    # was removed on 22 April 2026: with narrow curated runs (1-2
    # tests per cluster) the context-universe denominator collapses
    # to the cluster's own hits and every row reports 100% — not
    # useful. With broad runs the denominator is dominated by
    # Django-boot imports and every row converges toward the
    # framework total — also not useful. Neither regime gives a
    # reliable per-cluster signal. We now report the SAME
    # project-wide coverage on every cluster row, computed once from
    # the combined .coverage file. It's honest: "here is how much
    # of the framework this release exercised in total".
    overall_pct = _overall_coverage_pct()
    print("\n── Project-wide coverage (same on every cluster row) ──",
          flush=True)
    print(f"  · framework-wide: {overall_pct}%" if overall_pct is not None
          else "  · framework-wide: — (no coverage data)", flush=True)
    for r in results:
        r["coverage_pct"] = overall_pct

    # Aggregate overall.
    overall = {
        "ran":      sum(r["ran"] for r in results),
        "passed":   sum(r["passed"] for r in results),
        "failed":   sum(r["failed"] for r in results),
        "errors":   sum(r["errors"] for r in results),
        "skipped":  sum(r["skipped"] for r in results),
        "xfailed":  sum(r["xfailed"] for r in results),
        "wall_s":   round(sum(r["wall_s"] for r in results), 2),
        "clusters_total":   len(results),
        "clusters_passing": sum(1 for r in results if r["outcome"] == "success"),
        "coverage_pct":     _overall_coverage_pct(),
        "outcome": "success" if all(r["outcome"] == "success" for r in results) else "failure",
    }

    manifest = {
        "generated_at": int(time.time()),
        "overall": overall,
        "clusters": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"\nWrote {args.out}: {overall['clusters_passing']}/{overall['clusters_total']} "
          f"clusters passing, coverage {overall['coverage_pct']}%")
    # Always exit 0 — the workflow inspects manifest.overall.outcome.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))














