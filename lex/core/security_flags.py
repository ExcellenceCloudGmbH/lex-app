"""Secure defaults and their opt-outs.

Every hardening below is **on by default**. A project that needs the old
behaviour while it migrates can switch them all off with
``LEX_LEGACY_SECURITY=true``, or a single one with ``LEX_SEC_<NAME>=false``.
A per-flag setting always wins over the umbrella switch, so
``LEX_LEGACY_SECURITY=true`` plus ``LEX_SEC_EDIT=true`` keeps edit enforcement
while everything else is legacy.

The opt-out is a migration aid and is deprecated; it logs a warning once.

Flags (name → what "on" means):

* ``FAIL_CLOSED``   – an exception inside a permission hook denies access.
* ``NOT_FOUND``     – a record the user may not read answers 404 (not 200 ``{}``).
* ``READ_SURFACES`` – history, global search, export and file download apply read permission.
* ``EDIT``          – ``permission_edit`` is enforced on every write path (BUG-010).
* ``FILTERS``       – list filters/ordering only on fields the user may see.
* ``TRACEBACKS``    – error responses carry no traceback unless ``DEBUG`` or ``LEX_API_TRACEBACKS=true``.
* ``OIDC_REDIRECT`` – ``next``/``fail`` on ``/oidc/*`` must be relative or an allowed origin.
* ``INTERNAL``      – internal tools (file cleanup, log upload, calculation logs, user edits) are restricted.
* ``HISTORY_WRITE`` – history tables can only be written by superusers.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

FLAGS = (
    "FAIL_CLOSED",
    "NOT_FOUND",
    "READ_SURFACES",
    "EDIT",
    "FILTERS",
    "TRACEBACKS",
    "OIDC_REDIRECT",
    "INTERNAL",
    "HISTORY_WRITE",
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_warned = False


def _env_bool(name: str):
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def legacy_mode() -> bool:
    """True when ``LEX_LEGACY_SECURITY`` asks for the old, unhardened behaviour."""
    global _warned
    legacy = bool(_env_bool("LEX_LEGACY_SECURITY"))
    if legacy and not _warned:
        _warned = True
        logger.warning(
            "LEX_LEGACY_SECURITY=true restores the unhardened security behaviour. "
            "This opt-out is deprecated and will be removed in a later major release."
        )
    return legacy


def strict(name: str) -> bool:
    """Return whether the hardening ``name`` is active."""
    if name not in FLAGS:
        raise KeyError(f"Unknown security flag: {name!r}")
    explicit = _env_bool(f"LEX_SEC_{name}")
    if explicit is not None:
        return explicit
    return not legacy_mode()


def is_superuser_or_admin(request) -> bool:
    """Whether the caller behind ``request`` is a superuser or in the ``admin`` group.

    The same convention LexModel's permission examples use
    (``user_context.is_superuser or 'admin' in user_context.groups``). API keys
    carry no groups and are never admins.
    """
    try:
        from lex.core.models.LexModel import UserContext

        context = UserContext.from_request_base(request)
    except Exception:
        return False
    return bool(context.is_superuser or "admin" in (context.groups or set()))
