"""Cluster 5m — edit-time correctness and ``as_of`` time-travel round trip.

Intent: customers rely on ``?as_of`` to reconstruct what a record looked like
before an edit, and on ``edited_at`` to know when the edit happened. Those two
contracts must agree to the microsecond and survive the timezone chain end to
end: the edit stamps naive UTC (``lex_datetime_now``), the API serializes it
with an explicit ``Z`` (BUG-025 fix), ``parse_as_of_datetime`` reads ``Z``
back to naive UTC, and ``get_queryset_as_of`` compares it against the
``sys_from``/``valid_from`` windows stamped by the same clock. A shift
anywhere in that chain (the BUG-025 class of failure) silently time-travels to
the wrong snapshot — worse than an error, it returns plausible wrong data.
Customer concern 2026-07-14: "when we edit something the time is correct...
we rely on the as_of mechanism".
Cluster 5m — scenarios 5.98–5.101. Type: E.
Covers: lex/api/utils/temporal.py (parse_as_of_datetime),
        lex/core/services/Bitemporal.py (get_queryset_as_of),
        lex/api/views/model_entries/History.py (?as_of branch),
        lex/core/models/LexModel.py (lex_datetime_now / edited_at).
Run: python -m lex pytest lex/test_project/tests/history/test_5m_asof_edit_time.py -v
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone as dt_timezone

import pytest
from rest_framework import status

from lex.core.models.LexModel import lex_datetime_now
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HIST_SIMPLE, HistSimpleItem

pytestmark = pytest.mark.history


def _parse_serialized_utc(rendered: str) -> datetime:
    """Parse an API datetime string back to the naive-UTC storage clock."""
    parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    return parsed.astimezone(dt_timezone.utc).replace(tzinfo=None)


class TestCluster05m_AsOfEditTime(E2ETestCase):
    """Cluster 5m: edited_at is the true edit instant; as_of honors it."""

    e2e_models = ALL_MODELS

    def _create_and_edit(self):
        """Create v1, edit to v2; return (item, t_before_edit)."""
        item = HistSimpleItem.objects.create(name="v1")
        # Real wall-clock gaps so the as_of anchor falls strictly between
        # the two versions' windows regardless of DB timestamp precision.
        _time.sleep(0.05)
        t_before_edit = lex_datetime_now()
        _time.sleep(0.05)
        item.name = "v2"
        item.save()
        return item, t_before_edit

    def _snapshots(self, pk, as_of: str):
        url = self.url_history(HIST_SIMPLE, pk) + f"?as_of={as_of}"
        resp = self.client.get(url)
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"as_of history GET failed: {resp.status_code}",
        )
        return resp.data

    def test_5_98_edited_at_is_the_true_edit_instant(self) -> None:
        """
        Scenario 5.98: edited_at lands inside the real edit window and its
        serialized form denotes the same instant.
        Given: a record edited between two lex_datetime_now() readings
        When: the row is reloaded and fetched through the API
        Then: stored edited_at is within the bounds, and parsing the
              serialized string (explicit 'Z') yields the stored value exactly
        """
        item = HistSimpleItem.objects.create(name="v1")
        before = lex_datetime_now()
        item.name = "v2"
        item.save()
        after = lex_datetime_now()

        item.refresh_from_db()
        self.assertTrue(
            before <= item.edited_at <= after,
            f"edited_at {item.edited_at} outside the actual edit window "
            f"[{before}, {after}] — the edit clock is wrong.",
        )

        resp = self.client.get(self.url_detail(HIST_SIMPLE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rendered = resp.data["edited_at"]
        self.assertEqual(
            _parse_serialized_utc(rendered), item.edited_at,
            f"Serialized edited_at {rendered!r} does not denote the stored "
            f"instant {item.edited_at} — a client computing as_of anchors "
            f"from it would time-travel to the wrong snapshot.",
        )

    def test_5_99_as_of_before_the_edit_returns_the_pre_edit_state(self) -> None:
        """
        Scenario 5.99: as_of strictly before the edit returns exactly the
        pre-edit snapshot with the pre-edit values.
        Given: v1 created, then edited to v2
        When: GET history?as_of=<between create and edit> (Z format)
        Then: exactly one snapshot, carrying name='v1', still open-ended
              (valid_to None — at that instant nothing had superseded it)
        """
        item, t_before_edit = self._create_and_edit()

        snaps = self._snapshots(item.pk, t_before_edit.isoformat() + "Z")
        self.assertEqual(
            len(snaps), 1,
            f"At a pre-edit instant the system knew exactly one version, "
            f"got {len(snaps)} snapshots.",
        )
        snapshot = snaps[0]["snapshot"]
        self.assertEqual(
            snapshot["name"], "v1",
            f"as_of(before edit) must return the pre-edit value, got "
            f"{snapshot['name']!r} — the time-travel window is shifted.",
        )
        self.assertIsNone(
            snaps[0]["valid_to"],
            "Before the edit, v1 was the open-ended current version.",
        )

    def test_5_100_as_of_now_knows_both_versions_latest_current(self) -> None:
        """
        Scenario 5.100: as_of at the present returns the full system
        knowledge — the superseded v1 (closed window) and the current v2.
        Given: v1 created, then edited to v2
        When: GET history?as_of=<now> (Z format)
        Then: two snapshots; the open-ended one carries v2, the closed one v1
        """
        item, _ = self._create_and_edit()

        snaps = self._snapshots(item.pk, lex_datetime_now().isoformat() + "Z")
        self.assertEqual(
            len(snaps), 2,
            f"After one edit the system knows two versions, got {len(snaps)}.",
        )
        by_open = {snap["valid_to"] is None: snap for snap in snaps}
        self.assertIn(True, by_open, "One snapshot must be the open-ended current version.")
        self.assertIn(False, by_open, "One snapshot must be the closed superseded version.")
        self.assertEqual(
            by_open[True]["snapshot"]["name"], "v2",
            "The open-ended snapshot must carry the post-edit value.",
        )
        self.assertEqual(
            by_open[False]["snapshot"]["name"], "v1",
            "The superseded snapshot must preserve the pre-edit value.",
        )

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-026: edited_at and the history windows come from separate "
        "clock reads — as_of anchored at edited_at still shows the pre-edit state",
    )
    def test_5_101_serialized_edited_at_is_a_valid_as_of_anchor(self) -> None:
        """
        Scenario 5.101: the frontend contract — anchoring as_of on the API's
        own serialized edited_at lands on the correct side of the edit.
        Given: v1 created, then edited to v2; the client reads the serialized
               edited_at of the CURRENT record (the edit instant, Z format)
        When: querying as_of at that anchor and strictly before it
        Then: at the anchor the current v2 is known; strictly before the
              edit window only v1 exists — no timezone shift anywhere in the
              serialize→parse→compare chain
        """
        item, t_before_edit = self._create_and_edit()

        rendered = self.client.get(self.url_detail(HIST_SIMPLE, item.pk)).data["edited_at"]

        # At the (inclusive) edit instant, the system already knows v2.
        at_edit = self._snapshots(item.pk, rendered)
        open_snaps = [s for s in at_edit if s["valid_to"] is None]
        self.assertTrue(
            open_snaps and open_snaps[0]["snapshot"]["name"] == "v2",
            f"as_of at the serialized edit instant must know the new value; "
            f"snapshots: {[(s['snapshot']['name'], s['valid_to']) for s in at_edit]}. "
            f"A mismatch here means the serialize→parse chain shifted the time.",
        )

        # Strictly before the edit, only v1 existed. (t_before_edit predates
        # the edit by a real wall-clock gap, immune to precision issues.)
        before_edit = self._snapshots(item.pk, t_before_edit.isoformat() + "Z")
        self.assertEqual(
            [s["snapshot"]["name"] for s in before_edit], ["v1"],
            "Strictly before the edit, the system must know only v1.",
        )
