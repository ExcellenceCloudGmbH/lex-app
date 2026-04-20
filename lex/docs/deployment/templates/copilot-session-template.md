# Copilot Session Template (Deployment)

## Session goal

- Convert approved run artifacts into deployable, publishable documentation and release packages.

## Mandatory behavior

- Respect step order in `docs/deployment/README.md`.
- Start deployment only after all planning/implementation steps (0–14) are fully complete.
- Retrieve deployment step instructions only via MCP using `get_deployment_step`.
- Execute one step per MCP request: request Step `N`, complete Step `N`, then request Step `N+1` in a new call.
- Write all generated artifacts only under `plans/<run-id>/deployment/`.
- Enforce explicit approvals at all gates.
