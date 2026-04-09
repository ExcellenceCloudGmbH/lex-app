"""
Tests for editing history records (data-field changes, not valid_from/to).

Scenarios tested
----------------
1.  Editing the name of the *currently-valid* history record updates the
    main table.
2.  Editing the name of a *non-current* (closed) history record does NOT
    change the main table.
3.  System-time as_of correctly shows the pre-edit and post-edit snapshots
    after a data-field edit on a history record.
4.  Multiple sequential edits each produce a new meta-history version.
"""

from unittest.mock import patch
from datetime import timedelta
import datetime

from django.db import models, connection
from django.test import TransactionTestCase

from lex.core.models.LexModel import LexModel
from lex.core.services.Bitemporal import get_queryset_as_of


# ── Test model ──────────────────────────────────────────────────────

class HistEditModel(LexModel):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


# ── Test suite ──────────────────────────────────────────────────────

class BitemporalHistoryEditTest(TransactionTestCase):
    """Test data-field edits on Level-1 history records."""

    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration

        mr = ModelRegistration()
        try:
            mr._register_standard_model(HistEditModel, [])
        except Exception:
            pass

        self.HistoryModel = HistEditModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        with connection.schema_editor() as editor:
            editor.create_model(HistEditModel)
            editor.create_model(self.HistoryModel)
            editor.create_model(self.MetaModel)

    def tearDown(self):
        with connection.schema_editor() as editor:
            for m in (self.MetaModel, self.HistoryModel, HistEditModel):
                try:
                    editor.delete_model(m)
                except Exception:
                    pass

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _ts(self, dt):
        if dt is None:
            return "∞"
        return dt.strftime("%H:%M:%S")

    def _dump(self, label):
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print(f"{'=' * 60}")
        print("  Main Table:")
        for obj in HistEditModel.objects.all():
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
    # Test 1: Editing current history updates main table
    # ----------------------------------------------------------------

    def test_edit_current_history_updates_main_table(self):
        """
        If the currently-valid history record's name is changed directly
        on the H-table, the main table must reflect the new name.

        Timeline:
            t0  Create "Alpha"
            t1  Edit h-record name → "Alpha-Fixed" (history row is still
                the currently-valid one; only name changed, not valid_from)
        """
        t0 = datetime.datetime(2025, 7, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)

        # ── t0: Create ──
        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistEditModel.objects.create(name="Alpha")

        self._dump("After t0: Create 'Alpha'")

        h_alpha = self.HistoryModel.objects.get(name="Alpha")
        self.assertIsNone(h_alpha.valid_to)  # currently valid

        # ── t1: Edit name directly on history row ──
        with patch("django.utils.timezone.now", return_value=t1):
            h_alpha.name = "Alpha-Fixed"
            h_alpha.save()

        self._dump("After t1: Edit h-record name → 'Alpha-Fixed'")

        # Main table must reflect the edit
        obj.refresh_from_db()
        self.assertEqual(
            obj.name,
            "Alpha-Fixed",
            "Main table must be updated when the currently-valid history "
            "record's data field is edited.",
        )

    # ----------------------------------------------------------------
    # Test 2: Editing non-current history does NOT change main table
    # ----------------------------------------------------------------

    def test_edit_non_current_history_does_not_change_main_table(self):
        """
        If a *closed* (expired) history record is edited, the main table
        must stay unchanged — it should still reflect the currently-valid
        history record.

        Timeline:
            t0  Create "Alpha"
            t1  Update → "Beta" (closes Alpha at t1)
            t2  Edit h_alpha name → "Alpha-Corrected" (Alpha is expired)
        """
        t0 = datetime.datetime(2025, 7, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistEditModel.objects.create(name="Alpha")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Beta"
            obj.save()

        self._dump("After t1: Update → 'Beta'")

        # Main table is "Beta"
        obj.refresh_from_db()
        self.assertEqual(obj.name, "Beta")

        # Now edit the *expired* h_alpha
        h_alpha = self.HistoryModel.objects.get(name="Alpha")
        self.assertIsNotNone(h_alpha.valid_to)  # confirmed expired

        with patch("django.utils.timezone.now", return_value=t2):
            h_alpha.name = "Alpha-Corrected"
            h_alpha.save()

        self._dump("After t2: Edit expired h_alpha → 'Alpha-Corrected'")

        # Main table must still be "Beta"
        obj.refresh_from_db()
        self.assertEqual(
            obj.name,
            "Beta",
            "Main table must NOT change when an expired history record "
            "is edited — the currently-valid record is still 'Beta'.",
        )

    # ----------------------------------------------------------------
    # Test 3: System-time as_of after a data edit
    # ----------------------------------------------------------------

    def test_system_time_as_of_after_edit(self):
        """
        After editing a history record's name at t2, a system-time query
        before t2 must return the OLD name and after t2 the NEW name.

        Timeline:
            t0  Create "Alpha"   → h1 valid=[t0, ∞)
            t1  Update → "Beta"  → h1 valid=[t0, t1), h2 valid=[t1, ∞)
            t2  Edit h2.name → "Beta-Fixed" (direct history edit)

        System-time expectations:
            as_of(between t1 and t2) → should see "Beta"  (pre-edit)
            as_of(after t2)          → should see "Beta-Fixed" (post-edit)
        """
        t0 = datetime.datetime(2025, 7, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t_pre_edit = t1 + timedelta(minutes=5)    # between t1 and t2
        t_post_edit = t2 + timedelta(minutes=5)   # after t2

        # ── t0: Create ──
        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistEditModel.objects.create(name="Alpha")

        # ── t1: Update ──
        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Beta"
            obj.save()

        self._dump("After t1: Update → 'Beta'")

        h_beta = self.HistoryModel.objects.get(name="Beta")

        # Count meta records before edit
        meta_count_before = self.MetaModel.objects.count()
        print(f"\nMeta count before edit: {meta_count_before}")

        # ── t2: Edit h_beta name directly ──
        with patch("django.utils.timezone.now", return_value=t2):
            h_beta.name = "Beta-Fixed"
            h_beta.save()

        self._dump("After t2: Edit h_beta → 'Beta-Fixed'")

        meta_count_after = self.MetaModel.objects.count()
        print(f"Meta count after edit: {meta_count_after}")

        # ── KEY ASSERTION: A new meta version must have been created ──
        self.assertGreater(
            meta_count_after,
            meta_count_before,
            "Editing a history record's data fields must create a new "
            "meta-history version to preserve the audit trail.",
        )

        # ── System-time as_of: pre-edit ──
        qs_pre = get_queryset_as_of(self.HistoryModel, t_pre_edit)
        names_pre = sorted([m.name for m in qs_pre])
        print(f"\nSystem-time as_of({self._ts(t_pre_edit)}): {names_pre}")
        self.assertIn(
            "Beta",
            names_pre,
            "Pre-edit system-time snapshot must show the original name 'Beta'.",
        )
        self.assertNotIn(
            "Beta-Fixed",
            names_pre,
            "Pre-edit system-time snapshot must NOT contain 'Beta-Fixed'.",
        )

        # ── System-time as_of: post-edit ──
        qs_post = get_queryset_as_of(self.HistoryModel, t_post_edit)
        names_post = sorted([m.name for m in qs_post])
        print(f"System-time as_of({self._ts(t_post_edit)}): {names_post}")
        self.assertIn(
            "Beta-Fixed",
            names_post,
            "Post-edit system-time snapshot must show 'Beta-Fixed'.",
        )
        self.assertNotIn(
            "Beta",
            names_post,
            "Post-edit system-time snapshot must NOT contain old name 'Beta' "
            "for that history row.",
        )

    # ----------------------------------------------------------------
    # Test 4: Multiple sequential edits each produce a meta version
    # ----------------------------------------------------------------

    def test_multiple_edits_produce_meta_versions(self):
        """
        Each data-field edit on the same history row must produce a
        distinct meta-history version, creating a complete audit trail.

        Timeline:
            t0  Create "V1"
            t1  Edit → "V2"
            t2  Edit → "V3"
            t3  Edit → "V4"

        Expected: 4 meta versions for this history row (one per state).
        """
        t0 = datetime.datetime(2025, 7, 1, 10, 0, 0)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistEditModel.objects.create(name="V1")

        h = self.HistoryModel.objects.get(name="V1")

        for i, (name, minutes) in enumerate([
            ("V2", 10), ("V3", 20), ("V4", 30)
        ], start=1):
            t = t0 + timedelta(minutes=minutes)
            with patch("django.utils.timezone.now", return_value=t):
                h.name = name
                h.save()
            h.refresh_from_db()

        self._dump("After 3 edits: V1 → V2 → V3 → V4")

        # Get all meta versions for this history row
        meta_versions = list(
            self.MetaModel.objects.filter(
                history_object=h,
            ).order_by("sys_from", "meta_history_id")
        )

        print(f"\nMeta versions for h (history_id={h.history_id}):")
        for m in meta_versions:
            print(
                f"  meta_id={m.meta_history_id}  name={m.name}  "
                f"sys=[{self._ts(m.sys_from)}, {self._ts(m.sys_to)})  "
                f"type={m.meta_history_type}"
            )

        # Each edit should have produced a new meta version
        self.assertGreaterEqual(
            len(meta_versions),
            4,
            f"Expected at least 4 meta versions (V1, V2, V3, V4) but got "
            f"{len(meta_versions)}: {[m.name for m in meta_versions]}",
        )

        # The names in chronological order should be V1, V2, V3, V4
        meta_names = [m.name for m in meta_versions]
        self.assertEqual(
            meta_names,
            ["V1", "V2", "V3", "V4"],
            f"Meta versions should track each edit: {meta_names}",
        )

        # Only the last version should be open (sys_to=None)
        for m in meta_versions[:-1]:
            self.assertIsNotNone(
                m.sys_to,
                f"Meta version for '{m.name}' should be closed (sys_to set) "
                f"because it was superseded.",
            )
        self.assertIsNone(
            meta_versions[-1].sys_to,
            "The latest meta version should be open (sys_to=None).",
        )
