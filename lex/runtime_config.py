from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from lex.tools.project_root import find_project_root


def derive_repo_name(project_root: str | os.PathLike[str] | None, cwd: str | os.PathLike[str] | None = None) -> str:
    raw_root = project_root or cwd or os.getcwd()
    normalized = str(raw_root).replace("\\", "/")
    path = Path(normalized).expanduser()
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path

    if resolved.name:
        return resolved.name

    normalized = normalized.rstrip("/")
    return normalized.rsplit("/", 1)[-1]


def resolve_project_root(
    project_root: str | os.PathLike[str] | None,
    cwd: str | os.PathLike[str] | None = None,
) -> Path:
    start = project_root or cwd or os.getcwd()
    return Path(find_project_root(start)).expanduser().resolve()


def resolve_local_sqlite_path(
    project_root: str | os.PathLike[str] | None,
    repo_name: str | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> Path:
    resolved_project_root = resolve_project_root(project_root, cwd)
    effective_repo_name = repo_name or derive_repo_name(resolved_project_root)
    return resolved_project_root / f"{effective_repo_name}.sqlite3"


def format_db_connection_unicode_error(
    exc: UnicodeDecodeError,
    database_settings: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> str:
    env = environ or os.environ
    current_os_name = os_name or os.name
    db_engine = database_settings.get("ENGINE", "<unknown>")
    db_name = database_settings.get("NAME", "<unknown>")
    db_host = database_settings.get("HOST", "<unknown>")
    db_port = database_settings.get("PORT", "<unknown>")
    db_user = database_settings.get("USER", "<unknown>")
    deployment_target = env.get("DATABASE_DEPLOYMENT_TARGET", "default")

    lines = [
        "Database connection failed while psycopg2/libpq was decoding PostgreSQL connection settings.",
        f"Underlying error: {exc}",
        f"Effective database config: ENGINE={db_engine}, NAME={db_name}, HOST={db_host}, PORT={db_port}, USER={db_user}",
        f"DATABASE_DEPLOYMENT_TARGET={deployment_target}",
        "Check whether any PostgreSQL credential source contains non-UTF-8 bytes.",
    ]

    if current_os_name == "nt":
        appdata = env.get("APPDATA", r"%APPDATA%")
        lines.extend(
            [
                "On Windows, inspect PostgreSQL credential files and env overrides:",
                f"  - {appdata}\\postgresql\\pgpass.conf",
                f"  - {appdata}\\postgresql\\pg_service.conf",
                "  - PGPASSFILE / PGSERVICEFILE / PGPASSWORD / PGSERVICE",
                "If you intended a local SQLite init, run with DATABASE_DEPLOYMENT_TARGET=local.",
            ]
        )

    return "\n".join(lines)
