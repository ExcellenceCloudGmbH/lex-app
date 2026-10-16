## 9. Signals & WebSocket

**What it tests:** `ActiveCalculationStateStore` tracking, `WebSocketNotifier` broadcasts, `CacheManager` cleanup, and `update_calculation_status` signal.

**Why ninth:** Real-time UI updates are the last user-facing layer. If signals are broken, the UI shows stale data but the backend still works.

**Models needed:**
- `AtomicCalc` (reused)
- `ParentCalc`, `ChildCalc` (reused)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.1 | `mark_in_progress` registers record in state store | Record retrievable by `get_calculation_id` |
| 9.2 | Calculation completion cleans up state store | Record removed after SUCCESS/ERROR |
| 9.3 | WebSocket notification sent on state change | `send_calculation_update` called with correct model/state |
| 9.4 | Root process cleans up cache | `CacheManager.cleanup_calculation` called for root |
| 9.5 | Child process skips cache cleanup | Cleanup NOT called for child process |
| 9.6 | `update_calculation_status` called with error details on failure | Exception details and stack trace included |
| 9.43 | `from lex.core.signals import *` succeeds | Binds `update_calculation_status`, `do_post_save` and `custom_post_save` — `CalculationSignals`' own functions |

---

### 9h. Scoped realtime delivery ✅

**What it tests:** Without hooks events go to the shared group with unchanged payloads; broadcast_mutations=False silences a model; with MUTATION_GROUPS/USER_GROUPS events reach only matching groups and anonymous sockets join none; group names are sanitised; calculation-status sockets join their user's groups.

**Why a regression matters:** Every signed-in socket used to receive every tenant's activity, including calculation error tracebacks.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 9.44 | default shared group | event to model_data_update; socket joins it |
| 9.45 | opt-out | nothing sent |
| 9.46 | scoped delivery | event to .a only; tenant-b socket joins .b; anonymous none |
| 9.47 | sanitised names | unsafe characters replaced; under 100 characters |
| 9.48 | calculation sockets | join update_calculation_status.b; reconciliation sent |

**Scenario range:** 9.44-9.48. **Test file:** `lex/test_project/tests/signals_ws/test_9h_realtime_scoping.py`. **Type:** U. **Status:** ✅ Complete (2026-10-16). Covers `lex/core/realtime.py`, `lex/core/signals/ModelMutationSignal.py`, `lex/core/signals/CalculationSignals.py`, `lex/api/consumers/ModelDataUpdateConsumer.py`, `lex/api/consumers/UpdateCalculationStatusConsumer.py`. Batch: [9h](batches.md).

---
