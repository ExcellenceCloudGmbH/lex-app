"""
Cluster 4e: Read-restriction filter backend.

Targets ``lex.api.views.model_entries.filter_backends.UserReadRestrictionFilterBackend`` —
the single most-executed permission enforcement point in the framework
(every List / Export / History request passes through it). Baseline
coverage before this sub-cluster: **28.97%**.

Intent (from docs/features/permissions/ + docs/features/api-layer/):

    * The filter backend is the *one place* where row-level read
      restrictions are enforced on list-style endpoints.
    * For models using the default ``LexModel.permission_read``, the
      fast path scans ``request.user_permissions`` and translates it
      into a single DB filter — no per-row Python check.
    * For models with a custom ``permission_read``, each row is
      evaluated via ``permission_read(UserContext)`` and denied rows
      are excluded from the response.
    * The ``pk_only=true`` fast path (bulk-selection) must honour the
      same filter — returning the ids of *allowed* rows only.
    * A profile with zero read scope must produce an empty result
      without any extraneous SQL.

Scenario numbering matches
docs/test-plan/test-clusters.md § Planned Expansions → 4e.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.tests.e2e._authenticated_e2e_test_case import AuthenticatedE2ETestCase

from .models import (
    ALL_MODELS,
    FILTER_BACKEND,
    FilterBackendItem,
)


def _seed_mixed_rows() -> None:
    """Create four rows: two public, two secret."""
    FilterBackendItem.objects.create(name="public-1", is_secret=False)
    FilterBackendItem.objects.create(name="public-2", is_secret=False)
    FilterBackendItem.objects.create(name="secret-1", is_secret=True)
    FilterBackendItem.objects.create(name="secret-2", is_secret=True)


def _rows(resp_data):
    """Normalize list-endpoint shape — paginated dict vs raw list."""
    if isinstance(resp_data, dict) and "results" in resp_data:
        return resp_data["results"]
    return resp_data


class TestCluster04e_FilterBackend_RegularUser(AuthenticatedE2ETestCase):
    """
    Regular user (no admin group) — ``permission_read`` denies secret
    rows row-by-row. Exercises ``_handle_lexmodel_default``'s
    ``queryset.iterator()`` + ``excluded.append`` path.
    """

    e2e_models = ALL_MODELS
    as_superuser = False

    # -- 4.13 ----------------------------------------------------------
    def test_4_13_per_row_visibility_filters_denied_rows(self) -> None:
        """
        Scenario 4.13: List returns only rows whose ``permission_read``
        allows the caller. The mixed page must contain the public rows
        and nothing else.
        """
        _seed_mixed_rows()

        resp = self.client.get(self.url_list(FILTER_BACKEND))

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            msg=f"List must return 200; got {resp.status_code}",
        )
        names = sorted(row["name"] for row in _rows(resp.data))
        self.assertEqual(
            names, ["public-1", "public-2"],
            msg=(
                "Only public rows may be visible to a non-admin. "
                f"Got: {names!r} (expected the two public rows and zero "
                "secret rows)"
            ),
        )

    # -- 4.16 ----------------------------------------------------------
    def test_4_16_pk_only_fast_path_respects_permissions(self) -> None:
        """
        Scenario 4.16: ``?pk_only=true`` (bulk-selection fast path)
        must return only the pks of rows the caller is allowed to read.
        If this is wrong, "select all" in the frontend silently
        selects denied rows and the next bulk action operates on data
        the user can't see.
        """
        _seed_mixed_rows()
        allowed = list(
            FilterBackendItem.objects
            .filter(is_secret=False)
            .values_list("pk", flat=True)
        )

        resp = self.client.get(self.url_list(FILTER_BACKEND), {"pk_only": "true"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("ids", resp.data, msg="pk_only response must carry 'ids'")
        self.assertEqual(
            sorted(resp.data["ids"]), sorted(allowed),
            msg=(
                "pk_only must not leak the pks of denied rows. "
                f"Expected {sorted(allowed)!r}, got {sorted(resp.data['ids'])!r}"
            ),
        )
        self.assertEqual(
            resp.data["count"], len(allowed),
            msg="pk_only 'count' must match the allowed-id list length",
        )


class TestCluster04e_FilterBackend_Admin(AuthenticatedE2ETestCase):
    """Admin-group user — ``permission_read`` returns ``allow_all``."""

    e2e_models = ALL_MODELS
    as_superuser = False
    extra_groups = frozenset({"admin"})

    # -- 4.17 ----------------------------------------------------------
    def test_4_17_allow_all_profile_returns_every_row(self) -> None:
        """
        Scenario 4.17: When ``permission_read`` returns ``allow_all``
        for every row, the filter backend must not exclude anything.
        Exercises the ``not excluded`` branch (``return queryset``).
        """
        _seed_mixed_rows()

        resp = self.client.get(self.url_list(FILTER_BACKEND))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = _rows(resp.data)
        self.assertEqual(
            len(rows), 4,
            msg=(
                f"Admin with allow_all must see every row. Got {len(rows)}; "
                f"rows={rows!r}"
            ),
        )


class TestCluster04e_FilterBackend_DenyAll(AuthenticatedE2ETestCase):
    """Deny-all profile — ``permission_read`` returns ``deny`` on every row."""

    e2e_models = ALL_MODELS
    as_superuser = False
    extra_groups = frozenset({"deny_all"})

    # -- 4.18 ----------------------------------------------------------
    def test_4_18_deny_all_short_circuits_to_empty(self) -> None:
        """
        Scenario 4.18: A profile whose ``permission_read`` denies every
        row must produce an empty list response. Every seeded row is
        visible in the DB; the API surface must expose zero of them.
        """
        _seed_mixed_rows()

        resp = self.client.get(self.url_list(FILTER_BACKEND))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = _rows(resp.data)
        self.assertEqual(
            len(rows), 0,
            msg=(
                "A deny-everyone profile must return zero rows even "
                f"though {FilterBackendItem.objects.count()} exist in "
                f"the DB. Got rows={rows!r}"
            ),
        )

    # -- 4.41 ----------------------------------------------------------
    def test_4_41_read_deny_detail_response_leaks_no_domain_fields(self) -> None:
        """
        Scenario 4.41: A detail GET for a row whose
        ``permission_read`` returns :meth:`PermissionResult.deny` must
        not leak any domain fields.

        List endpoints already drop denied rows through
        :class:`UserReadRestrictionFilterBackend` (4.18). Detail GETs
        do not run that list filter, so the final guard is
        ``LexSerializer.to_representation`` returning an empty object.
        This pins the customer-visible contract: even if the URL is
        guessed, a fully read-denied row exposes no ``name`` / secret
        data and no framework-injected ``id``.
        """
        item = FilterBackendItem.objects.create(name="hidden-detail", is_secret=False)

        resp = self.client.get(self.url_detail(FILTER_BACKEND, item.pk))

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            msg=f"Detail endpoint currently represents read-deny as 200 + {{}}; got {resp.status_code}",
        )
        data = getattr(resp, "data", None)
        self.assertEqual(
            data, {},
            msg=(
                "A full read deny must serialize as an empty object on detail GET. "
                "Leaking even the primary key would let a guessed URL confirm row existence; "
                f"got {data!r}."
            ),
        )


class TestCluster04e_FilterBackend_AuditLog(AuthenticatedE2ETestCase):
    """
    AuditLog visibility — the DB-filter + residual-permission path
    (``_build_auditlog_db_visibility_filters`` / ``_handle_auditlog``).

    These two scenarios depend on the audit-logging fixture and a
    seeded ``AuditLog`` table alongside a ``user_permissions`` payload
    shaped like what the Keycloak middleware attaches. Deferred to a
    follow-up pass so we don't couple Cluster 4 to the Cluster 6
    fixture plumbing.
    """

    e2e_models = ALL_MODELS

    # -- 4.14 ----------------------------------------------------------
    @unittest.skip(
        "4.14: AuditLog DB visibility filter — needs Keycloak "
        "user_permissions + seeded AuditLog rows. Deferred.",
    )
    def test_4_14_auditlog_resource_filter_db_path(self) -> None:  # pragma: no cover
        pass

    # -- 4.15 ----------------------------------------------------------
    @unittest.skip(
        "4.15: AuditLog deferred-permission path — needs mixed handled/"
        "residual resources and can_read_from_payload fixture. Deferred.",
    )
    def test_4_15_auditlog_deferred_permission_residual_path(self) -> None:  # pragma: no cover
        pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()




