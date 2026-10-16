"""Which project API paths answer without sign-in.

Two sources, both relative to ``lex_config.API_PREFIX``:

* ``lex_config.PUBLIC_PATHS`` – plain paths such as ``"me"`` or ``"schema"``.
  An entry ending in ``/`` covers everything below it
  (``"signatures/webhook/"``).
* views in ``API_URLCONF`` whose class sets ``public = True``
  (:class:`~lex.api.project.LexAPIView`, :class:`~lex.api.project.LexWebhookView`).

lex's login middleware consults :func:`is_public_path`; everything else under
the prefix still requires a signed-in user.
"""
from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional, Pattern

from django.urls import URLPattern, URLResolver

from lex.core import extensions

_cache: Optional[List[Pattern[str]]] = None
_cache_key: Optional[tuple] = None


def _base_path() -> str:
    raw = os.getenv("DJANGO_BASE_PATH", "").strip().strip("/")
    return f"{raw}/" if raw else ""


def full_prefix() -> str:
    """Absolute URL prefix of the project API, e.g. ``/api/atrium/``."""
    return "/" + _base_path() + extensions.api_prefix()


def _pattern_regex(pattern) -> str:
    regex = pattern.pattern.regex.pattern
    return regex[1:] if regex.startswith("^") else regex


def _strip_end(regex: str) -> str:
    if regex.endswith(r"\Z"):
        return regex[:-2]
    if regex.endswith("$") and not regex.endswith(r"\$"):
        return regex[:-1]
    return regex


def _ensure_end(regex: str) -> str:
    return regex if regex.endswith(("$", r"\Z")) else regex + "$"


def _walk(patterns: Iterable, prefix: str) -> Iterable[str]:
    for entry in patterns:
        if isinstance(entry, URLResolver):
            yield from _walk(entry.url_patterns, prefix + _strip_end(_pattern_regex(entry)))
        elif isinstance(entry, URLPattern):
            view_class = getattr(entry.callback, "cls", None) or getattr(entry.callback, "view_class", None)
            if getattr(entry.callback, "lex_public", False) or getattr(view_class, "public", False):
                yield prefix + _ensure_end(_pattern_regex(entry))


def _compile() -> List[Pattern[str]]:
    from importlib import import_module

    prefix = full_prefix()
    patterns: List[Pattern[str]] = []
    for entry in extensions.get_setting("PUBLIC_PATHS") or ():
        path = str(entry).lstrip("/")
        if path.endswith("/"):
            patterns.append(re.compile("^" + re.escape(prefix + path)))
        else:
            patterns.append(re.compile("^" + re.escape(prefix + path) + "/?$"))
    schema = extensions.get_setting("SCHEMA")
    if isinstance(schema, dict) and schema.get("public"):
        for name in ("schema", "docs"):
            patterns.append(re.compile("^" + re.escape(prefix + name) + "/?$"))
    urlconf = extensions.get_setting("API_URLCONF")
    if urlconf:
        module = import_module(urlconf) if isinstance(urlconf, str) else urlconf
        for regex in _walk(getattr(module, "urlpatterns", ()), ""):
            patterns.append(re.compile("^" + re.escape(prefix) + regex))
    return patterns


def public_path_patterns() -> List[Pattern[str]]:
    global _cache, _cache_key
    key = (
        extensions.get_setting("API_PREFIX"),
        tuple(extensions.get_setting("PUBLIC_PATHS") or ()),
        extensions.get_setting("API_URLCONF"),
        repr(extensions.get_setting("SCHEMA")),
        _base_path(),
    )
    if _cache is None or _cache_key != key:
        _cache = _compile()
        _cache_key = key
    return _cache


def is_public_path(path: str) -> bool:
    if not (
        extensions.get_setting("API_URLCONF")
        or extensions.get_setting("PUBLIC_PATHS")
        or extensions.get_setting("SCHEMA")
    ):
        return False
    return any(pattern.search(path) for pattern in public_path_patterns())


def reset_public_path_cache() -> None:
    global _cache, _cache_key
    _cache = None
    _cache_key = None
