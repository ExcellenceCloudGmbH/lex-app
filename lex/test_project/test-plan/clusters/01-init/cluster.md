

### 1eq. lex.ui: a search through self.api matches the model's own text, never created_by ✅

**What it tests:** `self.api.list(Model, search=)` searches the first plain text field the model itself declares, never the `created_by` audit field every LexModel inherits; lex_table's default and a model table's search column follow; a model without its own text field is refused with what to write.

**Why a regression matters:** Every search on a LexModel silently found nothing.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1023 | A search matches the model's own text, not created_by | customer/region/name icontains for a LexModel, a calculation, a LexModel and a Django model; lex_table's default; a model table showing created_by first searches customer; no own text field refused with filters= advice |

**Scenario range:** 1.1023. **Test file:** `lex/test_project/tests/init/test_1eq_ui_api_search_field.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1eq](batches.md).
