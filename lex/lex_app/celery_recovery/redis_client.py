"""
Thin Redis client wrapper for the recovery system.

Why a wrapper:

- Lazy initialization. The recovery package is imported during settings load on
  every Django/Celery process, even ones that never talk to Redis (CI runs,
  ``manage.py shell``). We must not open a connection at import time.
- Single source of truth for *which* Redis to talk to. We reuse the Celery
  broker URL — the recovery system lives in the same Redis as the queue.
- Testability. Tests can swap in a fake via :func:`set_client_factory`.

All callers must use :func:`get_client`. Do not import ``redis`` directly from
elsewhere in this package.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_client: Any = None
_client_lock = threading.Lock()
_client_factory: Optional[Callable[[], Any]] = None


def _default_factory() -> Any:
    """Build a ``redis.Redis`` connection pointed at the Celery broker.

    The broker URL is read from Django settings if available, falling back to
    the same env-derived URL ``lex/lex_app/settings.py`` uses. We avoid
    importing ``django.conf.settings`` at module import time because the
    recovery package must remain import-safe outside a configured Django app.
    """
    import redis  # local import keeps the wrapper import-light

    url = _resolve_broker_url()
    return redis.Redis.from_url(url, decode_responses=False, socket_timeout=2.0)


def _resolve_broker_url() -> str:
    """Mirror the logic in ``lex/lex_app/settings.py`` so workers and the web
    process talk to the same Redis even if Django settings haven't loaded."""
    try:
        from django.conf import settings as django_settings

        url = getattr(django_settings, "CELERY_BROKER_URL", None)
        if url:
            return url
    except Exception:
        pass

    if os.getenv("DEPLOYMENT_ENVIRONMENT") is not None:
        user = os.getenv("REDIS_USERNAME", "")
        pwd = os.getenv("REDIS_PASSWORD", "")
        host = os.getenv("REDIS_HOST", "localhost")
        return f"redis://{user}:{pwd}@{host}/1"
    return "redis://127.0.0.1:6379/1"


def set_client_factory(factory: Optional[Callable[[], Any]]) -> None:
    """Override the connection factory (used by tests to inject a fake)."""
    global _client_factory, _client
    with _client_lock:
        _client_factory = factory
        _client = None  # force rebuild on next get_client()


def get_client() -> Any:
    """Return a process-wide Redis client, building it on first use."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            factory = _client_factory or _default_factory
            _client = factory()
    return _client


def reset_for_tests() -> None:
    """Drop the cached client and the factory override.

    Tests call this in ``tearDown`` so each test starts from a clean slate.
    """
    global _client, _client_factory
    with _client_lock:
        _client = None
        _client_factory = None


__all__ = ["get_client", "set_client_factory", "reset_for_tests"]
