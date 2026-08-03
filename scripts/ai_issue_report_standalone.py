#!/usr/bin/env python3
"""
Standalone AI issue report collector.

This script has no project-library dependencies. Share this single file and run:

    python ai_issue_report_standalone.py

It creates a ZIP in the current directory with raw Copilot/MCP artifacts,
plus manifest and inventory files for triage.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile


@dataclass(frozen=True)
class ReportResult:
    archive_path: Path
    copied_files: int
    missing_sources: tuple[str, ...]
    collection_errors: tuple[str, ...]


def utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_mcp_config_path() -> Path:
    home = Path.home()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base_dir = Path(local_app_data) if local_app_data else (home / "AppData" / "Local")
        return base_dir / "github-copilot" / "intellij" / "mcp.json"
    return home / ".config" / "github-copilot" / "intellij" / "mcp.json"


def resolve_state_db_path(mcp_config_path: Path | None = None) -> Path:
    if mcp_config_path is not None:
        return mcp_config_path.resolve().parent.parent / "copilot-intellij.db"
    home = Path.home()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base_dir = Path(local_app_data) if local_app_data else (home / "AppData" / "Local")
        return base_dir / "github-copilot" / "copilot-intellij.db"
    return home / ".config" / "github-copilot" / "copilot-intellij.db"


def iter_vscode_workspace_storage_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = []

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        app_data = os.environ.get("APPDATA")
        candidates = [
            Path(local_app_data) / "Code" / "User" if local_app_data else home / "AppData" / "Local" / "Code" / "User",
            Path(app_data) / "Code" / "User" if app_data else home / "AppData" / "Roaming" / "Code" / "User",
            Path(local_app_data) / "Programs" / "Microsoft VS Code" / "resources" if local_app_data else None,
        ]
    elif sys.platform == "darwin":
        candidates = [
            home / "Library" / "Application Support" / "Code" / "User",
            home / "Library" / "Application Support" / "Code - Insiders" / "User",
        ]
    else:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        config_home = Path(xdg_config_home) if xdg_config_home else (home / ".config")
        candidates = [
            config_home / "Code" / "User",
            config_home / "Code - Insiders" / "User",
            config_home / "VSCodium" / "User",
        ]

    for candidate in candidates:
        if candidate is None:
            continue
        workspace_storage = candidate / "workspaceStorage"
        if workspace_storage.is_dir():
            roots.append(workspace_storage)

    return roots


def iter_copilot_chat_roots() -> list[Path]:
    roots: list[Path] = []
    for workspace_storage_root in iter_vscode_workspace_storage_roots():
        for workspace_dir in sorted(workspace_storage_root.iterdir()):
            if not workspace_dir.is_dir():
                continue
            chat_root = workspace_dir / "GitHub.copilot-chat"
            if chat_root.exists():
                roots.append(chat_root)
    return roots


def iter_sources(project_root: Path) -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []

    mcp_config_path = resolve_mcp_config_path()
    state_db_path = resolve_state_db_path(mcp_config_path)

    sources.append(("project_env", project_root / ".env"))
    sources.append(("github_copilot_mcp_json", mcp_config_path))
    sources.append(("github_copilot_state_db", state_db_path))

    copilot_root = mcp_config_path.resolve().parent.parent
    sources.append(("github_copilot_root", copilot_root))

    chat_roots = iter_copilot_chat_roots()
    for index, chat_root in enumerate(chat_roots, start=1):
        sources.append((f"vscode_copilot_{index}", chat_root))

    workspace_vscode = project_root / ".vscode"
    if workspace_vscode.exists():
        sources.append(("workspace_vscode", workspace_vscode))

    return sources


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 128)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def gather_files_for_source(label: str, source_path: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
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


def build_report(
    project_root: Path,
    output_zip: Path,
    artifact_mode: str,
) -> ReportResult:
    normalized_mode = artifact_mode.strip().lower()
    if normalized_mode not in {"auto", "off", "strict"}:
        raise ValueError(f"Unsupported artifact mode: {artifact_mode!r}")

    output_zip = output_zip.expanduser().resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    copied_files = 0
    missing_sources: list[str] = []
    collection_errors: list[str] = []
    inventory: list[dict[str, object]] = []

    sources: list[tuple[str, Path]] = []
    if normalized_mode != "off":
        sources = iter_sources(project_root)

    with zipfile.ZipFile(output_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        if normalized_mode != "off":
            for label, source_path in sources:
                files, source_errors = gather_files_for_source(label, source_path)
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
                        digest = file_sha256(source_file)
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
            "project_root": str(project_root),
            "artifact_mode": normalized_mode,
            "copied_files": copied_files,
            "missing_sources": missing_sources,
            "collection_errors": collection_errors,
            "sources": [{"label": label, "path": str(path)} for label, path in sources],
        }

        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        archive.writestr("inventory.json", json.dumps(inventory, indent=2) + "\n")

    if normalized_mode == "strict" and copied_files == 0:
        raise RuntimeError("Strict artifact mode requested, but no Copilot raw artifact files were captured.")

    return ReportResult(
        archive_path=output_zip,
        copied_files=copied_files,
        missing_sources=tuple(missing_sources),
        collection_errors=tuple(collection_errors),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a raw AI issue report ZIP with Copilot/MCP artifacts "
            "for support triage."
        )
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root to collect .env/.vscode from (default: current directory)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output ZIP path (default: ./ai_issue_report_<UTC timestamp>.zip)",
    )
    parser.add_argument(
        "--artifact-mode",
        default="auto",
        choices=["auto", "off", "strict"],
        help="auto=best effort, off=skip artifacts, strict=require at least one artifact",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt about raw secret/private artifact inclusion",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()

    if args.output:
        output_zip = Path(args.output)
    else:
        output_zip = Path.cwd() / f"ai_issue_report_{utc_now_compact()}.zip"

    if not args.yes:
        print("WARNING: this report may include raw secrets/private artifacts.")
        answer = input("Continue and generate the report? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Aborted by user.")
            return 1

    try:
        result = build_report(
            project_root=project_root,
            output_zip=output_zip,
            artifact_mode=args.artifact_mode,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"UNEXPECTED ERROR: {exc}")
        return 3

    print(f"AI issue report written: {result.archive_path}")
    print(f"Captured files: {result.copied_files}")
    if result.missing_sources:
        print(f"Missing sources: {len(result.missing_sources)}")
    if result.collection_errors:
        print(f"Collection errors: {len(result.collection_errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
