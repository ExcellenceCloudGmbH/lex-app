---
date: 2026-10-09
clusters: [2, 7]
tests_added: "4 new scenarios (2.113-2.116); 7.236 rewritten to check side effects"
suite_tally: "2l 7 pass, 7u 10 pass, 12m 5 pass, 8v 12 pass (34 pass / 0 fail); full cluster suite 1673 pass / 37 fail / 27 skip / 6 xfail / 5 collection errors - every failure in the categories the earlier runs on this branch and on the calculate-on-create branch had (24 cluster-15 rows when run after cluster 6, 6 Reflex CLI with no reflex package locally, 5 stress timing, the gate self-test); browser check on the e2e harness through the create and edit forms"
---

# Batch 2l, extended — editing a closed record

Review of the closed-calculations change asked two things: whether a `calculate_on_create` model's
run start asks `calculation_closed_reason()` too, minding the side effects of the hook that starts
it; and that every field other than the calculation stays editable on a closed record.

[7u](../../clusters/07-calculations/batches.md)'s 7.236 now checks that a record created closed —
its model's `calculation_closed_reason()` answers a reason; no field name is involved — sets off
nothing of a run: no registration, broadcast or cache entry, no run audit entry, no history row
beyond its creation. It passed as written: the create path asks the same method as every other
path.

[2l](../../clusters/02-crud_api/batches.md) gained four edit scenarios. The fields were already
editable, but every edit through the app reset the record to NOT_CALCULATED, which a closed record
can never leave, and closing a calculated record through an edit flipped it to NOT_CALCULATED in
the same save (2.113 and 2.114 failed). The edit now asks `calculation_closed_reason()` on a copy
carrying the incoming values, and a record closed once the edit is applied keeps its status;
reopening, and every open record, reset as before (2.115, 2.116). Breaking it four ways failed the
right tests each time: always resetting (2.113, 2.114), asking the record from before the edit
(2.114, 2.115), re-saving without the guard (2.113, 2.114), and dropping the create-time check
(7.236).
