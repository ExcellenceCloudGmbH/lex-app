

### Batch 1fa — lex.ui pages: more than four tables on a record page refused in lex.ui's words, with the way out ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1037 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/kinds.py` (`Record._at_most_tables`) |
| Test file | `lex/test_project/tests/init/test_1fa_ui_record_table_limit.py` |
| Test classes | `TestCluster01fa_RecordTableLimit` (1.1037) |
| Fixtures | none |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

Section 6.22 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1037 | More than four tables is refused with the way out | four compile; five refused with and without tabs: slot sections, 5 tables in Lines (2), Invoices, Payments, Shipments, at most 4, with tabs=True too, a page of their own, ui.link to order_invoices; no 'use tab navigation' |
