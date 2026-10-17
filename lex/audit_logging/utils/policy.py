"""What lex writes into its audit log: the author and how much of the payload.

Two ``lex_config.py`` keys, both optional:

* ``AUDIT_PAYLOAD`` – ``"full"`` (default, unchanged): the payload as sent and
  saved. ``"fields"``: only which fields a write touched — every value becomes
  ``None``, nested objects keep their keys, files are named by field only.
  ``"none"``: an empty payload. For projects whose erasure rules do not reach
  lex's tables (tokens, IBANs and names would otherwise be copied there).
* ``AUDIT_ACTOR`` – a dotted path to ``actor(user) -> str | None``. Its answer
  is the author of every audit entry and the ``created_by``/``edited_by`` of
  every record written in a request, e.g. ``"user:42"`` instead of a name or an
  e-mail address. Unset: the user's name, username or e-mail, as before.
"""
from __future__ import annotations

from typing import Any, Optional

AUDIT_PAYLOAD_MODES = ("full", "fields", "none")


def payload_mode() -> str:
    from django.core.exceptions import ImproperlyConfigured

    from lex.core.extensions import get_setting

    mode = get_setting("AUDIT_PAYLOAD") or "full"
    if mode not in AUDIT_PAYLOAD_MODES:
        raise ImproperlyConfigured(f"lex_config.AUDIT_PAYLOAD must be one of {AUDIT_PAYLOAD_MODES}, not {mode!r}")
    return mode


def _keys_only(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _keys_only(item) if isinstance(item, dict) else None for key, item in value.items()}
    return None


def audit_payload(payload: Any) -> Any:
    """``payload`` as ``AUDIT_PAYLOAD`` allows it to be stored."""
    mode = payload_mode()
    if mode == "full":
        return payload
    if mode == "none" or not isinstance(payload, dict):
        return {}
    return _keys_only(payload)


def audit_actor(user: Any) -> Optional[str]:
    """The author recorded for ``user`` (``AUDIT_ACTOR``, else lex's normalised name)."""
    from lex.core.extensions import get_hook

    if user is None:
        return None
    hook = get_hook("AUDIT_ACTOR")
    if hook is not None:
        answer = hook(user)
        return str(answer) if answer else None
    from lex.core.models.LexModel import LexModel

    return LexModel._normalize_audit_actor(user)
