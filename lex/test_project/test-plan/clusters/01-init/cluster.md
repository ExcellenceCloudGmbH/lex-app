

### 1fa. lex.ui pages: more than four tables on a record page refused in lex.ui's words, with the way out ✅

**What it tests:** four tables on a record page compile; five, with or without tabs, are refused before RFDS is asked, naming the count, the sections, that tabs do not lift it, and a page of its own led to by a link; RFDS's pointer to tab navigation is not repeated.

**Why a regression matters:** A page past RFDS's rule accepted, or a refusal that sends the author to tabs, which do not help.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1037 | More than four tables is refused with the way out | four compile; five refused with and without tabs: slot sections, 5 tables in Lines (2), Invoices, Payments, Shipments, at most 4, with tabs=True too, a page of their own, ui.link to order_invoices; no 'use tab navigation' |

**Scenario range:** 1.1037. **Test file:** `lex/test_project/tests/init/test_1fa_ui_record_table_limit.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1fa](batches.md).
