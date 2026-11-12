import io
import logging

import markdown2
from django.http import Http404, HttpResponse
from lex.audit_logging.mixins.FileOperationAuditMixin import FileOperationAuditMixin
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.file_operation_audit import ACTION_DOWNLOAD
from rest_framework.exceptions import ValidationError
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


# Cap on how many rows one combined document may pull in. Set high enough that
# no real run reaches it; when it IS reached the document says so rather than
# ending mid-run, because a silently truncated log reads exactly like a log of a
# calculation that stopped early.
MAX_SUBTREE_NODES = 500

_TRUTHY = {"1", "true", "yes", "on"}


def _wants_descendants(raw: "str | None") -> bool:
    return str(raw or "").strip().lower() in _TRUTHY


#: The three things a reader can ask the endpoint for. ``node`` is one log,
#: ``subtree`` that log and everything under it, ``calculation`` every log of
#: the calculation the log belongs to -- the whole execution tree, whichever
#: node the reader happened to have selected.
SCOPE_NODE = "node"
SCOPE_SUBTREE = "subtree"
SCOPE_CALCULATION = "calculation"
SCOPES = (SCOPE_NODE, SCOPE_SUBTREE, SCOPE_CALCULATION)

_FILENAME_SUFFIX = {
    SCOPE_NODE: "",
    SCOPE_SUBTREE: "_full",
    SCOPE_CALCULATION: "_calculation",
}


def resolve_scope(query_params) -> str:
    """Which scope a request asked for.

    ``?scope=`` wins when present. Without it, ``?include_descendants=true``
    still means ``subtree`` -- that flag predates ``scope`` and released
    frontends send it. An unknown scope is a 400 rather than a quiet fallback
    to one log: a reader who asked for the whole calculation and received one
    step would have no way to tell from the file.
    """
    raw = query_params.get("scope")
    if raw is not None and str(raw).strip():
        scope = str(raw).strip().lower()
        if scope not in SCOPES:
            raise ValidationError(
                {"scope": f"Unknown scope {raw!r}; expected one of: {', '.join(SCOPES)}."}
            )
        return scope
    if _wants_descendants(query_params.get("include_descendants")):
        return SCOPE_SUBTREE
    return SCOPE_NODE


def _node_title(log: CalculationLog, index: int) -> str:
    """A heading a person can navigate by.

    ``str(log)`` is deliberately NOT used: for model-backed rows it returns
    ``CalculationLog object (49)``, which is Django's default and tells a reader
    nothing. Section rows carry a real title in ``heading``; model-backed rows
    are named after the object they logged.
    """
    if log.heading:
        return log.heading
    calculatable = log.calculatable_object
    if calculatable is not None:
        return str(calculatable)
    return f"Section {index}"


def collect_subtree(root: CalculationLog, cap: int = MAX_SUBTREE_NODES):
    """The root, then every descendant, in pre-order (document order).

    Gathers breadth-first so the number of queries is bounded by DEPTH rather
    than by node count, then re-orders depth-first so the document reads the way
    the log tree displays it.

    ``parent_log`` is a self-referential FK, so a cycle is representable in the
    table even though nothing should write one; the visited set is what stops a
    bad row from hanging the request instead of returning a PDF.

    Returns ``(ordered, truncated)`` where ordered is a list of
    ``(log, depth)`` pairs.
    """
    by_parent: "dict[int, list[CalculationLog]]" = {}
    seen = {root.pk}
    frontier = [root.pk]
    truncated = False

    while frontier and not truncated:
        children = list(
            CalculationLog.objects.filter(parent_log_id__in=frontier).order_by("id")
        )
        frontier = []
        for child in children:
            if child.pk in seen:
                continue
            if len(seen) >= cap:
                truncated = True
                break
            seen.add(child.pk)
            by_parent.setdefault(child.parent_log_id, []).append(child)
            frontier.append(child.pk)

    ordered = []
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        ordered.append((node, depth))
        # Reversed so the explicit stack yields children left-to-right.
        for child in reversed(by_parent.get(node.pk, [])):
            stack.append((child, depth + 1))

    return ordered, truncated


def collect_calculation(log: CalculationLog, cap: "int | None" = None):
    """Every log of ``log``'s calculation, in the order the execution tree shows.

    The execution tree is every row sharing a ``calculationId``; a row whose
    parent is not in that set is one of its roots. The same rule is applied
    here, so "the whole calculation" is exactly what the reader was looking at
    -- including a calculation with more than one root, which a walk down from
    the topmost ancestor of the selected node would miss.

    One query, bounded by ``cap``. Rows are taken in id order, and a parent row
    is written before its children, so truncation drops the end of the run
    rather than orphaning its middle.

    A cycle has no root and would never be reached from one; its rows are
    appended as roots of their own, so nothing the table holds goes missing.

    Returns ``(ordered, truncated)`` like :func:`collect_subtree`.
    """
    if cap is None:
        cap = MAX_SUBTREE_NODES
    rows = list(
        CalculationLog.objects.filter(calculationId=log.calculationId).order_by("id")[: cap + 1]
    )
    truncated = len(rows) > cap
    rows = rows[:cap]

    ids = {row.pk for row in rows}
    by_parent: "dict[int, list[CalculationLog]]" = {}
    roots = []
    for row in rows:
        if row.parent_log_id is None or row.parent_log_id not in ids:
            roots.append(row)
        else:
            by_parent.setdefault(row.parent_log_id, []).append(row)

    ordered = []
    seen: "set[int]" = set()

    def walk(start):
        stack = [(start, 0)]
        while stack:
            node, depth = stack.pop()
            if node.pk in seen:
                continue
            seen.add(node.pk)
            ordered.append((node, depth))
            for child in reversed(by_parent.get(node.pk, [])):
                stack.append((child, depth + 1))

    for root in roots:
        walk(root)
    for row in rows:
        if row.pk not in seen:
            walk(row)

    return ordered, truncated


def build_subtree_markdown(ordered, truncated: bool, title_roots: bool = False) -> str:
    """One markdown document from a log and its descendants.

    The root contributes its body with no synthetic heading -- it IS the
    document, and its log usually opens with a title of its own. Every
    descendant gets a heading at its tree depth, so the PDF carries the shape
    the log tree showed instead of running the sections together.

    ``title_roots`` is for a document with several roots (a whole calculation):
    there no single root is the document, so each root gets a top-level
    heading too, or the reader could not tell where one ends and the next
    begins.
    """
    parts: "list[str]" = []
    for index, (log, depth) in enumerate(ordered):
        body = (log.calculation_log or "").strip()
        if depth == 0 and not title_roots:
            if body:
                parts.append(body)
            continue
        # Markdown has six heading levels; deeper nesting flattens onto the last
        # rather than emitting ``#######``, which renders as literal text.
        level = min(depth + 1, 6)
        parts.append(f"{'#' * level} {_node_title(log, index)}")
        parts.append(body if body else "_No output._")

    if truncated:
        parts.append(
            "---\n\n"
            f"_This document stops at {MAX_SUBTREE_NODES} sections. The "
            "calculation has more; open the log tree to read the rest._"
        )

    return "\n\n".join(parts)


class DownloadMarkdownPdf(FileOperationAuditMixin, APIView):
    permission_classes = [IsAuthenticated]
    audit_action = ACTION_DOWNLOAD

    def audit_model(self, request, *args, **kwargs):
        return CalculationLog

    def audit_request_payload(self, request, *args, **kwargs):
        # The payload is opened before ``get`` runs. A bad scope is rejected
        # there; here it is recorded as asked, so the refusal's entry says what
        # was requested.
        try:
            scope = resolve_scope(request.query_params)
        except ValidationError:
            scope = request.query_params.get("scope")
        return {
            "pk": kwargs.get("pk"),
            "format": "pdf",
            "delivery": "stream",
            "include_descendants": scope in (SCOPE_SUBTREE, SCOPE_CALCULATION),
            "scope": scope,
        }

    def get(self, request, pk, format=None):
        """One calculation log as PDF -- or more of its tree, as one document.

        ``?scope=`` picks how much: ``node`` (the default, exactly what this
        endpoint always returned), ``subtree`` (the log and everything under
        it; ``?include_descendants=true`` is the older spelling), or
        ``calculation`` (every log of the calculation the log belongs to --
        the whole execution tree).
        """
        scope = resolve_scope(request.query_params)
        obj = CalculationLog.objects.filter(pk=pk).first()
        # The same rule as the log tree (LEX_SEC_INTERNAL): a log of a
        # calculation the caller may not see does not exist for it.
        from lex.core.security_flags import superuser_only

        if superuser_only(request):
            from lex.api.views.model_entries.CalculationLogTreeView import calculation_visible

            if obj is None or not calculation_visible(request, obj.calculationId):
                raise Http404()

        if obj is not None:
            self.attach_audit(obj)

        filename = f"document_{pk}{_FILENAME_SUFFIX[scope]}.pdf"
        if obj is not None and scope == SCOPE_SUBTREE:
            ordered, truncated = collect_subtree(obj)
            md_text = build_subtree_markdown(ordered, truncated)
            self.record_audit(section_count=len(ordered), truncated=truncated)
        elif obj is not None and scope == SCOPE_CALCULATION:
            ordered, truncated = collect_calculation(obj)
            root_count = sum(1 for _, depth in ordered if depth == 0)
            md_text = build_subtree_markdown(
                ordered, truncated, title_roots=root_count > 1
            )
            self.record_audit(section_count=len(ordered), truncated=truncated)
        else:
            md_text = (obj.calculation_log if obj else "") or ""

        full_html = build_document_html(md_text)
        try:
            pdf_bytes = render_pdf_bytes(full_html)
        except Exception:
            logger.exception("Calculation-log PDF rendering failed for pk=%s", pk)
            return HttpResponse("Error generating PDF", status=500)

        self.record_audit(filename=filename, size_bytes=len(pdf_bytes))

        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp
