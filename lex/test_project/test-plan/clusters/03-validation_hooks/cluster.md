## 3. Validation Hooks

**What it tests:** `pre_validation()` (cancel save before it happens) and `post_validation()` (rollback after save if validation fails). This is the first layer of data quality a customer adds.

**Why third:** Once a customer is creating records, the next question is "how do I prevent bad data?" Validation hooks are the answer.

**Models needed:**
- `PreValidatedItem` — raises exception in `pre_validation()` for specific values
- `PostValidatedItem` — raises exception in `post_validation()` for specific values
- `HookOrderItem` — records hook execution order in a class-level list
- `_ConditionalHooksBase` (abstract) → `LeanConditionalItem` / `FullConditionalItem` — identical conditional-hook declarations (legacy `when=`/`when_any=` + `condition=` objects incl. chained) differing only in `lex_lean_initial_state`, for lean-vs-full parity (3f)
- `LeanExtraFieldItem` — lean model that consults `has_changed()` imperatively and declares the field via `lex_initial_state_extra_fields` (3f escape hatch)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 3.1 | `pre_validation` passes | Record saved normally |
| 3.2 | `pre_validation` raises exception | Save cancelled, no DB change, no history row |
| 3.3 | `post_validation` passes | Record saved, history created |
| 3.4 | `post_validation` raises exception | Record rolled back to pre-save state, error raised |
| 3.5 | Hook execution order on create | BEFORE_CREATE → BEFORE_SAVE → (save) → AFTER_SAVE → AFTER_CREATE |
| 3.6 | Hook execution order on update | BEFORE_UPDATE → BEFORE_SAVE → (save) → AFTER_SAVE → AFTER_UPDATE |
| 3.7 | Validation recursion guard | `_validation_in_progress` prevents infinite recursion |
| 3.8 | Rollback restores field values | After `post_validation` failure, DB record matches pre-save snapshot |
| 3.9 | Snapshot released after success | After a successful save (create/update), the pre-validation rollback buffer is not pinned on the instance |
| 3.10 | Snapshot release keeps rollback intact | A `post_validation` failure on a later update still rolls back, even though the buffer is freed after each successful save |
| 3.11 | Lean is the default; opt-out keeps full snapshot | Class default `lex_lean_initial_state=True`; a model that explicitly sets it `False` still holds untracked fields in `_initial_state` |
| 3.12 | Lean snapshot shape | Lean snapshot keys == exactly the change-detected fields (`edited_at` + hook-clause fields) |
| 3.13 | Lean snapshot drops untracked | Fields no hook consults (`name`, `note`) are absent from the lean snapshot |
| 3.14 | Lean `edited_at` auto-stamp | Lean update still auto-stamps `edited_at` (framework `has_changed('edited_at')` dependency intact) |
| 3.15 | Lean explicit `edited_at` respected | An explicitly-set `edited_at` is not overwritten on a lean update |
| 3.16 | Legacy `when=` fires (lean) | `when='status'` hook fires on a status change |
| 3.17 | Legacy `when=` silent (lean) | `when='status'` hook does not fire when status is unchanged |
| 3.18 | Legacy `when_any=` fires (lean) | `when_any=['a','b']` fires when `b` changes |
| 3.19 | `WhenFieldHasChanged` fires (lean) | `condition=WhenFieldHasChanged('amount')` fires on amount change |
| 3.20 | `WhenFieldValueWas` fires (lean) | `condition=WhenFieldValueWas('status','draft')` fires when prior status was draft |
| 3.21 | `WhenFieldValueChangesTo` fires (lean) | `condition=WhenFieldValueChangesTo('status','paid')` fires on draft→paid |
| 3.22 | Chained condition fires (lean) | `WhenFieldHasChanged('amount') & WhenFieldValueIs('status','paid')` fires when both limbs hold |
| 3.23 | Chained condition silent (lean) | Chained condition does not fire when only one limb holds |
| 3.24 | Lean-vs-full hook parity | Identical mutation fires an identical set of hooks on lean and full models |
| 3.25 | `has_changed` True tracked (lean) | `has_changed` is True for a changed tracked field |
| 3.26 | `has_changed` False untracked (lean) | `has_changed` reports False for a changed untracked field (documented trade-off) |
| 3.27 | `initial_value` tracked (lean) | `initial_value` returns the pre-change value for a tracked field |
| 3.28 | Extra-fields escape hatch | `lex_initial_state_extra_fields` keeps an imperatively-queried field tracked |
| 3.29 | Post-save re-baseline is lean | The on-commit re-baseline re-narrows the snapshot; a stale change is not re-reported |
| 3.30 | `refresh_from_db` re-baseline is lean | `refresh_from_db` rebuilds a lean snapshot and resets `has_changed` |
| 3.31 | Create-path hooks unaffected | Lean create still stamps `created_at` / `created_by` (no change-detection involved) |
| 3.32 | Lean snapshot is smaller | Lean `_initial_state` holds strictly fewer keys than the full snapshot |

**Sub-clusters:** 3a `pre_validation` (3.1–3.2) · 3b `post_validation` (3.3–3.4) · 3c hook ordering (3.5–3.6) · 3d recursion guard (3.7) · 3e snapshot lifecycle (3.8–3.10) · **3f lean `_initial_state` default-on / opt-out (3.11–3.32)** — covers `lex/core/models/LexModel.py`, Type E, ✅ Complete (22 pass / 0 fail).

---
