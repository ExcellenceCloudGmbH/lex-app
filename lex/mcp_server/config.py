"""Settings accessors for the MCP server with sensible defaults."""
from __future__ import annotations

from typing import Any, Dict

from django.conf import settings


_DEFAULTS: Dict[str, Any] = {
    "ENABLED": True,
    "MOUNT_PATH": "/mcp",
    "DEFAULT_PAGE_SIZE": 50,
    "MAX_PAGE_SIZE": 200,
    # None -> expose every registered model_container.
    # Otherwise: iterable of model_container ids (lower-case) to expose.
    "EXPOSED_MODELS": None,
    # Iterable of model_container ids that should be read-only over MCP.
    "WRITE_DISABLED_MODELS": (),
    # If False, no create/update/delete tools are registered at all.
    "ENABLE_WRITE": True,
    # Per-feature toggles for the Phase 6 tool/resource/prompt bundles.
    "ENABLE_CALCULATIONS": True,
    "ENABLE_SEARCH": True,
    "ENABLE_HISTORY": True,
    "ENABLE_RESOURCES": True,
    "ENABLE_PROMPTS": True,
    # Phase 8 — permission introspection tools.
    "ENABLE_PERMISSIONS": True,
    # Phase 7 — file/SharePoint/PDF tools.
    "ENABLE_FILES": True,
    # Phase 10 — embeddable frontend URL tool.
    "ENABLE_EMBED": True,
    # Base URL of the React frontend for embed URL generation.
    # Resolution order: this setting → REACT_APP_URL env → LEX_FRONTEND_URL
    # env → http://localhost:8000.
    "FRONTEND_BASE_URL": "https://melihs-macbook-pro.tail604752.ts.net",
    # Extra origins the embed widget iframe may navigate to (e.g. auth/SSO
    # provider).  These are added to frameDomains, connectDomains, and
    # resourceDomains in the ChatGPT sandbox CSP.
    "EMBED_EXTRA_CSP_ORIGINS": ["https://auth.excellence-cloud.de"],
    # Hard caps applied to base64-encoded MCP file envelopes. Over-cap
    # responses are returned as 413 envelopes (no `base64` payload).
    "FILE_MAX_BYTES": 5 * 1024 * 1024,
    "EXPORT_MAX_BYTES": 25 * 1024 * 1024,
    # Caps for log-tool responses to avoid blowing past the MCP message budget.
    "LOG_TAIL_MAX_BYTES": 64 * 1024,
    "LOG_TREE_MAX_ROWS": 200,
    # Per-principal token-bucket caps (see lex.mcp_server.ratelimit).
    "RATE_LIMIT_PER_MINUTE": 600,
    # Phase 9 — operational hardening.
    "RATE_LIMIT_ENABLED": True,
    # Soft burst allowance (in requests above the per-minute cap) — applied
    # by the limiter as the bucket capacity. Defaults to ~10% of the cap.
    "RATE_LIMIT_BURST": 60,
    # Django cache alias to use for the limiter; falls back to "default" if
    # the named alias is unavailable.
    "RATE_LIMIT_CACHE": "redis",
    "RATE_LIMIT_NAMESPACE": "lexmcp:rl",
    # Toggle structured per-call logging + Sentry tags.
    "OBSERVABILITY_ENABLED": True,
    # DNS-rebinding protection (FastMCP transport_security).
    # Default off: the MCP endpoint is already protected by API-KEY / Bearer
    # auth, and DNS-rebinding protection mainly targets unauthenticated
    # localhost servers (Claude Desktop). Set to True to opt in; then
    # ``ALLOWED_HOSTS``/``ALLOWED_ORIGINS`` (or Django's ALLOWED_HOSTS) gate
    # which Host/Origin headers are accepted.
    "DNS_REBINDING_PROTECTION": False,
    "ALLOWED_HOSTS": None,
    "ALLOWED_ORIGINS": None,
}


def mcp_setting(key: str) -> Any:
    overrides = getattr(settings, "MCP_SERVER", {}) or {}
    return overrides.get(key, _DEFAULTS[key])
