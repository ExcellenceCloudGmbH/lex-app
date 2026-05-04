#!/usr/bin/env python3
"""
Build the Platform Health Report from a manifest produced by
``run_showcase_suite.py``.

Outputs:
  * ``report.html``           — email body, tabular; passes single-line,
                                failures expand with the "what it means"
                                paragraph plus the cluster's
                                ``worst_case_outcome``.
  * ``report.pdf``            — detailed archive, every row expanded,
                                plus the "What we test and why" glossary.
  * ``logo.png``              — rasterised wordmark, attached inline by
                                the email sender (``Content-ID: logo``).
  * ``report_preview.html``   — (optional, via ``--out-email-preview``)
                                browser-openable copy of the email with
                                the logo inlined as a data URI.

The report shows ONE ROW PER CLUSTER, grouped by section
(Foundations / Engine / Surface), with columns:
    Status | Capability | Scenarios
A row tagged ``release_gate=True`` carries a small "RELEASE GATE"
pill next to its label. The verdict band leads with the headline
metric; supporting numbers move to the totals row and the
environment strip.

Per-cluster coverage was removed on 22 April 2026 — see the long
comment in ``_results_table`` for the rationale. Framework-wide
coverage still appears in the totals row.
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
from showcase_clusters import (  # noqa: E402
    CLUSTERS,
    Cluster,
    SECTION_LABELS,
    SECTION_ORDER,
    cluster_by_key,
    clusters_by_section,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGO_SVG = REPO_ROOT / "images" / "dark-lex-logo.svg"


# ── Brand palette — Excellence Cloud ────────────────────────────────
C = {
    "brand":      "#283067",
    "brand_dim":  "#3b4683",
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
    "warn_border":"#c79100",
    "gate_bg":    "#eef0fa",
    "gate_ink":   "#283067",
    "gate_border":"#c4cae8",
}
SANS = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif")
SERIF = "Georgia, 'Times New Roman', Times, serif"

# ── Style fragments — kept here so visual tweaks live in one place ──
S = {
    "section_h": (
        f"font:700 11px/1 {SANS};color:{C['brand']};"
        "letter-spacing:1.6px;text-transform:uppercase;"
    ),
    "th": (
        f"padding:12px 16px;background:{C['bg']};"
        f"border-bottom:1px solid {C['rule']};"
        f"font:700 11px/1 {SANS};color:{C['muted']};"
        "letter-spacing:1.2px;text-transform:uppercase;"
    ),
    "td": (
        f"padding:14px 16px;border-bottom:1px solid {C['rule']};"
        "vertical-align:top;"
    ),
    "label": f"font:600 14px/1.3 {SANS};color:{C['ink']};",
    "muted_sm": f"font:400 12px/1.5 {SANS};color:{C['muted']};",
    "body":  f"font:400 13px/1.6 {SANS};color:{C['ink']};",
    "kicker": (
        f"font:400 11px/1 {SANS};color:#fff;"
        "letter-spacing:2px;text-transform:uppercase;opacity:.70;"
    ),
}


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


def _row_state(row: dict[str, Any]) -> str:
    """Reduce a row to one of: pass / pass_with_skips / fail.

    A "pass with skips" row is still green — Django reports outcome
    success — but we want to flag visually that some scenarios were
    not run, so a reader doesn't read the green chip as 'every
    scenario executed cleanly'.
    """
    if row.get("outcome") != "success":
        return "fail"
    if row.get("skipped", 0) or row.get("xfailed", 0):
        return "pass_with_skips"
    return "pass"


def _outcome_chip(state: str) -> str:
    """A small status chip. Uses a coloured square plus a word — no
    Unicode glyphs in the chip body so Outlook/Exchange relays don't
    mangle the header. The colour + the word together convey state
    even if the user is colour-blind.
    """
    if state == "pass":
        bg, ink, border, dot, word = (
            C["ok_bg"], C["ok_ink"], C["ok_border"], C["ok_border"], "Pass"
        )
    elif state == "pass_with_skips":
        bg, ink, border, dot, word = (
            C["warn_bg"], C["warn_ink"], C["warn_border"], C["warn_border"],
            "Pass · skipped",
        )
    elif state == "fail":
        bg, ink, border, dot, word = (
            C["bad_bg"], C["bad_ink"], C["bad_border"], C["bad_border"], "Fail"
        )
    else:
        bg, ink, border, dot, word = (
            C["bg"], C["muted"], C["rule"], C["muted"], state.title() or "—"
        )
    return (
        f'<span style="display:inline-block;padding:5px 10px;'
        f'background:{bg};color:{ink};border:1px solid {border};'
        f'border-radius:999px;font:600 11px/1 {SANS};letter-spacing:.4px;'
        f'white-space:nowrap;">'
        f'<span style="display:inline-block;width:8px;height:8px;'
        f'background:{dot};border-radius:50%;vertical-align:middle;'
        f'margin-right:6px;"></span>'
        f'{word}</span>'
    )


def _release_gate_pill() -> str:
    return (
        f'<span style="display:inline-block;margin-left:8px;'
        f'padding:2px 7px;background:{C["gate_bg"]};color:{C["gate_ink"]};'
        f'border:1px solid {C["gate_border"]};border-radius:4px;'
        f'font:700 9px/1.4 {SANS};letter-spacing:1.1px;'
        f'text-transform:uppercase;vertical-align:middle;">'
        f'Release gate</span>'
    )


def _progress_bar(passed: int, total: int) -> str:
    """A single horizontal bar showing passed/total. Pure inline CSS,
    width-percent driven, so it renders in every email client."""
    if total <= 0:
        pct = 0
    else:
        pct = max(0, min(100, int(round(100 * passed / total))))
    fill = C["ok_border"] if pct == 100 else (
        C["warn_border"] if pct >= 80 else C["bad_border"]
    )
    return (
        f'<div style="width:100%;height:6px;background:{C["rule"]};'
        f'border-radius:3px;overflow:hidden;">'
        f'<div style="width:{pct}%;height:6px;background:{fill};"></div>'
        f'</div>'
    )


def _verdict_band(overall: dict[str, Any]) -> str:
    ok = overall["outcome"] == "success"
    has_skips = ok and (overall.get("skipped", 0) or overall.get("xfailed", 0))
    if not ok:
        bg, ink, border = C["bad_bg"], C["bad_ink"], C["bad_border"]
        title = "One or more capabilities are broken"
    elif has_skips:
        bg, ink, border = C["warn_bg"], C["warn_ink"], C["warn_border"]
        title = "All capabilities passing — some scenarios skipped"
    else:
        bg, ink, border = C["ok_bg"], C["ok_ink"], C["ok_border"]
        title = "All capabilities are working"

    headline = (
        f"{overall['clusters_passing']} of "
        f"{overall['clusters_total']} capabilities passing"
    )
    return f"""
    <tr>
      <td style="padding:24px 32px;background:{bg};">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="vertical-align:middle;padding-right:18px;">
              <div style="font:700 22px/1.2 {SERIF};color:{ink};">
                {title}
              </div>
              <div style="font:600 14px/1.4 {SANS};color:{ink};
                          margin-top:6px;opacity:.9;">
                {headline}
              </div>
            </td>
            <td align="right" style="vertical-align:middle;width:120px;">
              <div style="display:inline-block;padding:8px 14px;
                          background:{border};color:#fff;border-radius:6px;
                          font:700 13px/1.2 {SANS};letter-spacing:.5px;
                          text-transform:uppercase;white-space:nowrap;">
                {'Action required' if not ok else ('Review skipped' if has_skips else 'All clear')}
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """


def _empty_verdict_band() -> str:
    """Used when no manifest was produced at all (infrastructure
    crash). The previous version silently rendered '0 / 0' — which
    looks like a green run with nothing to do. Make it loud instead."""
    return f"""
    <tr>
      <td style="padding:24px 32px;background:{C['bad_bg']};">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="vertical-align:middle;padding-right:18px;">
              <div style="font:700 22px/1.2 {SERIF};color:{C['bad_ink']};">
                No test manifest produced
              </div>
              <div style="font:600 14px/1.4 {SANS};color:{C['bad_ink']};
                          margin-top:6px;opacity:.9;">
                The test runner did not finish. Treat this release as
                <strong>blocked</strong> until the run log is investigated.
              </div>
            </td>
            <td align="right" style="vertical-align:middle;width:120px;">
              <div style="display:inline-block;padding:8px 14px;
                          background:{C['bad_border']};color:#fff;border-radius:6px;
                          font:700 13px/1.2 {SANS};letter-spacing:.5px;
                          text-transform:uppercase;white-space:nowrap;">
                Blocked
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
                 generated_at: dt.datetime,
                 commit_sha: str | None,
                 branch: str | None,
                 version: str | None) -> str:
    # Accent stripe now carries the version + short SHA so a reader can
    # identify exactly which build this report describes without
    # scrolling to the footer.
    stripe_bits: list[str] = []
    if version:
        stripe_bits.append(f"version <strong>{html.escape(version)}</strong>")
    elif branch:
        stripe_bits.append(f"branch <strong>{html.escape(branch)}</strong>")
    if commit_sha:
        stripe_bits.append(f"commit <code style=\"font:600 11px/1 "
                           f"'SFMono-Regular',Menlo,Consolas,monospace;\">"
                           f"{html.escape(commit_sha[:8])}</code>")
    stripe = " &nbsp;·&nbsp; ".join(stripe_bits) or "&nbsp;"
    return f"""
    <tr>
      <td style="background:{C['brand']};padding:26px 32px;color:#fff;">
        <div style="{S['kicker']}">
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
      <td style="background:{C['accent']};padding:8px 32px;
                 font:500 11px/1.4 {SANS};color:{C['brand']};
                 letter-spacing:.3px;">
        {stripe}
      </td>
    </tr>
    """


def _env_strip(overall: dict[str, Any], *, python_version: str | None,
               postgres_version: str | None) -> str:
    bits: list[str] = []
    bits.append(f"<strong style=\"color:{C['ink']};\">"
                f"{_fmt_duration(overall.get('wall_s'))}</strong> wall time")
    cov = overall.get("coverage_pct")
    if cov is not None:
        bits.append(f"coverage <strong style=\"color:{C['ink']};\">"
                    f"{_fmt_pct(cov)}</strong>")
    if python_version:
        bits.append(f"Python {html.escape(python_version)}")
    if postgres_version:
        bits.append(f"PostgreSQL {html.escape(postgres_version)}")
    return f"""
    <tr>
      <td style="padding:14px 32px;background:{C['bg']};
                 border-bottom:1px solid {C['rule']};
                 font:400 12px/1.6 {SANS};color:{C['muted']};">
        {' &nbsp;·&nbsp; '.join(bits)}
      </td>
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


def _section_header_row(label: str) -> str:
    return f"""
      <tr>
        <td colspan="3"
            style="padding:18px 16px 10px 16px;background:{C['card']};
                   border-bottom:1px solid {C['rule']};">
          <div style="{S['section_h']}">{html.escape(label)}</div>
        </td>
      </tr>
    """


def _capability_cell(cluster: Cluster) -> str:
    pill = _release_gate_pill() if cluster.release_gate else ""
    return (
        f'<div style="{S["label"]}">{html.escape(cluster.label)}{pill}</div>'
        f'<div style="{S["muted_sm"]};margin-top:3px;">'
        f'{cluster.short_description}</div>'
    )


def _failure_detail(cluster: Cluster) -> str:
    """Used on a failing row in the email view. Leads with the
    customer-visible worst case so a reader's first read of a red row
    is the consequence, not the framing."""
    pieces: list[str] = []
    if cluster.worst_case_outcome:
        pieces.append(
            f'<div style="font:600 13px/1.5 {SANS};color:{C["bad_ink"]};">'
            f'Customer-visible impact: '
            f'<span style="font-weight:400;">'
            f'{html.escape(cluster.worst_case_outcome)}</span></div>'
        )
    pieces.append(
        f'<div style="font:400 13px/1.6 {SANS};color:{C["bad_ink"]};'
        f'margin-top:6px;">{cluster.what_it_means_if_broken}</div>'
    )
    return "".join(pieces)


def _success_detail(cluster: Cluster) -> str:
    return (
        f'<div style="{S["body"]}">{cluster.what_it_proves}</div>'
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
    # number, in the Totals row.
    header = f"""
      <thead>
        <tr>
          <th align="left" style="{S['th']}width:120px;">Status</th>
          <th align="left" style="{S['th']}">Capability</th>
          <th align="left" style="{S['th']}width:130px;">Scenarios</th>
        </tr>
      </thead>
    """
    rows_by_key = {r["key"]: r for r in rows}
    body_rows: list[str] = []

    # Walk sections in declaration order. Only emit a section header
    # if at least one cluster in that section appears in the manifest.
    grouped = clusters_by_section()
    zebra = 0
    for section_key in SECTION_ORDER:
        clusters_in_section = grouped.get(section_key, [])
        section_rows = [
            (c, rows_by_key[c.key]) for c in clusters_in_section
            if c.key in rows_by_key
        ]
        if not section_rows:
            continue
        body_rows.append(_section_header_row(SECTION_LABELS[section_key]))
        for cluster, row in section_rows:
            state = _row_state(row)
            row_bg = (C["bad_tint"] if state == "fail"
                      else (C["zebra"] if zebra % 2 else C["card"]))
            zebra += 1
            body_rows.append(f"""
              <tr>
                <td style="{S['td']}background:{row_bg};">
                  {_outcome_chip(state)}
                </td>
                <td style="{S['td']}background:{row_bg};">
                  {_capability_cell(cluster)}
                </td>
                <td style="{S['td']}background:{row_bg};">
                  {_scenarios_cell(row)}
                </td>
              </tr>
            """)

            failing = state == "fail"
            show_detail = failing or expand_passes
            if show_detail:
                body_bg = C["bad_tint"] if failing else row_bg
                detail = (_failure_detail(cluster) if failing
                          else _success_detail(cluster))
                body_rows.append(f"""
                  <tr>
                    <td colspan="3"
                        style="padding:0 16px 16px 16px;background:{body_bg};
                               border-bottom:1px solid {C['rule']};">
                      {detail}
                    </td>
                  </tr>
                """)

    # ── Totals row.  Appended to <tbody> (not <tfoot>) because WeasyPrint
    # treats <tfoot> as a repeating page-footer group and duplicates it on
    # every printed page. The totals row carries the scenario progress bar
    # so a reader can see at-a-glance how complete the run was.
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
    totals_bg = C["brand"]
    totals_ink = "#ffffff"
    totals_row = f"""
      <tr>
        <td style="padding:18px 16px;background:{totals_bg};
                   border-top:2px solid {C['rule']};
                   font:700 11px/1 {SANS};color:#fff;
                   letter-spacing:1.4px;text-transform:uppercase;">
          Totals
        </td>
        <td style="padding:18px 16px;background:{totals_bg};
                   border-top:2px solid {C['rule']};
                   font:600 13px/1.4 {SANS};color:{totals_ink};">
          <div>{overall.get('clusters_passing', 0)} of
          {overall.get('clusters_total', 0)} capabilities passing</div>
          <div style="margin-top:8px;max-width:380px;">
            {_progress_bar(total_passed, total_ran)}
          </div>
          <div style="font:400 11px/1.5 {SANS};color:#fff;opacity:.78;
                      margin-top:6px;">
            Total run time {_fmt_duration(overall.get('wall_s'))}
            &nbsp;&middot;&nbsp;
            Framework-wide coverage <strong>{cov_fmt}</strong>
          </div>
        </td>
        <td style="padding:18px 16px;background:{totals_bg};
                   border-top:2px solid {C['rule']};
                   font:700 16px/1.2 {SANS};color:{totals_ink};
                   vertical-align:top;">
          {totals_scenarios}
          {(f'<div style="font:400 11px/1.4 {SANS};color:#fff;opacity:.78;'
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
              <div style="{S['muted_sm']};margin-top:2px;">
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


def _is_empty_manifest(manifest: dict[str, Any]) -> bool:
    overall = manifest.get("overall") or {}
    return (overall.get("clusters_total", 0) == 0
            and overall.get("ran", 0) == 0
            and not manifest.get("clusters"))


def _verdict(manifest: dict[str, Any]) -> str:
    if _is_empty_manifest(manifest):
        return _empty_verdict_band()
    return _verdict_band(manifest["overall"])


def render_email_html(manifest, *, brand, logo_src, commit_sha, branch,
                      run_url, generated_at, version, python_version,
                      postgres_version):
    rows = manifest.get("clusters", [])
    inner = (
        _header_band(brand, logo_src=logo_src, generated_at=generated_at,
                     commit_sha=commit_sha, branch=branch, version=version)
        + _verdict(manifest)
        + (_env_strip(manifest["overall"],
                      python_version=python_version,
                      postgres_version=postgres_version)
           if not _is_empty_manifest(manifest) else "")
        + (_results_table(rows, expand_passes=False,
                          overall=manifest["overall"])
           if not _is_empty_manifest(manifest) else "")
        + _footer(commit_sha, branch, run_url,
                  manifest.get("overall", {}).get("wall_s"), for_email=True)
    )
    return _scaffold(inner, f"{brand} — Platform Health Report")


def render_pdf_html(manifest, *, brand, logo_src, commit_sha, branch,
                    run_url, generated_at, version, python_version,
                    postgres_version):
    rows = manifest.get("clusters", [])
    inner = (
        _header_band(brand, logo_src=logo_src, generated_at=generated_at,
                     commit_sha=commit_sha, branch=branch, version=version)
        + _verdict(manifest)
        + (_env_strip(manifest["overall"],
                      python_version=python_version,
                      postgres_version=postgres_version)
           if not _is_empty_manifest(manifest) else "")
        + (_results_table(rows, expand_passes=True,
                          overall=manifest["overall"])
           if not _is_empty_manifest(manifest) else "")
        + (_why_section(rows) if rows else "")
        + _footer(commit_sha, branch, run_url,
                  manifest.get("overall", {}).get("wall_s"), for_email=False)
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
    crash). Ensures the email step always has *something* to render —
    and ``_empty_verdict_band`` makes that 'something' visibly red."""
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
    p.add_argument("--version", default=os.environ.get("SHOWCASE_VERSION"),
                   help="Version label for the accent stripe (e.g. 2.0.0rc124).")
    p.add_argument("--python-version", default=os.environ.get("SHOWCASE_PYTHON_VERSION"))
    p.add_argument("--postgres-version", default=os.environ.get("SHOWCASE_POSTGRES_VERSION"))
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
    p.add_argument("--require-logo", action="store_true",
                   help="Hard-fail if the logo cannot be rasterised. Set "
                        "this in release-gate runs so a missing logo "
                        "doesn't ship a wordmark fallback to customers.")
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
        msg = (f"logo not rasterised from {args.logo_svg} — "
               "wordmark fallback will be used.")
        if args.require_logo:
            raise SystemExit(f"error: {msg} (--require-logo set)")
        print(f"note: {msg}", file=sys.stderr)
        email_logo_src = pdf_logo_src = None

    generated_at = dt.datetime.now(dt.timezone.utc)

    common_kwargs = dict(
        brand=args.brand, commit_sha=args.commit_sha, branch=args.branch,
        run_url=args.run_url, generated_at=generated_at,
        version=args.version, python_version=args.python_version,
        postgres_version=args.postgres_version,
    )
    email_html = render_email_html(
        manifest, logo_src=email_logo_src, **common_kwargs,
    )
    pdf_html = render_pdf_html(
        manifest, logo_src=pdf_logo_src, **common_kwargs,
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
