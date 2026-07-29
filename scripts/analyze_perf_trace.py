#!/usr/bin/env python3
"""
Analyze a ``lex_perf.log`` trace and produce a ranked bottleneck
report.

The LEX perf tracer dumps periodic summaries that look like::

    ===== LEX_PERF_TRACE @ 2026-04-20 16:37:12 (pid=82841) =====
    LEX_PERF_TRACE summary:
    --------------------------------------------------------------
    category                                  label           count  total_s  avg_ms
    --------------------------------------------------------------
    CalculationModel.execute_calculation_syn  Period            1  1379.066  1379066.0
    db.query                                  SELECT       649327   417.252      0.643
    ...

This script keeps the **last** (cumulative) summary block in the file,
parses it, then ranks entries by ``total_s``, groups them by category
family (framework-overhead vs app-work), and prints the top offenders.

Usage:

    python scripts/analyze_perf_trace.py lex_perf.log
    python scripts/analyze_perf_trace.py lex_perf.log --top 30
    python scripts/analyze_perf_trace.py lex_perf.log --format json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ── parsing ────────────────────────────────────────────────────────

SUMMARY_HEADER = re.compile(r"^===== LEX_PERF_TRACE @ .+? \(pid=\d+\) =====$")

# A row is "category<spaces>label<spaces>count<spaces>total_s<spaces>avg_ms".
# Labels can contain spaces and punctuation; count is the first integer,
# total_s and avg_ms are the two floats that follow it.
ROW_RE = re.compile(
    r"^(?P<category>\S[^ ]*(?:\.[^ ]+)*)"     # non-space token with dots ok
    r"\s{2,}(?P<label>.+?)"                   # label (lazy)
    r"\s{2,}(?P<count>\d+)"                   # count
    r"\s+(?P<total>-?\d+\.\d+)"               # total_s
    r"\s+(?P<avg>-?\d+\.\d+)\s*$"             # avg_ms
)


@dataclass
class Row:
    category: str
    label: str
    count: int
    total_s: float
    avg_ms: float

    @property
    def key(self) -> str:
        return f"{self.category}|{self.label}"


@dataclass
class Summary:
    rows: list[Row] = field(default_factory=list)

    def by_total(self, limit: int | None = None) -> list[Row]:
        out = sorted(self.rows, key=lambda r: r.total_s, reverse=True)
        return out[:limit] if limit else out

    def total_time(self) -> float:
        # Wall-clock root marker — "CalculationModel.execute_calculation_syn"
        # or "calculate.body" on the root calc. Use the biggest one.
        candidates = [
            r for r in self.rows
            if r.category in {
                "CalculationModel.execute_calculation_sync",
                "CalculationModel.execute_calculation_syn",  # truncated header
                "calculate.body",
            }
        ]
        return max((r.total_s for r in candidates), default=0.0)


def parse_summaries(path: Path) -> list[Summary]:
    """Parse every summary block. Returns them in document order."""
    summaries: list[Summary] = []
    with path.open("r", errors="replace") as fh:
        current: Summary | None = None
        in_body = False
        dash_count = 0
        for line in fh:
            line = line.rstrip("\n")
            if SUMMARY_HEADER.match(line):
                current = Summary()
                summaries.append(current)
                in_body = False
                dash_count = 0
                continue
            if current is None:
                continue
            if line.startswith("-" * 10):
                dash_count += 1
                # Format: dash-line | header | dash-line | rows...
                # So the body begins AFTER the second dash-line.
                if dash_count >= 2:
                    in_body = True
                continue
            if not in_body:
                continue
            m = ROW_RE.match(line)
            if m:
                current.rows.append(Row(
                    category=m["category"],
                    label=m["label"].strip(),
                    count=int(m["count"]),
                    total_s=float(m["total"]),
                    avg_ms=float(m["avg"]),
                ))
    return summaries


# ── classification ─────────────────────────────────────────────────

# Categories attributed to framework machinery — not the customer's
# ``calculate()`` body. Time spent here is pure overhead relative to
# the old lex-app baseline.
FRAMEWORK_PATTERNS = [
    # CalculationLog machinery — the biggest single new-framework overhead.
    ("calc_log", re.compile(r"^CalculationLog\.log\b")),
    ("calc_log_db", re.compile(r"^db\.query\.(UPDATE|SELECT|INSERT) audit_logging_calculationlog")),
    # AuditLog writes from the audit mixin layer.
    ("audit_log_db", re.compile(r"^db\.query\.(UPDATE|SELECT|INSERT) audit_logging_auditlog")),
    # Context resolution + cache + websocket (all invoked by CalculationLog).
    ("context_resolver", re.compile(r"^ContextResolver\.")),
    ("cache_manager", re.compile(r"^CacheManager\.")),
    ("channel_layer", re.compile(r"^channel_layer\.")),
    # History / bi-temporal writes.
    ("history_db", re.compile(r"^db\.query\.INSERT .+_history$")),
    ("meta_history_db", re.compile(r"^db\.query\.INSERT .+_meta_history$")),
    # Django signals and LexModel lifecycle hooks.
    ("signals", re.compile(r"^signals\.")),
    ("hooks", re.compile(r"^LexModel\.hooks\.")),
    # UserContext rebuild cost (BUG-011 family).
    ("user_context_groups", re.compile(r"auth_group <- .*LexModel\.py")),
    ("user_context_auth_group", re.compile(r"^db\.query\.SELECT +auth_group\b")),
    ("user_context_session", re.compile(r"django_session")),
    ("content_type", re.compile(r"django_content_type")),
    ("user_context_tokens", re.compile(r"oauth2_authcodeflow_blacklistedtoken")),
    # Model instantiation overhead — per-row cost of LexModel.__init__.
    ("lex_model_init", re.compile(r"^LexModel\.__init__\b")),
]

APP_PATTERNS = [
    ("calc_body", re.compile(r"^calculate\.body ")),
    ("calc_hook", re.compile(r"^CalculationModel\.calculate_hook ")),
    # Trace column truncates "execute_calculation_sync" → "_syn". Match both.
    ("calc_exec", re.compile(r"^CalculationModel\.execute_calculation_syn")),
    ("app_save", re.compile(r"^LexModel\.(save|base_save) ")),
    # Per-table queries (label is the table name).
    ("app_db", re.compile(r"^db\.query\.(SELECT|INSERT|UPDATE|DELETE) ACP_")),
    # Roll-up rows: category exactly 'db.query', label is SELECT/INSERT/etc.
    ("app_db_total", re.compile(r"^db\.query (SELECT|INSERT|UPDATE|DELETE|TX)$")),
]


def classify(row: Row) -> tuple[str, str]:
    """Return (bucket, bucket_detail). Bucket is 'framework' | 'app' | 'other'."""
    # Match the full "category label" string against framework patterns;
    # some of them need to see the label (e.g. auth_group attribution).
    key_line = f"{row.category} {row.label}"
    for tag, pat in FRAMEWORK_PATTERNS:
        if pat.search(key_line):
            return "framework", tag
    for tag, pat in APP_PATTERNS:
        if pat.search(key_line):
            return "app", tag
    return "other", "unclassified"


# ── reporting ──────────────────────────────────────────────────────

def render_table(rows: Iterable[Row], total: float) -> str:
    lines = []
    lines.append(
        f"{'category':<42} {'label':<50} {'count':>8} "
        f"{'total_s':>10} {'%':>6} {'avg_ms':>10}"
    )
    lines.append("-" * 132)
    for r in rows:
        pct = (r.total_s / total * 100.0) if total else 0.0
        lines.append(
            f"{r.category[:42]:<42} {r.label[:50]:<50} "
            f"{r.count:>8} {r.total_s:>10.3f} {pct:>5.1f}% "
            f"{r.avg_ms:>10.3f}"
        )
    return "\n".join(lines)


def build_report(summary: Summary, top: int) -> str:
    total = summary.total_time()
    buckets: dict[str, list[Row]] = {"framework": [], "app": [], "other": []}
    tags: dict[str, list[Row]] = {}
    for r in summary.rows:
        bucket, tag = classify(r)
        buckets[bucket].append(r)
        tags.setdefault(f"{bucket}:{tag}", []).append(r)

    out = []
    out.append("=" * 132)
    out.append(f"LEX PERF ANALYSIS — root calc wall-clock: {total:.2f}s "
               f"({total/60:.1f} min)")
    out.append("=" * 132)

    out.append(f"\nTOP {top} BY total_s (overall)")
    out.append(render_table(summary.by_total(top), total))

    for bucket in ("framework", "app", "other"):
        bucket_total = sum(r.total_s for r in buckets[bucket])
        bucket_pct = bucket_total / total * 100.0 if total else 0.0
        out.append(
            f"\n── {bucket.upper()} bucket: {bucket_total:.2f}s "
            f"({bucket_pct:.1f}% of root) — top {top}"
        )
        out.append(
            render_table(
                sorted(buckets[bucket], key=lambda r: r.total_s, reverse=True)[:top],
                total,
            )
        )

    # Grouped by tag — quickly surfaces e.g. "CalculationLog overhead
    # is tag=calc_log + calc_log_db combined".
    out.append("\n── FRAMEWORK SUB-TAGS (grouped total_s)")
    out.append(f"{'tag':<30} {'count_rows':>10} {'sum_count':>12} {'total_s':>10} {'% root':>8}")
    out.append("-" * 72)
    for key in sorted(tags, key=lambda k: sum(r.total_s for r in tags[k]), reverse=True):
        bucket, tag = key.split(":", 1)
        if bucket != "framework":
            continue
        group = tags[key]
        gs = sum(r.total_s for r in group)
        gc = sum(r.count for r in group)
        pct = gs / total * 100 if total else 0.0
        out.append(
            f"{tag:<30} {len(group):>10} {gc:>12} {gs:>10.3f} {pct:>7.1f}%"
        )

    return "\n".join(out)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", type=Path, help="Path to lex_perf.log")
    p.add_argument("--top", type=int, default=20,
                   help="How many rows to show per section (default 20)")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--block", type=int, default=-1,
                   help="Which summary block to analyze (-1 = last = cumulative)")
    args = p.parse_args(argv)

    if not args.path.exists():
        print(f"File not found: {args.path}", file=sys.stderr)
        return 2

    summaries = parse_summaries(args.path)
    if not summaries:
        print("No LEX_PERF_TRACE summary blocks found.", file=sys.stderr)
        return 1
    block = summaries[args.block] if args.block != -1 else summaries[-1]

    if args.format == "json":
        rows_sorted = block.by_total()
        enriched = []
        for r in rows_sorted:
            bucket, tag = classify(r)
            enriched.append({
                **r.__dict__,
                "bucket": bucket,
                "tag": tag,
            })
        json.dump({
            "total_s": block.total_time(),
            "rows": enriched,
        }, sys.stdout, indent=2)
        print()
    else:
        print(build_report(block, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

