"""
Cluster 1k: ``lex init`` Keycloak client safety pre-flight — REAL Keycloak.

Companion to ``test_1j_client_preflight.py``. 1j drives the
``assert_client_is_safe_for_init`` logic in isolation with a stubbed
``kc_manager``; this file wires the same code against a **real**
Keycloak server using the credentials from the project's local
``.env`` file. No mocking of the SDK, no canned responses — every
assertion bottoms out in an HTTP round-trip.

Project convention enforced (matches lex-instance-controller-backend
on the ``e2e-testing`` branch):

* ``publicClient == False``                       → confidential
* when ``DEPLOYMENT_ENVIRONMENT`` is unset/empty: at least one
  ``redirectUris`` entry on host ``localhost``    → DEVELOPMENT

That is the full contract for a client to be safe for ``lex init``.

Why a real-Keycloak integration test?
-------------------------------------

Two things only a live server actually proves:

1. The ``KeycloakManager`` initialization path works end-to-end with
   the real OIDC token endpoint and the real ``OIDC_RP_CLIENT_UUID``
   resolution logic.
2. The shape of ``admin.get_client(uuid)`` matches what the
   pre-flight reads (``publicClient`` flag AND ``redirectUris``
   list). If Keycloak ever changes its response shape this test
   catches it before customers do.

How the test gates itself
-------------------------

* env var ``LEX_RUN_KEYCLOAK_INTEGRATION=1``
* a real ``.env`` file holding the Keycloak credentials. By default
  we look for ``lex/test_project/tests/init/.env`` next to this file
  (gitignored — operators drop their own credentials there). If
  that's missing we fall back to ``init.ENV_FILE`` (the same file
  ``lex init`` reads, ``Path.cwd() / ".env"``). The
  ``LEX_KEYCLOAK_INTEGRATION_ENV`` env var overrides the path
  entirely for operators who keep their credentials elsewhere.

We explicitly load that ``.env`` into ``os.environ`` in
``setUpClass`` rather than relying on the operator to ``source`` it,
because the lex-app source tree carries an empty placeholder
``.env`` that ``KeycloakSyncManager`` would otherwise pick up via
its cwd-based resolution and clobber the values.

Every test in this module is **read-only** — we never call
``update_client``, ``create_*``, ``import_*`` or anything that
mutates server-side state.

Scenario numbering extends ``docs/test-plan/test-clusters.md`` —
sub-cluster 1k picks up at **1.91**.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import TestCase

from django.core.management.base import CommandError
from lex.lex_app.management.commands import init as init_module
from lex.lex_app.management.commands.init import (
    KEYCLOAK_DEV_REDIRECT_HOST,
    KEYCLOAK_ENV_VARS,
    KeycloakSyncManager,
    _deployment_environment_is_set,
    _redirect_uris_indicate_development,
)


def _resolve_integration_env_file() -> Path | None:
    """Resolve the ``.env`` to source for Keycloak integration tests.

    Resolution order:

    1. ``LEX_KEYCLOAK_INTEGRATION_ENV`` env var — explicit override.
    2. ``lex/test_project/tests/init/.env`` — co-located with this
       file. Gitignored, so credentials never get committed; the
       operator drops a real ``.env`` next to the test and the suite
       picks it up regardless of which directory the runner was
       launched from.
    3. ``init.ENV_FILE`` — the same file ``lex init`` itself reads
       (``Path.cwd() / ".env"``); last-resort fallback for runners
       launched from the consumer project root.
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


def _snapshot_keycloak_env() -> dict[str, str | None]:
    return {key: os.environ.get(key) for key in KEYCLOAK_ENV_VARS}


def _restore_keycloak_env(snapshot: dict[str, str | None]) -> None:
    for key in KEYCLOAK_ENV_VARS:
        value = snapshot.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _required_env_already_set() -> bool:
    if any(not os.getenv(k) for k in _REQUIRED_KEYCLOAK_VARS):
        return False
    if not (os.getenv("KEYCLOAK_REALM") or os.getenv("KEYCLOAK_REALM_NAME")):
        return False
    return True


def _normalize_keycloak_realm_env() -> None:
    if not os.getenv("KEYCLOAK_REALM") and os.getenv("KEYCLOAK_REALM_NAME"):
        os.environ["KEYCLOAK_REALM"] = os.environ["KEYCLOAK_REALM_NAME"]


def _reset_keycloak_manager_singleton() -> None:
    try:
        from lex.api.views.authentication.KeycloakManager import (
            KeycloakManager,
        )
        KeycloakManager._singleton_instance = None
        KeycloakManager._singleton_initialized = False
    except Exception:  # pragma: no cover - defensive
        pass


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


_SKIP_REASON = (
    "Real-Keycloak integration tests are off by default. Set "
    "LEX_RUN_KEYCLOAK_INTEGRATION=1, then EITHER export the Keycloak "
    "vars directly (CI secrets / sourced .env) OR drop a populated "
    ".env at lex/test_project/tests/init/.env (gitignored) — or "
    "override with LEX_KEYCLOAK_INTEGRATION_ENV=/path/to/.env. Required: "
    "KEYCLOAK_URL / KEYCLOAK_REALM / OIDC_RP_CLIENT_ID / "
    "OIDC_RP_CLIENT_SECRET."
)


@unittest.skipUnless(_integration_enabled(), _SKIP_REASON)
class TestCluster01k_RealKeycloakPreflight(TestCase):
    """1.91 / 1.92 / 1.93 / 1.94 — pre-flight against the live Keycloak client."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._original_keycloak_env = _snapshot_keycloak_env()

        if _required_env_already_set():
            env_file = None
            cls._loaded_keys = {}
        else:
            env_file = _resolve_integration_env_file()
            if env_file is None:  # pragma: no cover - guarded by skipUnless
                raise unittest.SkipTest("No integration .env file found.")
            cls._loaded_keys = _load_integration_env_into_os(env_file)
        _normalize_keycloak_realm_env()
        cls._integration_env_file = env_file

        # Reset the LexSingleton cache so we connect with the env we
        # just loaded (not whatever earlier import baked in).
        _reset_keycloak_manager_singleton()

        try:
            # Patch ENV_FILE so KeycloakSyncManager.__init__ loads our
            # .env, not the empty placeholder shipped in the lex-app
            # source tree.
            from lex.lex_app.management.commands import init as init_module
            cls._original_env_file = init_module.ENV_FILE
            init_module.ENV_FILE = env_file if env_file is not None else Path(os.devnull)
            cls.mgr = KeycloakSyncManager()
        except Exception as exc:  # pragma: no cover - operator env fault
            raise unittest.SkipTest(
                f"Could not construct KeycloakSyncManager against "
                f"{env_file}: {exc}"
            ) from exc

        if not cls.mgr.kc_manager.client_uuid:
            kc = cls.mgr.kc_manager
            details = [
                f"env_file={env_file}",
                f"client_uuid={kc.client_uuid!r}",
                f"admin={'OK' if kc.admin else 'None (init failed)'}",
                f"oidc={'OK' if kc.oidc else 'None (init failed)'}",
                f"OIDC_RP_CLIENT_UUID={os.getenv('OIDC_RP_CLIENT_UUID')!r}",
                f"OIDC_RP_CLIENT_ID={os.getenv('OIDC_RP_CLIENT_ID')!r}",
            ]
            raise unittest.SkipTest(
                "KeycloakManager could not resolve the configured "
                "client. If `admin=None` the SDK could not "
                "authenticate against the realm — check KEYCLOAK_URL "
                "/ KEYCLOAK_REALM and the client secret. "
                "Diagnostic: " + " | ".join(details)
            )

    @classmethod
    def tearDownClass(cls):
        original = getattr(cls, "_original_env_file", None)
        if original is not None:
            from lex.lex_app.management.commands import init as init_module
            init_module.ENV_FILE = original
        snapshot = getattr(cls, "_original_keycloak_env", None)
        if snapshot is not None:
            _restore_keycloak_env(snapshot)
        _reset_keycloak_manager_singleton()
        super().tearDownClass()

    # -- 1.91 ----------------------------------------------------------
    def test_1_91_real_client_passes_preflight(self):
        """1.91: the configured client satisfies the active preflight contract.

        Asserts the operator's ``.env`` actually points at a Keycloak
        client safe for ``lex init``. Failure → fix is on the
        Keycloak / controller side, not the test side: recreate the
        client as confidential; when ``DEPLOYMENT_ENVIRONMENT`` is
        unset/empty it must also be a ``client_type="DEVELOPMENT"``
        client (which adds ``http://localhost/*`` to its redirect URIs).
        """
        configured_client_id = os.environ["OIDC_RP_CLIENT_ID"]
        configured_uuid = os.environ.get("OIDC_RP_CLIENT_UUID", "")

        try:
            rep = self.mgr.assert_client_is_safe_for_init()
        except CommandError as exc:
            extra_hint = (
                f"  Hint: client must be confidential "
                f"(publicClient=false) AND have a redirect URI on host "
                f"`{KEYCLOAK_DEV_REDIRECT_HOST}` "
                "(controller emits this for client_type=DEVELOPMENT)."
                if not _deployment_environment_is_set()
                else "  Hint: client must be confidential (publicClient=false)."
            )
            self.fail(
                "Real Keycloak preflight failed against the configured "
                "client. The .env points at a client that is not safe "
                "for `lex init`. Fix on the Keycloak side, then re-run.\n"
                f"  CommandError: {exc}\n"
                f"{extra_hint}"
            )

        self.assertEqual(
            rep.get("clientId"), configured_client_id,
            f"Resolved clientId {rep.get('clientId')!r} does not match "
            f"OIDC_RP_CLIENT_ID={configured_client_id!r} from .env — the "
            "manager is operating on the wrong Keycloak client.",
        )
        self.assertIs(
            rep.get("publicClient"), False,
            "Live client must be publicClient=false (confidential).",
        )
        if _deployment_environment_is_set():
            self.assertIsInstance(
                rep.get("redirectUris", []), list,
                "Live client redirectUris must still round-trip as a list.",
            )
        else:
            self.assertTrue(
                _redirect_uris_indicate_development(rep.get("redirectUris", [])),
                f"Live client redirectUris {rep.get('redirectUris')!r} do "
                "not include a localhost entry — controller-side this is "
                "the DEVELOPMENT marker.",
            )
        if configured_uuid:
            self.assertEqual(
                self.mgr.kc_manager.client_uuid, configured_uuid,
                "kc_manager.client_uuid must match OIDC_RP_CLIENT_UUID "
                "from .env after _resolve_client_uuid runs.",
            )

    # -- 1.92 ----------------------------------------------------------
    def test_1_92_real_client_shape_matches_preflight_contract(self):
        """1.92: the live client representation has the fields the
        preflight reads.

        Independent of whether the client *passes* the gate, the
        fields the preflight inspects must exist on the rep returned
        by Keycloak. If Keycloak ever changes its response shape this
        test catches it before a release ships.
        """
        client_uuid = self.mgr.kc_manager.client_uuid
        rep = self.mgr.kc_manager.admin.get_client(client_uuid)

        self.assertIsInstance(
            rep, dict,
            f"admin.get_client must return a dict; got {type(rep).__name__}",
        )
        self.assertIn(
            "clientId", rep,
            "Keycloak client representation is missing `clientId` — "
            "preflight cannot identify the client in error messages.",
        )
        self.assertIn(
            "publicClient", rep,
            "Keycloak client representation is missing `publicClient` "
            "— preflight cannot determine confidential vs public.",
        )
        self.assertIsInstance(
            rep["publicClient"], bool,
            f"`publicClient` must be a bool; got "
            f"{type(rep['publicClient']).__name__}.",
        )
        self.assertIn(
            "redirectUris", rep,
            "Keycloak client representation is missing `redirectUris` "
            "— preflight cannot determine DEVELOPMENT vs STANDARD.",
        )
        self.assertIsInstance(
            rep["redirectUris"], list,
            f"`redirectUris` must be a list; got "
            f"{type(rep['redirectUris']).__name__}.",
        )

    # -- 1.93 ----------------------------------------------------------
    def test_1_93_env_vars_round_trip_through_dotenv(self):
        """1.93: every env var the preflight depends on is reachable
        via ``os.environ`` at the moment the manager runs.
        """
        env_file = self._integration_env_file
        for key in KEYCLOAK_ENV_VARS:
            if key in ("KEYCLOAK_REALM", "KEYCLOAK_REALM_NAME"):
                self.assertTrue(
                    os.getenv("KEYCLOAK_REALM") or os.getenv("KEYCLOAK_REALM_NAME"),
                    f"Either KEYCLOAK_REALM or KEYCLOAK_REALM_NAME must "
                    f"be set in {env_file}.",
                )
                continue
            self.assertTrue(
                os.getenv(key),
                f"Integration test requires {key} in {env_file}.",
            )

    # -- 1.94 ----------------------------------------------------------
    def test_1_94_dev_host_constant_is_documented(self):
        """1.94: pin ``KEYCLOAK_DEV_REDIRECT_HOST`` against the live contract.

        If the controller (lex-instance-controller-backend) ever
        changes the localhost convention, this test reminds the
        maintainer to update both halves of the contract.
        """
        self.assertEqual(KEYCLOAK_DEV_REDIRECT_HOST, "localhost")
