

### 1er. lex.ui tables: the rows a table shows reach the browser and are never stored with the state ✅

**What it tests:** a table over a `ui.private` value shows its page; every state of the tab, serialised as Reflex stores it and pickled, holds no row value, total or filter choice, while the browser's updates carry them; a stored table is marked and reads its rows again on `restore` (sent by the page's opener); every `ui.table` (model tables too) is not stored, `lex_table` is; a row's own link still opens after its address was left out.

**Why a regression matters:** Persons' names and amounts in Redis and in state pickles, defeating `ui.private`; or a table that shows nothing after its state was stored.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1024 | A table's rows reach the browser and no stored state | the browser's updates carry name, pay, team; no BaseStateToken.serialize or pickle of any state of the tab holds them or the total; stored: no rows, count kept, _rows_dropped; restore reads nothing when not dropped, reads again when dropped |
| 1.1025 | Every ui.table is not stored; a row link survives the store | a ui.table model table is rows_not_stored and told to restore by its page; lex_table's state is not; a row's own address left out of the pickle, the click still redirects to it |

**Scenario range:** 1.1024 – 1.1025. **Test file:** `lex/test_project/tests/init/test_1er_ui_table_rows_not_stored.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1er](batches.md).
