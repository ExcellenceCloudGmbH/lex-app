

### Batch 1et — lex.ui: every record through self.api.all, and a model table filtered by a range of days ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1028 – 1.1029 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/context.py` (`Api.all`, `ALL_LIMIT`, `ALL_PAGE`), `lex/lex_app/reflex/data.py` (`LexTableState.set_dates`, `date_lookups`, `table_state(date_lookups=)`), `lex/lex_app/reflex/ui/pages/tables.py` (model date filters; the export/totals refusal) |
| Test file | `lex/test_project/tests/init/test_1et_ui_api_all_and_model_dates.py` |
| Test classes | `TestCluster01et_ApiAllAndModelDates` (1.1028–1.1029) |
| Fixtures | httpx `MockTransport`; `_session`/`_in_thread` from 1cf; `ui.testing.Harness`; a recording API stand-in patched over `data.lex_api` |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

Section 5.15 of `docs/superpowers/specs/2026-11-23-reflex-ui-tables-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1028 | self.api.all reads every record in a stable order up to a ceiling | 2,345 rows once and in order; pages 1-3 of 1,000 with ordering amount,id; count; ALL_LIMIT refusal after the first page with the count; a rows table over it builds with export and totals; a model table's totals refusal names self.api.all |
| 1.1029 | A model table filters a date field by a range of days | date_lookups event_at -> event_at__date; the picker in the toolbar; event_at__date__gte/lte sent; a field that is no date filter ignored; a malformed day dropped; clear_filters removes the range |
