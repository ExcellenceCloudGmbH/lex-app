"""Raw AI issue report bundle generation for Lex AI/MCP support triage.

The report intentionally stores raw artifacts (without parsing) so support can
inspect the exact bytes that existed on the user's machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

from lex.tools.setup_with_ai import (
    resolve_github_copilot_mcp_config_path,
    resolve_github_copilot_state_db_path,
)


@dataclass(frozen=True)
class AIIssueReportResult:
    """Summary of a generated issue report bundle."""

    archive_path: Path
    copied_files: int
    missing_sources: tuple[str, ...]
    collection_errors: tuple[str, ...]


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_output_path(project_root: Path, output: Path | None) -> Path:
    if output is not None:
        return Path(output).expanduser().resolve()
    return (project_root / ".lex-ai-reports" / f"ai_issue_report_{_utc_now_compact()}.zip").resolve()


def _iter_vscode_copilot_dirs(project_root: Path) -> list[Path]:
    """Return known VS Code Copilot artifact roots.

    Includes user-level workspace storage and workspace-local `.vscode` config.
    """
    candidates: list[Path] = []

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        code_user = base / "Code" / "User"
    else:
        code_user = Path.home() / ".config" / "Code" / "User"

    workspace_storage = code_user / "workspaceStorage"
    if workspace_storage.is_dir():
        for workspace in sorted(workspace_storage.iterdir()):
            chat_root = workspace / "GitHub.copilot-chat"
            if chat_root.exists():
                candidates.append(chat_root)

    workspace_vscode = project_root / ".vscode"
    if workspace_vscode.exists():
        candidates.append(workspace_vscode)

    return candidates


def _iter_copilot_raw_sources(project_root: Path) -> list[tuple[str, Path]]:
    """Return raw artifact sources as (label, source_path)."""
    sources: list[tuple[str, Path]] = []

    mcp_config_path = resolve_github_copilot_mcp_config_path()
    state_db_path = resolve_github_copilot_state_db_path(mcp_config_path)

    sources.append(("project_env", project_root / ".env"))
    sources.append(("github_copilot_mcp_json", mcp_config_path))
    sources.append(("github_copilot_state_db", state_db_path))

    # LOCALAPPDATA\\github-copilot\\<ide-code> on Windows, or ~/.config variant.
    copilot_root = mcp_config_path.resolve().parent.parent
    sources.append(("github_copilot_root", copilot_root))

    for index, vscode_dir in enumerate(_iter_vscode_copilot_dirs(project_root), start=1):
        sources.append((f"vscode_copilot_{index}", vscode_dir))

    return sources


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 128)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _gather_files_for_source(label: str, source_path: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    """Collect files for one source as (source_file, archive_relative_path)."""
    files: list[tuple[Path, Path]] = []
    errors: list[str] = []

    if source_path.is_file():
        files.append((source_path, Path("raw") / label / source_path.name))
        return files, errors

    if source_path.is_dir():
        for file_path in sorted(source_path.rglob("*")):
            if not file_path.is_file():
                continue
            try:
                relative = file_path.relative_to(source_path)
            except ValueError:
                relative = Path(file_path.name)
            files.append((file_path, Path("raw") / label / relative))
        return files, errors

    errors.append(f"{label}: not found ({source_path})")
    return files, errors


def create_ai_issue_report(
    *,
    project_root: Path,
    output: Path | None = None,
    artifact_mode: str = "auto",
) -> AIIssueReportResult:
    """Create a raw artifact ZIP report for AI/MCP support.

    artifact_mode:
      - ``auto``: best-effort collection
      - ``off``: skip Copilot raw artifact capture
      - ``strict``: require at least one Copilot raw source to be captured
    """
    normalized_mode = artifact_mode.strip().lower()
    if normalized_mode not in {"auto", "off", "strict"}:
        raise ValueError(f"Unsupported artifact mode: {artifact_mode!r}")

    root = Path(project_root).expanduser().resolve()
    archive_path = _resolve_output_path(root, output)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    copied_files = 0
    missing_sources: list[str] = []
    collection_errors: list[str] = []
    inventory: list[dict[str, object]] = []

    sources: list[tuple[str, Path]] = []
    if normalized_mode != "off":
        sources = _iter_copilot_raw_sources(root)

    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        if normalized_mode != "off":
            for label, source_path in sources:
                files, source_errors = _gather_files_for_source(label, source_path)
                missing_sources.extend(source_errors)

                for source_file, archive_rel in files:
                    try:
                        archive.write(source_file, arcname=archive_rel.as_posix())
                    except OSError as exc:
                        collection_errors.append(
                            f"{label}: failed to add {source_file} ({exc})"
                        )
                        continue

                    copied_files += 1
                    try:
                        stat = source_file.stat()
                        digest = _file_sha256(source_file)
                    except OSError as exc:
                        collection_errors.append(
                            f"{label}: failed to stat/hash {source_file} ({exc})"
                        )
                        continue

                    inventory.append(
                        {
                            "label": label,
                            "source_path": str(source_file),
                            "archive_path": archive_rel.as_posix(),
                            "size_bytes": stat.st_size,
                            "mtime_epoch": stat.st_mtime,
                            "sha256": digest,
                        }
                    )

        manifest = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "project_root": str(root),
            "artifact_mode": normalized_mode,
            "copied_files": copied_files,
            "missing_sources": missing_sources,
            "collection_errors": collection_errors,
            "sources": [
                {
                    "label": label,
                    "path": str(path),
                }
                for label, path in sources
            ],
        }

        archive.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        archive.writestr(
            "inventory.json",
            json.dumps(inventory, indent=2, sort_keys=False) + "\n",
        )

    if normalized_mode == "strict" and copied_files == 0:
        raise RuntimeError(
            "Strict artifact mode requested, but no Copilot raw artifact files were captured."
        )

    return AIIssueReportResult(
        archive_path=archive_path,
        copied_files=copied_files,
        missing_sources=tuple(missing_sources),
        collection_errors=tuple(collection_errors),
    )
