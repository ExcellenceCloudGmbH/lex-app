"""Models for Cluster 1 — Project Bootstrap & maintenance commands.

``IncidentDatetimeItem`` exercises the ``rebase_incident_datetimes`` management
command. It carries a user-entered ``event_at`` (the kind of field the incident
corrupted) alongside the LexModel-managed ``created_at`` / ``edited_at``, so a
test can prove the command re-anchors the former and leaves the latter untouched.

Rule #3: no cross-cluster imports — models live here and are imported by this
cluster's test modules only.
"""

from __future__ import annotations

from django.db import models

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel, PermissionResult


class IncidentDatetimeItem(LexModel):
    """A customer-shaped model: a user datetime plus managed timestamps."""

    name = models.CharField(max_length=100)
    # User-entered instant — the class of field the TIME_ZONE incident corrupted.
    event_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "lex_app"


ALL_MODELS = [IncidentDatetimeItem]
INCIDENT = "incidentdatetimeitem"


# -- A calculation behind a lex.ui calculation page (cluster 1ef-1ei) -----------------------------


class UiRegionReport(CalculationModel):
    """A report over one region and year, as a lex.ui calculation page starts it.

    ``calculate()`` logs a line, counts the region's orders and writes one line per customer
    (``UiRegionReportLine``). Test controls, reset by every test that uses them: ``gate`` holds
    the run open so a test can watch it in progress; ``may_create`` / ``may_read`` stand for the
    model's permissions (a user who may not start one, or may not see another's run); ``with_file``
    makes the run write a CSV into ``report_file``.
    """

    region = models.CharField(max_length=20)
    year = models.IntegerField()
    orders = models.IntegerField(null=True, blank=True)
    revenue = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    calculation_error_message = models.TextField(blank=True, default="")
    report_file = models.FileField(upload_to="ui_region_reports/", null=True, blank=True)

    gate = None
    may_create = True
    may_read = True
    with_file = False

    class Meta:
        app_label = "lex_app"
        verbose_name = "region report"

    def __str__(self) -> str:
        return f"{self.region} {self.year}"

    def permission_create(self, user_context):
        return type(self).may_create

    def permission_read(self, user_context):
        if type(self).may_read:
            return PermissionResult.allow_all("cluster 1ef")
        return PermissionResult.deny_all("cluster 1ef")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("cluster 1ef")

    def permission_list(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True

    def calculate(self):
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_heading("Orders", level=2).add_text(f"Reading the orders of {self.region}.").log()
        if type(self).gate is not None:
            type(self).gate.wait(timeout=10)
        if self.region == "nowhere":
            raise ValueError(f"There is no region called {self.region}.")
        self.orders = 3
        self.revenue = "1250.50"
        for customer in ("Acme", "Beta"):
            UiRegionReportLine.objects.create(report=self, customer=customer, amount="625.25")
        if type(self).with_file:
            from django.core.files.base import ContentFile

            self.report_file.save(f"region-{self.region}-{self.year}.csv",
                                  ContentFile(b"customer,amount\nAcme,625.25\nBeta,625.25\n"), save=False)


class UiRegionReportLine(LexModel):
    """One customer's line of a region report: what a calculation page shows as a related table."""

    report = models.ForeignKey(UiRegionReport, on_delete=models.CASCADE, related_name="lines")
    customer = models.CharField(max_length=50)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        app_label = "lex_app"

    def permission_read(self, user_context):
        return PermissionResult.allow_all("cluster 1ef")

    def permission_list(self, user_context):
        return True


UI_CALC_MODELS = [UiRegionReport, UiRegionReportLine]
UI_REGION_REPORT = "uiregionreport"


# -- A calculation whose input is a foreign key (cluster 1es) ---------------------------------------


class UiCustomer(LexModel):
    """A customer a report is about: its name is what a foreign key to it shows."""

    name = models.CharField(max_length=50)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        return self.name

    def permission_read(self, user_context):
        return PermissionResult.allow_all("cluster 1es")

    def permission_list(self, user_context):
        return True


class UiCustomerReport(CalculationModel):
    """A report over one customer and year: its customer is a foreign key."""

    customer = models.ForeignKey(UiCustomer, on_delete=models.CASCADE, related_name="reports")
    year = models.IntegerField()

    class Meta:
        app_label = "lex_app"

    def permission_create(self, user_context):
        return True

    def permission_read(self, user_context):
        return PermissionResult.allow_all("cluster 1es")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("cluster 1es")

    def permission_list(self, user_context):
        return True

    def calculate(self):
        return None


UI_FK_MODELS = [UiCustomer, UiCustomerReport]
