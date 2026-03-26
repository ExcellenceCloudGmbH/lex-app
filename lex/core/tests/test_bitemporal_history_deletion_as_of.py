"""
Tests for the bitemporal history-deletion + system-time as_of bug.

Scenario
--------
    t0  Create record name="Test"
    t1  Modify → "Test1"
    t2  Modify → "Test2"
    t3  Delete the Level-1 history row for "Test1"
        (chain repair extends Test's valid_to to cover the gap)
    t4  Query time (after deletion)

Bug
---
``get_queryset_as_of(HistoricalModel, between_t2_and_t3)`` should return
the **pre-deletion** system-time snapshot — three history rows
(Test, Test1, Test2).  Instead it returns the **post-deletion** state
because:

1.  The deletion of h_test1 was never recorded as a ``-`` meta-history row.
2.  The chain-repair save (h_test.valid_to: t1 → t2) was never versioned
    in meta-history.
"""

from unittest.mock import patch
from datetime import timedelta
import datetime

from django.db import models, connection
from django.test import TransactionTestCase

from lex.core.models.LexModel import LexModel
from lex.core.services.Bitemporal import get_queryset_as_of


# ── Test model ──────────────────────────────────────────────────────

class HistDelAsOfModel(LexModel):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


# ── Test suite ──────────────────────────────────────────────────────

class BitemporalHistoryDeletionAsOfTest(TransactionTestCase):
    """Reproduce and verify the history-deletion / system-time as_of bug."""

    # ----------------------------------------------------------------
    # Fixtures
    # ----------------------------------------------------------------

    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration

        mr = ModelRegistration()
        try:
            mr._register_standard_model(HistDelAsOfModel, [])
        except Exception:
            pass

        self.HistoryModel = HistDelAsOfModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        with connection.schema_editor() as editor:
            editor.create_model(HistDelAsOfModel)
            editor.create_model(self.HistoryModel)
            editor.create_model(self.MetaModel)

    def tearDown(self):
        with connection.schema_editor() as editor:
            for m in (self.MetaModel, self.HistoryModel, HistDelAsOfModel):
                try:
                    editor.delete_model(m)
                except Exception:
                    pass

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _ts(self, dt):
        """Format datetime for debug output."""
        if dt is None:
            return "∞"
        return dt.strftime("%H:%M:%S")

    def _dump_state(self, label):
        """Print current state of all three tables for debugging."""
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print(f"{'=' * 60}")

        print("  Main Table:")
        for obj in HistDelAsOfModel.objects.all():
            print(f"    pk={obj.pk}  name={obj.name}")

        print("  History Table:")
        for h in self.HistoryModel.objects.order_by("valid_from", "history_id"):
            print(
                f"    history_id={h.history_id}  name={h.name}  "
                f"valid=[{self._ts(h.valid_from)}, {self._ts(h.valid_to)})  "
                f"type={h.history_type}"
            )

        print("  Meta-History Table:")
        for m in self.MetaModel.objects.order_by("sys_from", "meta_history_id"):
            print(
                f"    meta_id={m.meta_history_id}  "
                f"history_object_id={m.history_object_id}  "
                f"name={m.name}  "
                f"valid=[{self._ts(m.valid_from)}, {self._ts(m.valid_to)})  "
                f"sys=[{self._ts(m.sys_from)}, {self._ts(m.sys_to)})  "
                f"type={m.meta_history_type}"
            )

    # ----------------------------------------------------------------
    # Bug-reproduction test
    # ----------------------------------------------------------------

    def test_system_time_as_of_after_history_deletion(self):
        """
        After deleting a Level-1 history row at t3, a system-time query
        for any moment *before* t3 must return the pre-deletion snapshot
        of the history timeline.

        Timeline:
            t0  h_test   name="Test"    valid=[t0, t1)
            t1  h_test1  name="Test1"   valid=[t1, t2)
            t2  h_test2  name="Test2"   valid=[t2, ∞)
            t3  DELETE h_test1
                chain repair → h_test  valid=[t0, t2)

        Expected system-time behaviour:
            as_of(t_between_t2_t3) on HistoryModel →
                three meta rows (Test, Test1, Test2)  [pre-deletion world]
            as_of(t_after_t3) on HistoryModel →
                two meta rows (Test, Test2)            [post-deletion world]
        """
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t3 = t2 + timedelta(minutes=10)
        t_between_t2_t3 = t2 + timedelta(minutes=5)
        t_after_t3 = t3 + timedelta(minutes=5)

        # ── t0: Create "Test" ──
        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="Test")

        self._dump_state("After t0: Create 'Test'")

        # ── t1: Modify → "Test1" ──
        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Test1"
            obj.save()

        self._dump_state("After t1: Modify → 'Test1'")

        # ── t2: Modify → "Test2" ──
        with patch("django.utils.timezone.now", return_value=t2):
            obj.name = "Test2"
            obj.save()

        self._dump_state("After t2: Modify → 'Test2'")

        # Verify pre-deletion history chain
        h_test = self.HistoryModel.objects.get(name="Test")
        h_test1 = self.HistoryModel.objects.get(name="Test1")
        h_test2 = self.HistoryModel.objects.get(name="Test2")

        self.assertEqual(h_test.valid_from, t0)
        self.assertEqual(h_test.valid_to, t1)
        self.assertEqual(h_test1.valid_from, t1)
        self.assertEqual(h_test1.valid_to, t2)
        self.assertEqual(h_test2.valid_from, t2)
        self.assertIsNone(h_test2.valid_to)

        # Count meta records before deletion — should be ≥ 3
        # (one per history row, possibly more from chaining updates)
        meta_before_delete = self.MetaModel.objects.count()
        print(f"\nMeta records before deletion: {meta_before_delete}")

        # ── t3: Delete history row for "Test1" ──
        h_test1_id = h_test1.history_id
        with patch("django.utils.timezone.now", return_value=t3):
            h_test1.delete()

        self._dump_state("After t3: Delete history of 'Test1'")

        # Chain repair should have extended h_test.valid_to from t1 → t2
        h_test.refresh_from_db()
        self.assertEqual(
            h_test.valid_to,
            t2,
            "Chain repair must extend Test's valid_to to cover the gap",
        )

        # h_test2 unchanged
        h_test2.refresh_from_db()
        self.assertIsNone(h_test2.valid_to)

        # ── KEY ASSERTION 1: Deletion must be tracked in meta-history ──
        #
        # There should be a meta record with meta_history_type="-" for h_test1
        # that was created at t3.  This is the audit trail showing "we deleted
        # this history row at system time t3".
        deletion_meta = self.MetaModel.objects.filter(
            history_object_id=h_test1_id,
            meta_history_type="-",
        )
        self.assertTrue(
            deletion_meta.exists(),
            "A '-' (deleted) meta-history record must be created when a "
            "history row is deleted.  Currently missing — this is Bug #1.",
        )

        # ── KEY ASSERTION 2: Chain-repair valid_to change must be versioned ──
        #
        # When h_test.valid_to changed from t1 → t2 during chain repair,
        # a new meta-history version should have been created (or the existing
        # one updated) with sys_from=t3.
        #
        # The meta records for h_test should show:
        #   - An older version where valid_to = t1  (sys_to = t3)
        #   - A current version where valid_to = t2 (sys_to = None)
        meta_for_h_test = list(
            self.MetaModel.objects.filter(
                history_object=h_test,
            ).order_by("sys_from", "meta_history_id")
        )

        print(f"\nMeta records for h_test after deletion:")
        for m in meta_for_h_test:
            print(
                f"  meta_id={m.meta_history_id}  "
                f"valid_to={self._ts(m.valid_to)}  "
                f"sys=[{self._ts(m.sys_from)}, {self._ts(m.sys_to)})"
            )

        # There should be at least two versions: before-repair and after-repair
        versions_with_old_valid_to = [
            m for m in meta_for_h_test if m.valid_to == t1
        ]
        versions_with_new_valid_to = [
            m for m in meta_for_h_test if m.valid_to == t2
        ]

        self.assertTrue(
            len(versions_with_new_valid_to) >= 1,
            "After chain repair, meta-history for h_test must contain a "
            "version with valid_to=t2.  This shows the repair was tracked.",
        )

        # If properly versioned, the old version (valid_to=t1) should be
        # closed (sys_to is not None).
        if versions_with_old_valid_to:
            for old_version in versions_with_old_valid_to:
                self.assertIsNotNone(
                    old_version.sys_to,
                    "The old meta-history version (valid_to=t1) should be "
                    "closed (sys_to set) after the chain repair.  Bug #2.",
                )

        # ── KEY ASSERTION 3: System-time as_of BEFORE deletion ──
        #
        # ``get_queryset_as_of(HistoryModel, t_between_t2_t3)`` asks:
        # "What did the system know about the history at a time between t2
        #  and t3 (before the deletion happened)?"
        #
        # Expected: three meta rows — one for each of Test, Test1, Test2.
        qs_before = get_queryset_as_of(self.HistoryModel, t_between_t2_t3)
        names_before = sorted(qs_before.values_list("name", flat=True))
        print(f"\nSystem-time as_of({self._ts(t_between_t2_t3)}): {names_before}")

        self.assertIn(
            "Test",
            names_before,
            "Pre-deletion snapshot must include 'Test'",
        )
        self.assertIn(
            "Test1",
            names_before,
            "Pre-deletion snapshot must include 'Test1' — "
            "it hadn't been deleted yet at this system time.",
        )
        self.assertIn(
            "Test2",
            names_before,
            "Pre-deletion snapshot must include 'Test2'",
        )
        self.assertEqual(
            len(names_before),
            3,
            f"Pre-deletion system snapshot should have exactly 3 rows, "
            f"got {len(names_before)}: {names_before}",
        )

        # ── KEY ASSERTION 4: System-time as_of AFTER deletion ──
        #
        # ``get_queryset_as_of(HistoryModel, t_after_t3)`` asks:
        # "What does the system currently know about the history?"
        #
        # Expected: two meta rows — Test and Test2 (Test1 was deleted).
        qs_after = get_queryset_as_of(self.HistoryModel, t_after_t3)
        names_after = sorted(qs_after.values_list("name", flat=True))
        print(f"System-time as_of({self._ts(t_after_t3)}): {names_after}")

        self.assertNotIn(
            "Test1",
            names_after,
            "Post-deletion snapshot must NOT include 'Test1' — "
            "it was deleted at t3.",
        )
        self.assertEqual(
            len(names_after),
            2,
            f"Post-deletion system snapshot should have exactly 2 rows, "
            f"got {len(names_after)}: {names_after}",
        )
        self.assertIn("Test", names_after)
        self.assertIn("Test2", names_after)

    # ----------------------------------------------------------------
    # Focused unit tests for individual invariants
    # ----------------------------------------------------------------

    def test_history_deletion_creates_meta_record(self):
        """
        Deleting a Level-1 history row must produce a ``-`` meta-history
        record so the deletion is auditable at the system-time level.
        """
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t_delete = t0 + timedelta(minutes=20)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="Alpha")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Beta"
            obj.save()

        h_alpha = self.HistoryModel.objects.get(name="Alpha")
        h_alpha_id = h_alpha.history_id

        # Count meta records before deletion
        meta_count_before = self.MetaModel.objects.filter(
            history_object_id=h_alpha_id,
        ).count()

        with patch("django.utils.timezone.now", return_value=t_delete):
            h_alpha.delete()

        # A deletion meta record must exist
        deletion_metas = self.MetaModel.objects.filter(
            history_object_id=h_alpha_id,
            meta_history_type="-",
        )
        self.assertTrue(
            deletion_metas.exists(),
            f"Expected a '-' meta-history record for deleted history row "
            f"(history_id={h_alpha_id}).  "
            f"Meta count before={meta_count_before}, "
            f"after={self.MetaModel.objects.filter(history_object_id=h_alpha_id).count()}",
        )

        # The deletion meta record should have sys_from ≈ t_delete
        del_meta = deletion_metas.first()
        self.assertAlmostEqual(
            del_meta.sys_from.timestamp(),
            t_delete.timestamp(),
            delta=2,
            msg="Deletion meta record sys_from should be ≈ t_delete",
        )

    def test_chain_repair_creates_new_meta_version(self):
        """
        When chain repair changes h_prev.valid_to (from t_old → t_new),
        a new meta-history version must be created so system-time queries
        can distinguish before/after the repair.
        """
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t_delete = t2 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="A")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "B"
            obj.save()

        with patch("django.utils.timezone.now", return_value=t2):
            obj.name = "C"
            obj.save()

        h_a = self.HistoryModel.objects.get(name="A")
        h_b = self.HistoryModel.objects.get(name="B")

        # h_a.valid_to should be t1 before deletion
        self.assertEqual(h_a.valid_to, t1)

        # Snapshot meta records for h_a before repair
        meta_a_before = list(
            self.MetaModel.objects.filter(history_object=h_a)
            .order_by("sys_from")
            .values_list("valid_to", "sys_from", "sys_to")
        )
        print(f"Meta for A before repair: {meta_a_before}")

        with patch("django.utils.timezone.now", return_value=t_delete):
            h_b.delete()

        h_a.refresh_from_db()
        self.assertEqual(h_a.valid_to, t2, "Chain repair should extend A's valid_to to t2")

        # After repair, meta-history for h_a should show the new valid_to=t2
        meta_a_after = list(
            self.MetaModel.objects.filter(history_object=h_a)
            .order_by("sys_from", "meta_history_id")
        )
        print(f"Meta for A after repair:")
        for m in meta_a_after:
            print(
                f"  valid_to={self._ts(m.valid_to)} "
                f"sys=[{self._ts(m.sys_from)}, {self._ts(m.sys_to)})"
            )

        # At least one meta record should reflect the repaired valid_to
        has_repaired_version = any(m.valid_to == t2 for m in meta_a_after)
        self.assertTrue(
            has_repaired_version,
            "After chain repair, at least one meta record for A must have "
            "valid_to=t2 to reflect the repair.",
        )

        # The old version (valid_to=t1) should be superseded (sys_to set)
        old_versions = [m for m in meta_a_after if m.valid_to == t1]
        for old_v in old_versions:
            self.assertIsNotNone(
                old_v.sys_to,
                "Old meta version (valid_to=t1) must be closed after repair.",
            )

    def test_valid_time_as_of_correct_after_history_deletion(self):
        """
        Verify that valid-time queries on the main model still work
        correctly after a history row deletion + chain repair.

        This is the part the user says works — we verify it stays correct.
        """
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t3 = t2 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="Test")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Test1"
            obj.save()

        with patch("django.utils.timezone.now", return_value=t2):
            obj.name = "Test2"
            obj.save()

        # Delete history for "Test1" at t3
        h_test1 = self.HistoryModel.objects.get(name="Test1")
        with patch("django.utils.timezone.now", return_value=t3):
            h_test1.delete()

        # Valid-time query at various points (on main model)
        # At t0+5min: should be "Test" (gap filled after deletion)
        qs_t0 = get_queryset_as_of(HistDelAsOfModel, t0 + timedelta(minutes=5))
        self.assertEqual(qs_t0.count(), 1)
        self.assertEqual(qs_t0.first().name, "Test")

        # At t1+5min: should STILL be "Test" (Test1 was deleted, gap filled)
        qs_t1 = get_queryset_as_of(HistDelAsOfModel, t1 + timedelta(minutes=5))
        self.assertEqual(qs_t1.count(), 1)
        self.assertEqual(
            qs_t1.first().name,
            "Test",
            "After deleting Test1, the gap should be filled by Test "
            "(valid_to extended to t2).",
        )

        # At t2+5min: should be "Test2"
        qs_t2 = get_queryset_as_of(HistDelAsOfModel, t2 + timedelta(minutes=5))
        self.assertEqual(qs_t2.count(), 1)
        self.assertEqual(qs_t2.first().name, "Test2")

    def test_system_time_shows_pre_and_post_deletion_snapshots(self):
        """
        Full end-to-end test: system-time queries at different moments
        must show different snapshots of the history timeline.

        Before deletion (system-time < t3):
            History knew about 3 rows: Test, Test1, Test2

        After deletion (system-time > t3):
            History knows about 2 rows: Test (extended), Test2
        """
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t3 = t2 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="Test")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Test1"
            obj.save()

        with patch("django.utils.timezone.now", return_value=t2):
            obj.name = "Test2"
            obj.save()

        # Snapshot: system-time between t2 and t3 (before deletion)
        # All three history rows exist, so meta_history should reflect all 3
        qs_pre = get_queryset_as_of(
            self.HistoryModel, t2 + timedelta(minutes=5)
        )
        names_pre = sorted(qs_pre.values_list("name", flat=True))
        print(f"\nPre-deletion system snapshot: {names_pre}")

        # Sanity check: we should see all 3 names
        self.assertEqual(
            len(names_pre), 3,
            f"Before deletion, system should know 3 history rows, got: {names_pre}",
        )

        # Now delete Test1 at t3
        h_test1 = self.HistoryModel.objects.get(name="Test1")
        with patch("django.utils.timezone.now", return_value=t3):
            h_test1.delete()

        self._dump_state("After deletion at t3")

        # System-time query BEFORE deletion (t2 + 5min)
        qs_pre2 = get_queryset_as_of(
            self.HistoryModel, t2 + timedelta(minutes=5)
        )
        names_pre2 = sorted(qs_pre2.values_list("name", flat=True))
        print(f"System-time as_of(pre-deletion): {names_pre2}")

        self.assertIn("Test1", names_pre2,
            "System-time before t3 must still show Test1 existed")
        self.assertEqual(len(names_pre2), 3,
            f"System-time before t3 should show 3 rows: {names_pre2}")

        # System-time query AFTER deletion (t3 + 5min)
        qs_post = get_queryset_as_of(
            self.HistoryModel, t3 + timedelta(minutes=5)
        )
        names_post = sorted(qs_post.values_list("name", flat=True))
        print(f"System-time as_of(post-deletion): {names_post}")

        self.assertNotIn("Test1", names_post,
            "System-time after t3 must NOT show Test1")
        self.assertEqual(len(names_post), 2,
            f"System-time after t3 should show 2 rows: {names_post}")
        self.assertIn("Test", names_post)
        self.assertIn("Test2", names_post)

        # Additionally, the "Test" row in the post-deletion snapshot should
        # reflect the repaired valid_to = t2 (not the old t1)
        test_meta_post = [r for r in qs_post if r.name == "Test"]
        self.assertEqual(len(test_meta_post), 1)
        self.assertEqual(
            test_meta_post[0].valid_to,
            t2,
            "Post-deletion meta for Test should show repaired valid_to=t2",
        )
