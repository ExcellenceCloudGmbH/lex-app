

### 1et. lex.ui: every record through self.api.all, and a model table filtered by a range of days ✅

**What it tests:** `self.api.all` reads every row once across pages of 1,000 ordered by the asked order then the key, refuses past the ceiling after one page, and feeds a rows table with export and totals; a model table's refusal points to it; a model table's date filter sends the API's `__date__gte`/`__lte` and clears.

**Why a regression matters:** Rows read twice or missed, an unbounded read into memory, or a date filter the API never receives.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1028 | self.api.all reads every record in a stable order up to a ceiling | 2,345 rows once and in order; pages 1-3 of 1,000 with ordering amount,id; count; ALL_LIMIT refusal after the first page with the count; a rows table over it builds with export and totals; a model table's totals refusal names self.api.all |
| 1.1029 | A model table filters a date field by a range of days | date_lookups event_at -> event_at__date; the picker in the toolbar; event_at__date__gte/lte sent; a field that is no date filter ignored; a malformed day dropped; clear_filters removes the range |

**Scenario range:** 1.1028 – 1.1029. **Test file:** `lex/test_project/tests/init/test_1et_ui_api_all_and_model_dates.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1et](batches.md).
