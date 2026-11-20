"""
Cluster 4n: Keycloak permissions in Reflex dashboards.

Intent (from lex/lex_app/reflex/auth.py and the Streamlit proxy it replaces):

    A Streamlit dashboard reads the user's Keycloak UMA permissions from
    ``st.session_state.permissions``, which the proxy fills. A Reflex
    dashboard reads the same list through ``current_permissions(self)``,
    and gates a handler on it with
    ``lex_event(auth=has_permission(Model, scope))``. The contract:

        * the permissions are the ones ``KeycloakManager`` -- the client
          the API enforces with -- returns for the user's access token,
          fetched once per token, and fetched again once it is refreshed;
        * a failed lookup keeps what the SAME user was last granted (a
          Keycloak blip must not demote someone mid-session) and never
          lets one user's permissions stand in for another's;
        * without a sign-in there are none, and Keycloak is not asked;
        * ``has_permission`` names a model's resource the way lex-app
          registers it (``<app_label>.<ModelName>``), counts model-wide
          grants only -- as the API's default read check does -- and fails
          closed;
        * ``current_access_token`` hands any state the user's bearer token.

The access token comes from the session's binding to the user's lex-app
sign-in (``lex.lex_app.reflex.session``, batch 1bx): the tests hand a session
its binding the way the socket guard does (``X-Lex-Binding``) and give the
binding its token; the only boundary patched is ``KeycloakManager``,
Keycloak's client.

Scenario numbering matches
docs/test-plan/test-clusters.md#4-permissions.
"""

from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import jwt
import pytest
from asgiref.sync import async_to_sync

from lex.lex_app.reflex import auth, session
from lex.lex_app.reflex.auth import current_access_token, current_permissions, has_permission
from lex.lex_app.reflex.dashboards import LexDashboardState

from .models import KeycloakItem

pytestmark = pytest.mark.permissions

_KEYCLOAK = "lex.api.views.authentication.KeycloakManager.KeycloakManager"
_RESOURCE = "lex_app.KeycloakItem"
_BINDING = "b" * 64


def _token(subject: str, jti: str = "1") -> str:
    """An access token for ``subject``; ``jti`` tells a refreshed one apart."""
    return jwt.encode({"sub": subject, "jti": jti, "exp": int(time.time()) + 600}, "not-verified-here",
                      algorithm="HS256")


def _session(binding: str = _BINDING):
    """One browser session's state tree, its socket carrying ``binding`` (as the guard sets it)."""
    from reflex.istate.data import HeaderData, ReflexURL, RouterData
    from reflex.state import State

    root = State()
    root.router = RouterData(url=ReflexURL("http://localhost:8502/"), headers=HeaderData.from_router_data(
        {"headers": {"host": "localhost:8503", "x-lex-binding": binding}}))
    return root


def _substate(root, state_cls):
    return root.get_substate(state_cls.get_full_name().split("."))


def _present(subject: str | None, token: str = "", binding: str = _BINDING) -> None:
    """Sign ``subject`` in on the binding with ``token`` -- or, with ``None``, leave it signed out."""
    session.forget(binding)
    if subject is None:
        return
    who = SimpleNamespace(sub=subject, issuer="kc", email=f"{subject}@example.com")
    session._remember(binding, session.Standing(time.monotonic(), 7, who, {"sub": subject}))
    with session._LOCK:
        session._TOKENS[7] = (token, int(time.time()) + 600)


def _grant(scopes, *, resource=_RESOURCE, record=None) -> dict:
    grant = {"rsname": resource, "scopes": list(scopes)}
    if record is not None:
        grant["resource_set_id"] = record
    return grant


class _ReflexSession(TestCase):
    """A signed-out session and a patched Keycloak client."""

    def setUp(self) -> None:
        self.root = _session()
        patcher = patch(_KEYCLOAK)
        self.keycloak = patcher.start().return_value
        self.addCleanup(patcher.stop)
        for cache in (auth._UMA, auth._LAST_BY_SUBJECT):
            cache.clear()
            self.addCleanup(cache.clear)
        session.forget()
        self.addCleanup(session.forget)

    def permissions(self) -> list[dict]:
        return async_to_sync(current_permissions)(self.root)

    def asked_with(self) -> list[str]:
        """The access tokens Keycloak was asked about, in order."""
        return [call.args[0] for call in self.keycloak.get_uma_permissions.call_args_list]


class TestCluster04n_PermissionLookup(_ReflexSession):
    """The UMA permissions a dashboard sees, and when Keycloak is asked."""

    # -- 4.75 ----------------------------------------------------------
    def test_4_75_permissions_are_fetched_once_per_access_token(self) -> None:
        """
        Scenario 4.75: one Keycloak lookup per access token.
        Given: a signed-out session, then Ada signed in, then her token refreshed.
        When:  a dashboard reads ``current_permissions`` several times.
        Then:  signed out it is ``[]`` without asking Keycloak; signed in it is
               exactly what ``KeycloakManager`` returned for her token, asked
               once however often it is read; a refreshed token is asked
               about again; the cache keeps a digest of the token, never the
               token itself; callers get a copy they cannot corrupt it through.
        """
        self.assertEqual(self.permissions(), [])
        self.keycloak.get_uma_permissions.assert_not_called()

        first = _token("ada", jti="1")
        self.keycloak.get_uma_permissions.return_value = [_grant(["read"])]
        _present("ada", first)

        self.assertEqual(self.permissions(), [_grant(["read"])])
        self.permissions().append(_grant(["delete"]))
        self.assertEqual(self.permissions(), [_grant(["read"])])
        self.assertEqual(self.asked_with(), [first], "one lookup per token")

        self.assertEqual(list(auth._UMA), [hashlib.sha256(first.encode()).hexdigest()],
                         "the cache keeps a digest of the token, never the token")

        refreshed = _token("ada", jti="2")
        self.keycloak.get_uma_permissions.return_value = [_grant(["read", "edit"])]
        _present("ada", refreshed)

        self.assertEqual(self.permissions(), [_grant(["read", "edit"])])
        self.assertEqual(self.asked_with(), [first, refreshed])

    # -- 4.76 ----------------------------------------------------------
    def test_4_76_a_failed_lookup_never_demotes_a_user_or_promotes_another(self) -> None:
        """
        Scenario 4.76: Keycloak failures degrade safely.
        Given: Ada granted ``read``, then her refreshed token meets a Keycloak
               that fails (raises, or answers with an error body), then Bob
               signs in on the same session while it still fails.
        When:  the permissions are read after each step.
        Then:  Ada keeps ``read`` while Keycloak fails, and every read retries
               until it answers; Bob gets ``[]``, never Ada's grants.
        """
        _present("ada", _token("ada", jti="1"))
        self.keycloak.get_uma_permissions.return_value = [_grant(["read"])]
        self.assertEqual(self.permissions(), [_grant(["read"])])

        _present("ada", _token("ada", jti="2"))
        failures = {
            "raises": {"side_effect": RuntimeError("keycloak down")},
            "answers nothing": {"side_effect": None, "return_value": None},
            "answers an error body": {"side_effect": None, "return_value": {"error": "invalid_grant"}},
        }
        with self.assertLogs("lex.lex_app.reflex.auth", "ERROR") as logs:
            for failure, behaviour in failures.items():
                with self.subTest(failure):
                    self.keycloak.get_uma_permissions.configure_mock(**behaviour)
                    self.assertEqual(self.permissions(), [_grant(["read"])],
                                     "the same user keeps what they were granted")
        self.assertIn("Could not fetch Keycloak UMA permissions", logs.output[0])
        self.assertEqual(self.keycloak.get_uma_permissions.call_count, 1 + len(failures),
                         "each read after a failure asks Keycloak again")

        self.keycloak.get_uma_permissions.configure_mock(side_effect=None, return_value=[])
        self.assertEqual(self.permissions(), [], "Keycloak's answer, once it gives one, stands")

        self.keycloak.get_uma_permissions.return_value = [_grant(["read", "edit"])]
        _present("ada", _token("ada", jti="3"))
        self.assertEqual(self.permissions(), [_grant(["read", "edit"])])

        self.keycloak.get_uma_permissions.configure_mock(side_effect=RuntimeError("keycloak down"))
        _present("bob", _token("bob"))
        with self.assertLogs("lex.lex_app.reflex.auth", "ERROR"):
            self.assertEqual(self.permissions(), [], "another user never inherits Ada's grants")

    # -- 4.77 ----------------------------------------------------------
    def test_4_77_signing_out_forgets_the_permissions(self) -> None:
        """
        Scenario 4.77: without a sign-in there are no permissions.
        Given: Ada signed in and granted ``edit``.
        When:  her session's binding stops answering (she signed out, or her
               sign-in ended), then Bob signs in on the same session.
        Then:  the signed-out session sees ``[]`` without asking Keycloak, and
               no session state holds her permissions; Bob gets his own lookup.
        """
        _present("ada", _token("ada"))
        self.keycloak.get_uma_permissions.return_value = [_grant(["edit"])]
        self.assertEqual(self.permissions(), [_grant(["edit"])])

        _present(None)
        self.assertEqual(self.permissions(), [])
        self.assertEqual(self.keycloak.get_uma_permissions.call_count, 1)
        self.assertNotIn("rsname", repr(self.root.dict()), "permissions never live in session state")

        bob = _token("bob")
        self.keycloak.get_uma_permissions.return_value = [_grant(["read"])]
        _present("bob", bob)
        self.assertEqual(self.permissions(), [_grant(["read"])])
        self.assertEqual(self.asked_with()[-1], bob)


class TestCluster04n_HasPermission(_ReflexSession):
    """``has_permission`` as an ``auth=`` check, as RFDS's PolicyMiddleware runs it."""

    def check(self, target, scope="read") -> bool:
        from rfds.auth import AuthContext

        ctx = AuthContext(userinfo={}, payload={}, surface="browser", state=self.root, handler="h")
        return async_to_sync(has_permission(target, scope))(ctx)

    # -- 4.78 ----------------------------------------------------------
    def test_4_78_has_permission_counts_model_wide_grants_of_the_scope(self) -> None:
        """
        Scenario 4.78: ``has_permission(target, scope)`` mirrors lex-app's own checks.
        Given: Ada granted ``read``/``list`` on KeycloakItem, ``edit`` on only
               one of its records, ``delete`` on another resource, and entries
               a check must survive (not a dict, no scopes).
        When:  checks run for the model class, its resource name, and others.
        Then:  a model class resolves to ``lex_app.KeycloakItem``; only a
               model-wide grant of the scope counts (``read`` by default); a
               record grant, another resource's grant, a signed-out user and
               a Keycloak that never answered are all denials; anything but a
               model or a name is refused when the check is declared.
        """
        self.keycloak.get_uma_permissions.return_value = [
            "not-a-grant",
            {"rsname": _RESOURCE},
            _grant(["read", "list"]),
            _grant(["edit"], record="42"),
            _grant(["delete"], resource="lex_app.OtherItem"),
        ]
        self.assertFalse(self.check(KeycloakItem), "signed out: denied")
        self.keycloak.get_uma_permissions.assert_not_called()

        _present("ada", _token("ada"))
        self.assertTrue(self.check(KeycloakItem))
        self.assertTrue(self.check(KeycloakItem, "list"))
        self.assertTrue(self.check(_RESOURCE, "read"))
        self.assertFalse(self.check(KeycloakItem, "edit"), "a record grant does not open the model")
        self.assertFalse(self.check(KeycloakItem, "delete"), "another resource's grant")
        self.assertFalse(self.check("lex_app.keycloakitem"), "resource names are exact")

        self.assertEqual(
            has_permission(KeycloakItem, "edit").__qualname__,
            "has_permission('lex_app.KeycloakItem', 'edit')",
            "a denial in the log names what was checked",
        )
        with self.assertRaisesRegex(TypeError, "model class or a Keycloak resource name"):
            has_permission(object())

        self.root = _session("c" * 64)
        self.keycloak.get_uma_permissions.side_effect = RuntimeError("keycloak down")
        _present("bob", _token("bob"), binding="c" * 64)
        with self.assertLogs("lex.lex_app.reflex.auth", "ERROR"):
            self.assertFalse(self.check(_RESOURCE), "Keycloak never answered for Bob: denied")


class TestCluster04n_AccessToken(_ReflexSession):
    """The user's bearer token, for calling lex-app's API as them."""

    # -- 4.79 ----------------------------------------------------------
    def test_4_79_any_state_reaches_the_users_access_token(self) -> None:
        """
        Scenario 4.79: ``current_access_token`` / ``current_permissions`` from any state.
        Given: a dashboard's own state in a session, signed out and signed in.
        When:  it asks for the user's access token and permissions.
        Then:  it gets the token of the session's sign-in (``""`` signed out)
               and the user's permissions -- read through the session's own
               binding, so both follow the sign-in.
        """
        dashboard = _substate(self.root, LexDashboardState)
        self.assertEqual(async_to_sync(current_access_token)(dashboard), "")
        self.assertEqual(async_to_sync(current_permissions)(dashboard), [])

        token = _token("ada")
        self.keycloak.get_uma_permissions.return_value = [_grant(["read"])]
        _present("ada", token)

        self.assertEqual(async_to_sync(current_access_token)(dashboard), token)
        self.assertEqual(async_to_sync(current_permissions)(dashboard), [_grant(["read"])])
        self.assertEqual(self.asked_with(), [token])
