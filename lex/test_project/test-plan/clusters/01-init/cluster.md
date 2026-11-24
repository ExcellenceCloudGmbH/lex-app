

### 1ez. lex.ui forms: a field of several choices can be emptied at once -- its Clear ✅

**What it tests:** each several-choice field (multi-select past twelve options, checkbox group below) has a Clear under a condition on something being chosen, which sets the field's browser value to the empty list; a single choice has none.

**Why a regression matters:** A long choice that can only be undone one option at a time, or a Clear that leaves values behind.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1036 | Several choices can be emptied at once | Clear for regions (multi-select) and kinds (checkbox group) under a cond, none for main; the multi-select compiled; onClick sets the client state to []; .length > 0 |

**Scenario range:** 1.1036. **Test file:** `lex/test_project/tests/init/test_1ez_ui_choices_clear.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1ez](batches.md).
