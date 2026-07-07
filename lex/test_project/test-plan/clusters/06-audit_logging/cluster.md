## 6. Audit Logging

**What it tests:** The `AuditLogMixin` records every API create/update/delete with correct actor, action, payload, and status. Also tests calculation audit finalization.

**Why sixth:** Audit logs are the compliance backbone. Customers in regulated industries (finance, healthcare) need proof of every action.

**Documented contract** (from `docs/features/tracking/audit logs.md` + `docs/interface/record-detail/audit log tab.md` + `docs/features/tracking/tracking tables.md`): **Audit log entries are created exclusively through the REST API layer** (`AuditLogMixin` on DRF views) — a programmatic `obj.save()` at the ORM level does **not** produce an audit row. Only API endpoints (POST create, PATCH/PUT update, DELETE) trigger the mixin. The one exception is **calculation audit finalization**: `ensure_terminal_calculation_audit` writes a terminal audit row from the calc state machine (not the API layer) to record whether a calculation succeeded or failed.

Every API create / update / delete produces an `AuditLog` (`date`, `author`, `resource`, `action`, `payload`, `content_type` + `object_id` GenericForeignKey, optional `calculation_id`) **plus** a paired `AuditLogStatus` whose status walks `pending → success` (or `pending → failure` carrying the full error traceback). The audit row is written **before** the operation, so even operations that fail at validation / permission / DB level are recorded with full context. The `payload` starts as the submitted request body; on success it is **rewritten to the final persisted state** (so the audit row reflects what was actually saved, not what was attempted); on failure it remains the attempted payload.

When the change was triggered by a calculation, the audit entry's `calculation_id` is non-empty and links to the Calculation Log tree — that's how an operator traces from "this field was changed" to "this is the calculation that changed it". For plain user edits, `calculation_id` is empty.

> [!note] `edited_by` / `edited_at` vs audit `author`
> The tracking-tables doc (§3, note) clarifies that `edited_by` / `edited_at` on the record reflect **edits only** — calculation-driven changes do *not* update them, even though they do produce history and audit entries. To determine whether a change came from a person or a calculation, look at the audit entry's `calculation_id`.

`BulkAuditLogMixin` produces one audit row per record in a bulk op (a 100-row bulk update writes 100 audit rows). The system is resilient by design: deadlocks and serialization conflicts are auto-retried with exponential backoff, ContentType cache staleness is auto-corrected, and audit rows are effectively read-only — `permission_create` / `permission_delete` return False and `permission_edit` denies for everyone except `AdminReportsModificationRestriction`.

**Models needed:**
- `SimpleItem` (reused)
- `AtomicCalc` (from Cluster 7)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.1 | API create produces audit log | Audit log with action=create, correct actor, status=success |
| 6.2 | API update produces audit log | Audit log with action=update, payload includes changed fields |
| 6.3 | API delete produces audit log | Audit log with action=delete |
| 6.4 | Failed API operation | Audit log with status=failure |
| 6.5 | Calculation audit — success | Terminal audit log with audit_status=success |
| 6.6 | Calculation audit — failure | Terminal audit log with audit_status=failure, includes error message |
| 6.7 | Actor resolution — authenticated user | `created_by`/`edited_by` = user email or username |
| 6.8 | Actor resolution — API key | `created_by`/`edited_by` = "Technical User" |
| 6.9 | Actor resolution — no context | `created_by`/`edited_by` = "Initial Data Upload" (fallback) |
| 6.10 | Audit log survives calculation failure | `_finalize_pending_terminal_audit` runs even when `save()` atomic block rolls back |

> **Audit notes — May 5.** Walked the implementation against `docs/features/tracking/audit logs.md` + `docs/interface/record-detail/audit log tab.md`:
> * **6.1 thin.** Asserts `count==1` + `author truthy` + `status=='success'`. The docs explicitly enumerate **six** customer-visible columns the audit log row must carry (`date`, `author`, `resource`, `action`, `payload`, `content_type`+`object_id` GenericForeignKey, optional `calculation_id`) — only `author`, `resource`, `action` are pinned today. Most importantly, **`content_type` + `object_id` are never asserted**, even though the Audit Log Tab UI filters by them to show "operations that affected this specific record". See gap sub-cluster **6d** scenarios 6.41–6.43.
> * **6.2 thin.** Asserts only that `"value"` is in the payload dict. Docs require: payload carries the *full serialized data* of the operation, on update payload is *refreshed to the final state* on success (`audit_log.payload = updated_payload` line 265 of `AuditLogMixin.py`), payload includes `id` after save. None pinned. See **6d** scenarios 6.42–6.43.
> * **6.3 thin.** Asserts only `count==1` + `status=='success'`. Docs explicitly say "For deletions, the payload captures the record's state at the moment of deletion — so you can always inspect what was removed." That contract is uncovered. See **6d** scenario 6.44.
> * **6.4 skipped.** Documented as "needs middleware-level audit hook". The mixin's *exception path* (`AuditLogMixin.py` lines 234–283 / 298–311 — `_pending_failed_audit_logs` queue + `_failed_audit_logged` sentinel + atomic vs. non-atomic split) is dark. The contract is concrete and reachable today via raising `pre_validation` + a `perform_create` save: failure status row, status='failure', traceback non-empty, atomic-block path queues for replay. See **6d** scenarios 6.45–6.47 (re-scoped — no middleware needed).
> * **Pending intermediate state never observed.** The docs mermaid diagram makes " Pending →  Success /  Failure" a customer-visible lifecycle, but no test ever observes the *pending* state mid-flight. See **6d** scenario 6.48.
> * **`BulkAuditLogMixin` not tested.** Documented as "Each individual record in a bulk operation gets its own audit log entry — so a bulk update of 100 records creates 100 audit log entries." Cluster 2e's bulk DELETE scenarios (2.23/2.24/2.25) never assert the audit row count. See gap sub-cluster **6e** scenarios 6.51–6.53.
> * **Resilience contracts unpinned.** Two are explicitly called out in docs ("Resilience" section): deadlock retries (`RETRYABLE_SQLSTATE_CODES = {"40P01", "40001"}` + 3 attempts + exponential backoff) and ContentType cache healing (`safe_get_content_type` recovers when Django's cache goes stale post-migration). See **6f** scenarios 6.61–6.63.
> * **Read-only / immutable contract not gated.** Docs `[!note]`: "Audit logs are effectively read-only. Only administrators should modify or delete them." `AuditLog.permission_create` / `permission_delete` return False, `permission_edit` denies. No test pins this — a regression that flipped any of those bools to `True` would silently allow audit deletion. See **6g** scenarios 6.71–6.73.
> * **6.10 still failing.** The "audit row survives the outer atomic rollback" contract from `ensure_terminal_calculation_audit` is not yet honoured — the inner `transaction.atomic()` joins the outer block as a savepoint, so it rolls back too. Already tracked as BUG-001 family but the marker on `test_6_10` is currently *commented out* (the `@unittest.expectedFailure` line is `# @unittest.expectedFailure`), so when the test fails it's an *unexpected* failure. **Fix in this update**: re-enable the marker.
>
> **Audit notes — May 7 (tracking-tables doc cross-check).** Walked the test-plan against `docs/features/tracking/tracking tables.md`:
> * **API-only scope clarified.** The tracking-tables doc and user confirmation make explicit what was implicit: audit log entries are created **exclusively through the REST API layer** (`AuditLogMixin`), not by programmatic `obj.save()`. The one exception is `ensure_terminal_calculation_audit`, which writes a terminal audit row from the calc state machine. This distinction is now documented in the Cluster 6 contract above. History, by contrast, fires at the ORM level on every `save()` — both API and programmatic. Updated Cluster 5 contract to note this.
> * **Payload lifecycle clarified.** The doc explicitly states: "starts as submitted payload, on success rewritten to final persisted state, on failure remains the attempted payload." Already covered by 6.42/6.43, but the Cluster 6 contract paragraph now carries this language directly.
> * **`calculation_id` linkage noted.** The doc describes `calculation_id` as the bridge between "what was changed" and "why" — non-empty when triggered by a calculation, empty for plain edits. Not yet tested in isolation (no scenario pins `calculation_id` populated on a calc-driven audit entry vs empty on a user edit). Noted as future gap.
> * **`edited_by` / `edited_at` edit-only semantics noted.** The doc's note ("only edits update `edited_by`/`edited_at`; calculations do not") is now referenced in the Cluster 6 contract. Relates to BUG-007 but is a broader contract.
> * **`history_change_reason` UI limitation noted.** The doc says it's "currently only writable from code, no UI." Updated Cluster 5 contract.
> * **`history_user` definition clarified.** The doc says: "the person who edited the record, or the user who launched the calculation." Updated Cluster 5 contract.
> * **BUG-001b expanded (Session 54).** 6.10 now has 6 companion scenarios. The synthetic ones — **6.10-control** (sanity), **6.10b** (`AuditLogStatus` child also wiped), **6.10c** (3 retries → 0 rows), **6.10d** (nested savepoint shape) — call `ensure_terminal_calculation_audit()` directly inside a synthetic outer atomic and remain live regression gates (1 pass + 4 xfail). The end-to-end ones — **6.10e** (programmatic `calc.save()` inside outer atomic) and **6.10f** (API POST → PATCH → fallback) — are now `@unittest.skip` because the audit log's API-only contract poisons their diagnostic value: a programmatic `calc.save()` never seeds the API-layer `_pending_terminal_audit`, so the except-branch finalize has nothing to finalize and the 0-row outcome is ambiguous (could be "row rolled back" OR "row never written"). 6.10f's API path is the right shape but blocked by BUG-009 (PATCH of `is_calculated` is silently dropped). Both unblock once BUG-009 is fixed and the API path can drive a calc end-to-end with a real audit-mixin-seeded pending row in scope.

### 6p. Calculation-log cache backfill buffer cap ✅

**What it tests:** `CacheManager.store_message` (the server-side buffer that backfills the "recent history" shown when the calculation-log panel opens, while newer lines keep arriving over the WebSocket stream). Before this change, every log line did an unbounded `cache.get()` + string-concat + `cache.set()` — O(N²) bandwidth with multiple multi-MB copies alive at once, no size cap, and `CACHE_TIMEOUT` was defined but never passed to `set()`. On a long/heavy calculation this OOM-ed the backend pod when a user opened the log. The fix bounds the buffer to a 256 KB tail (full log always persists in `CalculationLog`) and applies the one-week TTL.

**Why a regression matters:** an unbounded backfill buffer is a direct path to a production pod restart — the exact crash this batch closes. The cap must keep only the most recent tail and start on a clean line boundary so the backfill is never a partial leading line.

**Scenario range:** 6.109 – 6.113. **Test file:** `lex/test_project/tests/audit_logging/test_6p_cache_buffer_cap.py`. **Type:** U. **Status:** ✅ Complete (Session 77 — June 8). Scenarios: 6.109 buffer never exceeds the cap; 6.110 trim starts on a clean line boundary; 6.111 newest retained / oldest dropped; 6.112 under-cap buffer left untouched; 6.113 `set()` uses the configured `CACHE_TIMEOUT`.

---
