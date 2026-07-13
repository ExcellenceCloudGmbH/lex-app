## Cluster 12 — Serializer Contract

(no batches recorded yet)

---

### Batch 12g — Datetime timezone ambiguity (BUG-025) ✅

| Property | Value |
| --- | --- |
| Scenario range | 12.36 – 12.38 |
| Type | E |
| Files covered | `core/models/LexModel.py` (`lex_datetime_now`, `edited_at`/`created_at` stamping), `audit_logging/serializers/CalculationLogSerializer.py` (`timestamp`), `lex_app/settings.py` (`USE_TZ`/`TIME_ZONE` coupling) |
| Test file | `lex/test_project/tests/serializers/test_12g_datetime_tz_ambiguity.py` |
| Test classes | `TestCluster12g_DatetimeTimezoneAmbiguity` |
| Fixtures | reuse cluster-12 `WideItem` models |
| Est. tests | 3 |
| Coverage gain | bug-documentation batch (xfail strict) |
| Prereqs | none |
| Status | ✅ Complete — 3 xfail(strict) / 0 unexpected |
| Note | Confirms **BUG-025** (customer tickets 2026-07-13, instance 1409: `edited_at` and log times shifted −2h). Asserts the CORRECT contract — every REST-serialized datetime ends in `Z`/`±HH:MM` — which fails on `USE_TZ=False` targets because naive-UTC values serialize as bare ISO strings that browsers parse as local time. Markers drop when the fix (explicit `Z` rendering, or `USE_TZ` decoupling) lands; the batch then becomes a live regression gate. |
