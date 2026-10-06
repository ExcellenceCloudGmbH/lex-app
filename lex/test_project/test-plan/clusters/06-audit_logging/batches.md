## Cluster 6 — Audit Logging (existing 6a–6f)

This is the biggest single chunk — 18 files. Split into **three** batches so reviews stay tractable.

### Batch 6g — Models & enums

| Property | Value |
| --- | --- |
| Scenario range | 6.30 – 6.42 |
| Type | U + I |
| Files covered | `models/AuditLog.py`, `models/AuditLogStatus.py`, `models/CalculationLog.py` |
| Test file | `lex/test_project/tests/audit_logging/test_6g_audit_models.py` |
| Test classes | `TestAuditLogModelFields`, `TestAuditLogStatusTransitions`, `TestCalculationLogParentLinking` |
| Fixtures | `AuditedItem`, `CalcWithLogging` |
| Est. tests | ~14 |
| Coverage gain | +0.8 % |
| Prereqs | none |

### Batch 6h — Mixins & utils

| Property | Value |
| --- | --- |
| Scenario range | 6.43 – 6.62 |
| Type | I |
| Files covered | `mixins/AuditLogMixin.py`, `mixins/BulkAuditLogMixin.py`, `utils/ModelContext.py`, `utils/ContextResolver.py`, `utils/DataModels.py`, `utils/calculation_audit.py`, `utils/InitialDataAuditLogger.py`, `utils/config.py`, `utils/content_types.py` |
| Test file | `lex/test_project/tests/audit_logging/test_6h_audit_mixins_and_utils.py` |
| Test classes | one per file (9 classes) — keeps the failure point unambiguous in CI |
| Fixtures | reuse 6g + a `BulkOpItem` |
| Est. tests | ~30 |
| Coverage gain | +1.5 % |
| Prereqs | 6g |

### Batch 6i — Serialisers & handlers (incl. bug-§1 LexLogger surface)

| Property | Value |
| --- | --- |
| Scenario range | 6.63 – 6.78 |
| Type | I |
| Files covered | `serializers/AuditLogSerializer.py`, `serializers/AuditLogMixinSerializer.py`, `serializers/CalculationLogSerializer.py`, `handlers/LexLogger.py`, `handlers/WebSocketHandler.py` |
| Test file | `lex/test_project/tests/audit_logging/test_6i_audit_serializers_and_handlers.py` |
| Test classes | `TestAuditLogSerializerShape`, `TestAuditLogMixinSerializerExtras`, `TestCalculationLogSerializerTree`, `TestLexLoggerBuilderAndPersist`, `TestWebSocketHandlerEmits` (mock channel layer) |
| Fixtures | reuse 6g/6h |
| Est. tests | ~16 |
| Coverage gain | +0.8 % |
| Prereqs | 6g, 6h |
| Note | This is the right time to add the regression test for [`NOTES_TODO.md` §1](../../../NOTES_TODO.md) — the duplicated-children bug. Place it as `TestCalculationLogTreeBugRegression` here, mark `expectedFailure` until the framework fix lands. |

---

### Batch 6p — Calculation-log cache backfill buffer cap ✅

| Property | Value |
| --- | --- |
| Scenario range | 6.109 – 6.113 |
| Type | U |
| Files covered | `audit_logging/utils/CacheManager.py` (`store_message` buffer cap + TTL) |
| Test file | `lex/test_project/tests/audit_logging/test_6p_cache_buffer_cap.py` |
| Test classes | `TestCluster06p_CacheBufferCap` |
| Fixtures | none (LocMemCache via `CALC_CACHE_NAME="local"`) |
| Est. tests | 5 |
| Coverage gain | measured locally; CacheManager store path |
| Prereqs | none |
| Status | ✅ Complete — 5 pass / 0 fail |
| Note | Backend OOM fix (session 77): the live-log backfill buffer was an unbounded `get`+concat+`set` per line. Now capped to a ~256 KB tail (`MAX_CACHE_MESSAGE_CHARS`), trimmed to a clean line boundary, written with `CACHE_TIMEOUT`. Full log still persists in `CalculationLog`; only the recent-history backfill is bounded. |

---

---

### Batch 6q — Root-calculation detection ignores heading frames ✅

| Property | Value |
| --- | --- |
| Scenario range | 6.117 – 6.118 |
| Type | U |
| Files covered | `audit_logging/utils/calculation_audit.py` (`_is_root_calculation` heading-frame transparency) |
| Test file | `lex/test_project/tests/audit_logging/test_6q_root_detection_with_headings.py` |
| Test classes | `TestCluster06q_RootDetectionWithHeadings` |
| Fixtures | none (unsaved instances with explicit pks; explicit `model_context=` kwarg — no contextvar, no DB) |
| Est. tests | 2 |
| Coverage gain | measured with batch 15g |
| Prereqs | none |
| Status | ✅ Complete — 2 pass / 0 fail |
| Note | Companion to batch 15g (`feat/calclog-heading-context`): `LogHeading` frames on the model context stack are presentation-only, so root detection resolves via `get_root_model()`/`get_current_model()` — a heading wrapped around (or inside) the root calculation must not demote it, and a heading between root and child must not hide the nesting. |

### Batch 6r — A run started by a create has its own audit entry ✅

| Property | Value |
| --- | --- |
| Scenario range | 6.119 – 6.120 |
| Type | E |
| Files covered | `lex/api/views/model_entries/One.py` (`_calculate_after_create`), `lex/audit_logging/mixins/AuditLogMixin.py` (`log_calculation`, `log_change(calculation_id=)`) |
| Test file | `lex/test_project/tests/audit_logging/test_6r_create_triggered_run_audit.py` |
| Test classes | `TestCluster06r_CreateTriggeredRunAudit` — 6.119 the create's entry is 'success' and the run's is a separate 'update' entry under its own `<model>_<pk>_update_<uuid>` id, finalised to 'success'; 6.120 a failed run's entry is 'failure' with a traceback while the create's stays 'success' |
| Fixtures | `OnCreateCalc` from `calculations/models.py` (cluster 7t); unpatches `store_message`, `build_cache_key`, `ensure_terminal_calculation_audit` like 6i |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete — paired with 7t. Checked to fail when the run shares the create's entry, or is filed under the create request's id |

---
