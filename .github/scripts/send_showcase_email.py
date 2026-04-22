#!/usr/bin/env python3
"""
Send the showcase report via SendGrid.

Reads:
  * ``SENDGRID_API_KEY``                     — required
  * ``SHOWCASE_REPORT_RECIPIENTS``           — required, comma-separated
  * ``SHOWCASE_REPORT_FROM``                 — required, must be a
        SendGrid-verified sender
  * ``SHOWCASE_REPORT_FROM_NAME``            — optional, display name
  * ``SHOWCASE_BRAND``                       — optional, used in subject

Usage:
    python send_showcase_email.py \\
        --html-path report.html \\
        --pdf-path report.pdf \\
        --overall-ok true
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import os
import sys


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--html-path", required=True)
    p.add_argument("--pdf-path", required=True)
    p.add_argument(
        "--overall-ok", required=True,
        help="'true' or 'false' — drives the subject-line tone",
    )
    args = p.parse_args(argv)

    api_key = os.environ.get("SENDGRID_API_KEY")
    recipients_raw = os.environ.get("SHOWCASE_REPORT_RECIPIENTS", "")
    sender = os.environ.get("SHOWCASE_REPORT_FROM")
    sender_name = os.environ.get("SHOWCASE_REPORT_FROM_NAME", "Platform Health")
    brand = os.environ.get("SHOWCASE_BRAND", "LEX Platform")

    missing = [
        name for name, val in [
            ("SENDGRID_API_KEY", api_key),
            ("SHOWCASE_REPORT_RECIPIENTS", recipients_raw),
            ("SHOWCASE_REPORT_FROM", sender),
        ] if not val
    ]
    if missing:
        print(
            "Skipping email: missing required env vars: "
            + ", ".join(missing)
            + ". The HTML + PDF artefact has still been produced.",
            file=sys.stderr,
        )
        # Exit 0 — the report is still a valid artefact and we don't
        # want the whole workflow to fail just because SendGrid isn't
        # configured yet.
        return 0

    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    if not recipients:
        print("SHOWCASE_REPORT_RECIPIENTS is set but empty — skipping email.",
              file=sys.stderr)
        return 0

    try:
        from sendgrid import SendGridAPIClient  # type: ignore
        from sendgrid.helpers.mail import (  # type: ignore
            Mail, Attachment, FileContent, FileName, FileType, Disposition,
            To, From,
        )
    except ImportError as e:
        raise SystemExit(
            "sendgrid is required to send the report. "
            "Install with: pip install sendgrid"
        ) from e

    with open(args.html_path, "r", encoding="utf-8") as fh:
        html_body = fh.read()
    with open(args.pdf_path, "rb") as fh:
        pdf_bytes = fh.read()

    overall_ok = args.overall_ok.strip().lower() == "true"
    marker = "✓ all green" if overall_ok else "✗ action required"
    today = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y")
    subject = f"[{marker}] {brand} — Platform Health Report, {today}"

    message = Mail(
        from_email=From(sender, sender_name),
        to_emails=[To(r) for r in recipients],
        subject=subject,
        html_content=html_body,
    )
    message.attachment = Attachment(
        FileContent(base64.b64encode(pdf_bytes).decode()),
        FileName(f"platform-health-report-{today.replace(' ', '-')}.pdf"),
        FileType("application/pdf"),
        Disposition("attachment"),
    )

    client = SendGridAPIClient(api_key)
    response = client.send(message)
    print(f"SendGrid status: {response.status_code}")
    if response.status_code >= 300:
        print("SendGrid response body:", response.body, file=sys.stderr)
        return 1
    print(f"Sent report to: {', '.join(recipients)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

