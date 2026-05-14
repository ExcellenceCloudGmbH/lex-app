from __future__ import annotations

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


def get_api_key_request_identity(request) -> Optional[APIKeyRequestIdentity]:
    holder = _get_request_holder(request)
    cached_identity = getattr(holder, "_lex_api_key_identity", _UNSET)
    if cached_identity is not _UNSET:
        return cached_identity

    identity = None
    try:
        raw_key = KeyParser().get(holder)
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
    return get_api_key_request_identity(request) is not None
