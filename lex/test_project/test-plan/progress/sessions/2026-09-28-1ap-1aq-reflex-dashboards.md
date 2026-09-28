---
date: 2026-09-28
clusters: [1, 4, 10]
tests_added: 33
suite_tally: "init + permissions + api_layer: 797 pass / 0 fail / 16 skip (all environmental) — 1ap 11, 1aq 16, 4n 5, 10d 1 pass"
---

# Batches 1ap, 1aq, 4n, 10d — Reflex dashboards beside Streamlit

Reflex joins Streamlit as a second way to write a project's dashboards, with the same
place in Lex App: a `lex reflex` command and run configurations
([batch 1ap](../../clusters/01-init/batches.md), 1.348–1.356), sign-in against the
project's Keycloak, the Django ORM from event handlers and the `?model=&pk=` dispatch
([batch 1aq](../../clusters/01-init/batches.md), 1.357–1.369), the user's Keycloak
permissions ([batch 4n](../../clusters/04-permissions/batches.md), 4.75–4.79), and a
global search that skips the new `Reflex` report
([batch 10d](../../clusters/10-api_layer/batches.md), 10.82). Built on Reflex
Enterprise, whose `AuthPlugin` does what the Streamlit proxy does, so there is no
proxy route.

What the tests taught, beyond the scenarios themselves:

* **1.361's first form was decoration.** It asserted that a connection inside an
  `atomic` block survived an event; a middleware that closed it anyway still passed,
  because Django's `close()` inside a transaction flags the connection instead of
  dropping it. The scenario now pins `closed_in_transaction`, `needs_rollback` and a
  query in the same transaction — and fails against that mutation.
* **Discovery imported the Reflex package twice.** Django's model discovery walks
  lex's own package and imports each module under its app-relative name
  (`lex_app.reflex.auth`), a second module object beside `lex.lex_app.reflex.auth`
  — which for Reflex defines every state twice. Discovery now skips that directory
  inside lex's packages only; 1.367 pins both halves.
* **granian runs uvloop, which `nest_asyncio` refuses**, and `LexAppConfig.ready()`
  applied it unconditionally — Django setup failed in the Reflex worker. 1.368
  (in a subprocess, since `nest_asyncio` patches asyncio process-wide).
* **Reflex allows one `AuthPlugin` per process**, so 1.366 compiles the real app once
  in `setUpClass` and reads every assertion from that compile.
* **`@orm` was shadowed by its own module.** The decorator lived in
  `lex/lex_app/reflex/orm.py`, and importing a submodule sets the package attribute of
  the same name — so once anything imported the submodule (the app always does),
  `from lex.lex_app.reflex import orm` returned the module and `@orm` failed. Found by
  1.357c's new check that every lazy export resolves to its own object; the module is
  now `django_orm.py`, and 1.360 imports `orm` by its public path.
* **No `offline_access`.** Reflex's documentation asks for it to get a refresh token;
  Keycloak issues one without it (the Streamlit proxy has always renewed with that),
  and with it the refresh token becomes an offline token that outlives the Keycloak
  session — signing out of Lex App would not end the dashboard's session, and a
  realm without the `offline_access` role refuses the sign-in. 1.357 and 1.366 pin
  its absence.

1m's and 1y's exhaustive command/configuration lists gained `reflex`/`Reflex`; no new
scenarios there. The browser flow (Keycloak redirect and callback, dashboards signed in,
embed mode, sign-out, and a cross-site frame signing in through the popup) was verified
outside the suite against a mock OIDC provider, with the documented examples as the
project's dashboards.
