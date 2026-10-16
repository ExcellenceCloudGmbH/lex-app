"""Secure defaults and their opt-outs.

Since lex-app 3.0 every hardening below is **on by default**. A project that
needs the pre-3.0 behaviour while it migrates can switch them all off with
``LEX_LEGACY_SECURITY=true``, or a single one with ``LEX_SEC_<NAME>=false``.
A per-flag setting always wins over the umbrella switch, so
``LEX_LEGACY_SECURITY=true`` plus ``LEX_SEC_EDIT=true`` keeps edit enforcement
while everything else is legacy.

The opt-out is a migration aid and is deprecated; it logs a warning once.

Flags (name → what "on" means):

* ``FAIL_CLOSED``   – an exception inside a permission hook denies access.
* ``NOT_FOUND``     – a record the user may not read answers 404 (not 200 ``{}``/403).
* ``READ_SURFACES`` – history, global search, export and file download apply read permission.
* ``EDIT``          – ``permission_edit`` is enforced on every write path (BUG-010).
* ``FILTERS``       – list filters/ordering only on fields the user may see.
* ``TRACEBACKS``    – error responses carry no traceback unless ``DEBUG`` or ``LEX_API_TRACEBACKS=true``.
* ``OIDC_REDIRECT`` – ``next``/``fail`` on ``/oidc/*`` must be relative or an allowed origin.
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
    """True when ``LEX_LEGACY_SECURITY`` asks for the pre-3.0 behaviour."""
    global _warned
    legacy = bool(_env_bool("LEX_LEGACY_SECURITY"))
    if legacy and not _warned:
        _warned = True
        logger.warning(
            "LEX_LEGACY_SECURITY=true restores pre-3.0 security behaviour. "
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


def expose_tracebacks() -> bool:
    """Whether error responses may include a server traceback."""
    from django.conf import settings

    explicit = _env_bool("LEX_API_TRACEBACKS")
    if explicit is not None:
        return explicit
    if not strict("TRACEBACKS"):
        return True
    return bool(getattr(settings, "DEBUG", False))
