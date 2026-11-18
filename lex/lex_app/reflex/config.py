"""The Reflex configuration a Lex App project's ``rxconfig.py`` returns.

Reflex reads its configuration from ``rxconfig.py`` in the directory it runs
in, so a project that serves Reflex dashboards has one -- ``lex reflex`` writes
it on first use -- and all it holds is::

    from lex.lex_app.reflex.config import lex_config

    config = lex_config()

This module must stay importable before Django is set up and before any
Reflex state exists: ``rxconfig.py`` is loaded by every Reflex process first,
and a state class imported here would re-enter the config it is part of.

It is a plain ``rx.Config``: lex-app's dashboards need no Reflex Enterprise and
no Reflex account (decision E1). Sign-in is lex-app's own, added by the app
itself (:mod:`lex.lex_app.reflex.session`), so no plugin carries it.
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

# No ports here, deliberately. Reflex treats a port in the config exactly like
# one passed on the command line, so a configured frontend port makes
# `--backend-only` refuse to start, and two configured ports make every
# `--env prod` run refuse (prod serves both on one port). `lex reflex` supplies
# lex-app's defaults instead, per run mode -- see `_resolve_reflex_port_flags`.


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


def public_origins() -> list[str]:
    """Every origin a Reflex page of this instance may be served from.

    One entry per hostname the dashboards answer on: the canonical one, plus
    any customer-owned domain attached to the instance. The backend's event
    websocket checks the browser's ``Origin`` against this list, so a hostname
    missing from it is an attached domain whose dashboards load and then fail
    to connect -- a blank page with a console error and nothing in any log.

    Empty -- meaning "leave Reflex's own default alone" -- when the instance
    has no public hostname configured at all, which is every local run.
    """
    from lex import hosts as lex_hosts

    origins = []
    for host in lex_hosts.public_hosts("reflex"):
        if not host or host.startswith("localhost") or host.startswith("127."):
            continue
        origins.append(f"https://{host}")
    return origins


def lex_config(**overrides: Any):
    """An ``rx.Config`` for the project, with lex-app's defaults.

    Any ``rx.Config`` option overrides the default of the same name, and
    ``REFLEX_<OPTION>`` environment variables override both, as in any Reflex
    app. ``plugins`` replaces the whole list: keep RFDS's two plugins in this
    order, or ``rfds.install()`` refuses to start.

    The plugins are Radix with RFDS's root theme, and RFDS itself with lex-app's
    brand and locale (:mod:`lex.lex_app.reflex.design`). The *Built with Reflex*
    badge is off, which plain Reflex honours in every run mode.
    """
    import reflex as rx

    from lex.lex_app.reflex.design import rfds_plugin, theme_plugin

    _make_app_module_findable()
    project_root = os.getenv("PROJECT_ROOT") or os.getcwd()
    settings: dict[str, Any] = {
        "app_name": app_name_for(project_root),
        "app_module_import": APP_MODULE,
        "telemetry_enabled": False,
        "show_built_with_reflex": False,
        "plugins": [theme_plugin(), rfds_plugin()],
        # A sitemap advertises pages to crawlers; every page here is behind a
        # sign-in, so it would advertise nothing a crawler could open.
        "disable_plugins": [rx.plugins.SitemapPlugin],
    }

    # No `api_url` here, deliberately. Reflex leaves it at `http://localhost:<backend
    # port>`, and its generated frontend rewrites a localhost backend URL to
    # `window.location` before opening the websocket (`getBackendURL` in
    # reflex's state.js). That is precisely the behaviour an instance with
    # custom domains needs: the dashboard addresses the backend on whichever
    # hostname the browser reached it on, so one build serves the platform
    # hostname and every attached customer domain. Pinning `api_url` to the
    # canonical URL -- which an earlier deployment did -- hard-codes one
    # hostname into the bundle, and every other hostname then opens a
    # cross-origin websocket to it, carrying no cookies.
    origins = public_origins()
    if origins:
        settings["cors_allowed_origins"] = origins

    settings.update(overrides)
    return rx.Config(**settings)
