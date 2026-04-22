#!/usr/bin/env python3
"""
Build the Platform Health Report from a manifest produced by
``run_showcase_suite.py``.

Outputs:
  * ``report.html``           — email body, tabular; passes single-line,
                                failures expand with the "what it means"
                                paragraph.
  * ``report.pdf``            — detailed archive, every row expanded,
                                plus the "What we test and why" glossary.
  * ``logo.png``              — rasterised wordmark, attached inline by
                                the email sender (``Content-ID: logo``).
  * ``report_preview.html``   — (optional, via ``--out-email-preview``)
                                browser-openable copy of the email with
                                the logo inlined as a data URI.

The report shows ONE ROW PER CLUSTER, with columns:
    Status | Capability | Scenarios
(Per-cluster coverage was removed on 22 April 2026 — see the long
comment in ``_results_table`` for the rationale. Framework-wide
coverage still appears in the verdict band and totals row.)
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from showcase_clusters import CLUSTERS, Cluster, cluster_by_key  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGO_SVG = REPO_ROOT / "images" / "dark-lex-logo.svg"


# ── Brand palette — Excellence Cloud ────────────────────────────────
C = {
    "brand":      "#283067",
    "accent":     "#24b6bb",
    "ink":        "#1a2230",
    "muted":      "#5d6b7a",
    "rule":       "#e2e5ec",
    "bg":         "#f5f7fb",
    "card":       "#ffffff",
    "zebra":      "#fafbfd",
    "ok_bg":      "#e8f5e9",
    "ok_ink":     "#1b5e20",
    "ok_border":  "#2e7d32",
    "bad_bg":     "#ffebee",
    "bad_ink":    "#b71c1c",
    "bad_border": "#c62828",
    "bad_tint":   "#fff5f6",
    "warn_bg":    "#fff8e1",
    "warn_ink":   "#8a6d00",
}
SANS = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif")
SERIF = "Georgia, 'Times New Roman', Times, serif"


# ── Logo ─────────────────────────────────────────────────────────────
def _svg_to_png_bytes(svg_path: Path, *, width: int = 520) -> bytes | None:
    try:
        import cairosvg  # type: ignore
    except ImportError:
        return None
    if not svg_path.exists():
        return None
    try:
        return cairosvg.svg2png(url=str(svg_path), output_width=width)
    except Exception:
        return None


def _png_data_uri(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


# ── Shared rendering ────────────────────────────────────────────────
def _fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "—"
    if seconds < 1:
        return f"{int(seconds * 1000)} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{seconds / 60:.1f} min"


def _fmt_pct(pct: float | None) -> str:
    return "—" if pct is None else f"{pct:.1f}%"


def _outcome_chip(outcome: str) -> str:
    table = {
        "success":   (C["ok_bg"],  C["ok_ink"],  C["ok_border"],  "✓", "Passed"),
        "failure":   (C["bad_bg"], C["bad_ink"], C["bad_border"], "✗", "Failed"),
    }
    bg, ink, border, icon, word = table.get(
        outcome, (C["bg"], C["muted"], C["rule"], "?", outcome.title())
    )
    return (
        f'<span style="display:inline-block;padding:4px 10px;'
        f'background:{bg};color:{ink};border:1px solid {border};'
        f'border-radius:999px;font:600 11px/1 {SANS};letter-spacing:.4px;'
        f'white-space:nowrap;">{icon}&nbsp; {word}</span>'
    )


def _verdict_band(overall: dict[str, Any]) -> str:
    ok = overall["outcome"] == "success"
    bg     = C["ok_bg"]     if ok else C["bad_bg"]
    ink    = C["ok_ink"]    if ok else C["bad_ink"]
    border = C["ok_border"] if ok else C["bad_border"]
    icon   = "✓" if ok else "✗"
    title  = ("All capabilities are working"
              if ok else "One or more capabilities are broken")
    counts = (f"{overall['clusters_passing']} of "
              f"{overall['clusters_total']} capabilities passing"
              f"  &middot;  {overall['passed']} of {overall['ran']} scenarios passing"
              f"  &middot;  overall coverage {_fmt_pct(overall.get('coverage_pct'))}")
    return f"""
    <tr>
      <td style="padding:24px 32px;background:{bg};">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="vertical-align:middle;width:60px;">
              <div style="width:52px;height:52px;border-radius:50%;
                          background:{border};color:#fff;
                          font:700 28px/52px {SANS};text-align:center;">
                {icon}
              </div>
            </td>
            <td style="vertical-align:middle;padding-left:18px;">
              <div style="font:700 19px/1.25 {SANS};color:{ink};">
                {title}
              </div>
              <div style="font:400 13px/1.5 {SANS};color:{ink};
                          margin-top:4px;opacity:.85;">
                {counts}
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """


def _logo_block(*, logo_src: str | None, text_fallback: str) -> str:
    if logo_src:
        return (
            f'<img src="{logo_src}" '
            f'alt="{html.escape(text_fallback)}" '
            f'width="260" '
            f'style="display:block;width:260px;max-width:100%;'
            f'height:auto;border:0;outline:0;">'
        )
    return (
        f'<div style="font:700 26px/1.1 {SERIF};color:#fff;">'
        f'{html.escape(text_fallback)}</div>'
    )


def _header_band(brand: str, *, logo_src: str | None,
                 generated_at: dt.datetime) -> str:
    return f"""
    <tr>
      <td style="background:{C['brand']};padding:26px 32px;color:#fff;">
        <div style="font:400 11px/1 {SANS};letter-spacing:2px;
                    text-transform:uppercase;opacity:.70;">
          Platform Health Report
        </div>
        <div style="margin-top:12px;">
          {_logo_block(logo_src=logo_src, text_fallback='Excellence Cloud')}
        </div>
        <div style="font:400 13px/1.4 {SANS};margin-top:12px;opacity:.82;">
          <strong style="font-weight:600;">{html.escape(brand)}</strong>
          &nbsp;&middot;&nbsp;
          Generated {generated_at.strftime("%A, %d %B %Y at %H:%M UTC")}
        </div>
      </td>
    </tr>
    <tr>
      <td style="height:4px;background:{C['accent']};line-height:0;
                 font-size:0;">&nbsp;</td>
    </tr>
    """


def _footer(commit_sha: str | None, branch: str | None,
            run_url: str | None, wall_s: float | None,
            *, for_email: bool) -> str:
    bits: list[str] = []
    if wall_s is not None:
        bits.append(f'<div>Total run time: <strong style="color:{C["ink"]};">'
                    f'{_fmt_duration(wall_s)}</strong></div>')
    if commit_sha:
        bits.append(
            f'<div>Build identifier: <code style="'
            f'font:500 12px/1 \'SFMono-Regular\',Menlo,Consolas,monospace;'
            f'color:{C["ink"]};">{html.escape(commit_sha[:12])}</code></div>'
        )
    if branch:
        bits.append(f'<div>Branch: {html.escape(branch)}</div>')
    if run_url:
        bits.append(
            f'<div><a href="{html.escape(run_url)}" '
            f'style="color:{C["brand"]};text-decoration:underline;">'
            f'View the full run log</a></div>'
        )

    pdf_hint = (
        '<div style="margin-top:10px;">'
        'The attached PDF contains the full report including the '
        '&ldquo;What we test and why&rdquo; glossary.'
        '</div>' if for_email else ""
    )
    return f"""
    <tr>
      <td style="padding:20px 32px 26px 32px;background:{C['bg']};
                 border-top:1px solid {C['rule']};
                 font:400 12px/1.6 {SANS};color:{C['muted']};">
        <div style="margin-bottom:8px;font-weight:600;color:{C['ink']};">
          Traceability
        </div>
        {''.join(bits) or '<div>—</div>'}
        {pdf_hint}
        <div style="margin-top:14px;padding-top:14px;
                    border-top:1px solid {C['rule']};">
          This report was generated automatically after an automated
          test run. If anything is unclear, or if a capability is
          marked as broken, please contact engineering.
        </div>
      </td>
    </tr>
    """


# ── Cluster table ───────────────────────────────────────────────────
def _scenarios_cell(row: dict[str, Any]) -> str:
    ran = row.get("ran", 0)
    passed = row.get("passed", 0)
    failed = row.get("failed", 0) + row.get("errors", 0)
    skipped = row.get("skipped", 0)
    xfailed = row.get("xfailed", 0)

    main = f"{passed}/{ran}"
    detail_bits = []
    if failed:
        detail_bits.append(f"{failed} failed")
    if skipped:
        detail_bits.append(f"{skipped} skipped")
    if xfailed:
        detail_bits.append(f"{xfailed} expected failures")
    detail = " &middot; ".join(detail_bits) if detail_bits else ""
    return (
        f'<div style="font:600 14px/1.3 {SANS};color:{C["ink"]};">{main}</div>'
        + (f'<div style="font:400 11px/1.4 {SANS};color:{C["muted"]};'
           f'margin-top:2px;">{detail}</div>' if detail else "")
    )



def _results_table(rows: list[dict[str, Any]], *, expand_passes: bool,
                   overall: dict[str, Any]) -> str:
    # Three columns only — Status / Capability / Scenarios.
    #
    # The per-cluster Coverage column was removed on 22 April 2026:
    # every reasonable attribution strategy we tried (manual cov_include
    # globs, coverage.py contexts with the executable-lines denominator,
    # and contexts with the universe-of-executed-lines denominator)
    # produced the same number on every row in at least one common run
    # regime — because Django's app loading imports the same framework
    # code under every test. A column where every cluster reports the
    # same value is noise, not signal. We keep ONE framework-wide
    # number, in the Totals row and the verdict band.
    header = f"""
      <thead>
        <tr>
          <th align="left"
              style="padding:12px 16px;background:{C['bg']};
                     border-bottom:1px solid {C['rule']};
                     font:700 11px/1 {SANS};color:{C['muted']};
                     letter-spacing:1.2px;text-transform:uppercase;
                     width:110px;">
            Status
          </th>
          <th align="left"
              style="padding:12px 16px;background:{C['bg']};
                     border-bottom:1px solid {C['rule']};
                     font:700 11px/1 {SANS};color:{C['muted']};
                     letter-spacing:1.2px;text-transform:uppercase;">
            Capability
          </th>
          <th align="left"
              style="padding:12px 16px;background:{C['bg']};
                     border-bottom:1px solid {C['rule']};
                     font:700 11px/1 {SANS};color:{C['muted']};
                     letter-spacing:1.2px;text-transform:uppercase;
                     width:130px;">
            Scenarios
          </th>
        </tr>
      </thead>
    """
    body_rows: list[str] = []
    for i, row in enumerate(rows):
        cluster: Cluster | None = cluster_by_key(row["key"])
        if cluster is None:
            continue
        outcome = row.get("outcome", "failure")
        failing = outcome != "success"
        row_bg = (C['bad_tint'] if failing
                  else (C['zebra'] if i % 2 else C['card']))

        body_rows.append(f"""
          <tr>
            <td style="padding:14px 16px;background:{row_bg};
                       border-bottom:1px solid {C['rule']};vertical-align:top;">
              {_outcome_chip(outcome)}
            </td>
            <td style="padding:14px 16px;background:{row_bg};
                       border-bottom:1px solid {C['rule']};vertical-align:top;">
              <div style="font:600 14px/1.3 {SANS};color:{C['ink']};">
                {html.escape(cluster.label)}
              </div>
              <div style="font:400 12px/1.5 {SANS};color:{C['muted']};
                          margin-top:3px;">
                {cluster.short_description}
              </div>
            </td>
            <td style="padding:14px 16px;background:{row_bg};
                       border-bottom:1px solid {C['rule']};vertical-align:top;">
              {_scenarios_cell(row)}
            </td>
          </tr>
        """)

        show_detail = failing or expand_passes
        if show_detail:
            body = (cluster.what_it_proves if not failing
                    else cluster.what_it_means_if_broken)
            body_ink = C['bad_ink'] if failing else C['ink']
            body_bg = C['bad_tint'] if failing else row_bg
            body_rows.append(f"""
              <tr>
                <td colspan="3"
                    style="padding:0 16px 16px 16px;background:{body_bg};
                           border-bottom:1px solid {C['rule']};">
                  <div style="font:400 13px/1.6 {SANS};color:{body_ink};">
                    {body}
                  </div>
                </td>
              </tr>
            """)

    # ── Totals row.  Appended to <tbody> (not <tfoot>) because WeasyPrint
    # treats <tfoot> as a repeating page-footer group (CSS display:
    # table-footer-group) and duplicates it on every printed page.
    total_ran     = overall.get("ran", 0)
    total_passed  = overall.get("passed", 0)
    total_failed  = overall.get("failed", 0) + overall.get("errors", 0)
    total_skipped = overall.get("skipped", 0)
    total_xfailed = overall.get("xfailed", 0)
    totals_scenarios = f"{total_passed}/{total_ran}"
    totals_detail_bits: list[str] = []
    if total_failed:
        totals_detail_bits.append(f"{total_failed} failed")
    if total_skipped:
        totals_detail_bits.append(f"{total_skipped} skipped")
    if total_xfailed:
        totals_detail_bits.append(f"{total_xfailed} expected failures")
    totals_detail = " &middot; ".join(totals_detail_bits)
    cov_fmt = _fmt_pct(overall.get("coverage_pct"))
    totals_row = f"""
      <tr>
        <td style="padding:14px 16px;background:{C['bg']};
                   border-top:2px solid {C['rule']};
                   font:700 11px/1 {SANS};color:{C['brand']};
                   letter-spacing:1.2px;text-transform:uppercase;">
          Totals
        </td>
        <td style="padding:14px 16px;background:{C['bg']};
                   border-top:2px solid {C['rule']};
                   font:600 13px/1.4 {SANS};color:{C['ink']};">
          {overall.get('clusters_passing', 0)} of
          {overall.get('clusters_total', 0)} capabilities passing
          <div style="font:400 11px/1.5 {SANS};color:{C['muted']};
                      margin-top:2px;">
            Total run time {_fmt_duration(overall.get('wall_s'))}
            &nbsp;&middot;&nbsp;
            Framework-wide code coverage <strong style="color:{C['ink']};">{cov_fmt}</strong>
          </div>
        </td>
        <td style="padding:14px 16px;background:{C['bg']};
                   border-top:2px solid {C['rule']};
                   font:600 14px/1.3 {SANS};color:{C['ink']};
                   vertical-align:top;">
          {totals_scenarios}
          {(f'<div style="font:400 11px/1.4 {SANS};color:{C["muted"]};'
            f'margin-top:2px;">{totals_detail}</div>')
           if totals_detail else ''}
        </td>
      </tr>
    """
    body_rows.append(totals_row)

    return f"""
    <tr>
      <td style="padding:0 32px 16px 32px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="background:{C['card']};border:1px solid {C['rule']};
                      border-radius:8px;overflow:hidden;">
          {header}
          <tbody>
            {''.join(body_rows)}
          </tbody>
        </table>
      </td>
    </tr>
    """


def _why_section(rows: list[dict[str, Any]]) -> str:
    items = []
    for row in rows:
        c = cluster_by_key(row["key"])
        if c is None:
            continue
        items.append(f"""
          <tr>
            <td style="padding:8px 0;">
              <div style="font:700 13px/1.4 {SANS};color:{C['ink']};">
                {html.escape(c.label)}
              </div>
              <div style="font:400 13px/1.6 {SANS};color:{C['muted']};margin-top:2px;">
                {c.why_it_matters}
              </div>
            </td>
          </tr>
        """)
    return f"""
    <tr>
      <td style="padding:10px 32px 26px 32px;">
        <div style="font:700 12px/1 {SANS};color:{C['brand']};
                    letter-spacing:1.5px;text-transform:uppercase;
                    padding-bottom:10px;border-bottom:2px solid {C['accent']};">
          What we test and why
        </div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="margin-top:8px;">
          {''.join(items)}
        </table>
      </td>
    </tr>
    """


# ── Scaffold ────────────────────────────────────────────────────────
def _scaffold(inner: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{html.escape(title)}</title>
    <style>
      @page {{ size: A4; margin: 14mm; }}
      body {{ margin: 0; padding: 0; }}
    </style>
  </head>
  <body style="margin:0;padding:0;background:{C['bg']};">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="background:{C['bg']};padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="720" cellpadding="0" cellspacing="0" border="0"
                 style="max-width:720px;background:#fff;border:1px solid {C['rule']};
                        border-radius:10px;overflow:hidden;">
            {inner}
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def render_email_html(manifest, *, brand, logo_src, commit_sha, branch,
                      run_url, generated_at):
    rows = manifest["clusters"]
    inner = (
        _header_band(brand, logo_src=logo_src, generated_at=generated_at)
        + _verdict_band(manifest["overall"])
        + _results_table(rows, expand_passes=False, overall=manifest["overall"])
        + _footer(commit_sha, branch, run_url,
                  manifest["overall"].get("wall_s"), for_email=True)
    )
    return _scaffold(inner, f"{brand} — Platform Health Report")


def render_pdf_html(manifest, *, brand, logo_src, commit_sha, branch,
                    run_url, generated_at):
    rows = manifest["clusters"]
    inner = (
        _header_band(brand, logo_src=logo_src, generated_at=generated_at)
        + _verdict_band(manifest["overall"])
        + _results_table(rows, expand_passes=True, overall=manifest["overall"])
        + _why_section(rows)
        + _footer(commit_sha, branch, run_url,
                  manifest["overall"].get("wall_s"), for_email=False)
    )
    return _scaffold(inner, f"{brand} — Platform Health Report")


def render_pdf(html_str: str, out_path: str) -> None:
    try:
        from weasyprint import HTML  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "WeasyPrint is required. Install with: pip install weasyprint"
        ) from e
    HTML(string=html_str, base_url=str(REPO_ROOT)).write_pdf(out_path)


# ── Manifest loader + CLI ───────────────────────────────────────────
def _empty_manifest() -> dict[str, Any]:
    """Used when the runner didn't produce a manifest (e.g. infrastructure
    crash). Ensures the email step always has *something* to render."""
    return {
        "generated_at": int(dt.datetime.now(dt.timezone.utc).timestamp()),
        "overall": {
            "ran": 0, "passed": 0, "failed": 0, "errors": 0,
            "skipped": 0, "xfailed": 0, "wall_s": 0,
            "clusters_total": 0, "clusters_passing": 0,
            "coverage_pct": None, "outcome": "failure",
        },
        "clusters": [],
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", default="manifest.json")
    p.add_argument("--brand", default=os.environ.get("SHOWCASE_BRAND", "Excellence Cloud"))
    p.add_argument("--logo-svg", default=str(DEFAULT_LOGO_SVG))
    p.add_argument("--commit-sha", default=os.environ.get("GITHUB_SHA"))
    p.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME"))
    p.add_argument("--run-url", default=(
        f"{os.environ.get('GITHUB_SERVER_URL', '')}/"
        f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
        f"{os.environ.get('GITHUB_RUN_ID', '')}"
        if os.environ.get("GITHUB_RUN_ID") else None
    ))
    p.add_argument("--out-html", default="report.html")
    p.add_argument("--out-pdf", default="report.pdf")
    p.add_argument("--out-logo", default="logo.png")
    p.add_argument("--out-email-preview", default=None)
    p.add_argument("--cid-logo", default="logo")
    p.add_argument("--skip-pdf", action="store_true")
    args = p.parse_args(argv)

    # Load (or synthesise) the manifest.
    if os.path.exists(args.manifest):
        with open(args.manifest, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    else:
        print(f"note: {args.manifest} not found — rendering empty report.",
              file=sys.stderr)
        manifest = _empty_manifest()

    # Logo.
    png_bytes = _svg_to_png_bytes(Path(args.logo_svg))
    if png_bytes:
        with open(args.out_logo, "wb") as fh:
            fh.write(png_bytes)
        print(f"Wrote {args.out_logo} ({len(png_bytes):,} bytes)")
        email_logo_src = f"cid:{args.cid_logo}"
        pdf_logo_src = _png_data_uri(png_bytes)
    else:
        print(f"note: logo not rasterised — using text wordmark fallback.",
              file=sys.stderr)
        email_logo_src = pdf_logo_src = None

    generated_at = dt.datetime.now(dt.timezone.utc)

    email_html = render_email_html(
        manifest, brand=args.brand, logo_src=email_logo_src,
        commit_sha=args.commit_sha, branch=args.branch,
        run_url=args.run_url, generated_at=generated_at,
    )
    pdf_html = render_pdf_html(
        manifest, brand=args.brand, logo_src=pdf_logo_src,
        commit_sha=args.commit_sha, branch=args.branch,
        run_url=args.run_url, generated_at=generated_at,
    )

    with open(args.out_html, "w", encoding="utf-8") as fh:
        fh.write(email_html)
    print(f"Wrote {args.out_html} (email, cid logo)")

    if args.out_email_preview and pdf_logo_src:
        preview_html = email_html.replace(
            f'src="cid:{args.cid_logo}"', f'src="{pdf_logo_src}"', 1,
        )
        with open(args.out_email_preview, "w", encoding="utf-8") as fh:
            fh.write(preview_html)
        print(f"Wrote {args.out_email_preview} (browser preview)")

    if not args.skip_pdf:
        render_pdf(pdf_html, args.out_pdf)
        print(f"Wrote {args.out_pdf} (archive)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))











