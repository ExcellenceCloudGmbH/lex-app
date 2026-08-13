## 16. MCP Server Tools (`mcp_tools`)

**What it tests:** the four framework source files added by PR #703 that
expose the MCP server's embed-view tool, asset verification, mode-switch
invocation helper, and `.env` file management.

**Why it matters:** these utilities are the bridge between the lex-app
Django side and the ``lex-mcp-local`` runtime.  Bugs here silently leave
the IDE pointing at the wrong mode, break the React embed URL construction
(wrong iframe origin, missing ``#embed`` fragment, mangled CSP headers),
or corrupt the project ``.env`` in-place — all very hard to diagnose in
production.

**Surfaces covered:**

- ``lex/mcp_server/tools/embed.py`` — URL classification, title generation,
  frontend URL resolution, CSP origin building, full embed-URL construction.
- ``lex/tools/verify_ai_assets.py`` — env-file value reader, six-level mode
  resolution priority chain, per-directory verification / restoration logic.
- ``lex/tools/mcp_mode_invoke.py`` — mode normalisation, result dataclass ok
  property, fallback-strategy invocation when ``lex_mcp`` is absent.
- ``lex/tools/setup_with_ai.py`` — atomic env-file write / update / append /
  legacy-key removal.

**Design principle:** pure-unit tests with no Django requirement.  ``embed.py``
is loaded via ``importlib.util.spec_from_file_location`` with stub modules for
the unavailable ``mcp.*`` and ``lex.mcp_server.*`` packages so the tests run
in any Python environment.

### 16a. embed-view URL building and path classification

### 16b. MCP mode resolution and asset verification

### 16c. MCP mode-switch invocation from outside the server

### 16d. .env file management
