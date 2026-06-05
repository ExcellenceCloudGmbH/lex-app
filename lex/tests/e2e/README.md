# End-to-End Tests — `lex.tests.e2e`

> **Story-driven tests** exercising the full Lex stack — from REST API
> to database and back — with real models, real history, real calculations.

## Philosophy

Unlike unit tests (everything mocked) or integration tests (one subsystem
at a time), these tests tell **complete user stories** with deliberate
mistakes and corrections.  They exercise:

- **CRUD lifecycle** — POST/GET/PUT/PATCH/DELETE through DRF
- **Calculation lifecycle** — `NOT_CALCULATED → IN_PROGRESS → SUCCESS / ERROR`
- **Atomicity** — `is_atomic` flag, `transaction.atomic`, rollback on failure
- **Validation guards** — `pre_validation` blocks, `post_validation` rolls back
- **Audit trail** — history records, meta-history, actor tracking
- **Permission boundaries** — model-level and field-level access control
- **Serializer logic** — `lex_reserved_scopes`, FK filtering, field visibility

## Test Models

Tests define their own models inline (like the integration tests) rather
than importing from `e2e_project`.  This keeps them self-contained and
runnable with the standard `django_test_settings.py`.

## Files

| File | What it covers |
|------|----------------|
| `test_fund_manager_journey.py` | 5-act story: setup → mistake → correction → calculation → permissions |
| `test_calculation_atomicity.py` | Atomic vs non-atomic calculations, nested parent→child, post_validation rollback |
| `test_audit_and_history.py` | History records, bitemporal corrections, actor tracking through CRUD + calculation |

## How to Run

```bash
source /path/to/your-project/.venv/bin/activate  # the host project where lex-app is installed editable
lex test lex.tests.e2e --noinput
```
