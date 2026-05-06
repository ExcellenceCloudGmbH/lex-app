"""
Tests for programmatic record creation.

Verifies that ``objects.create()``, ``object.save()``, and ``bulk_create()``
correctly trigger the full bitemporal pipeline:
  Level 1:  History record with valid_from / valid_to
  Level 2:  MetaHistory record with sys_from / sys_to
  Main table synchronisation
"""

import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, models
from django.test import TransactionTestCase

from api.utils import OperationContext
from core.models.LexModel import LexModel
from process_admin.utils.model_registration import ModelRegistration


def project_timestamp(year, month, day, hour, minute=0, second=0):
    if settings.USE_TZ:
        return datetime.datetime(
            year, month, day, hour, minute, second, tzinfo=datetime.timezone.utc,
        )

    configured_tz = ZoneInfo(getattr(settings, "TIME_ZONE", "UTC"))
    return datetime.datetime(
        year, month, day, hour, minute, second, tzinfo=configured_tz,
    ).replace(tzinfo=None)


# ── Test Model ──────────────────────────────────────────────────────
class ProgrammaticTestModel(LexModel):
    name = models.CharField(max_length=100)
    value = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"


class ProgrammaticCreationTest(TransactionTestCase):
    """Verify history is created for various programmatic creation paths."""

    def setUp(self):
        from simple_history.models import registered_models

        mr = ModelRegistration()

        if ProgrammaticTestModel in registered_models:
            del registered_models[ProgrammaticTestModel]

        try:
            mr._register_standard_model(ProgrammaticTestModel, [])
        except Exception:
            pass  # already registered

        self.HistoryModel = ProgrammaticTestModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        tables = connection.introspection.table_names()
        with connection.schema_editor() as schema_editor:
            for model in [ProgrammaticTestModel, self.HistoryModel, self.MetaModel]:
                if model._meta.db_table in tables:
                    schema_editor.delete_model(model)
                schema_editor.create_model(model)

    def tearDown(self):
        from simple_history.models import registered_models

        if ProgrammaticTestModel in registered_models:
            del registered_models[ProgrammaticTestModel]

        with connection.schema_editor() as schema_editor:
            for model in [self.MetaModel, self.HistoryModel, ProgrammaticTestModel]:
                try:
                    schema_editor.delete_model(model)
                except Exception:
                    pass

    # ────────────────────────────────────────────────────────────────
    # Test: objects.create()
    # ────────────────────────────────────────────────────────────────

    def test_create_generates_history(self):
        """objects.create() should produce 1 history + 1 meta-history record."""
        T0 = datetime.datetime(2025, 6, 1, 10, 0, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            obj = ProgrammaticTestModel.objects.create(name="alpha", value=42)

        # ── Main table ──
        self.assertEqual(ProgrammaticTestModel.objects.count(), 1)
        self.assertEqual(ProgrammaticTestModel.objects.first().name, "alpha")

        # ── History (Level 1) ──
        h_records = list(self.HistoryModel.objects.filter(id=obj.pk))
        self.assertEqual(len(h_records), 1, "Expected exactly 1 history record")

        h = h_records[0]
        self.assertEqual(h.name, "alpha")
        self.assertEqual(h.value, 42)
        self.assertEqual(h.history_type, "+")
        self.assertTrue(abs((h.valid_from - T0).total_seconds()) < 1.0)
        self.assertIsNone(h.valid_to, "First record should have valid_to=None (∞)")

        # ── MetaHistory (Level 2) ──
        m_records = list(self.MetaModel.objects.filter(history_object=h))
        self.assertGreaterEqual(len(m_records), 1, "Expected at least 1 meta record")

        m = m_records[0]
        self.assertTrue(abs((m.sys_from - T0).total_seconds()) < 1.0)
        self.assertIsNone(m.sys_to, "First meta record should have sys_to=None")

    # ────────────────────────────────────────────────────────────────
    # Test: instance.save() (manual creation)
    # ────────────────────────────────────────────────────────────────

    def test_save_generates_history(self):
        """Manual obj = Model(); obj.save() should also produce history."""
        T0 = datetime.datetime(2025, 6, 1, 11, 0, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            obj = ProgrammaticTestModel(name="beta", value=7)
            obj.save()

        # ── History ──
        h_records = list(self.HistoryModel.objects.filter(id=obj.pk))
        self.assertEqual(len(h_records), 1)
        self.assertEqual(h_records[0].name, "beta")
        self.assertEqual(h_records[0].history_type, "+")

        # ── Meta ──
        m_records = list(self.MetaModel.objects.filter(history_object=h_records[0]))
        self.assertGreaterEqual(len(m_records), 1)

    # ────────────────────────────────────────────────────────────────
    # Test: objects.create() then .save() (update)
    # ────────────────────────────────────────────────────────────────

    def test_update_generates_second_history(self):
        """Updating a record should create a second history entry of type '~'."""
        T0 = datetime.datetime(2025, 6, 1, 12, 0, 0)
        T1 = datetime.datetime(2025, 6, 1, 12, 5, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            obj = ProgrammaticTestModel.objects.create(name="gamma", value=1)

        with patch("django.utils.timezone.now", return_value=T1):
            obj.name = "gamma_updated"
            obj.value = 2
            obj.save()

        h_records = list(
            self.HistoryModel.objects.filter(id=obj.pk).order_by("valid_from")
        )
        self.assertEqual(len(h_records), 2, "Expected 2 history records (create + update)")

        # First record: created
        self.assertEqual(h_records[0].name, "gamma")
        self.assertEqual(h_records[0].history_type, "+")
        self.assertTrue(abs((h_records[0].valid_from - T0).total_seconds()) < 1.0)
        # Chaining: valid_to of first = valid_from of second
        self.assertTrue(abs((h_records[0].valid_to - T1).total_seconds()) < 1.0)

        # Second record: updated
        self.assertEqual(h_records[1].name, "gamma_updated")
        self.assertEqual(h_records[1].history_type, "~")
        self.assertTrue(abs((h_records[1].valid_from - T1).total_seconds()) < 1.0)
        self.assertIsNone(h_records[1].valid_to, "Latest record should be open-ended")

    # ────────────────────────────────────────────────────────────────
    # Test: bulk_create()
    # ────────────────────────────────────────────────────────────────

    def test_bulk_create_generates_history_by_default(self):
        """
        bulk_create() should preserve LexModel history semantics by default.
        """
        T0 = datetime.datetime(2025, 6, 1, 14, 0, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            objs = ProgrammaticTestModel.objects.bulk_create([
                ProgrammaticTestModel(name="delta", value=10),
                ProgrammaticTestModel(name="epsilon", value=20),
                ProgrammaticTestModel(name="zeta", value=30),
            ])

        self.assertEqual(ProgrammaticTestModel.objects.count(), 3)
        for obj in objs:
            self.assertIsNotNone(obj.pk, "bulk_create should return objects with PKs")

        self.assertEqual(
            self.HistoryModel.objects.count(),
            3,
            "Default bulk_create() should create history for each object",
        )
        self.assertGreaterEqual(
            self.MetaModel.objects.count(),
            3,
            "Default bulk_create() should create meta-history for each object",
        )

    @unittest.skip("bulk_create(with_history=True) not yet implemented — revisit")
    def test_bulk_create_with_history_generates_history(self):
        """
        bulk_create(with_history=True) should remain an explicit history-preserving alias.
        """
        T0 = datetime.datetime(2025, 6, 1, 14, 0, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            objs = ProgrammaticTestModel.objects.bulk_create([
                ProgrammaticTestModel(name="delta", value=10),
                ProgrammaticTestModel(name="epsilon", value=20),
                ProgrammaticTestModel(name="zeta", value=30),
            ], with_history=True)

        # ── Main table ──
        self.assertEqual(ProgrammaticTestModel.objects.count(), 3)

        # ── All 3 should have PKs assigned ──
        for obj in objs:
            self.assertIsNotNone(obj.pk, "bulk_create should return objects with PKs")

        # ── History (Level 1) — each object should have 1 record ──
        h_count = self.HistoryModel.objects.count()
        self.assertEqual(h_count, 3, "Each bulk-created object should have 1 history record")

        for obj in objs:
            h = self.HistoryModel.objects.filter(id=obj.pk)
            self.assertEqual(h.count(), 1, f"Object {obj.name} should have 1 history record")
            self.assertEqual(h.first().name, obj.name)
            self.assertEqual(h.first().history_type, "+")

        # ── MetaHistory (Level 2) — each history record should have a meta record ──
        m_count = self.MetaModel.objects.count()
        self.assertGreaterEqual(m_count, 3, "Each history record should have at least 1 meta record")

    def test_bulk_create_skip_history(self):
        """
        skip_history=True should use raw bulk_create without history.
        """
        T0 = datetime.datetime(2025, 6, 1, 14, 30, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            objs = ProgrammaticTestModel.objects.bulk_create(
                [
                    ProgrammaticTestModel(name="raw_a", value=100),
                    ProgrammaticTestModel(name="raw_b", value=200),
                ],
                skip_history=True,
            )

        # Main table has the objects
        self.assertEqual(ProgrammaticTestModel.objects.count(), 2)

        # No history should exist (raw bulk_create bypasses signals)
        h_count = self.HistoryModel.objects.count()
        self.assertEqual(h_count, 0, "skip_history=True should NOT create history records")

    # ────────────────────────────────────────────────────────────────
    # Test: Multiple creates for same model
    # ────────────────────────────────────────────────────────────────

    def test_multiple_creates(self):
        """Creating multiple independent objects should each get their own history chain."""
        T0 = datetime.datetime(2025, 6, 1, 15, 0, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            obj_a = ProgrammaticTestModel.objects.create(name="A", value=1)
            obj_b = ProgrammaticTestModel.objects.create(name="B", value=2)

        h_a = list(self.HistoryModel.objects.filter(id=obj_a.pk))
        h_b = list(self.HistoryModel.objects.filter(id=obj_b.pk))

        self.assertEqual(len(h_a), 1, "Object A should have 1 history record")
        self.assertEqual(len(h_b), 1, "Object B should have 1 history record")
        self.assertEqual(h_a[0].name, "A")
        self.assertEqual(h_b[0].name, "B")

        # Each should have independent meta records
        m_a = self.MetaModel.objects.filter(history_object=h_a[0]).count()
        m_b = self.MetaModel.objects.filter(history_object=h_b[0]).count()
        self.assertGreaterEqual(m_a, 1)
        self.assertGreaterEqual(m_b, 1)

    # ────────────────────────────────────────────────────────────────
    # Test: Delete creates history with type '-'
    # ────────────────────────────────────────────────────────────────

    def test_delete_generates_deletion_history(self):
        """Deleting an object should create a deletion marker in history."""
        T0 = datetime.datetime(2025, 6, 1, 16, 0, 0)
        T1 = datetime.datetime(2025, 6, 1, 16, 5, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            obj = ProgrammaticTestModel.objects.create(name="to_delete", value=99)
        pk = obj.pk

        with patch("django.utils.timezone.now", return_value=T1):
            obj.delete()

        # Main table should be empty
        self.assertEqual(ProgrammaticTestModel.objects.filter(pk=pk).count(), 0)

        # History should contain both create (+) and delete (-) records
        h_records = list(
            self.HistoryModel.objects.filter(id=pk).order_by("valid_from", "history_id")
        )
        self.assertGreaterEqual(len(h_records), 2, "Expected at least create + delete records")

        types = [r.history_type for r in h_records]
        self.assertIn("+", types, "Should have a creation record")
        self.assertIn("-", types, "Should have a deletion record")

    def test_create_ignores_explicit_created_by_override(self):
        """created_by is immutable - explicit values are always overwritten by the system."""
        obj = ProgrammaticTestModel.objects.create(
            name="manual_created_by",
            value=1,
            created_by="Streamlit Override",
        )

        # The system should overwrite the manual value with the fallback
        self.assertEqual(obj.created_by, "Initial Data Upload")
        self.assertEqual(
            ProgrammaticTestModel.objects.get(pk=obj.pk).created_by,
            "Initial Data Upload",
        )

    def test_update_ignores_explicit_edited_by_override(self):
        """edited_by is immutable - explicit values are always overwritten by the system."""
        obj = ProgrammaticTestModel.objects.create(name="manual_edited_by", value=1)

        obj.value = 2
        obj.edited_by = "Streamlit Override"
        obj.save()

        # The system should overwrite the manual value with the fallback
        self.assertEqual(obj.edited_by, "Initial Data Upload")
        self.assertEqual(
            ProgrammaticTestModel.objects.get(pk=obj.pk).edited_by,
            "Initial Data Upload",
        )

    def test_create_sets_created_at_only(self):
        created_ts = project_timestamp(2025, 6, 1, 17, 0, 0)

        with patch("core.models.LexModel.lex_datetime_now", return_value=created_ts):
            obj = ProgrammaticTestModel.objects.create(name="timestamped", value=1)

        self.assertEqual(obj.created_at, created_ts)
        self.assertIsNone(obj.edited_at)

        stored = ProgrammaticTestModel.objects.get(pk=obj.pk)
        self.assertEqual(stored.created_at, created_ts)
        self.assertIsNone(stored.edited_at)

    def test_update_refreshes_edited_at_without_changing_created_at(self):
        created_ts = project_timestamp(2025, 6, 1, 18, 0, 0)
        edited_ts = project_timestamp(2025, 6, 1, 18, 5, 0)

        with patch("core.models.LexModel.lex_datetime_now", return_value=created_ts):
            obj = ProgrammaticTestModel.objects.create(name="timestamp_update", value=1)

        with patch("core.models.LexModel.lex_datetime_now", return_value=edited_ts):
            obj.value = 2
            obj.save()

        self.assertEqual(obj.created_at, created_ts)
        self.assertEqual(obj.edited_at, edited_ts)

        stored = ProgrammaticTestModel.objects.get(pk=obj.pk)
        self.assertEqual(stored.created_at, created_ts)
        self.assertEqual(stored.edited_at, edited_ts)

    def test_explicit_timestamp_overrides_are_preserved(self):
        explicit_created_ts = project_timestamp(2024, 1, 1, 8, 0, 0)
        explicit_edited_ts = project_timestamp(2024, 1, 1, 9, 0, 0)
        later_ts = project_timestamp(2025, 6, 1, 19, 0, 0)

        with patch("core.models.LexModel.lex_datetime_now", return_value=later_ts):
            obj = ProgrammaticTestModel.objects.create(
                name="manual_timestamps",
                value=1,
                created_at=explicit_created_ts,
                edited_at=explicit_edited_ts,
            )

        self.assertEqual(obj.created_at, explicit_created_ts)
        self.assertEqual(obj.edited_at, explicit_edited_ts)

        manual_edit_ts = project_timestamp(2025, 6, 1, 19, 5, 0)
        with patch("core.models.LexModel.lex_datetime_now", return_value=later_ts):
            obj.value = 2
            obj.edited_at = manual_edit_ts
            obj.save()

        self.assertEqual(obj.created_at, explicit_created_ts)
        self.assertEqual(obj.edited_at, manual_edit_ts)

    def test_request_context_populates_audit_actor(self):
        """OperationContext with request user correctly populates audit fields."""
        request = SimpleNamespace(user="Request User")

        with OperationContext(request=request):
            obj = ProgrammaticTestModel.objects.create(
                name="request_actor_create",
                value=1,
            )

        self.assertEqual(obj.created_by, "Request User")

    def test_explicit_created_by_is_overwritten_by_request_context(self):
        """Even with explicit created_by, the request user always wins."""
        request = SimpleNamespace(user="Request User")

        with OperationContext(request=request):
            obj = ProgrammaticTestModel.objects.create(
                name="explicit_ignored_on_create",
                value=1,
                created_by="Manual Actor",
            )

        self.assertEqual(obj.created_by, "Request User")

    def test_explicit_edited_by_is_overwritten_by_request_context(self):
        """Even with explicit edited_by, the request user always wins."""
        request = SimpleNamespace(user="Request User")

        with OperationContext(request=request):
            obj = ProgrammaticTestModel.objects.create(
                name="explicit_ignored_on_update",
                value=1,
            )

        with OperationContext(request=request):
            obj.value = 2
            obj.edited_by = "Manual Actor"
            obj.save()

        self.assertEqual(obj.edited_by, "Request User")
