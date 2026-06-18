"""Encrypted runtime metadata for health responses.

The public health contract stays deliberately boring: ``{"status": "Healthy :)"}``.
When a deployed instance has ``LEX_API_KEY`` available, health responses also carry
an encrypted ``runtime`` token that the Instance Controller can decrypt with the
same per-instance key it already stores.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from typing import Any

from lex._version import __version__

logger = logging.getLogger(__name__)

HEALTH_STATUS = "Healthy :)"


def _derive_fernet_key(api_key: str) -> bytes:
    """Derive the 32-byte urlsafe Fernet key from the per-instance API key."""
    digest = hashlib.sha256(api_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _runtime_metadata() -> dict[str, str]:
    commit_sha = os.getenv("COMMIT_SHA", "").strip()
    return {
        "lex_app_version": __version__,
        "instance_commit_sha": commit_sha,
        "commit_sha_source": "env" if commit_sha else "unknown",
    }


def encrypt_runtime_metadata(api_key: str, metadata: dict[str, Any] | None = None) -> str:
    """Return a Fernet token carrying runtime metadata for IC to decrypt."""
    from cryptography.fernet import Fernet

    payload = json.dumps(
        metadata if metadata is not None else _runtime_metadata(),
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return Fernet(_derive_fernet_key(api_key)).encrypt(payload).decode("ascii")


def build_health_payload() -> dict[str, str]:
    """Build the public health response, optionally with encrypted runtime data.

    Health must never fail because runtime metadata cannot be encrypted. If the
    per-instance key is missing or encryption raises, return the legacy payload.
    """
    payload = {"status": HEALTH_STATUS}
    api_key = os.getenv("LEX_API_KEY", "").strip()
    if not api_key:
        return payload

    try:
        payload["runtime"] = encrypt_runtime_metadata(api_key)
    except Exception as exc:  # noqa: BLE001 - health must stay best-effort
        logger.info("Skipping encrypted runtime health metadata: %s", exc)
    return payload
