## Cluster 3 — Validation Hooks (existing 3a–3d)

### Batch 3e — Pre-validation snapshot lifecycle (v1→v2 calculate-all memory fix) ✅

| Property | Value |
| --- | --- |
| Scenario range | 3.9 – 3.10 |
| Type | E (E2E save through the public `save()` entry point) |
| Files covered | `lex/core/models/LexModel.py` (`post_validation_hook` — releases `_pre_validation_snapshot` on the successful path) |
| Test file | `lex/test_project/tests/validation_hooks/test_3e_snapshot_lifecycle.py` |
| Test classes | `TestCluster03e_SnapshotLifecycle` (3.9 snapshot released after a successful create *and* update, 3.10 release-on-success does not weaken rollback — a later rejected update still restores the pre-save value) |
| Fixtures | existing `PostValidatedItem` (reused, no new model) |
| Tests landed | 2 pass / 0 fail (full cluster 3: 11 pass / 0 fail) |
| Coverage gain | successful-path snapshot release in `post_validation_hook` |
| Status | ✅ Complete (Session 82 — June 22) |
| Note | The `_pre_validation_snapshot` is an in-flight rollback buffer (a second full-field copy per row); it was never freed after a successful save, pinning ~1800 B/inst (~34% of the v2 per-instance footprint) for the instance's lifetime — the measured driver of the non-atomic `calculate_all` v1→v2 RAM regression (3.28× → ~2.17× per saved row). |

---

### Batch 3f — Default-on lean `_initial_state` (last full-field snapshot removed; hooks preserved) ✅

| Property | Value |
| --- | --- |
| Scenario range | 3.11 – 3.32 |
| Type | E (E2E through `save()` / `refresh_from_db`; on-commit re-baseline needs `TransactionTestCase`) |
| Files covered | `lex/core/models/LexModel.py` (`lex_lean_initial_state` flag, `lex_initial_state_extra_fields`, `_expand_field_ref`, `_field_names_from_condition`, `_fields_from_hook_config`, `_lean_tracked_field_names`, `_build_lean_initial_state`, `_reset_initial_state` override, `refresh_from_db` override, `__init__` hook) |
| Test file | `lex/test_project/tests/validation_hooks/test_3f_lean_initial_state.py` |
| Test classes | `TestCluster03f_LeanInitialState` — 3.11 default-on + explicit opt-out keeps full snapshot; 3.12–3.13 lean snapshot shape (tracked-only); 3.14–3.15 `edited_at` auto-stamp + explicit override; 3.16–3.23 every conditional form (legacy `when=`/`when_any=`, `WhenFieldHasChanged`/`WhenFieldValueWas`/`WhenFieldValueChangesTo`, chained); 3.24 lean-vs-full parity; 3.25–3.27 `has_changed`/`initial_value`; 3.28 escape hatch; 3.29 post-save re-baseline; 3.30 `refresh_from_db` re-baseline; 3.31 create-path stamping; 3.32 snapshot strictly smaller |
| Fixtures | `_ConditionalHooksBase` (abstract) → `LeanConditionalItem` (lean, matches the new default) / `FullConditionalItem` (explicit `lex_lean_initial_state=False` opt-out, control) + `LeanExtraFieldItem` (escape hatch) — added to `validation_hooks/models.py` |
| Tests landed | 22 pass / 0 fail (full cluster 3: 33 pass / 0 fail) |
| Coverage gain | the new lean-snapshot machinery on `LexModel` |
| Status | ✅ Complete (Session 83 — June 23) |
| Note | django-lifecycle's `_initial_state` is a second full-field copy per instance (set in `__init__`, re-captured after each save) — the ~2.17× per-row floor left after 3e. The framework's only dependency is `has_changed('edited_at')`; all other consumers are statically-discoverable hook clauses. The opt-in narrows the retained snapshot to `edited_at` + hook-clause fields + declared extras, built by filtering the full snapshot so tracked values stay byte-for-byte identical. Default **on** framework-wide — the narrowing is transparent because every consumer is either `has_changed('edited_at')` or a statically-discoverable hook clause; a model that queries `has_changed`/`initial_value` on an undeclared field lists it in `lex_initial_state_extra_fields` or sets `lex_lean_initial_state = False`. |

---

### Batch 3g — DateTimeField aware-on-assignment invariant (`AwareDateTimeDescriptor`) ✅

| Property | Value |
| --- | --- |
| Scenario range | 3.33 – 3.38 |
| Type | I |
| Files covered | `lex/core/models/LexModel.py` (`AwareDateTimeDescriptor`, `_install_aware_datetime_descriptors`, `class_prepared` wiring) |
| Test file | `lex/test_project/tests/validation_hooks/test_3g_aware_datetime_assignment.py` |
| Test classes | `TestCluster03g_AwareDatetimeAssignment` (3.33 naive ctor kwarg → aware in default tz; 3.34 DST-correct offsets on attribute set — summer +02:00 / winter +01:00; 3.35 aware values pass through untouched (no re-zoning); 3.36 non-datetime values pass through — None / date / string; 3.37 save→refetch preserves the instant; 3.38 deferred `.only()` field still lazy-loads aware — the data-descriptor regression guard) |
| Fixtures | `StampedItem` (hook-free `DateTimeField` carrier) — added to `validation_hooks/models.py` |
| Tests landed | **6 pass / 0 fail** (full cluster 3: 39 pass / 0 fail) |
| Coverage gain | the aware-on-assignment normalization on `LexModel` DateTimeFields |
| Status | ✅ Complete — Under USE_TZ=True Django only normalizes datetimes at the DB boundary: fetched values are aware, in-memory assignments (fixture load, Excel parse, `datetime.now()`) stay naive until the next round trip. Mixing the two crashes downstream comparisons (`TypeError: Cannot compare tz-naive and tz-aware timestamps` in pandas sorts / xirr). The descriptor closes the gap at assignment using the exact save-time interpretation (default tz), so the stored instant never changes — the in-memory object simply agrees with its own future save. Ships with the `USE_TZ=True` cutover. |

---
