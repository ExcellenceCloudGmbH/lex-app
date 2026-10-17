"""A broken lex_config.py stops startup instead of running with defaults.

Intent
------
Before, an exception while executing ``lex_config.py`` was only printed and
every extension key fell back to its default: the generic API open to everyone,
no database context, no project middleware — a misconfiguration nobody would
notice. ``validate_configuration()`` (called from lex's ``ready()``) now raises
``ImproperlyConfigured`` for a config that fails to load and resolves every
configured hook up front, so a typo in a dotted path is found at startup, not
at the first request. A project resolver for API keys that raises refuses the
key and logs the error instead of failing silently.

Cluster 1ax — scenarios 1.398–1.401. Type: U.
Covers: lex/core/extensions.py (validate_configuration), lex/lex_app/apps.py
(_validate_project_configuration), lex/api/utils/api_key_requests.py (resolver errors).
Run: python -m lex pytest lex/test_project/tests/init/test_1ax_config_validation.py -v
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, SimpleTestCase

from lex.api.utils import api_key_requests
from lex.core import extensions
from lex.lex_app import apps as lex_apps

pytestmark = pytest.mark.init


def broken_resolver(raw_key):
    raise RuntimeError("cluster 1ax: resolver down")


class TestCluster01ax_ConfigValidation(SimpleTestCase):
    """Cluster 1ax: validate_configuration."""

    def _project(self, source: str | None):
        """A temporary project root with this lex_config.py (or none)."""
        root = tempfile.mkdtemp(prefix="cluster1ax_")
        if source is not None:
            Path(root, "lex_config.py").write_text(source)
        patcher = mock.patch.dict(os.environ, {"PROJECT_ROOT": root})
        patcher.start()
        self.addCleanup(patcher.stop)
        extensions.reset_extension_cache()
        self.addCleanup(extensions.reset_extension_cache)

    # -- 1.398 ---------------------------------------------------------
    def test_1_398_a_config_that_fails_to_load_stops_startup(self) -> None:
        """
        Scenario 1.398: an exception inside lex_config.py is not swallowed.
        Given: a lex_config.py that raises while executing
        When: lex validates the configuration (as in ready())
        Then: ImproperlyConfigured naming lex_config.py and the error.
        """
        self._project('GENERIC_API = "superuser"\nraise RuntimeError("cluster 1ax boom")\n')
        with self.assertRaisesMessage(ImproperlyConfigured, "cluster 1ax boom"):
            lex_apps._validate_project_configuration()

    # -- 1.399 ---------------------------------------------------------
    def test_1_399_unresolvable_settings_stop_startup(self) -> None:
        """
        Scenario 1.399: a typo is found at startup, not at the first request.
        Given: one wrong value at a time — a hook path, a middleware path, the
               GENERIC_API mode, the API URLconf, the API key headers
        When: lex validates the configuration
        Then: each raises ImproperlyConfigured naming its key.
        """
        cases = {
            'DB_CONTEXT = "cluster1ax.missing.set_user"': "DB_CONTEXT",
            'MIDDLEWARE_EXTRA = {"first": ["cluster1ax.Missing"]}': "MIDDLEWARE_EXTRA",
            'GENERIC_API = "everyone"': "GENERIC_API",
            'API_URLCONF = "cluster1ax_missing.urls"': "API_URLCONF",
            "API_KEY_HEADERS = [42]": "API_KEY_HEADERS",
        }
        for source, key in cases.items():
            with self.subTest(key=key):
                self._project(source + "\n")
                with self.assertRaisesMessage(ImproperlyConfigured, key):
                    extensions.validate_configuration()

    # -- 1.400 ---------------------------------------------------------
    def test_1_400_valid_or_missing_config_passes(self) -> None:
        """
        Scenario 1.400: a correct configuration, or none, starts as before.
        Given: a lex_config.py whose hooks all resolve, then a project without one
        When: lex validates the configuration
        Then: no error, and the resolved hook is the configured callable.
        """
        self._project(
            'GENERIC_API = "superuser"\n'
            'DB_CONTEXT = "lex.test_project.tests.init.test_1ax_config_validation.broken_resolver"\n'
            'MIDDLEWARE_EXTRA = {"first": ["lex.core.middleware.project_slots.ProjectLastSlot"]}\n'
            'API_URLCONF = "lex.test_project.tests.api_layer._project_api"\n'
        )
        extensions.validate_configuration()
        self.assertIs(extensions.get_hook("DB_CONTEXT"), broken_resolver)
        self._project(None)
        extensions.validate_configuration()

    # -- 1.401 ---------------------------------------------------------
    def test_1_401_a_failing_key_resolver_refuses_and_logs(self) -> None:
        """
        Scenario 1.401: a resolver outage is visible in the log.
        Given: API_KEY_RESOLVER that raises
        When: a request presents an API key
        Then: the key is refused (None) and the error is logged with its traceback.
        """
        request = RequestFactory().get("/api/project/x", HTTP_API_KEY="cluster1ax-key")
        with extensions.override_extensions(API_KEY_RESOLVER=broken_resolver), \
                self.assertLogs("lex.api.utils.api_key_requests", level="ERROR") as logs:
            self.assertIsNone(api_key_requests.get_scoped_api_key(request))
        self.assertIn("API_KEY_RESOLVER", logs.output[0])
        self.assertIn("resolver down", "\n".join(logs.output))
