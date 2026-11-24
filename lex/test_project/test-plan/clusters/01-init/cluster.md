

### 1ew. lex.ui pages: a closed section mounts what it holds when opened; a title from the state; actions ✅

**What it tests:** a closed section's chart sits under a condition on its client-side opened state (set by a click), its table does not; an open section mounts at once; a state title and actions in a form's result compile into RFDS's section; a record page, a tab and the navigation refuse them with what to write.

**Why a regression matters:** A chart drawn at zero size, a closed section's count lost, or a title or an action silently dropped.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1032 | A closed section mounts its content when opened, its table at once | the chart under a cond on rfdsLexopened; the data table not; onClick sets it; an open section's chart not under the cond |
| 1.1033 | A section's title may be a state value, and it takes actions | form result: lines_title and rfds-section-actions with the button; record title, record actions, tab actions, a text in actions refused; nav section state title refused; a number title refused |

**Scenario range:** 1.1032 – 1.1033. **Test file:** `lex/test_project/tests/init/test_1ew_ui_section_opened_title_actions.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1ew](batches.md).
