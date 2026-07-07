# Testing Philosophy

> **Back to:** [Test Plan Index](index.md) | **Progress:** [Progress & Organization](progress.md)  
> **Audience:** Engineering leadership, developers

---

## Ordering: The User Journey

Clusters are ordered by **how a customer first encounters the framework**, not by internal architecture. A new user:

1. Sets up a project and runs `lex setup` + `lex Init` → **Init — Project Bootstrap**
2. Creates, reads, updates, deletes records through the REST API → **CRUD via REST API**
3. Adds validation rules to protect data quality → **Validation Hooks**
4. Controls who can see and edit what → **Permissions**
5. Views the change history of records → **History & Bitemporal**
6. Checks audit logs for compliance → **Audit Logging**
7. Adds calculations that derive values from data → **Calculation State Machine**
8. Scales calculations with Celery → **Celery & Async**
9. Gets real-time updates in the UI → **Signals & WebSocket**
10. Builds integrations through the REST API → **API Layer**
11. Runs their real dataset through it all — and expects it to finish in reasonable time → **Stress & Performance**
12. Builds a frontend or integration that consumes the JSON — and expects the shape to be stable → **Serializer Contract**
13. Clicks "Export to Excel" from the AG Grid UI — with filters, grouping, row selection, and FKs — and expects the file to be correct → **Export Endpoint**
14. Scrolls, sorts, filters, groups and pivots in the AG Grid UI — and expects every query to return the right rows → **AG Grid Query Endpoint**

This ordering means: **if cluster N is broken, clusters N+1 through 10 are also likely broken.** We test foundations first.

---

## Testing Philosophy

> ### ⚠️ THE GOLDEN RULE
>
> **Test what the framework is _trying to achieve_, not what the current code happens to do.**
>
> The source code is an **incomplete story**. It has bugs, workarounds, and shortcuts. If we write tests that mirror the code, we lock in those bugs as "correct behavior" and the test suite becomes a shield for broken features instead of a detector of them.
>
> **How to find the intent:**
> 1. Read the docs in [docs/features/](../features/), [docs/reference/](../reference/), and [docs/tutorial/](../tutorial/)
> 2. Read the public API docstrings
> 3. Ask: _"What would a customer reasonably expect from this feature?"_
> 4. Write the test for **that** — even if the current code fails it
>
> **If a test fails because the code is buggy: good.** Mark it `@unittest.expectedFailure` with a reference in the [Known Bugs Tracker](known-bugs.md). The failure is the test doing its job.
>
> **Never adjust a test to match broken behavior.** That is overfitting, and it is how we ended up with 2,000 green tests that missed real production bugs.

---

### The Rules

Every test in every cluster follows these rules:

1. **Test intent, not implementation** — derived from docs and reasonable customer expectations, not from reading the current `save()` / `calculate_hook()` source
2. **Use the same code path as a customer** — `instance.save()`, REST API calls, documented patterns — never `skip_hooks=True` + manual hook calls to work around bugs
3. **Mock only at true external boundaries** — Celery broker, WebSocket, Redis, S3, Keycloak HTTP
4. **Real Django models with real database tables** — no stubs, no fakes, no `_make_model_stub()`
5. **Assert on observable behavior** — final state in DB, history rows, API response codes and bodies. Never assert on mock call counts as a substitute for behavior.
6. **A failing test is valuable** — if it exposes a real bug, track it and keep it. Do not delete or weaken it.

### Red flags that mean a test is overfitting

If any of these are true, the test is probably wrong:

- ❌ Test sets up the exact internal state the implementation needs, then asserts that state survives
- ❌ Test mocks a method on the class under test
- ❌ Test mocks the ORM while testing ORM-dependent code
- ❌ Test asserts `mock.called_once_with(...)` but never checks the real effect
- ❌ Test passes even when the feature is known to be broken
- ❌ Test breaks when an internal helper is renamed, even though the feature still works
- ❌ Comment in test says _"work around framework bug"_ or _"use skip_hooks to avoid X"_

---

