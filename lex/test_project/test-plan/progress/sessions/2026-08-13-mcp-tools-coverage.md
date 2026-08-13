---
date: 2026-08-13
clusters: [16-mcp-tools]
tests_added: 65
suite_tally: 65 pass / 0 fail / 0 skip
---

## Coverage for PR #703 — MCP server tools

Adds cluster 16 (`mcp_tools`) with four sub-cluster batches (16a–16d) covering
the four framework source files PR #703 introduced without paired tests.

All tests are pure-unit (no Django test runner required). `embed.py` is loaded
via `importlib.util.spec_from_file_location` with in-process stubs for the
unavailable `mcp.*` and `lex.mcp_server.*` packages, keeping the cluster
portable across environments that don't ship those optional dependencies.

See [batches.md](../../../test-plan/clusters/16-mcp-tools/batches.md) for the
full scenario breakdown.

Fixes #720.
