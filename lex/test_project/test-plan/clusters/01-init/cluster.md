

### 1es. lex.ui: a foreign key shows the related record's name, in a model table, a run's history and its facts ✅

**What it tests:** a model table's foreign-key column shows `<fk>__short_description` from lex-app's real list view, keeps the id in the row, sorts by the key; lex_columns and the grid's columns too; `self.api.list(fields=[fk])` keeps the name; a calculation's past runs and its run's facts read the name.

**Why a regression matters:** A bare id where a person reads a customer's name, or a sort the API refuses.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1026 | A model table's foreign key shows the related record's name | column field customer__short_description headed Customer; rows carry names and ids through the real list view; desc sort sends -customer and returns beta first; lex_columns/lex_grid_columns; Api.list(fields=[customer]) keeps the name |
| 1.1027 | A calculation's history and run show the customer's name | the past runs' column is customer__short_description; the run's input fact and result fact read run[customer__short_description] |

**Scenario range:** 1.1026 – 1.1027. **Test file:** `lex/test_project/tests/init/test_1es_ui_foreign_key_names.py`. **Type:** E. **Status:** ✅ Complete (2026-11-24). Batch: [1es](batches.md).
