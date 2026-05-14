"""
Cluster 1j: ``lex init`` Keycloak client safety pre-flight (mocked).

Intent
------

Before ``lex init`` rewrites *any* authorization config, it must
verify the configured Keycloak client is:

1. **Confidential** — ``publicClient is False``. Matches the
   controller backend's ``is_confidential=true`` flow (the controller
   sets ``publicClient = not is_confidential`` on the create payload).
2. **DEVELOPMENT** when ``DEPLOYMENT_ENVIRONMENT`` is unset or empty —
   at least one ``redirectUris`` entry whose host
   is ``localhost``. The controller's ``Clients.py`` (e2e-testing
   branch) only emits ``http://localhost/*`` for
   ``client_type="DEVELOPMENT"`` clients; STANDARD / SHAREPOINT
   clients ship with the production redirect only. So the presence
   of a localhost redirect URI is the observable Keycloak-only
   signal that this is a dev client.

These checks are enforced by
``KeycloakSyncManager.assert_client_is_safe_for_init`` with a
``CommandError`` that names the client (``clientId`` + UUID) and tells
the operator what to fix.

Scenarios in this file are pure-unit (mocked ``kc_manager.admin``).
The real-Keycloak counterpart lives in
``test_1k_client_preflight_integration.py`` and is env-gated.

Scenario numbering extends ``docs/test-plan/test-clusters.md`` —
sub-cluster 1j picks up at **1.71**.
"""

from __future__ import annotations

import io
from unittest import TestCase, mock

from django.core.management.base import CommandError
from lex.lex_app.management.commands.init import (
    KEYCLOAK_DEV_REDIRECT_HOST,
    KeycloakSyncManager,
    _deployment_environment_is_set,
    _redirect_uris_indicate_development,
)

# ---------------------------------------------------------------------
# Fixtures (same shape as 1e / 1g): bypass __init__ + stub kc_manager.
# ---------------------------------------------------------------------
PROD_REDIRECT = "https://excellence-cloud.de/*"
DEV_REDIRECT = "http://localhost/*"


def _make_sync_manager(client_uuid: str = "test-client-uuid"):
    mgr = KeycloakSyncManager.__new__(KeycloakSyncManager)
    mgr.kc_manager = mock.MagicMock()
    mgr.kc_manager.client_uuid = client_uuid
    mgr.kc_manager.last_authz_import_error = None
    mgr.default_scopes = ["list", "read", "create", "edit", "delete", "export"]
    mgr.exported_configs = None
    return mgr


def _client_rep(
    *,
    client_id: str = "armira-cashflow",
    public_client: object = False,
    redirect_uris: object = None,
):
    """Build a minimally valid ``admin.get_client(...)`` payload.

    Default is the safe-for-init shape (confidential + DEVELOPMENT
    redirect URIs) so each test can override only the bit it cares
    about.
    """
    if redirect_uris is None:
        redirect_uris = [PROD_REDIRECT, DEV_REDIRECT]
    return {
        "clientId": client_id,
        "publicClient": public_client,
        "redirectUris": redirect_uris,
    }


# ---------------------------------------------------------------------
# 1.71 — Happy path
# ---------------------------------------------------------------------
class TestCluster01j_HappyPath(TestCase):
    """``assert_client_is_safe_for_init`` accepts confidential + DEV clients."""

    def test_1_71_confidential_with_localhost_redirect_passes(self):
        """1.71: ``publicClient=false`` + redirects include ``http://localhost/*`` → pass."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep()
        rep = mgr.assert_client_is_safe_for_init()
        self.assertEqual(rep["clientId"], "armira-cashflow")
        self.assertIs(rep["publicClient"], False)
        mgr.kc_manager.admin.get_client.assert_called_once_with(
            mgr.kc_manager.client_uuid,
        )

    def test_1_72_localhost_with_port_passes(self):
        """1.72: ``http://localhost:8000/*`` is also a localhost redirect."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            redirect_uris=[PROD_REDIRECT, "http://localhost:8000/*"],
        )
        mgr.assert_client_is_safe_for_init()

    def test_1_73_only_localhost_redirect_passes(self):
        """1.73: a client with **only** the localhost redirect (no
        prod URI at all) is still a valid dev client.
        """
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            redirect_uris=[DEV_REDIRECT],
        )
        mgr.assert_client_is_safe_for_init()


# ---------------------------------------------------------------------
# 1.74 — Confidential check fails
# ---------------------------------------------------------------------
class TestCluster01j_ConfidentialCheck(TestCase):
    """``publicClient`` must be exactly ``False``."""

    def test_1_74_public_client_true_raises(self):
        """1.74: ``publicClient=true`` → CommandError mentioning 'confidential'."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            public_client=True,
        )
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        msg = str(cm.exception)
        self.assertIn("confidential", msg.lower())
        self.assertIn("publicClient=true", msg)
        self.assertIn("armira-cashflow", msg)

    def test_1_75_public_client_missing_raises(self):
        """1.75: missing ``publicClient`` flag is treated as unsafe."""
        mgr = _make_sync_manager()
        rep = _client_rep()
        rep.pop("publicClient")
        mgr.kc_manager.admin.get_client.return_value = rep
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        self.assertIn("missing the `publicClient` flag", str(cm.exception))


# ---------------------------------------------------------------------
# 1.76 — Development check via redirectUris
# ---------------------------------------------------------------------
class TestCluster01j_DevelopmentCheck(TestCase):
    """At least one ``redirectUris`` entry must point at ``localhost``."""

    def test_1_76_only_production_redirect_raises(self):
        """1.76: a client with only the prod redirect (i.e. STANDARD
        on the controller side) is rejected as not-DEVELOPMENT."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            redirect_uris=[PROD_REDIRECT],
        )
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        msg = str(cm.exception)
        self.assertIn("DEVELOPMENT", msg)
        self.assertIn("localhost", msg)
        self.assertIn(PROD_REDIRECT, msg)
        self.assertIn("armira-cashflow", msg)

    def test_1_77_empty_redirect_uris_raises(self):
        """1.77: an empty redirect-URI list cannot indicate DEVELOPMENT."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            redirect_uris=[],
        )
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        msg = str(cm.exception)
        self.assertIn("DEVELOPMENT", msg)
        self.assertIn("<empty>", msg)

    def test_1_78_redirect_uris_missing_raises(self):
        """1.78: the ``redirectUris`` field absent (malformed rep) raises."""
        mgr = _make_sync_manager()
        rep = _client_rep()
        rep.pop("redirectUris")
        mgr.kc_manager.admin.get_client.return_value = rep
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        self.assertIn("malformed `redirectUris`", str(cm.exception))

    def test_1_79_redirect_uris_wrong_type_raises(self):
        """1.79: ``redirectUris`` set to a string (malformed) raises."""
        mgr = _make_sync_manager()
        rep = _client_rep()
        rep["redirectUris"] = "http://localhost/*"
        mgr.kc_manager.admin.get_client.return_value = rep
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        self.assertIn("malformed `redirectUris`", str(cm.exception))

    def test_1_80_localhost_subdomain_does_not_match(self):
        """1.80: ``http://localhost.example.com/*`` is NOT a localhost
        redirect — the gate uses parsed ``url.hostname``, not substring
        match, so the unrelated host is correctly rejected.
        """
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            redirect_uris=[PROD_REDIRECT, "http://localhost.example.com/*"],
        )
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        self.assertIn("DEVELOPMENT", str(cm.exception))

    @mock.patch.dict("os.environ", {"DEPLOYMENT_ENVIRONMENT": "PROD"}, clear=False)
    def test_deployment_environment_bypasses_development_client_requirement(self):
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            redirect_uris=[PROD_REDIRECT],
        )

        rep = mgr.assert_client_is_safe_for_init()

        self.assertEqual(rep["redirectUris"], [PROD_REDIRECT])

    @mock.patch.dict("os.environ", {"DEPLOYMENT_ENVIRONMENT": ""}, clear=False)
    def test_empty_deployment_environment_does_not_bypass_development_client_requirement(self):
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            redirect_uris=[PROD_REDIRECT],
        )

        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()

        self.assertIn("DEVELOPMENT", str(cm.exception))


# ---------------------------------------------------------------------
# 1.81 — Helper unit tests (parser correctness)
# ---------------------------------------------------------------------
class TestCluster01j_RedirectHostParser(TestCase):
    """Direct coverage of ``_redirect_uris_indicate_development``.

    The helper is the entire DEVELOPMENT signal — a regression here
    silently turns the gate into a no-op, so we pin the host-parser
    contract explicitly rather than relying on the integration paths
    above.
    """

    def test_1_81_parser_accepts_known_dev_shapes(self):
        """1.81: every shape the controller emits parses as 'dev'."""
        for uri in (
            "http://localhost/*",
            "http://localhost:3000/*",
            "https://localhost/cb",
            "http://LOCALHOST/*",  # case-insensitive host
        ):
            self.assertTrue(
                _redirect_uris_indicate_development([uri]),
                f"{uri!r} should be detected as a localhost redirect",
            )

    def test_1_82_parser_rejects_non_localhost(self):
        """1.82: production / 127.0.0.1 / look-alike hosts are NOT 'dev'.

        We deliberately do **not** treat ``127.0.0.1`` as dev — the
        controller only ever emits the literal ``localhost`` host,
        and accepting loopback IPs would let an operator silently
        bypass the gate by hand-editing the redirect list.
        """
        for uri in (
            "https://excellence-cloud.de/*",
            "http://127.0.0.1/*",
            "http://localhost.example.com/*",
            "not a url",
            "",
        ):
            self.assertFalse(
                _redirect_uris_indicate_development([uri]),
                f"{uri!r} must NOT be treated as a localhost redirect",
            )

    def test_1_83_parser_skips_non_string_entries(self):
        """1.83: non-string entries (None, dict) are ignored, not raised on."""
        self.assertFalse(
            _redirect_uris_indicate_development([None, 42, {"x": "y"}]),
        )
        # Mixed list: one valid localhost entry alongside garbage still passes.
        self.assertTrue(
            _redirect_uris_indicate_development(
                [None, "http://localhost/*", 42],
            ),
        )

    def test_1_84_parser_handles_none_or_empty(self):
        """1.84: ``None`` / empty list → False (not crash)."""
        self.assertFalse(_redirect_uris_indicate_development([]))
        self.assertFalse(_redirect_uris_indicate_development(None))  # type: ignore[arg-type]

    def test_1_85_dev_host_constant_is_documented(self):
        """1.85: pin ``KEYCLOAK_DEV_REDIRECT_HOST`` so any change is deliberate.

        If the controller ever switches the localhost convention, this
        test reminds the maintainer to update both halves of the contract.
        """
        self.assertEqual(KEYCLOAK_DEV_REDIRECT_HOST, "localhost")


class TestCluster01j_DeploymentEnvironmentGate(TestCase):
    def test_helper_is_false_for_unset_or_empty_values(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            self.assertFalse(_deployment_environment_is_set())
        with mock.patch.dict("os.environ", {"DEPLOYMENT_ENVIRONMENT": ""}, clear=False):
            self.assertFalse(_deployment_environment_is_set())
        with mock.patch.dict("os.environ", {"DEPLOYMENT_ENVIRONMENT": "   "}, clear=False):
            self.assertFalse(_deployment_environment_is_set())

    def test_helper_is_true_for_non_empty_value(self):
        with mock.patch.dict("os.environ", {"DEPLOYMENT_ENVIRONMENT": "PROD"}, clear=False):
            self.assertTrue(_deployment_environment_is_set())


# ---------------------------------------------------------------------
# 1.86 — Surrounding error handling
# ---------------------------------------------------------------------
class TestCluster01j_ErrorHandling(TestCase):
    """Failure modes around the admin call itself."""

    def test_1_86_no_client_uuid_raises(self):
        """1.86: missing ``client_uuid`` → CommandError before any HTTP call."""
        mgr = _make_sync_manager(client_uuid="")
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        self.assertIn("no client UUID resolved", str(cm.exception))
        mgr.kc_manager.admin.get_client.assert_not_called()

    def test_1_87_admin_get_client_raises_wrapped(self):
        """1.87: admin.get_client raising → CommandError wraps + chains."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.side_effect = RuntimeError("boom")
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        msg = str(cm.exception)
        self.assertIn("failed to fetch client representation", msg)
        self.assertIn("boom", msg)
        self.assertIsInstance(cm.exception.__cause__, RuntimeError)

    def test_1_88_unexpected_response_shape_raises(self):
        """1.88: admin.get_client returning a non-dict → CommandError."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = ["not", "a", "dict"]
        with self.assertRaises(CommandError) as cm:
            mgr.assert_client_is_safe_for_init()
        self.assertIn("unexpected", str(cm.exception).lower())


# ---------------------------------------------------------------------
# 1.89 — Wiring into the `init` command itself
# ---------------------------------------------------------------------
class TestCluster01j_CommandWiring(TestCase):
    """``Command.handle`` calls the preflight before any sync work,
    and ``--skip-client-preflight`` short-circuits it."""

    def _build_command(self):
        from lex.lex_app.management.commands.init import Command

        cmd = Command()
        cmd.stdout = io.StringIO()
        cmd.stderr = io.StringIO()
        return cmd

    def _run_handle(self, *, sync_manager, options=None):
        from lex.lex_app.management.commands import init as init_module

        cmd = self._build_command()
        opts = {
            "dry_run": False,
            "preserve_renamed_permissions": True,
            "check_missing": False,
            "bootstrap": False,
            "skip_migrations": True,
            "migration_verbosity": 0,
            "no_makemigrations": True,
            "ensure_default_authz": False,
            "sync_retries": 1,
            "skip_client_preflight": False,
            "makemigrations_args": "",
            "migrate_args": "",
        }
        if options:
            opts.update(options)

        with mock.patch.object(
            init_module, "KeycloakSyncManager", return_value=sync_manager,
        ), mock.patch.object(
            init_module, "MigrationAutodetector",
        ) as mock_autodetect, mock.patch.object(
            init_module, "MigrationLoader",
        ), mock.patch.object(
            init_module, "ProjectState",
        ):
            mock_autodetect.return_value.changes.return_value = {}
            try:
                cmd.handle(**opts)
            except CommandError as e:
                return cmd.stdout.getvalue(), cmd.stderr.getvalue(), e
            return cmd.stdout.getvalue(), cmd.stderr.getvalue(), None

    def test_1_89_preflight_failure_aborts_init(self):
        """1.89: a failing preflight stops `init` before sync runs."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            redirect_uris=[PROD_REDIRECT],  # not DEVELOPMENT
        )
        mgr.process_model_changes = mock.MagicMock()
        mgr.export_configs = mock.MagicMock(return_value={})

        _stdout, _stderr, err = self._run_handle(sync_manager=mgr)

        self.assertIsNotNone(err, "init should have raised CommandError")
        self.assertIn("DEVELOPMENT", str(err))
        self.assertIn("armira-cashflow", str(err))
        mgr.process_model_changes.assert_not_called()

    def test_1_90_skip_flag_bypasses_preflight(self):
        """1.90: ``--skip-client-preflight`` is the documented escape hatch."""
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.side_effect = AssertionError(
            "preflight must not run when --skip-client-preflight is set",
        )
        mgr.process_model_changes = mock.MagicMock()

        stdout, _stderr, err = self._run_handle(
            sync_manager=mgr,
            options={"skip_client_preflight": True},
        )

        self.assertIsNone(err, f"init must not fail when preflight skipped: {err}")
        self.assertIn("--skip-client-preflight is set", stdout)
        mgr.process_model_changes.assert_called_once()
        mgr.kc_manager.admin.get_client.assert_not_called()

    @mock.patch.dict("os.environ", {"DEPLOYMENT_ENVIRONMENT": "PROD"}, clear=False)
    def test_deployed_environment_allows_non_development_client_in_handle(self):
        mgr = _make_sync_manager()
        mgr.kc_manager.admin.get_client.return_value = _client_rep(
            redirect_uris=[PROD_REDIRECT],
        )
        mgr.process_model_changes = mock.MagicMock()

        _stdout, _stderr, err = self._run_handle(sync_manager=mgr)

        self.assertIsNone(err, f"init should succeed for deployed envs: {err}")
        mgr.process_model_changes.assert_called_once()
