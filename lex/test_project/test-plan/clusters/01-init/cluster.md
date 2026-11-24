

### 1ex. lex.ui: a handler brings a section or a table into view -- ui.scroll_to ✅

**What it tests:** the ids `ui.scroll_to(title)` and `ui.scroll_to(State.rows)` name are the ids the form's section, a record page's section and the rows table are built with; a handler's results keep their order; the events are Reflex's scroll_to of those ids; anything else is refused with what to write.

**Why a regression matters:** A scroll that goes nowhere because the result's id and the part's id differ.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1034 | A handler brings a section or a table into view | errors-found on a form's section and a record page's; the table's box id; run() results Toast then ScrollTo; to_events are scroll events with those ids; empty string, a number, an action refused |

**Scenario range:** 1.1034. **Test file:** `lex/test_project/tests/init/test_1ex_ui_scroll_to.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1ex](batches.md).
