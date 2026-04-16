"""
High-level user-story integration tests.

These tests simulate the journeys a real Lex project user would take,
exercising broad code paths across the framework rather than testing
individual functions in isolation.

User stories covered
--------------------
1. **Data entry lifecycle** -- A user creates domain records (LexModel),
   edits them, and the framework auto-tracks who did what and when.

2. **Calculation lifecycle** -- A user triggers a calculation
   (CalculationModel) which transitions NOT_CALCULATED -> IN_PROGRESS
   -> SUCCESS, and the result is persisted.  A failing calculation
   transitions to ERROR with traceback.

3. **Parent-child nested calculation** -- A parent CalculationModel
   triggers a child calculation inside model_logging_context.
   Both must succeed and each should track its own state independently.

4. **Validation guards** -- pre_validation blocks invalid data before
   save; post_validation rolls back the DB if business rules are
   violated after save.  The model must revert to its snapshot.

5. **Recalculation after data change** -- A domain model is updated,
   and a dependent CalculationModel is re-triggered.  The calculation
   re-reads the new data and produces an updated result.

How to run
----------
.. code-block:: bash

    python -m django test lex.tests.integration.test_user_stories \\
        --settings=lex.process_admin.tests.django_test_settings \\
        --verbosity=2 --noinput
"""

import os
from unittest.mock import patch

from django.db import connection, models
from django.test import TransactionTestCase

from lex.api.utils import OperationContext
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.core.exceptions import ValidationError
from lex.core.models.CalculationModel import (
    CalculationModel,
    CalculationModelException,
)
from lex.core.models.LexModel import LexModel, PermissionResult


# ====================================================================
#  Test models -- minimal domain models that mirror a real project
# ====================================================================

class Investor(LexModel):
    """A domain-data model, like Organisation or Vehicle in project_example."""
    name = models.CharField(max_length=200)
    committed_capital = models.FloatField(default=0)

    class Meta:
        app_label = "lex_app"

    def pre_validation(self):
        if self.committed_capital < 0:
            raise ValueError("Committed capital cannot be negative.")


class Fund(LexModel):
    """Another domain model with a post_validation guard."""
    name = models.CharField(max_length=200)
    budget = models.FloatField(default=0)
    budget_limit = models.FloatField(default=100_000)

    class Meta:
        app_label = "lex_app"

    def post_validation(self):
        if self.budget > self.budget_limit:
            raise ValueError(
                f"Budget {self.budget} exceeds limit {self.budget_limit}."
            )


class NAVReport(CalculationModel):
    """
    A report model that reads Investor data and computes a result.
    Mirrors CalculateNAV or InvestorTrackRecord from project_example.
    """
    investor = models.ForeignKey(
        Investor, on_delete=models.CASCADE, null=True, blank=True
    )
    nav_value = models.FloatField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        if self.investor:
            self.nav_value = self.investor.committed_capital * 1.05

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class PortfolioSummary(CalculationModel):
    """
    A parent report that triggers child NAVReports.
    Mirrors RunDemandForecast -> DemandForecast pattern.
    """
    total_nav = models.FloatField(default=0)
    investor_count = models.IntegerField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        investors = Investor.objects.all()
        self.investor_count = investors.count()
        total = 0
        for inv in investors:
            report = NAVReport.objects.filter(investor=inv).first()
            if report is None:
                report = NAVReport.objects.create(
                    investor=inv,
                    is_calculated=CalculationModel.NOT_CALCULATED,
                )
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save(skip_hooks=True)
                report.calculate_hook()
            report.refresh_from_db()
            total += report.nav_value
        self.total_nav = total

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class FailingReport(CalculationModel):
    """A report that always fails -- tests error-recovery path."""
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        raise ValueError("Market data unavailable")

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


# ====================================================================
#  Shared test infrastructure
# ====================================================================

ALL_MODELS = [Investor, Fund, NAVReport, PortfolioSummary, FailingReport]


class UserStoryTestCase(TransactionTestCase):
    """
    Base class that creates/destroys all test model tables and mocks
    external boundaries (Celery, WebSocket, cache).
    """

    _created_tables = []

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        existing = set(connection.introspection.table_names())
        cls._created_tables = []
        with connection.schema_editor() as schema_editor:
            for model in ALL_MODELS:
                if model._meta.db_table not in existing:
                    schema_editor.create_model(model)
                    cls._created_tables.append(model)

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._created_tables:
                with connection.schema_editor() as schema_editor:
                    for model in reversed(cls._created_tables):
                        schema_editor.delete_model(model)
        finally:
            super().tearDownClass()

    def _fixture_teardown(self):
        if connection.vendor == "sqlite":
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA foreign_keys = OFF")
        try:
            super()._fixture_teardown()
        finally:
            if connection.vendor == "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA foreign_keys = ON")

    def setUp(self):
        super().setUp()
        # lex_app is not in INSTALLED_APPS, so TransactionTestCase
        # will not flush our custom tables.  Clean them manually.
        for model in reversed(ALL_MODELS):
            model.objects.all().delete()

        self._env_patch = patch.dict(
            os.environ, {"CELERY_ACTIVE": "False"}, clear=False
        )
        self._env_patch.start()

        self._status_patch = patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        )
        self._status_patch.start()

        self._cache_patch = patch(
            "lex.core.models.CalculationModel.CacheManager"
        )
        mock_cache = self._cache_patch.start()
        mock_cache.cleanup_calculation.return_value = type(
            "R", (), {"success": True}
        )()

        self._store_patch = patch(
            "lex.core.signals.ActiveCalculationStateStore"
            ".ActiveCalculationStateStore"
        )
        mock_store = self._store_patch.start()
        mock_store.get_calculation_id.return_value = None

        self._audit_patch = patch(
            "lex.audit_logging.utils.calculation_audit.ensure_terminal_calculation_audit"
        )
        self._audit_patch.start()

    def tearDown(self):
        self._audit_patch.stop()
        self._store_patch.stop()
        self._cache_patch.stop()
        self._status_patch.stop()
        self._env_patch.stop()
        super().tearDown()


# ====================================================================
# Story 1 -- Data entry lifecycle
#
#   "As a user, I create an Investor record.  The framework
#    automatically sets created_by, edited_by, and timestamps."
# ====================================================================

class TestDataEntryLifecycle(UserStoryTestCase):

    def test_create_investor_sets_metadata(self):
        """
        Creating a domain model via the API sets created_by, edited_by,
        created_at, and edited_at automatically.
        """
        with OperationContext(actor="Portfolio Manager"):
            inv = Investor.objects.create(
                name="Alpha Capital", committed_capital=1_000_000
            )

        inv.refresh_from_db()
        self.assertEqual(inv.name, "Alpha Capital")
        self.assertEqual(inv.committed_capital, 1_000_000)
        self.assertIsNotNone(inv.created_at)
        self.assertIsNotNone(inv.edited_at)
        self.assertEqual(inv.created_by, "Portfolio Manager")
        # edited_by is only set on updates (BEFORE_UPDATE hook), not creates
        self.assertIsNone(inv.edited_by)

    def test_update_investor_updates_edited_fields(self):
        """
        Updating a record changes edited_at and edited_by but preserves
        created_at and created_by.
        """
        with OperationContext(actor="Creator"):
            inv = Investor.objects.create(
                name="Beta Fund", committed_capital=500_000
            )

        original_created_at = inv.created_at
        original_created_by = inv.created_by

        with OperationContext(actor="Editor"):
            inv.committed_capital = 750_000
            inv.save()

        inv.refresh_from_db()
        self.assertEqual(inv.committed_capital, 750_000)
        self.assertEqual(inv.created_at, original_created_at)
        self.assertEqual(inv.created_by, original_created_by)
        self.assertEqual(inv.edited_by, "Editor")

    def test_multiple_investors_tracked_independently(self):
        """
        Each record tracks its own metadata independently.
        """
        with OperationContext(actor="User A"):
            inv1 = Investor.objects.create(
                name="Fund A", committed_capital=100
            )
        with OperationContext(actor="User B"):
            inv2 = Investor.objects.create(
                name="Fund B", committed_capital=200
            )

        self.assertNotEqual(inv1.pk, inv2.pk)
        self.assertEqual(inv1.name, "Fund A")
        self.assertEqual(inv2.name, "Fund B")
        self.assertEqual(Investor.objects.count(), 2)


# ====================================================================
# Story 2 -- Calculation lifecycle (success + failure)
#
#   "As a user, I click Calculate on a report.  The framework
#    transitions the status, runs my calculate() method, and
#    persists the result.  If it fails, I see an ERROR status."
# ====================================================================

class TestCalculationLifecycle(UserStoryTestCase):

    def test_successful_calculation_persists_result(self):
        """
        User story: create an Investor, then trigger a NAVReport.
        The calculate() reads the investor's committed_capital and
        computes nav_value = capital * 1.05.  Status goes to SUCCESS.
        """
        with OperationContext(actor="Analyst"):
            inv = Investor.objects.create(
                name="Gamma LP", committed_capital=1_000_000
            )
            report = NAVReport.objects.create(investor=inv)

        with OperationContext(calculation_id="calc-nav"):
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save(skip_hooks=True)
                report.calculate_hook()

        report.refresh_from_db()
        self.assertEqual(report.is_calculated, CalculationModel.SUCCESS)
        self.assertAlmostEqual(report.nav_value, 1_050_000.0)

    def test_failed_calculation_transitions_to_error(self):
        """
        User story: a report's calculate() raises an exception.
        The framework catches it, sets is_calculated=ERROR, and
        raises CalculationModelException so the caller knows.
        """
        with OperationContext(actor="Analyst"):
            report = FailingReport.objects.create()

        with self.assertRaises(CalculationModelException):
            with OperationContext(calculation_id="calc-fail"):
                with model_logging_context(report):
                    report.is_calculated = CalculationModel.IN_PROGRESS
                    report.save(skip_hooks=True)
                    report.calculate_hook()

        report.refresh_from_db()
        self.assertEqual(report.is_calculated, CalculationModel.ERROR)

    def test_recalculation_updates_result(self):
        """
        User story: after updating the input data, the user re-triggers
        calculation.  The report must pick up the new data.
        """
        with OperationContext(actor="Analyst"):
            inv = Investor.objects.create(
                name="Delta Partners", committed_capital=500_000
            )
            report = NAVReport.objects.create(investor=inv)

        # First calculation
        with OperationContext(calculation_id="calc-1"):
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save(skip_hooks=True)
                report.calculate_hook()

        report.refresh_from_db()
        self.assertAlmostEqual(report.nav_value, 525_000.0)

        # Update the input data
        with OperationContext(actor="Analyst"):
            inv.committed_capital = 2_000_000
            inv.save()

        # Re-trigger calculation
        with OperationContext(calculation_id="calc-2"):
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save(skip_hooks=True)
                report.calculate_hook()

        report.refresh_from_db()
        self.assertEqual(report.is_calculated, CalculationModel.SUCCESS)
        self.assertAlmostEqual(report.nav_value, 2_100_000.0)


# ====================================================================
# Story 3 -- Parent-child nested calculation
#
#   "As a user, I trigger a PortfolioSummary.  It creates and
#    calculates NAVReports for each Investor, then aggregates."
# ====================================================================

class TestNestedCalculation(UserStoryTestCase):

    def test_parent_triggers_child_calculations(self):
        """
        User story: PortfolioSummary.calculate() iterates all Investors,
        creates a NAVReport for each, triggers its calculation inside
        model_logging_context, and sums up the results.
        """
        with OperationContext(actor="PM"):
            Investor.objects.create(name="LP-1", committed_capital=100_000)
            Investor.objects.create(name="LP-2", committed_capital=200_000)
            Investor.objects.create(name="LP-3", committed_capital=300_000)
            summary = PortfolioSummary.objects.create()

        with OperationContext(calculation_id="calc-portfolio"):
            with model_logging_context(summary):
                summary.is_calculated = CalculationModel.IN_PROGRESS
                summary.save(skip_hooks=True)
                summary.calculate_hook()

        summary.refresh_from_db()
        self.assertEqual(summary.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(summary.investor_count, 3)
        # 100k*1.05 + 200k*1.05 + 300k*1.05 = 630k
        self.assertAlmostEqual(summary.total_nav, 630_000.0)

        # All child NAVReports must also be SUCCESS
        for nav in NAVReport.objects.all():
            self.assertEqual(nav.is_calculated, CalculationModel.SUCCESS)
            self.assertGreater(nav.nav_value, 0)

    def test_parent_with_no_investors_produces_zero(self):
        """
        Edge case: no investors exist.  Parent should still succeed
        with zero totals.
        """
        with OperationContext(actor="PM"):
            summary = PortfolioSummary.objects.create()

        with OperationContext(calculation_id="calc-empty"):
            with model_logging_context(summary):
                summary.is_calculated = CalculationModel.IN_PROGRESS
                summary.save(skip_hooks=True)
                summary.calculate_hook()

        summary.refresh_from_db()
        self.assertEqual(summary.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(summary.investor_count, 0)
        self.assertAlmostEqual(summary.total_nav, 0.0)


# ====================================================================
# Story 4 -- Validation guards
#
#   "As a user, I try to save invalid data.  pre_validation blocks
#    the save entirely.  post_validation catches violations that can
#    only be detected after the row is written and rolls back."
# ====================================================================

class TestValidationGuards(UserStoryTestCase):

    def test_pre_validation_blocks_negative_capital(self):
        """
        User story: a user enters negative committed_capital.
        pre_validation() raises ValueError, the framework wraps it in
        ValidationError and blocks the save.
        No row should be written to the database.
        """
        with OperationContext(actor="User"):
            with self.assertRaises(ValidationError) as ctx:
                Investor.objects.create(
                    name="Bad LP", committed_capital=-100
                )
            self.assertIn("negative", str(ctx.exception).lower())

        self.assertEqual(Investor.objects.count(), 0)

    def test_pre_validation_allows_valid_data(self):
        """
        Positive capital passes pre_validation; row is persisted.
        """
        with OperationContext(actor="User"):
            inv = Investor.objects.create(
                name="Good LP", committed_capital=100_000
            )

        self.assertEqual(Investor.objects.count(), 1)
        inv.refresh_from_db()
        self.assertEqual(inv.committed_capital, 100_000)

    def test_post_validation_rolls_back_over_budget(self):
        """
        User story: a Fund row is saved, then post_validation detects
        that budget exceeds budget_limit.  The save is rolled back and
        the field reverts to its previous value.
        """
        with OperationContext(actor="User"):
            fund = Fund.objects.create(
                name="Growth Fund", budget=50_000, budget_limit=100_000
            )

        with OperationContext(actor="User"):
            with self.assertRaises(ValidationError) as ctx:
                fund.budget = 150_000
                fund.save()
            self.assertIn("exceeds limit", str(ctx.exception).lower())

        # The database should still have the original budget
        fund.refresh_from_db()
        self.assertEqual(fund.budget, 50_000)

    def test_post_validation_allows_within_budget(self):
        """
        Budget within limit passes post_validation; update persists.
        """
        with OperationContext(actor="User"):
            fund = Fund.objects.create(
                name="Balanced Fund", budget=30_000, budget_limit=100_000
            )

        with OperationContext(actor="User"):
            fund.budget = 90_000
            fund.save()

        fund.refresh_from_db()
        self.assertEqual(fund.budget, 90_000)


# ====================================================================
# Story 5 -- End-to-end: data change triggers recalculation
#
#   "As a user, I update an Investor's capital.  I then re-trigger
#    the PortfolioSummary.  The new totals reflect the update."
# ====================================================================

class TestDataChangeDrivesRecalculation(UserStoryTestCase):

    def test_updated_investor_reflected_in_portfolio(self):
        """
        Full journey:
        1. Create two Investors.
        2. Trigger PortfolioSummary -> creates child NAVReports -> aggregates.
        3. Update one Investor's capital.
        4. Re-trigger PortfolioSummary -> child NAVReports recalculate.
        5. Assert the new total reflects the change.
        """
        # Step 1: seed data
        with OperationContext(actor="PM"):
            inv1 = Investor.objects.create(
                name="Anchor LP", committed_capital=1_000_000
            )
            inv2 = Investor.objects.create(
                name="Growth LP", committed_capital=500_000
            )
            summary = PortfolioSummary.objects.create()

        # Step 2: first calculation
        with OperationContext(calculation_id="calc-round-1"):
            with model_logging_context(summary):
                summary.is_calculated = CalculationModel.IN_PROGRESS
                summary.save(skip_hooks=True)
                summary.calculate_hook()

        summary.refresh_from_db()
        # (1M + 500k) * 1.05 = 1,575,000
        self.assertAlmostEqual(summary.total_nav, 1_575_000.0)
        self.assertEqual(summary.investor_count, 2)

        # Step 3: update investor
        with OperationContext(actor="PM"):
            inv1.committed_capital = 2_000_000
            inv1.save()

        # Step 4: re-trigger
        with OperationContext(calculation_id="calc-round-2"):
            with model_logging_context(summary):
                summary.is_calculated = CalculationModel.IN_PROGRESS
                summary.save(skip_hooks=True)
                summary.calculate_hook()

        summary.refresh_from_db()
        # (2M + 500k) * 1.05 = 2,625,000
        self.assertAlmostEqual(summary.total_nav, 2_625_000.0)
        self.assertEqual(summary.is_calculated, CalculationModel.SUCCESS)
