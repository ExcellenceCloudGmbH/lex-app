#!/usr/bin/env python3
"""
Send the Platform Health Report via SendGrid.

Attaches the PDF as a regular attachment and the logo PNG as an
**inline** attachment (``Content-ID: logo``) so the email body's
``<img src="cid:logo">`` renders in every major client — Gmail,
Apple Mail, Outlook, and corporate relays that strip ``data:`` URIs.

Env vars (required):
    SENDGRID_API_KEY
    SHOWCASE_REPORT_RECIPIENTS   comma-separated
    SHOWCASE_REPORT_FROM         SendGrid-verified sender

Env vars (optional):
    SHOWCASE_REPORT_FROM_NAME    sender display name
    SHOWCASE_BRAND               used in the subject line

Usage:
    python send_showcase_email.py \\
        --html-path report.html \\
        --pdf-path report.pdf \\
        --logo-path logo.png \\
        --logo-cid logo \\
        --overall-ok true
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import os
import sys
import time


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--html-path", required=True)
    p.add_argument("--pdf-path", required=True)
    p.add_argument("--logo-path", default=None,
                   help="Path to the inline logo PNG (matches cid:<logo>)")
    p.add_argument("--logo-cid", default="logo")
    p.add_argument("--overall-ok", required=True,
                   help="'true' or 'false' — shapes the subject line")
    p.add_argument("--clusters-passing", default=None,
                   help="e.g. '14/14' — included in the subject when provided")
    args = p.parse_args(argv)

    api_key = os.environ.get("SENDGRID_API_KEY")
    recipients_raw = os.environ.get("SHOWCASE_REPORT_RECIPIENTS", "")
    sender = os.environ.get("SHOWCASE_REPORT_FROM")
    sender_name = os.environ.get("SHOWCASE_REPORT_FROM_NAME",
                                 "Platform Health")
    brand = os.environ.get("SHOWCASE_BRAND", "Excellence Cloud")

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
        return 0

    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    if not recipients:
        print("SHOWCASE_REPORT_RECIPIENTS is empty — skipping email.",
              file=sys.stderr)
        return 0

    try:
        from sendgrid import SendGridAPIClient  # type: ignore
        from sendgrid.helpers.mail import (  # type: ignore
            Mail, Attachment, FileContent, FileName, FileType, Disposition,
            ContentId, To, From,
        )
    except ImportError as e:
        raise SystemExit(
            "sendgrid is required. Install with: pip install sendgrid"
        ) from e

    with open(args.html_path, "r", encoding="utf-8") as fh:
        html_body = fh.read()
    with open(args.pdf_path, "rb") as fh:
        pdf_bytes = fh.read()

    overall_ok = args.overall_ok.strip().lower() == "true"
    # Use plain ASCII markers in the subject — Outlook and a number of
    # corporate relays mangle U+2713 / U+2717 in headers, which makes
    # mail rules and inbox filters unreliable. The HTML body is free
    # to use any glyph it likes; the *subject* stays portable.
    marker = "PASS" if overall_ok else "FAIL"
    cluster_tag = f" - {args.clusters_passing} clusters passing" if args.clusters_passing else ""
    today = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y")
    subject = f"[{marker}{cluster_tag}] {brand} - Platform Health Report, {today}"

    message = Mail(
        from_email=From(sender, sender_name),
        to_emails=[To(r) for r in recipients],
        subject=subject,
        html_content=html_body,
    )

    attachments: list = []

    # PDF — regular attachment.
    pdf_attachment = Attachment(
        FileContent(base64.b64encode(pdf_bytes).decode()),
        FileName(f"platform-health-report-{today.replace(' ', '-')}.pdf"),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    attachments.append(pdf_attachment)

    # Logo — inline attachment, referenced by ``<img src="cid:logo">``.
    if args.logo_path and os.path.exists(args.logo_path):
        with open(args.logo_path, "rb") as fh:
            logo_bytes = fh.read()
        logo_attachment = Attachment(
            FileContent(base64.b64encode(logo_bytes).decode()),
            FileName(os.path.basename(args.logo_path)),
            FileType("image/png"),
            Disposition("inline"),
        )
        logo_attachment.content_id = ContentId(args.logo_cid)
        attachments.append(logo_attachment)
    else:
        print(f"note: no logo attached (path '{args.logo_path}' not found) — "
              "email will use the text wordmark fallback from the HTML.",
              file=sys.stderr)

    message.attachment = attachments

    client = SendGridAPIClient(api_key)

    # Retry on 5xx with exponential backoff. SendGrid edge nodes
    # occasionally bounce a request with a transient 5xx during
    # incident windows; we don't want a release-day report email to
    # be lost just because the first POST was unlucky. 4xx is left
    # un-retried — those are caller errors (auth, bad sender, rate
    # limit) where retrying just papers over the real problem.
    max_attempts = 4
    backoff_s = 2.0
    response = None
    for attempt in range(1, max_attempts + 1):
        response = client.send(message)
        status = response.status_code
        print(f"SendGrid status: {status} (attempt {attempt}/{max_attempts})")
        if status < 500:
            break
        if attempt == max_attempts:
            break
        sleep_s = backoff_s * (2 ** (attempt - 1))
        print(f"  transient {status} — retrying in {sleep_s:.1f}s",
              file=sys.stderr)
        time.sleep(sleep_s)

    assert response is not None
    if response.status_code >= 300:
        print("SendGrid response body:", response.body, file=sys.stderr)
        return 1
    print(f"Sent report to: {', '.join(recipients)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


