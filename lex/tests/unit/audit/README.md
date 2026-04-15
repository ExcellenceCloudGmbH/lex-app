# Audit & Logging Tests — `lex.tests.unit.audit`

> **Story:** *"Every data mutation must leave an auditable trail — who changed
> what, when, why — and downstream systems (WebSocket, cache, context resolver)
> must stay in sync without duplicating logic."*

## What Lives Here (13 files, 246 tests)

| File | Tests | Covers |
|------|------:|--------|
| `test_audit_log_mixin.py` | 49 | `AuditLogMixin` create path — timestamps, actor resolution, history + meta-history, status tracking |
| `test_audit_log_mixin_update.py` | 42 | `AuditLogMixin` update path — dirty-field detection, delta recording, valid_from/sys_from handling |
| `test_bulk_audit_log_mixin.py` | 28 | Bulk create/update — batch history insertion, per-row actor propagation |
| `test_calculation_audit.py` | 22 | `ensure_terminal_calculation_audit` — success/error/abort terminal log, idempotent re-runs |
| `test_model_logging_context.py` | 12 | `ModelLoggingContext` — thread-local actor stack, nested context push/pop |
| `test_cache_manager.py` | 14 | `CacheManager` — get/set/invalidate, TTL expiry, bulk invalidation |
| `test_content_types.py` | 8 | `ContentType` resolution helpers — model→CT, CT→model, caching |
| `test_context_resolution.py` | 18 | `ContextResolver` — model context assembly from request, user, and model metadata |
| `test_context_resolver_errors.py` | 12 | `ContextResolver` error paths — missing model, missing user, malformed input |
| `test_initial_data_audit_logger.py` | 10 | `InitialDataAuditLogger` — fixture-load audit trail, idempotent re-import |
| `test_websocket_notifier.py` | 8 | `WebSocketNotifier` — channel routing, payload construction, silent failure on disconnect |
| `test_lex_logger.py` | 8 | `LexLogger` — structured log output, context enrichment, level filtering |
| `test_audit_actor_tracking.py` | 15 | End-to-end actor propagation across create → calculate → update cycles |

## Key Concepts Tested

- **Bitemporal audit** — every mutation writes `History` (valid_from / valid_to) + `MetaHistory` (sys_from / sys_to)
- **Actor tracking** — Keycloak user or system actor attached to every audit record
- **Context resolver** — assembles audit context from HTTP request, user, model, and operation type
- **Cache coherence** — `CacheManager` invalidation keeps audit queries fresh
- **WebSocket fan-out** — real-time audit notifications without polling

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
lex test lex.tests.unit.audit              # all 246 tests
lex test lex.tests.unit.audit.test_audit_log_mixin  # 49 tests
```
