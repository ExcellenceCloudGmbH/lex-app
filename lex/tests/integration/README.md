# Integration Tests

> **Location:** `lex/tests/integration/`
> **Test runner:** `python -m django test lex.tests.integration --settings=lex.process_admin.tests.django_test_settings -v2`
> **Base classes:** `TransactionTestCase`, `APITestCase` (all tests use a real SQLite database)

---

## What Are Integration Tests?

Integration tests exercise **multiple modules working together** against a
real database.  They create tables with `schema_editor`, insert rows, call
real Django ORM / DRF code, and verify end-to-end behaviour.  They are
slower than unit tests (~1-5 s each) but catch bugs that mocks miss:
foreign-key cascades, signal side-effects, serializer ↔ view ↔ model
interactions, and bitemporal chaining logic.

---

## Files

### `test_bitemporal.py` — *Story 9: "Time Travel Works Correctly"*

> *As an analyst, I can view any record as it existed at any point in time.
> If someone corrects a historical entry, I can see both the correction
> and the original — with full system-time and valid-time dimensions.*

Consolidated from six separate files that shared overlapping setup code
and test models.  Each test class verifies a different facet of the
3-layer bitemporal architecture (Main Table → History → Meta-History):

| Class | Tests | What It Proves |
|-------|------:|----------------|
| `BitemporalLogicTest` | 1 | Core chaining: correcting a history row rewires valid-time boundaries and preserves system-time meta-history |
| `BitemporalTraceTest` | 1 | Canonical user trace: retroactive corrections extend predecessor valid-time boundaries |
| `BitemporalScenarioTest` | 3 | Multi-step create/update/correction chains; deletion closes intervals; retroactive `_history_date` |
| `BitemporalRobustnessTest` | 2 | Edge cases: future-valid rows don't appear in main table; validity gaps clear main table |
| `BitemporalAsOfTest` | 3 | `get_queryset_as_of` hides future-valid rows, returns correct snapshot at any point, supports history model |
| `BitemporalHistoryEditTest` | 4 | Direct data-field edits on Level-1 history records: current vs non-current, system-time snapshots, sequential edits |
| `BitemporalHistoryDeletionAsOfTest` | 5 | Deleting a history row: chain repair, meta-history audit trail, system-time as-of before/after deletion |
| `TestHistoryTimelineAPI` | ~8 | REST API endpoint (`/history/<pk>`) with `as_of` parameter, deleted-record history, bitemporal correction via API |

**Labels:** `integration`, `bitemporal`, `data-integrity`, `regression`

---

### `test_user_stories.py` — *Story 10: "A Full Day in the Life of a Portfolio Manager"*

> *Integration tests simulating real user workflows: create records, run
> calculations, fix mistakes, verify the audit trail — using real Django
> ORM operations against SQLite.*

| Class | Tests | What It Proves |
|-------|------:|----------------|
| `TestDataEntryLifecycle` | 3 | Creating and updating domain records auto-tracks created_by, edited_by, timestamps |
| `TestCalculationLifecycle` | 3 | Calculation transitions (SUCCESS, ERROR), recalculation with updated input |
| `TestNestedCalculation` | 2 | Parent CalculationModel triggers child calculations inside `model_logging_context` |
| `TestValidationGuards` | 4 | `pre_validation` blocks invalid saves; `post_validation` rolls back on violation |
| `TestDataChangeDrivesRecalculation` | 1 | End-to-end: data change → re-trigger → new result |

**Labels:** `integration`, `crud`, `state-machine`, `lifecycle`, `error-handling`

---

### `test_api_user_journey.py` — *Story 10b: "The Same Day, Through the REST API"*

> *The same user workflows as test_user_stories, but exercised through the
> full DRF stack — HTTP verbs, URL routing, authentication, serialisation,
> permission checks, and all the way to the database and back.*

| Class | Tests | What It Proves |
|-------|------:|----------------|
| `TestCrudLifecycle` | 6 | POST create, GET detail, GET list, PUT update, PATCH partial, DELETE |
| `TestCalculationTrigger` | 2 | `calculate: true` triggers calculation; plain PUT resets `is_calculated` |
| `TestMistakeAndCorrection` | 4 | History generated on create/update, history API returns versions, direct history edit, delete tombstone |
| `TestListFilteringAndOrdering` | 2 | `?ordering=budget`, `?status_text=active` query-param filtering |
| `TestPermissionDenied` | 1 | `permission_create → False` returns 400 |
| `TestEndToEndWorkflow` | 1 | Full session: create → list → calculate → fix mistake → recalculate → review history |

**Labels:** `integration`, `crud`, `security`, `bitemporal`, `ui-contract`

---

### `test_calculation_log.py` — *Story 11: "Calculation Logs Don't Duplicate or Drop"*

> *Regression tests for CalculationLog deduplication and delivery.  When
> the same calculation logs a message, it must append — not create a
> duplicate row.  Cache + WebSocket fan-out must reach both root and
> current records.*

| Class | Tests | What It Proves |
|-------|------:|----------------|
| `CalculationLogRegressionTest` | 5 | Duplicate log rows reuse oldest, deferred persistence inside `atomic()`, cache + WS fan-out, model-context fallback |

**Labels:** `integration`, `audit`, `async`, `data-integrity`, `regression`

---

### `test_audit_recovery.py` — *Story 11b: "Crashed Calculations Leave Auditable Traces"*

> *When a sync calculation raises, the failure branch must create a
> terminal AuditLog/AuditLogStatus pair with the real traceback — so
> support sees the root cause, not a wrapper.*

| Class | Tests | What It Proves |
|-------|------:|----------------|
| `CalculationAuditRecoveryTests` | 2 | Sync failure creates terminal audit records; nested exception preserves inner traceback |

**Labels:** `integration`, `audit`, `error-handling`, `regression`

---

### `test_event_scheduling.py` — *Story 11c: "Future Records Are Scheduled and Revocable"*

> *Inserting a future-valid record creates a Celery periodic task.
> Deleting the record revokes the task.  Meta-history tracks the
> SCHEDULED status.*

| Class | Tests | What It Proves |
|-------|------:|----------------|
| `EventSchedulingTest` | 2 | Future insert creates `PeriodicTask`; deletion revokes it |

**Labels:** `integration`, `async`, `bitemporal`, `state-machine`

---

## Shared Infrastructure

### `_bitemporal_test_case.py`

Reusable `TransactionTestCase` base class for dynamically-registered
bitemporal test models.  Handles:

- Model registration with `simple_history` + `ModelRegistration`
- Table creation/teardown for Main + History + Meta-History
- Cleanup of `registered_models` to avoid cross-test pollution

Used by all `test_bitemporal.py` classes that subclass
`DynamicBitemporalModelTestCase`.

---

## How to Run

```bash
# Activate venv
source /path/to/your-project/.venv/bin/activate  # the host project where lex-app is installed editable

# All integration tests (60 tests)
lex test lex.tests.integration

# Single file
lex test lex.tests.integration.test_bitemporal

# Single class
lex test lex.tests.integration.test_bitemporal.BitemporalAsOfTest

# Old labels still work (via re-export shims)
lex test lex.core.tests.test_bitemporal_as_of
```
