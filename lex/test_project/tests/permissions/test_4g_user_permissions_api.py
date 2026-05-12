"""
Cluster 4g: User-permissions REST surface + Keycloak middleware + audit
permission-scope helpers.

Targets three customer-visible layers that were cold after Cluster 4a/4e/4f:

* ``lex.api.views.authentication.UserPermissionView.UserPermissionsView``
  (21 stmts, 19% baseline) — the endpoint the ra-rbac frontend layer
  calls to learn what a logged-in user can do. Response shape must be
  ``[{action, resource, record?}]``; a silent drift here breaks every
  permission-guarded component in the UI.
* ``lex.api.middleware.keycloak_permissions.KeycloakPermissionsMiddleware``
  (64 stmts, 19% baseline) — the middleware that attaches
  ``request.user_permissions`` / ``request.userinfo`` /
  ``request.client_roles`` so every downstream layer can reason about
  the caller's identity. If the default-list fallback leaks away, the
  entire stack starts raising ``AttributeError`` on anonymous requests.
* ``lex.api.utils.helpers`` (228 stmts, 16% baseline) — audit-log
  permission-scope cache + shadow-instance construction. Every
  AuditLog read goes through ``can_read_from_payload`` → this module;
  a bug here either over-denies (compliance view goes blank) or
  over-allows (PII leak in audit trail).

Intent (from docs/lex_topics/06-permissions-authorization.md +
docs/lex_topics/15-authentication-and-keycloak.md):

    Keycloak is the single source of truth for who can do what. The
    middleware pulls UMA permissions once per authenticated request
    and caches them on the request object; the view translates that
    server-side shape into the ra-rbac client-side shape. Everything
    in between (serializers, filter backends, DRF permission classes)
    reads ``request.user_permissions`` — never calls Keycloak
    directly.

No Keycloak broker required for any scenario — the view pulls from
``request.user_permissions`` (attached by the middleware), the
middleware itself is driven via a factory + a fake ``KeycloakManager``,
and the helpers take plain dicts.

Scenario numbering continues Cluster 4 (4.27 – 4.39).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, TestCase
from lex.api.middleware.keycloak_permissions import (
    KeycloakPermissionsMiddleware,
)
from lex.api.utils import helpers as api_helpers
from lex.api.views.authentication.UserPermissionView import UserPermissionsView
from lex.core.models.LexModel import LexModel


def _with_default_permission_read(model_cls):
    """Temporarily swap a model's ``permission_read`` back to the
    :class:`LexModel` default so the fast-path helpers (which gate
    on ``model_cls.permission_read is LexModel.permission_read``) actually
    fire. Every test-project model overrides ``permission_read``, which
    would otherwise bypass the cache we're testing here.

    Returns a ``patch.object`` context manager, ready to be entered.
    """
    return patch.object(model_cls, "permission_read", LexModel.permission_read)


# =====================================================================
# TestCluster04g_KeycloakMiddleware
#
# Drives ``KeycloakPermissionsMiddleware.__call__`` with a real request
# + a stubbed ``KeycloakManager``. Asserts on the attributes the
# middleware stamps onto the request — everything downstream keys off
# these.
# =====================================================================
class TestCluster04g_KeycloakMiddleware(SimpleTestCase):
    """Scenarios 4.31 – 4.34 — middleware surface."""

    def _mk_request(self, session=None):
        factory = RequestFactory()
        req = factory.get("/any/")
        req.session = session if session is not None else {}
        return req

    # -- 4.31 ----------------------------------------------------------
    def test_4_31_defaults_installed_when_no_access_token(self) -> None:
        """Scenario 4.31: every request leaves the middleware with
        ``request.user_permissions == []``, ``request.userinfo == {}``,
        ``request.client_roles == []`` — even anonymous ones.

        Downstream code (serializers, filter backends) does
        ``getattr(request, "user_permissions", ())`` with the
        expectation that the attribute *always* exists. Removing
        the default assignment would turn every anonymous request
        into an ``AttributeError`` 500.
        """
        get_response = MagicMock(return_value="RESPONSE")
        mw = KeycloakPermissionsMiddleware(get_response)

        req = self._mk_request(session={})  # no oidc_access_token

        with patch(
            "lex.api.middleware.keycloak_permissions.KeycloakManager",
        ) as km_cls:
            result = mw(req)

        self.assertEqual(result, "RESPONSE")
        self.assertEqual(req.user_permissions, [])
        self.assertEqual(req.userinfo, {})
        self.assertEqual(req.client_roles, [])
        km_cls.assert_not_called()  # never instantiated without a token
        get_response.assert_called_once_with(req)

    # -- 4.32 ----------------------------------------------------------
    def test_4_32_populates_identity_from_keycloak_when_token_present(self) -> None:
        """Scenario 4.32: a session carrying ``oidc_access_token``
        makes the middleware fetch UMA perms + userinfo and stamp
        them on the request. The populated values flow through to
        the serializer + filter-backend layers unchanged.
        """
        get_response = MagicMock(return_value="RESPONSE")
        mw = KeycloakPermissionsMiddleware(get_response)

        req = self._mk_request(session={"oidc_access_token": "tok-123"})

        fake_km = MagicMock()
        fake_km.get_uma_permissions.return_value = [
            {"rsname": "lex_app.Invoice", "scopes": ["read"]},
        ]
        fake_km.oidc.userinfo.return_value = {
            "email": "u@x",
            "client_roles": ["admin", "viewer"],
        }

        with patch(
            "lex.api.middleware.keycloak_permissions.KeycloakManager",
            return_value=fake_km,
        ):
            mw(req)

        fake_km.get_uma_permissions.assert_called_once_with("tok-123")
        self.assertEqual(len(req.user_permissions), 1)
        self.assertEqual(req.user_permissions[0]["rsname"], "lex_app.Invoice")
        self.assertEqual(req.userinfo["email"], "u@x")
        self.assertEqual(
            sorted(req.client_roles), ["admin", "viewer"],
            "client_roles must be extracted from userinfo — downstream "
            "code (UserContext.from_request) relies on this attribute.",
        )

    # -- 4.32b ---------------------------------------------------------
    def test_4_32b_uma_fetch_failure_keeps_defaults(self) -> None:
        """Scenario 4.32b: if ``get_uma_permissions`` raises, the
        request continues through to the view — the middleware MUST
        NOT 500 on a Keycloak outage. The default ``[]`` survives.
        """
        get_response = MagicMock(return_value="RESPONSE")
        mw = KeycloakPermissionsMiddleware(get_response)
        req = self._mk_request(session={"oidc_access_token": "tok-xyz"})

        fake_km = MagicMock()
        fake_km.get_uma_permissions.side_effect = RuntimeError("kc down")
        fake_km.oidc = None  # no userinfo path either

        with patch(
            "lex.api.middleware.keycloak_permissions.KeycloakManager",
            return_value=fake_km,
        ):
            mw(req)

        self.assertEqual(req.user_permissions, [])
        self.assertEqual(req.userinfo, {})
        self.assertEqual(req.client_roles, [])

    # -- 4.33 ----------------------------------------------------------
    def test_4_33_extract_client_roles_shapes(self) -> None:
        """Scenario 4.33: ``_extract_client_roles`` accepts every
        shape Keycloak can return — str, list, tuple, set, dict.

        Keycloak's ``userinfo`` payload shape depends on the realm's
        mappers. One realm returns a list, another returns a dict of
        ``{client_id: [roles]}``. The helper normalises all of them
        to a flat list so downstream code can treat roles uniformly.
        """
        extract = KeycloakPermissionsMiddleware._extract_client_roles

        # None / missing → []
        self.assertEqual(extract({}), [])
        self.assertEqual(extract({"client_roles": None}), [])

        # bare string → wrapped in list
        self.assertEqual(extract({"client_roles": "admin"}), ["admin"])

        # list / tuple / set → filtered to strings
        self.assertEqual(extract({"client_roles": ["a", "b"]}), ["a", "b"])
        self.assertEqual(
            sorted(extract({"client_roles": ("a", "b", 42)})),
            ["a", "b"],
            "Non-string entries must be filtered — otherwise a role "
            "that is accidentally an int propagates into the scope "
            "comparisons and silently fails.",
        )
        self.assertEqual(
            sorted(extract({"client_roles": frozenset(["x", "y"])})),
            ["x", "y"],
        )

        # dict of client_id → roles
        got = extract(
            {
                "client_roles": {
                    "my-client": ["admin", "viewer"],
                    "other-client": "editor",
                    "junk": 42,
                },
            }
        )
        self.assertEqual(sorted(got), ["admin", "editor", "viewer"])

        # completely unknown shape → []
        self.assertEqual(extract({"client_roles": 42}), [])

    # -- 4.34 ----------------------------------------------------------
    def test_4_34_cleanup_invalid_tokens_scrubs_session(self) -> None:
        """Scenario 4.34: the session-cleanup helper removes ``None``
        / empty / non-string tokens and their associated expiry
        metadata.

        A session row that stores ``None`` for ``oidc_id_token``
        crashes the JWT parser on the next request; the cleanup
        helper is what keeps sessions usable across framework
        upgrades that changed how tokens are stored.
        """
        mw = KeycloakPermissionsMiddleware(lambda r: r)

        class _FakeSession(dict):
            """Tiny ``Session``-like wrapper — session.save() is a no-op
            but must exist because the helper calls it."""

            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.saved = False

            def save(self):
                self.saved = True

        session = _FakeSession(
            {
                "oidc_id_token": None,
                "oidc_access_token": "good-token",
                "oidc_refresh_token": "",  # empty
                "oidc_access_expires_at": 1234,
                "oidc_logout_state": "abc",
                "unrelated_key": "stays",
            }
        )
        req = self._mk_request(session=session)
        mw.cleanup_invalid_tokens(req)

        self.assertNotIn("oidc_id_token", session, "None token must be deleted")
        self.assertNotIn("oidc_refresh_token", session, "Empty token must be deleted")
        self.assertIn(
            "oidc_access_token", session,
            "Valid string tokens must survive the cleanup",
        )
        self.assertNotIn(
            "oidc_access_expires_at", session,
            "When any token was cleaned, the related expiry metadata "
            "must also be scrubbed so the session is consistent.",
        )
        self.assertNotIn("oidc_logout_state", session)
        self.assertIn("unrelated_key", session)
        self.assertTrue(session.saved, "Session must be .save()'d after scrubbing")


# =====================================================================
# TestCluster04g_HelpersScopeCache
#
# Pure-function tests on ``lex/api/utils/helpers.py``. These functions
# are the cache layer for the audit-log visibility contract — they
# translate Keycloak's per-rsname permission payload into either a
# global-read boolean or a frozenset of allowed ids, then answer
# "can this request read this payload?" in O(1).
# =====================================================================
class TestCluster04g_HelpersScopeCache(TestCase):
    """Scenarios 4.35 – 4.39 — audit permission-scope + shadow-instance."""

    def setUp(self) -> None:
        super().setUp()
        # Reset module-level cache so tests don't pollute each other.
        api_helpers._FIELD_MAP_CACHE.clear()

    def _req(self, user_permissions=()):
        factory = RequestFactory()
        req = factory.get("/any/")
        req.user_permissions = list(user_permissions)
        return req

    # -- 4.35 ----------------------------------------------------------
    def test_4_35_global_read_scope(self) -> None:
        """Scenario 4.35: a permission with ``rsname=lex_app.<Model>``,
        scope ``read``, and NO ``resource_set_id`` is a **global**
        read grant — the scope cache returns ``(True, frozenset())``
        and every payload is readable without consulting row ids.

        This is the common case — an admin role carries a global
        read scope per model, not per-row.
        """
        # Use a real LexModel to hit the LexModel.permission_read
        # fast-path check inside the helper. Every test-project model
        # overrides ``permission_read``, so we restore the default for
        # the scope of this scenario.
        from lex.test_project.tests.crud_api.models import SimpleItem

        req = self._req(
            user_permissions=[
                {
                    "rsname": "lex_app.SimpleItem",
                    "scopes": ["read", "edit"],
                    # no resource_set_id → global
                },
            ],
        )

        with _with_default_permission_read(SimpleItem):
            scope = api_helpers.get_default_read_permission_scope(req, SimpleItem)
        self.assertIsNotNone(scope, "LexModel with default permission_read must get a scope entry")
        has_global, allowed_ids = scope
        self.assertTrue(has_global, "Missing resource_set_id means global read")
        self.assertEqual(allowed_ids, frozenset())

    # -- 4.36 ----------------------------------------------------------
    def test_4_36_row_scoped_read_permissions(self) -> None:
        """Scenario 4.36: multiple permissions with distinct
        ``resource_set_id`` values collapse to a ``frozenset`` of
        allowed ids — this is the row-level UMA contract.
        """
        from lex.test_project.tests.crud_api.models import SimpleItem

        req = self._req(
            user_permissions=[
                {
                    "rsname": "lex_app.SimpleItem",
                    "scopes": ["read"],
                    "resource_set_id": "1",
                },
                {
                    "rsname": "lex_app.SimpleItem",
                    "scopes": ["read"],
                    "resource_set_id": "5",
                },
                # A permission without 'read' scope must NOT contribute
                # (would be a silent over-allow if included).
                {
                    "rsname": "lex_app.SimpleItem",
                    "scopes": ["edit"],
                    "resource_set_id": "99",
                },
                # A permission for a different model — ignored.
                {
                    "rsname": "lex_app.OtherModel",
                    "scopes": ["read"],
                    "resource_set_id": "2",
                },
            ],
        )
        with _with_default_permission_read(SimpleItem):
            has_global, allowed_ids = api_helpers.get_default_read_permission_scope(req, SimpleItem)

        self.assertFalse(has_global, "No permission without resource_set_id → not global")
        self.assertEqual(
            allowed_ids, frozenset({"1", "5"}),
            "Only read-scoped permissions for THIS model must contribute, "
            "and only their resource_set_ids — leaking the edit-only row "
            "would be a silent over-allow.",
        )

    def test_4_36b_scope_cache_memoizes_per_request(self) -> None:
        """4.36b: the scope cache hangs off the request object. Two
        calls on the same request + model share a single computation."""
        from lex.test_project.tests.crud_api.models import SimpleItem

        req = self._req(
            user_permissions=[
                {"rsname": "lex_app.SimpleItem", "scopes": ["read"]},
            ],
        )
        with _with_default_permission_read(SimpleItem):
            scope1 = api_helpers.get_default_read_permission_scope(req, SimpleItem)
            scope2 = api_helpers.get_default_read_permission_scope(req, SimpleItem)
        self.assertIs(
            scope1, scope2,
            "Per-request cache must return the same tuple object — "
            "recomputing every call would be an N+1 on the AuditLog list.",
        )

    # -- 4.37 ----------------------------------------------------------
    def test_4_37_can_read_with_default_scope_branches(self) -> None:
        """Scenario 4.37: ``_can_read_with_default_permission_scope``
        returns the final allow/deny given a resolved scope.

        Branches exercised:
         (a) global read → True regardless of payload id
         (b) scoped + matching id in payload → True
         (c) scoped + non-matching id → False
         (d) scoped + no id in payload → False (can't be identified)
        """
        from lex.test_project.tests.crud_api.models import SimpleItem

        with _with_default_permission_read(SimpleItem):
            # (a) global
            req_global = self._req(
                user_permissions=[{"rsname": "lex_app.SimpleItem", "scopes": ["read"]}],
            )
            self.assertTrue(
                api_helpers.can_read_with_default_permission_scope(
                    req_global, SimpleItem, {"id": 999},
                ),
            )

            # (b) scoped + match — "id" key in payload
            req_scoped = self._req(
                user_permissions=[
                    {
                        "rsname": "lex_app.SimpleItem",
                        "scopes": ["read"],
                        "resource_set_id": "42",
                    },
                ],
            )
            self.assertTrue(
                api_helpers.can_read_with_default_permission_scope(
                    req_scoped, SimpleItem, {"id": "42"},
                ),
                "Scoped permission with matching id must allow",
            )

            # (c) scoped + no match
            self.assertFalse(
                api_helpers.can_read_with_default_permission_scope(
                    req_scoped, SimpleItem, {"id": 99},
                ),
                "Scoped permission with non-matching id must deny",
            )

            # (d) scoped + no id — can't be identified → deny
            self.assertFalse(
                api_helpers.can_read_with_default_permission_scope(
                    req_scoped, SimpleItem, {},
                ),
                "Scoped permission without identifiable id must deny — "
                "otherwise an unlabelled audit row silently passes the check.",
            )

    # -- 4.38 ----------------------------------------------------------
    def test_4_38_build_shadow_instance_coerces_scalars(self) -> None:
        """Scenario 4.38: ``build_shadow_instance`` builds a non-persisted
        model instance from an AuditLog payload dict. Scalar fields
        round-trip correctly, unknown keys are silently dropped
        (forward-compat with renamed fields in old audit rows).
        """
        from lex.test_project.tests.crud_api.models import SimpleItem

        shadow = api_helpers.build_shadow_instance(
            SimpleItem,
            {
                "name": "Hello",
                "value": 42,
                "description": "body",
                "ignored_extra": "dropped silently",
            },
        )

        self.assertIsNotNone(shadow, "Shadow must build for a valid payload")
        self.assertEqual(shadow.name, "Hello", "CharField round-trip")
        self.assertEqual(shadow.value, 42, "IntegerField round-trip")
        self.assertEqual(shadow.description, "body")
        # Unknown keys are silently dropped — otherwise an older
        # AuditLog row that references a since-renamed field would
        # crash the shadow path and break the compliance view.
        self.assertFalse(hasattr(shadow, "ignored_extra"))

    def test_4_38b_build_shadow_instance_empty_payload_returns_none(self) -> None:
        """4.38b: empty / falsy payload short-circuits and returns None
        — the caller treats None as 'cannot evaluate, allow by default'."""
        from lex.test_project.tests.crud_api.models import SimpleItem

        self.assertIsNone(api_helpers.build_shadow_instance(SimpleItem, {}))
        self.assertIsNone(api_helpers.build_shadow_instance(SimpleItem, None))

    def test_4_38c_parse_value_fk_dict_extracts_id(self) -> None:
        """4.38c: ``_parse_value`` handles the FK-dict payload shape
        (``{"id": X, "short_description": ...}``) that ``LexSerializer``
        emits on GET. This is what lets a shadow instance rebuild from
        an audit row whose FK columns were serialised as dicts.
        """
        from django.db import models as djmodels
        from django.db.models import ForeignKey

        # Real ForeignKey instance — build_shadow_instance will match
        # via isinstance(field, ForeignKey) and route through the dict
        # branch.
        fk_field = ForeignKey("lex_app.SimpleItem", on_delete=djmodels.CASCADE)
        fk_field.name = "related"

        self.assertEqual(
            api_helpers._parse_value(fk_field, {"id": 7, "short_description": "x"}),
            7,
            "FK-dict must be collapsed to the bare id so Django can "
            "assign to <field>_id downstream.",
        )
        # FK-dict with no "id" key → None (preserves the invariant that
        # the caller can distinguish 'unset' from 'set to id 0').
        self.assertIsNone(api_helpers._parse_value(fk_field, {"short_description": "orphan"}))
        # Non-dict FK value passes through untouched.
        self.assertEqual(api_helpers._parse_value(fk_field, 42), 42)

    # -- 4.39 ----------------------------------------------------------
    def test_4_39_can_read_from_payload_unresolvable_model_allows(self) -> None:
        """Scenario 4.39: when ``resolve_target_model`` cannot find a
        model for the audit log row (deleted model, renamed resource,
        garbage content_type), the helper returns True.

        This is the **allow-by-default on contract gap** — losing a
        single row from the compliance view because the model was
        renamed would be a worse failure than letting the row through
        one audit-list request. The compliance auditor sees the
        untargeted row; they can investigate.
        """
        audit_log = SimpleNamespace(
            resource="nonexistent_model_name_no_match",
            content_type_id=None,
            payload={"id": 1},
        )
        req = self._req()

        self.assertTrue(
            api_helpers.can_read_from_payload(req, audit_log),
            "Unresolvable target model must fall back to allow-by-default",
        )

    def test_4_39b_can_read_from_payload_honours_scope_cache(self) -> None:
        """4.39b: with a resolvable target model and a global-read
        scope, ``can_read_from_payload`` returns True via the fast
        path — no shadow-instance construction required.
        """
        from lex.test_project.tests.crud_api.models import SimpleItem

        req = self._req(
            user_permissions=[
                {"rsname": "lex_app.SimpleItem", "scopes": ["read"]},
            ],
        )
        audit_log = SimpleNamespace(
            resource="simpleitem",
            content_type_id=None,
            payload={"id": 1, "name": "x"},
        )

        with _with_default_permission_read(SimpleItem):
            self.assertTrue(api_helpers.can_read_from_payload(req, audit_log))

    def test_4_39c_can_read_from_payload_scoped_denies_non_matching(self) -> None:
        """4.39c: row-scoped permission + payload id that isn't in
        the allow-set → False. This is the per-row audit-log
        enforcement used by the AuditLog filter backend.
        """
        from lex.test_project.tests.crud_api.models import SimpleItem

        req = self._req(
            user_permissions=[
                {
                    "rsname": "lex_app.SimpleItem",
                    "scopes": ["read"],
                    "resource_set_id": "1",  # only row 1
                },
            ],
        )
        audit_log = SimpleNamespace(
            resource="simpleitem",
            content_type_id=None,
            payload={"id": 2},  # row 2 → denied
        )
        with _with_default_permission_read(SimpleItem):
            self.assertFalse(api_helpers.can_read_from_payload(req, audit_log))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()






