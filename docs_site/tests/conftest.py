"""Shared fixtures: the docs app, compiled once per test run, exactly as `reflex run` would."""

import contextlib
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SITE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def compiled():
    from reflex_base.registry import RegistrationContext

    with contextlib.chdir(SITE), RegistrationContext.ensure_context().fork():
        os.environ["CI"] = "true"                       # RFDS conformance: error, as in CI
        sys.modules.pop("docs_site.docs_site", None)
        app = importlib.import_module("docs_site.docs_site").app
        app._compile(dry_run=True)
        yield SimpleNamespace(app=app, pages=dict(app._pages))
