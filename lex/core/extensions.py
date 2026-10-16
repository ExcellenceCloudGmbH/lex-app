"""Project extension keys read from ``lex_config.py``.

A project extends lex-app through a fixed set of keys in its ``lex_config.py``
(custom API routes, public paths, extra middleware, hooks). This module is the
one place that knows those keys, their defaults, and how to resolve them.

Two rules keep this safe:

* **Lazy.** ``lex_config.py`` may import project code (models, serializers).
  Nothing here is evaluated while Django settings load; values are read on
  first use, after the app registry is ready.
* **Loud.** A key whose import string cannot be resolved raises
  :class:`~django.core.exceptions.ImproperlyConfigured` instead of being
  ignored, so a typo never silently turns a security hook off.

Every key is optional. With none of them set, lex-app behaves exactly as it
did before these keys existed.
"""
from __future__ import annotations

import contextlib
import functools
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

#: Every supported extension key and its default value.
EXTENSION_DEFAULTS: Dict[str, Any] = {
    # Custom API (WP2): a module with ``urlpatterns`` mounted under API_PREFIX.
    "API_URLCONF": None,
    "API_PREFIX": "api/project/",
    # Paths under API_PREFIX that answer without sign-in (WP3).
    "PUBLIC_PATHS": (),
    # Extra middleware per lex-owned slot: {"first": [...], "after_auth": [...], "last": [...]}.
    "MIDDLEWARE_EXTRA": {},
    # Extra DRF authentication classes, tried before lex's own (WP2).
    "AUTHENTICATION_CLASSES": (),
    # Error handling (WP4).
    "EXCEPTION_HANDLER": None,
    "DB_ERROR_MAPPER": None,
    # Per-request / per-task database context, e.g. for row-level security (WP10).
    "DB_CONTEXT": None,
    # Who may use lex's generic ``api/*`` endpoints: "all", "superuser" or "off" (WP2).
    "GENERIC_API": "all",
    # OpenAPI schema for the custom API (WP16).
    "SCHEMA": None,
    # Register the auto-generated serializer under its alias when ``default`` is overridden (WP7).
    "EXPOSE_AUTO_SERIALIZER": True,
    # Scoped API keys (WP11).
    "API_KEY_HEADERS": (),
    "API_KEY_RESOLVER": None,
    # Realtime groups (WP12).
    "MUTATION_GROUPS": None,
    "USER_GROUPS": None,
}

EXTENSION_KEYS = tuple(EXTENSION_DEFAULTS)

GENERIC_API_MODES = ("all", "superuser", "off")
MIDDLEWARE_SLOTS = ("first", "after_auth", "last")

_lock = threading.RLock()
_project_values: Optional[Dict[str, Any]] = None
_overrides: List[Dict[str, Any]] = []
_hook_cache: Dict[str, Any] = {}


def _load_project_values() -> Dict[str, Any]:
    global _project_values
    with _lock:
        if _project_values is None:
            from lex.core.config import LexProjectConfig

            _project_values = dict(LexProjectConfig.load().extensions)
        return _project_values


def get_setting(name: str) -> Any:
    """Return the configured value of extension key ``name`` or its default."""
    if name not in EXTENSION_DEFAULTS:
        raise KeyError(f"Unknown lex extension key: {name!r}")
    for layer in reversed(_overrides):
        if name in layer:
            return layer[name]
    return _load_project_values().get(name, EXTENSION_DEFAULTS[name])


def is_configured(name: str) -> bool:
    """True when ``name`` differs from its default (set in ``lex_config.py`` or overridden)."""
    return get_setting(name) != EXTENSION_DEFAULTS[name]


def _resolve(name: str, value: Any) -> Any:
    if value is None or callable(value):
        return value
    if isinstance(value, str):
        try:
            return import_string(value)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"lex_config.{name} = {value!r} cannot be imported: {exc}"
            ) from exc
    raise ImproperlyConfigured(
        f"lex_config.{name} must be a dotted import path or a callable, not {type(value).__name__}"
    )


def get_hook(name: str) -> Optional[Callable[..., Any]]:
    """Return the callable configured for ``name`` (resolved and cached), or ``None``."""
    value = get_setting(name)
    if _overrides:
        return _resolve(name, value)
    with _lock:
        if name not in _hook_cache:
            _hook_cache[name] = _resolve(name, value)
        return _hook_cache[name]


def get_import_list(name: str, value: Any = None) -> List[Any]:
    """Resolve a list of dotted paths (or objects) configured for ``name``."""
    items = get_setting(name) if value is None else value
    if isinstance(items, str):
        items = [items]
    return [_resolve(name, item) for item in (items or ())]


def api_prefix() -> str:
    """The custom API prefix, normalised to ``"segment/.../"`` (no leading slash)."""
    raw = str(get_setting("API_PREFIX") or "").strip().strip("/")
    if not raw:
        raise ImproperlyConfigured("lex_config.API_PREFIX must not be empty")
    return f"{raw}/"


def generic_api_mode() -> str:
    mode = get_setting("GENERIC_API")
    if mode not in GENERIC_API_MODES:
        raise ImproperlyConfigured(
            f"lex_config.GENERIC_API must be one of {GENERIC_API_MODES}, not {mode!r}"
        )
    return mode


def middleware_extra(slot: str) -> List[Any]:
    if slot not in MIDDLEWARE_SLOTS:
        raise KeyError(slot)
    config = get_setting("MIDDLEWARE_EXTRA") or {}
    if not isinstance(config, dict):
        raise ImproperlyConfigured("lex_config.MIDDLEWARE_EXTRA must be a dict of slot -> list")
    unknown = set(config) - set(MIDDLEWARE_SLOTS)
    if unknown:
        raise ImproperlyConfigured(
            f"lex_config.MIDDLEWARE_EXTRA has unknown slots {sorted(unknown)}; use {MIDDLEWARE_SLOTS}"
        )
    return get_import_list("MIDDLEWARE_EXTRA", config.get(slot, ()))


def reset_extension_cache() -> None:
    """Forget loaded values and resolved hooks. Intended for tests."""
    global _project_values
    with _lock:
        _project_values = None
        _hook_cache.clear()


@contextlib.contextmanager
def override_extensions(**values: Any) -> Iterator[None]:
    """Temporarily override extension keys (tests), like Django's ``override_settings``.

    Usable as a context manager or decorator. Unknown keys raise ``KeyError``.
    """
    unknown = set(values) - set(EXTENSION_DEFAULTS)
    if unknown:
        raise KeyError(f"Unknown lex extension keys: {sorted(unknown)}")
    with _lock:
        _overrides.append(dict(values))
        _hook_cache.clear()
    try:
        yield
    finally:
        with _lock:
            _overrides.pop()
            _hook_cache.clear()


def overriding_extensions(**values: Any):
    """Decorator form of :func:`override_extensions` for test methods."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with override_extensions(**values):
                return func(*args, **kwargs)

        return wrapper

    return decorator
