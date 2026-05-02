"""Per-principal rate limiting for the MCP transport.

Implements a simple fixed-window token-bucket on top of Django's cache.
Optimised for clarity and dev-environment portability rather than for
strict atomicity:

* Uses ``cache.add`` + ``cache.incr`` + ``cache.expire``-via-set, which is
  race-tolerant. Over-shoot in the worst case is bounded by the request
  concurrency on a single window boundary and is acceptable for an
  MCP transport limit (the canonical defence-in-depth lives in
  Keycloak / API-key throttling).
* Falls back to the ``default`` cache when the configured alias (default
  ``redis``) is not registered, so dev/test setups without Redis work
  out-of-the-box.
* Failures inside the limiter are logged at WARNING and treated as
  ``allowed=True`` (fail-open). The limiter must never produce a
  spurious 429 because of an infrastructure hiccup.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Optional

from django.core.cache import InvalidCacheBackendError, caches

from lex.mcp_server.config import mcp_setting
from lex.mcp_server.context import McpPrincipal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    current: int
    limit: int
    retry_after_seconds: int


def principal_key(principal: McpPrincipal) -> str:
    """Stable, low-cardinality key for ``principal``.

    Hashed so we never persist the raw API-key name or Keycloak ``sub``
    in the cache; collisions are astronomically unlikely with SHA-256.
    """
    raw: Optional[str] = None
    if principal.auth_kind == "api_key":
        raw = principal.api_key_name or "api_key:anonymous"
    else:
        sub = principal.userinfo.get("sub") if principal.userinfo else None
        if sub:
            raw = f"oidc:{sub}"
        else:
            user_id = getattr(principal.user, "id", None) or getattr(
                principal.user, "pk", None
            )
            raw = f"oidc:user:{user_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return digest


class RateLimiter:
    """Fixed-window per-minute limiter backed by Django cache."""

    def __init__(self) -> None:
        self._cache = self._resolve_cache()
        self._namespace = str(mcp_setting("RATE_LIMIT_NAMESPACE") or "lexmcp:rl")
        self._per_minute = max(int(mcp_setting("RATE_LIMIT_PER_MINUTE") or 0), 0)
        self._burst = max(int(mcp_setting("RATE_LIMIT_BURST") or 0), 0)

    @staticmethod
    def _resolve_cache():
        alias = str(mcp_setting("RATE_LIMIT_CACHE") or "default")
        try:
            return caches[alias]
        except InvalidCacheBackendError:
            return caches["default"]

    @property
    def limit(self) -> int:
        """Effective hard cap (per-minute + burst)."""
        return self._per_minute + self._burst

    def _bucket_key(self, principal_id: str, window_start: int) -> str:
        return f"{self._namespace}:{principal_id}:{window_start}"

    def acquire(self, principal: McpPrincipal) -> RateLimitDecision:
        """Account for one request from ``principal``.

        Always returns a decision; never raises. ``allowed=False`` means
        the caller should reject the request with HTTP 429.
        """
        if not mcp_setting("RATE_LIMIT_ENABLED"):
            return RateLimitDecision(True, 0, self.limit, 0)
        if self._per_minute <= 0:
            return RateLimitDecision(True, 0, self.limit, 0)

        try:
            now = int(time.time())
            window_start = now - (now % 60)
            window_end = window_start + 60
            key = self._bucket_key(principal_key(principal), window_start)

            # ``add`` initialises the counter on the first hit of the
            # window. Subsequent hits ``incr`` it. Both operations are
            # atomic on Redis; LocMemCache is single-process so atomicity
            # is also guaranteed.
            self._cache.add(key, 0, timeout=120)
            try:
                current = self._cache.incr(key)
            except ValueError:
                # Race: bucket evicted between add() and incr(). Re-seed
                # at 1 and let the next request stabilise.
                self._cache.set(key, 1, timeout=120)
                current = 1

            if current > self.limit:
                retry_after = max(window_end - now, 1)
                return RateLimitDecision(False, current, self.limit, retry_after)
            return RateLimitDecision(True, current, self.limit, 0)
        except Exception as exc:  # pragma: no cover - infrastructure errors
            logger.warning("MCP rate limiter unavailable, failing open: %s", exc)
            return RateLimitDecision(True, 0, self.limit, 0)


# Module-level singleton so the cache reference is reused across requests.
_LIMITER: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = RateLimiter()
    return _LIMITER


def reset_rate_limiter() -> None:
    """Drop the cached singleton (used by tests after settings tweaks)."""
    global _LIMITER
    _LIMITER = None


__all__ = [
    "RateLimitDecision",
    "RateLimiter",
    "get_rate_limiter",
    "principal_key",
    "reset_rate_limiter",
]
