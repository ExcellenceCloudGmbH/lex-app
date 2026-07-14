---
date: 2026-07-14
clusters: [5m]
tests_added: "4 (5.98–5.101; 5.101 xfail strict — BUG-026)"
suite_tally: "5m 3 pass / 1 xfail; regression: full `history` cluster = 39 pass / 1 skip / 1 xfail / 0 fail"
---

**Batch 5m landed — edit-time + as_of round-trip gates (customer concern
2026-07-14: "when we edit something the time is correct... we rely on the
as_of mechanism").** The suspected timezone-shift bug in as_of did NOT
reproduce: the chain edited_at (naive UTC) → `Z` serialization (BUG-025 fix)
→ `parse_as_of_datetime` → `get_queryset_as_of` windows is consistent — the
customer's observed symptom was almost certainly BUG-025's display shift
contaminating manually-chosen anchors, already fixed. Scenarios 5.98–5.100
pin that as live gates (true edit instant, pre-edit snapshot with values,
full knowledge at now). **5.101 surfaced a real, different bug (BUG-026):**
`edited_at` and the new version's `valid_from`/`sys_from` come from separate
clock reads (~1–2 ms apart), so `as_of` anchored at the record's own
serialized `edited_at` — the natural client anchor — still shows the
pre-edit state. Marked `xfail(strict=True)`. Fix design (single per-save
clock read consumed by both the audit-field hooks and the history/meta
stampers) and the `_history_date` trap (its absence is load-bearing —
presence would re-enable the expensive history→main synchronizer on every
save) are documented in the BUG-026 row.
See [batch 5m](../../clusters/05-history/batches.md).
