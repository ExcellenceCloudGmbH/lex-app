"""Unit tests for the lex.permissions.* tools."""
from __future__ import annotations

import asyncio
import sys
import types
from unittest import mock

import django
import pytest
from django.conf import settings
from mcp.shared.exceptions import McpError


def _ensure_django():
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            INSTALLED_APPS=[],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            USE_TZ=True,
        )
        django.setup()


_ensure_django()


def _install_view_stubs():
    """Stub the DRF view modules the tools lazy-import.

    Importing the real views drags in the full DRF + Lex stack which
    needs a real Django project; for these unit tests we only care that
    the tool wires the correct view through ``call_view`` (which we
    monkey-patch).
    """
    upv_mod = types.ModuleType("lex.api.views.authentication.UserPermissionView")
    upv_mod.UserPermissionsView = type("UserPermissionsView", (), {})
    sys.modules["lex.api.views.authentication.UserPermissionView"] = upv_mod

    mp_mod = types.ModuleType("lex.api.views.permissions.ModelPermissions")
    mp_mod.ModelPermissions = type("ModelPermissions", (), {})
    sys.modules["lex.api.views.permissions.ModelPermissions"] = mp_mod


_install_view_stubs()

from lex.mcp_server.context import McpPrincipal, bind_principal  # noqa: E402
from lex.mcp_server.tools import permissions  # noqa: E402


def _principal():
    return McpPrincipal(user=mock.Mock(is_authenticated=True), auth_kind="api_key")


def _patch_call_view(payload, status=200):
    async def _fake_call_view(*args, **kwargs):  # noqa: ANN001
        return status, payload

    return mock.patch.object(permissions, "call_view", _fake_call_view)


def test_user_permissions_returns_envelope():
    payload = [{"action": "read", "resource": "customer"}]

    with _patch_call_view(payload), bind_principal(_principal()):
        result = asyncio.run(permissions._user_permissions())

    assert result == {"status": 200, "result": payload}


def test_model_permissions_returns_envelope():
    payload = {"customer": {"can_read": True, "can_modify": False}}

    with mock.patch.object(
        permissions, "_require_container", return_value=mock.sentinel.container
    ), _patch_call_view(payload), bind_principal(_principal()):
        result = asyncio.run(permissions._model_permissions("customer"))

    assert result == {"status": 200, "result": payload}


def test_model_permissions_unknown_container_raises():
    def _raise(_id):
        from mcp.types import ErrorData, INVALID_PARAMS

        raise McpError(ErrorData(code=INVALID_PARAMS, message="unknown"))

    with mock.patch.object(permissions, "_require_container", side_effect=_raise):
        with bind_principal(_principal()):
            with pytest.raises(McpError):
                asyncio.run(permissions._model_permissions("nope"))
