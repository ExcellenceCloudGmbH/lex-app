"""Verify and restore the static asset directories that the LEX MCP server
depends on.

The LEX MCP runtime ships in two flavors inside the ``lex-mcp-local``
distribution:

* **forward** mode — driven by :mod:`lex_mcp_local`, ships
  ``src/lex_mcp_local/.github`` (new-project creation workflow).
* **backward** mode — driven by :mod:`lex_mcp_reverse`, ships
  ``src/lex_mcp_reverse/.github`` (existing-project documentation/migration).

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
    "mvp_generator": "lex_mcp_mvp_generator",
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
class VerifyAIAssetsResult:
    """Aggregated outcome across every directory the verifier inspected."""

    project_root: Path
    mode: str
    mode_source: str
    directories: tuple[DirectoryVerificationResult, ...] = field(default_factory=tuple)

    @property
    def restored_files(self) -> tuple[Path, ...]:
        return tuple(p for d in self.directories for p in d.restored_files)

    @property
    def removed_files(self) -> tuple[Path, ...]:
        return tuple(p for d in self.directories for p in d.removed_files)

    @property
    def ok(self) -> bool:
        return all(d.ok for d in self.directories)


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


def _resolve_mode_from_mcp_json_files(project_root: Path) -> str | None:
    """Scan CWD-relative mcp.json files for the active mode.

    Checks the same locations that lex-mcp-local's
    ``_candidate_mcp_json_paths()`` scans in the working directory:
    ``mcp.json``, ``.vscode/mcp.json``, ``.cursor/mcp.json``,
    ``.idea/mcp.json``, ``.pycharm/mcp.json``.
    Returns the first valid mode found, or *None*.
    """
    root = Path(project_root).resolve()
    candidates = [
        root / "mcp.json",
        root / ".vscode" / "mcp.json",
        root / ".cursor" / "mcp.json",
        root / ".idea" / "mcp.json",
        root / ".pycharm" / "mcp.json",
    ]
    for path in candidates:
        mode = _read_mode_from_mcp_json(path)
        if mode:
            return mode
    return None


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


def verify_ai_assets(
    project_root: Path,
    *,
    mode: str | None = None,
    python_executable: Path | None = None,
    env: Mapping[str, str] | None = None,
    docs_directory_names: tuple[str, ...] = LEX_APP_EMBEDDED_DIRECTORY_NAMES,
    mode_directory_names: tuple[str, ...] = MCP_MODE_DIRECTORIES,
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
    """
    project_root_resolved = Path(project_root).resolve()

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

    # Mode-specific directories (currently ``.github`` per mode).
    for mode_name in _modes_to_verify(active_mode):
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
    )
