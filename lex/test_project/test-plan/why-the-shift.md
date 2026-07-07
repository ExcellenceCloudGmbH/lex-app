# Why the Shift Was Necessary

> **Back to:** [Test Plan Index](index.md)  
> **Audience:** Engineering leadership, QA supervisors

---

## The Problem in One Sentence

Our old tests verified **how the code was written**, not **what it does** — so when the code had real bugs, the tests stayed green.

---

## What Went Wrong with the Old Approach

### 1. Tests That Tested Nothing

**`test_calculation_signals.py`** mocked two internal methods: `_load_state_map` and `_save_state_map`. These methods were later **removed from the production code entirely**. The tests kept passing because they were only testing their own mocks — they never touched the real system.

> **Impact:** Zero defect detection. The test file existed purely for coverage numbers.

### 2. Fake Models Instead of Real Ones

**`test_lifecycle_hooks.py`** created model stubs with `_make_model_stub()` — Python objects that looked like Django models but weren't. The test verified that a list of hook names was returned in the right order. It never tested whether Django actually fires those hooks when you call `.save()`.

> **Impact:** If a hook was silently broken (wrong trigger condition, wrong ordering), this test would never catch it.

### 3. Mocking the System Under Test

**`test_calculated_model_mixin.py`** tested Celery dispatch by mocking `os.environ`, `is_celery_worker_process`, `CeleryTaskDispatcher`, and `calc_and_save_sync`. Every collaborator was faked. The test verified that mock A called mock B with the right arguments — it never tested that a calculation actually runs.

> **Impact:** Any change to the dispatch internals breaks these tests even if the feature still works. Meanwhile, real dispatch bugs go undetected.

### 4. Tests Swallowing Errors

Several old tests used bare `except: pass` blocks or `print()` statements where assertions should be. `test_history_api` was checking API responses that returned **404 errors** — the test passed because it never asserted on the status code.

> **Impact:** The test suite reported "all green" while real features were broken.

---

## What Made Us Realize This

During a test audit session, we switched our E2E tests from using the internal pattern:

```python
# Old pattern — bypasses normal save flow
instance.save(skip_hooks=True)
instance.calculate_hook()
```

to the documented canonical pattern:

```python
# Canonical pattern — what customers and the API actually do
instance.is_calculated = "IN_PROGRESS"
instance.save()
```

**Nine tests immediately failed.** Not because the tests were wrong — because they exposed a real framework bug that had been invisible.

### The Bug

`LexModel.save()` wraps the IN_PROGRESS state write **and** all lifecycle hooks (including `calculate_hook`) inside a single `transaction.atomic()` block. When a calculation fails:

1. The IN_PROGRESS state was written to the database ✓
2. The history record for IN_PROGRESS was created ✓
3. `calculate_hook()` runs, calls `calculate()`, which throws an exception ✗
4. The exception propagates out of the `transaction.atomic()` block
5. **Everything rolls back** — the IN_PROGRESS state, its history record, even the ERROR state written by the exception handler
6. The object stays `NOT_CALCULATED` with **no trace of what happened**

This means: if a calculation fails, there is **no forensic evidence** that it was ever attempted. No IN_PROGRESS history. No ERROR history. The record looks like nothing happened.

### Why Old Tests Missed It

The old tests used `save(skip_hooks=True)` followed by a manual `calculate_hook()` call. This pattern:
- Commits the IN_PROGRESS state in its own transaction (the `save()` call)
- Then runs `calculate_hook()` separately

So the IN_PROGRESS state survives failures — but only in tests. In production, the API path (`One.py`) does it correctly too (it also uses `save(skip_hooks=True)` to commit IN_PROGRESS independently). But the ORM path that customers use when triggering calculations from within `calculate()` methods (parent → child) goes through the buggy `save()` flow.

**The old tests were accidentally working around the bug instead of finding it.**

---

## Old vs New — Side by Side

| Situation | Old Tests Said | Reality | New Tests Say |
|-----------|---------------|---------|---------------|
| Calculation fails | ✅ All green | IN_PROGRESS history lost, object stuck as NOT_CALCULATED | ❌ 9 failures — bug documented |
| Hook removed from code | ✅ All green (mocks don't care) | Feature silently broken | ❌ Fails immediately — real hook doesn't fire |
| API returns 404 | ✅ All green (no status assertion) | Feature broken for users | ❌ Fails — asserts 200/201 |
| Startup reset of stuck calcs | ✅ All green | Uses `queryset.update()`, bypasses history signals — no audit trail | ❌ Fails — asserts history row exists |

---

## The Core Principle

> **A test should fail when the feature breaks, and pass when the feature works.**
>
> If a test passes when the feature is broken, it is worse than no test at all — it gives false confidence.

Our old tests optimized for **green checkmarks**. The new tests optimize for **defect detection**.

---

## What This Means Going Forward

1. **Tests use the same code paths as customers** — `instance.save()`, REST API calls, the documented patterns from our own docs.
2. **Mocks only at true external boundaries** — Celery broker, WebSocket, Redis, S3. Never mock the ORM, never mock the class under test.
3. **Real Django models with real database tables** — created dynamically in tests, cleaned up after. No stubs.
4. **Tests that fail are valuable** — a failing test that exposes a real bug is more valuable than 100 passing tests that test nothing.
5. **A dedicated test project** — mirrors customer project structure, so we test the framework the way it's actually used.

---

> **Back to:** [Test Plan Index](index.md) | **Next:** [Testing Philosophy](testing-philosophy.md) · [Clusters](clusters/)
