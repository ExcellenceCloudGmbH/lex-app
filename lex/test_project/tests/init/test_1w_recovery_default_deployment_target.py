"""``LEX_TASK_RECOVERY_ENABLED`` defaults OFF so a stuck calc resets on restart.

Intent: the Celery worker-recovery system deliberately leaves a stuck
``IN_PROGRESS`` calculation row untouched at startup — the liveness-aware sweep
(``process_admin.utils.model_registration._handle_calculation_model_reset``)
skips any row a *tracked* recovery task owns, trusting a recovery-supervisor pod
to requeue/resume it. When that pod is not running — local dev, CI, and any
deployment that never provisioned it — the row is orphaned ``IN_PROGRESS``
forever, and a server restart never clears it. Defaulting the master switch OFF
keeps the startup sweep in its original blind-abort mode, so a stuck row is reset
to ``ABORTED`` on the next boot (the behaviour operators actually expect from a
restart). Deployments that DO run the supervisor opt back in with an explicit
``LEX_TASK_RECOVERY_ENABLED=true`` (the recovery-supervisor manifest already sets
it). A regression that flips the default back to ``true`` silently re-orphans
every stuck row in every environment that lacks the supervisor.

This flag gates ONLY the recovery registry/heartbeat/supervisor and the sweep's
skip-set; it does not gate calculation dispatch, so a calculation that dispatches
sub-calculations is unaffected either way (covered by clusters 7j/7q/7r/8ab).

Cluster 1w — scenarios 1.184–1.186. Type: U.
Covers: lex/lex_app/settings.py (LEX_TASK_RECOVERY_ENABLED default).
Run: python -m lex pytest lex/test_project/tests/init/test_1w_recovery_default_deployment_target.py -v
"""

from __future__ import annotations

import importlib
import os
import unittest
from unittest import mock

import pytest
from django.test import SimpleTestCase

from lex.lex_app import settings as lex_settings

pytestmark = pytest.mark.init


class _SettingsReloadCase(SimpleTestCase):
    """Base: save/restore the recovery env key + reload ``lex.lex_app.settings``.

    Reloading re-executes the settings module top-to-bottom, which is how an
    import-time, env-driven default is exercised in both directions.
    ``sentry_sdk.init`` is patched out during every reload so the re-execution is
    side-effect free.
    """

    def setUp(self) -> None:
        self._saved = os.environ.get("LEX_TASK_RECOVERY_ENABLED")
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._saved is None:
            os.environ.pop("LEX_TASK_RECOVERY_ENABLED", None)
        else:
            os.environ["LEX_TASK_RECOVERY_ENABLED"] = self._saved
        with mock.patch("sentry_sdk.init"):
            importlib.reload(lex_settings)

    def _reload(self, value=None):
        if value is None:
            os.environ.pop("LEX_TASK_RECOVERY_ENABLED", None)
        else:
            os.environ["LEX_TASK_RECOVERY_ENABLED"] = value
        with mock.patch("sentry_sdk.init"):
            importlib.reload(lex_settings)
        return lex_settings


class TestCluster01w_RecoveryDefaultOff(_SettingsReloadCase):
    """Cluster 1w: LEX_TASK_RECOVERY_ENABLED defaults OFF, explicit env overrides."""

    # -- 1.184 ---------------------------------------------------------
    def test_1_184_defaults_off_when_env_unset(self) -> None:
        """
        Scenario 1.184: no ``LEX_TASK_RECOVERY_ENABLED`` in the environment.
        Given: the variable is absent (local dev / CI / an un-provisioned deploy)
        When:  ``lex.lex_app.settings`` resolves the flag
        Then:  it is ``False`` — recovery is OFF by default, so the startup sweep
               blind-aborts stuck IN_PROGRESS rows instead of leaving them for a
               supervisor that is not running.
        """
        settings = self._reload(value=None)
        self.assertIs(
            settings.LEX_TASK_RECOVERY_ENABLED,
            False,
            "With LEX_TASK_RECOVERY_ENABLED unset the flag must default to False; "
            "a True default re-orphans stuck IN_PROGRESS rows wherever no recovery "
            "supervisor pod is running.",
        )

    # -- 1.185 ---------------------------------------------------------
    def test_1_185_explicit_true_opts_in(self) -> None:
        """
        Scenario 1.185: a deployment that runs the supervisor opts back in.
        Given: ``LEX_TASK_RECOVERY_ENABLED=true`` (the recovery-supervisor manifest)
        When:  the settings module resolves the flag
        Then:  it is ``True`` — the explicit opt-in must still turn recovery on so
               production keeps its dead-worker requeue behaviour.
        """
        settings = self._reload(value="true")
        self.assertIs(
            settings.LEX_TASK_RECOVERY_ENABLED,
            True,
            "An explicit LEX_TASK_RECOVERY_ENABLED=true must enable recovery so a "
            "deployment running the supervisor pod keeps requeueing dead workers.",
        )

    # -- 1.186 ---------------------------------------------------------
    def test_1_186_explicit_false_and_case_insensitive(self) -> None:
        """
        Scenario 1.186: explicit off and case-insensitive truthiness.
        Given: ``LEX_TASK_RECOVERY_ENABLED`` set to ``"false"``, then ``"TRUE"``
        When:  the settings module resolves each value
        Then:  ``"false"`` → ``False`` (explicit override in the OFF direction) and
               ``"TRUE"`` → ``True`` (the ``.lower() == "true"`` parse is
               case-insensitive, so operators aren't tripped by casing).
        """
        self.assertIs(
            self._reload(value="false").LEX_TASK_RECOVERY_ENABLED,
            False,
            "LEX_TASK_RECOVERY_ENABLED=false must resolve to False.",
        )
        self.assertIs(
            self._reload(value="TRUE").LEX_TASK_RECOVERY_ENABLED,
            True,
            "LEX_TASK_RECOVERY_ENABLED=TRUE must resolve to True (parse is "
            "case-insensitive via .lower()).",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
