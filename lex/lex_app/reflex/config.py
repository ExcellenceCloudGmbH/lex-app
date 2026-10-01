"""The Reflex configuration a Lex App project's ``rxconfig.py`` returns.

Reflex reads its configuration from ``rxconfig.py`` in the directory it runs
in, so a project that serves Reflex dashboards has one -- ``lex reflex`` writes
it on first use -- and all it holds is::

    from lex.lex_app.reflex.config import lex_config

    config = lex_config()

This module must stay importable before Django is set up and before any
Reflex state exists: ``rxconfig.py`` is loaded by every Reflex process first,
and a provider class imported here would re-enter the config it is part of.
That is why the auth provider is named by import path, never imported.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

#: The module Reflex imports the app from: lex-app's own, which sets Django up
#: and serves the project's dashboards. The counterpart of ``streamlit_app.py``.
APP_MODULE = "lex.reflex_app"

#: The provider that signs users in against lex-app's Keycloak.
AUTH_PROVIDER = "lex.lex_app.reflex.auth.LexKeycloakAuthState"

#: The ``/login`` page: it starts the sign-in by itself instead of offering a
#: button, since lex-app has exactly one provider to sign in with.
LOGIN_PAGE = "lex.lex_app.reflex.auth.lex_login_page"

# No ports here, deliberately. Reflex treats a port in the config exactly like
# one passed on the command line, so a configured frontend port makes
# `--backend-only` refuse to start, and two configured ports make every
# `--env prod` run refuse (prod serves both on one port). `lex reflex` supplies
# lex-app's defaults instead, per run mode -- see `_resolve_reflex_port_flags`.

# No `offline_access` either, although Reflex's documentation suggests it for
# refresh tokens. Keycloak issues a refresh token for the authorization-code flow
# without it -- the Streamlit proxy has always renewed with exactly that -- and
# asking for it turns the refresh token into an OFFLINE token: one that outlives
# the Keycloak session, so signing out of lex-app would no longer end the
# dashboard's session, and a realm without the `offline_access` role refuses the
# sign-in outright. Without it, a dashboard session is bound to the Keycloak
# session like every other lex-app session.


def app_name_for(project_root: str | os.PathLike[str]) -> str:
    """A valid Reflex ``app_name`` for the project in ``project_root``.

    Reflex requires a Python identifier that is not ``reflex``; a project
    directory name is usually one already, and is made into one when not.
    """
    name = re.sub(r"[^0-9a-zA-Z_]", "_", Path(project_root).name)
    if not name or not name[0].isalpha():
        name = f"lex_{name}".rstrip("_")
    if name.lower() == "reflex":
        name = "reflex_dashboards"
    return name


def _make_app_module_findable() -> None:
    """Put the directory holding the ``lex`` package on ``sys.path``.

    Reflex locates the app module by scanning ``sys.path`` directories for its
    file (``reflex.utils.misc.get_module_path``), not through the import
    system. An editable install of lex-app -- how the framework itself is
    developed -- is reached through an import hook instead, which that scan
    cannot see, and Reflex reports ``Module lex.reflex_app not found``. For a
    regular install the directory is site-packages and already on the path.
    """
    import lex

    parent = str(Path(lex.__file__).resolve().parents[1])
    if parent not in sys.path:
        sys.path.append(parent)


def _repair_single_port_proxy() -> None:
    """Mount Reflex Enterprise's single-port proxy on the Reflex backend itself.

    ``use_single_port`` (``REFLEX_USE_SINGLE_PORT``) has the backend answer its
    own routes and pass every other request to the frontend server, so that a
    development run -- two servers -- works behind the one port a pod exposes.
    reflex-enterprise 0.9.6 never manages it on Reflex 0.9.12:

    * its lifespan task names its parameter ``app``, and Reflex passes the
      *Reflex* app under that name (the Starlette app only as
      ``starlette_app``), so it logs "Unable to find the base Starlette app";
    * given the Starlette app, it mounts on the outermost one, and Reflex
      reaches its backend through a catch-all ``Mount("")`` there. Behind an
      ``api_transformer`` that wraps the backend in ASGI middleware (RFDS's
      security headers do), the proxy lands after that mount and is never
      reached. Every page 404s either way.

    So the replacement takes the Reflex app and hands the proxy its backend,
    ``app._api`` -- where Reflex itself mounts a compiled frontend -- inside
    every wrapper, after every backend route. Reflex Enterprise imports the
    proxy when it builds the app, after ``rxconfig.py`` has run, so replacing
    the module's function is enough. The stand-in Reflex Enterprise defines
    when asgiproxy is missing, which names no parameter, is left alone, and so
    is this replacement itself.
    """
    import contextlib
    import inspect

    from reflex_enterprise import proxy

    upstream = proxy.proxy_middleware
    if getattr(upstream, "lex_mounts_on_backend", False):
        return
    if not {"app", "starlette_app"} & set(inspect.signature(upstream).parameters):
        return

    @contextlib.asynccontextmanager
    async def proxy_middleware(app):
        async with upstream(app._api):
            yield

    proxy_middleware.lex_mounts_on_backend = True
    proxy.proxy_middleware = proxy_middleware


def lex_auth_plugin(**options: Any):
    """``rxe.AuthPlugin``, signing users in against lex-app's Keycloak.

    Takes any ``AuthPlugin`` option -- ``auth=`` for an app-wide authorization
    check, ``extra_scopes`` for more claims, custom page builders, an audit hook.
    Both defaults are named by import path, which the plugin resolves at compile
    time: importing them here would create Reflex states while ``rxconfig.py``
    is still loading.
    """
    import reflex_enterprise as rxe

    # Here because every project's rxconfig.py calls this -- `lex_config()`
    # does, and so does a hand-written one that keeps lex-app's sign-in -- and
    # Reflex reads rxconfig.py before it builds the app.
    _repair_single_port_proxy()
    options.setdefault("auth_providers", [AUTH_PROVIDER])
    options.setdefault("login_page", LOGIN_PAGE)
    return rxe.AuthPlugin(**options)


def lex_config(**overrides: Any):
    """An ``rxe.Config`` for the project, with lex-app's defaults.

    Any ``rx.Config`` or ``rxe.Config`` option overrides the default of the same
    name, and ``REFLEX_<OPTION>`` environment variables override both, as in any
    Reflex app. ``plugins`` replaces the whole list: keep
    :func:`lex_auth_plugin` in it, or the dashboards stop requiring sign-in.
    """
    import reflex as rx
    import reflex_enterprise as rxe

    _make_app_module_findable()
    project_root = os.getenv("PROJECT_ROOT") or os.getcwd()
    settings: dict[str, Any] = {
        "app_name": app_name_for(project_root),
        "app_module_import": APP_MODULE,
        "telemetry_enabled": False,
        "plugins": [lex_auth_plugin(), rx.plugins.RadixThemesPlugin()],
        # A sitemap advertises pages to crawlers; every page here is behind a
        # sign-in, so it would advertise nothing a crawler could open.
        "disable_plugins": [rx.plugins.SitemapPlugin],
    }
    settings.update(overrides)
    return rxe.Config(**settings)
