

### Batch 1ey — lex.ui pages: a tab shown only while the state says so -- visible= a state value on a tab ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1035 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/kinds.py` (`hide_tabs`, `tab_visible`, `Record._sections`), `lex/lex_app/reflex/ui/pages/forms.py` (`Form._tabs`) |
| Test file | `lex/test_project/tests/init/test_1ey_ui_tab_shown_by_state.py` |
| Test classes | `TestCluster01ey_TabShownByState` (1.1035) |
| Fixtures | none |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

Section 6.22 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1035 | A tab is drawn only while its state value is true | form tabs: figures trigger and panel under cond on has_figures, summary not; compiles; record tabs the same; record without tabs refused pointing to tabs=True |
