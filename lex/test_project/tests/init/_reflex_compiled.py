"""``lex.reflex_app``, compiled once per test process, for every batch that reads the compiled app.

Compiling the app installs process-wide hooks -- RFDS's identity and permission
resolvers, the navigation's permission checker -- and takes seconds, so the
batches asserting on the compiled app (1aq, 1bt, 1by) share this one compile
instead of each making their own. It compiles the app exactly as ``reflex run``
would: in a project whose ``rxconfig.py`` is the one ``lex reflex`` writes, with
the reference ``_reflex_structure.py`` as the project's structure, in CI mode.
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
def example_model() -> type:
    """A model for the reference example's pages to be about.

    The example shows its pages for one of the project's models, and the
    ``lex_app`` app this suite compiles against has none of its own -- 1aq's
    probe models exist only once its module is imported. Defined here, the
    compile does not depend on which test module ran first. Never migrated:
    compiling reads no rows.
    """
    from django.db import models

    from lex.core.models.LexModel import LexModel

    class ReflexExampleFund(LexModel):
        name = models.CharField(max_length=100)

        class Meta:
            app_label = "lex_app"

    return ReflexExampleFund


@cache
def compiled_app() -> SimpleNamespace:
    """The compiled app and what its compile left behind, read once."""
    example_model()
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
