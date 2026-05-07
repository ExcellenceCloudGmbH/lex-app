"""
Cluster 5k: MetaHistory positive contract.

Intent (from docs/features/tracking/bitemporal history.md):

    Every save on a tracked LexModel produces:
      - one Level-1 row in ``Historical<Model>`` (valid time)
      - one Level-2 row in ``MetaHistorical<Model>`` (system time)

    The L2 row's ``history_object`` FK points back at the L1 row;
    ``sys_from`` / ``sys_to`` chain across L2 rows just like
    ``valid_from`` / ``valid_to`` chain across L1 rows. Direct user
    saves leave ``meta_task_status = NONE``; only scheduled
    bitemporal activations bump it to SCHEDULED → DONE.

Cluster 9.7-9.10 covers the suppression *primitives* (ContextVars).
5k locks down the positive contract — that the L2 row actually exists
and is wired correctly. Scenario numbering matches
docs/test-plan/test-clusters.md § 5k.
"""

from __future__ import annotations

import time
import unittest

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HistSimpleItem


def _meta_model_for(model_class):
    """Resolve ``MetaHistorical<Model>``. The ``meta_history`` manager
    is attached to the *historical* model by
    ``ModelRegistration._register_standard_model`` (see
    ``lex/process_admin/utils/model_registration.py`` line ~334:
    ``history.contribute_to_class(historical_model, "meta_history")``)
    — NOT to the source model. The earlier shorthand
    ``model_class.meta_history.model`` always raised ``AttributeError``
    and silently sent every 5k test to the skip path even when L2 was
    wired (see Cluster 5l, Session 52, for the same access pattern
    landing live)."""
    return model_class.history.model.meta_history.model


def _meta_history_wired() -> bool:
    """Probe at call time — at class body / import time, simple_history's
    late ``register()`` may not have attached the manager yet. Uses the
    canonical access path on the *historical* model."""
    try:
        _ = HistSimpleItem.history.model.meta_history.model
        return True
    except Exception:
        return False


_SKIP_REASON = (
    "MetaHistorical* is not wired on this build. The L2 manager is "
    "attached by ``ModelRegistration._register_standard_model`` at "
    "framework boot; if the test_project's HistSimpleItem fixture "
    "skipped that path the manager will be missing. The documented "
    "contract (5.81-5.84) holds for production-registered models; "
    "see lex.tests.unit.api.test_history_endpoint and lex.tests.unit."
    "infra.test_bitemporal_service for unit-level coverage on a "
    "Mock-backed L2 manager. Cluster 5l (Session 52) demonstrates "
    "the canonical access pattern when L2 *is* wired."
)


class TestCluster05k_MetaHistoryContract(E2ETestCase):
    """Level-2 row creation, ``sys_to`` chaining, retroactive corrections."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        if not _meta_history_wired():
            self.skipTest(_SKIP_REASON)

    # -- 5.81 ----------------------------------------------------------
    def test_5_81_single_save_creates_one_meta_row(self) -> None:
        """
        Scenario 5.81: After ``obj.save()`` (create), exactly 1 L2
        row exists with ``history_object_id == L1.history_id``,
        ``sys_to is None``, ``meta_history_type == "+"``.
        """
        item = HistSimpleItem.objects.create(name="s5-81", value=1)
        l1_row = item.history.first()
        meta_model = _meta_model_for(HistSimpleItem)

        meta_rows = meta_model.objects.filter(history_object_id=l1_row.history_id)
        self.assertEqual(
            meta_rows.count(), 1,
            "Single create save must produce exactly 1 L2 meta row "
            "for that L1 history row; got %d" % meta_rows.count(),
        )
        meta = meta_rows.first()
        self.assertIsNone(
            meta.sys_to,
            "Newly-created L2 row's sys_to must be None (head of "
            "system-time chain); got %r" % (meta.sys_to,),
        )
        self.assertEqual(
            meta.meta_history_type, "+",
            "First L2 row's meta_history_type must be '+' (created); "
            "got %r" % (meta.meta_history_type,),
        )

    # -- 5.82 ----------------------------------------------------------
    @unittest.expectedFailure  # BUG-022: every valid_to refinement on a previous L1 row mints an extra L2 row, so 3 saves produce 5 (not 3) — contradicts the in-place-update comment in on_history_saved__chain_valid_to
    def test_5_82_three_saves_chain_sys_to(self) -> None:
        """
        Scenario 5.82: Three saves produce L2 rows whose ``sys_to``
        chains into the next ``sys_from`` — same contract as 5.61 but
        on the L2 table.

        The documented intent (from ``on_history_saved__chain_valid_to``
        line 109: "mark the record with ``_strict_chaining_update`` so
        the MetaHistory layer can update the existing meta record
        **in-place** instead of creating a new version") is one L2
        row per save.

        Observed: each save after the first mints **two** L2 rows —
        one for the newly created L1 row, plus one extra for the
        refinement-update of the previous L1 row's ``valid_to``. The
        in-place flag is not honoured. Tracked as **BUG-022**.
        """
        item = HistSimpleItem.objects.create(name="s5-82", value=1)
        time.sleep(0.001)
        item.value = 2
        item.save()
        time.sleep(0.001)
        item.value = 3
        item.save()

        meta_model = _meta_model_for(HistSimpleItem)
        meta_rows = list(
            meta_model.objects.filter(id=item.pk).order_by("sys_from")
        )
        self.assertEqual(
            len(meta_rows), 3,
            "3 saves must produce 3 L2 rows; got %d" % len(meta_rows),
        )
        self.assertEqual(
            meta_rows[0].sys_to, meta_rows[1].sys_from,
            "L2 row[0].sys_to must equal row[1].sys_from "
            "(system-time chain); got %r != %r"
            % (meta_rows[0].sys_to, meta_rows[1].sys_from),
        )
        self.assertEqual(
            meta_rows[1].sys_to, meta_rows[2].sys_from,
            "L2 row[1].sys_to must equal row[2].sys_from",
        )
        self.assertIsNone(
            meta_rows[-1].sys_to,
            "Latest L2 row's sys_to must be None — open-ended at the "
            "head of system-time chain",
        )

    # -- 5.83 ----------------------------------------------------------
    @unittest.expectedFailure  # BUG-021: retroactive valid_from correction is documented intent that the framework does not yet accept on user-supplied save() — the L1 row's valid_from is silently rewritten to now()
    def test_5_83_retroactive_valid_from_correction(self) -> None:
        """
        Scenario 5.83: A retroactive ``valid_from`` correction lands in
        the L1 timeline at the customer-supplied date, but the
        corresponding L2 row's ``sys_from`` reflects the *clock time*
        of the correction, not the customer-supplied valid_from.
        """
        import datetime as _dt

        item = HistSimpleItem.objects.create(name="s5-83", value=1)
        clock_before_correction = _dt.datetime.now(_dt.timezone.utc)

        # Retroactive correction — explicit earlier valid_from
        earlier = clock_before_correction - _dt.timedelta(days=30)
        item.value = 2
        item.valid_from = earlier
        item.save()

        rows = list(item.history.order_by("history_id"))
        self.assertEqual(len(rows), 2)
        self.assertLess(
            rows[1].valid_from, rows[0].valid_from,
            "Retroactive correction must land with the earlier "
            "valid_from in the L1 timeline",
        )

        meta_model = _meta_model_for(HistSimpleItem)
        meta_rows = list(meta_model.objects.filter(id=item.pk).order_by("sys_from"))
        self.assertEqual(len(meta_rows), 2)
        # The 2nd L2 row's sys_from must be CLOCK time, not valid_from
        self.assertGreaterEqual(
            meta_rows[1].sys_from, clock_before_correction,
            "L2.sys_from must reflect clock time of the correction, "
            "NOT the customer-supplied valid_from — got %r vs clock %r"
            % (meta_rows[1].sys_from, clock_before_correction),
        )

    # -- 5.84 ----------------------------------------------------------
    def test_5_84_meta_task_status_default(self) -> None:
        """
        Scenario 5.84: Direct user saves leave ``meta_task_status`` at
        a non-active default (NONE / pending). Scheduled activations
        are the only path that bumps it to SCHEDULED → DONE.
        """
        item = HistSimpleItem.objects.create(name="s5-84", value=1)
        meta_model = _meta_model_for(HistSimpleItem)
        meta = meta_model.objects.filter(id=item.pk).first()
        self.assertIsNotNone(meta, "L2 row must exist after create save")

        observed = getattr(meta, "meta_task_status", None)
        # Customer contract: direct saves never produce SCHEDULED or
        # DONE here. Anything else (NONE / None / "") is acceptable as
        # the "no scheduled activation" sentinel.
        self.assertNotIn(
            observed, ("SCHEDULED", "DONE"),
            "Direct save must NOT set meta_task_status to SCHEDULED or "
            "DONE — those are reserved for scheduled bitemporal "
            "activations; got %r" % (observed,),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()






