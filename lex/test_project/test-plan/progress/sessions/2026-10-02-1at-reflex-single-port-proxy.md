---
date: 2026-10-02
clusters: [1]
tests_added: 3
suite_tally: "init + permissions + api_layer: 812 pass / 0 fail / 15 skip (all environmental) — 1at 3 pass"
---

# Batch 1at — a Reflex development run behind one port

A project's Reflex pod crashed at start with "Address already in use": it ran `lex reflex run` in
development mode with both servers on the one port the pod exposes. The platform now gives the
backend that port and lets it pass every other request to Vite, through Reflex Enterprise's
single-port mode, and that mode turned out never to have worked on Reflex 0.9.12: the proxy names its
lifespan parameter `app`, which Reflex fills with the Reflex app, not the Starlette app it needs.
lex-app hands it the right one ([batch 1at](../../clusters/01-init/batches.md), 1.381–1.383).

What the work taught, beyond the scenarios themselves:

* **Reflex binds lifespan tasks by parameter name.** `app` is the Reflex app and `starlette_app` the
  Starlette app; any task written against an older Reflex, where the Starlette app came as `app`,
  silently gets the wrong object.
* **The repair sits where every rxconfig.py passes.** Reflex Enterprise imports its proxy when it
  builds the app, after the configuration is loaded, and both the rxconfig.py `lex reflex` writes and
  a hand-written one that keeps lex-app's sign-in call `lex_auth_plugin()`.
