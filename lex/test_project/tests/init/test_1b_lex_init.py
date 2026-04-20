"""
Cluster 1b: ``lex Init`` — first-run initialization command.

Tests the Django management command that applies migrations, syncs
Django models to Keycloak, and loads seed data. External boundaries
(Keycloak HTTP, the DB migrations subsystem) are mocked at their
interfaces — never inside the command itself.

Intent (from docs/installation.md):

    ``lex Init`` does three things:
      1. Applies migrations — creates/updates database tables from your models
      2. Syncs to Keycloak — registers your models as "Resources" and
         permissions as "Scopes"
      3. Enables access management — you can manage permissions on
         Excellence Cloud

Scenario numbering matches
docs/test-plan/test-clusters.md#1-init--project-bootstrap.
"""

from __future__ import annotations

import unittest
from io import StringIO
from unittest import TestCase
from unittest.mock import MagicMock, patch


class TestCluster01b_LexInit(TestCase):
    """Handler-level tests for ``lex Init``."""

    # Default options required by Command.handle(). Kept here so tests
    # express only the scenario-specific overrides.
    _DEFAULT_OPTS = {
        "dry_run": True,
        "preserve_renamed_permissions": True,
        "check_missing": False,
        "bootstrap": False,
        "skip_migrations": False,
        "migration_verbosity": 1,
        "no_makemigrations": True,
        "ensure_default_authz": False,
        "sync_retries": 1,
        "makemigrations_args": "",
        "migrate_args": "",
    }

    def _make_command(self):
        from lex.lex_app.management.commands.init import Command
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()
        return cmd

    def _patches(self):
        """Standard external-boundary patches for Init tests."""
        return [
            patch("lex.lex_app.management.commands.init.KeycloakSyncManager"),
            patch("lex.lex_app.management.commands.init.MigrationAutodetector"),
            patch("lex.lex_app.management.commands.init.MigrationLoader"),
            patch("lex.lex_app.management.commands.init.ProjectState.from_apps"),
            patch("lex.lex_app.management.commands.init.call_command"),
        ]

    def setUp(self) -> None:
        self._started = [p.start() for p in self._patches()]
        (self.mock_manager_cls, self.mock_autodetector_cls,
         self.mock_loader_cls, self.mock_from_apps,
         self.mock_call_command) = self._started

        # Sensible defaults so handle() can run through.
        loader = MagicMock()
        loader.graph = MagicMock()
        loader.project_state.return_value = MagicMock()
        self.mock_loader_cls.return_value = loader
        self.mock_from_apps.return_value = MagicMock()

        autodetector = MagicMock()
        autodetector.changes.return_value = {}
        self.mock_autodetector_cls.return_value = autodetector

        self.mock_manager = MagicMock()
        self.mock_manager_cls.return_value = self.mock_manager

    def tearDown(self) -> None:
        for p in self._patches():
            try:
                p.stop()
            except RuntimeError:
                pass

    # -- 1.6 -----------------------------------------------------------
    def test_1_6_first_run_applies_migrations(self) -> None:
        """
        Scenario 1.6: First run on empty DB.

        When a customer runs ``lex Init`` on a fresh project, the command
        must **engage Django's migration subsystem** so tables for their
        models end up in the database. The exact sequence of underlying
        calls is an implementation detail; what matters to the customer
        is that *some* migration work was done.
        """
        cmd = self._make_command()
        opts = {**self._DEFAULT_OPTS, "dry_run": False, "no_makemigrations": False}
        cmd.handle(**opts)

        invoked = [c.args[0] for c in self.mock_call_command.call_args_list]
        self.assertTrue(
            any(name in invoked for name in ("migrate", "makemigrations")),
            "lex Init must engage Django's migration subsystem on first "
            "run so the customer's tables exist. "
            "Invoked commands: %s" % invoked,
        )

    # -- 1.7 -----------------------------------------------------------
    def test_1_7_second_run_is_idempotent(self) -> None:
        """
        Scenario 1.7: Second run is idempotent.

        Running ``lex Init`` twice in a row on an already-initialised
        project must not raise. No drift, no errors.
        """
        cmd = self._make_command()
        cmd.handle(**self._DEFAULT_OPTS)
        # Second invocation on the same (mocked) stable state.
        cmd.handle(**self._DEFAULT_OPTS)
        # If no exception was raised the scenario passes.

    # -- 1.13 ----------------------------------------------------------
    def test_1_13_gateway_timeout_is_non_fatal(self) -> None:
        """
        Scenario 1.13: Keycloak unavailable (timeout).

        If Keycloak is briefly unreachable during sync, ``lex Init``
        should NOT explode. It should log a warning and exit cleanly so
        the customer can retry.
        """
        self.mock_manager.kc_manager.last_authz_import_error = {
            "kind": "gateway_timeout",
            "status_code": 504,
            "response_text": "gateway timeout",
        }
        self.mock_manager.sync_models.side_effect = Exception("keycloak down")

        cmd = self._make_command()
        try:
            cmd.handle(**{**self._DEFAULT_OPTS, "sync_retries": 2})
        except Exception as exc:  # pragma: no cover — behavioural assertion
            self.fail(
                "lex Init must treat Keycloak gateway_timeout as non-fatal "
                "so the customer can retry without losing local state. "
                f"Got: {exc!r}"
            )

    # -- 1.14 ----------------------------------------------------------
    def test_1_14_missing_keycloak_env_vars_fails_fast(self) -> None:
        """
        Scenario 1.14: Missing Keycloak env vars.

        If the user has not configured any Keycloak credentials AND has
        not passed ``--bootstrap``, Init must fail fast with an
        actionable message — not silently half-succeed.
        """
        self.mock_manager_cls.side_effect = RuntimeError(
            "KEYCLOAK_URL is not set"
        )
        cmd = self._make_command()

        with self.assertRaises(
            Exception,
            msg="Missing Keycloak env vars must raise — not swallow the error",
        ):
            cmd.handle(**self._DEFAULT_OPTS)

    # -- 1.16 ----------------------------------------------------------
    def test_1_16_excluded_apps_are_not_synced(self) -> None:
        """
        Scenario 1.16: Excluded apps skipped.

        Framework-internal apps (``legacy_data``, ``AuditLog``, historical
        models) must not be registered as customer-facing Keycloak
        resources — customers should not see framework plumbing in their
        access-management UI.
        """
        from lex.lex_app.management.commands.init import (
            KEYCLOAK_SYNC_EXCLUDED_APPS,
            KEYCLOAK_SYNC_EXCLUDED_MODEL_PREFIXES,
            KEYCLOAK_SYNC_EXCLUDED_RESOURCE_NAMES,
        )
        self.assertIn("legacy_data", KEYCLOAK_SYNC_EXCLUDED_APPS)
        self.assertIn(
            "audit_logging.AuditLog", KEYCLOAK_SYNC_EXCLUDED_RESOURCE_NAMES,
        )
        self.assertTrue(
            any(p.startswith("histor") for p in KEYCLOAK_SYNC_EXCLUDED_MODEL_PREFIXES),
            "Historical models must be excluded from the Keycloak sync — "
            "they are framework internals, not customer resources.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
