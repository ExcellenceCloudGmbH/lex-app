"""
Cluster 12a (continued): History-row serialization & MetaHistory scope
immutability.

Scenarios in this module extend the 12a read-contract family with the
three history / scope-shape cases called out in
``docs/test-plan/test-clusters.md#12a-read-contract``:

* 12.6 — a history-row snapshot's field visibility matches the main
  model's ``permission_read`` (i.e. the serializer unwraps to the
  main model's permission predicate before filtering fields).
* 12.7 — a MetaHistorical instance's ``lex_reserved_scopes`` is fixed
  at ``{"edit": [], "delete": False, "export": False}`` regardless of
  caller. This locks in the immutable audit-surface contract.
* 12.8 — ``lex_reserved_scopes.edit`` reflects ``permission_edit``
  ``allow_fields({"x"})`` exactly — no more, no less.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import (
    ALL_MODELS,
    EDIT_SCOPED,
    PROTECTED_WIDE,
    EditScopedItem,
    ProtectedWideItem,
)

import pytest

pytestmark = pytest.mark.serializers


class TestCluster12a_HistoryMetaScopes(E2ETestCase):
    """History-snapshot & MetaHistory / edit-scope shape."""

    e2e_models = ALL_MODELS

    # -- 12.6 ----------------------------------------------------------
    def test_12_6_history_row_respects_main_model_permission_read(self) -> None:
        """Scenario 12.6: field visibility on a history-row snapshot
        matches the main model's ``permission_read``.

        ``ProtectedWideItem`` restricts non-admins to ``{id, name,
        amount}``. When we GET the record's history, each entry's
        ``snapshot`` must carry exactly those data fields (plus the
        framework-managed keys) — not the full field set just because
        the row is stored in a historical table.
        """
        item = ProtectedWideItem.objects.create(
            name="history-12-6",
            amount="12.3400",
            secret_note="this must not leak via history",
            secret_category="beta",
        )
        # Mutate so there's a meaningful history trail.
        item.name = "history-12-6-renamed"
        item.save()

        resp = self.client.get(self.url_history(PROTECTED_WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        entries = resp.data
        self.assertGreaterEqual(
            len(entries), 1,
            "History endpoint returned no entries for a record that was "
            "created and updated",
        )

        for idx, entry in enumerate(entries):
            snapshot = entry.get("snapshot") or {}
            self.assertIsInstance(
                snapshot, dict,
                f"Entry {idx} snapshot must be a dict; got {type(snapshot).__name__}",
            )
            # Fields the main model's permission_read DENIES must not
            # appear in the serialized snapshot — otherwise history is
            # a permission-bypass surface.
            for leaked in ("secret_note", "secret_category"):
                self.assertNotIn(
                    leaked, snapshot,
                    f"History entry {idx}: restricted field {leaked!r} "
                    f"leaked through history serialization. "
                    f"Snapshot keys: {sorted(snapshot.keys())}",
                )
            # Allowed data fields must be present.
            self.assertIn(
                "name", snapshot,
                f"Entry {idx}: allowed field 'name' missing from snapshot",
            )

    # -- 12.7 ----------------------------------------------------------
    def test_12_7_meta_historical_scopes_are_immutable(self) -> None:
        """Scenario 12.7: a MetaHistorical instance's
        ``lex_reserved_scopes`` is ``{"edit": [], "delete": False,
        "export": False}`` — regardless of caller.

        Asserted by invoking the LexSerializer's
        ``get_lex_reserved_scopes`` directly on a MetaHistorical
        instance; that method has an explicit early-return that fixes
        the scope shape for every subclass whose class name starts with
        ``MetaHistorical``.
        """
        item = ProtectedWideItem.objects.create(
            name="meta-scope-subject", amount="1.0000",
        )
        # Force at least one history + meta-history row to exist.
        item.name = "meta-scope-subject-v2"
        item.save()

        history_cls = ProtectedWideItem.history.model
        # Find the MetaHistorical class. ``meta_history`` is dynamically
        # registered by the framework (not a declared field on the
        # history model), so we locate its class by walking the app
        # registry for ``MetaHistorical*`` subclasses whose history FK
        # points back at our history class.
        from django.apps import apps as _apps

        meta_cls = None
        for m in _apps.get_models():
            if (
                m.__name__.startswith("MetaHistorical")
                and history_cls.__name__ in m.__name__
            ):
                meta_cls = m
                break
        self.assertIsNotNone(
            meta_cls,
            "Could not locate MetaHistorical class for "
            f"{history_cls.__name__} — simple_history registration "
            "may have changed.",
        )
        meta_row = meta_cls.objects.first()
        self.assertIsNotNone(
            meta_row,
            "Fixture did not produce a MetaHistorical row — cannot "
            "verify the immutable-scopes contract",
        )
        self.assertTrue(
            meta_row.__class__.__name__.startswith("MetaHistorical"),
            f"Expected MetaHistorical-prefixed class name; got "
            f"{meta_row.__class__.__name__!r}",
        )

        # Build a LexSerializer and invoke the scope computation.
        from lex.api.serializers.base_serializers import LexSerializer

        class _Stub(LexSerializer):
            class Meta:
                model = meta_cls
                fields = []

        # Make a fake request with the e2e_user session so
        # get_lex_reserved_scopes reaches its MetaHistorical branch.
        from rest_framework.test import APIRequestFactory

        rf = APIRequestFactory()
        request = rf.get("/")
        request.user = self.user

        serializer = _Stub(context={"request": request})
        scopes = serializer.get_lex_reserved_scopes(meta_row)

        self.assertEqual(
            scopes,
            {"edit": [], "delete": False, "export": False},
            f"MetaHistorical scopes must be immutable — got {scopes!r}. "
            f"Any deviation is a permission-bypass risk on the audit surface.",
        )

    # -- 12.8 ----------------------------------------------------------
    def test_12_8_lex_reserved_scopes_edit_reflects_permission_edit(self) -> None:
        """Scenario 12.8: when ``permission_edit`` returns
        ``allow_fields({"x"})``, ``lex_reserved_scopes.edit == ["x"]``
        — no more, no less.
        """
        item = EditScopedItem.objects.create(x="xv", y="yv", z="zv")

        resp = self.client.get(self.url_detail(EDIT_SCOPED, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        scopes = resp.data["lex_reserved_scopes"]
        self.assertEqual(
            scopes["edit"], ["x"],
            f"Expected edit == ['x'] (mirror of permission_edit "
            f"allow_fields({{'x'}})); got {scopes['edit']!r}. "
            f"A wider set leaks an editable surface the user does not have.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

