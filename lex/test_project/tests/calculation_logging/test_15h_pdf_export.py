"""Cluster 15h — Calculation-log PDF export renders like the log view.

Scenarios 15.32 – 15.34.

Intent (customer report 2026-07-14): the "Download PDF" button on a
calculation log produced a document that did not look like the on-screen
log view — markdown structure was lost. The contract this batch pins:
**anything the log view can render must appear rendered in the exported
PDF** — headings, bold/strike emphasis, lists, quotes, fenced code,
links, and tables — never as raw markdown syntax (``###``, ``**``,
``|---|``…). The PDF is generated server-side (`DownloadMarkdownPdf`),
so this is the backend half; the frontend button simply downloads what
this endpoint produces.

Golden Rule: we assert what the customer sees in the file — content
present, markup absent — not which rendering library produced it.
"""
from __future__ import annotations

import io

from lex.audit_logging.models.CalculationLog import CalculationLog

from . import _CalcLogTestCase

import pytest

pytestmark = pytest.mark.calculation_logging

# The full feature surface the frontend log view renders (markdown-it):
# headings, bold (both ** and __ forms), strikethrough, italics, a table,
# a list, a blockquote, fenced + inline code, and a link.
FULL_SURFACE_MD = """# Investor Track Record

This example demonstrates the **enhanced Markdown logger** that supports
__DataFrames__ as markdown tables, ~~plain text only~~, *and more*.

## DataFrame Example

| Name    |   Age | City        |
|:--------|------:|:------------|
| Alice   |    30 | New York    |
| Bob     |    25 | Los Angeles |
| Charlie |    35 | Chicago     |

### Additional Features

- Text
- Headings
- Quotes

> Markdown makes formatting simple and elegant!

```python
print('Hello, Markdown!')
```

Inline `code_span` too, and a [documentation link](https://example.com/docs).
"""


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Concatenated text of every page (pypdf)."""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return "".join(page.extract_text() for page in reader.pages)


class TestCluster15h_PdfExport(_CalcLogTestCase):
    """GET api/download-pdf/<pk>/ — the exported PDF mirrors the log view."""

    def _download(self, pk) -> bytes:
        url = self._url("download-markdown-pdf", pk=pk)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        return b"".join(response.streaming_content) if getattr(
            response, "streaming", False
        ) else response.content

    # -- 15.32 ---------------------------------------------------------
    def test_15_32_endpoint_returns_a_real_pdf(self) -> None:
        """Scenario 15.32: the endpoint returns an actual PDF document
        (magic bytes + attachment disposition), not an error page."""
        log = CalculationLog.objects.create(calculation_log="# Title\n\nBody.")
        url = self._url("download-markdown-pdf", pk=log.pk)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertTrue(
            response.content.startswith(b"%PDF"),
            "Response body is not a PDF document",
        )

    # -- 15.33 ---------------------------------------------------------
    def test_15_33_markdown_is_rendered_not_raw(self) -> None:
        """Scenario 15.33: markdown STRUCTURE never leaks into the PDF as
        raw syntax. A customer reading the file must see formatted
        content — a ``###`` or ``|---|`` in the text means the export is
        a text dump, exactly the reported bug."""
        log = CalculationLog.objects.create(calculation_log=FULL_SURFACE_MD)
        text = _extract_pdf_text(self._download(log.pk))

        for raw_token in ("###", "|---", "**", "~~", "```", "](https"):
            self.assertNotIn(
                raw_token, text,
                f"Raw markdown syntax {raw_token!r} leaked into the PDF text — "
                f"the markdown was not rendered.",
            )

    # -- 15.34 ---------------------------------------------------------
    def test_15_34_every_log_view_feature_survives_into_the_pdf(self) -> None:
        """Scenario 15.34: every content element the log view renders is
        present in the PDF — headings, emphasis bodies, table cells
        (all three rows), list items, the quote, code (fenced + inline),
        and the link text. A missing table row or a dropped code block is
        the 'does not look like the log view' bug."""
        log = CalculationLog.objects.create(calculation_log=FULL_SURFACE_MD)
        text = _extract_pdf_text(self._download(log.pk))

        expected_contents = [
            "Investor Track Record",         # h1
            "DataFrame Example",             # h2
            "Additional Features",           # h3
            "enhanced Markdown logger",      # **bold**
            "DataFrames",                    # __bold__ (log view renders this)
            "plain text only",               # ~~strike~~ body text
            "Alice", "Bob", "Charlie",       # all table rows
            "New York", "Los Angeles", "Chicago",
            "simple and elegant",            # blockquote
            "Hello, Markdown",               # fenced code
            "code_span",                     # inline code
            "documentation link",            # link text
        ]
        for content in expected_contents:
            self.assertIn(
                content, text,
                f"Log-view content {content!r} missing from the exported PDF.",
            )
