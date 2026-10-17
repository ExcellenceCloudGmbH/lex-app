---
date: 2026-10-17
clusters: [4, 1, 10, 4]
tests_added: "12 new scenarios (1.398-1.401, 4.115-4.118, 10.117-10.121)"
suite_tally: "4t 3 pass, 1ax 4 pass (5 subtests), 10u 4 pass, 4u 1 pass; 4o, 4q, 1av, 10q unchanged and green; permissions and api_layer clusters 271 pass / 0 fail"
---

# Project fit: gaps found while moving Atrium onto the extension points

Sixth stacked PR (feat/ext-6-project-fit on feat/ext-5-realtime-keycloak). Moving Atrium onto lex's extension points showed six gaps; five are fixed here and one is documented.

- A closed generic API (GENERIC_API superuser/off) now answers 404 to anonymous callers too; the bundled frontend's session probe api/user/ keeps its own 401, so operators can sign in. Before, it answered 404 in superuser mode.
- A broken lex_config.py, or a hook path that does not import, stops startup instead of silently falling back to defaults.
- The docs page and lex schema use the same SCHEMA settings as the served schema; the enum-name cache follows them.
- LexAPIKeyAuthentication has an OpenAPI scheme at low priority.
- LexAPIKey is registered in the admin.
- Revoking EXECUTE on the bitemporal SECURITY DEFINER functions from PUBLIC is left to projects with a restricted runtime role: the pg_cron job may run as a non-superuser admin (Cloud SQL). The row-level-security docs say how.

Batches: [1ax](../../clusters/01-init/batches.md), [4t, 4u](../../clusters/04-permissions/batches.md), [10u](../../clusters/10-api_layer/batches.md).

Later on the same branch, found while Atrium moved onto it: drf-spectacular ~=0.30.0 (0.28 named colliding enums differently, so a project's committed contract changed), and lex's base views lend no docstring to project endpoints (GET_LIB_DOC_EXCLUDES, scenario 10.121). Atrium's suite on this head: 922 passed, 2 failed (both need Keycloak settings, failing the same way before the move); its OpenAPI contract is unchanged byte for byte.
