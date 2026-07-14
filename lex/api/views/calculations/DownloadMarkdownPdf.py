import io
import logging

import markdown2
from django.http import HttpResponse
from lex.audit_logging.models.CalculationLog import CalculationLog
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

# Markdown extras chosen to match what the frontend log view renders
# (markdown-it, default preset): tables, fenced code blocks, and
# strikethrough. Deliberately NOT "code-friendly" — that extra disables
# ``__bold__`` intra-word emphasis, which the log view DOES render, so the
# PDF would silently diverge from the on-screen log.
MARKDOWN_EXTRAS = ["tables", "fenced-code-blocks", "strike"]

# GitHub-style stylesheet mirroring the frontend calculation-log view
# (CalculationLogDialog / CalculationLogTree): sans-serif body, bordered
# headings, shaded monospace code blocks, bordered tables with a tinted
# header row, and a left-rail blockquote. ``pre-wrap`` keeps long log lines
# on the page instead of overflowing the sheet.
PDF_STYLESHEET = """
@page {
  size: A4;
  margin: 18mm 16mm;
}
body {
  font-family: Helvetica, Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.55;
  color: #24292f;
}
h1, h2, h3, h4, h5, h6 {
  color: #1f2328;
  border-bottom: 1px solid #d0d7de;
  padding-bottom: 0.3em;
  margin-top: 20px;
  margin-bottom: 12px;
  page-break-after: avoid;
}
h1 { font-size: 17pt; }
h2 { font-size: 14pt; }
h3 { font-size: 12pt; }
p { margin: 0 0 8px 0; }
ul, ol { padding-left: 2em; margin: 0 0 8px 0; }
li { margin: 2px 0; }
a { color: #0969da; text-decoration: underline; }
img { max-width: 100%; }
blockquote {
  color: #656d76;
  border-left: 3px solid #d0d7de;
  padding: 0 1em;
  margin: 8px 0;
}
pre {
  background-color: #f6f8fa;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  padding: 10px;
  font-family: Courier, monospace;
  font-size: 9pt;
  white-space: pre-wrap;
  word-wrap: break-word;
  page-break-inside: avoid;
}
code {
  background-color: #eff1f3;
  padding: 0.15em 0.35em;
  border-radius: 4px;
  font-family: Courier, monospace;
  font-size: 9pt;
}
pre code { background-color: transparent; padding: 0; }
table {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  page-break-inside: auto;
}
th, td {
  border: 1px solid #d0d7de;
  padding: 5px 9px;
  text-align: left;
  font-size: 9.5pt;
}
th { background-color: #f6f8fa; font-weight: bold; }
tr { page-break-inside: avoid; }
del { color: #656d76; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 14px 0; }
"""


def render_markdown_to_html(md_text: str) -> str:
    """Markdown → HTML with the same feature surface the log view renders."""
    return markdown2.markdown(md_text or "", extras=MARKDOWN_EXTRAS)


def build_document_html(md_text: str) -> str:
    """Full printable HTML document for a calculation log."""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<style>{PDF_STYLESHEET}</style></head>"
        f"<body>{render_markdown_to_html(md_text)}</body></html>"
    )


def render_pdf_bytes(full_html: str) -> bytes:
    """Render HTML → PDF.

    WeasyPrint first (near-browser CSS fidelity — the PDF matches the
    on-screen log view); if it is unavailable or fails at runtime (its
    system libraries — pango/cairo — are not guaranteed on every target),
    fall back to xhtml2pdf so the endpoint keeps working with reduced
    styling rather than 500ing.
    """
    try:
        import weasyprint

        return weasyprint.HTML(string=full_html).write_pdf()
    except Exception:
        logger.warning(
            "WeasyPrint unavailable or failed; falling back to xhtml2pdf",
            exc_info=True,
        )
        from xhtml2pdf import pisa

        result = io.BytesIO()
        status = pisa.CreatePDF(src=full_html, dest=result)
        if status.err:
            raise RuntimeError("xhtml2pdf failed to render the document")
        return result.getvalue()


class DownloadMarkdownPdf(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk, format=None):
        obj = CalculationLog.objects.filter(pk=pk).first()
        md_text = (obj.calculation_log if obj else "") or ""

        full_html = build_document_html(md_text)
        try:
            pdf_bytes = render_pdf_bytes(full_html)
        except Exception:
            logger.exception("Calculation-log PDF rendering failed for pk=%s", pk)
            return HttpResponse("Error generating PDF", status=500)

        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="document_{pk}.pdf"'
        return resp
