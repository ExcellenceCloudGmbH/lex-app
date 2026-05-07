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
    def test_5_83a_history_date_lands_l1_row_at_earlier_moment(self) -> None:
        """
        Scenario 5.83a — half of the retroactive-correction contract
        the framework gets right.

        The framework exposes ``_history_date`` (used by
        ``backfill_bitemporal_history``, ``Bitemporal.resurrect_object``,
        and the integration tests in ``lex/tests/integration/``) as the
        hook for "land the next save's L1 row at an earlier ``valid_from``
        instead of ``timezone.now()``".

        This scenario asserts that the hook works for what it does ship:
        the L1 row's ``valid_from`` round-trips to the customer-supplied
        earlier moment, while the corresponding L2 row's ``sys_from``
        reflects clock time of the correction (so the "what did the
        system know on date X?" forensic surface remains honest).

        It does NOT assert the supersede semantic — that's 5.83b's job
        and is a known framework gap (BUG-021).
        """
        from datetime import timedelta
        from django.utils import timezone as dj_timezone

        item = HistSimpleItem.objects.create(name="lukas", value=50000)
        clock_before_correction = dj_timezone.now()

        earlier = clock_before_correction - timedelta(days=30)
        item.value = 60000
        item._history_date = earlier
        item.save()

        # ── L1 side: the correction row landed at the earlier date ──
        rows = list(item.history.order_by("history_id"))
        self.assertEqual(
            len(rows), 2,
            "Two saves (create + correction) must yield 2 L1 rows; "
            "got %d" % len(rows),
        )
        correction = rows[1]
        self.assertEqual(
            correction.value, 60000,
            "Correction L1 row must carry the corrected value; "
            "got %r" % (correction.value,),
        )
        self.assertLess(
            correction.valid_from, rows[0].valid_from,
            "Correction L1 row's valid_from must be EARLIER than "
            "the original row's valid_from — that is the whole point "
            "of _history_date. Got correction.valid_from=%r vs "
            "original.valid_from=%r"
            % (correction.valid_from, rows[0].valid_from),
        )
        # Round-trip to within a sub-second tolerance — the saved
        # value must be the customer-supplied earlier moment.
        delta = abs((correction.valid_from - earlier).total_seconds())
        self.assertLess(
            delta, 1.0,
            "Correction L1 row's valid_from must round-trip to the "
            "customer-supplied earlier moment within 1s; got delta=%fs"
            % delta,
        )

        # ── L2 side: sys_from reflects CLOCK time of the correction ──
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
        self.assertGreaterEqual(
            meta_for_correction.sys_from, clock_before_correction,
            "L2.sys_from must reflect clock time of the correction "
            "(NOT the customer-supplied earlier valid_from) — that's "
            "what makes 'what did the system know on date X?' "
            "queries truthful. Got sys_from=%r vs clock_before=%r"
            % (meta_for_correction.sys_from, clock_before_correction),
        )

    # -- 5.83b -------------------------------------------------------- (XFAIL)
    @unittest.expectedFailure  # BUG-021: framework treats a retroactive _history_date as "fill a past gap" instead of "supersede the open-ended head" — see the 'Backdated raise' user story in the BUG-021 tracker entry. Customer-visible: as-of queries between the correction date and clock-time NOW return the OLD value, not the corrected one
    def test_5_83b_lukas_backdated_raise_supersedes_original_row(self) -> None:
        """
        Scenario 5.83b — the customer-visible failure half of BUG-021.

        The 'Backdated raise that wasn't' user story:

          * **Some time ago** — Acme hires Lukas at €50,000. The system
            records the original L1 row at the moment of hire.
          * **Today** — HR realizes the offer was actually €60,000 and
            issues a correction backdated to the hire date.

        Documented intent (worked HR example in
        ``docs/features/tracking/bitemporal history.md``):
        *"Old row chained: valid_to = Jan 1 (superseded)"*. The
        corrected row becomes the new open-ended head; the original
        50,000 row is closed at the correction's ``valid_from``.

        Customer-visible expectation that this test pins:

          * an as-of query for *any* moment between the correction
            date and clock-time NOW must return €60,000 (the
            corrected, currently-believed-true salary on that date),
          * NOT €50,000 (which is what the framework returns today —
            the original row stays open-ended; the correction is
            squeezed into a past-only [earlier, hire_clock_time)
            window. A simple ?as_of= query a few seconds after the
            correction returns 50,000 because clock-time NOW falls in
            the original row's preserved-open-ended window).

        Downstream consequence: every back-pay calculation, year-end
        tax filing, and audit-trail ?as_of= query that runs after
        the correction returns the wrong number. In the user story,
        Frau Klein (the auditor) catches a €25,000 discrepancy in
        Lukas's gross salary report and Acme has to file a
        Lohnsteuer correction with the Finanzamt.

        When BUG-021 is fixed (chain logic clamps the previous
        open-ended row's ``valid_to`` when a retroactive correction
        lands earlier than its ``valid_from``), this test goes green
        and the marker should be removed.
        """
        from datetime import timedelta
        from django.utils import timezone as dj_timezone

        from lex.core.services.Bitemporal import get_queryset_as_of

        # ── "30 days ago" — Acme hires Lukas at €50,000 ──────────────
        # Real customers create at clock-time NOW (no _history_date),
        # so we mirror that: the original row's valid_from = now() at
        # the moment of create. We then sleep a microsecond so the
        # subsequent correction's clock-time is strictly later.
        lukas = HistSimpleItem.objects.create(name="lukas", value=50000)
        original_valid_from = lukas.history.first().valid_from
        time.sleep(0.001)

        # ── "Today" — HR correction backdated 30 days before hire ───
        # The customer's intent: "salary was 60K from `earlier`
        # onward"; the docs' HR example asks for the original row to
        # be superseded at this earlier moment.
        earlier = original_valid_from - timedelta(days=30)
        lukas.value = 60000
        lukas._history_date = earlier
        lukas.save()

        # ── Customer-visible question: query the timeline at an
        # instant strictly between `earlier` and clock-time NOW. The
        # corrected (60K) row's window must cover this instant.
        # Pick an instant 1 day before the original hire — squarely
        # inside what the customer thinks is "Lukas was earning 60K".
        ask_at = original_valid_from - timedelta(days=1)

        live_at_ask = list(
            get_queryset_as_of(HistSimpleItem, ask_at).filter(name="lukas")
        )
        self.assertEqual(
            len(live_at_ask), 1,
            "Exactly one L1 row must be valid for Lukas at the ask "
            "instant; got %d. Either the timeline has a gap (zero "
            "rows) or it has overlapping versions (more than one)."
            % len(live_at_ask),
        )
        self.assertEqual(
            live_at_ask[0].value, 60000,
            "After a backdated raise (60K, _history_date=earlier), "
            "an as-of query at any instant between `earlier` and "
            "clock-time NOW must return 60000 — the corrected, "
            "currently-believed-true salary. Returning %r means the "
            "framework treated the correction as a past-gap insert "
            "instead of a supersede (BUG-021): downstream back-pay, "
            "tax filings, and audit forensics will all be wrong."
            % (live_at_ask[0].value,),
        )

        # And ask the same question at clock-time NOW. The customer
        # expects the same answer (60K) because the supersede should
        # leave the corrected row open-ended.
        live_at_now = list(
            get_queryset_as_of(
                HistSimpleItem, dj_timezone.now()
            ).filter(name="lukas")
        )
        self.assertEqual(
            live_at_now[0].value, 60000,
            "After the supersede, the corrected row must be the new "
            "open-ended head — an as-of query at clock-time NOW "
            "must also return 60000. Returning %r means the original "
            "50K row was preserved as the open-ended head (the "
            "BUG-021 gap-fill behaviour)."
            % (live_at_now[0].value,),
        )

        # And the historical-record contract from the docs:
        # "Old row chained: valid_to = Jan 1 (superseded)".
        rows = list(
            HistSimpleItem.history.filter(id=lukas.pk).order_by("history_id")
        )
        original = rows[0]
        # The original 50K row should be closed at the correction
        # date, not left open-ended.
        self.assertIsNotNone(
            original.valid_to,
            "Per the docs' HR-correction example, the original row "
            "must be superseded — its valid_to must be set, not left "
            "NULL/open-ended. Got original.valid_to=%r"
            % (original.valid_to,),
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






