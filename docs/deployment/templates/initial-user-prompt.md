# Initial User Prompt (Deployment Phase)

Use this prompt to begin deployment work for a run:

0. Set `HANDBOOK_ROOT=./.venv/lib/python3.12/site-packages/lex/docs` as the canonical handbook location.
1. Assume only `${HANDBOOK_ROOT}` and the connected `lex-mcp` server are required runtime context for the LLM.
2. Start deployment only after implementation is fully complete and approved.
3. Create `plans/<run-id>/deployment/run.md`.
4. Retrieve deployment step instructions only via MCP using `get_deployment_step`.
5. Execute one deployment step at a time: one MCP request for one step, complete it, then request the next step.
6. Begin by requesting deployment Step 0.
7. Install `lex-app`, configure `.env`, run `lex Init`, start the server, and verify the deployment.
8. Stop at approval gates and require explicit `Approved: Yes/No`.
