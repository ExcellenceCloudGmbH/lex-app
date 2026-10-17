# 2026-10-17 — ext-8: the database decides, and the request scope

Branch `feat/ext-8-database-decides` (stacked on `feat/ext-7-generic-records`).

- [4w](../../clusters/04-permissions/batches.md) — `lex_permission_source = "database"` / `PERMISSION_SOURCE`, the first-request guard, policy refusals as 404.
- [10w](../../clusters/10-api_layer/batches.md) — `REQUEST_SCOPE`: the acting scope on every generic endpoint.
