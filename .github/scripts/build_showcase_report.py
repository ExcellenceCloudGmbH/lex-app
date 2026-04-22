#!/usr/bin/env python3
"""
Build the Platform Health Report in two layouts:

  1. ``report.html``  — the **email body**: headline layout that stays
     short no matter how many tests we run. Passing tests are listed as
     compact one-line rows; only failing tests get an expanded paragraph.
     The "what we test and why" glossary is NOT included — it lives in
     the PDF.
  2. ``report.pdf``   — the **archive**: the full detailed version with
     a card per test and the glossary. Rendered via WeasyPrint.

Both share the Excellence Cloud brand palette (navy ``#283067`` +
teal ``#24b6bb``) inferred from the logo. The logo is embedded as a
base64 PNG (converted from SVG via cairosvg) so every email client —
including older Outlook, which strips inline SVG — renders it.

Usage:
    python build_showcase_report.py \\
        --init-outcome success --init-duration 0.45 \\
        --crud-outcome success --crud-duration 0.28 \\
        --out-html report.html --out-pdf report.pdf
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import os
import sys
from dataclasses import dataclass
from pathlib import Path


# ── Business mapping — single source of truth for labels/copy ────────
@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    short_description: str
    what_it_proves: str
    what_it_means_if_broken: str
    why_it_matters: str
    technical_id: str


CAPABILITIES: dict[str, Capability] = {
    "init": Capability(
        key="init",
        label="Project initialisation",
        short_description=(
            "When a customer presses <strong>Init</strong> on a new "
            "project, the platform prepares the database and access "
            "management in one step."
        ),
        what_it_proves=(
            "The platform correctly detects the customer's data model, "
            "generates the necessary database changes, applies them, and "
            "registers the project with the access-management system — "
            "all in a single command. This is the end-to-end onboarding "
            "path every new customer walks through on day one."
        ),
        what_it_means_if_broken=(
            "New customers cannot reliably onboard. The platform may "
            "leave a project in a half-configured state where either the "
            "database is missing tables or access management does not "
            "recognise the project. Engineering should be notified "
            "immediately."
        ),
        why_it_matters=(
            "Day-one onboarding is the single highest-risk moment in "
            "the customer journey. If initialisation fails silently, "
            "the customer cannot use the platform at all."
        ),
        technical_id=(
            "lex.test_project.tests.init.test_1b_lex_init."
            "TestCluster01b_LexInit.test_1_6b_init_runs_full_pipeline"
        ),
    ),
    "crud": Capability(
        key="crud",
        label="Create a record through the public API",
        short_description=(
            "A record sent to the platform's public REST API is "
            "accepted, stored, and readable on subsequent requests."
        ),
        what_it_proves=(
            "The platform's public Create API works end-to-end: a "
            "record posted by an authorised caller is validated, "
            "persisted to the database, and returned with a stable "
            "identifier that can be used to retrieve the record later. "
            "This underpins every customer-facing workflow that creates "
            "data."
        ),
        what_it_means_if_broken=(
            "Any customer-facing flow that creates data is also broken "
            "— from a user filling in a form, to an integration posting "
            "records, to a data-loader importing a batch. Engineering "
            "should be notified immediately."
        ),
        why_it_matters=(
            "Creating records is the most fundamental operation of the "
            "platform. If this is broken, no other feature matters."
        ),
        technical_id=(
            "lex.test_project.tests.crud_api.test_2a_create."
            "TestCluster02a_Create.test_2_1_post_creates_record"
        ),
    ),
}


# ── Brand palette — inferred from the Excellence Cloud logo ──────────
C = {
    "brand":      "#283067",   # deep navy — logo hexagon
    "accent":     "#24b6bb",   # teal — logo "=" band + "CLOUD" text
    "ink":        "#1a2230",
    "muted":      "#5d6b7a",
    "rule":       "#e2e5ec",
    "bg":         "#f5f7fb",
    "card":       "#ffffff",
    "ok_bg":      "#e8f5e9",
    "ok_ink":     "#1b5e20",
    "ok_border":  "#2e7d32",
    "bad_bg":     "#ffebee",
    "bad_ink":    "#b71c1c",
    "bad_border": "#c62828",
    "warn_bg":    "#fff8e1",
    "warn_ink":   "#8a6d00",
}
SANS = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif")
SERIF = "Georgia, 'Times New Roman', Times, serif"


# ── Logo loader ──────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGO_SVG = _REPO_ROOT / "images" / "dark-lex-logo.svg"


def _logo_data_uri(svg_path: Path, *, width: int = 520) -> str | None:
    """
    Convert the given SVG to a PNG and return a data URI.

    Used for email bodies: Outlook and several corporate gateways strip
    inline SVG, so we pre-render to PNG. Returns None if cairosvg is not
    installed or the file is missing — the caller then falls back to a
    text wordmark so the report still renders.
    """
    try:
        import cairosvg  # type: ignore
    except ImportError:
        return None
    if not svg_path.exists():
        return None
    try:
        png_bytes = cairosvg.svg2png(
            url=str(svg_path), output_width=width
        )
    except Exception:
        return None
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


# ── Small rendering helpers ──────────────────────────────────────────
def _verdict_palette(overall_ok: bool) -> dict[str, str]:
    return {
        "bg":     C["ok_bg"]     if overall_ok else C["bad_bg"],
        "ink":    C["ok_ink"]    if overall_ok else C["bad_ink"],
        "border": C["ok_border"] if overall_ok else C["bad_border"],
        "icon":   "✓" if overall_ok else "✗",
        "word":   (
            "All capabilities are working"
            if overall_ok
            else "One or more capabilities are broken"
        ),
    }


_OUTCOME_PALETTE = {
    "success":   ("ok",   "✓", "Passed"),
    "failure":   ("bad",  "✗", "Failed"),
    "cancelled": ("warn", "⚠", "Cancelled"),
    "skipped":   ("mute", "–", "Skipped"),
}


def _outcome_chip(outcome: str) -> str:
    kind, icon, word = _OUTCOME_PALETTE.get(
        outcome, ("mute", "?", outcome.title())
    )
    if kind == "ok":
        bg, ink, border = C["ok_bg"], C["ok_ink"], C["ok_border"]
    elif kind == "bad":
        bg, ink, border = C["bad_bg"], C["bad_ink"], C["bad_border"]
    elif kind == "warn":
        bg, ink, border = C["warn_bg"], C["warn_ink"], C["warn_ink"]
    else:
        bg, ink, border = C["bg"], C["muted"], C["rule"]
    return (
        f'<span style="display:inline-block;padding:4px 12px;'
        f'background:{bg};color:{ink};border:1px solid {border};'
        f'border-radius:999px;font:600 12px/1 {SANS};'
        f'letter-spacing:.4px;white-space:nowrap;">'
        f'{icon}&nbsp; {word}</span>'
    )


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "—"
    if seconds < 1:
        return f"{int(seconds * 1000)} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{seconds / 60:.1f} min"


def _logo_block(logo_data_uri: str | None, *, for_email: bool) -> str:
    # On dark navy header — white wordmark if logo is present, else text.
    if logo_data_uri:
        # email clients respect width attr on <img>; PDF respects max-width
        return (
            f'<img src="{logo_data_uri}" '
            f'alt="Excellence Cloud" '
            f'width="260" '
            f'style="display:block;width:260px;max-width:100%;'
            f'height:auto;border:0;outline:0;">'
        )
    return (
        f'<div style="font:700 26px/1.1 {SERIF};color:#fff;">'
        f'Excellence Cloud</div>'
    )


def _header_band(brand: str, logo_data_uri: str | None,
                 *, for_email: bool, generated_at: dt.datetime) -> str:
    return f"""
    <tr>
      <td style="background:{C['brand']};padding:26px 32px;color:#fff;">
        <div style="font:400 11px/1 {SANS};letter-spacing:2px;
                    text-transform:uppercase;opacity:.70;">
          Platform Health Report
        </div>
        <div style="margin-top:12px;">
          {_logo_block(logo_data_uri, for_email=for_email)}
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


def _verdict_band(overall_ok: bool, counts: dict[str, int]) -> str:
    v = _verdict_palette(overall_ok)
    total = sum(counts.values())
    passed = counts.get("success", 0)
    counts_line = (
        f"{passed} of {total} capabilities passing"
        if total
        else "No capabilities checked"
    )
    return f"""
    <tr>
      <td style="padding:26px 32px;background:{v['bg']};">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="vertical-align:middle;width:60px;">
              <div style="width:52px;height:52px;border-radius:50%;
                          background:{v['border']};color:#fff;
                          font:700 28px/52px {SANS};text-align:center;">
                {v['icon']}
              </div>
            </td>
            <td style="vertical-align:middle;padding-left:18px;">
              <div style="font:700 19px/1.25 {SANS};color:{v['ink']};">
                {v['word']}
              </div>
              <div style="font:400 13px/1.5 {SANS};color:{v['ink']};
                          margin-top:4px;opacity:.85;">
                {counts_line} &middot; This report covers the
                capabilities the platform promises to every customer.
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """


def _footer(commit_sha: str | None, branch: str | None,
            run_url: str | None, *, for_email: bool) -> str:
    bits: list[str] = []
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
        '</div>'
        if for_email else ""
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


def _compact_row(cap: Capability, outcome: str,
                 duration: float | None) -> str:
    """One-line row for the email layout — scales to many tests."""
    return f"""
    <tr>
      <td style="padding:14px 32px;border-bottom:1px solid {C['rule']};">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="vertical-align:middle;">
              <div style="font:600 14px/1.3 {SANS};color:{C['ink']};">
                {html.escape(cap.label)}
              </div>
              <div style="font:400 12px/1.4 {SANS};color:{C['muted']};
                          margin-top:2px;">
                Runtime {_fmt_duration(duration)}
              </div>
            </td>
            <td style="vertical-align:middle;text-align:right;
                       white-space:nowrap;padding-left:16px;">
              {_outcome_chip(outcome)}
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """


def _expanded_failure(cap: Capability, outcome: str,
                      duration: float | None) -> str:
    """Expanded card for a failure — only used in the email."""
    return f"""
    <tr>
      <td style="padding:0 32px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="margin:16px 0;border:1px solid {C['bad_border']};
                      border-radius:8px;overflow:hidden;">
          <tr>
            <td style="padding:16px 20px;background:{C['bad_bg']};
                       border-bottom:1px solid {C['bad_border']};">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td>
                    <div style="font:700 15px/1.3 {SANS};color:{C['bad_ink']};">
                      {html.escape(cap.label)}
                    </div>
                    <div style="font:400 12px/1.5 {SANS};color:{C['bad_ink']};
                                margin-top:4px;opacity:.85;">
                      Runtime {_fmt_duration(duration)}
                    </div>
                  </td>
                  <td style="text-align:right;white-space:nowrap;padding-left:16px;">
                    {_outcome_chip(outcome)}
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 20px;background:{C['card']};">
              <div style="font:400 13px/1.6 {SANS};color:{C['ink']};">
                {cap.what_it_means_if_broken}
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """


def _detailed_card(cap: Capability, outcome: str,
                   duration: float | None,
                   generated_at: dt.datetime) -> str:
    """Full card — used in the PDF for every test."""
    body = (cap.what_it_proves if outcome == "success"
            else cap.what_it_means_if_broken)
    return f"""
    <tr>
      <td style="padding:0 32px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="background:{C['card']};border:1px solid {C['rule']};
                      border-radius:8px;margin:16px 0;">
          <tr>
            <td style="padding:20px 22px 14px 22px;
                       border-bottom:1px solid {C['rule']};">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="vertical-align:top;">
                    <div style="font:700 15px/1.3 {SANS};color:{C['ink']};">
                      {html.escape(cap.label)}
                    </div>
                    <div style="font:400 13px/1.5 {SANS};color:{C['muted']};margin-top:4px;">
                      {cap.short_description}
                    </div>
                  </td>
                  <td style="vertical-align:top;text-align:right;
                             white-space:nowrap;padding-left:16px;">
                    {_outcome_chip(outcome)}
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 22px;">
              <div style="font:400 13px/1.65 {SANS};color:{C['ink']};">
                {body}
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:10px 22px 16px 22px;
                       border-top:1px solid {C['rule']};">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="font:400 11px/1.4 {SANS};color:{C['muted']};">
                    Runtime: <strong style="color:{C['ink']};">
                    {_fmt_duration(duration)}</strong>
                  </td>
                  <td style="font:400 11px/1.4 {SANS};color:{C['muted']};text-align:right;">
                    Checked {generated_at.strftime("%d %b %Y, %H:%M UTC")}
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """


def _why_section(caps: list[Capability]) -> str:
    items = "".join(
        f'''
        <tr>
          <td style="padding:8px 0;">
            <div style="font:700 13px/1.4 {SANS};color:{C['ink']};">
              {html.escape(cap.label)}
            </div>
            <div style="font:400 13px/1.6 {SANS};color:{C['muted']};margin-top:2px;">
              {cap.why_it_matters}
            </div>
          </td>
        </tr>
        ''' for cap in caps
    )
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
          {items}
        </table>
      </td>
    </tr>
    """


# ── Layouts ──────────────────────────────────────────────────────────
def _count_outcomes(results: list[tuple[Capability, str, float | None]]
                    ) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _, outcome, _ in results:
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _scaffold(inner: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{html.escape(title)}</title>
    <style>
      @page {{ size: A4; margin: 16mm; }}
      body {{ margin: 0; padding: 0; }}
    </style>
  </head>
  <body style="margin:0;padding:0;background:{C['bg']};">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="background:{C['bg']};padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"
                 style="max-width:640px;background:#fff;border:1px solid {C['rule']};
                        border-radius:10px;overflow:hidden;">
            {inner}
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def render_email_html(results: list[tuple[Capability, str, float | None]],
                      *, brand: str,
                      logo_data_uri: str | None,
                      commit_sha: str | None, branch: str | None,
                      run_url: str | None,
                      generated_at: dt.datetime) -> str:
    """
    Compact headline layout that scales to any number of tests.

    Passes → 1-line rows. Failures → expanded card with "what it means
    if broken". Glossary lives only in the PDF.
    """
    counts = _count_outcomes(results)
    overall_ok = all(o == "success" for _, o, _ in results)

    # 1. Failures first, as expanded cards (so the reader's eye lands
    #    on what needs attention immediately).
    failures = [(c, o, d) for c, o, d in results if o != "success"]
    failures_block = "".join(
        _expanded_failure(c, o, d) for c, o, d in failures
    )
    failures_heading = (f"""
    <tr>
      <td style="padding:22px 32px 4px 32px;">
        <div style="font:700 12px/1 {SANS};color:{C['bad_ink']};
                    letter-spacing:1.5px;text-transform:uppercase;">
          Needs attention ({len(failures)})
        </div>
      </td>
    </tr>
    """ if failures else "")

    # 2. Passing rows — compact.
    passes = [(c, o, d) for c, o, d in results if o == "success"]
    passes_block = "".join(
        _compact_row(c, o, d) for c, o, d in passes
    )
    passes_heading = (f"""
    <tr>
      <td style="padding:22px 32px 4px 32px;">
        <div style="font:700 12px/1 {SANS};color:{C['ok_ink']};
                    letter-spacing:1.5px;text-transform:uppercase;">
          Working ({len(passes)})
        </div>
      </td>
    </tr>
    """ if passes else "")

    inner = (
        _header_band(brand, logo_data_uri,
                     for_email=True, generated_at=generated_at)
        + _verdict_band(overall_ok, counts)
        + failures_heading + failures_block
        + passes_heading + passes_block
        + _footer(commit_sha, branch, run_url, for_email=True)
    )
    return _scaffold(inner, f"{brand} — Platform Health Report")


def render_pdf_html(results: list[tuple[Capability, str, float | None]],
                    *, brand: str,
                    logo_data_uri: str | None,
                    commit_sha: str | None, branch: str | None,
                    run_url: str | None,
                    generated_at: dt.datetime) -> str:
    """
    Full detailed version — one expanded card per test + glossary.
    """
    counts = _count_outcomes(results)
    overall_ok = all(o == "success" for _, o, _ in results)
    cards = "".join(
        _detailed_card(c, o, d, generated_at) for c, o, d in results
    )
    inner = (
        _header_band(brand, logo_data_uri,
                     for_email=False, generated_at=generated_at)
        + _verdict_band(overall_ok, counts)
        + cards
        + _why_section([c for c, _, _ in results])
        + _footer(commit_sha, branch, run_url, for_email=False)
    )
    return _scaffold(inner, f"{brand} — Platform Health Report")


# ── PDF rendering ───────────────────────────────────────────────────
def render_pdf(html_str: str, out_path: str) -> None:
    try:
        from weasyprint import HTML  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "WeasyPrint is required for PDF output. "
            "Install with: pip install weasyprint"
        ) from e
    HTML(string=html_str, base_url=str(_REPO_ROOT)).write_pdf(out_path)


# ── Entry point ─────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--init-outcome", required=True)
    p.add_argument("--init-duration", type=float, default=None)
    p.add_argument("--crud-outcome", required=True)
    p.add_argument("--crud-duration", type=float, default=None)
    p.add_argument("--brand", default=os.environ.get("SHOWCASE_BRAND", "Excellence Cloud"))
    p.add_argument("--logo-svg", default=str(DEFAULT_LOGO_SVG),
                   help="Path to the SVG used in the header band.")
    p.add_argument("--commit-sha", default=os.environ.get("GITHUB_SHA"))
    p.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME"))
    p.add_argument(
        "--run-url",
        default=(
            f"{os.environ.get('GITHUB_SERVER_URL', '')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID', '')}"
            if os.environ.get("GITHUB_RUN_ID") else None
        ),
    )
    p.add_argument("--out-html", default="report.html")
    p.add_argument("--out-pdf", default="report.pdf")
    p.add_argument("--skip-pdf", action="store_true",
                   help="Render HTML only (useful for local dev without WeasyPrint).")
    args = p.parse_args(argv)

    results: list[tuple[Capability, str, float | None]] = [
        (CAPABILITIES["init"], args.init_outcome, args.init_duration),
        (CAPABILITIES["crud"], args.crud_outcome, args.crud_duration),
    ]
    logo_uri = _logo_data_uri(Path(args.logo_svg))
    if logo_uri is None:
        print(
            f"note: logo not embedded (cairosvg missing or {args.logo_svg} "
            "unavailable) — using text wordmark fallback.",
            file=sys.stderr,
        )

    generated_at = dt.datetime.now(dt.timezone.utc)

    email_html = render_email_html(
        results,
        brand=args.brand, logo_data_uri=logo_uri,
        commit_sha=args.commit_sha, branch=args.branch, run_url=args.run_url,
        generated_at=generated_at,
    )
    pdf_html = render_pdf_html(
        results,
        brand=args.brand, logo_data_uri=logo_uri,
        commit_sha=args.commit_sha, branch=args.branch, run_url=args.run_url,
        generated_at=generated_at,
    )

    with open(args.out_html, "w", encoding="utf-8") as fh:
        fh.write(email_html)
    print(f"Wrote {args.out_html} (email layout)")

    if not args.skip_pdf:
        render_pdf(pdf_html, args.out_pdf)
        print(f"Wrote {args.out_pdf} (detailed layout)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

