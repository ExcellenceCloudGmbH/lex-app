# 2026-10-17 — ext-11: follow-ups from moving a project onto the generic API

Branch `feat/ext-11-generic-openapi`. Found while Atrium moved every endpoint onto the generic records (its decision 094).

- [10z](../../clusters/10-api_layer/batches.md) — 10.149: `@lex_action(many=True)` documents an action that answers with a list.
- [10x](../../clusters/10-api_layer/batches.md) — 10.150: `lex_actions` names the actions a model offers; a projection no longer exposes every action of the model it projects.
- [4v](../../clusters/04-permissions/batches.md) — 4.136: an unknown path below `api/` is 404 in strict mode instead of the app's index.html with 200.
