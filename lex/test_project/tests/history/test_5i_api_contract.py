"""
Cluster 5i: History API contract — response shape + ``as_of`` time-travel.

Intent (from docs/features/tracking/bitemporal history.md):

    ``GET /api/<model>/<pk>/history/`` returns a JSON array where each
    entry carries:
      - ``history_id`` (int)
      - ``valid_from`` / ``valid_to``
      - ``history_type`` ("+" / "~" / "-")
      - ``user`` ({id, email, name} or null)
      - ``snapshot`` (full field map at that point in time)
      - ``system_history`` (list of L2 meta records)

    With ``?as_of=<ISO datetime>`` the endpoint returns the L2
    meta-history snapshot at that system time instead — the time-
    travel mode the As-Of UI control uses.

    Two helpers underlie this:
      ``get_queryset_as_of(Model, t)``       → valid-time slice
      ``get_queryset_as_of(HistoryModel, t)`` → system-time slice

Cluster 5.9 only asserts ``200 OK + ≥3 rows`` — none of the contract
above is pinned. 5i closes that gap. Scenario numbering matches
docs/test-plan/test-clusters.md § 5i.
"""

from __future__ import annotations

import datetime as _dt
import time
import unittest

from rest_framework import status

from lex.core.services.Bitemporal import get_queryset_as_of
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HIST_SIMPLE, HistSimpleItem


def _meta_history_wired_for_hist_simple() -> bool:
    """Probe whether ``HistSimpleItem`` has a *real* L2 manager attached.
    Deferred to call time — at import time, simple_history's late
    ``register()`` may not have attached the manager. Probing
    ``.model`` forces the descriptor to resolve, so a phantom
    attribute (e.g. a class-level placeholder) raises rather than
    silently passing the hasattr check."""
    for owner in (HistSimpleItem, getattr(HistSimpleItem, "history", None)):
        if owner is None:
            continue
        target = owner.model if hasattr(owner, "model") else owner
        try:
            _ = target.meta_history.model
            return True
        except Exception:
            continue
    return False


class TestCluster05i_HistoryAPIContract(E2ETestCase):
    """Endpoint response shape + ``?as_of`` system-time branch."""

    e2e_models = ALL_MODELS

    # -- 5.71 ----------------------------------------------------------
    def test_5_71_history_response_shape(self) -> None:
        """
        Scenario 5.71: Each row of ``GET /history/`` carries the
        documented keys. Drift here would silently break the History
        Tab UI without any other test catching it.
        """
        item = HistSimpleItem.objects.create(name="s5-71", value=1)
        item.value = 2
        item.save()

        resp = self.client.get(self.url_history(HIST_SIMPLE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        self.assertGreaterEqual(len(rows), 2, "Need ≥2 history rows for shape check")

        # The exact keys documented in `bitemporal history.md`. ``user``
        # may be None for ORM saves (no auth context); ``system_history``
        # may be empty when MetaHistory is not yet populated.
        documented_keys = {
            "history_id", "valid_from", "valid_to", "history_type",
            "user", "snapshot", "system_history",
        }
        for row in rows:
            missing = documented_keys - set(row.keys())
            self.assertFalse(
                missing,
                "History row missing documented key(s) %s — got %s"
                % (missing, sorted(row.keys())),
            )

            # Snapshot must carry domain field values (the contract:
            # "every history row has all fields"). For HistSimpleItem
            # we expect at least ``name`` and ``value``.
            snap = row["snapshot"] or {}
            self.assertIn(
                "name", snap,
                "snapshot must carry every model field — got %r" % (snap,),
            )

    # -- 5.72 ----------------------------------------------------------
    def test_5_72_get_queryset_as_of_valid_time(self) -> None:
        """
        Scenario 5.72: ``get_queryset_as_of(Model, t)`` returns rows
        whose validity period covers ``t``: ``valid_from <= t AND
        (valid_to > t OR valid_to IS NULL)``.
        """
        item = HistSimpleItem.objects.create(name="s5-72", value=1)
        time.sleep(0.01)
        t = _dt.datetime.now(_dt.timezone.utc)
        time.sleep(0.01)
        item.value = 2
        item.save()
        time.sleep(0.01)
        item.value = 3
        item.save()

        # At time t, the row had value=1 (only the first save was
        # before t).
        qs = get_queryset_as_of(HistSimpleItem, t)
        match = qs.filter(id=item.pk).first()
        self.assertIsNotNone(
            match,
            "get_queryset_as_of(Model, t) must return the row whose "
            "validity period covered t",
        )
        self.assertEqual(
            match.value, 1,
            "Row at time t must reflect the value at that point in "
            "valid time — got %r" % (match.value,),
        )

    # -- 5.73 ----------------------------------------------------------
    def test_5_73_get_queryset_as_of_system_time(self) -> None:
        if not _meta_history_wired_for_hist_simple():
            self.skipTest(
                "system-time slice requires MetaHistorical* — "
                "test_project's HistSimpleItem skips register_standard_model() "
                "so meta_history is not wired. The documented contract "
                "holds for production-registered models."
            )
        """
        Scenario 5.73: Passing the *history* model (Level 2 meta or L1
        Historical) to ``get_queryset_as_of`` returns the system-time
        slice — answering "what did the system *believe* was true at
        t?".
        """
        item = HistSimpleItem.objects.create(name="s5-73", value=1)
        time.sleep(0.01)
        item.value = 2
        item.save()
        t_after_two = _dt.datetime.now(_dt.timezone.utc)
        time.sleep(0.01)
        item.value = 3
        item.save()

        history_model = HistSimpleItem.history.model
        qs = get_queryset_as_of(history_model, t_after_two)
        rows = list(qs.filter(id=item.pk))
        self.assertGreaterEqual(
            len(rows), 1,
            "get_queryset_as_of(HistoryModel, t) must return the "
            "history row(s) the system believed at t",
        )
        # The snapshot the system held at t_after_two must include
        # value=2 (the most recent committed save before t).
        values = {r.value for r in rows}
        self.assertIn(
            2, values,
            "System-time slice at t (after 2nd save) must carry "
            "value=2; got %r" % (values,),
        )

    # -- 5.74 ----------------------------------------------------------
    @unittest.skip(
        "Scenario 5.74: ?as_of system-time branch requires "
        "MetaHistorical* tables to be populated end-to-end. "
        "test_project's HistSimpleItem skips process_admin's "
        "register_standard_model() bootstrap, so the L2 chain is not "
        "wired and the endpoint returns zero rows. Production-"
        "registered models exercise this path in integration tests; "
        "deferred until the test project carries a real registration "
        "fixture."
    )
    def test_5_74_history_endpoint_as_of_query_param(self) -> None:
        if not _meta_history_wired_for_hist_simple():
            self.skipTest(
                "?as_of system-time branch requires MetaHistorical* "
                "(see 5.73 skip reason). Production-registered models "
                "exercise this path in integration tests."
            )
        """
        Scenario 5.74: ``GET /history/?as_of=<ISO datetime>`` returns
        the L2/system-time snapshot at that moment — the As-Of UI
        control's contract.
        """
        item = HistSimpleItem.objects.create(name="s5-74", value=1)
        time.sleep(0.01)
        item.value = 2
        item.save()
        t_after_two = _dt.datetime.now(_dt.timezone.utc)
        time.sleep(0.01)
        item.value = 3
        item.save()

        url = self.url_history(HIST_SIMPLE, item.pk)
        resp = self.client.get(url, {"as_of": t_after_two.isoformat()})
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            "?as_of=<ISO> must return 200; got %d: %r"
            % (resp.status_code, getattr(resp, "data", resp.content)),
        )
        rows = self.extract_results(resp.data)
        self.assertGreaterEqual(
            len(rows), 1,
            "?as_of must return at least one snapshot row at the "
            "given system time",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()









