---
date: 2026-10-19
clusters: [1, 4, 10, 13, 14]
tests_added: "9 new scenarios (4.119-4.124, 14.84-14.86); 4.113, 10.94's helper and 14.75 changed; this branch's batches renumbered past lex-app-v2's"
suite_tally: "focused (this branch's files and lex-app-v2's new files in clusters 1, 4, 10, 13, 14, plus serializers): 290 pass / 0 fail / 2 skip / 1 xfail"
---

# Merging lex-app-v2 into the value-list filters — #833's secure defaults

lex-app-v2 took batch letters and scenario numbers this branch held (1aw, 4o, 10o, 14f), so this
branch's batches moved past them: 1ax and [1ay](../../clusters/01-init/batches.md),
[4q](../../clusters/04-permissions/batches.md), [10p](../../clusters/10-api_layer/batches.md), and
[14k, 14g and 14j](../../clusters/14-queries/batches.md).

[14l](../../clusters/14-queries/batches.md) pins the value lists and the list's GET lookups to
lex-app-v2's filter allow-list, and a fix the merge needed: the merged serializer's read-once check
assumed every model has `can_read`, so a plain Django model's list answered 500.
[4r](../../clusters/04-permissions/batches.md) makes `filter_values` follow the list under the
secure defaults: a raising permission check withholds the row from both counting paths, a column
the caller may not filter by is refused like a field that does not exist (and the fields endpoint
and the filter bar agree), a refusing `permission_list` answers 404, and every switch restores the
old behaviour. 4.113 moved to a per-row empty list; the design records the change as Amendment 17.
14.75 now pins that an API-key caller's value list matches its grid: since lex-app-v2 the list
reuses the read filter's per-row answer, which carries no API-key scopes.
