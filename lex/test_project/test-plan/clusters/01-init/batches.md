

### Batch 1eq — lex.ui: a search through self.api matches the model's own text, never created_by ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1023 |
| Type | U |
| Files covered | `lex/lex_app/reflex/data.py` (`_default_search_field`, `_framework_fields`), `lex/lex_app/reflex/ui/context.py` (`Api.list(search=)`), `lex/lex_app/reflex/ui/pages/tables.py` (a model table's search column) |
| Test file | `lex/test_project/tests/init/test_1eq_ui_api_search_field.py` |
| Test classes | `TestCluster01eq_ApiSearchField` (1.1023) |
| Fixtures | httpx `MockTransport`; `_session`/`_in_thread` from 1cf |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

Section 5.15 of `docs/superpowers/specs/2026-11-23-reflex-ui-tables-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1023 | A search matches the model's own text, not created_by | customer/region/name icontains for a LexModel, a calculation, a LexModel and a Django model; lex_table's default; a model table showing created_by first searches customer; no own text field refused with filters= advice |
