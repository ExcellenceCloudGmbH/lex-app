"""Project extension keys, environment switches and security flags.

Intent
------
A project extends lex-app through ``lex_config.py`` keys instead of
overriding Django settings (custom API, public paths, middleware, hooks).
Those keys must be read lazily, have documented defaults that leave lex
unchanged, and fail loudly when an import string is wrong — a silently
ignored hook could turn a security feature off. Deployment knobs that Django
reads while settings load are environment variables: ``DATABASE_PORT`` and
``LEX_CORS_ALLOWED_ORIGINS``. Since 3.0 the hardenings are on by default;
``LEX_LEGACY_SECURITY`` and ``LEX_SEC_<NAME>`` switch them off.

Cluster 1av — scenarios 1.386–1.395. Type: U.
Covers: lex/core/extensions.py, lex/core/config.py, lex/core/security_flags.py,
lex/lex_app/settings.py (DATABASE_PORT, LEX_CORS_ALLOWED_ORIGINS),
lex/core/middleware/project_slots.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1av_project_extensions.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

import lex
from lex.core import extensions, security_flags
from lex.core.config import LexProjectConfig
from lex.core.middleware.project_slots import (
    ProjectAfterAuthSlot,
    ProjectFirstSlot,
    ProjectLastSlot,
)

pytestmark = pytest.mark.init

_CHECKOUT = Path(lex.__file__).resolve().parent.parent

#: Calls recorded by the sample middleware below, in order.
CALLS: list = []


class FirstSampleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        CALLS.append("first-sample")
        return self.get_response(request)


class SecondSampleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        CALLS.append("second-sample")
        response = self.get_response(request)
        response["X-Second-Sample"] = "yes"
        return response


def sample_hook(*args, **kwargs):
    return "called"


def _settings_in_fresh_interpreter(env_overrides: dict, expression: str):
    """Evaluate ``expression`` on a freshly imported lex settings module."""
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_PORT", "LEX_CORS_ALLOWED_ORIGINS")}
    env.update(env_overrides)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_CHECKOUT), env.get("PYTHONPATH")) if p)
    code = (
        "import json\n"
        "from lex.lex_app import settings as s\n"
        f"print(json.dumps({expression}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=_CHECKOUT, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestCluster01av_ProjectExtensions(SimpleTestCase):
    """Cluster 1av: lex_config extension keys, env switches, security flags."""

    def setUp(self) -> None:
        extensions.reset_extension_cache()
        self.addCleanup(extensions.reset_extension_cache)
        CALLS.clear()

    # -- 1.386 ---------------------------------------------------------
    def test_1_386_defaults_leave_lex_unchanged(self) -> None:
        """
        Scenario 1.386: with no extension keys set, every key has its default.
        Given: the test project's lex_config.py, which sets none of the keys
        When: each key is read through lex.core.extensions
        Then: it equals the documented default, is_configured() is False for all,
              the generic API is open to all, and no hook resolves to a callable.
        """
        for key, default in extensions.EXTENSION_DEFAULTS.items():
            self.assertEqual(extensions.get_setting(key), default, key)
            self.assertFalse(extensions.is_configured(key), key)
        self.assertEqual(extensions.generic_api_mode(), "all")
        self.assertIsNone(extensions.get_hook("DB_CONTEXT"))
        self.assertEqual(extensions.api_prefix(), "api/project/")

    # -- 1.387 ---------------------------------------------------------
    def test_1_387_lex_config_values_are_read_and_hooks_resolved(self) -> None:
        """
        Scenario 1.387: keys in a project's lex_config.py are picked up lazily.
        Given: a lex_config.py that sets API_PREFIX, PUBLIC_PATHS and DB_CONTEXT
        When: LexProjectConfig.load() runs and the hook is resolved
        Then: the raw values are stored without importing anything, and
              get_hook() returns the callable named by the dotted path.
        """
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "lex_config.py").write_text(
                textwrap.dedent(
                    f"""
                    API_PREFIX = "api/sample/"
                    PUBLIC_PATHS = ["me"]
                    DB_CONTEXT = "{__name__}.sample_hook"
                    """
                )
            )
            with mock.patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                config = LexProjectConfig.load()
                self.assertEqual(
                    config.extensions,
                    {"API_PREFIX": "api/sample/", "PUBLIC_PATHS": ["me"], "DB_CONTEXT": f"{__name__}.sample_hook"},
                )
                extensions.reset_extension_cache()
                self.assertEqual(extensions.api_prefix(), "api/sample/")
                self.assertIs(extensions.get_hook("DB_CONTEXT"), sample_hook)
                self.assertTrue(extensions.is_configured("PUBLIC_PATHS"))

    # -- 1.388 ---------------------------------------------------------
    def test_1_388_unresolvable_hook_raises(self) -> None:
        """
        Scenario 1.388: a wrong import string is an error, never a silent no-op.
        Given: DB_CONTEXT pointing at a module that does not exist
        When: the hook is resolved
        Then: ImproperlyConfigured names the key and the value.
        """
        with extensions.override_extensions(DB_CONTEXT="no_such_module.set_user"):
            with self.assertRaisesMessage(ImproperlyConfigured, "DB_CONTEXT"):
                extensions.get_hook("DB_CONTEXT")
        with extensions.override_extensions(GENERIC_API="sometimes"):
            with self.assertRaises(ImproperlyConfigured):
                extensions.generic_api_mode()
        with extensions.override_extensions(API_PREFIX="/"):
            with self.assertRaises(ImproperlyConfigured):
                extensions.api_prefix()

    # -- 1.389 ---------------------------------------------------------
    def test_1_389_override_is_scoped_and_validated(self) -> None:
        """
        Scenario 1.389: override_extensions applies only inside its block.
        Given: an override of GENERIC_API and an attempt with an unknown key
        When: the value is read inside and after the block
        Then: inside it is overridden, afterwards the default is back, and an
              unknown key raises KeyError instead of being ignored.
        """
        with extensions.override_extensions(GENERIC_API="superuser"):
            self.assertEqual(extensions.generic_api_mode(), "superuser")
        self.assertEqual(extensions.generic_api_mode(), "all")
        with self.assertRaises(KeyError):
            with extensions.override_extensions(NOT_A_KEY=1):
                pass

    # -- 1.390 ---------------------------------------------------------
    def test_1_390_database_port_from_environment(self) -> None:
        """
        Scenario 1.390: DATABASE_PORT sets the port of every PostgreSQL target.
        Given: a fresh interpreter with and without DATABASE_PORT
        When: lex's settings module is imported
        Then: without it every PostgreSQL alias uses 5432 (unchanged); with
              DATABASE_PORT=6543 every PostgreSQL alias uses 6543.
        """
        expr = "sorted({c['PORT'] for c in s.DATABASES.values() if 'postgresql' in c['ENGINE']})"
        self.assertEqual(_settings_in_fresh_interpreter({}, expr), ["5432"])
        self.assertEqual(_settings_in_fresh_interpreter({"DATABASE_PORT": "6543"}, expr), ["6543"])

    # -- 1.391 ---------------------------------------------------------
    def test_1_391_cors_origins_from_environment(self) -> None:
        """
        Scenario 1.391: LEX_CORS_ALLOWED_ORIGINS replaces the permissive CORS defaults.
        Given: a fresh interpreter with and without LEX_CORS_ALLOWED_ORIGINS
        When: lex's settings module is imported
        Then: without it CORS allows all origins (unchanged); with it only the
              listed origins are allowed and "*" is gone.
        """
        expr = "[s.CORS_ORIGIN_ALLOW_ALL, s.CORS_ALLOWED_ORIGINS]"
        allow_all, _ = _settings_in_fresh_interpreter({}, expr)
        self.assertTrue(allow_all)
        allow_all, origins = _settings_in_fresh_interpreter(
            {"LEX_CORS_ALLOWED_ORIGINS": "https://app.example.com, https://admin.example.com"}, expr
        )
        self.assertFalse(allow_all)
        self.assertEqual(origins, ["https://app.example.com", "https://admin.example.com"])

    # -- 1.392 ---------------------------------------------------------
    def test_1_392_security_flags_default_on_with_opt_outs(self) -> None:
        """
        Scenario 1.392: hardenings are on by default and can be switched off.
        Given: no security environment variables
        When: each flag is read, then LEX_LEGACY_SECURITY and LEX_SEC_<NAME> are set
        Then: all flags are on by default; the umbrella switch turns all off;
              a per-flag value wins over the umbrella in both directions.
        """
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
        with mock.patch.dict(os.environ, clean, clear=True):
            self.assertTrue(all(security_flags.strict(name) for name in security_flags.FLAGS))
            with mock.patch.dict(os.environ, {"LEX_LEGACY_SECURITY": "true"}):
                self.assertFalse(any(security_flags.strict(name) for name in security_flags.FLAGS))
                with mock.patch.dict(os.environ, {"LEX_SEC_EDIT": "true"}):
                    self.assertTrue(security_flags.strict("EDIT"))
                    self.assertFalse(security_flags.strict("NOT_FOUND"))
            with mock.patch.dict(os.environ, {"LEX_SEC_FILTERS": "false"}):
                self.assertFalse(security_flags.strict("FILTERS"))
                self.assertTrue(security_flags.strict("EDIT"))
        with self.assertRaises(KeyError):
            security_flags.strict("NOT_A_FLAG")

    # -- 1.393 ---------------------------------------------------------
    def test_1_393_tracebacks_follow_debug_and_switches(self) -> None:
        """
        Scenario 1.393: tracebacks are shown only when DEBUG or explicitly allowed.
        Given: DEBUG off/on and the LEX_API_TRACEBACKS / legacy switches
        When: expose_tracebacks() is asked
        Then: off when DEBUG is off, on when DEBUG is on, on with
              LEX_API_TRACEBACKS=true, and on in legacy mode (pre-3.0 behaviour).
        """
        clean = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY", "LEX_API_TRACEBACKS"))
        }
        with mock.patch.dict(os.environ, clean, clear=True):
            with override_settings(DEBUG=False):
                self.assertFalse(security_flags.expose_tracebacks())
                with mock.patch.dict(os.environ, {"LEX_API_TRACEBACKS": "true"}):
                    self.assertTrue(security_flags.expose_tracebacks())
                with mock.patch.dict(os.environ, {"LEX_LEGACY_SECURITY": "true"}):
                    self.assertTrue(security_flags.expose_tracebacks())
            with override_settings(DEBUG=True):
                self.assertTrue(security_flags.expose_tracebacks())

    # -- 1.394 ---------------------------------------------------------
    def test_1_394_middleware_slots_pass_through_or_run_project_middleware(self) -> None:
        """
        Scenario 1.394: middleware slots are inert by default and run project middleware in order.
        Given: each slot with nothing configured, then "first" with two classes
        When: a request passes through the slot
        Then: an empty slot returns the inner response untouched; a configured
              slot runs the classes in list order and returns their response.
        """
        request = RequestFactory().get("/anything")
        inner = lambda req: HttpResponse("inner")  # noqa: E731
        for slot_class in (ProjectFirstSlot, ProjectAfterAuthSlot, ProjectLastSlot):
            response = slot_class(inner)(request)
            self.assertEqual(response.content, b"inner")
        self.assertEqual(CALLS, [])

        with extensions.override_extensions(
            MIDDLEWARE_EXTRA={"first": [f"{__name__}.FirstSampleMiddleware", f"{__name__}.SecondSampleMiddleware"]}
        ):
            response = ProjectFirstSlot(inner)(request)
            self.assertEqual(response["X-Second-Sample"], "yes")
            self.assertEqual(CALLS, ["first-sample", "second-sample"])
            # Another slot is unaffected.
            ProjectLastSlot(inner)(request)
            self.assertEqual(CALLS, ["first-sample", "second-sample"])

        with extensions.override_extensions(MIDDLEWARE_EXTRA={"middle": []}):
            with self.assertRaises(ImproperlyConfigured):
                ProjectFirstSlot(inner)

    # -- 1.395 ---------------------------------------------------------
    def test_1_395_project_prefix_must_not_overlap_lex_routes(self) -> None:
        """
        Scenario 1.395: a project API prefix that would shadow lex's routes is refused.
        Given: API_URLCONF set with prefixes that collide with lex's own paths
        When: the project URL patterns are built
        Then: ImproperlyConfigured is raised for "api/", "api/model_entries/x/"
              and "oidc/"; a distinct prefix builds one include pattern.
        """
        from lex.lex_app.urls import build_project_urlpatterns

        for bad in ("api/", "api/model_entries/x/", "oidc/", "admin/"):
            with extensions.override_extensions(API_URLCONF="lex.test_project.tests.init.test_1av_project_extensions", API_PREFIX=bad):
                with self.assertRaises(ImproperlyConfigured, msg=bad):
                    build_project_urlpatterns()
        with extensions.override_extensions(
            API_URLCONF="lex.test_project.tests.init.test_1av_project_extensions", API_PREFIX="api/sample/"
        ):
            patterns = build_project_urlpatterns()
        self.assertEqual(len(patterns), 1)
        self.assertEqual(str(patterns[0].pattern), "api/sample/")


#: Lets this module serve as an (empty) API_URLCONF in scenario 1.395.
urlpatterns: list = []
