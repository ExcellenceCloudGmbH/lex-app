

### Batch 1ex — lex.ui: a handler brings a section or a table into view -- ui.scroll_to ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1034 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/results.py` (`scroll_to`, `ScrollTo`, `split`, `to_events`), `lex/lex_app/reflex/ui/pages/kinds.py` (`section_anchor`, `table_anchor`, a titled section's id), `lex/lex_app/reflex/ui/pages/tables.py` (a rows table's id), `lex/ui.py` |
| Test file | `lex/test_project/tests/init/test_1ex_ui_scroll_to.py` |
| Test classes | `TestCluster01ex_ScrollTo` (1.1034) |
| Fixtures | none |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

Section 6.22 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1034 | A handler brings a section or a table into view | errors-found on a form's section and a record page's; the table's box id; run() results Toast then ScrollTo; to_events are scroll events with those ids; empty string, a number, an action refused |
