"""Cluster 1s: log-noise cleanup + lex-namespace debug control (EXC-1787).

Intent
------
A customer running LEX App V2 locally reported two log-ergonomics problems
(ticket EXC-1787):

1. **`InsecureRequestWarning` spam.** The Keycloak admin client may run with TLS
   verification disabled, so urllib3 prints an ``InsecureRequestWarning`` on
   *every* request to the auth host. The framework's documented intent is that
   local logs are clean by default, with the warning suppressed at startup and an
   opt-out (``LEX_SUPPRESS_INSECURE_WARNING=False``) for anyone debugging TLS.

2. **No targeted debug control.** Operators must be able to see the framework's
   own ``logger.debug(...)`` output *without* turning on the firehose of
   third-party DEBUG logging that the blanket ``LOG_LEVEL=DEBUG`` pulls in. The
   intent: ``LEX_LOG_LEVEL=DEBUG`` raises only the ``lex.*`` namespace while the
   root logger stays at ``INFO`` so non-lex libraries are still gated.

Why a regression matters: these are the exact knobs a customer toggles on their
own machine. If the suppression default flips, the original ticket reopens; if
``LEX_LOG_LEVEL`` stops isolating the ``lex`` namespace (e.g. the console handler
silently drops lex DEBUG, or root gets elevated), debugging the framework locally
becomes impossible without the third-party noise.

These are pure-Python, no-DB assertions on the framework-owned settings module.
Each branch is exercised by reloading ``lex.lex_app.settings`` under a patched
environment and restoring the process state afterwards. ``sentry_sdk.init`` is
mocked across every reload so re-executing the module never re-initialises Sentry.

Cluster 1s — scenarios 1.159–1.168. Type: U.
Covers: lex/lex_app/settings.py (InsecureRequestWarning suppression gate,
        the blanket ``LEX_SUPPRESS_WARNINGS`` Python-warnings filter,
        LEX_LOG_LEVEL, the ``lex`` logger entry, console-handler level derivation).
Run: python -m lex pytest lex/test_project/tests/init/test_1s_log_cleanup_and_lex_debug.py -v
"""

from __future__ import annotations

import importlib
import logging
import os
import unittest
from unittest import mock

import urllib3

import pytest
from django.test import SimpleTestCase

from lex.lex_app import settings as lex_settings

pytestmark = pytest.mark.init


# Environment keys these tests mutate; saved in setUp and restored in cleanup so
# the reloaded settings module is returned to the process's real configuration.
_TOUCHED_KEYS = (
    "LOG_LEVEL",
    "LEX_LOG_LEVEL",
    "LEX_SUPPRESS_INSECURE_WARNING",
    "LEX_SUPPRESS_WARNINGS",
)


class _SettingsReloadCase(SimpleTestCase):
    """Base: save/restore env + reload ``lex.lex_app.settings`` safely.

    Reloading re-executes the settings module top-to-bottom, which is how we
    exercise both branches of an import-time, env-driven decision. ``sentry_sdk.init``
    is always patched out during a reload so the re-execution is side-effect free.
    """

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in _TOUCHED_KEYS}
        self.addCleanup(self._restore_env_and_reload)

    def _restore_env_and_reload(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        with mock.patch("sentry_sdk.init"):
            importlib.reload(lex_settings)

    def _reload(self, set_env=None, unset_env=()):
        for key in unset_env:
            os.environ.pop(key, None)
        for key, value in (set_env or {}).items():
            os.environ[key] = value
        with mock.patch("sentry_sdk.init"):
            importlib.reload(lex_settings)
        return lex_settings


# ---------------------------------------------------------------------
# 1.159–1.161 — InsecureRequestWarning suppression gate
# ---------------------------------------------------------------------
class TestCluster01s_InsecureWarningSuppression(_SettingsReloadCase):
    """``lex/lex_app/settings.py`` — urllib3 InsecureRequestWarning gate.

    The ticket's whole point: clean local logs by default, with an explicit
    opt-out for anyone who wants the TLS warning back while debugging.
    """

    def test_1_159_warning_suppressed_by_default(self):
        """1.159: with the env var unset, the warning is suppressed.

        Given: ``LEX_SUPPRESS_INSECURE_WARNING`` is not set (the local default).
        When:  the settings module is imported/reloaded.
        Then:  ``urllib3.disable_warnings`` is called once with the
               ``InsecureRequestWarning`` category — the documented default that
               keeps customer logs clean.
        """
        os.environ.pop("LEX_SUPPRESS_INSECURE_WARNING", None)
        with mock.patch("sentry_sdk.init"), \
                mock.patch("urllib3.disable_warnings") as mock_disable:
            importlib.reload(lex_settings)

        mock_disable.assert_called_once_with(
            urllib3.exceptions.InsecureRequestWarning,
        )

    def test_1_160_warning_not_suppressed_when_opted_out(self):
        """1.160: ``LEX_SUPPRESS_INSECURE_WARNING=False`` restores the warning.

        Given: an operator who wants the TLS warning back to debug certificates.
        When:  they set ``LEX_SUPPRESS_INSECURE_WARNING=False`` and start the app.
        Then:  the suppression is NOT installed — ``disable_warnings`` is never
               called, so urllib3 emits its warning as normal.
        """
        os.environ["LEX_SUPPRESS_INSECURE_WARNING"] = "False"
        with mock.patch("sentry_sdk.init"), \
                mock.patch("urllib3.disable_warnings") as mock_disable:
            importlib.reload(lex_settings)

        mock_disable.assert_not_called()

    def test_1_161_opt_out_is_case_insensitive(self):
        """1.161: the opt-out value is parsed case-insensitively.

        Given: an operator who writes ``LEX_SUPPRESS_INSECURE_WARNING=FALSE``.
        When:  the app starts.
        Then:  suppression is still skipped — the gate lower-cases the value, so
               ``FALSE``/``False``/``false`` are all honoured. A regression to a
               case-sensitive ``== "false"`` check would silently re-suppress.
        """
        os.environ["LEX_SUPPRESS_INSECURE_WARNING"] = "FALSE"
        with mock.patch("sentry_sdk.init"), \
                mock.patch("urllib3.disable_warnings") as mock_disable:
            importlib.reload(lex_settings)

        mock_disable.assert_not_called()


# ---------------------------------------------------------------------
# 1.162–1.165 — lex-namespace debug level + console handler derivation
# ---------------------------------------------------------------------
class TestCluster01s_LexNamespaceDebugLevel(_SettingsReloadCase):
    """``lex/lex_app/settings.py`` — the ``lex`` logger + console handler level.

    ``LEX_LOG_LEVEL`` must control the ``lex.*`` namespace ONLY, leaving the root
    logger (and therefore every third-party library) at its existing level.
    """

    def test_1_162_lex_logger_defaults_to_info(self):
        """1.162: with ``LEX_LOG_LEVEL`` unset, the ``lex`` logger is INFO.

        Given: a default local setup (no ``LEX_LOG_LEVEL``).
        When:  settings load.
        Then:  a dedicated ``lex`` logger exists, routed to the console, at INFO,
               with ``propagate=False`` so it owns its namespace and does not
               double-emit through root.
        """
        settings = self._reload(unset_env=("LEX_LOG_LEVEL",))

        lex_logger = settings.LOGGING["loggers"].get("lex")
        self.assertIsNotNone(
            lex_logger,
            "a dedicated 'lex' logger must exist so the framework namespace "
            "can be controlled in one place",
        )
        self.assertEqual(
            lex_logger["level"], "INFO",
            "lex namespace must default to INFO",
        )
        self.assertEqual(
            lex_logger["handlers"], ["console"],
            "lex logger must route to the console handler",
        )
        self.assertFalse(
            lex_logger["propagate"],
            "lex logger must not propagate — it owns its namespace, avoiding "
            "duplicate emission via root",
        )

    def test_1_163_lex_debug_raises_only_lex_namespace(self):
        """1.163: ``LEX_LOG_LEVEL=DEBUG`` raises lex only; root stays INFO.

        Given: an operator who wants framework debug logs but not third-party noise.
        When:  they set ``LEX_LOG_LEVEL=DEBUG`` (and leave ``LOG_LEVEL`` unset).
        Then:  the ``lex`` logger is at DEBUG while the ROOT logger stays at INFO —
               so ``lex.*`` debug records surface but third-party DEBUG records,
               which route to root, are dropped at their logger. This isolation is
               the entire feature.
        """
        settings = self._reload(
            set_env={"LEX_LOG_LEVEL": "DEBUG"},
            unset_env=("LOG_LEVEL",),
        )

        self.assertEqual(
            settings.LOGGING["loggers"]["lex"]["level"], "DEBUG",
            "LEX_LOG_LEVEL=DEBUG must raise the lex namespace to DEBUG",
        )
        self.assertEqual(
            settings.LOGGING["root"]["level"], "INFO",
            "root must stay at INFO so third-party DEBUG output is still gated "
            "out — this is what keeps the logs clean",
        )

    def test_1_164_console_handler_drops_to_debug_for_lex(self):
        """1.164: console handler becomes verbose enough to emit lex DEBUG.

        Given: ``LEX_LOG_LEVEL=DEBUG`` with ``LOG_LEVEL`` unset (INFO).
        When:  settings load.
        Then:  the console *handler* level is DEBUG (the more verbose of the two),
               otherwise it would discard lex DEBUG records before they print —
               the handler is the final gate after the logger lets a record
               through.
        """
        settings = self._reload(
            set_env={"LEX_LOG_LEVEL": "DEBUG"},
            unset_env=("LOG_LEVEL",),
        )

        self.assertEqual(
            settings.CONSOLE_HANDLER_LEVEL, logging.DEBUG,
            "console handler must drop to DEBUG so lex DEBUG records are emitted",
        )
        self.assertEqual(
            settings.LOGGING["handlers"]["console"]["level"], logging.DEBUG,
            "the LOGGING dict's console handler must use the derived level",
        )

    def test_1_165_console_handler_stays_info_by_default(self):
        """1.165: with both levels at their default, the handler stays INFO.

        Given: a default setup (neither ``LOG_LEVEL`` nor ``LEX_LOG_LEVEL`` set).
        When:  settings load.
        Then:  the console handler is at INFO — the min() derivation must not
               accidentally make the default console more verbose than before this
               feature existed.
        """
        settings = self._reload(unset_env=("LOG_LEVEL", "LEX_LOG_LEVEL"))

        self.assertEqual(
            settings.CONSOLE_HANDLER_LEVEL, logging.INFO,
            "default console handler level must remain INFO",
        )
        self.assertEqual(
            settings.LOGGING["handlers"]["console"]["level"], logging.INFO,
            "the LOGGING dict's console handler must stay INFO by default",
        )


# ---------------------------------------------------------------------
# 1.166–1.168 — blanket Python-warnings suppression gate
# ---------------------------------------------------------------------
class TestCluster01s_BlanketWarningSuppression(_SettingsReloadCase):
    """``lex/lex_app/settings.py`` — the ``LEX_SUPPRESS_WARNINGS`` gate.

    Beyond the targeted urllib3 warning, local startup also emits Python warnings
    (e.g. Django's "Accessing the database during app initialization" RuntimeWarning).
    The intent is a quiet startup by default, with a single opt-out for anyone who
    wants Python's normal warning behaviour back while debugging.
    """

    def test_1_166_warnings_filtered_by_default(self):
        """1.166: with the env var unset, all Python warnings are filtered.

        Given: ``LEX_SUPPRESS_WARNINGS`` is not set (the local default).
        When:  the settings module is imported/reloaded.
        Then:  ``warnings.filterwarnings`` is installed with the ``"ignore"`` action —
               the documented default that keeps customer startup logs clean.
        """
        os.environ.pop("LEX_SUPPRESS_WARNINGS", None)
        with mock.patch("sentry_sdk.init"), \
                mock.patch("warnings.filterwarnings") as mock_filter:
            importlib.reload(lex_settings)

        mock_filter.assert_called_once_with("ignore")

    def test_1_167_warnings_not_filtered_when_opted_out(self):
        """1.167: ``LEX_SUPPRESS_WARNINGS=False`` restores normal warnings.

        Given: a developer who wants Python's warnings back while debugging.
        When:  they set ``LEX_SUPPRESS_WARNINGS=False`` and start the app.
        Then:  the blanket ignore filter is NOT installed — ``filterwarnings`` is
               never called, so warnings surface as usual.
        """
        os.environ["LEX_SUPPRESS_WARNINGS"] = "False"
        with mock.patch("sentry_sdk.init"), \
                mock.patch("warnings.filterwarnings") as mock_filter:
            importlib.reload(lex_settings)

        mock_filter.assert_not_called()

    def test_1_168_opt_out_is_case_insensitive(self):
        """1.168: the opt-out value is parsed case-insensitively.

        Given: a developer who writes ``LEX_SUPPRESS_WARNINGS=FALSE``.
        When:  the app starts.
        Then:  the blanket filter is still skipped — the gate lower-cases the value,
               so ``FALSE``/``False``/``false`` are all honoured. A regression to a
               case-sensitive check would silently re-suppress.
        """
        os.environ["LEX_SUPPRESS_WARNINGS"] = "FALSE"
        with mock.patch("sentry_sdk.init"), \
                mock.patch("warnings.filterwarnings") as mock_filter:
            importlib.reload(lex_settings)

        mock_filter.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
