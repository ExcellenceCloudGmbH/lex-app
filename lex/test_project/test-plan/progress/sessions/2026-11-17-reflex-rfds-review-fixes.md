---
date: 2026-11-17
clusters: [1]
tests_added: 0
suite_tally: "Reflex, embed and Streamlit-embed batches: 272 pass / 0 fail / 3 skip (environmental)"
---

# Review fixes across batches 1bt–1bw

Writing the user docs against the code found eight defects in the new Reflex surface; each is
fixed in the PR that introduced it, and each fix extends its batch's scenario
([batches](../../clusters/01-init/batches.md)) rather than adding scenarios:

* 1.701, 1.706 — RFDS's `requires()` refused on a page open past five minutes; a middleware now
  refreshes grants older than a minute before an event.
* 1.714–1.716 — `lex_table(page_size=)` opened at 50; differently filtered tables (and grids)
  shared one state and showed the same rows; an unreachable API read as a missing record.
* 1.719, 1.721–1.723 — a re-run's final calculation status never reached `on_status` (ids were
  de-duplicated for the frame's life); each widget frame drew its own log dialog; a filters dict
  naming a state var raised; Firefox (no `ancestorOrigins`) never found the framing origin.
* 1.725 — the object-page template suggested a relative import that breaks from a model in a
  subfolder.
