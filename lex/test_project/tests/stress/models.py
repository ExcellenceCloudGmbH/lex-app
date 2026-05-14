"""
Shared models for Cluster 11 — Stress & Performance.

Dedicated to high-volume tests (SMALL=500, MEDIUM=5k, LARGE=20k). Do
not reuse the lightweight cluster 2/5/7 models — stress data would
pollute their per-test counts.

All permissions are wide-open: Cluster 11 tests *throughput*, not
access control (covered in cluster 4). A non-trivial
``permission_read`` override on :class:`StressInvoice` deliberately
exists so scenario 11.14 can verify the framework calls it exactly
once per list request, not once per row.
"""

from __future__ import annotations

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel, PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all(
        "cluster 11: stress"
    )
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all(
        "cluster 11: stress"
    )
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class StressCounterparty(LexModel):
    """Small FK target — ~200 rows — referenced by StressInvoice."""

    name = models.CharField(max_length=200)
    country = models.CharField(max_length=2, default="SE")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name


@_permissive
class StressPeriod(LexModel):
    """
    Bitemporal period row. 20k of these spread across rolling 12-month
    windows exercise the period-calc path (11.7 / 11.8 / 11.9).
    """

    label = models.CharField(max_length=64, db_index=True)
    period_from = models.DateField(db_index=True)
    period_to = models.DateField(db_index=True)

    class Meta:
        app_label = "lex_app"
        indexes = [
            models.Index(fields=["period_from", "period_to"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return self.label


class StressInvoice(LexModel):
    """
    Wide row: the stress-test workhorse. Mirrors the shape of a
    realistic customer invoice table — 15 fields, one FK, indexed
    ``booked_on`` and ``period`` for the filter / period scenarios.

    Permissions: ``permission_read`` is a real override (not the
    ``_permissive`` factory) so scenario 11.14 can observe the
    per-request invocation count.
    """

    # Identifying
    invoice_number = models.CharField(max_length=64, db_index=True)
    counterparty = models.ForeignKey(
        StressCounterparty,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    period = models.ForeignKey(
        StressPeriod,
        on_delete=models.PROTECT,
        related_name="invoices",
    )

    # Dates
    booked_on = models.DateField(db_index=True)
    due_on = models.DateField()

    # Amounts
    amount_net = models.DecimalField(max_digits=14, decimal_places=2)
    amount_tax = models.DecimalField(max_digits=14, decimal_places=2)
    amount_gross = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="EUR")

    # Classification
    category = models.CharField(max_length=32, default="standard", db_index=True)
    sub_category = models.CharField(max_length=32, blank=True, default="")

    # Narrative
    description = models.TextField(blank=True, default="")
    memo = models.TextField(blank=True, default="")

    # Flags / bookkeeping
    is_posted = models.BooleanField(default=False, db_index=True)
    is_reversed = models.BooleanField(default=False)

    class Meta:
        app_label = "lex_app"
        indexes = [
            models.Index(fields=["booked_on", "category"]),
            models.Index(fields=["period", "is_posted"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return self.invoice_number

    # Scenario 11.14 watches this — it must run once per list request,
    # not once per row. A classmethod-level counter would hide that; we
    # rely on ``unittest.mock.patch`` over this method in the test.
    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 11: stress")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 11: stress")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


@_permissive
class PeriodAggregateCalc(CalculationModel):
    """
    Aggregates :class:`StressInvoice` rows inside a given
    :class:`StressPeriod`. Writes ``total_net`` / ``total_gross`` and
    the invoice count. One instance per period — the test seeds 12 of
    these for the all-periods run (11.8).

    ``calculate()`` must hit the DB **once**, not once per invoice —
    the whole point of 11.7 is catching the regression where someone
    loops ``StressInvoice.objects.filter(period=self.period)``
    row-by-row.
    """

    period = models.OneToOneField(
        StressPeriod,
        on_delete=models.CASCADE,
        related_name="aggregate",
    )
    total_net = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
    )
    total_gross = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
    )
    invoice_count = models.IntegerField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return f"agg({self.period_id})"

    def calculate(self):
        from django.db.models import Count, Sum

        agg = StressInvoice.objects.filter(period=self.period).aggregate(
            n=Count("id"),
            net=Sum("amount_net"),
            gross=Sum("amount_gross"),
        )
        self.invoice_count = agg["n"] or 0
        self.total_net = agg["net"] or 0
        self.total_gross = agg["gross"] or 0


@_permissive
class DependentPeriodCalc(CalculationModel):
    """
    Depends on :class:`PeriodAggregateCalc` outputs for the previous
    3 periods — gives scenario 11.9 real dependency depth without
    making each leaf an expensive calc.

    ``calculate()`` must resolve each underlying period *once*, not
    once per dependent. A regression where each DependentPeriodCalc
    re-fetches the same 3 aggregates ``n`` times would turn 11.9's
    runtime from ``O(periods)`` to ``O(periods^2)``.
    """

    period = models.OneToOneField(
        StressPeriod,
        on_delete=models.CASCADE,
        related_name="dependent",
    )
    trailing_3_net = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
    )
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return f"dep({self.period_id})"

    def calculate(self):
        # Deliberately written the efficient way: one query ordered by
        # valid_from, sliced. If someone rewrites this as a per-period
        # loop, 11.9's query-count budget catches it.
        prior = (
            PeriodAggregateCalc.objects
            .filter(period__period_from__lt=self.period.period_from)
            .order_by("-period__period_from")
            .values_list("total_net", flat=True)[:3]
        )
        self.trailing_3_net = sum(prior) if prior else 0


ALL_MODELS = [
    StressCounterparty,
    StressPeriod,
    StressInvoice,
    PeriodAggregateCalc,
    DependentPeriodCalc,
]

COUNTERPARTY = "stresscounterparty"
PERIOD = "stressperiod"
INVOICE = "stressinvoice"
AGGREGATE = "periodaggregatecalc"
DEPENDENT = "dependentperiodcalc"


# ── FK-heavy export fixture ────────────────────────────────────────
#
# Scenario set 11.16–11.20 exercises FK fan-out specifically: 25k
# rows, each with 4 FK relationships. Small FK targets keep the join
# cheap individually; what we measure is whether the framework keeps
# the join *count* bounded (1 eager JOIN, not 4 × n SELECTs) and
# whether iteration is memory-safe at 25k × 4 FKs materialized.
#
# These models live in their own table set so they don't collide with
# the main StressInvoice fixtures — the column layout and FK count
# are the point.


@_permissive
class FKHeavyCategory(LexModel):
    """Small FK target — ~20 rows. Simulates a 'chart-of-accounts' join."""

    code = models.CharField(max_length=16, db_index=True)
    label = models.CharField(max_length=200)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.code


@_permissive
class FKHeavyCurrency(LexModel):
    """Small FK target — ~5 rows. ISO-4217-style currency reference."""

    code = models.CharField(max_length=3, db_index=True)
    symbol = models.CharField(max_length=4, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.code


class FKHeavyInvoice(LexModel):
    """
    25k-row export target with four FK relationships.

    Each instance references :class:`StressCounterparty`,
    :class:`StressPeriod`, :class:`FKHeavyCategory`, and
    :class:`FKHeavyCurrency`. Four joins is a realistic "wide row on
    steroids" — enough to make the framework's naive path (one SELECT
    per FK per row) visibly catastrophic at volume while the
    documented path (``select_related`` + ``.iterator()``) stays
    bounded.

    Permissions are trivial allow-all — cluster 11 is about throughput,
    and BUG-011 already has a dedicated permission-fanout test
    (11.14).
    """

    invoice_number = models.CharField(max_length=64, db_index=True)
    counterparty = models.ForeignKey(
        StressCounterparty,
        on_delete=models.CASCADE,
        related_name="fk_heavy_invoices",
    )
    period = models.ForeignKey(
        StressPeriod,
        on_delete=models.PROTECT,
        related_name="fk_heavy_invoices",
    )
    category = models.ForeignKey(
        FKHeavyCategory,
        on_delete=models.PROTECT,
        related_name="fk_heavy_invoices",
    )
    currency = models.ForeignKey(
        FKHeavyCurrency,
        on_delete=models.PROTECT,
        related_name="fk_heavy_invoices",
    )

    booked_on = models.DateField(db_index=True)
    amount_net = models.DecimalField(max_digits=14, decimal_places=2)
    amount_gross = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.invoice_number

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 11: fk-heavy")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 11: fk-heavy")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


FK_HEAVY_MODELS = [
    StressCounterparty,
    StressPeriod,
    FKHeavyCategory,
    FKHeavyCurrency,
    FKHeavyInvoice,
]

FK_HEAVY_INVOICE = "fkheavyinvoice"






