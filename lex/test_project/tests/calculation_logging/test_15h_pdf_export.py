"""Cluster 15h — Calculation-log PDF export renders like the log view.

Scenarios 15.32 – 15.38.

Intent (customer report 2026-07-14): the "Download PDF" button on a
calculation log produced a document that did not look like the on-screen
log view — markdown structure was lost. The contract this batch pins:
**anything the log view can render must appear rendered in the exported
PDF** — headings, bold/strike emphasis, lists, quotes, fenced code,
links, and tables — never as raw markdown syntax (``###``, ``**``,
``|---|``…). The PDF is generated server-side (`DownloadMarkdownPdf`),
so this is the backend half; the frontend button simply downloads what
this endpoint produces.

15.35–15.38 cover the second scope. A calculation log is a TREE — a run's
consolidated log with its steps beneath it — and "download this step" and
"download this step and everything under it" are different questions. The
endpoint answers the second with ``?include_descendants=true``, rendering the
whole subtree into ONE document. The default is untouched, which 15.35 pins.

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

    def setUp(self):
        super().setUp()
        # These logs belong to no record, so only an operator may read them (LEX_SEC_INTERNAL, 4.104).
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

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


    # -- 15.35 ---------------------------------------------------------
    def test_15_35_default_still_exports_only_that_log(self) -> None:
        """Scenario 15.35: without the flag, nothing changed. A parent's
        PDF contains its own log and NOT its children's — every existing
        caller of this endpoint depends on that."""
        parent = CalculationLog.objects.create(calculation_log="Parent body.")
        CalculationLog.objects.create(
            calculation_log="Child body.", parent_log=parent, heading="Step one"
        )

        text = _extract_pdf_text(self._download(parent.pk))

        self.assertIn("Parent body.", text)
        self.assertNotIn("Child body.", text)
        self.assertNotIn("Step one", text)

    # -- 15.36 ---------------------------------------------------------
    def test_15_36_the_subtree_exports_as_one_document(self) -> None:
        """Scenario 15.36: with the flag, the parent's log and every
        descendant's arrive in a single PDF, each under a heading a reader
        can navigate by — not as separate files and not concatenated
        anonymously."""
        parent = CalculationLog.objects.create(calculation_log="Parent body.")
        child = CalculationLog.objects.create(
            calculation_log="Child body.", parent_log=parent, heading="Data collection"
        )
        CalculationLog.objects.create(
            calculation_log="Grandchild body.", parent_log=child, heading="Input datasets"
        )

        url = self._url("download-markdown-pdf", pk=parent.pk)
        response = self.client.get(url, {"include_descendants": "true"})
        self.assertEqual(response.status_code, 200)
        text = _extract_pdf_text(response.content)

        for expected in (
            "Parent body.",
            "Data collection",
            "Child body.",
            "Input datasets",
            "Grandchild body.",
        ):
            self.assertIn(expected, text, f"{expected!r} missing from the combined PDF")

        self.assertIn("_full.pdf", response["Content-Disposition"])

    # -- 15.37 ---------------------------------------------------------
    def test_15_37_a_sibling_subtree_is_not_swept_in(self) -> None:
        """Scenario 15.37: 'everything under it' means under THAT node.
        Exporting one branch must not pull in a sibling branch, or the
        scope is meaningless and the file is just the whole run again."""
        root = CalculationLog.objects.create(calculation_log="Root body.")
        branch = CalculationLog.objects.create(
            calculation_log="Branch body.", parent_log=root, heading="Chosen branch"
        )
        CalculationLog.objects.create(
            calculation_log="Under chosen.", parent_log=branch, heading="Chosen child"
        )
        CalculationLog.objects.create(
            calculation_log="Sibling body.", parent_log=root, heading="Other branch"
        )

        url = self._url("download-markdown-pdf", pk=branch.pk)
        response = self.client.get(url, {"include_descendants": "true"})
        text = _extract_pdf_text(response.content)

        self.assertIn("Branch body.", text)
        self.assertIn("Under chosen.", text)
        self.assertNotIn("Sibling body.", text)
        self.assertNotIn("Root body.", text)

    # -- 15.38 ---------------------------------------------------------
    def test_15_38_a_cycle_returns_a_pdf_instead_of_hanging(self) -> None:
        """Scenario 15.38: ``parent_log`` is a self-referential FK, so a
        cycle is representable in the table even though nothing should
        write one. The walk must terminate — a hung request is worse than
        any document it could have produced."""
        first = CalculationLog.objects.create(calculation_log="First body.")
        second = CalculationLog.objects.create(
            calculation_log="Second body.", parent_log=first, heading="Second"
        )
        # Close the loop behind the ORM's back.
        CalculationLog.objects.filter(pk=first.pk).update(parent_log_id=second.pk)

        url = self._url("download-markdown-pdf", pk=first.pk)
        response = self.client.get(url, {"include_descendants": "true"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        text = _extract_pdf_text(response.content)
        self.assertIn("First body.", text)
        self.assertIn("Second body.", text)
