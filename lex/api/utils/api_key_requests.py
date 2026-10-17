from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet, Optional

from rest_framework_api_key.models import APIKey
from rest_framework_api_key.permissions import KeyParser

DEFAULT_API_KEY_SCOPES: FrozenSet[str] = frozenset(
    {"create", "read", "edit", "delete", "list", "export"}
)
_UNSET = object()


class _EmptyGroups:
    def values_list(self, *args, **kwargs):
        return []


class TechnicalAPIKeyUser:
    def __init__(self, api_key_name: str):
        self.username = api_key_name
        self.email = ""
        self.first_name = ""
        self.last_name = ""
        self.is_authenticated = True
        self.is_superuser = False
        self.is_anonymous = False
        self.groups = _EmptyGroups()

    def __str__(self) -> str:
        return self.username


@dataclass(frozen=True)
class APIKeyRequestIdentity:
    api_key_name: str
    user: TechnicalAPIKeyUser
    email: str = ""
    scopes: FrozenSet[str] = DEFAULT_API_KEY_SCOPES


def _get_request_holder(request):
    return getattr(request, "_request", request)


def get_raw_api_key(request) -> Optional[str]:
    holder = _get_request_holder(request)
    raw_key = KeyParser().get(holder)
    if raw_key:
        return raw_key

    auth_header = holder.META.get("HTTP_AUTHORIZATION", "")
    prefix = "Api-Key "
    if auth_header.startswith(prefix):
        candidate = auth_header[len(prefix) :].strip()
        return candidate or None
    return None


#: ``rsname`` of the synthetic permission a scoped key grants: every resource.
ANY_RESOURCE = "*"


def _extra_header_keys(holder) -> Optional[str]:
    """Raw key from the project's additional headers (``lex_config.API_KEY_HEADERS``)."""
    try:
        from lex.core.extensions import get_setting

        headers = get_setting("API_KEY_HEADERS") or ()
    except Exception:
        return None
    if isinstance(headers, str):
        headers = [headers]
    for meta_name in headers:
        value = holder.META.get(str(meta_name), "")
        if value and value.strip():
            return value.strip()
    return None


def get_scoped_api_key(request):
    """The :class:`~lex.authentication.models.LexAPIKey` the request presents, or ``None``.

    Revoked and expired keys do not count. ``lex_config.API_KEY_RESOLVER``
    replaces the lookup (for example with a database function that can read
    the key table before anyone is signed in under row-level security); it
    receives the raw key and returns a key object or ``None``.
    """
    holder = _get_request_holder(request)
    cached = getattr(holder, "_lex_scoped_api_key", _UNSET)
    if cached is not _UNSET:
        return cached

    key = None
    raw_key = get_raw_api_key(holder) or _extra_header_keys(holder)
    if raw_key:
        try:
            from lex.core.extensions import get_hook

            resolver = get_hook("API_KEY_RESOLVER")
            if resolver is not None:
                key = resolver(raw_key)
            else:
                from lex.authentication.models import LexAPIKey

                try:
                    key = LexAPIKey.objects.get_from_key(raw_key)
                except LexAPIKey.DoesNotExist:
                    key = None
            if key is not None and (getattr(key, "revoked", False) or getattr(key, "has_expired", False)):
                key = None
        except Exception:
            key = None

    setattr(holder, "_lex_scoped_api_key", key)
    return key


def scoped_key_permissions(key) -> list:
    """The key's scopes as one model-wide permission on every resource."""
    scopes = [str(scope) for scope in (getattr(key, "scopes", None) or [])]
    return [{"rsname": ANY_RESOURCE, "resource_set_id": None, "scopes": scopes}]


def get_api_key_request_identity(request) -> Optional[APIKeyRequestIdentity]:
    holder = _get_request_holder(request)
    cached_identity = getattr(holder, "_lex_api_key_identity", _UNSET)
    if cached_identity is not _UNSET:
        return cached_identity

    # A scoped key acts as its user; it is never the all-scope technical identity.
    if get_scoped_api_key(holder) is not None:
        setattr(holder, "_lex_api_key_identity", None)
        return None

    identity = None
    try:
        raw_key = get_raw_api_key(holder)
        if raw_key:
            api_key = APIKey.objects.get_from_key(raw_key)
            api_key_name = str(api_key).strip() or "Technical User"
            identity = APIKeyRequestIdentity(
                api_key_name=api_key_name,
                user=TechnicalAPIKeyUser(api_key_name),
            )
    except Exception:
        identity = None

    setattr(holder, "_lex_api_key_identity", identity)
    return identity


def is_api_key_request(request) -> bool:
    return get_scoped_api_key(request) is not None or get_api_key_request_identity(request) is not None


def is_instance_api_key_request(request) -> bool:
    raw_key = get_raw_api_key(request)
    expected_key = os.getenv("LEX_API_KEY")
    return bool(raw_key and expected_key and raw_key == expected_key)
