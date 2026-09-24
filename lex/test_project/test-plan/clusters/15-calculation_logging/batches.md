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

## Batch 15i — a calculation row learns its latest run (15.39-15.46)

| | |
| --- | --- |
| Scenario range | 15.39 – 15.46 |
| Type | U + API |
| Files covered | `lex/audit_logging/utils/latest_calculation.py` (new), `lex/api/serializers/base_serializers.py` (`_calculation_run_fields`, `_SYSTEM_FIELDS`, both factories), `lex/api/views/model_entries/List.py` (`_execute_leaf_level`) |
| Test file | `lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py` |
| Tests landed | **10 pass / 0 fail** |
| Status | ✅ Complete |
| Paired with | PAC batches `7f` and `12m` — the status cell's log button and the drawer that read these two fields |

**The rule is stated twice, deliberately.** `useResolvedCalculationId` resolves a record's run in the
browser; this resolves a whole page on the server. Both use the newest `calculationId` starting
`<model>_<pk>_`, by `id`. A second rule — the spec's first draft proposed the generic foreign key —
would let one row open different runs in the table and in a widget.

**15.41 exists because the obvious implementation is wrong for string keys.** A run id can start
with two of a page's prefixes; only the longest is the record that started it. Verified as a guard:
matching shortest-first fails 15.41 and nothing else.

15.43's second half exists because the first implementation shadowed the audit log's own getter: _wrap_custom_serializer builds (LexSerializer, custom_cls), so a same-named method on the shared base came first in the MRO and unannotated audit rows answered False. The fields now name their getters explicitly.

## Batch 15j — a failed run keeps what it logged (15.47-15.50)

| | |
| --- | --- |
| Scenario range | 15.47 – 15.50 |
| Files covered | `lex/audit_logging/models/CalculationLog.py` (`keep_rolled_back_log`), `lex/core/models/CalculationModel.py` (both failure paths) |
| Test file | `lex/test_project/tests/calculation_logging/test_15j_failed_run_keeps_its_log.py` (4) |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |
| Paired with | process-admin-general-client F12 `12r` |

**The steps that succeeded vanished with the one that failed.** `CalculationLog` persists on commit, so a
calculation that fails inside its transaction — every `is_atomic` model — was rolled back with every row
it wrote. The log popup could show the failure's traceback but nothing that led up to it.

**The live cache still had it.** The cache is not transactional: it holds the run's whole log, in
order, under the root's key until the run ends. `CalculationLog.keep_rolled_back_log` writes that text
back as the run's log, one row on the root record, in both failure paths, just before the cache is
purged. It does nothing when the run's rows survived, and it never raises. 15.47 is the regression.
15.48 checks what the popup reads: the audit row's `lex_reserved_has_calculation_log` turns true.

