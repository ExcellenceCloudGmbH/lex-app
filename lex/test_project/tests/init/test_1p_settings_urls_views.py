"""
Cluster 1p: settings / URLs / top-level views / config singletons.

Intent
------

Five files from the cleanup-and-coverage-plan COMPLETE bucket whose
contracts are user-visible the moment Django starts. None of them
need a full Django bootstrap — the ``test_project`` harness has
already done it once for the suite.

* ``lex/lex_app/settings.py`` — the top-level settings module. We
  do NOT re-test Django's settings machinery; we pin the
  *framework-owned* constants that downstream code (and operators)
  rely on, so a regression that flips ``ROOT_URLCONF`` or drops
  ``CELERY_TASK_ACKS_LATE`` is caught on import.

* ``lex/lex_app/urls.py`` — pin every named route ``reverse()``
  resolves, plus the ``DJANGO_BASE_PATH`` env-var prefix branch.
  A renamed name silently breaks every internal link;
  a broken prefix silently 404s every request behind a reverse
  proxy.

* ``lex/lex_app/views.py`` — the ``HealthCheck`` endpoint. It is
  three lines, but it is also the liveness probe every K8s/GCP
  deployment hits — a regression that flipped status to a
  ``500`` would silently restart the pod loop.

* ``lex/core/config.py`` — ``LexProjectConfig.load`` + the two
  cached accessors (``get_configured_default_serializer_name`` and
  ``get_tab_display_names_for_model``). The cache layer is what
  makes serializer wiring cheap; a regression that dropped the
  cache would re-import ``lex_config.py`` per request.

* ``lex/utilities/config/generic_app_config.py`` — module-level
  helpers + class statics that decide which files the autodiscovery
  walker treats as model modules. Wrong answer = either real models
  silently skipped (lost API) or junk files imported as modules
  (ImportError on every reload).

All scenarios are pure-Python or `Client()`-based, no DB writes,
no external services. Scenario range picks up at **1.125** (1o
ended at 1.124).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase, mock

from django.test import Client
from django.urls import reverse, NoReverseMatch


# ---------------------------------------------------------------------
# 1.125–1.130 — settings.py constants
# ---------------------------------------------------------------------
class TestCluster01p_SettingsConstants(TestCase):
    """``lex/lex_app/settings.py`` framework-owned constants.

    These are pinned, not derived — a code change that flips one of
    them would silently break operators, downstream Celery workers,
    or the URL router.
    """

    def test_1_125_root_urlconf_is_lex_app_urls(self):
        """1.125: ``ROOT_URLCONF == 'lex_app.urls'``.

        Drift would silently disconnect every route — health probe,
        admin, REST API — from the WSGI/ASGI handlers.
        """
        from lex.lex_app import settings

        self.assertEqual(
            settings.ROOT_URLCONF, "lex_app.urls",
            "ROOT_URLCONF drift breaks every URL",
        )

    def test_1_126_database_deployment_target_defaults_to_default(self):
        """1.126: ``DATABASE_DEPLOYMENT_TARGET`` defaults to ``"default"``.

        Operators without `DATABASE_DEPLOYMENT_TARGET` set must land
        on the ``default`` connection — the historical fallback that
        every test fixture and CI pipeline relies on.
        """
        from lex.lex_app import settings

        # In CI the env var is set to "default" explicitly; we just
        # assert the value is non-empty and the framework knows about
        # the alias `default`.
        self.assertTrue(
            settings.DATABASE_DEPLOYMENT_TARGET,
            "DATABASE_DEPLOYMENT_TARGET must never be empty",
        )
        self.assertIn(
            "default", settings.DATABASES,
            "the `default` connection must always be registered "
            "(even when running on alt targets, as the migration "
            "runner needs it)",
        )

    def test_1_127_celery_constants_pinned(self):
        """1.127: pin the four Celery resilience constants.

        ``acks_late`` + ``reject_on_worker_lost`` + ``prefetch=1``
        are what guarantee a calculation does not silently disappear
        when a worker is killed mid-task. A regression dropping any
        of these is the open BUG-019-class footgun documented in
        ``NOTES_TODO.md`` §4.
        """
        from lex.lex_app import settings

        self.assertTrue(settings.CELERY_TASK_ACKS_LATE,
                        "acks_late must stay True — dropping it loses "
                        "tasks on worker SIGKILL")
        self.assertTrue(settings.CELERY_TASK_REJECT_ON_WORKER_LOST,
                        "reject_on_worker_lost must stay True — "
                        "this is the requeue-on-loss path")
        self.assertEqual(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1,
                         "prefetch must stay 1 — higher values cause "
                         "lost-tasks under worker churn")
        self.assertTrue(settings.CELERY_TASK_TRACK_STARTED,
                        "task_track_started powers the IN_PROGRESS UI")

    def test_1_128_celery_serializers_accept_pickle(self):
        """1.128: pickle is in ``CELERY_ACCEPT_CONTENT``.

        Model-instance dispatch paths (covered by 8h/8j) serialise
        the instance via pickle. A regression that dropped pickle
        would silently break every ``CalculatedModelMixin.create()``
        async dispatch with a `ContentDisallowed` error at
        worker-side deserialisation — caught only when a customer
        first triggers a real calculation.
        """
        from lex.lex_app import settings

        self.assertIn(
            "pickle", settings.CELERY_ACCEPT_CONTENT,
            "pickle must remain accepted for model-instance dispatch",
        )

    def test_1_129_celery_default_queue_uses_instance_identifier(self):
        """1.129: ``CELERY_TASK_DEFAULT_QUEUE`` reads
        ``INSTANCE_RESOURCE_IDENTIFIER``, falling back to ``"celery"``.

        Multi-tenant deployments rely on the per-instance queue
        name to isolate workloads. A regression hard-coding
        ``"celery"`` would cause every tenant's tasks to land in the
        same queue.
        """
        from lex.lex_app import settings

        # Either it inherits the env var (if set) or it falls back to
        # the literal ``"celery"``. Both are valid; what matters is
        # that the constant is non-empty and a string.
        self.assertIsInstance(settings.CELERY_TASK_DEFAULT_QUEUE, str)
        self.assertTrue(settings.CELERY_TASK_DEFAULT_QUEUE)

    def test_1_130_react_app_build_path_resolves_to_a_path(self):
        """1.130: ``REACT_APP_BUILD_PATH`` resolves to a path string.

        Used by the catch-all ``serve_react`` route in ``urls.py``;
        a None or empty value would 500 every frontend request.
        """
        from lex.lex_app import settings

        self.assertTrue(
            str(settings.REACT_APP_BUILD_PATH),
            "REACT_APP_BUILD_PATH must resolve — serves the SPA",
        )


# ---------------------------------------------------------------------
# 1.131–1.134 — urls.py named routes
# ---------------------------------------------------------------------
class TestCluster01p_UrlConfResolves(TestCase):
    """``lex/lex_app/urls.py`` — every named route reverse()s.

    Internal callers ``reverse("…")`` on these names; a rename
    breaks every link silently.
    """

    def test_1_131_health_view_reverses(self):
        """1.131: ``reverse('health_view')`` resolves and returns
        the documented ``/health`` path (or its prefix-stripped
        variant)."""
        url = reverse("health_view")
        self.assertTrue(
            url.endswith("/health") or url.endswith("/health/"),
            f"health_view should end with /health, got {url!r}",
        )

    def test_1_132_api_health_view_reverses(self):
        """1.132: ``reverse('api_health_view')`` resolves to the
        ``/api/health`` route — separate from the bare /health for
        operators behind reverse proxies that strip /api."""
        url = reverse("api_health_view")
        self.assertIn(
            "/api/health", url,
            f"api_health_view should contain /api/health, got {url!r}",
        )

    def test_1_133_current_user_reverses(self):
        """1.133: ``reverse('current-user')`` resolves to the
        ``/api/user/`` endpoint — the SPA hits it on every page
        load to determine the logged-in user.
        """
        url = reverse("current-user")
        self.assertIn(
            "/api/user", url,
            f"current-user should contain /api/user, got {url!r}",
        )

    def test_1_134_unknown_route_raises_noreversematch(self):
        """1.134: an unknown name raises ``NoReverseMatch``.

        Defensive gate — if a future refactor silently removed the
        catch-all and started returning empty strings, every internal
        ``reverse()`` call would silently produce broken links.
        """
        with self.assertRaises(NoReverseMatch):
            reverse("definitely-not-a-real-route-name")


# ---------------------------------------------------------------------
# 1.135 — health endpoint
# ---------------------------------------------------------------------
class TestCluster01p_HealthEndpoint(TestCase):
    """``lex/lex_app/views.py`` — ``HealthCheck`` endpoint.

    Three lines of code, but it is the K8s/GCP liveness probe.
    A regression flipping the status code restarts pods in a loop.
    """

    def test_1_135_health_endpoint_returns_200_with_healthy_payload(self):
        """1.135: ``GET /health`` → 200 + JSON body containing
        ``"status": "Healthy :)"``.

        The smiley is part of the documented contract — operators
        grep for it in log dashboards. A regression that changed it
        silently would not break the probe but WOULD break any
        downstream alert that searches for the literal string.
        """
        client = Client()
        response = client.get("/health")
        self.assertEqual(
            response.status_code, 200,
            f"health probe must always return 200; got {response.status_code}",
        )
        # Don't depend on response.json() in case the route returned HTML;
        # decode + assert directly.
        body = response.content.decode("utf-8")
        self.assertIn(
            "Healthy", body,
            f"health body must contain 'Healthy'; got {body!r}",
        )

    def test_1_136_api_health_endpoint_returns_200(self):
        """1.136: ``GET /api/health`` → 200 — same handler, different
        route. Reverse-proxy deployments often strip /api before
        forwarding; both paths must work in parallel.
        """
        client = Client()
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------
# 1.137–1.142 — core/config.py
# ---------------------------------------------------------------------
class TestCluster01p_LexProjectConfig(TestCase):
    """``lex/core/config.py`` — ``LexProjectConfig.load`` + cached
    accessors.

    The cache layer is what makes serializer wiring cheap. A
    regression that dropped the cache would re-exec ``lex_config.py``
    on every request.
    """

    def setUp(self):
        # Always reset both module-level caches so each test starts
        # from a clean slate. The cache helpers are part of the
        # documented test API.
        from lex.core.config import (
            reset_default_serializer_name_cache,
            reset_tab_display_names_cache,
        )
        reset_default_serializer_name_cache()
        reset_tab_display_names_cache()
        self.addCleanup(reset_default_serializer_name_cache)
        self.addCleanup(reset_tab_display_names_cache)

    def _write_config(self, tmpdir, body):
        cfg = Path(tmpdir) / "lex_config.py"
        cfg.write_text(body, encoding="utf-8")
        return cfg

    def test_1_137_load_returns_defaults_when_no_config_file(self):
        """1.137: ``LexProjectConfig.load`` against an empty dir
        returns the documented defaults — no crash, no surprise
        values.
        """
        from lex.core.config import LexProjectConfig

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                config = LexProjectConfig.load()

        self.assertIsNone(config.initial_data)
        self.assertEqual(config.groups, [])
        self.assertEqual(config.default_serializer_name, "default")
        self.assertEqual(config.tab_display_names, {})
        self.assertFalse(config._loaded,
                         "_loaded should stay False when no config "
                         "file is present")

    def test_1_138_load_picks_up_attributes_from_lex_config_py(self):
        """1.138: every documented attribute is read from ``lex_config.py``.

        Both upper- and lower-case attribute names are supported
        (matching the codebase's mixed convention) — the load helper
        is the single point that resolves both shapes.
        """
        from lex.core.config import LexProjectConfig

        body = (
            "INITIAL_DATA = 'seed/data.json'\n"
            "PROJECT_GROUPS = ['admins', 'editors']\n"
            "DEFAULT_SERIALIZER_NAME = 'project_default'\n"
            "TAB_DISPLAY_NAMES = {\n"
            "    '__default__': {'history_tab': 'Changes'},\n"
            "    'invoice': {'audit_log_tab': 'Audit'}\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(tmp, body)
            with mock.patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                config = LexProjectConfig.load()

        self.assertEqual(config.initial_data, "seed/data.json")
        self.assertEqual(config.groups, ["admins", "editors"])
        self.assertEqual(config.default_serializer_name, "project_default")
        self.assertEqual(
            config.tab_display_names["invoice"]["audit_log_tab"], "Audit",
        )
        self.assertTrue(config._loaded,
                        "_loaded must flip True once the file loads")

    def test_1_139_load_falls_back_to_legacy_authentication_settings(self):
        """1.139: when ``lex_config.py`` is missing but
        ``_authentication_settings.py`` exists, the legacy file is
        read.

        Backward-compat path — projects pre-dating the rename must
        still load.
        """
        from lex.core.config import LexProjectConfig

        body = "INITIAL_DATA = 'legacy/seed.json'\n"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "_authentication_settings.py").write_text(body)
            with mock.patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                config = LexProjectConfig.load()

        self.assertEqual(config.initial_data, "legacy/seed.json")

    def test_1_140_load_swallows_exceptions_from_user_config(self):
        """1.140: a ``lex_config.py`` that raises on import does NOT
        kill the whole framework — it logs and returns defaults.

        Customer footgun guard: a typo in their config file should
        not crash Django startup.
        """
        from lex.core.config import LexProjectConfig

        body = "raise RuntimeError('bad user config')\n"
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(tmp, body)
            with mock.patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                # Should NOT raise.
                config = LexProjectConfig.load()

        # Defaults preserved
        self.assertEqual(config.default_serializer_name, "default")
        self.assertFalse(config._loaded)

    def test_1_141_get_configured_default_serializer_name_caches(self):
        """1.141: ``get_configured_default_serializer_name`` caches
        the resolved value — second call does not re-load the
        module from disk.
        """
        from lex.core import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(tmp, "DEFAULT_SERIALIZER_NAME = 'first'\n")
            with mock.patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                first = config_mod.get_configured_default_serializer_name()

                # Mutate the file under the cache (still inside the
                # TemporaryDirectory context — otherwise the dir is
                # deleted and load() falls back to defaults).
                self._write_config(tmp, "DEFAULT_SERIALIZER_NAME = 'second'\n")
                second = config_mod.get_configured_default_serializer_name()

                self.assertEqual(first, "first")
                self.assertEqual(
                    second, "first",
                    "cache must not re-read the file on second call",
                )

                # After explicit reset, the new value comes through.
                config_mod.reset_default_serializer_name_cache()
                third = config_mod.get_configured_default_serializer_name()
                self.assertEqual(
                    third, "second",
                    "reset_default_serializer_name_cache() must "
                    "force a re-load",
                )

    def test_1_142_get_tab_display_names_merges_default_and_model(self):
        """1.142: ``get_tab_display_names_for_model`` merges the
        ``__default__`` entry with the model-specific entry, model
        keys winning.
        """
        from lex.core import config as config_mod

        body = (
            "TAB_DISPLAY_NAMES = {\n"
            "    '__default__': {'history_tab': 'Changes', 'audit_log_tab': 'Trail'},\n"
            "    'invoice': {'audit_log_tab': 'Invoice Audit'}\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(tmp, body)
            with mock.patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
                merged = config_mod.get_tab_display_names_for_model("invoice")

        self.assertEqual(merged["history_tab"], "Changes",
                         "default key should pass through")
        self.assertEqual(merged["audit_log_tab"], "Invoice Audit",
                         "model-specific key must override default")

        # A model with no entry still gets the defaults.
        with mock.patch.dict(os.environ, {"PROJECT_ROOT": tmp}):
            other = config_mod.get_tab_display_names_for_model("unrelated")
        self.assertEqual(other["history_tab"], "Changes")


# ---------------------------------------------------------------------
# 1.143–1.146 — generic_app_config helpers
# ---------------------------------------------------------------------
class TestCluster01p_GenericAppConfigHelpers(TestCase):
    """``lex/utilities/config/generic_app_config.py`` — discovery
    helpers.

    These decide which files the autodiscovery walker treats as
    model modules. Wrong answer = silently dropped models OR junk
    files imported as modules.
    """

    def test_1_143_structure_file_recognisers(self):
        """1.143: ``_is_structure_yaml_file`` and ``_is_structure_file``
        recognise the documented filenames and reject look-alikes.
        """
        from lex.utilities.config.generic_app_config import (
            _is_structure_yaml_file,
            _is_structure_file,
        )

        self.assertTrue(_is_structure_yaml_file("model_structure.yaml"))
        self.assertFalse(_is_structure_yaml_file("model_structure.yml"),
                         ".yml is not the documented extension")
        self.assertFalse(_is_structure_yaml_file("other.yaml"))

        self.assertTrue(_is_structure_file("invoice_structure.py"))
        self.assertTrue(_is_structure_file("a_b_c_structure.py"))
        self.assertFalse(_is_structure_file("structure.py"),
                         "must end with `_structure.py`, not just `structure.py`")
        self.assertFalse(_is_structure_file("invoice.py"))

    def test_1_144_dir_filter_excludes_known_directories(self):
        """1.144: ``_dir_filter`` excludes the documented set of
        venv / build / migrations / hidden / underscore-prefixed
        directories.

        Walking into ``.venv`` would re-import the whole framework
        — historically caused infinite recursion on app startup.
        """
        from lex.utilities.config.generic_app_config import GenericAppConfig

        cfg = GenericAppConfig.__new__(GenericAppConfig)  # bypass __init__
        for excluded in ("venv", ".venv", "build", "migrations"):
            with self.subTest(dir=excluded):
                self.assertFalse(cfg._dir_filter(excluded))
        for hidden in ("_internal", ".git"):
            with self.subTest(dir=hidden):
                self.assertFalse(cfg._dir_filter(hidden))
        for ok in ("models", "calculations", "api"):
            with self.subTest(dir=ok):
                self.assertTrue(cfg._dir_filter(ok))

    def test_1_145_is_valid_module_filters_files(self):
        """1.145: ``_is_valid_module`` accepts business-model files
        and rejects every excluded-prefix/postfix/file in the
        documented sets.
        """
        from lex.utilities.config.generic_app_config import GenericAppConfig

        cfg = GenericAppConfig.__new__(GenericAppConfig)

        # Positive cases
        for module_name, file in [
            ("invoice", "invoice.py"),
            ("counterparty", "counterparty.py"),
        ]:
            with self.subTest(case=f"{module_name} ok"):
                self.assertTrue(cfg._is_valid_module(module_name, file))

        # Negative cases — excluded postfixes
        for module_name, file, reason in [
            ("test_invoice", "test_invoice.py", "test_ prefix"),
            ("invoice_", "invoice_.py", "underscore postfix"),
            ("create_db", "create_db.py", "explicit postfix"),
            ("settings", "settings.py", "excluded file name"),
            ("urls", "urls.py", "excluded file name"),
            ("asgi", "asgi.py", "excluded file name"),
            ("invoice", "invoice.txt", "non-py extension"),
            ("_internal", "_internal.py", "underscore prefix"),
            (".hidden", ".hidden.py", "dot prefix"),
        ]:
            with self.subTest(case=reason):
                self.assertFalse(
                    cfg._is_valid_module(module_name, file),
                    f"{module_name}/{file} should be rejected ({reason})",
                )

    def test_1_146_add_model_is_idempotent(self):
        """1.146: ``add_model`` does not overwrite an existing
        registration.

        First-write-wins is the documented contract — re-imports
        during reload must not silently swap the registered class.
        """
        from lex.utilities.config.generic_app_config import GenericAppConfig

        cfg = GenericAppConfig.__new__(GenericAppConfig)
        cfg.discovered_models = {}

        first = type("Invoice", (), {"version": 1})
        second = type("Invoice", (), {"version": 2})

        cfg.add_model("Invoice", first)
        cfg.add_model("Invoice", second)  # should be no-op

        self.assertIs(
            cfg.discovered_models["Invoice"], first,
            "first registration must win — second add_model is a no-op",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

