---
date: 2026-11-17
clusters: [1]
tests_added: 12
suite_tally: "Reflex batches 1ap–1at, 1bg, 4n, 1bt: 86 pass / 0 fail / 3 skip (environmental)"
---

# Batch 1bt — RFDS in the Reflex dashboards

RFDS becomes the component and design layer of the Reflex dashboards, with lex-app keeping the
runtime ([batch 1bt](../../clusters/01-init/batches.md), 1.695–1.706): RFDS's plugins in
`lex_config()`, RFDS installed by `create_app()` on Keycloak's identity and grants, security
headers that let only lex-app frame a dashboard, `@lex_page` in lex-app's chrome, and a browser
bridge for lex-app's colour mode and sign-in notices.

What the work taught, beyond the scenarios:

* **One compile per process.** Reflex Enterprise refuses a second `AuthPlugin` once its
  protection sweep has run, so 1aq and 1bt now read the same compile
  (`tests/init/_reflex_compiled.py`) instead of each compiling. 1.366's expected load events
  gained `LexAccessState.load` after the sign-in guard.
* **An app stylesheet must be a file under `assets/`.** `rx.asset`'s URL carries a `?v=`
  cache-buster the compiler cannot resolve, and the asset is linked into the directory the first
  call ran in. lex-app's few rules over RFDS are inlined into the head instead.
* **A hot reload moved pages in the navigation.** Re-registering a route appended it at the end
  of its section; it now keeps its place (1.703).
