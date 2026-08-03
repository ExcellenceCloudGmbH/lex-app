"""Verify and restore the static asset directories that the LEX MCP server
depends on.

The LEX MCP runtime ships six workflow modes inside the ``lex-mcp-local``
distribution, each with its own agent payload package:

* **forward** — :mod:`lex_mcp_local` (new-project creation workflow).
* **backward** — :mod:`lex_mcp_reverse` (documentation / reverse mapping).
* **edit** — :mod:`lex_mcp_edit` (targeted changes).
* **review** — :mod:`lex_mcp_review` (static audits).
* **mvp_generator** — :mod:`lex_mcp_mvp` (reduced-scope build).
* **mvp_completion** — :mod:`lex_mcp_mvp_completion` (MVP to full product).

Assets are delivered per **agentic environment**, not just per mode: the
project's ``LEX_AI_ENVIRONMENTS`` value decides which of GitHub Copilot
(JetBrains / VS Code / CLI), Cursor, Claude Code, OpenAI Codex, and Windsurf
receive the payload, and each receives it in its own native layout. That work
is delegated to ``lex_mcp.ai_onboarding`` so new environments ship with a
lex-mcp-local release and need no lex-app change. The legacy ``.github``
mirror below is the fallback for installs that predate the registry.

Mode selection inside the MCP server is determined by the CLI flag
(``--mode``), a one-shot override file (``~/.lex-mcp/mode-override``), or
the ``LEX_MCP_MODE`` environment variable / ``.env`` entry.  With the
crash-and-reboot mechanism (lex-mcp-local ≥ 1.0.0), mode switches are
instant: the server self-terminates via ``os._exit(0)`` and the IDE
auto-restarts the subprocess with the new mode's tool surface.

The override file is consumed on the fresh start and deleted. During the
brief auto-restart window it is the authoritative source of the target
mode.  The server also eagerly syncs ``LEX_MCP_MODE`` into ``.env`` and
``mcp.json`` *before* dying, so those files reflect the new mode
immediately.

This module's :func:`resolve_active_mcp_mode` checks the override file
first, then ``.env``, then project ``mcp.json`` files, then process
environment, to guarantee the correct mode is used even during a
mid-restart or live-switch window.

The ``docs/`` directory is shipped by the ``lex`` package itself and is
required in every mode.

This module exposes :func:`verify_ai_assets`, which:

1. Resolves the active MCP mode (CLI > override file > project ``.env`` >
   ``mcp.json`` > process env > default ``forward``).
2. Walks the canonical source trees for that mode.
3. Restores any file under the project root that is missing or whose contents
   have drifted from the canonical copy (byte-for-byte comparison).

User-added files in the destination are preserved except in mode-managed
subdirectories (currently ``.github/agents``, ``.github/instructions``, and
``.github/prompts``), which are mirrored exactly so stale mode assets are
removed during mode switches.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from lex.tools.setup_with_ai import (
    LEX_APP_EMBEDDED_DIRECTORY_NAMES,
    SetupWithAIError,
    resolve_active_python_executable,
    resolve_lex_app_package_root,
)

# ---------------------------------------------------------------------------
# Mode model
# ---------------------------------------------------------------------------

MCP_MODE_FORWARD = "forward"
MCP_MODE_BACKWARD = "backward"
DEFAULT_MCP_MODE = MCP_MODE_FORWARD

# Map mode -> package shipping the mode-specific ``.github`` directory.
MCP_MODE_PACKAGE: dict[str, str] = {
    MCP_MODE_FORWARD: "lex_mcp_local",
    MCP_MODE_BACKWARD: "lex_mcp_reverse",
    "edit": "lex_mcp_edit",
    "review": "lex_mcp_review",
    "mvp_generator": "lex_mcp_mvp",
    "mvp_completion": "lex_mcp_mvp_completion",
}

# Directories shipped inside each mode's package that must mirror to project
# root. Currently only ``.github`` but kept as a tuple for forward-compat.
MCP_MODE_DIRECTORIES: tuple[str, ...] = (".github",)

ALL_MCP_MODES: tuple[str, ...] = tuple(MCP_MODE_PACKAGE.keys())

# Pattern used to read ``KEY=value`` lines from a project ``.env``.
_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectoryVerificationResult:
    """Outcome of verifying a single source directory against the project."""

    directory_name: str
    source_directory: Path | None
    destination_directory: Path
    restored_files: tuple[Path, ...] = ()
    removed_files: tuple[Path, ...] = ()
    missing_files: tuple[Path, ...] = ()
    modified_files: tuple[Path, ...] = ()
    skipped_reason: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.skipped_reason is None
            and not self.restored_files
            and not self.removed_files
        )


@dataclass(frozen=True)
class EnvironmentVerificationResult:
    """Outcome of syncing one agentic environment's native asset layout."""

    environment: str
    display_name: str
    dialect: str
    written: tuple[str, ...] = ()
    pruned: tuple[str, ...] = ()
    unchanged_count: int = 0
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class VerifyAIAssetsResult:
    """Aggregated outcome across every directory the verifier inspected."""

    project_root: Path
    mode: str
    mode_source: str
    directories: tuple[DirectoryVerificationResult, ...] = field(default_factory=tuple)
    environments: tuple[EnvironmentVerificationResult, ...] = field(
        default_factory=tuple
    )
    environment_sync_error: str | None = None

    @property
    def restored_files(self) -> tuple[Path, ...]:
        return tuple(p for d in self.directories for p in d.restored_files)

    @property
    def removed_files(self) -> tuple[Path, ...]:
        return tuple(p for d in self.directories for p in d.removed_files)

    @property
    def ok(self) -> bool:
        return (
            all(d.ok for d in self.directories)
            and all(e.ok for e in self.environments)
            and self.environment_sync_error is None
        )


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


def _read_env_file_value(env_file_path: Path, key: str) -> str | None:
    if not env_file_path.is_file():
        return None
    try:
        text = env_file_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if match and match.group(1) == key:
            value = match.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            return value
    return None


MODE_OVERRIDE_FILE = Path.home() / ".lex-mcp" / "mode-override"

# Server-entry name matching for mcp.json — same logic as lex-mcp-local's
# mode_switch._update_mcp_json_mode: canonical name or any entry containing
# "lex-mcp" / "lex_mcp" (case-insensitive).
_LEX_MCP_SERVER_NAME = "lex-mcp-local"


def _read_mode_from_mcp_json(mcp_path: Path) -> str | None:
    """Extract the active mode from any lex-mcp entry in *mcp_path*.

    Checks both ``servers`` and ``mcpServers`` top-level keys and matches
    any entry whose name contains ``lex-mcp`` / ``lex_mcp``.  Prefers
    ``--mode`` CLI arg, then ``LEX_MCP_MODE`` env block.
    """
    if not mcp_path.is_file():
        return None
    try:
        import json as _json

        config = _json.loads(mcp_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for key in ("servers", "mcpServers"):
        block = config.get(key)
        if not isinstance(block, dict):
            continue
        for entry_name, server_def in block.items():
            if not isinstance(server_def, dict):
                continue
            name_lc = entry_name.lower()
            if (
                entry_name != _LEX_MCP_SERVER_NAME
                and "lex-mcp" not in name_lc
                and "lex_mcp" not in name_lc
            ):
                continue
            # --mode arg
            args = server_def.get("args", [])
            if isinstance(args, list):
                for i, arg in enumerate(args):
                    if arg == "--mode" and i + 1 < len(args):
                        val = str(args[i + 1]).strip().lower()
                        if val in MCP_MODE_PACKAGE:
                            return val
            # env block
            env_block = server_def.get("env", {})
            if isinstance(env_block, dict):
                val = str(env_block.get("LEX_MCP_MODE", "")).strip().lower()
                if val in MCP_MODE_PACKAGE:
                    return val
    return None


def _read_override_mode() -> str | None:
    """Read the mode from the one-shot override file, if present."""
    try:
        if not MODE_OVERRIDE_FILE.is_file():
            return None
        raw = MODE_OVERRIDE_FILE.read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            import json

            candidate = json.loads(raw).get("mode", "")
        else:
            candidate = raw
        candidate = str(candidate).strip().lower()
        return candidate if candidate in MCP_MODE_PACKAGE else None
    except Exception:
        return None


def _read_mode_from_codex_toml(config_path: Path) -> str | None:
    """Extract the active mode from a Codex ``config.toml`` MCP table."""
    if not config_path.is_file():
        return None
    try:
        from lex_mcp.environments import (  # type: ignore[import-not-found]
            SERVERS_KEY_CODEX,
            read_toml_server,
        )
    except Exception:
        return None
    try:
        entry = read_toml_server(
            config_path.read_text(encoding="utf-8"),
            _LEX_MCP_SERVER_NAME,
            servers_key=SERVERS_KEY_CODEX,
        )
    except OSError:
        return None
    if not entry:
        return None
    args = entry.get("args") or []
    if isinstance(args, (list, tuple)):
        for index, arg in enumerate(args):
            if str(arg) == "--mode" and index + 1 < len(args):
                value = str(args[index + 1]).strip().lower()
                if value in MCP_MODE_PACKAGE:
                    return value
    env_block = entry.get("env") or {}
    if isinstance(env_block, dict):
        value = str(env_block.get("LEX_MCP_MODE", "")).strip().lower()
        if value in MCP_MODE_PACKAGE:
            return value
    return None


def _resolve_mode_from_mcp_json_files(project_root: Path) -> str | None:
    """Scan project-scoped MCP configs for the active mode.

    Covers every environment the MCP server supports: the Copilot configs
    (``mcp.json``, ``.vscode/mcp.json``, ``.idea/mcp.json``), Cursor
    (``.cursor/mcp.json``), Claude Code (``.mcp.json``), and Codex
    (``.codex/config.toml``, which is TOML rather than JSON).
    Returns the first valid mode found, or *None*.
    """
    root = Path(project_root).resolve()
    candidates = [
        root / "mcp.json",
        root / ".vscode" / "mcp.json",
        root / ".cursor" / "mcp.json",
        root / ".mcp.json",
        root / ".idea" / "mcp.json",
        root / ".pycharm" / "mcp.json",
    ]
    for path in candidates:
        mode = _read_mode_from_mcp_json(path)
        if mode:
            return mode
    return _read_mode_from_codex_toml(root / ".codex" / "config.toml")


def resolve_active_mcp_mode(
    project_root: Path,
    *,
    explicit_mode: str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Resolve the active MCP mode and report where the value came from.

    Precedence:

    1. *explicit_mode* (e.g. ``lex ai-verify --mode backward``).
    2. Override file ``~/.lex-mcp/mode-override`` — written by
       ``switch_to_*_mode`` MCP tools and consumed on the next server
       start.  With the unified live-switch mechanism the override is
       very short-lived, but during the auto-restart window it is the
       authoritative source of the target mode.
    3. ``LEX_MCP_MODE`` in ``<project_root>/.env`` — eagerly synced by
       ``apply_mode_change_to_external_state`` before the server dies
       (or live-switches), so it is accurate immediately after a switch.
    4. ``mcp.json`` in the project root or CWD-relative IDE config
       directories (``mcp.json``, ``.vscode/mcp.json``, ``.cursor/mcp.json``,
       ``.idea/mcp.json``).  The server eagerly syncs these as well.
    5. ``LEX_MCP_MODE`` in *env* / process environment (fallback; may be
       stale if the server rewrote ``.env`` but the shell was not reloaded).
    6. :data:`DEFAULT_MCP_MODE` (``forward``).

    Returns ``(mode, source)``. Raises :class:`SetupWithAIError` if the
    resolved mode is not one of :data:`ALL_MCP_MODES`.
    """
    if explicit_mode:
        mode, source = explicit_mode.strip().lower(), "cli"
    else:
        # Check the override file first — it is the ground truth during
        # the brief auto-restart window.
        override_mode = _read_override_mode()
        if override_mode:
            mode, source = override_mode, "override-file"
        else:
            dotenv_value = _read_env_file_value(
                Path(project_root).resolve() / ".env",
                "LEX_MCP_MODE",
            )
            if dotenv_value:
                mode, source = dotenv_value.strip().lower(), "project-dotenv"
            else:
                # Scan mcp.json files in project root / CWD for mode.
                mcp_mode = _resolve_mode_from_mcp_json_files(project_root)
                if mcp_mode:
                    mode, source = mcp_mode, "mcp-json"
                else:
                    env_map = dict(os.environ if env is None else env)
                    env_mode = env_map.get("LEX_MCP_MODE", "").strip()
                    if env_mode:
                        mode, source = env_mode.lower(), "process-env"
                    else:
                        mode, source = DEFAULT_MCP_MODE, "default"

    if mode not in MCP_MODE_PACKAGE:
        raise SetupWithAIError(
            f"Unknown LEX_MCP_MODE {mode!r} (resolved from {source}); "
            f"expected one of: {', '.join(sorted(MCP_MODE_PACKAGE))}."
        )
    return mode, source


# ---------------------------------------------------------------------------
# Source directory resolution
# ---------------------------------------------------------------------------


def _resolve_package_directory(
    python_executable: Path,
    package_name: str,
    directory_name: str,
) -> Path | None:
    """Return ``<installed_package>/<directory_name>`` as seen by *python_executable*.

    Returns ``None`` when the package is not installed or the directory does
    not exist. Avoids importing the package in the current process so the
    inactive mode stays inert.
    """
    script = (
        "import importlib.util, os, sys; "
        f"spec = importlib.util.find_spec({package_name!r}); "
        "locations = list(spec.submodule_search_locations or []) if spec else []; "
        "location = locations[0] if locations else (os.path.dirname(spec.origin) if spec and spec.origin else ''); "
        "sys.stdout.write(location)"
    )
    try:
        result = subprocess.run(
            [str(python_executable), "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    package_root = result.stdout.strip()
    if not package_root:
        return None

    candidate = Path(package_root).resolve() / directory_name
    return candidate if candidate.is_dir() else None


# ---------------------------------------------------------------------------
# File-level verification
# ---------------------------------------------------------------------------


def _iter_source_files(source_directory: Path) -> Iterable[Path]:
    for current_root, _dirs, files in os.walk(source_directory):
        current_root_path = Path(current_root)
        for file_name in files:
            yield current_root_path / file_name


def _files_match(source_file: Path, destination_file: Path) -> bool:
    if not destination_file.is_file():
        return False
    try:
        # ``shallow=False`` forces a content comparison.
        return filecmp.cmp(source_file, destination_file, shallow=False)
    except OSError:
        return False


def _restore_file(source_file: Path, destination_file: Path) -> None:
    destination_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source_file, destination_file)
    except OSError as exc:
        raise SetupWithAIError(
            f"Could not restore {destination_file} from {source_file}: {exc}"
        ) from exc


def _prune_managed_files(
    source_directory: Path,
    destination_directory: Path,
    *,
    managed_relative_dirs: tuple[str, ...],
) -> tuple[Path, ...]:
    """Delete stale files in managed subdirectories that are absent in source.

    This is used for mode-scoped AI assets (for example ``.github/agents``),
    where leaving old files in place causes mixed-mode tool surfaces.
    """
    removed: list[Path] = []

    for relative_dir in managed_relative_dirs:
        source_root = source_directory / relative_dir
        destination_root = destination_directory / relative_dir

        if not destination_root.exists():
            continue

        source_relative_files: set[Path] = set()
        if source_root.is_dir():
            for source_file in _iter_source_files(source_root):
                source_relative_files.add(source_file.relative_to(source_directory))

        for current_root, _dirs, files in os.walk(destination_root):
            current_root_path = Path(current_root)
            for file_name in files:
                destination_file = current_root_path / file_name
                destination_relative = destination_file.relative_to(destination_directory)
                if destination_relative in source_relative_files:
                    continue
                try:
                    destination_file.unlink()
                    removed.append(destination_relative)
                except OSError as exc:
                    raise SetupWithAIError(
                        f"Could not remove stale mode asset {destination_file}: {exc}"
                    ) from exc

        # Best-effort cleanup of empty directories left behind.
        for current_root, _dirs, _files in os.walk(destination_root, topdown=False):
            current_root_path = Path(current_root)
            if current_root_path == destination_root:
                continue
            try:
                current_root_path.rmdir()
            except OSError:
                continue

    return tuple(removed)


def verify_directory(
    project_root: Path,
    source_directory: Path | None,
    directory_name: str,
    *,
    skipped_reason: str | None = None,
    display_name: str | None = None,
    prune_extra_relative_dirs: tuple[str, ...] = (),
) -> DirectoryVerificationResult:
    """Verify (and restore) every file inside *source_directory* under *project_root*."""
    destination_directory = Path(project_root).resolve() / directory_name
    label = display_name or directory_name

    if skipped_reason is not None:
        return DirectoryVerificationResult(
            directory_name=label,
            source_directory=source_directory,
            destination_directory=destination_directory,
            skipped_reason=skipped_reason,
        )

    if source_directory is None or not source_directory.is_dir():
        return DirectoryVerificationResult(
            directory_name=label,
            source_directory=source_directory,
            destination_directory=destination_directory,
            skipped_reason=(
                f"Source directory '{directory_name}' is not available in the "
                "installed package; skipping verification."
            ),
        )

    source_directory = source_directory.resolve()
    missing: list[Path] = []
    modified: list[Path] = []
    restored: list[Path] = []

    for source_file in _iter_source_files(source_directory):
        relative_path = source_file.relative_to(source_directory)
        destination_file = destination_directory / relative_path

        if not destination_file.exists():
            missing.append(relative_path)
        elif not _files_match(source_file, destination_file):
            modified.append(relative_path)
        else:
            continue

        _restore_file(source_file, destination_file)
        restored.append(relative_path)

    removed = ()
    if prune_extra_relative_dirs:
        removed = _prune_managed_files(
            source_directory,
            destination_directory,
            managed_relative_dirs=prune_extra_relative_dirs,
        )

    return DirectoryVerificationResult(
        directory_name=label,
        source_directory=source_directory,
        destination_directory=destination_directory,
        restored_files=tuple(restored),
        removed_files=removed,
        missing_files=tuple(missing),
        modified_files=tuple(modified),
    )

# Subdirectories in ``.github`` that are mode-owned. During mode switch, these
# must be mirrored exactly to avoid carrying stale assets from the previous mode.
MODE_MANAGED_GITHUB_SUBDIRS: tuple[str, ...] = (
    "agents",
    "instructions",
    "prompts",
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _modes_to_verify(mode: str) -> tuple[str, ...]:
    if mode == "all":
        return ALL_MCP_MODES
    return (mode,)


def _sync_agentic_environments(
    project_root: Path,
    mode: str,
    environments: Iterable[str] | None,
) -> tuple[tuple[EnvironmentVerificationResult, ...], str | None, bool]:
    """Materialise the mode payload for every agentic environment in use.

    Delegates to ``lex_mcp.ai_onboarding``, which owns the per-environment
    renderers (Copilot's ``.github`` tree, Claude Code's ``.claude`` tree,
    Cursor's ``.cursor`` tree, Codex's ``AGENTS.md``, …) so new environments
    ship with a lex-mcp-local release and need no lex-app change.

    Returns ``(results, error, handled)``. ``handled`` tells the caller that
    asset delivery is fully owned by this phase, so the legacy per-mode
    directory walk below must be skipped — otherwise a project that opted out
    of the Copilot surfaces would still get an unmanaged ``.github`` tree
    written into it.
    """
    try:
        from lex_mcp.ai_onboarding import (  # type: ignore[import-not-found]
            read_enabled_environments,
            sync_project_payloads,
        )
    except Exception:
        # lex-mcp-local predates the environment registry (or is not
        # installed): fall back to the legacy .github mirror entirely. This is
        # an expected downgrade path, not a verification failure.
        return (), None, False

    keys = tuple(environments) if environments else read_enabled_environments(
        project_root
    )
    try:
        results = sync_project_payloads(
            project_root, mode=mode, environments=keys
        )
    except Exception as exc:
        return (), f"environment payload sync failed: {exc}", False

    converted = tuple(
        EnvironmentVerificationResult(
            environment=result.environment,
            display_name=result.display_name,
            dialect=result.dialect,
            written=tuple(result.written),
            pruned=tuple(result.pruned),
            unchanged_count=len(result.unchanged),
            errors=tuple(result.errors),
        )
        for result in results
    )
    handled = bool(results) and all(result.ok for result in results)
    return converted, None, handled


def verify_ai_assets(
    project_root: Path,
    *,
    mode: str | None = None,
    python_executable: Path | None = None,
    env: Mapping[str, str] | None = None,
    docs_directory_names: tuple[str, ...] = LEX_APP_EMBEDDED_DIRECTORY_NAMES,
    mode_directory_names: tuple[str, ...] = MCP_MODE_DIRECTORIES,
    align_mcp_mode: bool = False,
    mcp_config_path: Path | None = None,
    mode_align_source_tool: str = "lex-ai-verify",
    sync_environments: bool = True,
    environments: Iterable[str] | None = None,
) -> VerifyAIAssetsResult:
    """Verify every asset directory required by the active MCP mode.

    Parameters
    ----------
    project_root:
        Root of the consumer project.
    mode:
        ``"forward"``, ``"backward"``, ``"all"``, or ``None`` to auto-resolve
        from the environment / project config.
    python_executable:
        Interpreter used to locate installed packages. Defaults to the active
        virtual environment's interpreter.
    align_mcp_mode:
        When *True*, treat ``<project_root>/.env``'s ``LEX_MCP_MODE`` as the
        source of truth. If the persisted MCP runtime view (override file or
        ``mcp.json``) disagrees, invoke the equivalent of the in-server
        ``switch_to_mode`` MCP tool to align them before verifying assets.
        Disabled by default to preserve the lightweight pre-flight behaviour.
    mcp_config_path:
        Path to the IDE ``mcp.json`` used for mode alignment. When *None*,
        the GitHub Copilot config is auto-resolved.
    sync_environments:
        When *True* (default), also materialise the mode's agent payload in
        every agentic environment the project targets (Copilot, Claude Code,
        Cursor, Codex, Windsurf, …) via ``lex_mcp.ai_onboarding``.
    environments:
        Explicit environment keys to sync. Defaults to the project's
        ``LEX_AI_ENVIRONMENTS`` value, then to what the project already has
        on disk.
    """
    project_root_resolved = Path(project_root).resolve()

    # ── Optional: align MCP runtime mode with project .env ────────────────
    # Treat the project's .env ``LEX_MCP_MODE`` as the source of truth. If
    # the runtime (override file or mcp.json) disagrees, run the
    # ``switch_to_mode`` primitives so the running MCP server is told to
    # adopt the .env mode before we restore assets for it.
    if align_mcp_mode and mode != "all":
        try:
            from lex.tools.mcp_mode_invoke import invoke_switch_to_mode
            from lex.tools.setup_with_ai import (
                resolve_github_copilot_mcp_config_path,
            )

            env_mode = _read_env_file_value(
                project_root_resolved / ".env", "LEX_MCP_MODE"
            )
            env_mode = (env_mode or "").strip().lower() or None
            if env_mode and env_mode in MCP_MODE_PACKAGE:
                resolved_mcp_path = (
                    Path(mcp_config_path).resolve()
                    if mcp_config_path is not None
                    else resolve_github_copilot_mcp_config_path()
                )
                runtime_mode = (
                    _read_override_mode()
                    or _read_mode_from_mcp_json(resolved_mcp_path)
                    or _resolve_mode_from_mcp_json_files(project_root_resolved)
                )
                if runtime_mode != env_mode:
                    invoke_switch_to_mode(
                        env_mode,
                        project_root=project_root_resolved,
                        mcp_config_path=resolved_mcp_path,
                        source_tool=mode_align_source_tool,
                        reason="aligning runtime mode with project .env",
                    )
                if mode is None:
                    mode = env_mode
        except Exception:
            # Best-effort — alignment failures must never block verification.
            # If the Copilot mcp.json path cannot be resolved, the mode
            # cannot be aligned, but we can still verify assets for
            # whatever mode resolve_active_mcp_mode picks up.
            pass

    if mode == "all":
        active_mode, mode_source = "all", "cli"
    else:
        active_mode, mode_source = resolve_active_mcp_mode(
            project_root_resolved,
            explicit_mode=mode,
            env=env,
        )

    python_path = (
        resolve_active_python_executable(project_root_resolved, env=env)
        if python_executable is None
        else Path(os.path.abspath(python_executable))
    )

    results: list[DirectoryVerificationResult] = []

    # ── Agentic environments ──────────────────────────────────────────────
    # Every environment the project targets gets the active mode's payload in
    # its own native layout. For Copilot surfaces that is a byte-mirror of the
    # package ``.github`` tree; for the others it is a rendered translation.
    # When this phase succeeds it owns asset delivery outright, so the legacy
    # per-mode walk below is skipped.
    environment_results: tuple[EnvironmentVerificationResult, ...] = ()
    environment_error: str | None = None
    environments_handled = False
    if sync_environments and active_mode != "all":
        (
            environment_results,
            environment_error,
            environments_handled,
        ) = _sync_agentic_environments(
            project_root_resolved, active_mode, environments
        )
        for environment_result in environment_results:
            if environment_result.written or environment_result.pruned:
                results.append(
                    DirectoryVerificationResult(
                        directory_name=(
                            f"{environment_result.display_name} "
                            f"[{environment_result.dialect}]"
                        ),
                        source_directory=None,
                        destination_directory=project_root_resolved,
                        restored_files=tuple(
                            Path(p) for p in environment_result.written
                        ),
                        removed_files=tuple(
                            Path(p) for p in environment_result.pruned
                        ),
                    )
                )

    # Mode-specific directories (currently ``.github`` per mode). Skipped
    # entirely once the environment phase succeeded: it already delivered the
    # right tree to the right places, and running this walk as well would push
    # a .github payload into projects that use none of the Copilot surfaces.
    for mode_name in _modes_to_verify(active_mode):
        if environments_handled and set(mode_directory_names) == {".github"}:
            break
        package_name = MCP_MODE_PACKAGE[mode_name]
        for directory_name in mode_directory_names:
            display_name = (
                f"{directory_name} [{mode_name}]"
                if active_mode == "all"
                else directory_name
            )
            source = _resolve_package_directory(
                python_path, package_name, directory_name
            )
            if source is None:
                results.append(
                    verify_directory(
                        project_root_resolved,
                        None,
                        directory_name,
                        display_name=display_name,
                        skipped_reason=(
                            f"Package '{package_name}' (mode '{mode_name}') is not "
                            f"installed in {python_path}; cannot verify "
                            f"'{directory_name}'."
                        ),
                    )
                )
                continue
            results.append(
                verify_directory(
                    project_root_resolved,
                    source,
                    directory_name,
                    display_name=display_name,
                    prune_extra_relative_dirs=(
                        MODE_MANAGED_GITHUB_SUBDIRS
                        if directory_name == ".github" and active_mode != "all"
                        else ()
                    ),
                )
            )

    # ``docs`` (and any other directories shipped by lex itself) — every mode.
    lex_package_root = resolve_lex_app_package_root(python_path)
    for name in docs_directory_names:
        source = (
            (lex_package_root / name).resolve()
            if lex_package_root is not None and (lex_package_root / name).is_dir()
            else None
        )
        # Don't restore into the lex package's own checkout (avoids self-copy).
        destination = project_root_resolved / name
        if source is not None and source.resolve() == destination.resolve():
            results.append(
                DirectoryVerificationResult(
                    directory_name=name,
                    source_directory=source,
                    destination_directory=destination,
                    skipped_reason=(
                        f"Source directory '{name}' is the destination; "
                        "skipping self-copy."
                    ),
                )
            )
            continue
        results.append(verify_directory(project_root_resolved, source, name))

    return VerifyAIAssetsResult(
        project_root=project_root_resolved,
        mode=active_mode,
        mode_source=mode_source,
        directories=tuple(results),
        environments=environment_results,
        environment_sync_error=environment_error,
    )
