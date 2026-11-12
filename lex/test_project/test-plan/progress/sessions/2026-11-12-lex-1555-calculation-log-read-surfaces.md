---
date: 2026-11-12
clusters: [15]
tests_added: "5 new scenarios (15.56-15.60), batch 15m"
suite_tally: "calculation_logging 15m 5 pass; on untouched lex-app-v2 15.56-15.58 fail on the 500 and 15.60 fails with error_traceback None (the 6u half), 15.59 passes on both; serializers, permissions, audit_logging and calculation_logging fail the same 24 tests with and without the change (cluster 15a-15j, pre-existing)"
---

# Batch 15m: the calculation-log table is readable again (LEX-1555)

[Batch 15m](../../clusters/15-calculation_logging/batches.md). The same customer report as
[6u](../../clusters/06-audit_logging/batches.md) carried a second symptom: opening a failed
run's log and switching to the **Details** tab threw the reader out of the drawer and onto the
CalculationLog table page, where every cell read `ERR`.

Both halves are one 500. `LexSerializer.to_representation` asks
`type(instance).can_read` before it asks whether the model declares one, and `CalculationLog`
is a plain `django.db.models.Model` — the only model served through `model_entries` that is not
a `LexModel`. Every read of the table therefore raised `AttributeError`. The two branches
underneath guard themselves with `has_can_read` / `has_permission_read`; this one, added with
the 2.4 read-surface hardening, ran first and raised before they could.

The frontend read that 500 twice. react-admin's `<Show>`, which the Details tab mounts on
`calculationlog`, answers a failed `getOne` by notifying and `redirect('list', resource)` —
that is the "it sends me to the table" half. AG Grid's server-side row model renders a block it
could not load as `ERR` in every cell — that is the other. `many` fails too, which is how the
log tree fetches each node's own text, so a run's log rendered as a tree of titled, empty
sections.

15.59 is the guard against fixing this by deleting the branch: BUG-011 put it there so the
serializer would not re-ask `permission_read` once per row after the list filter had already
asked, and a model that declares `can_read` still serves the remembered decision.

15.60 pins the drawer's own read, which 6u does not cover: the newest audit entry whose
*terminal* status is the one the row ended with, under the `<model>_<pk>_` prefix, carrying
`error_traceback` for a plain user. Without it the drawer says "No failure details are available
for this run" over a run that recorded one — on untouched `lex-app-v2` it fails with
`None != 'Traceback (most recent call last): …'`, which is the reported symptom exactly.
