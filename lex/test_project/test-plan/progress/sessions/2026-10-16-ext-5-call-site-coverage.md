---
date: 2026-10-16
clusters: [9]
tests_added: "4 new scenarios (9.49-9.52)"
suite_tally: "9i 4 pass, 9h 5 pass, signals_ws 58 pass; 9.49-9.51 fail against the pre-PR One/Many, 9.49 and 9.52 against the one-group update broadcast"
---

# Call-site coverage for the realtime and performance PR

Follow-up on feat/ext-5-realtime-keycloak so that every source file the PR touches has a test of its own (copilot coverage gate). The single-record endpoint is checked to hand the changed record to the mutation broadcast on create, update and delete, the bulk endpoint to hand the model class, and both to honour broadcast_mutations = False.

Changed after review: an update used to reach only the groups of the record as loaded when the request started (One's view-local instance), so a record moved from one group to another notified only the old group. One now also keeps the copy DRF saved, and broadcast_model_mutation(previous=...) sends the event to the groups of both versions, each group once (9.49, 9.52).

Batches: [9i](../../clusters/09-signals_ws/batches.md).
