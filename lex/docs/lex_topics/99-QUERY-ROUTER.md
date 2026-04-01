# Lex Query Router

Use this file to route a user question to the most relevant focused Lex topic file.

## Keyword → File Map

- install, setup, project structure, ETL, getting started → `00-GETTING-STARTED.md`
- architecture, runtime, request flow, calculation flow → `01-architecture-runtime.md`
- project layout, discovery, naming, excluded files → `02-project-structure-discovery.md`
- LexModel, validation hooks, rollback, created_by, edited_by → `03-lexmodel-core.md`
- CalculationModel, is_calculated, calculate_hook, status transitions, state machine → `04-calculationmodel-lifecycle.md`
- defining_fields, combinatorial expansion, duplicates, get_selected_key_list → `05-calculatedmodelmixin-combinatorics.md`
- UserContext, PermissionResult, permission_read/edit, scopes, Keycloak permissions → `06-permissions-authorization.md`
- streamlit, dashboards, streamlit_main, streamlit_class_main, analytics tab, streamlit token → `07-streamlit-dashboards.md`
- serializer, add_permission_checks, CRUD endpoint wiring, PATCH validation → `08-serializers-and-api-layer.md`
- XLSXField, PDFField, HTMLField, report output file → `09-fields-and-report-assets.md`
- process admin, model_structure.yaml, ModelContainer, registration, model_styling → `10-process-admin-and-model-structure.md`
- LexLogger, add_text, add_heading, add_table, logging, model_logging_context → `11-logging-and-lexlogger.md`
- celery_active, async dispatch, RunInCelery, UnblockCelery, lex_shared_task → `12-celery-async-dispatch.md`
- as_transaction, deferred recalculation, recalculation store → `13-transactions-and-deferred-recalculation.md`
- signals, dependency cascade, websocket status update → `14-signals-and-websocket-updates.md`
- Keycloak, UMA, RBAC, scopes, auth env vars → `15-authentication-and-keycloak.md`
- initial data, seed data, JSON, test data, subprocess, tag, INITIAL_DATA → `16-initial-data-upload.md`
- lex CLI, settings, env vars, import aliasing, utilities → `17-cli-settings-imports-utils.md`
- patterns, pitfalls, gotchas, implementation checklist → `18-patterns-pitfalls-checklists.md`
- examples, API endpoints, model info endpoints → `19-examples-and-endpoints.md`
- Lex purpose, LexModel vs CalculationModel, CSV-only, project rules → `20-LEX-SPECIFICATIONS.md`
- @hook, lifecycle hooks, AFTER_CREATE, BEFORE_UPDATE, django-lifecycle, skip_hooks, pre_validation, post_validation → `22-lifecycle-hooks.md`

## Out of Scope Routing

- History, bitemporal history, and audit logs → automatic Lex features (no user action needed).
- Lex framework internals (`LexModel`, `CalculationModel` implementations) → out of scope for planning outputs; use/subclass only.

## Routing Rules

1. Start with one best-match file.
2. Only open a second file if the first lacks required detail.
3. Expand to `lex_context.md` or `lex_context_repo.md` only for missing specifics.
