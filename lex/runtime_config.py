from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from lex.tools.project_root import find_project_root

logger = logging.getLogger(__name__)


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


def _normalise_origin(candidate: str) -> str | None:
    """Return ``candidate`` as ``scheme://host[:port]``, or ``None`` if it is not
    a bare http(s) origin (no path, query, fragment, credentials or wildcard)."""
    if "*" in candidate:
        return None

    try:
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in ("http", "https"):
            return None
        if not parts.hostname:
            return None
        if parts.path or parts.query or parts.fragment:
            return None
        if parts.username or parts.password:
            return None
        port = parts.port  # raises ValueError on a non-numeric port
    except ValueError:
        return None

    origin = f"{parts.scheme.lower()}://{parts.hostname.lower()}"
    if port is not None:
        origin += f":{port}"
    return origin


def parse_embed_allowed_origins(raw: str | None) -> list[str]:
    """Parse ``LEX_EMBED_ALLOWED_ORIGINS`` into a list of normalised origins.

    ``raw`` is the env var's raw comma-separated value. ``None`` or blank means
    unset, which returns an empty list -- the documented "off" state in which
    embedding/CSP behaviour is unchanged and the frontend's filter bridge
    channel stays disabled.

    Each non-empty entry must be exactly ``scheme://host[:port]`` with an
    ``http`` or ``https`` scheme and no path, query, fragment, credentials or
    wildcard. Valid entries are normalised (scheme and host lower-cased) and
    returned in order, with exact duplicates collapsed. An entry that fails
    validation is dropped rather than widening what the setting allows; every
    dropped entry is named together in a single WARNING log line so a typo
    is visible without crashing startup.

    A blank segment from a stray or trailing comma (``"a,,b"`` / ``"a,"``) is
    silently skipped -- it is a formatting artifact, not a malformed origin.
    """
    if not raw or not raw.strip():
        return []

    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()

    for entry in raw.split(","):
        candidate = entry.strip()
        if not candidate:
            continue

        normalised = _normalise_origin(candidate)
        if normalised is None:
            invalid.append(candidate)
            continue

        if normalised not in seen:
            seen.add(normalised)
            valid.append(normalised)

    if invalid:
        logger.warning(
            "LEX_EMBED_ALLOWED_ORIGINS: dropping invalid origin(s): %s",
            ", ".join(invalid),
        )

    return valid


def merge_frame_ancestors(existing_csp: str, directive: str) -> str:
    """Merge a ``frame-ancestors`` directive into an existing CSP header value.

    Shared by every place that adds the embed allow-list's CSP directive --
    ``EmbedXFrameOptionsMiddleware`` (for deployments that register it) and
    ``serve_react`` (whose own HTML documents are ``@xframe_options_exempt``,
    so they are not covered by the middleware at all, registered or not). Both
    need to add ``frame-ancestors`` without disturbing whatever else already
    set a ``Content-Security-Policy`` header on that response.

    Replaces an existing ``frame-ancestors`` directive in place if present,
    otherwise appends the new directive. Every other directive is preserved
    verbatim, in its original order.
    """
    if not existing_csp:
        return directive

    parts = [p.strip() for p in existing_csp.split(";") if p.strip()]
    for i, part in enumerate(parts):
        if part.lower().startswith("frame-ancestors"):
            parts[i] = directive
            break
    else:
        parts.append(directive)
    return "; ".join(parts)
