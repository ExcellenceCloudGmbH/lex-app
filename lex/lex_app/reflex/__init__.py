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
``lex_page``              a signed-in page in lex-app's chrome (``@rx.page``'s twin)
``lex_chrome``            any component in lex-app's chrome
``permission``            the RFDS permission string for a Keycloak scope on a model
``LexAccessState``        loads the user's grants for RFDS's ``can()`` and navigation
``lex_api``               lex-app's REST API as the signed-in user (reads, writes)
``lex_table``             RFDS's server-paged table over a model, via the API
``lex_grid``              RFDS's AG Grid over a model's rows, via the API
``lex_columns``           RFDS column specs for a model's fields
``LexRecordState``        the record ``?model=&pk=`` names, checked against the API
``lex_rows``              a queryset as wire rows for charts (inside ``run_orm``)
``lex_view``              one lex-app page (table, form, record) in the dashboard
``lex_widgets``           lex-app's controls in one frame: ``calculation``, ``calculation_log``
``lex_calculation``       one calculation's status and button (and its log)
``Flow`` / ``ref``        chained forms, exactly as in Streamlit
========================  ====================================================

UI comes from ``rfds`` (and plain ``reflex``): the components, charts, tables
and page templates. Anything that knows about lex-app comes from here.

Nothing is imported until it is used. ``rxconfig.py`` imports
:mod:`lex.lex_app.reflex.config` through this package before Django or any
Reflex state exists, and an eager import here would create the states -- the
auth provider among them -- inside the config that configures them.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lex.lex_app.reflex.access import LexAccessState, permission
    from lex.lex_app.reflex.auth import (
        LexKeycloakAuthState,
        LexUser,
        current_access_token,
        current_permissions,
        has_permission,
    )
    from lex.lex_app.reflex.config import lex_auth_plugin, lex_config
    from lex.lex_app.reflex.dashboards import LexDashboardState, current_record
    from lex.lex_app.reflex.data import (
        LexApiError,
        LexRecordState,
        LexTableState,
        lex_api,
        lex_columns,
        lex_grid,
        lex_grid_columns,
        lex_rows,
        lex_table,
    )
    from lex.lex_app.reflex.django_orm import orm, run_orm
    from lex.lex_app.embed.core import STAY, Flow, ref
    from lex.lex_app.reflex.embed import (
        calculation,
        calculation_log,
        lex_calculation,
        lex_calculation_log,
        lex_calculation_log_tree,
        lex_view,
        lex_widgets,
    )
    from lex.lex_app.reflex.shell import lex_chrome, lex_page

_EXPORTS = {
    "LexAccessState": "lex.lex_app.reflex.access",
    "permission": "lex.lex_app.reflex.access",
    "lex_chrome": "lex.lex_app.reflex.shell",
    "lex_page": "lex.lex_app.reflex.shell",
    "LexKeycloakAuthState": "lex.lex_app.reflex.auth",
    "LexUser": "lex.lex_app.reflex.auth",
    "current_access_token": "lex.lex_app.reflex.auth",
    "current_permissions": "lex.lex_app.reflex.auth",
    "has_permission": "lex.lex_app.reflex.auth",
    "lex_auth_plugin": "lex.lex_app.reflex.config",
    "lex_config": "lex.lex_app.reflex.config",
    "LexDashboardState": "lex.lex_app.reflex.dashboards",
    "current_record": "lex.lex_app.reflex.dashboards",
    "LexApiError": "lex.lex_app.reflex.data",
    "LexRecordState": "lex.lex_app.reflex.data",
    "LexTableState": "lex.lex_app.reflex.data",
    "lex_api": "lex.lex_app.reflex.data",
    "lex_columns": "lex.lex_app.reflex.data",
    "lex_grid": "lex.lex_app.reflex.data",
    "lex_grid_columns": "lex.lex_app.reflex.data",
    "lex_rows": "lex.lex_app.reflex.data",
    "lex_table": "lex.lex_app.reflex.data",
    "calculation": "lex.lex_app.reflex.embed",
    "calculation_log": "lex.lex_app.reflex.embed",
    "lex_calculation": "lex.lex_app.reflex.embed",
    "lex_calculation_log": "lex.lex_app.reflex.embed",
    "lex_calculation_log_tree": "lex.lex_app.reflex.embed",
    "lex_view": "lex.lex_app.reflex.embed",
    "lex_widgets": "lex.lex_app.reflex.embed",
    "STAY": "lex.lex_app.embed.core",
    "Flow": "lex.lex_app.embed.core",
    "ref": "lex.lex_app.embed.core",
    "orm": "lex.lex_app.reflex.django_orm",
    "run_orm": "lex.lex_app.reflex.django_orm",
}

#: The same names, spelled out: tools that read the package without importing it
#: -- the docs gate among them -- see a module ``__getattr__`` plus ``__all__`` as
#: its exports (PEP 562). 1.357c keeps the two lists equal.
__all__ = [
    "Flow",
    "LexAccessState",
    "LexApiError",
    "LexDashboardState",
    "LexKeycloakAuthState",
    "LexRecordState",
    "LexTableState",
    "LexUser",
    "STAY",
    "calculation",
    "calculation_log",
    "current_access_token",
    "current_permissions",
    "current_record",
    "has_permission",
    "lex_api",
    "lex_auth_plugin",
    "lex_calculation",
    "lex_calculation_log",
    "lex_calculation_log_tree",
    "lex_chrome",
    "lex_columns",
    "lex_config",
    "lex_grid",
    "lex_grid_columns",
    "lex_page",
    "lex_rows",
    "lex_table",
    "lex_view",
    "lex_widgets",
    "orm",
    "permission",
    "ref",
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
