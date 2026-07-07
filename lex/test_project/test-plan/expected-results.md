# Expected Results

> **Back to:** [Test Plan Index](index.md)  
> **Audience:** Engineering leadership, QA supervisors

---

## What We Expect to Achieve

### 1. Real Bug Detection

| Metric | Before | After |
|--------|--------|-------|
| Production bugs caught by tests before release | ~0 (bugs found manually or by customers) | Every known bug class has a corresponding test |
| False-green tests (pass when feature is broken) | Estimated 30–40% of calculation/signal tests | Target: 0% |
| Time to detect a regression | Days to weeks (manual QA) | Minutes (CI pipeline) |

### 2. Release Confidence

| Metric | Before | After |
|--------|--------|-------|
| "Can we ship this?" answer based on test suite | Low confidence — tests pass but bugs slip through | High confidence — if tests pass, features work |
| Hotfixes caused by missed regressions | Frequent | Rare |
| Release-blocking test failures that are real bugs (not flaky tests) | Mixed — unclear if failure = real problem | Clear — failure = real problem or infrastructure issue |

### 3. Developer Productivity

| Metric | Before | After |
|--------|--------|-------|
| Tests broken by safe refactors (rename, restructure) | ~50% of coupled tests break | <5% break (behavior tests survive internal changes) |
| Time spent debugging false test failures | Significant | Minimal |
| Time to write a new test for a new feature | Medium (need to understand internal mocking patterns) | Low (follow the test project patterns, use real models) |

### 4. Coverage Quality

We distinguish between **line coverage** (how many lines are executed) and **behavior coverage** (how many user-visible behaviors are verified).

| Metric | Before | After |
|--------|--------|-------|
| Line coverage | 60% | 70%+ (secondary goal) |
| Behavior coverage | Unknown — not measured | Tracked per cluster (primary goal) |
| Coverage that detects real bugs | Low correlation | High correlation |

> **Key insight:** 60% line coverage with implementation-coupled tests caught fewer bugs than 40% behavior coverage with intent-driven tests. Coverage percentage alone is not a quality indicator.

---

## Success Criteria per Cluster

Each test cluster has concrete pass/fail criteria:

| Cluster | Success = Tests Prove That... |
|---------|-------------------------------|
| CRUD & Lifecycle | A record created through the ORM has correct timestamps, correct actor, and a history row |
| Calculation State Machine | Every state transition (NOT_CALCULATED → IN_PROGRESS → SUCCESS/ERROR) produces the correct final state AND correct history trail |
| History & Bitemporal | Every `.save()` creates a history row; `valid_from`/`valid_to` chain correctly; no gaps or overlaps |
| Audit Logging | Every API create/update/delete produces an audit log with correct actor, action, and payload |
| Permissions | A user without `read` scope cannot see field values; a user without `edit` scope cannot modify fields |
| Validation Hooks | `pre_validation` raising an exception cancels the save (no DB change); `post_validation` failure rolls back |
| API Layer | REST endpoints return correct status codes, correct data shapes, and respect permissions |
| Celery & Async | When Celery is unavailable, calculations fall back to sync and still produce correct results |
| Signals & WebSocket | State store tracks IN_PROGRESS records; cleanup removes them after completion |
| Initial Data | Seed data loads correctly on first run; skips if data already exists |

---

## Known Bugs That New Tests Expose

The new suite has surfaced 20+ real framework bugs, each pinned by a test marked `@unittest.expectedFailure` (or `xfail`) so the suite stays green while the bug is tracked for resolution. The live tracker — with severity, owning cluster, test, and status — is **[`known-bugs.md`](known-bugs.md)**, the single source of truth enforced by the PR-shape gate.

---

## Timeline

| Phase | Scope | Target |
|-------|-------|--------|
| Phase 1 | Clusters 1–3 (CRUD, Calculations, History) — core data integrity | First |
| Phase 2 | Clusters 4–6 (Audit, Permissions, Validation) — compliance and access control | After Phase 1 |
| Phase 3 | Clusters 7–10 (API, Celery, Signals, Initial Data) — integration and infrastructure | After Phase 2 |

Each phase delivers a working, CI-gated test suite for its clusters before moving to the next.

---

> **Back to:** [Test Plan Index](index.md) | **Next:** [Testing Philosophy](testing-philosophy.md) · [Clusters](clusters/)
