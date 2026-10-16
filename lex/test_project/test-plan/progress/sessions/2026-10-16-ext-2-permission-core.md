---
date: 2026-10-16
clusters: [4, 12, 14]
tests_added: "30 new scenarios (4.86-4.103, 12.63-12.67, 14.36-14.42); 4.4 restored as a live test (BUG-010); 4.41, 10.47-10.54 and the 12h fixture updated to the 3.0 behaviour"
suite_tally: "4p 18 pass, 4b incl. 4.4 pass, 12l 5 pass, 14f 7 pass; full cluster suite (not stress, Reflex excluded) 1693 pass / 49 fail before the follow-up fixes - 33 identical to lex-app-v2, 2 fixed by PR-1's amendment, 14 were tests written against pre-3.0 behaviour or mocks without a real model (4.41, 4.65/4.65b, 10.47-10.54, 12.39-12.41), all updated and passing (permissions, serializers, queries, api_layer file-download: 244 pass)"
---

# Secure permission defaults on the generic REST API

Second of five stacked PRs (feat/ext-2-permission-core). Permission methods are enforced on every surface by default (LEX_LEGACY_SECURITY / LEX_SEC_<FLAG> opt out): fail closed, 404 for unreadable records on single, bulk and history endpoints, permission_edit on PATCH/PUT/bulk/run_step, permission_delete per row on bulk delete, permission_create on run_step, read checks on history/search/export/file download, and the filter allow-list. New hooks: permission_read_queryset, permission_serializer, HTMLReport.permission_view; lex_strict_input; EXPOSE_AUTO_SERIALIZER; serialize_for. permission_read now runs once per row and request.

Batches: [4p](../../clusters/04-permissions/batches.md), [12l](../../clusters/12-serializers/batches.md), [14f](../../clusters/14-queries/batches.md). Known bugs: BUG-010 resolved (4.4 live again); BUG-011's duplicate evaluations removed, the per-row evaluation itself remains for models without permission_read_queryset.
