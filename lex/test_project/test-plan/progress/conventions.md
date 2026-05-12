# Test-Plan Conventions

> **Back to:** [Progress index](../progress.md) | [Test Plan Index](../index.md)
> **Audience:** anyone authoring a new test (human or Copilot) — the stable rules that don't change per session.

This file owns the methodology, naming, and quality gates. The high-churn per-cluster status lives in [`dashboard.md`](dashboard.md); the per-session narrative lives in [`session-log.md`](session-log.md).

---

## How We Organize the Work

### Rule: Work in Cluster Order

Clusters are ordered by the user journey (see [test-clusters.md](../test-clusters.md#ordering-the-user-journey)). We implement them **in order** because each cluster builds on the one before it:

```
1. Initial Data → 2. CRUD → 3. Validation → 4. Permissions → 5. History → ...
```

**Exception:** If a cluster is blocked by a framework bug, skip it (mark ) and move to the next. Come back after the bug is fixed.

### Rule: One Cluster at a Time

Don't start Cluster N+1 until Cluster N is  or . This keeps work focused and progress visible.

### Rule: Test Intent, Never Overfit to Source Code

> **The source code is an incomplete story.** It has bugs, shortcuts, and workarounds.
>
> Tests must assert what the framework is **trying to achieve** — derived from the docs, the public API, and what a customer would reasonably expect. Never write a test by reading the current implementation and asserting "what the code does today."
>
> **If the code is buggy, the test fails. That is correct.** Mark the failing test `@unittest.expectedFailure`, add the bug to the [Known Bugs Tracker](#known-bugs-tracker), and move on. The test now guards against the bug being forgotten.
>
> **Never** weaken a test to make it pass on broken code. That is how the old suite ended up with 2,000 green tests that missed real bugs. See [Golden Rule + red flags in test-clusters.md](../test-clusters.md#testing-philosophy).

### Rule: Test First, Then Fix

When a test exposes a framework bug:
1. Write the test asserting the **correct** behavior (from docs / intent)
2. Mark it `@unittest.expectedFailure` with a comment explaining the bug and linking the tracker entry
3. Add the bug to [Known Bugs Tracker](#known-bugs-tracker)
4. Move on — don't block the test suite on the fix
5. When the bug is fixed, remove `@unittest.expectedFailure` — the test should now pass naturally

---

## User Experience: Making Tests Readable

Tests are documentation. A new developer reading a test should understand:
- **What** is being tested (the scenario)
- **Why** it matters (the risk)
- **How** to reproduce it (the setup)

### Naming Convention

```python
def test_<cluster_number>_<short_description>(self):
    """
    Scenario X.Y: <one-line description from the cluster table>
    
    Given: <setup>
    When: <action>
    Then: <expected outcome>
    """
```

**Example:**
```python
def test_02_01_create_sets_timestamps(self):
    """
    Scenario 2.1: Create a record via ORM
    
    Given: A SimpleItem model
    When: We create and save a new instance
    Then: created_at and edited_at are set, created_by = resolved actor
    """
    item = SimpleItem(name="Test", value=42)
    item.save()
    
    self.assertIsNotNone(item.created_at)
    self.assertIsNotNone(item.edited_at)
    self.assertEqual(item.created_by, "Initial Data Upload")
```

### Assertion Messages

Every assertion has a human-readable failure message:

```python
# Bad
self.assertEqual(item.is_calculated, "SUCCESS")

# Good
self.assertEqual(
    item.is_calculated, "SUCCESS",
    "After successful calculation, state must be SUCCESS"
)
```

### Test Class Organization

One test class per cluster, named clearly:

```python
class TestCluster02_CRUDLifecycle(E2ETestCase):
    """Cluster 2: CRUD & Lifecycle — tests the basic LexModel contract"""
    e2e_models = [SimpleItem, TrackedItem]
```

---

## How to Run Tests

### Run all clusters
```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
lex test lex.test_project.tests --noinput
```

### Run a single cluster
```bash
lex test lex.test_project.tests.test_02_crud_lifecycle --noinput
```

### Run with coverage
```bash
coverage run --source=lex manage.py test lex.test_project.tests --noinput
coverage report
```

---

## Quality Gates

Before any release, these must hold:

1. **All clusters  or ** — no cluster in  or  state
2. **Zero unexpected failures** — every failure is either a passing test or an `expectedFailure` with a tracked bug
3. **CI pipeline green** — `lex test lex.test_project.tests --noinput` passes in CI
4. **No overfitting** — no test uses `skip_hooks=True` + `calculate_hook()` (the pattern that works around bugs). No test mocks the class under test. No test was written by reading the implementation instead of the docs.
5. **Every `expectedFailure` is tracked** — must have an entry in [Known Bugs Tracker](#known-bugs-tracker) with severity and cluster
6. **Coverage threshold met** — `COVERAGE_FAIL_UNDER` not decreased

---

> **Back to:** [Progress index](../progress.md) | **See also:** [Test Clusters](../test-clusters.md) | [Dashboard](dashboard.md) | [Session Log](session-log.md)
