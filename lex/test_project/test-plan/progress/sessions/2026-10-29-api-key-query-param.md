---
date: 2026-10-29
clusters: [2]
tests_added: "5 new scenarios (2.117-2.121); 2j unit fakes gain an empty GET"
suite_tally: "crud_api + permissions + api_layer + exports + audit_logging: 514 pass / 2 skip / 1 xfail (test_4n_reflex_permissions not collected: reflex not installed locally)"
---

# Batch 2m — an API key in the `?api_key=` query parameter

See [2m](../../clusters/02-crud_api/batches.md). A caller that can only be configured with a URL
(the Postident DataConnect backend callback) cannot send an `Api-Key` header. `get_raw_api_key`
now reads `?api_key=` last, when no header carries a key, and the views use Lex's own `HasAPIKey`
(a subclass whose parser is `get_raw_api_key`) so the view accepts what the middleware lets through.
2.117 fails without the `HasAPIKey` swap.
