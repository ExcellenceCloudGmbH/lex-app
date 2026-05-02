"""Shared helpers for MCP tool modules."""
from __future__ import annotations

import base64
from typing import Any, Dict, Optional

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from lex.mcp_server.context import current_principal  # re-exported
from lex.mcp_server.registry import container_is_writable, get_container

__all__ = [
    "current_principal",
    "envelope",
    "require_container",
    "ensure_writable",
    "binary_envelope",
    "clamp_max_bytes",
]


def envelope(status_code: int, payload: Any) -> Dict[str, Any]:
    """Standard MCP tool response envelope."""
    return {"status": status_code, "result": payload}


def require_container(container_id: str):
    """Resolve an exposed container or raise a JSON-RPC ``-32602``."""
    container = get_container(container_id)
    if container is None:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=f"Unknown or unexposed model container '{container_id}'.",
            )
        )
    return container


def ensure_writable(container_id: str) -> None:
    if not container_is_writable(container_id):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=f"Model container '{container_id}' is read-only over MCP.",
            )
        )


def clamp_max_bytes(requested: Optional[int], ceiling: int) -> int:
    """Resolve an effective max-bytes cap from a tool argument.

    The caller's request is honoured as long as it is positive and at or
    below ``ceiling``. ``None``, ``0`` or negative values fall back to
    ``ceiling``.
    """
    try:
        ceiling_int = int(ceiling)
    except (TypeError, ValueError):
        ceiling_int = 0
    if ceiling_int <= 0:
        return 0
    if requested is None:
        return ceiling_int
    try:
        req = int(requested)
    except (TypeError, ValueError):
        return ceiling_int
    if req <= 0:
        return ceiling_int
    return min(req, ceiling_int)


def binary_envelope(
    decoded: Any,
    *,
    max_bytes: int,
    default_filename: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrap a dispatcher decode result as an MCP-friendly envelope.

    * Binary payloads (the ``{"_binary": True, ...}`` dict produced by
      :func:`lex.mcp_server.dispatch._decode_response`) are emitted as a
      base64 file envelope, or as a 413 ``file_too_large`` envelope if
      they exceed ``max_bytes``.
    * JSON / text pass-through (e.g. GCS ``{"download_url": ...}``) is
      returned via the regular ``{status, result}`` envelope.
    """
    if isinstance(decoded, dict) and decoded.get("_binary") is True:
        content: bytes = decoded.get("content_bytes") or b""
        size = len(content)
        name = decoded.get("filename") or default_filename
        content_type = decoded.get("content_type") or "application/octet-stream"
        status_code = int(decoded.get("status_code") or 200)

        if max_bytes and size > max_bytes:
            return envelope(
                413,
                {
                    "error": "file_too_large",
                    "size_bytes": size,
                    "max_bytes": max_bytes,
                    "name": name,
                    "content_type": content_type,
                },
            )

        return envelope(
            status_code,
            {
                "file": {
                    "name": name,
                    "content_type": content_type,
                    "size_bytes": size,
                    "encoding": "base64",
                    "base64": base64.b64encode(content).decode("ascii"),
                }
            },
        )

    return envelope(200, decoded)
