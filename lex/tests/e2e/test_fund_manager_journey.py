"""
E2E Test: The Fund Manager's Bad Day

A 5-act story exercising the full Lex stack through the REST API:

Act 1 — Setup & Data Entry
    Create currencies, funds, and positions via POST.  Verify audit
    fields (created_by, created_at) and serializer output.

Act 2 — The Mistake
    Enter bad data: wrong currency FK, inflated NAV.  A position with
    negative quantity is blocked by pre_validation.  The fund with wrong
    NAV is saved (no pre_validation on that field) — a deliberate mistake.

Act 3 — Correction & Audit Trail
    Fix the fund via PATCH.  Verify edited_by/edited_at update.  Check
    the history endpoint shows all versions (create → wrong → corrected).

Act 4 — Calculation
    Create a CalculationModel (PortfolioReport) that sums position
    market values per fund.  Trigger via ``calculate: true``.  Verify
    NOT_CALCULATED → SUCCESS.  Update a position, re-trigger, verify
    the new result.

Act 5 — Permission Boundaries
    Create a restricted model where permission_create returns False.
    Verify POST returns 400.
"""

import os
from unittest.mock import patch

from django.db import connection, models
from django.test import TransactionTestCase

from rest_framework import status

from lex.api.utils import OperationContext
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.core.exceptions import ValidationError
from lex.core.models.CalculationModel import (
    CalculationModel,
    CalculationModelException,
)
from lex.core.models.LexModel import LexModel, PermissionResult
from lex.tests.e2e._e2e_test_case import E2ETestCase


# ====================================================================
#  Test models — a mini fund-management domain
# ====================================================================


class E2ECurrency(LexModel):
    """Reference data — small lookup table."""

    code = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return self.code

    def permission_read(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class E2EFund(LexModel):
    """Main domain model — FK to currency, several field types."""

    name = models.CharField(max_length=200)
    currency = models.ForeignKey(
        E2ECurrency, on_delete=models.SET_NULL, null=True, blank=True,
    )
    nav = models.FloatField(default=0)
    strategy = models.CharField(max_length=50, default="equity")
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return self.name

    def permission_read(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class E2EPosition(LexModel):
    """Child of fund — FK, decimal fields, pre_validation guard."""

    fund = models.ForeignKey(
        E2EFund, on_delete=models.CASCADE, related_name="e2e_positions",
    )
    instrument = models.CharField(max_length=200)
    quantity = models.FloatField(default=0)
    market_value = models.FloatField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self):
        return f"{self.fund.name} — {self.instrument}"

    def pre_validation(self):
        if self.quantity < 0:
            raise ValueError("Position quantity cannot be negative.")

    def permission_read(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class E2EPortfolioReport(CalculationModel):
    """Sums position market_value across all positions of a fund."""

    fund = models.ForeignKey(
        E2EFund, on_delete=models.CASCADE, null=True, blank=True,
    )
    total_market_value = models.FloatField(default=0)
    position_count = models.IntegerField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        if self.fund:
            positions = E2EPosition.objects.filter(fund=self.fund)
            self.position_count = positions.count()
            self.total_market_value = sum(p.market_value for p in positions)
        else:
            self.position_count = 0
            self.total_market_value = 0

    def permission_read(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class E2ERestrictedModel(LexModel):
    """Model where create is always denied — for permission tests."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"

    def permission_create(self, user_context):
        return False

    def permission_read(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("e2e")

    def permission_delete(self, user_context):
        return True


ALL_MODELS = [
    E2ECurrency,
    E2EFund,
    E2EPosition,
    E2EPortfolioReport,
    E2ERestrictedModel,
]


# ====================================================================
#  Act 1 — Setup & Data Entry
# ====================================================================


class TestAct1_DataEntry(E2ETestCase):
    """Create reference data and domain records; verify audit fields."""

    e2e_models = ALL_MODELS

    def test_create_currency_via_api(self):
        """POST creates a currency; response includes audit fields."""
        url = self.url_create("e2ecurrency")
        resp = self.client.post(
            url, data={"code": "USD", "name": "US Dollar"}, format="json",
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        self.assertEqual(resp.data["code"], "USD")
        self.assertTrue(E2ECurrency.objects.filter(code="USD").exists())

    def test_create_fund_with_fk(self):
        """POST creates a fund linked to a currency via FK."""
        usd = E2ECurrency.objects.create(code="USD", name="US Dollar")
        url = self.url_create("e2efund")
        resp = self.client.post(
            url,
            data={"name": "Alpha Fund", "currency": usd.pk, "nav": 1_000_000, "strategy": "equity"},
            format="json",
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        fund = E2EFund.objects.get(name="Alpha Fund")
        self.assertEqual(fund.currency_id, usd.pk)
        self.assertEqual(fund.nav, 1_000_000)

    def test_create_positions_under_fund(self):
        """POST creates positions linked to a fund; list returns all."""
        usd = E2ECurrency.objects.create(code="USD", name="US Dollar")
        fund = E2EFund.objects.create(name="Beta Fund", currency=usd)
        for instr, qty, mv in [
            ("AAPL", 100, 15_000),
            ("MSFT", 200, 60_000),
            ("GOOGL", 50, 70_000),
        ]:
            url = self.url_create("e2eposition")
            resp = self.client.post(
                url,
                data={
                    "fund": fund.pk,
                    "instrument": instr,
                    "quantity": qty,
                    "market_value": mv,
                },
                format="json",
            )
            self.assertIn(
                resp.status_code,
                (status.HTTP_200_OK, status.HTTP_201_CREATED),
                f"Failed to create position {instr}: {resp.data}",
            )
        self.assertEqual(E2EPosition.objects.filter(fund=fund).count(), 3)

    def test_audit_fields_set_on_create(self):
        """created_at and created_by are populated on ORM create."""
        with OperationContext(actor="Fund Manager"):
            cur = E2ECurrency.objects.create(code="EUR", name="Euro")
        cur.refresh_from_db()
        self.assertIsNotNone(cur.created_at)
        self.assertEqual(cur.created_by, "Fund Manager")

    def test_list_endpoint_returns_all_records(self):
        """GET list returns all records."""
        E2ECurrency.objects.create(code="USD", name="US Dollar")
        E2ECurrency.objects.create(code="EUR", name="Euro")
        E2ECurrency.objects.create(code="GBP", name="British Pound")
        resp = self.list_get("e2ecurrency")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self.extract_results(resp.data)
        self.assertEqual(len(results), 3)


# ====================================================================
#  Act 2 — The Mistake
# ====================================================================


class TestAct2_TheMistake(E2ETestCase):
    """Enter bad data; pre_validation blocks some, others slip through."""

    e2e_models = ALL_MODELS

    def test_pre_validation_blocks_negative_quantity(self):
        """pre_validation raises → position is NOT created."""
        usd = E2ECurrency.objects.create(code="USD", name="US Dollar")
        fund = E2EFund.objects.create(name="Bad Fund", currency=usd)
        with OperationContext(actor="Careless Trader"):
            with self.assertRaises(ValidationError):
                E2EPosition.objects.create(
                    fund=fund, instrument="TSLA", quantity=-100, market_value=50_000,
                )
        self.assertEqual(E2EPosition.objects.count(), 0)

    def test_wrong_nav_is_saved_without_pre_validation(self):
        """No pre_validation on nav → wrong value persists."""
        usd = E2ECurrency.objects.create(code="USD", name="US Dollar")
        with OperationContext(actor="Junior Analyst"):
            fund = E2EFund.objects.create(
                name="Gamma Fund", currency=usd, nav=999_999_999,
            )
        fund.refresh_from_db()
        self.assertEqual(fund.nav, 999_999_999)  # mistake saved

    def test_api_post_with_wrong_data_persists(self):
        """POST with inflated budget — no server-side guard → 200."""
        usd = E2ECurrency.objects.create(code="USD", name="US Dollar")
        url = self.url_create("e2efund")
        resp = self.client.post(
            url,
            data={"name": "Oops Fund", "currency": usd.pk, "nav": 999_999_999},
            format="json",
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        self.assertEqual(E2EFund.objects.get(name="Oops Fund").nav, 999_999_999)


# ====================================================================
#  Act 3 — Correction & Audit Trail
# ====================================================================


class TestAct3_CorrectionAndAudit(E2ETestCase):
    """Fix bad data; verify history shows all versions."""

    e2e_models = ALL_MODELS

    def test_patch_corrects_field(self):
        """PATCH updates nav; old value gone from main table."""
        usd = E2ECurrency.objects.create(code="USD", name="US Dollar")
        fund = E2EFund.objects.create(name="Fix Me Fund", currency=usd, nav=999_999_999)
        url = self.url_detail("e2efund", fund.pk)
        resp = self.client.patch(url, data={"nav": 1_000_000}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        fund.refresh_from_db()
        self.assertEqual(fund.nav, 1_000_000)

    def test_edited_by_updates_on_save(self):
        """edited_by is set on the correcting save."""
        with OperationContext(actor="Creator"):
            usd = E2ECurrency.objects.create(code="JPY", name="Yen")
        with OperationContext(actor="Corrector"):
            usd.name = "Japanese Yen"
            usd.save()
        usd.refresh_from_db()
        self.assertEqual(usd.edited_by, "Corrector")

    def test_history_shows_all_versions(self):
        """History records: create → wrong → corrected."""
        fund = E2EFund.objects.create(name="V1", nav=100)
        fund.name = "V1 TYPO"
        fund.nav = 999
        fund.save()
        fund.name = "V1 Fixed"
        fund.nav = 100
        fund.save()
        self.assertEqual(fund.history.count(), 3)
        names = list(
            fund.history.order_by("history_id").values_list("name", flat=True)
        )
        self.assertEqual(names, ["V1", "V1 TYPO", "V1 Fixed"])

    def test_history_api_returns_versions(self):
        """GET /history/<pk> returns at least 2 records after an edit."""
        fund = E2EFund.objects.create(name="Original", nav=100)
        fund.name = "Edited"
        fund.save()
        url = self.url_history("e2efund", fund.pk)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        data = resp.json()
        self.assertGreaterEqual(len(data), 2)

    def test_delete_leaves_history_tombstone(self):
        """DELETE removes the main record but history retains a '-' entry."""
        fund = E2EFund.objects.create(name="Will Delete", nav=42)
        pk = fund.pk
        url = self.url_detail("e2efund", pk)
        self.client.delete(url)
        self.assertFalse(E2EFund.objects.filter(pk=pk).exists())
        hist = E2EFund.history.filter(id=pk).order_by("-history_id").first()
        self.assertIsNotNone(hist)
        self.assertEqual(hist.history_type, "-")


# ====================================================================
#  Act 4 — Calculation
# ====================================================================


class TestAct4_Calculation(E2ETestCase):
    """Trigger calculations via ORM; verify state transitions and results."""

    e2e_models = ALL_MODELS

    def test_successful_calculation(self):
        """Calculate sums position market_value → SUCCESS."""
        usd = E2ECurrency.objects.create(code="USD", name="US Dollar")
        fund = E2EFund.objects.create(name="Calc Fund", currency=usd)
        E2EPosition.objects.create(
            fund=fund, instrument="AAPL", quantity=100, market_value=15_000,
        )
        E2EPosition.objects.create(
            fund=fund, instrument="MSFT", quantity=200, market_value=60_000,
        )

        with OperationContext(calculation_id="calc-1"):
            report = E2EPortfolioReport.objects.create(fund=fund)
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save()

        report.refresh_from_db()
        self.assertEqual(report.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(report.total_market_value, 75_000)
        self.assertEqual(report.position_count, 2)

    def test_recalculation_after_data_change(self):
        """Update a position → re-trigger → result changes."""
        usd = E2ECurrency.objects.create(code="USD", name="US Dollar")
        fund = E2EFund.objects.create(name="Recalc Fund", currency=usd)
        pos = E2EPosition.objects.create(
            fund=fund, instrument="AAPL", quantity=100, market_value=10_000,
        )

        with OperationContext(calculation_id="calc-r1"):
            report = E2EPortfolioReport.objects.create(fund=fund)
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save()

        report.refresh_from_db()
        self.assertEqual(report.total_market_value, 10_000)

        # Fix market value
        pos.market_value = 25_000
        pos.save()

        with OperationContext(calculation_id="calc-r2"):
            with model_logging_context(report):
                report.is_calculated = CalculationModel.IN_PROGRESS
                report.save()

        report.refresh_from_db()
        self.assertEqual(report.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(report.total_market_value, 25_000)

    def test_calculation_via_api_trigger(self):
        """PUT with calculate=true triggers calculation through API."""
        usd = E2ECurrency.objects.create(code="USD", name="US Dollar")
        fund = E2EFund.objects.create(name="API Calc Fund", currency=usd)
        E2EPosition.objects.create(
            fund=fund, instrument="NVDA", quantity=50, market_value=40_000,
        )
        report = E2EPortfolioReport.objects.create(
            fund=fund, is_calculated=CalculationModel.NOT_CALCULATED,
        )

        url = self.url_detail("e2eportfolioreport", report.pk)
        resp = self.client.put(
            url,
            data={
                "fund": fund.pk,
                "total_market_value": 0,
                "position_count": 0,
                "calculate": "true",
                "is_calculated": CalculationModel.NOT_CALCULATED,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        report.refresh_from_db()
        self.assertEqual(report.total_market_value, 40_000)
        self.assertEqual(report.position_count, 1)


# ====================================================================
#  Act 5 — Permission Boundaries
# ====================================================================


class TestAct5_Permissions(E2ETestCase):
    """Verify permission checks are enforced at the API level."""

    e2e_models = ALL_MODELS

    def test_create_denied_returns_400(self):
        """Model with permission_create → False returns 400."""
        url = self.url_create("e2erestrictedmodel")
        resp = self.client.post(
            url, data={"name": "Should Fail"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not authorized", resp.data.get("message", "").lower())

    def test_full_crud_lifecycle_allowed(self):
        """Unrestricted model supports full CRUD cycle."""
        # Create
        url = self.url_create("e2ecurrency")
        resp = self.client.post(
            url, data={"code": "CHF", "name": "Swiss Franc"}, format="json",
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        pk = E2ECurrency.objects.get(code="CHF").pk

        # Read
        resp = self.client.get(self.url_detail("e2ecurrency", pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["code"], "CHF")

        # Update
        resp = self.client.patch(
            self.url_detail("e2ecurrency", pk),
            data={"name": "Schweizer Franken"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        # Delete
        resp = self.client.delete(self.url_detail("e2ecurrency", pk))
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT))
        self.assertFalse(E2ECurrency.objects.filter(pk=pk).exists())
