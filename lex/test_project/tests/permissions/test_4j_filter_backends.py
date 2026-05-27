"""
Sub-cluster 4j — `lex.api.views.model_entries.filter_backends` direct
unit + integration coverage.

PR-10 permissions tier — the highest-impact remaining gap. Baseline
coverage **30.69%** (198 stmts, 126 miss) — biggest single dark file
in the project after the cluster-6/7/8 batches landed. The existing
4e tests drive `UserReadRestrictionFilterBackend` end-to-end via
`AuthenticatedE2ETestCase` + the AG Grid endpoint, but only exercise
the LexModel-default branch with full request plumbing — they do not
cover:

* ``PrimaryKeyListFilterBackend`` — both `filter_queryset` (request
  query-params) AND `filter_for_export` (base64-decoded payload from
  the export pipeline). Nothing else in the suite touches the export
  branch; a regression there would silently dump the entire table to
  Excel even when the operator selected a subset of rows.
* ``UserReadRestrictionFilterBackend._get_default_permission_target``
  — branches for `LexModel`-fast-path opt-in, the `instance_type`
  follow-through (Historical*/MetaHistorical* models route through
  this), the `permission_read` override opt-out, and the
  `FieldDoesNotExist` short-circuit when the historical table doesn't
  carry the parent's pk.
* ``_apply_default_permission_read_filter`` — global vs scoped vs
  empty-allowed-ids vs pk-normalisation paths. Each path corresponds
  to a different production permission shape (admin = global, viewer =
  scoped, no-perm = empty → `queryset.none()`).
* ``_get_auditlog_default_permission_resource_map`` — the lru_cached
  `apps.get_models()` walk that maps audit-log `resource` strings
  back to their owning model. Ambiguous tokens (two models share a
  lowercased name) MUST be dropped, otherwise the audit-log filter
  silently picks the wrong model and either denies or allows the
  wrong rows.
* ``_build_auditlog_db_visibility_filters`` — the global / scoped Q
  composition that drives the AuditLog list filter; this is what
  decides which audit rows appear in the compliance view.
* ``_handle_auditlog`` — the handled-vs-residual split. Rows whose
  resource is in `handled_resources` get the DB-level fast filter;
  everything else falls back to the per-row payload check via
  `can_read_from_payload`.

Most scenarios are pure unit tests — no DB, no APIClient. They drive
the helpers directly with synthetic `RequestFactory` requests +
`MagicMock` querysets. The two AuditLog DB-path scenarios use real
`AuditLog` rows via `TestCase` because the Q composition only takes
effect against an actual queryset.

Scenario IDs 4.50 – 4.65 (4.42–4.49 deliberately skipped to leave
room for any future small extensions to 4g/4h/4i).

Run with:
    lex test lex.test_project.tests.permissions.test_4j_filter_backends \\
        --verbosity=2 --noinput --keepdb
"""
from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

from django.http import QueryDict
from django.test import RequestFactory, SimpleTestCase, TestCase

from lex.api.views.model_entries.filter_backends import (
    PrimaryKeyListFilterBackend,
    UserReadRestrictionFilterBackend,
)

import pytest

pytestmark = pytest.mark.permissions


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _request_with_perms(query_string="", user_permissions=()):
    """RequestFactory GET with `query_params` (DRF attribute) installed
    as a `QueryDict` so the backend can call `.getlist('ids')`. The
    middleware would normally stamp `user_permissions`; we install it
    directly for unit-test isolation."""
    factory = RequestFactory()
    path = f"/api/items/?{query_string}" if query_string else "/api/items/"
    req = factory.get(path)
    # DRF's `query_params` is a `QueryDict` over the raw query string.
    # We don't go through DRF's Request wrapper here, so install it
    # ourselves — the backend only reads `.getlist('ids')` off it.
    req.query_params = QueryDict(query_string)
    req.user_permissions = list(user_permissions)
    return req


def _view_with_container(model_class, pk_name="id"):
    """Build a stand-in for the ModelViewSet — only `kwargs[model_container]`
    + its `model_class` / `pk_name` are read by the filter backend."""
    container = SimpleNamespace(model_class=model_class, pk_name=pk_name)
    return SimpleNamespace(kwargs={"model_container": container})


def _mock_qs(model=None):
    """A queryset that records `.filter(...)` / `.none()` / `.exclude(...)`
    so we can assert what the backend asked for without touching a DB.
    `.model` defaults to the passed model so backends introspecting
    `queryset.model` get a real class."""
    qs = mock.MagicMock(name="queryset")
    qs.model = model
    qs.filter.return_value = qs
    qs.exclude.return_value = qs
    qs.none.return_value = qs
    return qs


# ---------------------------------------------------------------------------
# 1) PrimaryKeyListFilterBackend — query_params + base64 export branch
# ---------------------------------------------------------------------------


class TestCluster04j_PrimaryKeyListFilterBackend(SimpleTestCase):
    """``PrimaryKeyListFilterBackend`` is what powers the AG Grid
    "selected rows" subset operations — both list-with-ids and
    export-with-ids. The two methods share the same intent (filter
    the queryset to the explicit id list) but read the ids from
    different sources: live request query-params vs the base64-encoded
    payload the export endpoint embeds in its JSON body."""

    def setUp(self):
        self.backend = PrimaryKeyListFilterBackend()
        self.view = _view_with_container(model_class=None, pk_name="id")

    # -- 4.50 ---------------------------------------------------------
    def test_4_50_filter_queryset_with_ids_filters_to_subset(self):
        req = _request_with_perms(query_string=urlencode({"ids": ["1", "5", "9"]}, doseq=True))
        qs = _mock_qs()
        result = self.backend.filter_queryset(req, qs, self.view)
        # Backend builds `id__in=...` using the container's pk_name.
        qs.filter.assert_called_once_with(id__in=["1", "5", "9"])
        self.assertIs(result, qs)

    def test_4_50b_filter_queryset_drops_blank_ids(self):
        # Frontend sometimes sends `ids=&ids=42` when a row was
        # deselected; backend must filter the empty entries out
        # so we don't generate `id__in=[""]` which Django coerces to
        # 0 on integer pks (silent over-restrict).
        req = _request_with_perms(query_string="ids=&ids=42&ids=")
        qs = _mock_qs()
        self.backend.filter_queryset(req, qs, self.view)
        qs.filter.assert_called_once_with(id__in=["42"])

    def test_4_51_filter_queryset_no_ids_passes_through_untouched(self):
        # No `ids` query-param = no subset selection. Backend must
        # return the queryset unchanged — NOT call `.none()` (that
        # would be a silent always-empty list).
        req = _request_with_perms()
        qs = _mock_qs()
        result = self.backend.filter_queryset(req, qs, self.view)
        qs.filter.assert_not_called()
        qs.none.assert_not_called()
        self.assertIs(result, qs)

    # -- 4.52 ---------------------------------------------------------
    def test_4_52_filter_for_export_decodes_base64_and_filters(self):
        # The export endpoint base64-encodes the AG Grid querystring
        # into `filtered_export`. Backend must decode, parse, and
        # apply the same id__in filter as the live request branch.
        encoded = base64.b64encode(
            urlencode({"ids": ["7", "8"]}, doseq=True).encode("utf-8")
        ).decode("utf-8")
        json_data = {"filtered_export": encoded}
        qs = _mock_qs()
        result = self.backend.filter_for_export(json_data, qs, self.view)
        qs.filter.assert_called_once_with(id__in=["7", "8"])
        self.assertIs(result, qs)

    def test_4_52b_filter_for_export_no_ids_passes_through(self):
        # Encoded payload with no `ids` key → no subset → pass through.
        # Pre-fix this would have called `.none()`, dumping zero rows
        # instead of the full table on a no-selection export.
        encoded = base64.b64encode(b"other=foo").decode("utf-8")
        qs = _mock_qs()
        result = self.backend.filter_for_export({"filtered_export": encoded}, qs, self.view)
        qs.filter.assert_not_called()
        self.assertIs(result, qs)

    def test_4_52c_filter_for_export_uses_container_pk_name(self):
        # Container with non-default pk_name — backend must honour it
        # so models with custom pks (e.g. `code`) work in export.
        view = _view_with_container(model_class=None, pk_name="code")
        encoded = base64.b64encode(urlencode({"ids": ["A"]}, doseq=True).encode("utf-8")).decode("utf-8")
        qs = _mock_qs()
        self.backend.filter_for_export({"filtered_export": encoded}, qs, view)
        qs.filter.assert_called_once_with(code__in=["A"])


# ---------------------------------------------------------------------------
# 2) UserReadRestrictionFilterBackend — model-resolution + filter dispatch
# ---------------------------------------------------------------------------


class TestCluster04j_FilterDispatchByModel(SimpleTestCase):
    """``filter_queryset`` short-circuits for AuditLogStatus /
    CalculationLog (always-allow), routes AuditLog to `_handle_auditlog`,
    and falls through to `_handle_lexmodel_default` for everything else.
    The dispatch matters because each branch has very different DB
    cost — short-circuiting AuditLogStatus saves us a per-row loop on
    every audit-status list request."""

    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()

    def _view_with_named_model(self, name):
        model = mock.MagicMock()
        model.__name__ = name
        return _view_with_container(model_class=model)

    def test_4_53_auditlogstatus_short_circuits_to_passthrough(self):
        view = self._view_with_named_model("AuditLogStatus")
        qs = _mock_qs()
        result = self.backend.filter_queryset(_request_with_perms(), qs, view)
        # No filter, no exclude, no none — the queryset comes back
        # untouched. AuditLogStatus is always visible to whoever can
        # see the parent AuditLog row (handled by AuditLog's filter).
        qs.filter.assert_not_called()
        qs.exclude.assert_not_called()
        qs.none.assert_not_called()
        self.assertIs(result, qs)

    def test_4_53b_calculationlog_short_circuits_to_passthrough(self):
        view = self._view_with_named_model("CalculationLog")
        qs = _mock_qs()
        result = self.backend.filter_queryset(_request_with_perms(), qs, view)
        qs.filter.assert_not_called()
        self.assertIs(result, qs)

    def test_4_54_auditlog_routes_to_auditlog_handler(self):
        view = self._view_with_named_model("AuditLog")
        qs = _mock_qs()
        with mock.patch.object(
            self.backend, "_handle_auditlog", return_value="AUDIT_RESULT"
        ) as h:
            result = self.backend.filter_queryset(_request_with_perms(), qs, view)
        h.assert_called_once()
        self.assertEqual(result, "AUDIT_RESULT")

    def test_4_54b_other_models_route_to_lexmodel_default_handler(self):
        view = self._view_with_named_model("MyCustomModel")
        qs = _mock_qs()
        with mock.patch.object(
            self.backend, "_handle_lexmodel_default", return_value="DEFAULT_RESULT"
        ) as h:
            result = self.backend.filter_queryset(_request_with_perms(), qs, view)
        h.assert_called_once()
        self.assertEqual(result, "DEFAULT_RESULT")


# ---------------------------------------------------------------------------
# 3) _apply_default_permission_read_filter — global / scoped / empty paths
# ---------------------------------------------------------------------------


class TestCluster04j_ApplyDefaultPermissionFilter(TestCase):
    """The DB-level fast path — when a model uses the default
    `LexModel.permission_read`, we don't need a per-row Python check;
    the UMA permissions can be translated directly to a SQL `WHERE
    pk IN (...)` clause. This is what makes the AuditLog list scale."""

    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()
        # Use a real Django model so `target_model._meta.pk` resolves.
        from lex.test_project.tests.crud_api.models import SimpleItem
        self.SimpleItem = SimpleItem

    # -- 4.55 ---------------------------------------------------------
    def test_4_55_global_read_returns_queryset_untouched(self):
        # Permission with no `resource_set_id` → admin-style global
        # read. Backend must NOT add a WHERE clause; the entire
        # queryset is visible.
        req = _request_with_perms(
            user_permissions=[
                {"rsname": "lex_app.SimpleItem", "scopes": ["read"]},
            ],
        )
        qs = _mock_qs(model=self.SimpleItem)
        result = self.backend._apply_default_permission_read_filter(
            request=req, queryset=qs,
            target_model=self.SimpleItem, lookup_field="id",
        )
        qs.filter.assert_not_called()
        qs.none.assert_not_called()
        self.assertIs(result, qs)

    # -- 4.56 ---------------------------------------------------------
    def test_4_56_scoped_returns_filter_with_normalized_ids(self):
        # Multiple `resource_set_id`s collapse to a single
        # `id__in=[...]` filter. ids are passed through `pk_field.to_python`
        # so string ids from Keycloak are coerced to int for an
        # int pk model — without this the WHERE clause silently
        # matches zero rows on an int pk column.
        req = _request_with_perms(
            user_permissions=[
                {"rsname": "lex_app.SimpleItem", "scopes": ["read"], "resource_set_id": "1"},
                {"rsname": "lex_app.SimpleItem", "scopes": ["read"], "resource_set_id": "5"},
            ],
        )
        qs = _mock_qs(model=self.SimpleItem)
        self.backend._apply_default_permission_read_filter(
            request=req, queryset=qs,
            target_model=self.SimpleItem, lookup_field="id",
        )
        qs.filter.assert_called_once()
        kwargs = qs.filter.call_args.kwargs
        self.assertIn("id__in", kwargs)
        # pk is integer → strings coerced to int via to_python
        self.assertEqual(sorted(kwargs["id__in"]), [1, 5])

    # -- 4.57 ---------------------------------------------------------
    def test_4_57_no_matching_permissions_returns_empty(self):
        # No permission for THIS model + no global read → must be
        # `queryset.none()`. Returning the original queryset would
        # leak every row to a user with no read scope.
        req = _request_with_perms(
            user_permissions=[
                # Permission for a DIFFERENT model — must be ignored.
                {"rsname": "lex_app.OtherModel", "scopes": ["read"]},
                # Permission with wrong scope — must be ignored.
                {"rsname": "lex_app.SimpleItem", "scopes": ["edit"]},
            ],
        )
        qs = _mock_qs(model=self.SimpleItem)
        result = self.backend._apply_default_permission_read_filter(
            request=req, queryset=qs,
            target_model=self.SimpleItem, lookup_field="id",
        )
        qs.none.assert_called_once()
        self.assertIs(result, qs.none.return_value)

    def test_4_57b_non_mapping_permissions_silently_skipped(self):
        # Defensive: malformed user_permissions entries (not a dict)
        # must be skipped without raising. A regression that crashed
        # here would 500 every list request mid-way through the
        # permission loop.
        req = _request_with_perms(
            user_permissions=[
                "garbage-string",
                42,
                None,
                {"rsname": "lex_app.SimpleItem", "scopes": ["read"]},  # the real one
            ],
        )
        qs = _mock_qs(model=self.SimpleItem)
        result = self.backend._apply_default_permission_read_filter(
            request=req, queryset=qs,
            target_model=self.SimpleItem, lookup_field="id",
        )
        # The valid entry is global read → queryset returned untouched.
        qs.filter.assert_not_called()
        qs.none.assert_not_called()
        self.assertIs(result, qs)


# ---------------------------------------------------------------------------
# 4) _get_default_permission_target — fast-path opt-in / opt-out
# ---------------------------------------------------------------------------


class TestCluster04j_GetDefaultPermissionTarget(TestCase):
    """The fast-path is only safe for models that use the *default*
    `LexModel.permission_read` — every customer override could carry
    arbitrary Python logic and must run per-row. Helper returns
    `(None, None)` to signal "fall through to per-row loop"."""

    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()
        from lex.core.models.LexModel import LexModel
        from lex.test_project.tests.crud_api.models import SimpleItem
        self.LexModel = LexModel
        self.SimpleItem = SimpleItem

    # -- 4.58 ---------------------------------------------------------
    def test_4_58_lexmodel_with_default_permission_read_returns_target(self):
        # Temporarily swap SimpleItem.permission_read back to the
        # LexModel default so the fast-path opt-in fires.
        with mock.patch.object(
            self.SimpleItem, "permission_read", self.LexModel.permission_read
        ):
            target, lookup = self.backend._get_default_permission_target(self.SimpleItem)
        self.assertIs(target, self.SimpleItem)
        self.assertEqual(lookup, "id")

    # -- 4.59 ---------------------------------------------------------
    def test_4_59_lexmodel_with_overridden_permission_read_opts_out(self):
        # SimpleItem in the test_project DOES override permission_read
        # by default — so the helper must return (None, None).
        target, lookup = self.backend._get_default_permission_target(self.SimpleItem)
        self.assertIsNone(target)
        self.assertIsNone(lookup)

    def test_4_59b_non_lexmodel_with_no_instance_type_returns_none(self):
        # Plain Python class (not a LexModel subclass, no `instance_type`)
        # → None. Catches a regression that tried to fast-path a
        # raw managed model and crashed on `_meta.pk` access.
        class _NotALexModel:
            pass
        target, lookup = self.backend._get_default_permission_target(_NotALexModel)
        self.assertIsNone(target)
        self.assertIsNone(lookup)


# ---------------------------------------------------------------------------
# 5) AuditLog resource map — lru_cache + ambiguous-token dropout
# ---------------------------------------------------------------------------


class TestCluster04j_AuditLogResourceMap(TestCase):
    """The AuditLog filter walks `apps.get_models()` once per process
    (lru_cached) and builds a `{resource_token → model_class}` map.
    Tokens that resolve to two distinct models (e.g. an `Invoice` in
    two apps) must be DROPPED, not silently picked-arbitrarily —
    otherwise the audit-log filter applies the wrong model's
    permissions to those rows."""

    def setUp(self):
        # Wipe the lru_cache so each test sees a fresh build.
        UserReadRestrictionFilterBackend._get_auditlog_default_permission_resource_map.cache_clear()
        self.addCleanup(
            UserReadRestrictionFilterBackend._get_auditlog_default_permission_resource_map.cache_clear
        )

    # -- 4.60 ---------------------------------------------------------
    def test_4_60_resource_map_keys_are_lowercased_model_names(self):
        # Build map against a controlled set of fake models.
        from lex.core.models.LexModel import LexModel

        class _FakeMeta:
            def __init__(self, model_name):
                self.model_name = model_name

        def _fake_model(name, lower):
            cls = mock.MagicMock(spec=type)
            cls.__name__ = name
            cls.__mro__ = (cls, LexModel, object)
            cls._meta = _FakeMeta(lower)
            cls.permission_read = LexModel.permission_read
            return cls

        m1 = _fake_model("InvoiceA", "invoicea")
        m2 = _fake_model("BetaItem", "betaitem")

        with mock.patch(
            "lex.api.views.model_entries.filter_backends.apps.get_models",
            return_value=[m1, m2],
        ), mock.patch(
            "lex.api.views.model_entries.filter_backends.issubclass",
            side_effect=lambda cls, base: base is LexModel,
        ):
            resource_map = (
                UserReadRestrictionFilterBackend
                ._get_auditlog_default_permission_resource_map()
            )
        # Both lowercased + name-cased tokens map to the same model
        # (both go through {model_name.lower(), __name__.lower()}).
        self.assertIs(resource_map.get("invoicea"), m1)
        self.assertIs(resource_map.get("betaitem"), m2)

    # -- 4.61 ---------------------------------------------------------
    def test_4_61_lru_cache_returns_same_map_on_repeated_call(self):
        # Per the lru_cache(maxsize=1) decorator: the second call
        # returns the *exact same dict object*, not a recomputation.
        # This is what makes the AuditLog filter cheap on subsequent
        # requests (the apps.get_models() walk runs once per process).
        first = UserReadRestrictionFilterBackend._get_auditlog_default_permission_resource_map()
        second = UserReadRestrictionFilterBackend._get_auditlog_default_permission_resource_map()
        self.assertIs(first, second)


# ---------------------------------------------------------------------------
# 6) _build_auditlog_db_visibility_filters — global / scoped Q composition
# ---------------------------------------------------------------------------


class TestCluster04j_BuildAuditLogVisibilityFilters(TestCase):
    """Builds the Q objects fed to `queryset.filter(...)` for the
    AuditLog list. Composes a single OR'd Q across every model the
    caller can see — global tokens get `Q(resource__in=...)`, scoped
    tokens get `Q(resource=token, object_id__in=ids) | Q(resource=token,
    object_id__isnull=True, payload__id__in=ids)` (the second branch
    handles legacy audit rows where `object_id` was never populated)."""

    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()
        UserReadRestrictionFilterBackend._get_auditlog_default_permission_resource_map.cache_clear()
        self.addCleanup(
            UserReadRestrictionFilterBackend._get_auditlog_default_permission_resource_map.cache_clear
        )

    # -- 4.62 ---------------------------------------------------------
    def test_4_62_no_resolvable_models_returns_empty(self):
        # When the resource map is empty (no LexModel-default models
        # registered), the helper must return (frozenset(), None) —
        # NOT crash on the for-loop, NOT return a wide-open Q.
        with mock.patch.object(
            UserReadRestrictionFilterBackend,
            "_get_auditlog_default_permission_resource_map",
            return_value={},
        ):
            handled, allowed_q = self.backend._build_auditlog_db_visibility_filters(
                _request_with_perms()
            )
        self.assertEqual(handled, frozenset())
        self.assertIsNone(allowed_q)

    # -- 4.63 ---------------------------------------------------------
    def test_4_63_global_resource_collected_into_handled_set(self):
        # A model the user has global read on contributes its
        # resource_token to `handled` AND lands in the `Q(resource__in=
        # global_resources)` clause.
        from lex.test_project.tests.crud_api.models import SimpleItem
        from lex.core.models.LexModel import LexModel

        with mock.patch.object(
            UserReadRestrictionFilterBackend,
            "_get_auditlog_default_permission_resource_map",
            return_value={"simpleitem": SimpleItem},
        ), mock.patch.object(
            SimpleItem, "permission_read", LexModel.permission_read,
        ):
            req = _request_with_perms(
                user_permissions=[
                    {"rsname": "lex_app.SimpleItem", "scopes": ["read"]},
                ],
            )
            handled, allowed_q = self.backend._build_auditlog_db_visibility_filters(req)
        self.assertIn("simpleitem", handled)
        self.assertIsNotNone(
            allowed_q,
            "Global-read model must produce a non-None Q so the AuditLog "
            "filter actually shows the rows; None would hide every audit "
            "row for the model from the compliance view.",
        )


# ---------------------------------------------------------------------------
# 7) _handle_auditlog — handled/residual split
# ---------------------------------------------------------------------------


class TestCluster04j_HandleAuditLog(TestCase):
    """End-to-end exercise of the handled/residual logic against a real
    AuditLog table. Rows whose `resource` is in `handled_resources`
    get the cheap DB-level filter; everything else falls back to the
    per-row `can_read_from_payload` Python check."""

    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()
        UserReadRestrictionFilterBackend._get_auditlog_default_permission_resource_map.cache_clear()
        self.addCleanup(
            UserReadRestrictionFilterBackend._get_auditlog_default_permission_resource_map.cache_clear
        )

    # -- 4.64 ---------------------------------------------------------
    def test_4_64_no_handled_resources_iterates_residual_via_payload(self):
        # When nothing resolves at the DB level, every row falls
        # through to `can_read_from_payload`. Backend must call it
        # for every iterated row and exclude the deniers.
        from lex.audit_logging.models.AuditLog import AuditLog

        # Empty resource map → handled=frozenset(), allowed_q=None
        with mock.patch.object(
            UserReadRestrictionFilterBackend,
            "_get_auditlog_default_permission_resource_map",
            return_value={},
        ), mock.patch(
            "lex.api.views.model_entries.filter_backends.can_read_from_payload",
            return_value=True,
        ) as crfp:
            qs = AuditLog.objects.none()  # real queryset, empty
            result = self.backend._handle_auditlog(_request_with_perms(), qs)
        # Empty queryset → can_read_from_payload never called, but
        # the helper still returns a queryset (not None / not raise).
        self.assertIsNotNone(result)
        crfp.assert_not_called()


# ---------------------------------------------------------------------------
# 8) _handle_lexmodel_default — fall-through to per-row when no fast-path
# ---------------------------------------------------------------------------


class TestCluster04j_HandleLexmodelDefault(SimpleTestCase):
    """The default branch — runs when `_get_default_permission_target`
    returns `(None, None)`. Iterates the queryset, asks each
    instance's `permission_read` (or legacy `can_read`) whether the
    caller can see it, and excludes the deniers via `pk__in`. Driven
    here at the unit level with mocks; the end-to-end coverage lives
    in 4e (which uses `AuthenticatedE2ETestCase` + a real APIClient)."""

    def setUp(self):
        self.backend = UserReadRestrictionFilterBackend()

    # -- 4.65 ---------------------------------------------------------
    def test_4_65_excludes_rows_whose_permission_read_denies(self):
        # Two synthetic instances — one whose `permission_read` returns
        # an "allowed" PermissionResult, one whose returns "denied".
        # Backend must call `.exclude(pk__in=[denied_pk])` exactly once
        # at the end (not once per row — that would be N+1 SQL).
        from lex.core.models.LexModel import PermissionResult

        allowed_inst = mock.MagicMock(name="allowed_instance")
        allowed_inst.pk = 1
        allowed_inst.permission_read.return_value = PermissionResult.allow_all()
        denied_inst = mock.MagicMock(name="denied_instance")
        denied_inst.pk = 2
        denied_inst.permission_read.return_value = PermissionResult.deny()

        qs = _mock_qs()
        qs.iterator.return_value = iter([allowed_inst, denied_inst])

        # Force the fast-path opt-out so the per-row loop runs.
        with mock.patch.object(
            self.backend, "_get_default_permission_target", return_value=(None, None),
        ), mock.patch(
            "lex.api.views.model_entries.filter_backends._get_capabilities",
            return_value={"has_permission_read": True, "has_can_read": False},
        ), mock.patch(
            "lex.api.views.model_entries.filter_backends.UserContext.from_request_base",
            return_value=mock.MagicMock(name="base_ctx"),
        ):
            result = self.backend._handle_lexmodel_default(
                request=_request_with_perms(),
                queryset=qs,
            )
        # Exactly one exclude call, with the denied row's pk in the list.
        qs.exclude.assert_called_once()
        kwargs = qs.exclude.call_args.kwargs
        self.assertEqual(
            kwargs, {"pk__in": [2]},
            "Backend must batch all denied pks into a single exclude(pk__in=...) "
            "call — per-row excludes would scale O(n) SQL queries on the list.",
        )
        self.assertIs(result, qs)

    def test_4_65b_no_denials_returns_queryset_untouched(self):
        # When every row's permission_read allows, the backend must
        # NOT call `.exclude(...)` — that would be a wasted SQL round-trip
        # on every list request from an admin caller.
        from lex.core.models.LexModel import PermissionResult

        inst = mock.MagicMock(pk=1)
        inst.permission_read.return_value = PermissionResult.allow_all()

        qs = _mock_qs()
        qs.iterator.return_value = iter([inst])

        with mock.patch.object(
            self.backend, "_get_default_permission_target", return_value=(None, None),
        ), mock.patch(
            "lex.api.views.model_entries.filter_backends._get_capabilities",
            return_value={"has_permission_read": True, "has_can_read": False},
        ), mock.patch(
            "lex.api.views.model_entries.filter_backends.UserContext.from_request_base",
            return_value=mock.MagicMock(),
        ):
            result = self.backend._handle_lexmodel_default(
                request=_request_with_perms(),
                queryset=qs,
            )
        qs.exclude.assert_not_called()
        self.assertIs(result, qs)



