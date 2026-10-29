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

### Batch 6s — Export & download audit mechanism ✅

| Property | Value |
| --- | --- |
| Scenario range | 6.121 – 6.124 |
| Type | I |
| Files covered | `lex/audit_logging/models/AuditLog.py` (`ACTION_CHOICES`), `lex/audit_logging/utils/file_operation_audit.py`, `lex/audit_logging/mixins/FileOperationAuditMixin.py` |
| Test file | `lex/test_project/tests/audit_logging/test_6s_file_operation_audit.py` |
| Test classes | `TestCluster06s_FileOperationAudit` (6.121 `export` / `download` are recordable actions and round-trip through the table and `to_dict`; 6.122 an API-key download is authored by `Technical User`, not `AnonymousUser`; 6.123 the entry exists and reads `pending` from inside the view, before the file is served, and is `success` afterwards; 6.124 a failure the API layer has no answer for still leaves a `failure` entry carrying its traceback) |
| Fixtures | `AuditFileItem` in `tests/audit_logging/models.py` — the cluster's own `FileField` model; `AuditLog` + `AuditLogStatus` in `e2e_framework_models`; `authenticate_as_api_key` for 6.122 |
| Tests landed | **4 pass / 0 fail** |
| Coverage gain | the shared opener/finaliser behind every export and download entry — the per-endpoint surfaces are 10p, 13i and 15l |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-22-export-download-audit.md) |

---

### Batch 6t — Who reads export & download entries ✅

| Property | Value |
| --- | --- |
| Scenario range | 6.160 – 6.167 |
| Type | E |
| Files covered | `lex/api/utils/helpers.py` (`_can_read_file_operation`), `lex/api/views/model_entries/filter_backends.py` (`_handle_auditlog`, `_handle_auditlogstatus`), `lex/api/serializers/base_serializers.py` (payload pruning), `lex/api/security.py` (`is_readable`), `lex/audit_logging/models/AuditLogStatus.py`, `lex/audit_logging/serializers/AuditLogSerializer.py` (`get_error_traceback`) |
| Test file | `lex/test_project/tests/audit_logging/test_6t_file_operation_audit_visibility.py` |
| Test classes | `TestCluster06t_FileOperationAuditVisibility` (6.160 download entry follows the record; 6.161 export entry follows `permission_export`; 6.162 `resource&object_id&action` finds a record's download; 6.163 payload returned whole; 6.164 `auditlogstatus/list` follows its entry; 6.165 traceback superuser-only on both lists; 6.166 `LEX_SEC_READ_SURFACES=false` restores the payload check; 6.167 detail endpoint applies the list's rule) |
| Fixtures | `AuditGuardedFile` in `tests/audit_logging/models.py` — readable by its `owner` only, exportable by `exporter` only; users `owner`, `stranger`, `exporter` and a superuser |
| Tests landed | **8 pass / 0 fail** |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-22-audit-log-read-side.md). 4.53 (dispatch) and 6.145 (traceback read as superuser) updated |


### Batch 6u — Fleet-metrics audit contract — the backfill's columns ✅

| Property | Value |
| --- | --- |
| Scenario range | 6.168 |
| Type | U |
| Files covered | `lex/audit_logging/models/AuditLog.py`, `lex/audit_logging/models/AuditLogStatus.py` |
| Test file | `lex/test_project/tests/audit_logging/test_6u_fleet_metrics_audit_contract.py` |
| Test classes | `TestCluster06u_FleetMetricsAuditContract` — pins `AuditLog.id/date/author/resource/payload/calculation_id` and `AuditLogStatus.audit_log_id/status/created_at/updated_at` (field type, DB column name, `db_table`, and the `auto_now_add`/`auto_now` flags `emit_calculation_finished`'s `duration_seconds` depends on) |
| Fixtures | none — `Model._meta` introspection only, no DB access |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete. The out-of-repo backfill job on the Instance Controller reads these two tables directly by column name and type; this batch is the local tripwire for a framework refactor that would otherwise silently break it elsewhere, much later. |

---
