#!/usr/bin/env python3
"""
Build a customer-facing showcase report in HTML + PDF form.

The report is the sole artefact a non-technical stakeholder needs to
read to understand whether the two "showcase" capabilities of the
platform are working. It:

  * opens with a single verdict line (all green / something is broken)
  * shows one card per capability — business label, plain-English
    "what this proves", runtime, verdict
  * includes a "what we test and why" section that explains the
    capabilities in customer terms
  * carries a traceability footer (commit sha, branch, workflow run URL)

The HTML is deliberately 1998-style table-layout with inline CSS so it
renders correctly in Outlook, Gmail and Apple Mail. The same HTML is
rendered to PDF via WeasyPrint so the email body and the attached PDF
are visually identical.

Usage:
    python build_showcase_report.py \\
        --init-outcome success --init-duration 0.45 \\
        --crud-outcome success --crud-duration 0.28 \\
        --out-html report.html --out-pdf report.pdf
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import sys
from dataclasses import dataclass


# ── Business mapping — single source of truth for labels/copy ────────
@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    short_description: str      # one-liner under the label
    what_it_proves: str         # paragraph when the test passes
    what_it_means_if_broken: str  # paragraph when the test fails
    why_it_matters: str         # used in the "what we test" section
    technical_id: str           # for the traceability footer


CAPABILITIES: dict[str, Capability] = {
    "init": Capability(
        key="init",
        label="Project initialisation",
        short_description=(
            "When a customer presses <strong>Init</strong> on a new project, "
            "the platform prepares the database and access management in one step."
        ),
        what_it_proves=(
            "The platform correctly detects the customer's data model, "
            "generates the necessary database changes, applies them, and "
            "registers the project with the access-management system — "
            "all in a single command. This is the end-to-end onboarding "
            "path every new customer walks through on day one."
        ),
        what_it_means_if_broken=(
            "New customers cannot reliably onboard. The platform may leave "
            "a project in a half-configured state where either the database "
            "is missing tables or access management does not recognise the "
            "project. Engineering should be notified immediately."
        ),
        why_it_matters=(
            "Day-one onboarding is the single highest-risk moment in the "
            "customer journey. If Init fails silently, the customer cannot "
            "use the platform at all."
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
            "A record sent to the platform's public REST API is accepted, "
            "stored, and readable on subsequent requests."
        ),
        what_it_proves=(
            "The platform's public Create API works end-to-end: a record "
            "posted by an authorised caller is validated, persisted to the "
            "database, and returned with a stable identifier that can be "
            "used to retrieve the record later. This underpins every "
            "customer-facing workflow that creates data."
        ),
        what_it_means_if_broken=(
            "Any customer-facing flow that creates data is also broken — "
            "from a user filling in a form, to an integration posting "
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


# ── Styling ──────────────────────────────────────────────────────────
# All CSS is inline on elements (email-safe). Variables below are
# copy-once-used-many so colours/spacing stay consistent.
C = {
    "brand":      "#0f2a4a",   # deep navy
    "ink":        "#1a1a1a",
    "muted":      "#5d6b7a",
    "rule":       "#e0e4ea",
    "bg":         "#f7f9fc",
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


def _verdict_palette(overall_ok: bool) -> dict[str, str]:
    return {
        "bg":     C["ok_bg"]     if overall_ok else C["bad_bg"],
        "ink":    C["ok_ink"]    if overall_ok else C["bad_ink"],
        "border": C["ok_border"] if overall_ok else C["bad_border"],
        "icon":   "✓"            if overall_ok else "✗",
        "word":   "All capabilities are working" if overall_ok else "One or more capabilities are broken",
    }


def _outcome_chip(outcome: str) -> str:
    palette = {
        "success":   (C["ok_bg"], C["ok_ink"], C["ok_border"], "✓", "PASSED"),
        "failure":   (C["bad_bg"], C["bad_ink"], C["bad_border"], "✗", "FAILED"),
        "cancelled": (C["warn_bg"], C["warn_ink"], C["warn_ink"], "⚠", "CANCELLED"),
        "skipped":   (C["bg"], C["muted"], C["rule"], "–", "SKIPPED"),
    }
    bg, ink, border, icon, word = palette.get(
        outcome, (C["bg"], C["muted"], C["rule"], "?", outcome.upper())
    )
    return (
        f'<span style="display:inline-block;padding:4px 12px;'
        f'background:{bg};color:{ink};border:1px solid {border};'
        f'border-radius:999px;font:600 12px/1 {SANS};letter-spacing:.5px;">'
        f'{icon}&nbsp; {word}</span>'
    )


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 1:
        return f"{int(seconds * 1000)} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{seconds / 60:.1f} min"


# ── Report template ─────────────────────────────────────────────────
def render_html(
    init_outcome: str, init_duration: float | None,
    crud_outcome: str, crud_duration: float | None,
    *,
    brand: str,
    commit_sha: str | None,
    branch: str | None,
    run_url: str | None,
    generated_at: dt.datetime,
) -> str:
    outcomes = {"init": init_outcome, "crud": crud_outcome}
    durations = {"init": init_duration, "crud": crud_duration}
    overall_ok = all(o == "success" for o in outcomes.values())
    verdict = _verdict_palette(overall_ok)

    # ── Header band ────────────────────────────────────────────────
    header = f"""
    <tr>
      <td style="background:{C['brand']};padding:28px 32px;color:#fff;">
        <div style="font:400 12px/1 {SANS};letter-spacing:2px;text-transform:uppercase;opacity:.75;">
          Platform Health Report
        </div>
        <div style="font:700 26px/1.2 {SERIF};margin-top:6px;">
          {html.escape(brand)}
        </div>
        <div style="font:400 13px/1.4 {SANS};margin-top:10px;opacity:.85;">
          Generated {generated_at.strftime("%A, %d %B %Y at %H:%M UTC")}
        </div>
      </td>
    </tr>
    """

    # ── Verdict band ───────────────────────────────────────────────
    verdict_band = f"""
    <tr>
      <td style="padding:28px 32px;background:{verdict['bg']};border-top:4px solid {verdict['border']};">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="vertical-align:middle;width:64px;">
              <div style="width:56px;height:56px;border-radius:50%;
                          background:{verdict['border']};color:#fff;
                          font:700 32px/56px {SANS};text-align:center;">
                {verdict['icon']}
              </div>
            </td>
            <td style="vertical-align:middle;padding-left:20px;">
              <div style="font:700 20px/1.3 {SANS};color:{verdict['ink']};">
                {verdict['word']}
              </div>
              <div style="font:400 14px/1.5 {SANS};color:{verdict['ink']};margin-top:4px;opacity:.85;">
                This report covers the two core capabilities the platform
                promises to every customer.
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """

    # ── Capability cards ───────────────────────────────────────────
    cards_html = []
    for cap in (CAPABILITIES["init"], CAPABILITIES["crud"]):
        outcome = outcomes[cap.key]
        duration = durations[cap.key]
        body_copy = (
            cap.what_it_proves if outcome == "success"
            else cap.what_it_means_if_broken
        )
        cards_html.append(f"""
        <tr>
          <td style="padding:0 32px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background:{C['card']};border:1px solid {C['rule']};
                          border-radius:8px;margin:20px 0;">
              <tr>
                <td style="padding:22px 24px 16px 24px;border-bottom:1px solid {C['rule']};">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td style="vertical-align:top;">
                        <div style="font:700 16px/1.3 {SANS};color:{C['ink']};">
                          {html.escape(cap.label)}
                        </div>
                        <div style="font:400 13px/1.5 {SANS};color:{C['muted']};margin-top:4px;">
                          {cap.short_description}
                        </div>
                      </td>
                      <td style="vertical-align:top;text-align:right;white-space:nowrap;padding-left:16px;">
                        {_outcome_chip(outcome)}
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
              <tr>
                <td style="padding:18px 24px;">
                  <div style="font:400 14px/1.6 {SANS};color:{C['ink']};">
                    {body_copy}
                  </div>
                </td>
              </tr>
              <tr>
                <td style="padding:12px 24px 18px 24px;border-top:1px solid {C['rule']};">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td style="font:400 12px/1.4 {SANS};color:{C['muted']};">
                        Runtime: <strong style="color:{C['ink']};">{_fmt_duration(duration)}</strong>
                      </td>
                      <td style="font:400 12px/1.4 {SANS};color:{C['muted']};text-align:right;">
                        Checked {generated_at.strftime("%d %b %Y, %H:%M UTC")}
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        """)

    # ── "Why these capabilities" section ──────────────────────────
    why_items = []
    for cap in (CAPABILITIES["init"], CAPABILITIES["crud"]):
        why_items.append(f"""
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
        """)
    why_section = f"""
    <tr>
      <td style="padding:8px 32px 28px 32px;">
        <div style="font:700 13px/1 {SANS};color:{C['muted']};
                    letter-spacing:1.5px;text-transform:uppercase;
                    padding-bottom:10px;border-bottom:1px solid {C['rule']};">
          What we test and why
        </div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="margin-top:8px;">
          {''.join(why_items)}
        </table>
      </td>
    </tr>
    """

    # ── Footer ────────────────────────────────────────────────────
    traceability_rows = []
    if commit_sha:
        traceability_rows.append(
            f'<div>Build identifier: <code style="font:500 12px/1 '
            f'\'SFMono-Regular\',Menlo,Consolas,monospace;color:{C["ink"]};">'
            f'{html.escape(commit_sha[:12])}</code></div>'
        )
    if branch:
        traceability_rows.append(f'<div>Branch: {html.escape(branch)}</div>')
    if run_url:
        traceability_rows.append(
            f'<div><a href="{html.escape(run_url)}" '
            f'style="color:{C["brand"]};text-decoration:underline;">'
            f'View the full run log</a></div>'
        )
    footer = f"""
    <tr>
      <td style="padding:20px 32px 28px 32px;background:{C['bg']};
                 border-top:1px solid {C['rule']};
                 font:400 12px/1.6 {SANS};color:{C['muted']};">
        <div style="margin-bottom:8px;font-weight:600;color:{C['ink']};">
          Traceability
        </div>
        {''.join(traceability_rows) or '<div>—</div>'}
        <div style="margin-top:14px;padding-top:14px;border-top:1px solid {C['rule']};">
          This report was generated automatically after an automated
          test run. If anything in this report is unclear, or if you
          see a capability marked as broken, please contact engineering.
        </div>
      </td>
    </tr>
    """

    # ── Outer scaffold ────────────────────────────────────────────
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{html.escape(brand)} — Platform Health Report</title>
    <style>
      @page {{ size: A4; margin: 18mm; }}
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
            {header}
            {verdict_band}
            {''.join(cards_html)}
            {why_section}
            {footer}
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


# ── PDF rendering ───────────────────────────────────────────────────
def render_pdf(html_str: str, out_path: str) -> None:
    try:
        from weasyprint import HTML  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "WeasyPrint is required for PDF output. "
            "Install with: pip install weasyprint"
        ) from e
    HTML(string=html_str).write_pdf(out_path)


# ── Entry point ─────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--init-outcome", required=True)
    p.add_argument("--init-duration", type=float, default=None)
    p.add_argument("--crud-outcome", required=True)
    p.add_argument("--crud-duration", type=float, default=None)
    p.add_argument("--brand", default=os.environ.get("SHOWCASE_BRAND", "LEX Platform"))
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
                   help="Render HTML only (useful for local dev without WeasyPrint)")
    args = p.parse_args(argv)

    html_str = render_html(
        args.init_outcome, args.init_duration,
        args.crud_outcome, args.crud_duration,
        brand=args.brand,
        commit_sha=args.commit_sha,
        branch=args.branch,
        run_url=args.run_url,
        generated_at=dt.datetime.now(dt.timezone.utc),
    )
    with open(args.out_html, "w", encoding="utf-8") as fh:
        fh.write(html_str)
    print(f"Wrote {args.out_html}")

    if not args.skip_pdf:
        render_pdf(html_str, args.out_pdf)
        print(f"Wrote {args.out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


