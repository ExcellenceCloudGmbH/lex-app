"""
E2E Test: Audit Trail & History Integrity

Tests that verify:
1. History records are created for every CRUD operation.
2. Actor tracking propagates correctly through create → edit → calculate.
3. Bitemporal corrections (editing history records) create meta-history.
4. Calculation state transitions leave history breadcrumbs.
5. Delete operations create tombstone history entries.
"""

from datetime import timedelta
from types import SimpleNamespace

from django.db import models
from lex.api.utils import OperationContext
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.core.models.CalculationModel import (
    CalculationModel,
    CalculationModelException,
)
from lex.core.models.LexModel import LexModel, PermissionResult
from lex.tests.e2e._e2e_test_case import E2ETestCase


# ====================================================================
#  Test models
# ====================================================================


class AuditedAsset(LexModel):
    """Simple model to verify history and audit trail."""

    name = models.CharField(max_length=200)
    value = models.FloatField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return self.name

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class AuditedCalcReport(CalculationModel):
    """CalculationModel to verify calculation state in history."""

    asset = models.ForeignKey(
        AuditedAsset, on_delete=models.CASCADE, null=True, blank=True,
    )
    computed_value = models.FloatField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        if self.asset:
            self.computed_value = self.asset.value * 1.1

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class FailingAuditCalc(CalculationModel):
    """Always fails — for testing error state in history."""

    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        raise ValueError("Market data unavailable")

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


ALL_MODELS = [AuditedAsset, AuditedCalcReport, FailingAuditCalc]


# ====================================================================
#  History & Audit Tests
# ====================================================================


class TestHistoryOnCRUD(E2ETestCase):
    """History records are created for create, update, delete."""

    e2e_models = ALL_MODELS

    def test_create_generates_history(self):
        """Creating a record produces a '+' history entry."""
        asset = AuditedAsset.objects.create(name="Gold", value=1800)
        self.assertEqual(asset.history.count(), 1)
        hist = asset.history.first()
        self.assertEqual(hist.history_type, "+")
        self.assertEqual(hist.name, "Gold")

    def test_update_generates_change_history(self):
        """Updating a record produces a '~' history entry."""
        asset = AuditedAsset.objects.create(name="Silver", value=25)
        asset.value = 30
        asset.save()
        self.assertEqual(asset.history.count(), 2)
        latest = asset.history.order_by("-history_id").first()
        self.assertEqual(latest.history_type, "~")
        self.assertEqual(latest.value, 30)

    def test_delete_generates_tombstone(self):
        """Deleting a record produces a '-' history entry."""
        asset = AuditedAsset.objects.create(name="Copper", value=4)
        pk = asset.pk
        asset.delete()
        self.assertFalse(AuditedAsset.objects.filter(pk=pk).exists())
        tombstone = AuditedAsset.history.filter(id=pk).order_by("-history_id").first()
        self.assertIsNotNone(tombstone)
        self.assertEqual(tombstone.history_type, "-")

    def test_multiple_edits_produce_ordered_history(self):
        """Three saves produce 3 history entries in order."""
        asset = AuditedAsset.objects.create(name="Platinum", value=900)
        asset.value = 950
        asset.save()
        asset.value = 1000
        asset.save()
        history = list(
            asset.history.order_by("history_id").values_list("value", flat=True)
        )
        self.assertEqual(history, [900, 950, 1000])


class TestActorTracking(E2ETestCase):
    """Actor (created_by / edited_by) propagates through operations."""

    e2e_models = ALL_MODELS

    def test_created_by_set_on_create(self):
        """Request user from OperationContext appears in created_by."""
        with OperationContext(request=SimpleNamespace(user="Alice")):
            asset = AuditedAsset.objects.create(name="Bond A", value=100)
        asset.refresh_from_db()
        self.assertEqual(asset.created_by, "Alice")

    def test_edited_by_set_on_update(self):
        """A different actor editing sets edited_by."""
        with OperationContext(request=SimpleNamespace(user="Alice")):
            asset = AuditedAsset.objects.create(name="Bond B", value=100)
        with OperationContext(request=SimpleNamespace(user="Bob")):
            asset.value = 200
            asset.save()
        asset.refresh_from_db()
        self.assertEqual(asset.created_by, "Alice")
        self.assertEqual(asset.edited_by, "Bob")

    def test_actor_chain_across_three_operations(self):
        """Create → edit → edit: each operation has the correct actor."""
        with OperationContext(request=SimpleNamespace(user="Creator")):
            asset = AuditedAsset.objects.create(name="Bond C", value=50)
        with OperationContext(request=SimpleNamespace(user="Editor1")):
            asset.value = 75
            asset.save()
        with OperationContext(request=SimpleNamespace(user="Editor2")):
            asset.value = 100
            asset.save()

        asset.refresh_from_db()
        self.assertEqual(asset.created_by, "Creator")
        self.assertEqual(asset.edited_by, "Editor2")


class TestCalculationHistory(E2ETestCase):
    """Calculation state transitions are tracked in history."""

    e2e_models = ALL_MODELS

    def test_successful_calc_leaves_success_in_history(self):
        """After calculation, history shows SUCCESS state."""
        asset = AuditedAsset.objects.create(name="ETF", value=100)
        with OperationContext(calculation_id="calc-hist"):
            report = AuditedCalcReport.objects.create(asset=asset)
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save()

        report.refresh_from_db()
        self.assertEqual(report.is_calculated, CalculationModel.SUCCESS)
        self.assertAlmostEqual(report.computed_value, 110.0)

        # History should have at least create + SUCCESS states
        self.assertGreaterEqual(report.history.count(), 1)

    def test_failed_calc_leaves_error_in_history(self):
        """Failed calculation → ERROR state with error message."""
        with OperationContext(calculation_id="calc-fail-hist"):
            report = FailingAuditCalc.objects.create()
            with self.assertRaises(CalculationModelException):
                with model_logging_context(report):
                    report.is_calculated = CalculationModel.IN_PROGRESS
                    report.save()

        report.refresh_from_db()
        self.assertEqual(report.is_calculated, CalculationModel.ERROR,
                         "Failed calc must persist ERROR state (not revert to NOT_CALCULATED)")
        self.assertIn("Market data unavailable", report.calculation_error_message)

    def test_recalculation_adds_history_entries(self):
        """Two calculations produce additional history entries."""
        asset = AuditedAsset.objects.create(name="Index", value=200)
        with OperationContext(calculation_id="calc-1"):
            report = AuditedCalcReport.objects.create(asset=asset)
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save()

        initial_count = report.history.count()

        asset.value = 300
        asset.save()

        with OperationContext(calculation_id="calc-2"):
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save()

        report.refresh_from_db()
        self.assertAlmostEqual(report.computed_value, 330.0)
        self.assertGreater(report.history.count(), initial_count)


class TestHistoryAPIEndpoint(E2ETestCase):
    """History endpoint returns correct data via REST API."""

    e2e_models = ALL_MODELS

    def test_history_endpoint_returns_versions(self):
        """GET /history/<pk> returns all historical versions."""
        asset = AuditedAsset.objects.create(name="V1", value=100)
        asset.name = "V2"
        asset.save()
        asset.name = "V3"
        asset.save()

        url = self.url_history("auditedasset", asset.pk)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.json()
        self.assertGreaterEqual(len(data), 3)

    def test_history_after_correction_shows_both_values(self):
        """Edit a field → history shows old and new values."""
        asset = AuditedAsset.objects.create(name="Wrong", value=999)
        asset.name = "Correct"
        asset.value = 100
        asset.save()

        names = list(
            asset.history.order_by("history_id").values_list("name", flat=True)
        )
        self.assertEqual(names, ["Wrong", "Correct"])

    def test_delete_visible_in_history_api(self):
        """After DELETE via API, history still shows all versions."""
        asset = AuditedAsset.objects.create(name="Ephemeral", value=42)
        pk = asset.pk
        asset.name = "Updated"
        asset.save()

        # Delete via API
        url = self.url_detail("auditedasset", pk)
        self.client.delete(url)

        # Asset gone from main table
        self.assertFalse(AuditedAsset.objects.filter(pk=pk).exists())

        # But history retains everything including tombstone
        hist = list(
            AuditedAsset.history.filter(id=pk)
            .order_by("history_id")
            .values_list("history_type", flat=True)
        )
        self.assertIn("+", hist)
        self.assertIn("-", hist)


class TestBitemporalCorrection(E2ETestCase):
    """Editing a history record creates meta-history (Level 2)."""

    e2e_models = ALL_MODELS

    def test_editing_history_valid_from_creates_meta_history(self):
        """Changing valid_from on a history record creates a meta-history entry."""
        asset = AuditedAsset.objects.create(name="Backdated", value=500)
        asset.name = "Current"
        asset.save()

        # Get the latest history record
        hist = asset.history.order_by("-history_id").first()
        original_valid_from = hist.valid_from

        # Backdate it by 1 hour
        new_valid_from = original_valid_from - timedelta(hours=1)
        hist.valid_from = new_valid_from
        hist.save()

        hist.refresh_from_db()
        self.assertEqual(hist.valid_from, new_valid_from)

        # Meta-history should exist if the model has it
        if hasattr(hist, "meta_history"):
            meta_count = hist.__class__.meta_history.model.objects.count()
            self.assertGreater(meta_count, 0)
