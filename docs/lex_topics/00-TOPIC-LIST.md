# Lex Topic Map for AI Assistants

Purpose: high-signal index extracted from `lex_context.md` and `lex_context_repo.md`, split into focused documents for targeted retrieval.

## Topics Covered

0. `00-GETTING-STARTED.md` — installation, project structure (ETL convention), running the dev server
1. `01-architecture-runtime.md` — framework overview, request/calculation flow, runtime stack
2. `02-project-structure-discovery.md` — project layout, naming rules, auto-discovery/exclusion
3. `03-lexmodel-core.md` — LexModel fields, hooks, validation rollback, permission method summaries
4. `04-calculationmodel-lifecycle.md` — status model, state machine, calculation hooks, sync/async, is_atomic
5. `05-calculatedmodelmixin-combinatorics.md` — defining fields, combinatorial expansion, duplicate handling, common patterns
6. `06-permissions-authorization.md` — UserContext, PermissionResult, permission methods, fallback chain, code examples
7. `07-streamlit-dashboards.md` — model-level Streamlit contracts, runtime, embedding points, auth token handoff
8. `08-serializers-and-api-layer.md` — custom serializers, @add_permission_checks, PATCH-safe validation, file organization
9. `09-fields-and-report-assets.md` — Lex custom fields (XLSX/PDF/HTML/Bokeh) and report output patterns
10. `10-process-admin-and-model-structure.md` — registration, model_structure.yaml (all three sections), YAML examples
11. `11-logging-and-lexlogger.md` — LexLogger builder API, context-aware logging, nested calculations
12. `12-celery-async-dispatch.md` — @lex_shared_task, Celery activation, dispatch strategy, RunInCelery/UnblockCelery, fallback
13. `13-transactions-and-deferred-recalculation.md` — `as_transaction`, deferred recalculation store
14. `14-signals-and-websocket-updates.md` — dependency cascade, calculation status broadcasts
15. `15-authentication-and-keycloak.md` — RBAC tiers, scopes, auth env configuration
16. `16-initial-data-upload.md` — JSON seed data, subprocess chaining, tag:/datetime: prefixes, auto-load
17. `17-cli-settings-imports-utils.md` — `lex` CLI commands, settings/env vars, import aliasing, utility decorators
18. `18-patterns-pitfalls-checklists.md` — practical recipes, anti-patterns, implementation checklists
19. `19-examples-and-endpoints.md` — consolidated code example map and API endpoint reference
20. `20-LEX-SPECIFICATIONS.md` — canonical project-specific Lex rules and scope constraints
21. `21-LEX-APP-CONTEXT.yaml` — baked Lex framework runtime context (CalculationModel/LexModel/logging)
22. `22-lifecycle-hooks.md` — @hook decorator, django-lifecycle, conditional hooks, pre/post validation
99. `99-QUERY-ROUTER.md` — keyword-to-topic routing map for fast retrieval

## Out of Scope

- History and bitemporal history are automatic Lex features — no user action required.
- Audit logs are automatic Lex features — no user action required.

## Recommended Retrieval Flow

- Start with `00-GETTING-STARTED.md` for project bootstrapping.
- Use `01-architecture-runtime.md` for orientation.
- Jump to one subsystem page by topic.
- Use `19-examples-and-endpoints.md` when you need concrete endpoint paths or full-pattern examples.

## LLM Usage Rule

- Prefer one topic file per question first; only expand to source docs if key details are missing.
- Reuse the `LLM Prompt Starters` block from each topic file to keep prompts deterministic and short.

## Source Scope

- Primary practical guide: `lex_context.md`
- Primary repo-level reference: `lex_context_repo.md`
