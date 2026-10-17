# 2026-10-17 — ext-7: generic records for end users

Branch `feat/ext-7-generic-records` (stacked on `feat/nested-project-root`), for projects whose own clients use the generic API (Atrium, decision 094).

- [4v](../../clusters/04-permissions/batches.md) — `GENERIC_API = "records"`, `lex_api_exposed`, the socket gate, and the internal endpoints closed for non-superusers (`LEX_SEC_INTERNAL`).
- [10v](../../clusters/10-api_layer/batches.md) — generic views raise their errors to the handler (`LEX_SEC_ERRORS`).
- [6t](../../clusters/06-audit_logging/batches.md) — `AUDIT_PAYLOAD` and `AUDIT_ACTOR`.

Adjusted for the raised errors: 10k (destroy mixin, three direct-call tests), 10l (file download, two), 10m (the log tree is read by an operator).
