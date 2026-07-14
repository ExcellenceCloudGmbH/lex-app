---
date: 2026-07-14
clusters: [12i]
tests_added: "4 (12.42–12.45) + source in 1 file (base_serializers)"
suite_tally: "12i 4 pass / 0 fail; regression: serializers+crud_api+queries+exports+audit_logging+permissions+journeys+api_layer+history = 754 pass / 2 xfail / 3 skip / 0 fail"
---

**Batch 12i landed — foreign-key display names in the read contract (backend
root cause of frontend BUG-F-003: FK columns render as bare ids like `79`).**
A foreign key serializes as its raw pk (DRF's default for `fields="__all__"`),
which the customer sees as a meaningless number where they expect a name. Fix:
every serialized row now carries an **additive** companion key
`<fk>__short_description` = `str(related)` — the model author's
`__str__`/`short_description`, i.e. the documented customization point — next to
the **untouched** raw id, so anything that filters or edits on the id keeps
working.

Two paths, one contract:
- **List** — `FilteredListSerializer._batch_add_fk_display_names` resolves the
  whole page's names in **one `pk__in` query per FK field** (mirrors
  `ModelExport._apply_foreign_key_display_names`), suppressing the per-instance
  path so it stays N-safe.
- **Detail** — `LexSerializer._add_fk_display_names_single` resolves per
  instance (one query, fine for a single object), so **list-row shape ⊆ detail
  shape** (the 12c invariant holds — no contract drift).

Null FK → null companion (row shape stays stable row-to-row). The companion is
emitted only when the raw FK column survived visibility filtering, so a
permission-hidden FK leaks no name. Best-effort throughout: any resolution
failure leaves the raw ids in place rather than breaking the response.

12.42 list row carries the companion = `str(related)`; 12.43 the label honors a
custom `__str__` (not the id/field name); 12.44 raw id preserved **and** detail
carries the same companion; 12.45 null FK → null companion + query count does
not scale with row count (3-row vs 9-row page, equal FK-target query count).

Frontend twin: the grid and detail views render `<fk>__short_description`
instead of the bare id (F3 / F9.6). See
[batch 12i](../../clusters/12-serializers/batches.md).
