---
date: 2026-10-17
clusters: [4]
tests_added: "1 new scenario (4.114)"
suite_tally: "4s 1 pass (fails against 0653cc0c: empty body); 4q 7 pass with LEX_LEGACY_SECURITY=true and without; 12n 1 pass"
---

# A classic API key keeps its scopes in the serializer's read decision

Fix on feat/ext-2-permission-core found by the legacy-mode run of the full stack: 4.110 (classic API key reads a record) answered 200 with an empty body when LEX_SEC_NOT_FOUND was off. The BUG-011 change built LexModel.can_read's context with base.with_instance(), which re-resolves Keycloak scopes from the user's permissions; a classic key's context has none, so it lost the key's scopes. The serializer now builds exactly what UserContext.from_request builds: the API-key context as it is, any other context with the record's scopes.

Batches: [4s](../../clusters/04-permissions/batches.md).
