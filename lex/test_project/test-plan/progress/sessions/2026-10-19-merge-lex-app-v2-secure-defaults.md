---
date: 2026-10-19
clusters: [1, 4, 10, 13, 14]
tests_added: "3 new scenarios (14.84-14.86); this branch's batches renumbered past lex-app-v2's"
suite_tally: "14l 3 pass; 14.64 passes again"
---

# Merging lex-app-v2 into the value-list filters — #833's secure defaults

lex-app-v2 took batch letters and scenario numbers this branch held (1aw, 4o, 10o, 14f), so this
branch's batches moved past them: 1ax and [1ay](../../clusters/01-init/batches.md),
[4q](../../clusters/04-permissions/batches.md), [10p](../../clusters/10-api_layer/batches.md), and
[14k, 14g and 14j](../../clusters/14-queries/batches.md).

[14l](../../clusters/14-queries/batches.md) pins the value lists and the list's GET lookups to
lex-app-v2's filter allow-list, and a fix the merge needed: the merged serializer's read-once check
assumed every model has `can_read`, so a plain Django model's list answered 500.
