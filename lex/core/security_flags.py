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
* ``READ_SURFACES`` – history, global search, export and file download apply read permission; an
  export/download audit entry is visible to who may read its record (or export its model), its
  status row follows it, and audit tracebacks are superuser-only.
* ``EDIT``          – ``permission_edit`` is enforced on every write path (BUG-010).
* ``FILTERS``       – list filters/ordering only on fields the user may see.
* ``TRACEBACKS``    – error responses carry no traceback unless ``DEBUG`` or ``LEX_API_TRACEBACKS=true``.
* ``OIDC_REDIRECT`` – ``next``/``fail`` on ``/oidc/*`` must be relative or an allowed origin.
* ``INTERNAL``      – internal tools are restricted: deleting unused files needs a superuser or the
  ``admin`` client role, writing to the server log and editing Django's users and groups need a
  superuser, and the calculation log tree and its PDFs show only logs of records the caller may read.
* ``HISTORY_WRITE`` – history tables can only be written by superusers.
* ``CREATE``        – ``permission_create`` is asked a second time on the record filled with the
  request's validated values (foreign keys set) before it is saved; a refusal answers like the
  first check on the blank record (401/403 ``{message}``).

``LEX_API_TRACEBACKS=true|false`` decides on its own whether error responses
may carry a traceback (see :func:`expose_tracebacks`).
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
    "CREATE",
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


def is_superuser(request) -> bool:
    """Whether the signed-in user behind ``request`` is a Django superuser."""
    user = getattr(request, "user", None)
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
    )


def superuser_only(request) -> bool:
    """True when ``LEX_SEC_INTERNAL`` keeps an internal endpoint from this (non-superuser) caller."""
    return strict("INTERNAL") and not is_superuser(request)


def expose_tracebacks() -> bool:
    """Whether HTTP error responses may include a server traceback.

    ``LEX_API_TRACEBACKS=true|false`` decides when set. Otherwise a traceback
    is shown only with ``DEBUG`` on, or when the ``TRACEBACKS`` hardening is
    switched off (``LEX_SEC_TRACEBACKS=false`` / ``LEX_LEGACY_SECURITY=true``).
    """
    explicit = _env_bool("LEX_API_TRACEBACKS")
    if explicit is not None:
        return explicit
    if not strict("TRACEBACKS"):
        return True
    from django.conf import settings

    return bool(getattr(settings, "DEBUG", False))
