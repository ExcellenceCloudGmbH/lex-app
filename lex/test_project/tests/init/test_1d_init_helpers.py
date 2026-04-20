"""
Cluster 1d: ``lex Init`` — module-level helpers.

Intent (from docs/features/ai-onboarding/ and
``lex/lex_app/management/commands/init.py``):

    The ``init`` command ships a set of small pure helpers that every
    customer's setup flow depends on: detecting which Keycloak env
    vars are missing, (de)serializing the bootstrap state file,
    merging new keys into ``.env`` without losing comments,
    formatting Keycloak-side import errors for the console, and
    parsing the free-form ``--makemigrations-args`` / ``--migrate-args``
    strings into positional + keyword arguments.

    These are the kind of helpers where a silent regression means a
    customer's bootstrap fails for an unclear reason — exactly the
    surface the test suite must guard.

Scenario numbering extends
docs/test-plan/test-clusters.md#1-init--project-bootstrap
(new sub-cluster 1d).
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from lex.lex_app.management.commands import init as init_module
from lex.lex_app.management.commands.init import (
    Command,
    DEFAULT_CLIENT_ROLES,
    DEFAULT_SCOPE_POLICY_MAPPING,
    KEYCLOAK_ENV_VARS,
    NON_FATAL_KEYCLOAK_IMPORT_ERROR_KINDS,
    _format_keycloak_import_error_details,
    _get_keycloak_import_error_details,
    _is_non_fatal_keycloak_import_timeout,
    _load_state_map,
    _save_state_map,
    get_missing_keycloak_env,
    get_state,
    set_state,
    update_env_file,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
class _FakeSyncManager:
    """Minimal stand-in for KeycloakSyncManager used by error-detail helpers."""

    def __init__(self, error=None):
        self.kc_manager = mock.Mock()
        self.kc_manager.last_authz_import_error = error


# ---------------------------------------------------------------------
# 1.23 — import-error formatting
# ---------------------------------------------------------------------
class TestCluster01d_ImportErrorFormatting(TestCase):
    """``_format_keycloak_import_error_details`` — one human-readable line per error kind."""

    def test_1_23_timeout_formatting_includes_timeout_value(self):
        """Scenario 1.23: timeout error → message names the timeout."""
        msg = _format_keycloak_import_error_details({"kind": "timeout", "timeout": 30})
        self.assertEqual(msg, "request timed out after 30")

    def test_1_23b_timeout_without_value_has_generic_message(self):
        msg = _format_keycloak_import_error_details({"kind": "timeout"})
        self.assertEqual(msg, "request timed out")

    def test_1_23c_gateway_timeout_mentions_status_and_body(self):
        msg = _format_keycloak_import_error_details(
            {"kind": "gateway_timeout", "status_code": 504, "response_text": "upstream gone"},
        )
        self.assertIn("504", msg)
        self.assertIn("upstream gone", msg)

    def test_1_23d_http_error_mentions_status(self):
        msg = _format_keycloak_import_error_details(
            {"kind": "http_error", "status_code": 401, "response_text": "bad token"},
        )
        self.assertIn("401", msg)
        self.assertIn("bad token", msg)

    def test_1_23e_unknown_kind_returns_none(self):
        self.assertIsNone(
            _format_keycloak_import_error_details({"kind": "totally_unknown"}),
            "Unknown error kinds must return None so the caller falls back "
            "to a generic message rather than showing a partial dump",
        )

    def test_1_23f_empty_or_none_returns_none(self):
        self.assertIsNone(_format_keycloak_import_error_details(None))
        self.assertIsNone(_format_keycloak_import_error_details({}))


# ---------------------------------------------------------------------
# 1.24 — non-fatal import timeout detection
# ---------------------------------------------------------------------
class TestCluster01d_NonFatalImportTimeout(TestCase):
    """``_is_non_fatal_keycloak_import_timeout`` determines whether init continues."""

    def test_1_24_timeout_is_non_fatal(self):
        """Scenario 1.24: a ``timeout`` error is considered non-fatal."""
        sync_mgr = _FakeSyncManager(error={"kind": "timeout", "timeout": 10})
        self.assertTrue(
            _is_non_fatal_keycloak_import_timeout(sync_mgr),
            "A ``timeout`` import-error must let init continue — customer "
            "contract is 'don't brick a long-running init on a transient "
            "Keycloak slowdown'",
        )

    def test_1_24b_gateway_timeout_is_non_fatal(self):
        sync_mgr = _FakeSyncManager(error={"kind": "gateway_timeout", "status_code": 504})
        self.assertTrue(_is_non_fatal_keycloak_import_timeout(sync_mgr))

    def test_1_24c_http_error_is_fatal(self):
        """An ordinary HTTP error must abort init — misconfig is not worth hiding."""
        sync_mgr = _FakeSyncManager(error={"kind": "http_error", "status_code": 401})
        self.assertFalse(_is_non_fatal_keycloak_import_timeout(sync_mgr))

    def test_1_24d_no_error_is_fatal_false(self):
        self.assertFalse(_is_non_fatal_keycloak_import_timeout(_FakeSyncManager()))

    def test_1_24e_non_fatal_kinds_are_the_published_set(self):
        """The published set is the contract — any change is customer-visible."""
        self.assertEqual(
            NON_FATAL_KEYCLOAK_IMPORT_ERROR_KINDS,
            frozenset({"timeout", "gateway_timeout"}),
            "Published non-fatal error kinds must remain stable",
        )


# ---------------------------------------------------------------------
# 1.25 — missing env var detection
# ---------------------------------------------------------------------
class TestCluster01d_MissingKeycloakEnv(TestCase):
    """``get_missing_keycloak_env`` identifies env vars the bootstrap must fill."""

    def test_1_25_everything_missing_reports_all_vars(self):
        """Scenario 1.25: with nothing configured, every published var is listed."""
        class _EmptySettings:  # noqa: D401 - simple namespace
            pass
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(init_module, "settings", _EmptySettings()):
                missing = get_missing_keycloak_env()
        for key in ("KEYCLOAK_URL", "OIDC_RP_CLIENT_ID",
                    "OIDC_RP_CLIENT_SECRET", "OIDC_RP_CLIENT_UUID"):
            self.assertIn(
                key, missing,
                f"{key!r} must be flagged as missing when unset",
            )

    def test_1_25b_configured_var_drops_from_missing(self):
        """Scenario 1.25b: setting a var removes it from the missing list."""
        env = {"KEYCLOAK_URL": "https://kc.example",
               "OIDC_RP_CLIENT_ID": "x",
               "OIDC_RP_CLIENT_SECRET": "y",
               "OIDC_RP_CLIENT_UUID": "u",
               "KEYCLOAK_REALM": "r"}

        class _EmptySettings:
            pass
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(init_module, "settings", _EmptySettings()):
                missing = get_missing_keycloak_env()
        self.assertEqual(
            missing, [],
            f"With every var set, no vars must be reported missing; got {missing!r}",
        )

    def test_1_25c_published_env_var_list_is_stable(self):
        """
        The ``KEYCLOAK_ENV_VARS`` tuple is a public contract with the
        bootstrap UI. Order and membership must not drift silently.
        """
        self.assertEqual(
            tuple(KEYCLOAK_ENV_VARS),
            (
                "KEYCLOAK_URL",
                "KEYCLOAK_REALM",
                "KEYCLOAK_REALM_NAME",
                "OIDC_RP_CLIENT_ID",
                "OIDC_RP_CLIENT_SECRET",
                "OIDC_RP_CLIENT_UUID",
            ),
        )


# ---------------------------------------------------------------------
# 1.26 — bootstrap state file (JSON on disk)
# ---------------------------------------------------------------------
class TestCluster01d_StateFile(TestCase):
    """``get_state`` / ``set_state`` round-trip through ``.keycloak_state.json``."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.state_file = Path(self._tmp.name) / "state.json"
        self._patcher = mock.patch.object(init_module, "STATE_FILE", self.state_file)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def test_1_26_roundtrip_single_key(self):
        """Scenario 1.26: ``set_state`` then ``get_state`` returns the same value."""
        set_state("abc", "done")
        self.assertEqual(get_state("abc"), "done")

    def test_1_26b_missing_file_returns_empty_map(self):
        """No state file on disk → an empty map, not an exception."""
        self.assertEqual(_load_state_map(), {})
        self.assertIsNone(get_state("missing"))

    def test_1_26c_corrupt_json_is_fatal(self):
        """Scenario 1.26c: corrupt state file raises — customer must be told, not silently wiped."""
        self.state_file.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(Exception) as ctx:
            _load_state_map()
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_1_26d_multiple_keys_preserved_across_calls(self):
        """Adding a new key must not clobber earlier keys."""
        set_state("s1", "done")
        set_state("s2", "cancelled")
        self.assertEqual(get_state("s1"), "done")
        self.assertEqual(get_state("s2"), "cancelled")


# ---------------------------------------------------------------------
# 1.27 — .env file merge
# ---------------------------------------------------------------------
class TestCluster01d_UpdateEnvFile(TestCase):
    """``update_env_file`` must merge values without losing comments or blank lines."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._base_dir = Path(self._tmp.name)
        self._settings_patcher = mock.patch.object(
            init_module, "settings",
            mock.Mock(BASE_DIR=str(self._base_dir)),
        )
        self._settings_patcher.start()
        self.env_file = self._base_dir / ".env"

    def tearDown(self):
        self._settings_patcher.stop()
        self._tmp.cleanup()

    def test_1_27_creates_env_file_when_missing(self):
        """Scenario 1.27: customer with no ``.env`` yet — file is created."""
        update_env_file({"KEYCLOAK_URL": "https://kc.example"})
        self.assertTrue(self.env_file.exists())
        self.assertIn("KEYCLOAK_URL=https://kc.example", self.env_file.read_text())

    def test_1_27b_preserves_unrelated_keys(self):
        """Unrelated keys must not be touched by a partial update."""
        self.env_file.write_text(
            "# comment\nDB_HOST=localhost\nDB_PORT=5432\n", encoding="utf-8",
        )
        update_env_file({"KEYCLOAK_URL": "https://kc.example"})
        text = self.env_file.read_text()
        self.assertIn("DB_HOST=localhost", text)
        self.assertIn("DB_PORT=5432", text)
        self.assertIn("KEYCLOAK_URL=https://kc.example", text)

    def test_1_27c_overwrites_existing_key(self):
        """An existing key must be updated, not duplicated."""
        self.env_file.write_text("KEYCLOAK_URL=old\n", encoding="utf-8")
        update_env_file({"KEYCLOAK_URL": "new"})
        text = self.env_file.read_text()
        self.assertIn("KEYCLOAK_URL=new", text)
        self.assertNotIn("KEYCLOAK_URL=old", text)
        # Only one line for KEYCLOAK_URL
        self.assertEqual(
            sum(1 for ln in text.splitlines() if ln.startswith("KEYCLOAK_URL=")),
            1,
            "update_env_file must not duplicate an existing key",
        )

    def test_1_27d_preserves_comments_and_blank_lines(self):
        """
        Comments and blank lines must survive a merge — customers use
        them to document their own ``.env``.
        """
        self.env_file.write_text(
            "# Database\nDB_HOST=localhost\n\n# Keycloak\n",
            encoding="utf-8",
        )
        update_env_file({"OIDC_RP_CLIENT_ID": "abc"})
        text = self.env_file.read_text()
        self.assertIn("# Database", text)
        self.assertIn("# Keycloak", text)


# ---------------------------------------------------------------------
# 1.28 — extra-args CLI parsing
# ---------------------------------------------------------------------
class TestCluster01d_ParseExtraArgs(TestCase):
    """``Command._parse_extra_args`` forwards flags to Django's migrate/makemigrations."""

    def test_1_28_empty_string_yields_empty(self):
        """Scenario 1.28: empty input → empty positional + options."""
        pos, opts = Command._parse_extra_args("")
        self.assertEqual(pos, [])
        self.assertEqual(opts, {})

    def test_1_28b_flag_with_equals(self):
        """``--database=other`` parses as ``{"database": "other"}``."""
        pos, opts = Command._parse_extra_args("--database=other")
        self.assertEqual(pos, [])
        self.assertEqual(opts, {"database": "other"})

    def test_1_28c_flag_with_value(self):
        """``--database other`` parses as ``{"database": "other"}``."""
        pos, opts = Command._parse_extra_args("--database other")
        self.assertEqual(opts, {"database": "other"})

    def test_1_28d_boolean_flag(self):
        """Scenario 1.28d: ``--run-syncdb`` alone → ``{"run_syncdb": True}``."""
        pos, opts = Command._parse_extra_args("--run-syncdb")
        self.assertEqual(
            opts, {"run_syncdb": True},
            "Bare ``--foo`` must parse to ``{foo: True}`` and dashes must "
            "become underscores for Django's call_command kwargs",
        )

    def test_1_28e_positional_args_preserved(self):
        """Scenario 1.28e: positional tokens land in ``positional``."""
        pos, opts = Command._parse_extra_args("myapp 0001")
        self.assertEqual(pos, ["myapp", "0001"])
        self.assertEqual(opts, {})

    def test_1_28f_mixed(self):
        pos, opts = Command._parse_extra_args("--fake myapp 0001")
        self.assertEqual(opts, {"fake": "myapp"})
        self.assertEqual(pos, ["0001"])


# ---------------------------------------------------------------------
# 1.29 — database alias resolution
# ---------------------------------------------------------------------
class TestCluster01d_DatabaseAliasFromArgs(TestCase):
    """``Command._database_alias_from_migrate_args`` picks up ``--database=…``."""

    def test_1_29_default_when_no_args(self):
        """Scenario 1.29: absent / empty → ``default``."""
        self.assertEqual(Command._database_alias_from_migrate_args(""), "default")
        self.assertEqual(Command._database_alias_from_migrate_args(None), "default")

    def test_1_29b_custom_database_is_picked_up(self):
        self.assertEqual(
            Command._database_alias_from_migrate_args("--database=analytics"),
            "analytics",
            "A ``--database=`` override must flow through to the migrate step",
        )


# ---------------------------------------------------------------------
# 1.30 — default-authz publication contract
# ---------------------------------------------------------------------
class TestCluster01d_DefaultScopePolicyMapping(TestCase):
    """Published scope/policy contract must match the documented defaults."""

    def test_1_30_scope_policy_mapping_is_stable(self):
        """
        Scenario 1.30: ``DEFAULT_SCOPE_POLICY_MAPPING`` is the published
        contract with every customer project. The six documented scopes
        and their policy assignments must remain stable.
        """
        self.assertEqual(set(DEFAULT_SCOPE_POLICY_MAPPING.keys()), {
            "list", "read", "create", "edit", "delete", "export",
        })
        # Admin is on every scope
        for scope, policies in DEFAULT_SCOPE_POLICY_MAPPING.items():
            self.assertIn(
                "Policy - admin", policies,
                f"Admin must have access to the {scope!r} scope by default",
            )

    def test_1_30b_default_client_roles_tuple_is_published(self):
        """The three default client-role names are a published contract."""
        self.assertEqual(
            tuple(DEFAULT_CLIENT_ROLES),
            ("admin", "standard", "view-only"),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


