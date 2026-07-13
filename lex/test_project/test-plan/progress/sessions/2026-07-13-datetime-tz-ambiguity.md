---
date: 2026-07-13
clusters: [12g]
tests_added: "3 (12.36–12.38, all xfail strict — BUG-025)"
suite_tally: "12g 0 pass / 3 xfail / 0 unexpected; regression: full `serializers` cluster unchanged"
---

**Batch 12g landed — bug-documentation batch for BUG-025 (customer tickets
2026-07-13, instance 1409: `edited_at` shows 11:43 for a 13:43 edit; the same
instance's calculation-log times are off by the same 2 hours).** Root cause
traced, not guessed: `settings.py` couples `USE_TZ=False` → `TIME_ZONE="UTC"`
on the `default`/`GCP` deployment targets (deliberately — django_celery_beat
reads naive datetimes as UTC), so `lex_datetime_now()` and `auto_now_add`
store naive UTC, DRF serializes them as bare ISO strings with no `Z`/offset,
and browsers parse naive ISO strings as *local* time — rendering UTC
wall-clock as if it were local, i.e. −2h in Berlin summer. `USE_TZ=True`
targets serialize `…+02:00` and render correctly (why instance 1410 is
unaffected). The three scenarios assert the correct contract (every serialized
datetime ends in `Z`/`±HH:MM`) and are `xfail(strict=True)` until the fix
lands. The companion stuck-`IN_PROGRESS` ticket was triaged as operational
(recovery supervisor likely not running on that instance) — the recovery
machinery itself is already pinned by clusters 8w/8x/8y, so no new framework
bug was recorded for it.

[Batch 12g](../../clusters/12-serializers/batches.md); bug row in
[known-bugs.md](../../known-bugs.md) (BUG-025, includes both fix options).
Source under test: `lex/core/models/LexModel.py`,
`lex/audit_logging/serializers/CalculationLogSerializer.py`,
`lex/lex_app/settings.py`.
