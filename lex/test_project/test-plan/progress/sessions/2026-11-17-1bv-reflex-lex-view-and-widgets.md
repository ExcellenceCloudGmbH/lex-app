---
date: 2026-11-17
clusters: [1]
tests_added: 7
suite_tally: "embed, Streamlit and Reflex batches: 307 pass / 0 fail / 3 skip (environmental)"
---

# Batch 1bv — one embed contract, Streamlit and Reflex

lex-app's embed contract leaves the Streamlit package for framework-free `lex.lex_app.embed`, and
Reflex dashboards get `lex_view`, `lex_widgets` and `lex_calculation` over it
([batch 1bv](../../clusters/01-init/batches.md), 1.717–1.723). Every Streamlit test that touches
`lex_view`, the widgets or the embed module passed unchanged on the shared core (161 of them).

Two traps the batch records:

* **Model discovery imports lex's own packages a second time** under their app-relative name.
  `lex_app.embed.core.Flow` would be a second class beside `lex.lex_app.embed.core.Flow`;
  `embed` joins `reflex` in `_LEX_PACKAGE_EXCLUDED_DIRS` (mutation-checked: 1.717 fails without).
* **A Reflex component module must not defer annotations.** `rx.EventHandler[spec]` with `spec`
  a function local becomes a string Reflex resolves to nothing, and every handler of the
  component is dropped without an error.
