"""The Reflex surface of lex-app.

Everything a dashboard author needs is importable from here::

    from lex.lex_app.reflex import current_record, run_orm, has_permission

========================  ====================================================
``current_record``        the record ``?model=&pk=`` names, from any state
``run_orm`` / ``orm``     synchronous Django code, from an async event handler
``current_permissions``   the user's Keycloak UMA permissions
``has_permission``        an ``auth=`` check built from one of them
``current_access_token``  the user's Keycloak access token, for the lex-app API
``LexUser``               the signed-in user's names, as frontend vars
``lex_config``            the ``rxe.Config`` a project's ``rxconfig.py`` returns
========================  ====================================================

Nothing is imported until it is used. ``rxconfig.py`` imports
:mod:`lex.lex_app.reflex.config` through this package before Django or any
Reflex state exists, and an eager import here would create the states -- the
auth provider among them -- inside the config that configures them.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lex.lex_app.reflex.auth import (
        LexKeycloakAuthState,
        LexUser,
        current_access_token,
        current_permissions,
        has_permission,
    )
    from lex.lex_app.reflex.config import lex_auth_plugin, lex_config
    from lex.lex_app.reflex.dashboards import LexDashboardState, current_record
    from lex.lex_app.reflex.django_orm import orm, run_orm

_EXPORTS = {
    "LexKeycloakAuthState": "lex.lex_app.reflex.auth",
    "LexUser": "lex.lex_app.reflex.auth",
    "current_access_token": "lex.lex_app.reflex.auth",
    "current_permissions": "lex.lex_app.reflex.auth",
    "has_permission": "lex.lex_app.reflex.auth",
    "lex_auth_plugin": "lex.lex_app.reflex.config",
    "lex_config": "lex.lex_app.reflex.config",
    "LexDashboardState": "lex.lex_app.reflex.dashboards",
    "current_record": "lex.lex_app.reflex.dashboards",
    "orm": "lex.lex_app.reflex.django_orm",
    "run_orm": "lex.lex_app.reflex.django_orm",
}

#: The same names, spelled out: tools that read the package without importing it
#: -- the docs gate among them -- see a module ``__getattr__`` plus ``__all__`` as
#: its exports (PEP 562). 1.357c keeps the two lists equal.
__all__ = [
    "LexDashboardState",
    "LexKeycloakAuthState",
    "LexUser",
    "current_access_token",
    "current_permissions",
    "current_record",
    "has_permission",
    "lex_auth_plugin",
    "lex_config",
    "orm",
    "run_orm",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
