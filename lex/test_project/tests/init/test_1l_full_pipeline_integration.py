"""
Cluster 1l: ``lex init`` full pipeline — REAL Keycloak integration.

Companion to the mocked end-to-end coverage in
``test_1b_lex_init.py`` and the drift coverage in
``test_1f_keycloak_drift.py``. Where those run with a stubbed
``KeycloakSyncManager``, this file drives the **same code path the
real ``lex init`` command runs** against a live Keycloak server.

Why a separate file?
--------------------

The mocked tests cover *contract*: given a stubbed export the manager
produces the right resources / policies / permissions. The live
tests prove three additional things only a real server can:

1. The Keycloak admin REST API actually accepts the payloads
   ``KeycloakSyncManager`` builds — schema drift on Keycloak's side
   would fail here before it failed in production.
2. The end-to-end timing works (token refresh, multi-call sequences,
   no race against the authz-import endpoint).
3. ``last_authz_import_error`` round-trips to ``None`` on success —
   which is what ``Command.handle`` actually checks.

Gating — TWO levels
-------------------

* **Read-only tests** require ``LEX_RUN_KEYCLOAK_INTEGRATION=1`` and
  a populated ``.env`` (same gate as ``test_1k_*``).
* **Destructive tests** (the ones that actually call
  ``import_authorization_settings`` to rewrite the client's authz
  config) ALSO require ``LEX_RUN_KEYCLOAK_DESTRUCTIVE=1``. Default
  off so even an integration-on CI run never accidentally rewrites
  the live client. The destructive set is what proves the actual
  ``lex init`` command works end-to-end.

The destructive tests target ``LEX-Test-1771257005`` (the dedicated
test client). They are designed to be **idempotent** — running them
twice in a row is the test, and a second run that produces drift is
a regression.

Read-only methods exercised
---------------------------

* ``KeycloakSyncManager.verify_client_is_safe_for_init`` (delegated
  to 1k for primary coverage; re-asserted here as the gate before
  the destructive scenarios).
* ``get_all_django_models`` — walks the live ``apps`` registry.
* ``export_configs`` → ``kc_manager.export_authorization_settings``.
* ``get_existing_keycloak_resources`` against the live export.
* ``find_missing_models`` against the live export.
* ``get_client_roles`` — reads ``admin`` / ``standard`` / ``view-only``
  off the live client. (Note: this method lazily *creates* the three
  defaults if they are missing — for a properly-initialised client
  it's a pure read; for a brand-new client it does a one-time write.
  Documented in the test docstring.)

Destructive methods exercised
-----------------------------

* ``process_model_changes`` with empty `adds` / `deletes` / `renames`
  — exercises the full export → snapshot → import → restore loop
  without proposing any model changes (so the only mutation is the
  authz-config round-trip itself, which should land identical bytes
  on Keycloak's side after import).
* ``Command.handle`` end-to-end via ``call_command("init", …)`` with
  ``--skip-migrations`` + ``--no-makemigrations`` so the only side
  effect is the Keycloak round-trip — no DB schema changes.

Scenario numbering extends ``docs/test-plan/test-clusters.md`` —
sub-cluster 1l picks up at **1.95**.
"""

from __future__ import annotations

import io
import os
import unittest
from pathlib import Path
from unittest import TestCase

from django.core.management import call_command
from django.core.management.base import CommandError

from lex.lex_app.management.commands import init as init_module
from lex.lex_app.management.commands.init import (
    KEYCLOAK_ENV_VARS,
    KeycloakSyncManager,
)


def _resolve_integration_env_file() -> Path | None:
    """Resolve the ``.env`` to source for Keycloak integration tests.

    Resolution order:

    1. ``LEX_KEYCLOAK_INTEGRATION_ENV`` env var — explicit override.
    2. ``lex/test_project/tests/init/.env`` — co-located with the
       init test files. Gitignored, so credentials never get
       committed; the operator drops a real ``.env`` next to the
       tests and the suite picks it up regardless of which directory
       the runner was launched from.
    3. ``init.ENV_FILE`` — the same file ``lex init`` itself reads
       (``Path.cwd() / ".env"``); last-resort fallback.
    """
    override = os.getenv("LEX_KEYCLOAK_INTEGRATION_ENV")
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.exists() else None
    bundled = Path(__file__).parent / ".env"
    if bundled.exists():
        return bundled
    canonical = Path(init_module.ENV_FILE)
    return canonical if canonical.exists() else None


def _load_integration_env_into_os(env_file: Path) -> dict[str, str]:
    from dotenv import dotenv_values

    values = dotenv_values(str(env_file))
    loaded: dict[str, str] = {}
    for key in KEYCLOAK_ENV_VARS:
        if key in values and values[key]:
            os.environ[key] = str(values[key])
            loaded[key] = str(values[key])
    return loaded


_REQUIRED_KEYCLOAK_VARS = (
    "KEYCLOAK_URL",
    "OIDC_RP_CLIENT_ID",
    "OIDC_RP_CLIENT_SECRET",
)


def _required_env_already_set() -> bool:
    """True iff every required Keycloak var is already in ``os.environ``.

    CI / GitHub-Actions path: workflow injects secrets via ``env:``
    and tests just read them — no ``.env`` file required.
    """
    if any(not os.getenv(k) for k in _REQUIRED_KEYCLOAK_VARS):
        return False
    if not (os.getenv("KEYCLOAK_REALM") or os.getenv("KEYCLOAK_REALM_NAME")):
        return False
    return True


def _integration_enabled() -> bool:
    if os.getenv("LEX_RUN_KEYCLOAK_INTEGRATION", "").strip() != "1":
        return False
    if _required_env_already_set():
        return True
    env_file = _resolve_integration_env_file()
    if env_file is None or not env_file.exists():
        return False
    from dotenv import dotenv_values
    values = dotenv_values(str(env_file))
    if any(not values.get(k) for k in _REQUIRED_KEYCLOAK_VARS):
        return False
    if not (values.get("KEYCLOAK_REALM") or values.get("KEYCLOAK_REALM_NAME")):
        return False
    return True


def _destructive_enabled() -> bool:
    """Destructive tests need an explicit second opt-in.

    `lex init` rewrites the client's authorization config. Even
    against the dedicated test client, that mutation should never
    happen by accident — the operator must opt in twice.
    """
    return (
        _integration_enabled()
        and os.getenv("LEX_RUN_KEYCLOAK_DESTRUCTIVE", "").strip() == "1"
    )


_SKIP_REASON_RO = (
    "Read-only Keycloak integration tests are off by default. Set "
    "LEX_RUN_KEYCLOAK_INTEGRATION=1, then EITHER export the Keycloak "
    "vars directly (CI secrets / sourced .env) OR drop a populated "
    ".env at lex/test_project/tests/init/.env (gitignored). Required: "
    "KEYCLOAK_URL / KEYCLOAK_REALM / OIDC_RP_CLIENT_ID / "
    "OIDC_RP_CLIENT_SECRET."
)

_SKIP_REASON_DESTRUCTIVE = (
    "Destructive Keycloak integration tests are off by default. They "
    "rewrite the configured client's authorization config (the same "
    "thing `lex init` does in production). To opt in, set "
    "LEX_RUN_KEYCLOAK_INTEGRATION=1 AND LEX_RUN_KEYCLOAK_DESTRUCTIVE=1, "
    "and only point at a dedicated test client."
)


# ---------------------------------------------------------------------
# Shared base: build a real KeycloakSyncManager once for the class.
# ---------------------------------------------------------------------
class _RealKeycloakBase(TestCase):
    """Class-level fixture that constructs a live ``KeycloakSyncManager``.

    Uses the same trick as 1k: explicitly load the operator's real
    ``.env``, reset the ``KeycloakManager`` ``LexSingleton`` cache,
    and patch ``init_module.ENV_FILE`` so ``KeycloakSyncManager``'s
    own cwd-based dotenv resolution doesn't clobber our values with
    the empty placeholder shipped in lex-app's source tree.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Resolution priority — match 1k:
        #
        # 1. If every required Keycloak var is already in os.environ
        #    (CI path) we skip .env resolution entirely. Loading the
        #    canonical Path.cwd()/.env from the lex-app source tree
        #    would clobber our real values with empty placeholders.
        # 2. Otherwise resolve a .env file and source it.
        if _required_env_already_set():
            env_file = None
            cls._loaded_keys = {}
        else:
            env_file = _resolve_integration_env_file()
            if env_file is None:  # pragma: no cover - guarded by skipUnless
                raise unittest.SkipTest(
                    "No integration .env file found and required "
                    "Keycloak vars are not in os.environ."
                )
            cls._loaded_keys = _load_integration_env_into_os(env_file)
        cls._integration_env_file = env_file

        try:
            from lex.api.views.authentication.KeycloakManager import (
                KeycloakManager,
            )
            KeycloakManager._singleton_instance = None
            KeycloakManager._singleton_initialized = False
        except Exception:  # pragma: no cover - defensive
            pass

        try:
            cls._original_env_file = init_module.ENV_FILE
            init_module.ENV_FILE = (
                env_file if env_file is not None
                else Path("/dev/null/lex-no-dotenv")
            )
            cls.mgr = KeycloakSyncManager()
        except Exception as exc:  # pragma: no cover - operator env fault
            raise unittest.SkipTest(
                f"Could not construct KeycloakSyncManager against "
                f"{env_file}: {exc}"
            ) from exc

        if not cls.mgr.kc_manager.client_uuid:
            raise unittest.SkipTest(
                "KeycloakManager could not resolve the configured "
                "client — check KEYCLOAK_URL / KEYCLOAK_REALM and the "
                "client secret."
            )

    @classmethod
    def tearDownClass(cls):
        original = getattr(cls, "_original_env_file", None)
        if original is not None:
            init_module.ENV_FILE = original
        super().tearDownClass()


# ---------------------------------------------------------------------
# 1.95 — Read-only pipeline pieces (always run when integration is on)
# ---------------------------------------------------------------------
@unittest.skipUnless(_integration_enabled(), _SKIP_REASON_RO)
class TestCluster01l_PipelineReads(_RealKeycloakBase):
    """1.95 / 1.96 / 1.97 / 1.98 / 1.99 — every read in the
    full-pipeline path, exercised against the live server.

    Each scenario exercises a single ``KeycloakSyncManager`` method
    that the mocked 1b / 1f tests cover with a stubbed
    ``kc_manager``. The point isn't to re-prove the contract — that's
    1b's job — but to prove the wire format the live server uses
    matches what those mocked tests assume.
    """

    # -- 1.95 ----------------------------------------------------------
    def test_1_95_real_django_models_enumerated(self):
        """1.95: ``get_all_django_models`` walks the live Django apps.

        We don't pin a specific count or list (the test project's
        installed apps drift over time) — just that the method returns
        a non-empty set of dotted ``app.model`` strings. A regression
        here would break every customer's first ``lex init`` run.
        """
        models = self.mgr.get_all_django_models()
        self.assertIsInstance(models, set)
        self.assertGreater(len(models), 0, "Expected at least one Django model.")
        # Every entry is the exact resource-name shape Keycloak stores.
        for entry in models:
            self.assertIsInstance(entry, str)
            self.assertIn(".", entry, f"Expected 'app.Model' shape; got {entry!r}.")

    # -- 1.96 ----------------------------------------------------------
    def test_1_96_real_authz_export_shape(self):
        """1.96: ``export_configs`` round-trips the live authz config.

        Asserts the shape downstream code reads — ``resources`` and
        ``policies`` keys present and lists. If Keycloak ever reshapes
        the authorization-services export, this catches it before
        ``process_model_changes`` mis-keys into the dict.
        """
        cfg = self.mgr.export_configs()
        self.assertIsInstance(cfg, dict)
        # `process_model_changes` calls setdefault for these but the
        # live export should already provide them on a properly-init'd
        # client.
        self.assertIn("resources", cfg)
        self.assertIn("policies", cfg)
        self.assertIsInstance(cfg["resources"], list)
        self.assertIsInstance(cfg["policies"], list)

    # -- 1.97 ----------------------------------------------------------
    def test_1_97_existing_resources_extracted_from_live_export(self):
        """1.97: ``get_existing_keycloak_resources`` against the live export.

        Pure-function step that 1b mocks, sanity-checked against a real
        export.
        """
        cfg = self.mgr.export_configs()
        resources = self.mgr.get_existing_keycloak_resources(cfg)
        self.assertIsInstance(resources, set)
        # On a freshly-bootstrapped test client the set may be empty;
        # we only assert that every entry is a non-empty string.
        for name in resources:
            self.assertIsInstance(name, str)
            self.assertTrue(name, "Resource name must be non-empty.")

    # -- 1.98 ----------------------------------------------------------
    def test_1_98_find_missing_models_against_live(self):
        """1.98: ``find_missing_models`` against the live state.

        ``find_missing_models`` is the entry point ``Command.handle``
        uses to decide what to add. If the live client is up to date,
        the gap is empty; if not, the gap is the set the next ``lex
        init`` would create. Either is valid — we only assert the
        return type and that nothing in the gap is also in the
        existing set.
        """
        all_models = self.mgr.get_all_django_models()
        cfg = self.mgr.export_configs()
        existing = self.mgr.get_existing_keycloak_resources(cfg)
        missing = self.mgr.find_missing_models(all_models, existing, set())
        self.assertIsInstance(missing, set)
        # Disjointness invariant: missing is by definition NOT in existing.
        self.assertFalse(missing & existing, "find_missing_models must exclude existing resources.")
        # And every entry comes from the Django side.
        self.assertTrue(missing <= all_models)

    # -- 1.99 ----------------------------------------------------------
    def test_1_99_client_roles_contain_defaults(self):
        """1.99: ``get_client_roles`` returns the three default roles.

        Note on lazy-create: ``get_client_roles`` will *create* the
        defaults (``admin`` / ``standard`` / ``view-only``) if they
        are missing. For an already-initialised client (the
        ``LEX-Test-*`` test client we target) this is a pure read.
        For a brand-new client this is a one-time write — by that
        point ``lex init`` would have to run anyway, so it's an
        acceptable side effect.
        """
        roles = self.mgr.get_client_roles()
        self.assertIsInstance(roles, dict)
        for required in ("admin", "standard", "view-only"):
            self.assertIn(
                required, roles,
                f"Default role {required!r} must be present after "
                "get_client_roles runs (lazy-creates if missing).",
            )


# ---------------------------------------------------------------------
# 1.100 — DESTRUCTIVE: the real `lex init` pipeline end-to-end.
# ---------------------------------------------------------------------
@unittest.skipUnless(_destructive_enabled(), _SKIP_REASON_DESTRUCTIVE)
class TestCluster01l_FullPipelineDestructive(_RealKeycloakBase):
    """1.100 / 1.101 — run the actual ``lex init`` pipeline end-to-end.

    These tests **mutate** the configured Keycloak client's
    authorization config — the same way ``lex init`` does in
    production. Gated behind a second env var (see module docstring).
    Idempotency is the contract: a second run on the same input
    must not produce drift.
    """

    # -- 1.100 ---------------------------------------------------------
    def test_1_100_process_model_changes_no_op_round_trip(self):
        """1.100: ``process_model_changes`` with empty diff against live.

        Empty ``adds`` / ``deletes`` / ``renames`` exercises the full
        ``export → snapshot → import → restore`` loop **without**
        proposing any model changes. The Keycloak round-trip is the
        only mutation, and since the imported config was just
        exported, the post-state must equal the pre-state.

        We assert two things:
        1. ``process_model_changes`` raises nothing.
        2. ``kc_manager.last_authz_import_error`` is ``None`` after
           the call — that's what ``Command.handle`` itself checks
           before declaring success.
        """
        # Preflight is the gate before any mutation — same gate
        # ``Command.handle`` itself runs.
        self.mgr.verify_client_is_safe_for_init()

        # Empty diff: no adds, no deletes, no renames.
        self.mgr.process_model_changes(
            adds=[],
            deletes=[],
            renames=[],
            preserve_permissions=True,
            ensure_default_authz=False,
        )

        self.assertIsNone(
            self.mgr.kc_manager.last_authz_import_error,
            "After a successful process_model_changes, "
            "last_authz_import_error must be None — Command.handle "
            "uses this to decide whether to declare init successful.",
        )

    # -- 1.101 ---------------------------------------------------------
    def test_1_101_full_init_command_idempotent(self):
        """1.101: ``call_command("init", …)`` end-to-end, twice.

        This is the highest-fidelity test of ``lex init`` we have:
        the actual Django management command, against the actual
        Keycloak. We pass:

        * ``skip_migrations=True`` + ``no_makemigrations=True`` —
          skip every DB-side step (no migrate, no makemigrations).
          The point is to exercise the Keycloak half of the pipeline,
          not Django's migration runner (already covered by Django's
          own tests).
        * ``skip_client_preflight=False`` — leave the preflight ON.
          Failure here means the live client doesn't pass the
          confidential + DEVELOPMENT gate, in which case the
          destructive run shouldn't happen anyway.

        Idempotency: run it twice. A second run on identical Django
        models against the just-synced Keycloak must not crash, must
        leave ``last_authz_import_error`` as ``None``, and must
        produce a stable (read-back) export.
        """
        common_opts = dict(
            skip_migrations=True,
            no_makemigrations=True,
            skip_client_preflight=False,
            ensure_default_authz=False,
            preserve_renamed_permissions=True,
        )

        # First run — full sync.
        out1 = io.StringIO()
        err1 = io.StringIO()
        try:
            call_command("init", stdout=out1, stderr=err1, **common_opts)
        except CommandError as exc:
            self.fail(
                f"First run of `lex init` against live Keycloak failed: {exc}\n"
                f"  stdout: {out1.getvalue()}\n  stderr: {err1.getvalue()}"
            )

        # Read-back snapshot: capture the authorization export and
        # the resource set immediately after the first run.
        first_cfg = self.mgr.export_configs()
        first_resources = self.mgr.get_existing_keycloak_resources(first_cfg)

        # Reset cached export so the second run takes a fresh one.
        self.mgr.exported_configs = None

        # Second run — must be a no-op.
        out2 = io.StringIO()
        err2 = io.StringIO()
        try:
            call_command("init", stdout=out2, stderr=err2, **common_opts)
        except CommandError as exc:
            self.fail(
                "Second run of `lex init` (idempotency check) failed against "
                f"live Keycloak: {exc}\n"
                f"  stdout: {out2.getvalue()}\n  stderr: {err2.getvalue()}"
            )

        # Idempotency invariant: the resource set after the second
        # run is identical to after the first.
        self.mgr.exported_configs = None
        second_cfg = self.mgr.export_configs()
        second_resources = self.mgr.get_existing_keycloak_resources(second_cfg)
        self.assertEqual(
            first_resources, second_resources,
            "Resource set drifted between two consecutive `lex init` runs — "
            "the command is supposed to be idempotent.\n"
            f"  first - second = {first_resources - second_resources}\n"
            f"  second - first = {second_resources - first_resources}",
        )
