---
date: 2026-10-16
clusters: [9]
tests_added: "3 new scenarios (9.49-9.51)"
suite_tally: "9i 3 pass, 9h 5 pass; each 9i scenario fails against the pre-PR One/Many (checked by swapping the old files in)"
---

# Call-site coverage for the realtime and performance PR

Follow-up on feat/ext-5-realtime-keycloak so that every source file the PR touches has a test of its own (copilot coverage gate). The single-record endpoint is checked to hand the changed record to the mutation broadcast on create, update and delete, the bulk endpoint to hand the model class, and both to honour broadcast_mutations = False.

Observed and pinned: an update is sent to the groups of the record as it was loaded when the request started (One broadcasts with its view-local instance), so a record moved from one group to another notifies only the old group. Left as is for review.

Batches: [9i](../../clusters/09-signals_ws/batches.md).
