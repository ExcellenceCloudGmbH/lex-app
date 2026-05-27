#!/usr/bin/env python3
"""
Run the Platform Health showcase suite and emit a JSON manifest.

For every cluster declared in ``showcase_clusters.CLUSTERS`` this
script:

  1. runs the cluster path ``lex/test_project/tests/<key>`` through
     ``python -m lex pytest``, under ``coverage run -a`` so coverage
     accumulates across clusters;
  2. parses pytest's trailing summary line from stdout to extract
     ``passed``, ``failed``, ``errors``, ``skipped``, ``xfailed`` and
     ``xpassed`` counts;
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
from showcase_clusters import CLUSTERS, Cluster  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
DJANGO_ENV = {
    "DJANGO_SETTINGS_MODULE": "lex_app.settings",
    "DATABASE_DEPLOYMENT_TARGET": "default",
    "CELERY_ACTIVE": "False",
}


# ── Output parsing ──────────────────────────────────────────────────
# Pytest summary line examples (category order varies by version and by
# what categories are non-zero):
#   ===== 42 passed in 7.12s =====
#   ===== 3 failed, 39 passed, 2 skipped in 8.45s =====
#   ===== 1 xfailed, 41 passed in 7.20s =====
#   ===== 1 error in 0.45s =====
#   ===== 1 warning, 37 errors in 8.44s =====   (collection failures)
#
# We parse this in two steps to be genuinely order-agnostic:
#   1. ``_PYTEST_SUMMARY_LINE_RE`` matches the whole line and captures
#      the comma-separated category clause and the wall-clock duration.
#   2. ``_PYTEST_CATEGORY_TOKEN_RE`` is applied to the clause to pull
#      out every ``<N> <word>`` pair regardless of order.
# This replaces an earlier hard-coded-order regex that silently reported
# ``0 errors`` whenever pytest emitted ``warning`` before ``errors``.
_PYTEST_SUMMARY_LINE_RE = re.compile(
    r"^=+\s+(?P<body>.+?)\s+in\s+(?P<duration>[\d.]+)s\s+=+\s*$",
    re.MULTILINE,
)
_PYTEST_CATEGORY_TOKEN_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<word>[a-zA-Z]+)"
)
# Map every pytest category word to its manifest bucket. Singulars and
# plurals collapse to the same bucket so "1 error" and "37 errors" both
# count.
_PYTEST_CATEGORY_TO_BUCKET = {
    "passed":     "passed",
    "failed":     "failed",
    "error":      "errors",
    "errors":     "errors",
    "skipped":    "skipped",
    "xfailed":    "xfailed",
    "xpassed":    "xpassed",
    # Recognised-but-ignored buckets (kept so unknown-word warnings stay
    # signal, not noise):
    "deselected": None,
    "warning":    None,
    "warnings":   None,
}

# ── Per-test parsing for the customer report ───────────────────────
# Pytest -v test-result lines look like:
#   path/to/file.py::Class::method PASSED [ 12%]
#   path/to/file.py::Class::method FAILED [ 13%]
#   path/to/file.py::Class::method SKIPPED (reason) [ 14%]
#   path/to/file.py::Class::method XFAIL [ 15%]
#   path/to/file.py::Class::method XPASS [ 16%]
#   path/to/file.py::Class::method ERROR [ 17%]
# Parametrised tests carry "[paramset]" after the method name; we keep
# the whole nodeid in the `dotted` field for traceability.
_PYTEST_RESULT_RE = re.compile(
    r"^(?P<nodeid>(?:[\w\-./]+\.py)::[\w:\[\]\-.]+?)\s+"
    r"(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
    r"(?:\s+\([^)]*\))?"     # SKIPPED (reason) etc.
    r"(?:\s+\[\s*\d+%\])?"   # optional [ N%] progress marker
    r"\s*$",
    re.MULTILINE,
)

# Pytest's short test summary info block (one line per non-passing test):
#   FAILED nodeid - exception type: message
#   ERROR  nodeid - exception type: message
# This is the cleanest source for short error messages.
_PYTEST_SHORT_SUMMARY_LINE_RE = re.compile(
    r"^(?P<status>FAILED|ERROR)\s+(?P<nodeid>(?:[\w\-./]+\.py)::[\w:\[\]\-.]+?)"
    r"(?:\s+-\s+(?P<message>.+?))?\s*$",
    re.MULTILINE,
)


_PYTEST_STATUS_TO_OUTCOME = {
    "PASSED":  "passed",
    "FAILED":  "failed",
    "ERROR":   "errored",
    "SKIPPED": "skipped",
    "XFAIL":   "xfailed",
    "XPASS":   "xpassed",
}


def _parse_per_test(output: str) -> list[dict[str, Any]]:
    """Extract one entry per test method from a verbose pytest run.

    Output entries::

        {"name":    "test_8_45_real_redis_round_trip",
         "dotted":  "lex/test_project/tests/.../test_8k.py::TestCluster08k::test_8_45",
         "outcome": "failed",
         "message": "AssertionError: ..."}
    """
    # First pass: index short-summary lines so we can attach messages.
    messages: dict[str, str] = {}
    for m in _PYTEST_SHORT_SUMMARY_LINE_RE.finditer(output):
        msg = (m.group("message") or "").strip()
        if msg:
            messages[m.group("nodeid")] = msg[:400]

    # Second pass: walk the -v result lines. De-dupe on nodeid in case
    # pytest re-emits a test (e.g. rerun, parametrised reruns).
    tests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in _PYTEST_RESULT_RE.finditer(output):
        nodeid = m.group("nodeid")
        if nodeid in seen:
            continue
        seen.add(nodeid)
        outcome = _PYTEST_STATUS_TO_OUTCOME[m.group("status")]
        # nodeid is path::Class::method or path::function (no class).
        method = nodeid.split("::")[-1]
        tests.append({
            "name": method,
            "dotted": nodeid,
            "outcome": outcome,
            "message": messages.get(nodeid, ""),
        })
    return tests


def _parse_summary(output: str) -> dict[str, int | float | str]:
    """
    Parse pytest's trailing summary line into the manifest shape.

    Two-step parse so category order doesn't matter:

    * find the *last* line of the form ``==== <body> in <N>s ====`` —
      pytest always prints the summary as the final boxed line, so the
      last match is the authoritative one;
    * tokenise ``<body>`` into every ``<N> <word>`` pair and fold each
      into its bucket via ``_PYTEST_CATEGORY_TO_BUCKET``.

    "1 warning, 37 errors in 8.44s" now correctly reports 37 errors;
    the old hard-coded-order regex reported 0.
    """
    counts = {"passed": 0, "failed": 0, "errors": 0,
              "skipped": 0, "xfailed": 0, "xpassed": 0}
    wall_s = 0.0
    ran = 0
    summary_found = False

    last_match = None
    for m in _PYTEST_SUMMARY_LINE_RE.finditer(output):
        last_match = m
    if last_match is not None:
        summary_found = True
        wall_s = float(last_match.group("duration"))
        body = last_match.group("body")
        for tok in _PYTEST_CATEGORY_TOKEN_RE.finditer(body):
            word = tok.group("word").lower()
            count = int(tok.group("count"))
            bucket = _PYTEST_CATEGORY_TO_BUCKET.get(word)
            if bucket is not None:
                counts[bucket] += count
        ran = sum(counts.values())

    outcome = "success" if counts["failed"] == 0 and counts["errors"] == 0 else "failure"

    return {
        "ran": ran,
        "passed": counts["passed"],
        "failed": counts["failed"],
        "errors": counts["errors"],
        "skipped": counts["skipped"],
        "xfailed": counts["xfailed"],
        "xpassed": counts["xpassed"],
        "wall_s": wall_s,
        "outcome": outcome,
        "summary_found": summary_found,
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

    (The dotted form survives the pytest cutover because every cluster
    folder under ``lex/test_project/tests/`` is a real Python package
    — ``__init__.py`` files all the way up to the repo root — so
    pytest imports each test module under the same fully-qualified
    name Django did, and coverage's ``test_function`` context tag is
    derived from that import name.)

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

    produces the pytest nodeid::

        lex/test_project/tests/init/test_1b_lex_init.py::TestCluster01b_LexInit::test_1_6b_init_runs_full_pipeline

    When omitted, the full cluster folder is run as before.

    Note: ``keepdb`` is kept on the signature for backward compatibility
    with callers; pytest has no equivalent flag for unittest.TestCase
    tests, and Django TestCase's per-test transaction rollback already
    avoids DB recreation per test, so the speed characteristic is
    preserved without it.
    """
    base_path = f"lex/test_project/tests/{cluster.key}"
    if test_suffix:
        # test_suffix arrives as a dotted Django label tail, e.g.
        # "test_1b_lex_init.TestCluster01b_LexInit.test_1_6b_init_runs_full_pipeline".
        # Translate to a pytest nodeid:
        #   "<module>.py::<Class>::<method>"
        parts = test_suffix.split(".")
        module = parts[0]
        rest = "::".join(parts[1:])
        target = f"{base_path}/{module}.py::{rest}" if rest else f"{base_path}/{module}.py"
    else:
        target = base_path
    env = {**os.environ, **DJANGO_ENV}

    cmd = [
        "coverage", "run", "-a",
        "--rcfile=.coveragerc",
        "-m", "lex", "pytest",
        target,
        "-v",
    ]
    # keepdb intentionally ignored — see docstring.
    _ = keepdb

    subset_note = "  [single test]" if test_suffix else ""
    print(f"\n──── {cluster.key}{subset_note} ────  (running: {target})",
          flush=True)
    t0 = time.time()

    # Pytest writes most output (per-test -v lines, summary, FAILURES,
    # short test summary info) to stdout. Merge stderr into stdout via
    # `stderr=subprocess.STDOUT` so a single stream carries everything.
    if quiet:
        # Capture so we can parse the summary, but log a "still alive"
        # heartbeat every 15 s so the operator knows the process is
        # making progress on slow clusters (e.g. stress).
        proc = subprocess.Popen(
            cmd, cwd=REPO_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        last_heartbeat = time.time()
        stdout_chunks: list[str] = []
        while proc.poll() is None:
            try:
                if proc.stdout is not None:
                    chunk = proc.stdout.read1(4096)
                    if chunk:
                        stdout_chunks.append(chunk)
            except Exception:
                pass
            now = time.time()
            if now - last_heartbeat >= 15:
                elapsed = int(now - t0)
                print(f"  … {cluster.key} still running ({elapsed}s)", flush=True)
                last_heartbeat = now
            time.sleep(0.25)
        tail_out, _tail_err = proc.communicate()
        combined_output = "".join(stdout_chunks) + (tail_out or "")
    else:
        # Stream live; merge stderr into stdout so a single iterator
        # yields the full pytest output in order.
        proc = subprocess.Popen(
            cmd, cwd=REPO_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        stdout_chunks = []
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stderr.write(line)
            sys.stderr.flush()
            stdout_chunks.append(line)
        proc.wait()
        combined_output = "".join(stdout_chunks)

    wall_s = time.time() - t0
    returncode = proc.returncode if proc.returncode is not None else -1
    parsed = _parse_summary(combined_output)
    # Prefer the wall-clock measurement over pytest's internal one —
    # includes DB setup cost the stakeholder also pays.
    parsed["wall_s"] = round(wall_s, 2)

    # Catch the silent-success failure mode: pytest never printed a
    # summary line (e.g. it aborted before collection because
    # ``setup_databases()`` raised, or Django bootstrap blew up) and
    # the subprocess exit code is non-zero. Without this, the cluster
    # row would report "0 passed, 0 failed, 0 errors" with
    # outcome="success" — and the platform-health report would
    # confidently say "✓ all green — N/N clusters passing" while in
    # reality zero tests ran. Surface it as a hard error instead.
    if not parsed.pop("summary_found", False) and returncode != 0:
        parsed["errors"] = max(parsed["errors"], 1)
        parsed["ran"] = sum(
            parsed[k] for k in
            ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")
        )
        parsed["outcome"] = "failure"
        parsed["setup_error"] = (
            f"pytest emitted no summary line and exited with code "
            f"{returncode}. Likely cause: Django/DB bootstrap failed "
            f"before tests could run. Check the cluster log."
        )
    # If pytest DID emit a summary but the process still exited non-zero
    # (e.g. internal pytest error after the report), make sure that
    # surfaces too rather than reporting success based on counts alone.
    elif returncode != 0 and parsed["outcome"] == "success":
        parsed["errors"] = max(parsed["errors"], 1)
        parsed["outcome"] = "failure"
        parsed["setup_error"] = (
            f"pytest exited with code {returncode} despite a clean "
            f"summary line; treating cluster as failed."
        )

    # Capture every individual test the runner emitted, so the
    # customer report can list which scenarios actually broke (and
    # the short error message), not just "this cluster is red".
    parsed["tests"] = _parse_per_test(combined_output)

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
                        "cluster) or ``<cluster_key>:<test_suffix>`` (run "
                        "a single test — the suffix is the dotted "
                        "``<module>.<TestClass>.<method>`` tail under the "
                        "cluster folder, translated internally to the "
                        "pytest nodeid "
                        "``lex/test_project/tests/<cluster_key>/<module>.py"
                        "::<TestClass>::<method>``). "
                        "Example: ``init:test_1b_lex_init.TestCluster01b_"
                        "LexInit.test_1_6b_init_runs_full_pipeline,"
                        "crud_api:test_2a_create.TestCluster02a_Create."
                        "test_2_1_post_creates_record``.")
    p.add_argument("--quiet", action="store_true",
                   help="Capture test output per cluster (heartbeat "
                        "printed every 15s). Default streams live.")
    p.add_argument("--keepdb", action="store_true",
                   help="Reuse the test database across clusters — "
                        "big speed-up on local runs (don't use in CI).")
    args = p.parse_args(argv)

    # Parse --only: each entry is "<key>" or "<key>:<test_suffix>".
    # ``selectors`` preserves the caller's order and maps key → suffix (or None).
    selectors: dict[str, str | None]
    if args.only:
        selectors = {}
        for raw in args.only.split(","):
            entry = raw.strip()
            if not entry:
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














