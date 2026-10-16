---
date: 2026-10-16
clusters: [1, 4, 10, 10, 10]
tests_added: "35 new scenarios (1.386-1.395, 4.80-4.85, 10.83-10.101)"
suite_tally: "1av 10 pass, 4o 6 pass, 10o 9 pass, 10p 6 pass, 10q 4 pass (35 pass / 0 fail); full cluster suite (not stress, Reflex modules excluded: no reflex package locally) 1676 pass / 34 fail / 24 skip / 6 xfail - 33 of the failures fail identically on lex-app-v2 before this change (25 calculation-log rows, 7 Streamlit/asset environment checks, the gate self-test); the 34th, test_01_303 (GZip must sit at index <= 1), was fixed by moving the new middleware below GZip and passes"
---

# Project API extension point, secure error bodies, OpenAPI; Process removed

First of five stacked PRs (feat/ext-1-foundation). A project can serve its own REST endpoints through lex instead of replacing ROOT_URLCONF, MIDDLEWARE and REST_FRAMEWORK: lex_config extension keys read lazily (lex/core/extensions.py), the project urlconf mounted ahead of lex's routes, LexAPIView/LexWebhookView, middleware slots, a delegating DRF authentication class, the GENERIC_API gate, drf-spectacular schema and `lex schema`. Error responses carry no traceback unless DEBUG or LEX_API_TRACEBACKS; OIDC next/fail must stay on a trusted origin. The Process model type and api/process/<name> are removed.

Batches: [1av](../../clusters/01-init/batches.md), [4o](../../clusters/04-permissions/batches.md), [10o, 10p, 10q](../../clusters/10-api_layer/batches.md). Cluster 10 continues from 10.83 because 10.72-10.82 were taken by 10i and 10d.
