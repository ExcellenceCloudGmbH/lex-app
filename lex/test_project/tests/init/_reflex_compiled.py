"""``lex.reflex_app``, compiled once per test process, for every batch that reads the compiled app.

Reflex Enterprise's auth plugin binds itself to the process on the first compile
and refuses another ("Only one AuthPlugin may be active per process"), so the
batches asserting on the compiled app (1aq, 1bt) share this one compile instead
of each making their own. It compiles the app exactly as ``reflex run`` would:
in a project whose ``rxconfig.py`` is the one ``lex reflex`` writes, with the
reference ``_reflex_structure.py`` as the project's structure, in CI mode.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import tempfile
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

#: Variables a developer's shell may carry that would change what is compiled.
_CLEARED = (
    "LEX_FORMAT_LOCALE", "LEX_UI_LANGUAGE", "LEX_BASE_CURRENCY", "LEX_TIME_ZONE",
    "LEX_RFDS_CONFORMANCE", "LEX_APP_HOSTS", "DOMAIN_HOSTED", "LEX_DASHBOARD_TITLE",
    "LEX_ENVIRONMENT",
)


@cache
def compiled_app() -> SimpleNamespace:
    """The compiled app and what its compile left behind, read once."""
    from reflex.config import get_config
    from reflex_base.registry import RegistrationContext

    from lex.bin.lex import _ensure_reflex_config
    from lex.lex_app.reflex.examples import _reflex_structure as example

    env = {**{key: "" for key in _CLEARED}, "CI": "true"}
    with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp), patch.dict(os.environ, env), \
            patch("lex.lex_app.reflex.dashboards._project_app_label", return_value="lex_app"), \
            patch("lex.lex_app.reflex.dashboards.load_structure", return_value=example), \
            RegistrationContext.ensure_context().fork():
        _ensure_reflex_config(Path(tmp))
        sys.modules.pop("lex.reflex_app", None)
        try:
            app = importlib.import_module("lex.reflex_app").app
            app._compile(dry_run=True)
            from rfds.auth.identity import _PERMISSIONS, _RESOLVER
            from rfds.components.navigation.side_nav import _CHECKER

            return SimpleNamespace(
                app=app,
                pages=dict(app._pages),
                index_on_load=list(app._unevaluated_pages["index"].on_load),
                middlewares=list(app._middlewares),
                plugins=list(get_config().plugins),
                head=[str(component) for component in app.head_components],
                identity_resolver=list(_RESOLVER),
                permission_resolver=list(_PERMISSIONS),
                nav_checker=list(_CHECKER),
            )
        finally:
            sys.modules.pop("lex.reflex_app", None)
