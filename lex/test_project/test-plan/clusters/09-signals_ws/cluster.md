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

---
