"""Structured logging + Sentry tags for MCP tool/auth events.

Sentry is **optional**: if ``sentry_sdk`` isn't importable (or
``OBSERVABILITY_ENABLED`` is False), every helper degrades to a no-op so
the rest of the MCP server stays functional in minimal environments.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Mapping, Optional

from lex.mcp_server.config import mcp_setting

logger = logging.getLogger("lex.mcp_server")

try:  # pragma: no cover - optional dependency
    import sentry_sdk as _sentry
except Exception:  # pragma: no cover
    _sentry = None  # type: ignore[assignment]


def _enabled() -> bool:
    try:
        return bool(mcp_setting("OBSERVABILITY_ENABLED"))
    except Exception:
        return True


def _set_tag(key: str, value: Any) -> None:
    if _sentry is None or not _enabled():
        return
    try:
        _sentry.set_tag(key, value)
    except Exception:  # pragma: no cover
        pass


def _principal_id(principal) -> Optional[str]:
    if principal is None:
        return None
    if getattr(principal, "auth_kind", None) == "api_key":
        return f"apikey:{getattr(principal, 'api_key_name', None) or 'anon'}"
    info = getattr(principal, "userinfo", None) or {}
    sub = info.get("sub") if isinstance(info, Mapping) else None
    if sub:
        return f"oidc:{sub}"
    user = getattr(principal, "user", None)
    user_id = getattr(user, "id", None) or getattr(user, "pk", None)
    if user_id is not None:
        return f"oidc:user:{user_id}"
    return None


@contextmanager
def ToolCallSpan(tool_name: str, *, principal=None):
    """Wrap a tool/view dispatch in a uniform observability span."""
    enabled = _enabled()
    started = time.perf_counter()
    pid = _principal_id(principal)
    auth_kind = getattr(principal, "auth_kind", None)

    if enabled:
        _set_tag("mcp.tool", tool_name)
        if auth_kind:
            _set_tag("mcp.auth_kind", auth_kind)
        if pid:
            _set_tag("mcp.principal_id", pid)
        logger.info(
            "mcp.tool.call",
            extra={"mcp_tool": tool_name, "mcp_auth_kind": auth_kind, "mcp_principal_id": pid},
        )

    status = "ok"
    error: Optional[BaseException] = None
    try:
        yield
    except BaseException as exc:  # noqa: BLE001
        status = "error"
        error = exc
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        if enabled:
            extra = {
                "mcp_tool": tool_name,
                "mcp_auth_kind": auth_kind,
                "mcp_principal_id": pid,
                "duration_ms": duration_ms,
                "status": status,
            }
            if error is not None:
                extra["error_type"] = type(error).__name__
                extra["error_message"] = str(error)[:500]
                logger.warning("mcp.tool.done", extra=extra)
            else:
                logger.info("mcp.tool.done", extra=extra)


def record_auth(decision: str, *, auth_kind: Optional[str], reason: Optional[str] = None,
                principal_id: Optional[str] = None) -> None:
    """Record an authentication outcome (``ok`` | ``denied``)."""
    if not _enabled():
        return
    _set_tag("mcp.auth_kind", auth_kind or "none")
    _set_tag("mcp.auth_decision", decision)
    if principal_id:
        _set_tag("mcp.principal_id", principal_id)
    extra = {
        "mcp_auth_kind": auth_kind,
        "mcp_auth_decision": decision,
        "mcp_auth_reason": reason,
        "mcp_principal_id": principal_id,
    }
    if decision == "ok":
        logger.info("mcp.auth.ok", extra=extra)
    else:
        logger.warning("mcp.auth.denied", extra=extra)


__all__ = ["ToolCallSpan", "record_auth"]
