---
date: 2026-09-02
clusters: [16]
tests_added: 61
suite_tally: "ai_tooling: 61 pass / 0 skip / 0 xfail"
---

# Coverage task: cluster 16 (AI Tooling) for issue #734 / parent PR #733

Coverage-gate auto-opened issue (#734, closes-linked from parent PR #733)
flagged 4 framework source files that shipped without paired tests:
`lex/mcp_server/tools/embed.py`, `lex/tools/setup_with_ai.py`,
`lex/tools/mcp_mode_invoke.py`, `lex/tools/verify_ai_assets.py`. All four
predate this session's diff — they landed via the `feat/notes-richer-context`
merge chain — and none is owned by an existing cluster, so this session opens
a new cluster (**16 — AI Tooling**, next free number after
`15-calculation_logging`) rather than extending one.

Batch write-up, scenario table, and file list live in
[`clusters/16-ai_tooling/batches.md`](../../clusters/16-ai_tooling/batches.md)
and [`clusters/16-ai_tooling/cluster.md`](../../clusters/16-ai_tooling/cluster.md) —
not restated here.

**Environment note:** `lex/mcp_server/` in this checkout has only
`tools/embed.py` — its sibling modules (`config.py`, `registry.py`,
`context.py`) live on an unmerged branch (see AGENTS.md and
`lex/tests/unit/infra/test_mcp_server_sdk_compat.py`). Batch 16a's stub
helper (`lex/test_project/tests/ai_tooling/_mcp_server_stubs.py`) installs
minimal fakes for exactly those three imports so `embed.py`'s own source
runs unmodified. Similarly, `lex_mcp` (the separate `lex-mcp-local` package
that `mcp_mode_invoke.py` / `verify_ai_assets.py` shim over) is not installed
in this environment, so batch 16d exercises the "package absent"
`ImportError` path for real and installs a minimal fake `lex_mcp` only for
the "package present" delegation path.

`fastmcp`, `mcp`, `pytest`, and `pytest-django` were installed and the
package was `pip install -e .`'d to make `python -m lex pytest` runnable
here; a local PostgreSQL role/database (`django` / `db_lex-app`) was created
to satisfy the default `DATABASES["default"]` target. No repository files
were changed to accomplish this — sandbox-local setup only.

No framework bugs were surfaced by this batch — all 61 tests assert the
current, documented behaviour without an `expectedFailure`/`xfail`.
