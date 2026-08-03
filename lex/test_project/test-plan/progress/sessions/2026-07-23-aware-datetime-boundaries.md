---
date: 2026-07-23
clusters: [3, 13]
tests_added: 10
suite_tally: "validation_hooks+exports+history+init+serializers: 446 pass / 14 skip / 2 xfail (3 fails are untracked local scratch tests, pre-existing)"
---

# Aware-datetime boundaries: assignment invariant + report-file Excel

Closed the two naive/aware seams the `USE_TZ=True` cutover left open, found by a
downstream project's real calculation run (`TypeError: Cannot compare tz-naive
and tz-aware timestamps` in a pandas sort, then `Excel does not support
datetimes with timezones` while writing the report file).

- [Batch 3g](../../clusters/03-validation_hooks/batches.md) — `AwareDateTimeDescriptor`
  makes every `DateTimeField` assignment aware at set-time (default-tz, the
  save-time interpretation), so in-memory objects agree with fetches; includes
  the deferred-loading data-descriptor regression guard.
- [Batch 13g](../../clusters/13-exports/batches.md) — `XLSXField` renders aware
  datetimes as display-zone wall-clock (naive) across columns, object columns,
  (multi)index and headers; round-trip proven back to the exact instant via the
  3g invariant.

Blast-radius run (validation_hooks, exports, history, init, serializers):
446 pass — the only 3 failures are untracked local scratch tests from the
original bug diagnosis that assert the pre-fix shifted behaviour.
