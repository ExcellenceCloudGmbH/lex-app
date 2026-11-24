

### Batch 1ew — lex.ui pages: a closed section mounts what it holds when opened; a title from the state; actions ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1032 – 1.1033 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/kinds.py` (`section(title=, actions=)`, `Section.titled`, `_content`, `fixed`, `_render`), `lex/lex_app/reflex/ui/pages/forms.py` (a tab's section), `lex/lex_app/reflex/ui/pages/frame.py` (a navigation section) |
| Test file | `lex/test_project/tests/init/test_1ew_ui_section_opened_title_actions.py` |
| Test classes | `TestCluster01ew_SectionOpenedTitleActions` (1.1032–1.1033) |
| Fixtures | none |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

Section 6.22 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1032 | A closed section mounts its content when opened, its table at once | the chart under a cond on rfdsLexopened; the data table not; onClick sets it; an open section's chart not under the cond |
| 1.1033 | A section's title may be a state value, and it takes actions | form result: lines_title and rfds-section-actions with the button; record title, record actions, tab actions, a text in actions refused; nav section state title refused; a number title refused |
