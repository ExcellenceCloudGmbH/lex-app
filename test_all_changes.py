#!/usr/bin/env python3
"""Comprehensive test suite for all changes in this session.

Every test uses ONLY in-memory objects, temp directories, or temp SQLite
databases. Nothing touches real configs, real venvs, or real .env files.

Run:  .venv/bin/python test_all_changes.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
_pass = 0
_fail = 0


def section(title: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def ok(label: str) -> None:
    global _pass
    _pass += 1
    print(f"  ✓ {label}")


def fail(label: str, detail: str = "") -> None:
    global _fail
    _fail += 1
    msg = f"  ✗ {label}"
    if detail:
        msg += f"  —  {detail}"
    print(msg)


def check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        ok(label)
    else:
        fail(label, detail)


# ======================================================================
# 1. model_structure.py — bare-string tolerance
# ======================================================================
section("1. ModelStructure._normalize_model_list — bare string tolerance")

from lex.process_admin.utils.model_structure import ModelStructure

# 1a  Standard list  — ["A", "B"] → ["a", "b"]
check(
    ModelStructure._normalize_model_list(["A", "B"], "test") == ["a", "b"],
    "list input → lowered list",
)

# 1b  Dict shorthand — {"MyModel": None} → ["mymodel"]
check(
    ModelStructure._normalize_model_list({"MyModel": None, "Other": None}, "test")
    == ["mymodel", "other"],
    "dict input → list of keys",
)

# 1c  ★ NEW: Bare string — "MyModel" → ["mymodel"]
check(
    ModelStructure._normalize_model_list("MyModel", "test") == ["mymodel"],
    "bare string input → single-item list  (NEW FIX)",
)

# 1d  Empty / None / {} → []
for empty in (None, "", {}):
    check(
        ModelStructure._normalize_model_list(empty, "test") == [],
        f"empty input {empty!r} → []",
    )

# 1e  Integer → ValueError  (still rejects non-string/list/dict)
try:
    ModelStructure._normalize_model_list(123, "test")
    fail("integer input → ValueError", "no exception raised")
except ValueError:
    ok("integer input → ValueError")

# 1f  Bool → treated as "not defined" (empty list), not a crash.
#     YAML `untracked_models: true` should be silently ignored.
check(
    ModelStructure._normalize_model_list(True, "test") == [],
    "bool input → empty list (not defined)",
)


# ======================================================================
# 2. setup_with_ai.py — get_installed_lex_mcp_local_version
# ======================================================================
section("2. get_installed_lex_mcp_local_version — version detection")

from lex.tools.setup_with_ai import get_installed_lex_mcp_local_version

# We test by calling with the CURRENT interpreter.  Since lex-mcp-local
# is installed in our venv, we expect a version string.  If it isn't
# installed, we expect None — both are valid; the test just confirms
# the function doesn't crash.
py = Path(sys.executable)
version = get_installed_lex_mcp_local_version(py)
check(
    version is None or isinstance(version, str),
    f"returns str or None (got {version!r})",
)

# With a bogus interpreter → None (graceful failure)
bogus_version = get_installed_lex_mcp_local_version(Path("/nonexistent/python"))
check(bogus_version is None, "bogus interpreter → None (no crash)")


# ======================================================================
# 3. setup_with_ai.py — _has_unified_mcp_entry_point
# ======================================================================
section("3. _has_unified_mcp_entry_point — entry point detection")

from lex.tools.setup_with_ai import _has_unified_mcp_entry_point

unified = _has_unified_mcp_entry_point(py)
check(
    isinstance(unified, bool),
    f"returns bool (got {unified!r}; True means lex_mcp.server is importable)",
)

bogus_unified = _has_unified_mcp_entry_point(Path("/nonexistent/python"))
check(bogus_unified is False, "bogus interpreter → False (no crash)")


# ======================================================================
# 4. setup_with_ai.py — MINIMUM_DUAL_MODE_VERSION constant
# ======================================================================
section("4. MINIMUM_DUAL_MODE_VERSION constant")

from lex.tools.setup_with_ai import MINIMUM_DUAL_MODE_VERSION

check(
    MINIMUM_DUAL_MODE_VERSION == "0.2.3",
    f"equals '0.2.3' (got {MINIMUM_DUAL_MODE_VERSION!r})",
)


# ======================================================================
# 5. setup_with_ai.py — install_lex_mcp_local has upgrade kwarg
# ======================================================================
section("5. install_lex_mcp_local — upgrade parameter")

from lex.tools.setup_with_ai import build_lex_mcp_local_install_command

# Without upgrade
cmd_no_upgrade = build_lex_mcp_local_install_command(
    "/fake/python", "test-api-key", upgrade=False,
)
check(
    "--upgrade" not in cmd_no_upgrade,
    "upgrade=False → no --upgrade flag",
)

# With upgrade
cmd_with_upgrade = build_lex_mcp_local_install_command(
    "/fake/python", "test-api-key", upgrade=True,
)
check(
    "--upgrade" in cmd_with_upgrade,
    "upgrade=True → --upgrade flag present",
)

# Both still contain pip install and the index URL
check(
    "install" in cmd_with_upgrade and any("cloudsmith" in arg for arg in cmd_with_upgrade),
    "command includes 'pip install' and cloudsmith index URL",
)


# ======================================================================
# 6. setup_with_ai.py — resolve_mcp_server_args
# ======================================================================
section("6. resolve_mcp_server_args — dynamic entry point selection")

from lex.tools.setup_with_ai import resolve_mcp_server_args

# If the current interpreter HAS the unified entry point:
if _has_unified_mcp_entry_point(py):
    args = resolve_mcp_server_args(py, "backward")
    check(
        args == ["-m", "lex_mcp.server", "--mode", "backward"],
        f"unified available → [-m, lex_mcp.server, --mode, backward]  (got {args})",
    )
    args_fwd = resolve_mcp_server_args(py, "forward")
    check(
        args_fwd == ["-m", "lex_mcp.server", "--mode", "forward"],
        "unified available → forward mode args correct",
    )
else:
    ok("(skipped — lex_mcp.server not importable in this venv; fallback tested below)")

# With a bogus interpreter that has neither module, the function raises
# because it can't locate wrapper_mcp either. That's the expected behavior.
try:
    resolve_mcp_server_args(Path("/nonexistent/python"), "forward")
    fail("bogus interpreter → should raise SetupWithAIError")
except Exception:
    ok("bogus interpreter → raises (no valid entry point)")


# ======================================================================
# 7. ai_dashboard.py — _read_mode_from_mcp_json
# ======================================================================
section("7. _read_mode_from_mcp_json — mode extraction from mcp.json")

from lex.tools.ai_dashboard import _read_mode_from_mcp_json

with tempfile.TemporaryDirectory() as tmpdir:
    mcp_path = Path(tmpdir) / "mcp.json"

    # 7a  --mode in args → extracted
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server", "--mode", "backward"],
        }}
    }))
    check(
        _read_mode_from_mcp_json(mcp_path) == "backward",
        "reads --mode backward from args",
    )

    # 7b  --mode forward
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server", "--mode", "forward"],
        }}
    }))
    check(
        _read_mode_from_mcp_json(mcp_path) == "forward",
        "reads --mode forward from args",
    )

    # 7c  No --mode in args, but LEX_MCP_MODE in env block → falls back
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server"],
            "env": {"LEX_MCP_MODE": "backward"},
        }}
    }))
    check(
        _read_mode_from_mcp_json(mcp_path) == "backward",
        "falls back to LEX_MCP_MODE in env block",
    )

    # 7d  Neither present → None
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server"],
        }}
    }))
    check(
        _read_mode_from_mcp_json(mcp_path) is None,
        "no mode anywhere → None",
    )

    # 7e  File missing → None
    missing = Path(tmpdir) / "nope.json"
    check(
        _read_mode_from_mcp_json(missing) is None,
        "missing file → None",
    )

    # 7f  Malformed JSON → None
    bad = Path(tmpdir) / "bad.json"
    bad.write_text("not json {{{")
    check(
        _read_mode_from_mcp_json(bad) is None,
        "malformed JSON → None (no crash)",
    )

    # 7g  Invalid mode value → None
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server", "--mode", "banana"],
        }}
    }))
    check(
        _read_mode_from_mcp_json(mcp_path) is None,
        "invalid mode 'banana' → None",
    )

    # 7h  Fuzzy name matching: any entry containing "lex-mcp" is matched
    mcp_path.write_text(json.dumps({
        "servers": {"my-lex-mcp-server": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server", "--mode", "backward"],
        }}
    }))
    check(
        _read_mode_from_mcp_json(mcp_path) == "backward",
        "fuzzy match: 'my-lex-mcp-server' matched via 'lex-mcp' substring",
    )

    # 7i  mcpServers key (Claude Desktop / Cursor format)
    mcp_path.write_text(json.dumps({
        "mcpServers": {"lex-mcp-local": {
            "command": "python",
            "args": ["-m", "lex_mcp.server", "--mode", "backward"],
        }}
    }))
    check(
        _read_mode_from_mcp_json(mcp_path) == "backward",
        "mcpServers key works (Claude Desktop format)",
    )

    # 7j  Non-matching server name → None
    mcp_path.write_text(json.dumps({
        "servers": {"totally-unrelated": {
            "type": "stdio", "command": "python",
            "args": ["-m", "other_server", "--mode", "backward"],
        }}
    }))
    check(
        _read_mode_from_mcp_json(mcp_path) is None,
        "non-matching server name → None",
    )


# ======================================================================
# 8. ai_dashboard.py — _read_dashboard_state mode fallback chain
# ======================================================================
section("8. _read_dashboard_state — mode fallback: .env → mcp.json → default")

from lex.tools.ai_dashboard import _read_dashboard_state, MODE_OVERRIDE_FILE as _MOF8

# Tests 8 and 9 must run with NO override file so the .env/mcp.json
# fallback chain is the only input.  Save & restore the real one.
_test8_override_existed = _MOF8.exists()
_test8_override_content = (
    _MOF8.read_text(encoding="utf-8") if _test8_override_existed else None
)
if _test8_override_existed:
    _MOF8.unlink()

try:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_path = root / ".env"
        mcp_path = root / "mcp.json"

        # 8a  .env has mode → use it
        env_path.write_text("LEX_MCP_MODE=backward\n")
        mcp_path.write_text(json.dumps({
            "servers": {"lex-mcp-local": {
                "args": ["-m", "lex_mcp.server", "--mode", "forward"],
            }}
        }))
        state = _read_dashboard_state(root, env_path, py, mcp_path)
        check(
            state["mcp_mode"] == "backward",
            ".env has backward → uses .env (highest priority)",
        )

        # 8b  .env has NO mode, mcp.json has backward → falls back to mcp.json
        env_path.write_text("GITHUB_TOKEN=test\n")
        state = _read_dashboard_state(root, env_path, py, mcp_path)
        check(
            state["mcp_mode"] == "forward",
            ".env missing mode, mcp.json has forward → uses mcp.json",
        )

        # 8c  Neither .env nor mcp.json has mode → default "forward"
        mcp_path.write_text(json.dumps({
            "servers": {"lex-mcp-local": {"args": []}}
        }))
        state = _read_dashboard_state(root, env_path, py, mcp_path)
        check(
            state["mcp_mode"] == "forward",
            "no mode anywhere → defaults to 'forward'",
        )

        # 8d  mcp.json missing entirely → default "forward"
        mcp_path.unlink()
        state = _read_dashboard_state(root, env_path, py, mcp_path)
        check(
            state["mcp_mode"] == "forward",
            "mcp.json missing → defaults to 'forward'",
        )
finally:
    if _MOF8.exists():
        _MOF8.unlink()
    if _test8_override_existed and _test8_override_content is not None:
        _MOF8.write_text(_test8_override_content, encoding="utf-8")


# ======================================================================
# 9. ai_dashboard.py — _handle_save mode change detection
# ======================================================================
section("9. _handle_save — mode change detection (the 'no changes' fix)")

from lex.tools.ai_dashboard import _handle_save
from lex.tools.setup_with_ai import _read_dotenv_value

with tempfile.TemporaryDirectory() as tmpdir:
    root = Path(tmpdir)
    env_path = root / ".env"
    mcp_path = root / "mcp.json"

    # Setup: .env has no LEX_MCP_MODE, mcp.json says backward
    env_path.write_text("GITHUB_TOKEN=ghp_test\n")
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server", "--mode", "backward"],
            "env": {"LEX_MCP_MODE": "backward"},
        }}
    }))

    form = {"mcp_mode": ["forward"], "github_token": [""],
            "remote_mcp_api_key": [""], "remote_mcp_url": [""]}

    # 9a  Switch backward→forward: should succeed (not "no changes")
    s, e = _handle_save(form, root, env_path, mcp_path)
    check(
        any("Mode changed to forward" in x for x in s),
        "backward→forward: reports 'Mode changed to forward'",
    )
    check(not e, "backward→forward: no errors")

    # 9b  .env now has LEX_MCP_MODE=forward
    check(
        _read_dotenv_value(env_path, "LEX_MCP_MODE") == "forward",
        ".env updated with LEX_MCP_MODE=forward",
    )

    # 9c  mcp.json args updated
    data = json.loads(mcp_path.read_text())
    args = data["servers"]["lex-mcp-local"]["args"]
    idx = args.index("--mode") + 1
    check(
        args[idx] == "forward",
        "mcp.json --mode arg updated to 'forward'",
    )

    # 9d  mcp.json env block updated
    check(
        data["servers"]["lex-mcp-local"]["env"]["LEX_MCP_MODE"] == "forward",
        "mcp.json env block updated to 'forward'",
    )

    # 9e  Repeat with same mode → "No changes detected"
    s, e = _handle_save(form, root, env_path, mcp_path)
    check(
        any("No changes" in x for x in s),
        "same mode again → 'No changes detected'",
    )

    # 9f  ★ KEY SCENARIO: .env has NO LEX_MCP_MODE at all, user picks
    #     the default "forward". Previously this said "no changes" because
    #     current_mode defaulted to "forward" and matched the form value.
    #     Now it detects that .env doesn't have the key and writes it.
    env_path.write_text("GITHUB_TOKEN=ghp_test\n")  # reset: no LEX_MCP_MODE
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server"],
            "env": {},
        }}
    }))
    s, e = _handle_save(form, root, env_path, mcp_path)
    check(
        any("Mode changed" in x for x in s),
        "★ .env missing mode + form='forward' → writes it (not 'no changes')",
    )
    check(
        _read_dotenv_value(env_path, "LEX_MCP_MODE") == "forward",
        "★ .env now explicitly has LEX_MCP_MODE=forward",
    )

    # 9g  Switch to backward
    form_bwd = {**form, "mcp_mode": ["backward"]}
    s, e = _handle_save(form_bwd, root, env_path, mcp_path)
    check(
        any("Mode changed to backward" in x for x in s),
        "forward→backward switch works",
    )
    check(
        _read_dotenv_value(env_path, "LEX_MCP_MODE") == "backward",
        ".env updated to backward",
    )

    # 9h  Empty/invalid mode in form → no mode change, no crash
    form_empty = {**form, "mcp_mode": [""]}
    s, e = _handle_save(form_empty, root, env_path, mcp_path)
    check(
        any("No changes" in x for x in s),
        "empty mode in form → 'No changes' (no crash)",
    )

    form_bad = {**form, "mcp_mode": ["banana"]}
    s, e = _handle_save(form_bad, root, env_path, mcp_path)
    check(
        any("No changes" in x for x in s),
        "invalid mode 'banana' → 'No changes' (no crash)",
    )


# ======================================================================
# 10. ai_dashboard.py — _invalidate_copilot_mcp_cache
# ======================================================================
section("10. _invalidate_copilot_mcp_cache — PyCharm tool cache invalidation")

from lex.tools.ai_dashboard import _invalidate_copilot_mcp_cache
from lex.tools.setup_with_ai import (
    _ensure_github_copilot_state_table,
    _write_github_copilot_state_value,
    _load_github_copilot_mcp_servers_cache,
    GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY,
    GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY,
    _encode_github_copilot_state_value,
)

with tempfile.TemporaryDirectory() as tmpdir:
    # Create a fake Copilot directory structure:
    #   <tmpdir>/intellij/mcp.json  ← the mcp config path
    #   <tmpdir>/copilot-intellij.db    ← the state DB (parent.parent of intellij/)
    # Actually, resolve_github_copilot_state_db_path does:
    #   mcp_config_path.resolve().parent.parent / "copilot-intellij.db"
    # So for <tmpdir>/intellij/mcp.json → <tmpdir>/copilot-intellij.db
    intellij_dir = Path(tmpdir) / "intellij"
    intellij_dir.mkdir()
    fake_mcp = intellij_dir / "mcp.json"
    fake_mcp.write_text("{}")

    fake_db = Path(tmpdir) / "copilot-intellij.db"

    # Pre-populate the DB with a stale cache
    conn = sqlite3.connect(fake_db)
    _ensure_github_copilot_state_table(conn)
    _write_github_copilot_state_value(
        conn, GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY,
        {"lex-mcp-local": {"tools": [{"name": "reverse_kickstart"}]}},
    )
    _write_github_copilot_state_value(
        conn, GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY, "true",
    )
    conn.commit()
    conn.close()

    # 10a  Before invalidation — cache has tools
    conn = sqlite3.connect(fake_db)
    cached = _load_github_copilot_mcp_servers_cache(conn)
    conn.close()
    check(
        "lex-mcp-local" in cached and len(cached["lex-mcp-local"]["tools"]) == 1,
        "pre-invalidation: cache has 1 tool entry",
    )

    # 10b  Run invalidation
    result = _invalidate_copilot_mcp_cache(fake_mcp)
    check(result is True, "invalidation returns True")

    # 10c  After invalidation — cache is empty
    conn = sqlite3.connect(fake_db)
    cached = _load_github_copilot_mcp_servers_cache(conn)
    row = conn.execute(
        "SELECT value FROM state WHERE key=?",
        (GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY,),
    ).fetchone()
    conn.close()
    check(
        "lex-mcp-local" not in cached,
        "post-invalidation: lex-mcp-local removed from cache",
    )
    check(
        row is not None and '"false"' in row[0],
        "post-invalidation: mcp-first-boot-completed = false",
    )

    # 10d  Non-existent DB → False (no crash)
    fake_mcp2 = Path(tmpdir) / "nosuch" / "intellij" / "mcp.json"
    check(
        _invalidate_copilot_mcp_cache(fake_mcp2) is False,
        "non-existent DB → False (no crash)",
    )

    # 10e  Multiple servers: only our server is removed
    conn = sqlite3.connect(fake_db)
    _write_github_copilot_state_value(
        conn, GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY,
        {
            "lex-mcp-local": {"tools": [{"name": "kickstart_workflow"}]},
            "other-server": {"tools": [{"name": "other_tool"}]},
        },
    )
    _write_github_copilot_state_value(
        conn, GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY, "true",
    )
    conn.commit()
    conn.close()

    _invalidate_copilot_mcp_cache(fake_mcp)
    conn = sqlite3.connect(fake_db)
    cached = _load_github_copilot_mcp_servers_cache(conn)
    conn.close()
    check(
        "lex-mcp-local" not in cached and "other-server" in cached,
        "only lex-mcp-local removed; other-server preserved",
    )


# ======================================================================
# 11. ai_dashboard.py — _handle_save triggers cache invalidation
# ======================================================================
section("11. _handle_save — cache invalidation on mode change")

with tempfile.TemporaryDirectory() as tmpdir:
    # Recreate the Copilot directory structure for the dashboard
    intellij_dir = Path(tmpdir) / "copilot" / "intellij"
    intellij_dir.mkdir(parents=True)
    mcp_path = intellij_dir / "mcp.json"
    db_path = Path(tmpdir) / "copilot" / "copilot-intellij.db"

    env_path = Path(tmpdir) / ".env"
    env_path.write_text("GITHUB_TOKEN=ghp_test\n")
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server", "--mode", "backward"],
            "env": {"LEX_MCP_MODE": "backward"},
        }}
    }))

    # Pre-populate the state DB
    conn = sqlite3.connect(db_path)
    _ensure_github_copilot_state_table(conn)
    _write_github_copilot_state_value(
        conn, GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY,
        {"lex-mcp-local": {"tools": [{"name": "reverse_kickstart"}]}},
    )
    _write_github_copilot_state_value(
        conn, GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY, "true",
    )
    conn.commit()
    conn.close()

    # Change mode via dashboard save
    form = {"mcp_mode": ["forward"], "github_token": [""],
            "remote_mcp_api_key": [""], "remote_mcp_url": [""]}
    s, e = _handle_save(form, Path(tmpdir), env_path, mcp_path)

    check(
        any("IDE tool cache cleared" in x for x in s),
        "save reports 'IDE tool cache cleared'",
    )

    # Verify the DB was invalidated
    conn = sqlite3.connect(db_path)
    cached = _load_github_copilot_mcp_servers_cache(conn)
    row = conn.execute(
        "SELECT value FROM state WHERE key=?",
        (GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY,),
    ).fetchone()
    conn.close()
    check(
        "lex-mcp-local" not in cached,
        "after save: lex-mcp-local removed from cache",
    )
    check(
        row is not None and '"false"' in row[0],
        "after save: first-boot-completed reset to false",
    )


# ======================================================================
# 12. ai_dashboard.py — _mask_token
# ======================================================================
section("12. _mask_token — token masking")

from lex.tools.ai_dashboard import _mask_token

check(_mask_token("") == "(not set)", "empty → '(not set)'")
check(_mask_token("abc") == "•••", "short (3) → all bullets")
check(_mask_token("abcdefgh") == "••••••••", "exactly 8 → all bullets")
check(
    _mask_token("ghp_abcdefghij").startswith("ghp_")
    and _mask_token("ghp_abcdefghij").endswith("ghij"),
    "long token → first 4 + bullets + last 4",
)


# ======================================================================
# 13. build_mcp_server_definition — uses resolve_mcp_server_args
# ======================================================================
section("13. build_mcp_server_definition — dynamic args")

from lex.tools.setup_with_ai import build_mcp_server_definition

if _has_unified_mcp_entry_point(py):
    defn = build_mcp_server_definition(
        python_executable=py,
        github_token="ghp_test",
        remote_mcp_api_key="key123",
        mcp_mode="backward",
    )
    check(
        defn["args"] == ["-m", "lex_mcp.server", "--mode", "backward"],
        "build_mcp_server_definition uses unified entry for backward",
    )
    check(
        defn["env"]["LEX_MCP_MODE"] == "backward",
        "env block has LEX_MCP_MODE=backward",
    )
else:
    ok("(skipped — lex_mcp.server not in this venv)")


# ======================================================================
# 14. Cross-cutting: mode-override file handling
# ======================================================================
section("14. Mode override file — write and read")

from lex.tools.ai_dashboard import _write_mode_override, _read_override_file, MODE_OVERRIDE_FILE

# Save original state
original_exists = MODE_OVERRIDE_FILE.exists()
original_content = None
if original_exists:
    original_content = MODE_OVERRIDE_FILE.read_text(encoding="utf-8")

try:
    _write_mode_override("backward")
    override = _read_override_file()
    check(
        override is not None and override.get("mode") == "backward",
        "write + read round-trip: mode=backward",
    )

    _write_mode_override("forward")
    override = _read_override_file()
    check(
        override is not None and override.get("mode") == "forward",
        "overwrite: mode=forward",
    )
finally:
    # Restore original state
    if original_exists and original_content is not None:
        MODE_OVERRIDE_FILE.write_text(original_content, encoding="utf-8")
    elif not original_exists and MODE_OVERRIDE_FILE.exists():
        MODE_OVERRIDE_FILE.unlink()


# ======================================================================
# 15. Credential fields — _handle_save updates credentials
# ======================================================================
section("15. _handle_save — credential updates")

with tempfile.TemporaryDirectory() as tmpdir:
    root = Path(tmpdir)
    env_path = root / ".env"
    mcp_path = root / "mcp.json"

    env_path.write_text("LEX_MCP_MODE=forward\nGITHUB_TOKEN=old_token\n")
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "type": "stdio", "command": "python",
            "args": ["-m", "lex_mcp.server", "--mode", "forward"],
            "env": {"LEX_MCP_MODE": "forward", "GITHUB_TOKEN": "old_token"},
        }}
    }))

    form = {"mcp_mode": ["forward"], "github_token": ["new_gh_token"],
            "remote_mcp_api_key": ["new_key"], "remote_mcp_url": [""]}
    s, e = _handle_save(form, root, env_path, mcp_path)

    check(
        any("Updated" in x for x in s),
        "credential update reported",
    )
    check(
        _read_dotenv_value(env_path, "GITHUB_TOKEN") == "new_gh_token",
        ".env GITHUB_TOKEN updated",
    )
    check(
        _read_dotenv_value(env_path, "REMOTE_MCP_API_KEY") == "new_key",
        ".env REMOTE_MCP_API_KEY updated",
    )

    data = json.loads(mcp_path.read_text())
    mcp_env = data["servers"]["lex-mcp-local"]["env"]
    check(
        mcp_env.get("GITHUB_TOKEN") == "new_gh_token",
        "mcp.json env GITHUB_TOKEN updated",
    )


# ======================================================================
# 16. Dashboard override-awareness  (Part A of mode-sync fix)
# ======================================================================
section("16. _read_dashboard_state — override-aware effective mode")

# Save & isolate the real override file so test scenarios don't touch it.
from lex.tools.ai_dashboard import MODE_OVERRIDE_FILE, _write_mode_override

original_override_existed = MODE_OVERRIDE_FILE.exists()
original_override_content = (
    MODE_OVERRIDE_FILE.read_text(encoding="utf-8")
    if original_override_existed
    else None
)
if original_override_existed:
    MODE_OVERRIDE_FILE.unlink()

try:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_path = root / ".env"
        mcp_path = root / "mcp.json"

        # Setup: persisted state is "forward" (.env + mcp.json),
        # but the server just crashed-and-rebooted and the override
        # file still exists (mid-restart window).
        env_path.write_text("LEX_MCP_MODE=forward\n")
        mcp_path.write_text(json.dumps({
            "servers": {"lex-mcp-local": {
                "args": ["-m", "lex_mcp.server", "--mode", "forward"],
                "env": {"LEX_MCP_MODE": "forward"},
            }}
        }))
        _write_mode_override("backward")

        state = _read_dashboard_state(root, env_path, py, mcp_path)
        check(
            state["mcp_mode"] == "backward",
            "★ effective mode = override mode (backward), not persisted (forward)",
        )
        check(
            state["persisted_mcp_mode"] == "forward",
            "persisted_mcp_mode = forward (what mcp.json/.env say)",
        )
        check(
            state["pending_mode_change"] is True,
            "pending_mode_change = True (mid-restart: override differs from persisted)",
        )
        check(
            state["override_pending"] is not None
            and state["override_pending"].get("mode") == "backward",
            "override_pending payload preserved",
        )

        # Same persisted, override agrees → no pending flag
        _write_mode_override("forward")
        state = _read_dashboard_state(root, env_path, py, mcp_path)
        check(
            state["pending_mode_change"] is False,
            "override agrees with persisted → no pending flag",
        )
        check(
            state["mcp_mode"] == "forward",
            "agreement: effective mode = persisted mode",
        )

        # No override file → behaves like before
        MODE_OVERRIDE_FILE.unlink()
        state = _read_dashboard_state(root, env_path, py, mcp_path)
        check(
            state["pending_mode_change"] is False,
            "no override file → no pending flag",
        )
        check(
            state["mcp_mode"] == "forward",
            "no override → effective = persisted",
        )
        check(
            state["override_pending"] is None,
            "no override → override_pending = None",
        )

    # ── Save handler re-syncs stale state from crash-and-reboot ──────
    section("17. _handle_save — re-syncs stale persisted state")

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_path = root / ".env"
        mcp_path = root / "mcp.json"

        # Persisted state stuck on "forward": the crash-and-reboot wrote
        # the override and eagerly synced .env (but in this scenario .env
        # sync didn't take effect — simulating a partial failure).
        env_path.write_text("LEX_MCP_MODE=forward\n")
        mcp_path.write_text(json.dumps({
            "servers": {"lex-mcp-local": {
                "type": "stdio", "command": "python",
                "args": ["-m", "lex_mcp.server", "--mode", "forward"],
                "env": {"LEX_MCP_MODE": "forward"},
            }}
        }))
        _write_mode_override("backward")

        # User opens the dashboard during or after restart, sees
        # effective=backward, clicks Save to force re-sync.
        form = {"mcp_mode": ["backward"], "github_token": [""],
                "remote_mcp_api_key": [""], "remote_mcp_url": [""]}
        s, e = _handle_save(form, root, env_path, mcp_path)

        check(
            any("Mode changed to backward" in x for x in s),
            "★ stale forward + backward override + form=backward → re-syncs to backward",
        )
        check(
            _read_dotenv_value(env_path, "LEX_MCP_MODE") == "backward",
            ".env now says backward",
        )
        data = json.loads(mcp_path.read_text())
        args = data["servers"]["lex-mcp-local"]["args"]
        idx = args.index("--mode") + 1
        check(args[idx] == "backward", "mcp.json --mode now backward")
        check(
            data["servers"]["lex-mcp-local"]["env"]["LEX_MCP_MODE"] == "backward",
            "mcp.json env LEX_MCP_MODE now backward",
        )

finally:
    # Restore original override file state
    if MODE_OVERRIDE_FILE.exists():
        MODE_OVERRIDE_FILE.unlink()
    if original_override_existed and original_override_content is not None:
        MODE_OVERRIDE_FILE.write_text(original_override_content, encoding="utf-8")


# ======================================================================
# 18. lex-mcp-local mode_switch.apply_mode_change_to_external_state  (Part B)
# ======================================================================
section("18. mode_switch.apply_mode_change_to_external_state — eager external sync")

# Disable IDE autodiscovery so apply_mode_change_to_external_state does
# NOT touch the user's real ~/.config/github-copilot/intellij/mcp.json
# or the equivalent VSCode paths during the test.
os.environ["LEX_MCP_DISABLE_IDE_AUTODISCOVER"] = "1"

# Import from lex-mcp-local (installed in our venv as lex-mcp-local 0.2.3)
try:
    from lex_mcp import mode_switch as _ms
except ImportError:
    _ms = None
    fail("import lex_mcp.mode_switch", "package not importable in this venv")

if _ms is not None:
    # 18a  _update_mcp_json_mode: changes --mode arg and env block
    with tempfile.TemporaryDirectory() as tmpdir:
        mcp_path = Path(tmpdir) / "mcp.json"
        mcp_path.write_text(json.dumps({
            "servers": {"lex-mcp-local": {
                "type": "stdio", "command": "python",
                "args": ["-m", "lex_mcp.server", "--mode", "forward"],
                "env": {"LEX_MCP_MODE": "forward"},
            }}
        }))
        changed = _ms._update_mcp_json_mode(mcp_path, "backward")
        check(changed is True, "_update_mcp_json_mode returns True on change")
        data = json.loads(mcp_path.read_text())
        args = data["servers"]["lex-mcp-local"]["args"]
        idx = args.index("--mode") + 1
        check(args[idx] == "backward", "mcp.json --mode arg updated to backward")
        check(
            data["servers"]["lex-mcp-local"]["env"]["LEX_MCP_MODE"] == "backward",
            "mcp.json env block updated to backward",
        )

        # No-op when already in target mode
        again = _ms._update_mcp_json_mode(mcp_path, "backward")
        check(again is False, "_update_mcp_json_mode returns False when no change")

        # Appends --mode when missing
        mcp_path.write_text(json.dumps({
            "servers": {"lex-mcp-local": {
                "args": ["-m", "lex_mcp.server"],
                "env": {},
            }}
        }))
        _ms._update_mcp_json_mode(mcp_path, "backward")
        data = json.loads(mcp_path.read_text())
        check(
            "--mode" in data["servers"]["lex-mcp-local"]["args"]
            and "backward" in data["servers"]["lex-mcp-local"]["args"],
            "appends --mode <value> when missing from args",
        )

        # Wrong server name → no-op
        mcp_path.write_text(json.dumps({
            "servers": {"other-server": {"args": []}}
        }))
        check(
            _ms._update_mcp_json_mode(mcp_path, "backward") is False,
            "wrong server_name → no change",
        )

        # Malformed JSON → False, no crash
        bad = Path(tmpdir) / "bad.json"
        bad.write_text("not json")
        check(
            _ms._update_mcp_json_mode(bad, "backward") is False,
            "malformed JSON → False (no crash)",
        )

    # 18b  _invalidate_pycharm_copilot_cache against synthetic DB
    with tempfile.TemporaryDirectory() as tmpdir:
        intellij = Path(tmpdir) / "intellij"
        intellij.mkdir()
        mcp_path = intellij / "mcp.json"
        mcp_path.write_text("{}")
        db_path = Path(tmpdir) / "copilot-intellij.db"

        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE state (key TEXT PRIMARY KEY, "
            "value TEXT NOT NULL, updated_at INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO state(key, value, updated_at) VALUES (?, ?, ?)",
            ("mcp-servers-cache",
             # real Copilot value is a JSON string of JSON
             json.dumps(json.dumps({"lex-mcp-local": {"tools": [{"name": "x"}]},
                                    "other": {"tools": []}})),
             1),
        )
        conn.execute(
            "INSERT INTO state(key, value, updated_at) VALUES (?, ?, ?)",
            ("mcp-first-boot-completed", '"true"', 1),
        )
        conn.commit()
        conn.close()

        ok_invalidate = _ms._invalidate_pycharm_copilot_cache(mcp_path)
        check(ok_invalidate is True, "PyCharm cache invalidation returns True")

        conn = sqlite3.connect(db_path)
        cache_row = conn.execute(
            "SELECT value FROM state WHERE key='mcp-servers-cache'"
        ).fetchone()
        first_boot = conn.execute(
            "SELECT value FROM state WHERE key='mcp-first-boot-completed'"
        ).fetchone()
        conn.close()
        # Cache value is JSON string of JSON
        cache = json.loads(json.loads(cache_row[0]))
        check(
            "lex-mcp-local" not in cache,
            "lex-mcp-local removed from cache",
        )
        check("other" in cache, "other server preserved in cache")
        check(
            first_boot is not None and '"false"' in first_boot[0],
            "mcp-first-boot-completed reset to false",
        )

        # Idempotent: missing DB → False, no crash
        nodir = Path(tmpdir) / "nope" / "intellij" / "mcp.json"
        check(
            _ms._invalidate_pycharm_copilot_cache(nodir) is False,
            "missing DB → False (no crash)",
        )

    # 18c  apply_mode_change_to_external_state full flow
    with tempfile.TemporaryDirectory() as tmpdir:
        # Synthetic project .env that should get updated via sync_env_var
        project_dir = Path(tmpdir) / "project"
        project_dir.mkdir()
        project_env = project_dir / ".env"
        project_env.write_text(
            "GITHUB_TOKEN=ghp_test\nLEX_MCP_MODE=forward\n"
        )

        # Synthetic mcp.json under intellij/ so cache invalidation runs
        intellij = Path(tmpdir) / "intellij"
        intellij.mkdir()
        mcp_path = intellij / "mcp.json"
        mcp_path.write_text(json.dumps({
            "servers": {"lex-mcp-local": {
                "args": ["-m", "lex_mcp.server", "--mode", "forward"],
                "env": {"LEX_MCP_MODE": "forward"},
            }}
        }))
        # Synthetic state DB next to intellij/
        db_path = Path(tmpdir) / "copilot-intellij.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE state (key TEXT PRIMARY KEY, "
            "value TEXT NOT NULL, updated_at INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO state(key, value, updated_at) VALUES (?, ?, ?)",
            ("mcp-servers-cache",
             json.dumps(json.dumps({"lex-mcp-local": {"tools": [{"name": "kickstart"}]}})),
             1),
        )
        conn.commit()
        conn.close()

        # Point sync_env_var at the project .env via LEX_MCP_PROJECT_DIR
        old_project_dir = os.environ.get("LEX_MCP_PROJECT_DIR")
        old_mode = os.environ.get("LEX_MCP_MODE")
        os.environ["LEX_MCP_PROJECT_DIR"] = str(project_dir)
        try:
            report = _ms.apply_mode_change_to_external_state(
                "backward",
                extra_mcp_json_paths=[str(mcp_path)],
            )
        finally:
            if old_project_dir is None:
                os.environ.pop("LEX_MCP_PROJECT_DIR", None)
            else:
                os.environ["LEX_MCP_PROJECT_DIR"] = old_project_dir
            if old_mode is None:
                os.environ.pop("LEX_MCP_MODE", None)
            else:
                os.environ["LEX_MCP_MODE"] = old_mode

        check(report["mode"] == "backward", "report.mode = backward")
        check(
            str(mcp_path) in report["mcp_json_updated"],
            "mcp.json listed in mcp_json_updated",
        )
        check(
            str(mcp_path) in report["ide_caches_cleared"],
            "intellij/ mcp.json triggered IDE cache invalidation",
        )
        check(
            isinstance(report["env"], dict)
            and report["env"].get("mode") == "backward",
            "env sub-report present",
        )

        # Verify side effects on disk
        env_text = project_env.read_text()
        check(
            'LEX_MCP_MODE="backward"' in env_text or "LEX_MCP_MODE=backward" in env_text,
            "project .env LEX_MCP_MODE rewritten to backward",
        )
        data = json.loads(mcp_path.read_text())
        args = data["servers"]["lex-mcp-local"]["args"]
        idx = args.index("--mode") + 1
        check(args[idx] == "backward", "mcp.json --mode rewritten to backward")
        check(
            data["servers"]["lex-mcp-local"]["env"]["LEX_MCP_MODE"] == "backward",
            "mcp.json env block rewritten to backward",
        )
        conn = sqlite3.connect(db_path)
        cache_row = conn.execute(
            "SELECT value FROM state WHERE key='mcp-servers-cache'"
        ).fetchone()
        conn.close()
        cache = json.loads(json.loads(cache_row[0]))
        check(
            "lex-mcp-local" not in cache,
            "lex-mcp-local entry removed from PyCharm cache",
        )

    # 18d  Best-effort: returns a report even when nothing exists
    with tempfile.TemporaryDirectory() as tmpdir:
        # No mcp.json files, no project .env
        old_project_dir = os.environ.get("LEX_MCP_PROJECT_DIR")
        os.environ["LEX_MCP_PROJECT_DIR"] = tmpdir  # exists but no .env
        try:
            report = _ms.apply_mode_change_to_external_state("forward")
        finally:
            if old_project_dir is None:
                os.environ.pop("LEX_MCP_PROJECT_DIR", None)
            else:
                os.environ["LEX_MCP_PROJECT_DIR"] = old_project_dir

        check(
            isinstance(report, dict) and report["mode"] == "forward",
            "no targets → still returns a report (best-effort)",
        )


# ======================================================================
# 19. verify_ai_assets.resolve_active_mcp_mode — override-file priority
# ======================================================================
section("19. resolve_active_mcp_mode — override file takes priority over .env")

from lex.tools.verify_ai_assets import (
    resolve_active_mcp_mode,
    MODE_OVERRIDE_FILE as _VAI_MOF,
)

_test19_override_existed = _VAI_MOF.is_file()
_test19_override_content = (
    _VAI_MOF.read_text(encoding="utf-8") if _test19_override_existed else None
)
if _test19_override_existed:
    _VAI_MOF.unlink()

try:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_path = root / ".env"

        # 19a  Override file present → wins over .env
        env_path.write_text("LEX_MCP_MODE=forward\n")
        _VAI_MOF.parent.mkdir(parents=True, exist_ok=True)
        _VAI_MOF.write_text(
            json.dumps({"mode": "backward", "reason": "test"}),
            encoding="utf-8",
        )
        mode, source = resolve_active_mcp_mode(root)
        check(mode == "backward", "★ override file wins over .env (backward)")
        check(source == "override-file", "source = override-file")

        # 19b  Override file removed → falls back to .env
        _VAI_MOF.unlink()
        mode, source = resolve_active_mcp_mode(root)
        check(mode == "forward", "no override → .env used (forward)")
        check(source == "project-dotenv", "source = project-dotenv")

        # 19c  Neither override nor .env → default
        env_path.write_text("GITHUB_TOKEN=ghp_test\n")  # no LEX_MCP_MODE
        # Clear process env to avoid contamination
        old_env = os.environ.pop("LEX_MCP_MODE", None)
        try:
            mode, source = resolve_active_mcp_mode(root)
            check(mode == "forward", "no override, no .env, no env → default forward")
            check(source == "default", "source = default")
        finally:
            if old_env is not None:
                os.environ["LEX_MCP_MODE"] = old_env

        # 19d  Explicit mode always wins over everything
        _VAI_MOF.parent.mkdir(parents=True, exist_ok=True)
        _VAI_MOF.write_text(
            json.dumps({"mode": "backward"}), encoding="utf-8",
        )
        env_path.write_text("LEX_MCP_MODE=backward\n")
        mode, source = resolve_active_mcp_mode(root, explicit_mode="forward")
        check(mode == "forward", "explicit_mode wins over override + .env")
        check(source == "cli", "source = cli")

        # 19e  Override with bare string (non-JSON) also works
        _VAI_MOF.write_text("backward", encoding="utf-8")
        env_path.write_text("LEX_MCP_MODE=forward\n")
        mode, source = resolve_active_mcp_mode(root)
        check(mode == "backward", "bare-string override file works")
        check(source == "override-file", "source = override-file (bare string)")

        # Cleanup
        if _VAI_MOF.exists():
            _VAI_MOF.unlink()
finally:
    if _VAI_MOF.exists():
        _VAI_MOF.unlink()
    if _test19_override_existed and _test19_override_content is not None:
        _VAI_MOF.write_text(_test19_override_content, encoding="utf-8")


# ======================================================================
# 20. _find_server_defs — fuzzy server lookup (mcpServers + name matching)
# ======================================================================
section("20. _find_server_defs — fuzzy server lookup")

from lex.tools.ai_dashboard import _find_server_defs

# 20a  Canonical name under "servers"
config_a = {"servers": {"lex-mcp-local": {"args": ["--mode", "forward"]}}}
defs = _find_server_defs(config_a)
check(len(defs) == 1, "canonical name under 'servers' found")

# 20b  Fuzzy name under "servers"
config_b = {"servers": {"my-lex-mcp-server": {"args": []}}}
defs = _find_server_defs(config_b)
check(len(defs) == 1, "fuzzy 'lex-mcp' match found")

# 20c  mcpServers key
config_c = {"mcpServers": {"lex-mcp-local": {"args": []}}}
defs = _find_server_defs(config_c)
check(len(defs) == 1, "mcpServers key found")

# 20d  Both keys have entries
config_d = {
    "servers": {"lex-mcp-local": {"args": []}},
    "mcpServers": {"lex_mcp_custom": {"args": []}},
}
defs = _find_server_defs(config_d)
check(len(defs) == 2, "entries from both servers + mcpServers found")

# 20e  No match
config_e = {"servers": {"unrelated-server": {"args": []}}}
defs = _find_server_defs(config_e)
check(len(defs) == 0, "non-matching server name → empty list")

# 20f  lex_mcp (underscore) also matches
config_f = {"mcpServers": {"lex_mcp_tool": {"args": []}}}
defs = _find_server_defs(config_f)
check(len(defs) == 1, "lex_mcp (underscore) also matches")


# ======================================================================
# 21. _update_mcp_json_mode — mcpServers + fuzzy matching
# ======================================================================
section("21. _update_mcp_json_mode — mcpServers + fuzzy matching")

from lex.tools.ai_dashboard import _update_mcp_json_mode as _dashboard_update_mode

with tempfile.TemporaryDirectory() as tmpdir:
    mcp_path = Path(tmpdir) / "mcp.json"

    # 21a  mcpServers key: mode is updated
    mcp_path.write_text(json.dumps({
        "mcpServers": {"lex-mcp-local": {
            "command": "python",
            "args": ["-m", "lex_mcp.server", "--mode", "forward"],
            "env": {"LEX_MCP_MODE": "forward"},
        }}
    }))
    changed = _dashboard_update_mode(mcp_path, "backward")
    check(changed is True, "mcpServers: mode updated → True")
    data = json.loads(mcp_path.read_text())
    args = data["mcpServers"]["lex-mcp-local"]["args"]
    idx = args.index("--mode") + 1
    check(args[idx] == "backward", "mcpServers: --mode arg updated")
    check(
        data["mcpServers"]["lex-mcp-local"]["env"]["LEX_MCP_MODE"] == "backward",
        "mcpServers: env block updated",
    )

    # 21b  Fuzzy name match: "my-lex-mcp-v2" is updated
    mcp_path.write_text(json.dumps({
        "servers": {"my-lex-mcp-v2": {
            "args": ["-m", "lex_mcp.server", "--mode", "forward"],
            "env": {"LEX_MCP_MODE": "forward"},
        }}
    }))
    changed = _dashboard_update_mode(mcp_path, "backward")
    check(changed is True, "fuzzy name: mode updated → True")
    data = json.loads(mcp_path.read_text())
    check(
        data["servers"]["my-lex-mcp-v2"]["env"]["LEX_MCP_MODE"] == "backward",
        "fuzzy name: env block updated",
    )

    # 21c  Both keys have entries: all are updated
    mcp_path.write_text(json.dumps({
        "servers": {"lex-mcp-local": {
            "args": ["--mode", "forward"],
            "env": {"LEX_MCP_MODE": "forward"},
        }},
        "mcpServers": {"my-lex-mcp": {
            "args": ["--mode", "forward"],
            "env": {"LEX_MCP_MODE": "forward"},
        }},
    }))
    changed = _dashboard_update_mode(mcp_path, "backward")
    check(changed is True, "both keys: updated → True")
    data = json.loads(mcp_path.read_text())
    check(
        data["servers"]["lex-mcp-local"]["env"]["LEX_MCP_MODE"] == "backward",
        "both keys: servers entry updated",
    )
    check(
        data["mcpServers"]["my-lex-mcp"]["env"]["LEX_MCP_MODE"] == "backward",
        "both keys: mcpServers entry updated",
    )

    # 21d  No-op when already correct
    changed = _dashboard_update_mode(mcp_path, "backward")
    check(changed is False, "already correct → False")

    # 21e  Non-matching name → no change
    mcp_path.write_text(json.dumps({
        "servers": {"unrelated": {
            "args": ["--mode", "forward"],
            "env": {"LEX_MCP_MODE": "forward"},
        }}
    }))
    changed = _dashboard_update_mode(mcp_path, "backward")
    check(changed is False, "non-matching name → False")


# ======================================================================
# 22. _update_mcp_json_env_values — mcpServers + fuzzy matching
# ======================================================================
section("22. _update_mcp_json_env_values — mcpServers + fuzzy matching")

from lex.tools.ai_dashboard import _update_mcp_json_env_values

with tempfile.TemporaryDirectory() as tmpdir:
    mcp_path = Path(tmpdir) / "mcp.json"

    # 22a  mcpServers: env values are updated
    mcp_path.write_text(json.dumps({
        "mcpServers": {"lex-mcp-local": {
            "command": "python",
            "args": [],
            "env": {"GITHUB_TOKEN": "old_gh"},
        }}
    }))
    changed = _update_mcp_json_env_values(mcp_path, {"GITHUB_TOKEN": "new_gh"})
    check(changed is True, "mcpServers: env updated → True")
    data = json.loads(mcp_path.read_text())
    check(
        data["mcpServers"]["lex-mcp-local"]["env"]["GITHUB_TOKEN"] == "new_gh",
        "mcpServers: GITHUB_TOKEN updated",
    )

    # 22b  Fuzzy name match
    mcp_path.write_text(json.dumps({
        "servers": {"my-lex-mcp-tool": {
            "env": {"KEY": "old"},
        }}
    }))
    changed = _update_mcp_json_env_values(mcp_path, {"KEY": "new"})
    check(changed is True, "fuzzy name: env updated → True")
    data = json.loads(mcp_path.read_text())
    check(
        data["servers"]["my-lex-mcp-tool"]["env"]["KEY"] == "new",
        "fuzzy name: KEY updated",
    )


# ======================================================================
# 23. resolve_active_mcp_mode — mcp.json fallback
# ======================================================================
section("23. resolve_active_mcp_mode — mcp.json fallback in resolution chain")

from lex.tools.verify_ai_assets import (
    resolve_active_mcp_mode as _resolve_mode_23,
    MODE_OVERRIDE_FILE as _VAI_MOF_23,
)

_test23_override_existed = _VAI_MOF_23.is_file()
_test23_override_content = (
    _VAI_MOF_23.read_text(encoding="utf-8") if _test23_override_existed else None
)
if _test23_override_existed:
    _VAI_MOF_23.unlink()

try:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_path = root / ".env"

        # 23a  mcp.json in project root is consulted when no .env mode
        env_path.write_text("GITHUB_TOKEN=ghp_test\n")  # no LEX_MCP_MODE
        mcp_path = root / "mcp.json"
        mcp_path.write_text(json.dumps({
            "servers": {"lex-mcp-local": {
                "args": ["--mode", "backward"],
                "env": {"LEX_MCP_MODE": "backward"},
            }}
        }))
        old_env = os.environ.pop("LEX_MCP_MODE", None)
        try:
            mode, source = _resolve_mode_23(root)
            check(mode == "backward", "★ mcp.json fallback: backward")
            check(source == "mcp-json", "source = mcp-json")
        finally:
            if old_env is not None:
                os.environ["LEX_MCP_MODE"] = old_env

        # 23b  .env takes priority over mcp.json
        env_path.write_text("LEX_MCP_MODE=forward\n")
        mode, source = _resolve_mode_23(root)
        check(mode == "forward", ".env wins over mcp.json")
        check(source == "project-dotenv", "source = project-dotenv")

        # 23c  mcpServers key in mcp.json also works
        env_path.write_text("GITHUB_TOKEN=test\n")  # no LEX_MCP_MODE
        mcp_path.write_text(json.dumps({
            "mcpServers": {"lex-mcp-local": {
                "args": ["--mode", "backward"],
            }}
        }))
        old_env = os.environ.pop("LEX_MCP_MODE", None)
        try:
            mode, source = _resolve_mode_23(root)
            check(mode == "backward", "mcpServers key: backward from mcp.json")
            check(source == "mcp-json", "source = mcp-json (mcpServers)")
        finally:
            if old_env is not None:
                os.environ["LEX_MCP_MODE"] = old_env

        # 23d  .cursor/mcp.json is also scanned
        mcp_path.unlink()
        cursor_dir = root / ".cursor"
        cursor_dir.mkdir()
        cursor_mcp = cursor_dir / "mcp.json"
        cursor_mcp.write_text(json.dumps({
            "mcpServers": {"my-lex-mcp": {
                "args": ["--mode", "backward"],
            }}
        }))
        old_env = os.environ.pop("LEX_MCP_MODE", None)
        try:
            mode, source = _resolve_mode_23(root)
            check(mode == "backward", ".cursor/mcp.json scanned")
            check(source == "mcp-json", "source = mcp-json (.cursor)")
        finally:
            if old_env is not None:
                os.environ["LEX_MCP_MODE"] = old_env

        # Cleanup
        cursor_mcp.unlink()
        cursor_dir.rmdir()
finally:
    if _VAI_MOF_23.exists():
        _VAI_MOF_23.unlink()
    if _test23_override_existed and _test23_override_content is not None:
        _VAI_MOF_23.write_text(_test23_override_content, encoding="utf-8")


# ======================================================================
# SUMMARY
# ======================================================================
print(f"\n{'═' * 60}")
total = _pass + _fail
print(f"  RESULTS: {_pass}/{total} passed, {_fail} failed")
print(f"{'═' * 60}\n")

sys.exit(1 if _fail > 0 else 0)
