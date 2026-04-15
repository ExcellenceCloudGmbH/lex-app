# Temporal & Bitemporal Tests — `lex.tests.unit.temporal`

> **Story:** *"Every data record has two time axes — valid time (when the fact
> is true in the real world) and system time (when the system recorded it).
> The temporal layer must parse as-of queries, synchronise history to the main
> table, suppress signals during bulk operations, and reconcile stale records."*

## What Lives Here (6 files, 48 tests)

| File | Tests | Covers |
|------|------:|--------|
| `test_temporal_parse_as_of.py` | 14 | Bitemporal query-param parsing — None/garbage input, Z-suffix, naive→UTC, offset conversion, `DateTimeField` handling, edge cases (date-only, numeric input) |
| `test_bitemporal_synchronizer.py` | 7 | Main-table sync — upsert from effective history record, changed-field-only saves, deletion-record handling, no-effective-record deletion, history-model attribute discovery |
| `test_bitemporal_suppression.py` | 16 | `suppress_bitemporal_signals` context managers — default-is-False, active-inside-block, restore-after-exit, exception safety, nesting, combined suppression of all three flags |
| `test_history_deletion.py` | 3 | Mid-chain history deletion extends predecessor's `valid_to`, history-descriptor class caching, main-record deletion creates `valid_to` + meta-history entry |
| `test_temporal_progression.py` | 1 | Passage-of-time activation — a future-valid record appears in the main table after `reconcile_time` runs |
| `test_temporal_reconciler.py` | 7 | Single-model reconciliation, cross-model reconciliation, time-window filtering, models-without-history skip, default end-time-to-now |

## Key Concepts Tested

- **As-of parsing** — raw query params → UTC `datetime` for bitemporal queries
- **History sync** — `BitemporalSynchronizer` keeps the main table in sync with the latest effective history record
- **Signal suppression** — context managers to disable bitemporal signals during bulk imports
- **Deletion semantics** — deleting a history record extends the predecessor's validity; deleting a main record creates a terminal history entry
- **Temporal reconciliation** — `TemporalReconciler` activates future-valid records whose `valid_from` has passed

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
lex test lex.tests.unit.temporal           # all 48 tests
lex test lex.tests.unit.temporal.test_bitemporal_suppression  # 16 tests
```
