## Cluster 15 — Calculation Logging

(batch history lived under cluster 7 before the 2026-07-07 promotion — see ../07-calculations/batches.md for pre-promotion rows)

---

### Batch 15g — Object-less heading frames (TOC nodes) in the log tree ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.22 – 15.31 |
| Type | I |
| Files covered | `audit_logging/utils/ModelContext.py` (`LogHeading`, string acceptance, `get_root_model`/`get_current_model`), `audit_logging/utils/ContextResolver.py` (frames snapshot, routing skips headings), `audit_logging/models/CalculationLog.py` (`heading` field, lazy heading rows, `__str__`), `audit_logging/utils/DataModels.py` (`ContextInfo.frames`) |
| Test file | `lex/test_project/tests/calculation_logging/test_15g_heading_context.py` |
| Test classes | `TestCluster15g_HeadingFrames`, `TestCluster15g_HeadingPersistence` |
| Fixtures | reuse cluster-15 `LogRootCalc` + `_seed_operation_context_and_audit_log` (via `_CalcLogTestCase`) |
| Est. tests | 10 |
| Coverage gain | measured with batch 6q: heading persistence paths in ModelContext/ContextResolver/CalculationLog |
| Prereqs | none |
| Status | ✅ Complete — 10 pass / 0 fail |
| Note | Feature batch for `feat/calclog-heading-context`: `model_logging_context("Section title")` pushes a `LogHeading` frame producing a table-of-contents style node — a `CalculationLog` row with `heading` set and `content_type`/`object_id` NULL, created lazily on the first LexLogger flush inside the block. Design: `docs/superpowers/specs/2026-07-07-calclog-heading-context-design.md`. Companion batch 6q covers root-detection transparency. |

---

### Batch 15h — PDF export renders like the log view ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.32 – 15.34 |
| Type | E |
| Files covered | `api/views/calculations/DownloadMarkdownPdf.py` |
| Test file | `lex/test_project/tests/calculation_logging/test_15h_pdf_export.py` |
| Test classes | `TestCluster15h_PdfExport` |
| Fixtures | plain `CalculationLog` rows (all fields defaulted) |
| Est. tests | 3 |
| Coverage gain | calc-log PDF export contract (render parity with the log view) |
| Prereqs | none (pypdf available in the test venv for text extraction) |
| Status | ✅ Complete — 3 pass / 0 fail |
| Note | Customer report 2026-07-14: the exported PDF "doesn't look like the log view — markdown is not being rendered correctly". Root causes: (1) xhtml2pdf's minimal CSS support + a bare-bones stylesheet, (2) the `code-friendly` markdown2 extra silently DISABLED `__bold__` (which the log view's markdown-it renders), and `~~strike~~` was never enabled. Fix: render via **WeasyPrint** (near-browser CSS fidelity; already in requirements.txt) with a GitHub-style stylesheet mirroring the frontend log view (bordered headings, shaded code blocks with pre-wrap, bordered tables with tinted header, blockquote rail, link styling, image support); markdown extras now `tables, fenced-code-blocks, strike`. xhtml2pdf kept as a runtime fallback (WeasyPrint needs pango/cairo system libs — the endpoint degrades in styling rather than 500ing). 15.32 real PDF (magic bytes + disposition); 15.33 raw markdown syntax (`###`, `|---`, `**`, `~~`, ```` ``` ````, `](https`) never leaks into the extracted text; 15.34 every log-view feature's content survives (headings, both bold forms, strike body, all table rows, quote, fenced + inline code, link text). Frontend twin: the Download button (`CalculationLogFieldView.downloadCalculationLogPdf`) needs no change — it downloads what this endpoint produces. |
