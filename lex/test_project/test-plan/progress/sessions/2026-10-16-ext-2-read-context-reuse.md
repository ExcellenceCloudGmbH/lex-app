---
date: 2026-10-16
clusters: [12]
tests_added: "1 new scenario (12.68)"
suite_tally: "12n 1 pass (fails against the previous serializer: 32 vs 5 auth_group queries); stress 11.3 now passes; serializers, permissions, crud_api, api_layer, queries, audit_logging and history clusters 598 pass, the one failure (5.117) passes alone and came from a concurrent run on the same test database"
---

# BUG-011: the serializer builds the read context once per request

Follow-up on feat/ext-2-permission-core. Where the serializer decides rows without the list filter's remembered answers (a project view, serialize_for, a list with filter backends switched off as the stress cluster does), LexModel's own can_read built a fresh UserContext per row, one auth_group query each. The serializer now builds that context from the one it caches for the request; the answer per row is the same. Stress 11.3 (query budget per page) passes; 11.14 still fails by design: it asks for one permission_read call per request, which only permission_read_queryset can give. The stress tests' 100-row page is in fact 500 rows, because the list endpoint ignores page/page_size.

Batches: [12n](../../clusters/12-serializers/batches.md).
