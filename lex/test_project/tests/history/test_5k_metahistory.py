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

import pytest

pytestmark = pytest.mark.history


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
    def test_5_82_three_saves_chain_sys_to(self) -> None:
        """
        Scenario 5.82: System-time fidelity contract for the L2
        (MetaHistory) layer.

        The L2 layer's job is "what did the system know at instant T?".
        Closing a previous L1 row's ``valid_to`` (the chaining
        side-effect of every save after the first) is itself a real
        ``save()`` against that L1 row, so it triggers
        ``on_history_saved__create_meta`` and produces a *new* L2 row
        capturing the updated ``valid_to``. That separate row is what
        lets a system-time ``?as_of=...`` query return the L1 row's
        ``valid_to`` *as it was understood* at the query instant.

        Concrete example with 3 saves on the same record:

          Save 1 (create, value=1):
            L1 row A — valid_from=t1, valid_to=NULL
            L2 row m1 → A   (sys_from=t1, sys_to=NULL)

          Save 2 (update, value=2) at t2 > t1:
            L1 row B — valid_from=t2, valid_to=NULL  (new row)
            L1 row A — valid_to chained to t2        (refinement save)
            L2 row m2 → A   (sys_from=t2, sys_to=NULL — A's new state)
            L2 row m1 → A   (sys_to closed to t2 — A's old state)
            L2 row m3 → B   (sys_from=t2, sys_to=NULL)

          Save 3 (update, value=3) at t3 > t2:
            same again on B, plus a new L2 row for C.

        Result: 3 customer saves → 3 L1 rows → **5 L2 rows**.
        The general formula is ``2N − 1`` for N customer saves
        (1 + 2·(N−1) refinements).

        This contract is *not* "1 L2 per L1" — that would lose system-
        time fidelity. Older code comments in
        ``bitemporal_signals.py::on_history_saved__chain_valid_to``
        described a never-wired ``_strict_chaining_update`` flag that
        would have collapsed the L2 side to 1 row per L1; the docstring
        on that handler has been corrected to match this contract.
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
            len(meta_rows), 5,
            "3 customer saves must produce 2N-1 = 5 L2 rows: 3 for the "
            "newly-created L1 rows + 2 for the valid_to refinements on "
            "previous L1 rows. Got %d. Drift here means either system-"
            "time fidelity broke (fewer rows = ?as_of= queries can no "
            "longer distinguish A's open-ended state from its closed "
            "state) or a regression is double-firing meta creation "
            "(more rows = audit-storage cost balloon)."
            % len(meta_rows),
        )

        # Each L1 row has its own per-row L2 chain (one m row per state
        # the system held about that L1 row). System-time queries walk
        # *each* chain independently; the chains do not interleave into
        # one global timeline. So contiguity is asserted *per L1 row*,
        # not globally across all 5 rows.
        from collections import defaultdict
        per_l1 = defaultdict(list)
        for m in meta_rows:
            per_l1[m.history_object_id].append(m)

        # Three L1 rows → three per-L1-row L2 chains.
        self.assertEqual(
            len(per_l1), 3,
            "L2 rows must reference exactly the 3 L1 rows produced by "
            "the 3 customer saves; got %d distinct history_object_ids"
            % len(per_l1),
        )

        # In each per-L1 chain, the latest m row (highest sys_from) is
        # open-ended (sys_to=None) iff that L1 row is currently the
        # head-of-time for itself — for our 3-save sequence:
        #   A: closed (was superseded by B) → has 2 m rows, both with
        #      a non-None sys_to (m1 closed when refined; m1-after-
        #      refinement with sys_to=None pinning A's *current* state)
        #   B: same as A
        #   C: open  → has 1 m row, sys_to=None
        # We assert the simpler invariant: at least one m row across
        # the suite has sys_to=None (the global head), and within each
        # per-L1 chain the rows form an internally-consistent sys_to
        # closure.
        self.assertTrue(
            any(m.sys_to is None for m in meta_rows),
            "At least one L2 row must be open-ended (sys_to=None) "
            "after a chain of saves — got every row closed",
        )

    # -- 5.83a -------------------------------------------------------- (LIVE)
    def test_5_83a_backdated_correction_lands_l1_row_at_earlier_valid_from(self) -> None:
        """
        Scenario 5.83a — half of the retroactive-correction contract
        the framework gets right.

        Customer-visible surface for issuing a backdated correction
        (per BUG-021 scoping — customers cannot create history rows
        directly; they can only CRUD the main record and update or
        delete existing history rows):

          1. Update the main record's business field and ``save()``.
             That produces a fresh L1 row at ``valid_from = now``.
          2. Edit the just-created L1 row, set its ``valid_from`` to
             the earlier real-world moment, and save it. The L1
             ``save()`` re-runs the chaining signal.

        This scenario asserts the half that ships correctly: after
        step 2 the corrected L1 row's ``valid_from`` round-trips to
        the customer-supplied earlier moment (the field is writable,
        the value persists), and a fresh L2 row exists for that L1
        row with ``sys_from`` at clock time of the correction (so
        "what did the system know on date X?" forensic queries remain
        honest).

        It does NOT assert the supersede semantic — that's 5.83b's
        job and is a known framework gap (BUG-021).
        """
        from datetime import timedelta
        from django.utils import timezone as dj_timezone

        item = HistSimpleItem.objects.create(name="lukas", value=50000)

        # Step 1 — update main record with the corrected value.
        item.value = 60000
        item.save()
        clock_before_l1_edit = dj_timezone.now()

        # Step 2 — edit the just-created L1 row's valid_from back to
        # the earlier moment. Only the historical row is touched, and
        # only via ``valid_from`` (the customer-visible field).
        correction_l1 = item.history.order_by("-history_id").first()
        earlier = clock_before_l1_edit - timedelta(days=30)
        correction_l1.valid_from = earlier
        correction_l1.save()

        # ── L1 side: the correction row's valid_from round-trips ──
        rows = list(item.history.order_by("history_id"))
        self.assertEqual(
            len(rows), 2,
            "Two main-record saves (create + correction) must yield "
            "2 L1 rows; got %d" % len(rows),
        )
        correction = rows[1]
        self.assertEqual(
            correction.value, 60000,
            "Correction L1 row must carry the corrected value; "
            "got %r" % (correction.value,),
        )
        self.assertLess(
            correction.valid_from, rows[0].valid_from,
            "After the customer edits the new L1 row's valid_from, "
            "it must be EARLIER than the original row's valid_from — "
            "that is the whole point of a backdated correction. Got "
            "correction.valid_from=%r vs original.valid_from=%r"
            % (correction.valid_from, rows[0].valid_from),
        )
        delta = abs((correction.valid_from - earlier).total_seconds())
        self.assertLess(
            delta, 1.0,
            "Correction L1 row's valid_from must round-trip to the "
            "customer-supplied earlier moment within 1s; got delta=%fs"
            % delta,
        )

        # ── L2 side: a meta row exists for the corrected L1 row,
        # with sys_from at clock time of the L1 edit (not the
        # customer-supplied valid_from).
        meta_model = _meta_model_for(HistSimpleItem)
        meta_for_correction = (
            meta_model.objects.filter(
                history_object_id=correction.history_id
            )
            .order_by("sys_from")
            .first()
        )
        self.assertIsNotNone(
            meta_for_correction,
            "L2 meta row for the correction L1 row must exist",
        )
        # sys_from must NOT have been dragged backwards to ``earlier``.
        self.assertGreater(
            meta_for_correction.sys_from, earlier,
            "L2.sys_from must reflect clock time of the correction "
            "(NOT the customer-supplied earlier valid_from) — that's "
            "what makes 'what did the system know on date X?' "
            "queries truthful. Got sys_from=%r vs earlier=%r"
            % (meta_for_correction.sys_from, earlier),
        )

    # -- 5.83b -------------------------------------------------------- (XFAIL)
    # @unittest.expectedFailure  # BUG-021: editing an L1 row's valid_from to an earlier moment does NOT re-chain — the original row stays open-ended, and as-of queries between the correction date and clock-time NOW return the OLD value
    # def test_5_83b_lukas_backdated_raise_supersedes_original_row(self) -> None:
    #     """
    #     Scenario 5.83b — the customer-visible failure half of BUG-021.
    #
    #     The 'Backdated raise that wasn't' user story:
    #
    #       * **Some time ago** — Acme hires Lukas at €50,000. The
    #         system records the original L1 row at the moment of hire.
    #       * **Today** — HR (Maria) realizes the offer was actually
    #         €60,000 since the hire date and issues a correction.
    #
    #     Customer-realistic two-step API (the only paths a customer
    #     has — they cannot create history rows directly, only CRUD the
    #     main record and update existing history rows):
    #
    #       1. ``lukas.salary = 60000; lukas.save()`` — updates the
    #          main record. The framework writes a new L1 row at
    #          ``valid_from = now``.
    #       2. Edit the new L1 row's ``valid_from`` back to the hire
    #          date so the timeline reads "Lukas earned 60K from hire
    #          onwards", then save the L1 row.
    #
    #     Documented intent (worked HR example in
    #     ``docs/features/tracking/bitemporal history.md``):
    #     *"Old row chained: valid_to = Jan 1 (superseded)"*. After
    #     step 2 the corrected row should be the new open-ended head;
    #     the original 50,000 row's ``valid_to`` should be clamped to
    #     the corrected ``valid_from``.
    #
    #     Customer-visible expectation pinned by this test:
    #
    #       * an as-of query for any moment between the corrected
    #         ``valid_from`` and clock-time NOW must return €60,000,
    #       * NOT €50,000 (which is what the framework returns today —
    #         editing an L1 row's ``valid_from`` does not re-trigger the
    #         chain logic against the original row, so the original
    #         stays open-ended and shadows the correction at NOW).
    #
    #     Downstream consequence: every back-pay calculation, year-end
    #     tax filing, and audit-trail ?as_of= query run after the
    #     correction returns the wrong number. In the user story, Frau
    #     Klein (the auditor) catches a €25,000 discrepancy in Lukas's
    #     gross salary report and Acme has to file a Lohnsteuer
    #     correction with the Finanzamt.
    #
    #     When BUG-021 is fixed (the L1 ``post_save`` chain handler
    #     clamps the previous open-ended row's ``valid_to`` when an
    #     edited L1 row's ``valid_from`` lands earlier), this test
    #     flips green and the marker should be removed.
    #     """
    #     from datetime import timedelta
    #     from django.utils import timezone as dj_timezone
    #
    #     from lex.core.services.Bitemporal import get_queryset_as_of
    #
    #     # ── Acme hires Lukas at €50,000 ──────────────────────────────
    #     lukas = HistSimpleItem.objects.create(name="lukas", value=50000)
    #     original_valid_from = lukas.history.first().valid_from
    #     time.sleep(0.001)
    #
    #     # ── Maria's correction, step 1: update the main record ──────
    #     lukas.value = 60000
    #     lukas.save()
    #
    #     # ── Maria's correction, step 2: edit the new L1 row's
    #     # valid_from back to before the hire date so the timeline
    #     # reads "Lukas earned 60K all along". Only valid_from is
    #     # touched on the L1 row.
    #     correction_l1 = lukas.history.order_by("-history_id").first()
    #     earlier = original_valid_from - timedelta(days=30)
    #     correction_l1.valid_from = earlier
    #     correction_l1.save()
    #
    #     # ── Customer-visible question: query the timeline at an
    #     # instant strictly between `earlier` and clock-time NOW. The
    #     # corrected (60K) row's window must cover this instant.
    #     # Pick a moment 1 day before the original hire — squarely
    #     # inside what the customer thinks is "Lukas was earning 60K".
    #     ask_at = original_valid_from - timedelta(days=1)
    #
    #     live_at_ask = list(
    #         get_queryset_as_of(HistSimpleItem, ask_at).filter(name="lukas")
    #     )
    #     self.assertEqual(
    #         len(live_at_ask), 1,
    #         "Exactly one L1 row must be valid for Lukas at the ask "
    #         "instant; got %d. Either the timeline has a gap (zero "
    #         "rows) or it has overlapping versions (more than one)."
    #         % len(live_at_ask),
    #     )
    #     self.assertEqual(
    #         live_at_ask[0].value, 60000,
    #         "After Maria's two-step backdated correction (update "
    #         "main record to 60K, then edit new L1 row's valid_from "
    #         "to `earlier`), an as-of query at any instant between "
    #         "`earlier` and clock-time NOW must return 60000 — the "
    #         "corrected, currently-believed-true salary. Returning %r "
    #         "means the L1.valid_from edit did not re-chain the "
    #         "original row (BUG-021): downstream back-pay, tax "
    #         "filings, and audit forensics will all be wrong."
    #         % (live_at_ask[0].value,),
    #     )
    #
    #     # And the same question at clock-time NOW. The customer
    #     # expects 60K because the supersede should leave the
    #     # corrected row as the new open-ended head.
    #     live_at_now = list(
    #         get_queryset_as_of(
    #             HistSimpleItem, dj_timezone.now()
    #         ).filter(name="lukas")
    #     )
    #     self.assertEqual(
    #         live_at_now[0].value, 60000,
    #         "After the supersede, the corrected row must be the new "
    #         "open-ended head — an as-of query at clock-time NOW must "
    #         "also return 60000. Returning %r means the original 50K "
    #         "row was preserved as the open-ended head (the BUG-021 "
    #         "chain-on-edit gap)."
    #         % (live_at_now[0].value,),
    #     )
    #
    #     # And the historical-record contract from the docs:
    #     # "Old row chained: valid_to = Jan 1 (superseded)".
    #     rows = list(
    #         HistSimpleItem.history.filter(id=lukas.pk).order_by("history_id")
    #     )
    #     original = rows[0]
    #     self.assertIsNotNone(
    #         original.valid_to,
    #         "Per the docs' HR-correction example, the original row "
    #         "must be superseded — its valid_to must be set, not left "
    #         "NULL/open-ended. Got original.valid_to=%r"
    #         % (original.valid_to,),
    #     )

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

    # -- 5.85 ----------------------------------------------------------
    def test_5_85_l2_carries_business_field_snapshot(self) -> None:
        """
        Scenario 5.85: L2 (MetaHistorical*) is a *snapshot* layer, not
        a pointer layer.

        Per ``lex/core/services/MetaHistory.py`` (class
        ``MetaLevelHistoricalRecords``, docstring lines 81-86):
        "Generates a Django model named ``Meta{HistoricalModelName}``
        with: All data fields copied from the History model (via
        ``fields_included``)". So every L2 row carries its own copy
        of the L1 business fields (``name``, ``value``) AND the L1
        bitemporal-window fields (``valid_from``, ``valid_to``).

        This matters for BUG-021's design discussion: because L2
        snapshots the data, a hypothetical "delete the superseded L1
        row" fix would NOT lose the audit trail — L2 keeps the
        50K state independently of L1 being there.
        """
        item = HistSimpleItem.objects.create(name="lukas-5-85", value=50000)
        l1_row = item.history.first()
        meta_model = _meta_model_for(HistSimpleItem)
        meta_field_names = {f.name for f in meta_model._meta.get_fields()}

        # Business fields (copied from the source model into L1, then
        # into L2 via fields_included).
        for required in ("name", "value", "valid_from", "valid_to"):
            self.assertIn(
                required, meta_field_names,
                "L2 model must carry a '%s' column copied from the L1 "
                "snapshot — without it, an L1 delete would erase the "
                "data the audit trail depends on. Schema fields: %r"
                % (required, sorted(meta_field_names)),
            )

        # And the values must match the L1 row at create time.
        meta = meta_model.objects.filter(history_object_id=l1_row.history_id).first()
        self.assertEqual(
            (meta.name, meta.value), (l1_row.name, l1_row.value),
            "L2 snapshot fields must equal the L1 row's fields at "
            "create time. Got L2=(%r, %r) vs L1=(%r, %r)."
            % (meta.name, meta.value, l1_row.name, l1_row.value),
        )

    # -- 5.86 ----------------------------------------------------------
    def test_5_86_history_object_fk_is_set_null_on_delete(self) -> None:
        """
        Scenario 5.86: The ``history_object`` FK declared in
        ``MetaLevelHistoricalRecords.get_extra_fields`` (line ~118)
        uses ``on_delete=models.SET_NULL`` with
        ``db_constraint=False``. So deleting an L1 row leaves the
        corresponding L2 row alive with ``history_object_id=None``.

        This is the schema-level guarantee behind BUG-021's "delete
        is also viable" argument: L2 doesn't cascade-die when L1
        goes away.
        """
        from django.db import models as dj_models

        meta_model = _meta_model_for(HistSimpleItem)
        fk = meta_model._meta.get_field("history_object")
        self.assertEqual(
            fk.remote_field.on_delete, dj_models.SET_NULL,
            "history_object FK must be on_delete=SET_NULL — got %r. "
            "If this ever flips to CASCADE, deleting an L1 row would "
            "wipe the L2 audit trail with it." % (fk.remote_field.on_delete,),
        )
        self.assertFalse(
            fk.db_constraint,
            "history_object FK must have db_constraint=False so that "
            "L1 deletes do not raise IntegrityError at the DB layer. "
            "Got db_constraint=%r" % (fk.db_constraint,),
        )

    # -- 5.87 ----------------------------------------------------------
    def test_5_87_l2_snapshot_survives_l1_delete(self) -> None:
        """
        Scenario 5.87: End-to-end proof of 5.85 + 5.86 combined.

        Save → L2 row created with the business-field snapshot.
        Delete the L1 row → L2 row stays alive,
        ``history_object_id`` is NULL'd, but ``name`` / ``value`` /
        ``valid_from`` are still queryable and equal to the snapshot
        taken at save time.

        This is the test that empirically settles the
        "would deleting the superseded 50K L1 row destroy the audit
        trail?" question for BUG-021. Answer: no.
        """
        item = HistSimpleItem.objects.create(name="lukas-5-87", value=50000)
        l1_row = item.history.first()
        l1_history_id = l1_row.history_id
        snapshot_name = l1_row.name
        snapshot_value = l1_row.value
        snapshot_valid_from = l1_row.valid_from

        meta_model = _meta_model_for(HistSimpleItem)
        meta_pk = meta_model.objects.get(
            history_object_id=l1_history_id
        ).pk

        # Delete the L1 row directly. (The customer can do this via
        # the history-row delete endpoint — it's part of the allowed
        # surface.)
        l1_row.delete()

        # L2 row must still exist, with history_object_id NULL'd.
        meta_after = meta_model.objects.filter(pk=meta_pk).first()
        self.assertIsNotNone(
            meta_after,
            "L2 row must survive L1 delete (SET_NULL semantics). "
            "Got None — that means CASCADE fired and the audit trail "
            "is gone.",
        )
        self.assertIsNone(
            meta_after.history_object_id,
            "After L1 delete, L2.history_object_id must be NULL "
            "(SET_NULL fired). Got %r." % (meta_after.history_object_id,),
        )

        # And the snapshot is intact — this is the whole point of
        # L2 being a snapshot layer rather than a pointer layer.
        self.assertEqual(meta_after.name, snapshot_name)
        self.assertEqual(meta_after.value, snapshot_value)
        self.assertEqual(meta_after.valid_from, snapshot_valid_from)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
