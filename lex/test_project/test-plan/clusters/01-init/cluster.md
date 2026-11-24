

### 1ey. lex.ui pages: a tab shown only while the state says so -- visible= a state value on a tab ✅

**What it tests:** a later tab with `visible=` a state value has its trigger and panel under a condition on it, on a form's result and on a record page with tabs; the first tab is never hidden; a record page without tabs still refuses, pointing to tabs=True.

**Why a regression matters:** A tab shown with nothing to say, or a hidden tab the user can still open.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1035 | A tab is drawn only while its state value is true | form tabs: figures trigger and panel under cond on has_figures, summary not; compiles; record tabs the same; record without tabs refused pointing to tabs=True |

**Scenario range:** 1.1035. **Test file:** `lex/test_project/tests/init/test_1ey_ui_tab_shown_by_state.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1ey](batches.md).
