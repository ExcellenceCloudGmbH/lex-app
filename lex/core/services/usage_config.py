"""The usage-metrics privacy switch, the per-instance user pseudonym, and the
usage payload ``api/user/`` hands the interface (source-plan.md 3.1, 3.2, 3.4).

``usage_metrics_enabled()`` is copied from the closed, unmerged
``usage-recorder`` branch (``origin/feat/usage-recorder:lex/core/services/
usage_metrics.py:86-112``) -- the fail-closed on/off switch is reused as-is;
that branch's database-backed recorder is not being built here.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
from hashlib import sha256
from typing import Any, Dict

logger = logging.getLogger(__name__)

USAGE_METRICS_ENV = "LEX_USAGE_METRICS"
_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})

#: PostHog EU ingestion host, used when POSTHOG_HOST is unset (source-plan.md 3.1).
DEFAULT_POSTHOG_HOST = "https://eu.i.posthog.com"

# Guards the "unrecognised value" warning below it to at most once per process --
# not time-based, since a misconfigured value does not change from one call to the next.
_invalid_value_lock = threading.Lock()
_warned_invalid_value = False


def usage_metrics_enabled() -> bool:
    """Whether usage metrics recording is on. Read on every call; fails closed.

    Unset or empty is on, and so are ``true``, ``1``, ``yes`` and ``on``; ``false``,
    ``0``, ``no`` and ``off`` are off. Matching is case-insensitive and surrounding
    whitespace is ignored. Any other value is off too -- a typo must never silently
    turn recording back on -- and logs one warning naming the setting, at most once
    per process so a persistently bad value cannot flood the log.
    """
    global _warned_invalid_value

    raw = os.getenv(USAGE_METRICS_ENV, "")
    value = raw.strip().lower()
    if not value or value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False

    with _invalid_value_lock:
        should_warn = not _warned_invalid_value
        _warned_invalid_value = True
    if should_warn:
        logger.warning(
            "%s=%r is not a recognised value; usage metrics recording is off.",
            USAGE_METRICS_ENV, raw,
        )
    return False


def user_hash(user: Any) -> str:
    """The per-instance pseudonym for ``user`` (source-plan.md 3.2).

    ``hmac_sha256(key=LEX_USAGE_SALT, message=user.username)``, hex, first 32
    characters. ``user.username`` is already an unsalted email hash set by the
    OIDC backend; the per-instance salt makes the pseudonym different on every
    instance, so two instances cannot be joined on the same person. lex-app
    computes it -- the interface only ever forwards what :func:`usage_payload`
    hands it.

    ``LEX_USAGE_SALT`` is delivered "like LEX_API_KEY" (source-plan.md 3.1):
    an opaque string, never decoded. The HMAC key is therefore the literal
    UTF-8 bytes of that string, not ``bytes.fromhex(salt)`` -- the same
    handling every other credential-shaped environment variable in this
    codebase gets (``LEX_API_KEY`` is never hex-decoded either), and it does
    not assume the value is always well-formed 64-character hex.
    """
    salt = os.getenv("LEX_USAGE_SALT", "")
    digest = hmac.new(salt.encode("utf-8"), user.username.encode("utf-8"), sha256)
    return digest.hexdigest()[:32]


def usage_payload(user: Any) -> Dict[str, Any]:
    """The ``"usage"`` key ``api/user/`` hands the interface (source-plan.md 3.4).

    ``{"enabled": false}`` and nothing else when usage metrics are switched
    off, the per-instance salt (``LEX_USAGE_SALT``) is missing, or
    ``POSTHOG_PROJECT_KEY`` is empty -- in every one of those cases the
    interface must never initialise PostHog.
    """
    posthog_key = os.getenv("POSTHOG_PROJECT_KEY", "")
    salt = os.getenv("LEX_USAGE_SALT", "")

    if not usage_metrics_enabled() or not salt or not posthog_key:
        return {"enabled": False}

    return {
        "enabled": True,
        "posthog_key": posthog_key,
        "posthog_host": os.getenv("POSTHOG_HOST") or DEFAULT_POSTHOG_HOST,
        "instance_rid": os.getenv("INSTANCE_RESOURCE_IDENTIFIER", ""),
        "user_hash": user_hash(user),
    }


def _reset_for_tests() -> None:
    """Reset the once-per-process invalid-value warning guard. Test-only."""
    global _warned_invalid_value
    with _invalid_value_lock:
        _warned_invalid_value = False
