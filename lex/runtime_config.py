from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from lex.tools.project_root import find_project_root

logger = logging.getLogger(__name__)

#: RFC-1123-ish hostname label grammar, lowercase only (the host is lower-cased
#: before this runs): labels are alphanumeric, may contain internal hyphens, and
#: never start or end with one. Deliberately stricter than "whatever urlsplit
#: accepted" -- see ``_normalise_origin``.
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$"
)

#: The browser drops these from ``location.origin`` / ``MessageEvent.origin``;
#: an origin written with its scheme's default port must normalise to the same
#: string as one written without it, or two spellings of the same origin would
#: both appear in the CSP directive and never match the one the browser sends.
_DEFAULT_PORT = {"http": 80, "https": 443}


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
    a bare http(s) origin.

    ``urlsplit`` alone is not enough: it happily parses a non-ASCII host (which
    then either breaks the HTTP header or silently drops the whole directive)
    and a bracketed host containing arbitrary bytes -- including a space, which
    lets ``https://[v1.x https:]`` inject a second, bare ``https:`` CSP source
    token once joined into a directive with others. So, in order:

    1. Reject a wildcard or any bracketed host (an IPv6 literal or the
       IPvFuture ``[v1...]`` form) outright -- a single legitimate origin is
       never written with either.
    2. Scheme must be ``http``/``https``; no credentials, query or fragment;
       the path must be empty or exactly ``/`` (the origin of ``https://x/``
       is ``https://x`` -- anything past that single slash is a real path).
    3. The host is IDNA-encoded (``müller.de`` -> ``xn--mller-kva.de``) --
       this is what makes an internationalised domain safe to put in a header
       at all. A host stringprep/IDNA refuses (control characters, an
       over-length label, ...) raises ``UnicodeError`` here and is dropped.
    4. The IDNA result must still match a strict ASCII hostname grammar
       (``_HOSTNAME_RE``). IDNA's nameprep step does not reject every
       CSP-meaningful character -- a semicolon, space, quote, backslash or
       percent-encoding all pass IDNA unchanged -- so this is the step that
       actually blocks them from reaching a header.
    5. The scheme's default port (``:443`` for https, ``:80`` for http) is
       dropped, so the result matches the form a browser's own
       ``location.origin`` / ``MessageEvent.origin`` takes.
    """
    if "*" in candidate or "[" in candidate or "]" in candidate:
        return None

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORT:
        return None
    if parts.username or parts.password:
        return None
    if parts.query or parts.fragment:
        return None
    if parts.path not in ("", "/"):
        return None

    hostname = parts.hostname
    if not hostname:
        return None

    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None

    if not _HOSTNAME_RE.match(host):
        return None

    try:
        port = parts.port  # raises ValueError on a non-numeric port
    except ValueError:
        return None

    origin = f"{scheme}://{host}"
    if port is not None and port != _DEFAULT_PORT[scheme]:
        origin += f":{port}"
    return origin


def embed_origins_configured(raw: str | None) -> bool:
    """Whether ``LEX_EMBED_ALLOWED_ORIGINS`` was set to something non-blank.

    This is deliberately a different question from "did
    ``parse_embed_allowed_origins(raw)`` return anything" -- an operator who
    sets the variable to only invalid entries (a wildcard, say) gets ``True``
    here and ``[]`` there, and that combination must fail closed (restrict
    framing to the app itself) rather than silently behaving as if the
    setting were never touched at all. See ``parse_embed_allowed_origins``.
    """
    return bool(raw and raw.strip())


def parse_embed_allowed_origins(raw: str | None) -> list[str]:
    """Parse ``LEX_EMBED_ALLOWED_ORIGINS`` into a list of normalised origins.

    ``raw`` is the env var's raw comma-separated value. ``None`` or blank means
    unset, which returns an empty list -- the documented "off" state in which
    embedding/CSP behaviour is unchanged and the frontend's filter bridge
    channel stays disabled.

    Each non-empty entry must be exactly ``scheme://host[:port]`` with an
    ``http`` or ``https`` scheme and no path (beyond an optional trailing
    ``/``), query, fragment, credentials or wildcard -- see
    ``_normalise_origin`` for the full validation, including IDNA encoding of
    the host. Valid entries are normalised (scheme and host lower-cased, the
    scheme's default port dropped) and returned in order, with duplicates
    that normalise to the same origin collapsed to their first occurrence. An
    entry that fails validation is dropped rather than widening what the
    setting allows; every dropped entry is named together in a single
    WARNING log line so a typo is visible without crashing startup.

    A blank segment from a stray or trailing comma (``"a,,b"`` / ``"a,"``) is
    silently skipped -- it is a formatting artifact, not a malformed origin.

    If ``raw`` was non-blank (``embed_origins_configured(raw)`` is True) but
    every entry failed validation, this still returns ``[]`` -- that is
    correct, nothing here validated -- but also logs a second WARNING saying
    framing will be limited to the app itself. That combination (configured,
    nothing valid) is exactly what ``LEX_EMBED_ORIGINS_CONFIGURED`` flags for
    the middleware and ``serve_react`` to fail closed on, instead of treating
    an empty list as if the setting were simply unset.
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

    if not valid:
        logger.warning(
            "LEX_EMBED_ALLOWED_ORIGINS is set but contains no valid origin; "
            "framing is limited to the app itself (frame-ancestors 'self')."
        )

    return valid


def frame_ancestors_directive(origins: list[str]) -> str:
    """Build a ``frame-ancestors`` CSP directive from a list of origins.

    Shared by every place that needs the embed allow-list's directive text --
    ``EmbedXFrameOptionsMiddleware`` and ``serve_react`` -- so the fail-closed
    rule lives in exactly one place: an empty ``origins`` still produces a
    valid, restrictive directive (``frame-ancestors 'self'``), the outcome
    when ``LEX_EMBED_ALLOWED_ORIGINS`` was set but nothing in it validated.
    """
    if not origins:
        return "frame-ancestors 'self'"
    return "frame-ancestors 'self' " + " ".join(origins)


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
    verbatim, in its original order. The directive name is matched exactly
    (its first whitespace-separated token, case-insensitively) rather than by
    prefix, so a directive that merely starts with the same letters --
    nothing in the CSP spec is named that way, but this must not assume it --
    is never mistaken for ``frame-ancestors`` and overwritten.

    Not supported: a ``Content-Security-Policy`` header value containing
    multiple comma-separated policies (CSP allows enforcing several policies
    at once this way). This only ever splits on ``;`` (directives within one
    policy); a comma here is assumed to be part of a directive's own value,
    not a policy separator. Every caller in this codebase only ever sets a
    single policy, so this has not been a problem in practice -- but it is a
    real limitation, not an oversight to rely past.
    """
    if not existing_csp:
        return directive

    parts = [p.strip() for p in existing_csp.split(";") if p.strip()]
    for i, part in enumerate(parts):
        if part.split(None, 1)[0].lower() == "frame-ancestors":
            parts[i] = directive
            break
    else:
        parts.append(directive)
    return "; ".join(parts)
