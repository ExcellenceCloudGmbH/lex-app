

### Batch 1es — lex.ui: a foreign key shows the related record's name, in a model table, a run's history and its facts ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1026 – 1.1027 |
| Type | E |
| Files covered | `lex/lex_app/reflex/data.py` (`shown_field`, `lex_columns`, `lex_grid_columns`, `LexTableState` ordering), `lex/lex_app/reflex/ui/pages/tables.py`, `lex/lex_app/reflex/ui/pages/calculation.py` (`_value`, `_inputs_shown`), `lex/lex_app/reflex/ui/context.py` (`Api.list(fields=)`) |
| Test file | `lex/test_project/tests/init/test_1es_ui_foreign_key_names.py` |
| Test classes | `TestCluster01es_ForeignKeyNames` (1.1026–1.1027) |
| Fixtures | `E2ETestCase` with `UI_FK_MODELS` (`UiCustomer`, `UiCustomerReport`, new in `tests/init/models.py`); `ViewsClient` from `_ui_calc_aids` |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

Section 5.15 of `docs/superpowers/specs/2026-11-23-reflex-ui-tables-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1026 | A model table's foreign key shows the related record's name | column field customer__short_description headed Customer; rows carry names and ids through the real list view; desc sort sends -customer and returns beta first; lex_columns/lex_grid_columns; Api.list(fields=[customer]) keeps the name |
| 1.1027 | A calculation's history and run show the customer's name | the past runs' column is customer__short_description; the run's input fact and result fact read run[customer__short_description] |
