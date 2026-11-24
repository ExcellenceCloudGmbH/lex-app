

### Batch 1ez — lex.ui forms: a field of several choices can be emptied at once -- its Clear ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1036 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/forms.py` (`Field` input for `kind="choices"`) |
| Test file | `lex/test_project/tests/init/test_1ez_ui_choices_clear.py` |
| Test classes | `TestCluster01ez_ChoicesClear` (1.1036) |
| Fixtures | none |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

Section 6.22 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1036 | Several choices can be emptied at once | Clear for regions (multi-select) and kinds (checkbox group) under a cond, none for main; the multi-select compiled; onClick sets the client state to []; .length > 0 |
