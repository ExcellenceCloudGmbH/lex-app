"""
Cluster 5j: History snapshot completeness + ``history_user`` actor.

Intent (from docs/features/tracking/history.md +
docs/features/tracking/bitemporal history.md):

    Each Level-1 history row carries a *full snapshot* of every model
    field at that moment in time — not a diff. "If your model has 10
    fields, every history row has all 10."

    On API-driven saves, ``history_user`` is stamped to the
    authenticated user. ``history_change_reason`` defaults to ``None``
    unless explicitly set.

Scenario numbering matches docs/test-plan/test-clusters.md § 5j.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HIST_SIMPLE, HistSimpleItem

import pytest

pytestmark = pytest.mark.history


class TestCluster05j_SnapshotAndActor(E2ETestCase):
    """Snapshot completeness + ``history_user`` stamping."""

    e2e_models = ALL_MODELS

    # -- 5.75 ----------------------------------------------------------
    def test_5_75_history_row_carries_every_field_value(self) -> None:
        """
        Scenario 5.75: After update, the new history row carries every
        model field's value (full snapshot, not a diff). The prior
        ``+`` row carries the pre-update state for the same fields.
        """
        item = HistSimpleItem.objects.create(name="s5-75", value=1)
        item.name = "s5-75-edited"
        item.value = 99
        item.save()

        rows = list(item.history.order_by("history_id"))
        self.assertEqual(len(rows), 2, "Expected create + update = 2 rows")

        create_row, update_row = rows
        # Pre-update snapshot
        self.assertEqual(
            create_row.name, "s5-75",
            "Create-row snapshot must carry the pre-update name",
        )
        self.assertEqual(
            create_row.value, 1,
            "Create-row snapshot must carry the pre-update value",
        )
        # Post-update snapshot — every field, not just the changed one
        self.assertEqual(
            update_row.name, "s5-75-edited",
            "Update-row snapshot must carry every field's post-update "
            "value — name should be the new name",
        )
        self.assertEqual(
            update_row.value, 99,
            "Update-row snapshot must carry every field's post-update "
            "value — value should be the new value",
        )

    # -- 5.76 ----------------------------------------------------------
    def test_5_76_api_save_stamps_history_user(self) -> None:
        """
        Scenario 5.76: An API-driven save stamps ``history_user`` to
        the authenticated user; ``history_change_reason`` defaults to
        ``None``.
        """
        resp = self.client.post(
            self.url_create(HIST_SIMPLE),
            data={"name": "s5-76", "value": 1}, format="json",
        )
        self.assertIn(
            resp.status_code, (200, 201),
            "POST must succeed; got %d: %r"
            % (resp.status_code, getattr(resp, "data", resp.content)),
        )
        item = HistSimpleItem.objects.get(name="s5-76")
        history_row = item.history.first()
        self.assertIsNotNone(
            getattr(history_row, "history_user_id", None),
            "API-driven save must stamp history_user — got None. "
            "Without this, the History Tab cannot show 'who changed it'.",
        )
        self.assertIn(
            getattr(history_row, "history_change_reason", None), (None, ""),
            "history_change_reason must default to a falsy sentinel "
            "(None or '') — pin the default so a framework change is caught",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


