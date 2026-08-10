---
date: 2026-08-10
clusters: [10-api_layer]
tests_added: 27
suite_tally: "27 pass / 0 fail"
---

Coverage task for PR #703 (`fix/mcp-server-fastmcp4-port`).

Adds **[Batch 10o](../../clusters/10-api_layer/batches.md)** — unit tests
for `lex/mcp_server/tools/embed.py` (scenarios 10.72–10.80, type U).

The module is not yet importable through the normal package path (`mcp_server`
has no `__init__.py` files, and its `config`/`registry`/`context` siblings live
on an unmerged branch).  The test file loads `embed.py` by absolute path via
`importlib.util.spec_from_file_location` and injects lightweight module stubs
for all unavailable dependencies (`fastmcp`, `lex.mcp_server.config`, etc.)
before the module is executed.

Covered surfaces:

* `_classify_path` — all view-type patterns (list, create, detail/numeric id,
  detail/UUID, detail/show path, edit, custom/fallthrough).
* `_build_embed_url` — `embed=true` always present, `#embed` fragment always
  appended, each visibility toggle adds the correct param, redirect_after and
  extra_params are forwarded.
* `_resolve_frontend_url` — priority order: explicit setting → REACT_APP_URL →
  LEX_FRONTEND_URL → localhost fallback.
* `_csp_origins` — frontend origin always first; `EMBED_EXTRA_CSP_ORIGINS`
  appended without duplicating the base origin.
* `_embed_view_inner` — returns `[narration, JSON]` pair; narration must not
  contain the auth token; JSON `embed_url` must contain it; `view_type` and
  `resource` in the JSON match `_classify_path` output.
