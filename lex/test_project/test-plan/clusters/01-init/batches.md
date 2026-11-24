

### Batch 1er — lex.ui tables: the rows a table shows reach the browser and are never stored with the state ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1024 – 1.1025 |
| Type | U |
| Files covered | `lex/lex_app/reflex/data.py` (`RowsNotStored`, `rows_read`, `table_state(not_stored=)`), `lex/lex_app/reflex/ui/pages/rows_table.py` (`go`, `shown_on`, `restore_events`), `lex/lex_app/reflex/ui/pages/tables.py`, `lex/lex_app/reflex/ui/state.py` (`ui_open` sends `restore`) |
| Test file | `lex/test_project/tests/init/test_1er_ui_table_rows_not_stored.py` |
| Test classes | `TestCluster01er_TableRowsNotStored` (1.1024–1.1025) |
| Fixtures | `ui.testing.Harness`; Reflex's `BaseStateToken.serialize` (what Redis and disk store) and `pickle` |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

Section 5.15 of `docs/superpowers/specs/2026-11-23-reflex-ui-tables-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1024 | A table's rows reach the browser and no stored state | the browser's updates carry name, pay, team; no BaseStateToken.serialize or pickle of any state of the tab holds them or the total; stored: no rows, count kept, _rows_dropped; restore reads nothing when not dropped, reads again when dropped |
| 1.1025 | Every ui.table is not stored; a row link survives the store | a ui.table model table is rows_not_stored and told to restore by its page; lex_table's state is not; a row's own address left out of the pickle, the click still redirects to it |
