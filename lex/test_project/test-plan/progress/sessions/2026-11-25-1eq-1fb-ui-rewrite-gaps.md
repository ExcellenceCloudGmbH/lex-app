---
date: 2026-11-25
clusters: [1]
tests_added: 18
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dz 1ea-1ez 1fa-1fb 4n 10ag 10d): 358 pass / 0 fail, 483 subtests pass"
---

# lex.ui: gaps found by rewriting real dashboards (1eq-1fb)

Two bugs and ten generic gaps that rewrites of dashboards onto lex.ui hit, each a need of any lex-app
project. Design: section 5.15 of `docs/superpowers/specs/2026-11-23-reflex-ui-tables-design.md` (search
field, rows not stored, foreign-key names, `self.api.all` and model date filters) and section 6.22 of
`docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md` (unlisted areas, `lex reflex check`'s
imports, closed sections, `ui.scroll_to`, tabs by state, a choices field's Clear, the record page's
table limit, `ui.testing`). Batches [1eq-1fb](../../clusters/01-init/batches.md).

- 1eq: `self.api.list(search=)` searched `created_by` on every LexModel (bug).
- 1er: a table's shown rows were stored with the state in Redis, defeating `ui.private` (privacy bug).
- 1es-1fb: the features.

Earlier tests whose given changed: 1cx's 1.809 and 1eh's 1.998 (the model table's export/totals refusal
now names `self.api.all`); 1ea's 1.952 (a state value on a later tab is accepted; the first tab refuses
it); 1cs's 1.800 (the five-tables refusal is lex.ui's words, not RFDS's quoted sentence); 1cm's 1.783
(`scroll_to` is public). The calculation tests' `ViewsClient` is now `ui.testing.LexViews`.

Letters past `ez` continue at `fa`, `fb` (the plan's letters run a-z, then two letters).
