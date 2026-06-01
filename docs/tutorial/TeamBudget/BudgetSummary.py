import streamlit as st
import pandas as pd
from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.audit_logging.handlers.LexLogger import LexLogger

from .Team import Team
from .Expense import Expense


class BudgetSummary(CalculationModel):
    """Auto-calculated budget utilization per team per quarter."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    quarter = models.CharField(max_length=10)

    # Calculated fields (populated by calculate())
    total_expenses = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    remaining_budget = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    utilization_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Budget utilization percentage",
    )
    expense_count = models.IntegerField(default=0)
    is_over_budget = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.team.name} — {self.quarter}"

    # ── Calculation ──

    def calculate(self):
        """
        Calculate budget utilization for a team in a given quarter.
        Called when the user clicks "Calculate" in the UI.
        """
        logger = LexLogger()

        # Query all expenses for this team and quarter
        expenses = Expense.objects.filter(
            employee__team=self.team,
            quarter=self.quarter,
        )

        # Compute totals
        self.total_expenses = expenses.aggregate(
            total=models.Sum("amount")
        )["total"] or 0
        self.expense_count = expenses.count()
        self.remaining_budget = self.team.budget - self.total_expenses
        self.utilization_pct = (
            (self.total_expenses / self.team.budget * 100)
            if self.team.budget > 0
            else 0
        )
        self.is_over_budget = self.total_expenses > self.team.budget

        # ── Log the results ──

        logger.add_heading(
            f"Budget Report: {self.team.name} — {self.quarter}"
        )

        logger.add_table(
            headers=["Metric", "Value"],
            rows=[
                ["Total Expenses", f"€{self.total_expenses:,.2f}"],
                ["Team Budget", f"€{self.team.budget:,.2f}"],
                ["Remaining", f"€{self.remaining_budget:,.2f}"],
                ["Utilization", f"{self.utilization_pct:.1f}%"],
                ["# of Expenses", str(self.expense_count)],
            ],
        )

        # Breakdown by category
        category_data = (
            expenses.values("category")
            .annotate(total=models.Sum("amount"))
            .order_by("-total")
        )
        if category_data:
            logger.add_heading("Breakdown by Category", level=2)
            logger.add_table(
                headers=["Category", "Amount"],
                rows=[
                    [row["category"], f"€{row['total']:,.2f}"]
                    for row in category_data
                ],
            )

        # Over-budget warning
        if self.is_over_budget:
            logger.add_text(
                f"⚠️ OVER BUDGET by €{abs(self.remaining_budget):,.2f}!"
            )

        logger.log()

    # ── Streamlit: Table-level dashboard ──

    @classmethod
    def streamlit_class_main(cls):
        """Company-wide budget overview — all teams at a glance."""
        st.title("📊 Company Budget Overview")

        summaries = cls.objects.select_related("team").filter(
            is_calculated="SUCCESS"
        )

        if not summaries.exists():
            st.warning(
                "No budget summaries calculated yet. "
                "Run calculations first."
            )
            return

        # Build DataFrame
        data = []
        for s in summaries:
            data.append({
                "Team": s.team.name,
                "Quarter": s.quarter,
                "Budget": float(s.team.budget),
                "Spent": float(s.total_expenses),
                "Remaining": float(s.remaining_budget),
                "Utilization %": float(s.utilization_pct),
                "Status": "🔴 Over" if s.is_over_budget else "🟢 OK",
            })
        df = pd.DataFrame(data)

        # Summary metrics
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Budget", f"€{df['Budget'].sum():,.0f}")
        col2.metric("Total Spent", f"€{df['Spent'].sum():,.0f}")
        col3.metric(
            "Overall Utilization",
            f"{(df['Spent'].sum() / df['Budget'].sum() * 100):.1f}%",
        )

        # Bar chart
        st.subheader("Budget vs Actual by Team")
        chart_df = df.groupby("Team")[["Budget", "Spent"]].sum()
        st.bar_chart(chart_df)

        # Data table
        st.subheader("Detailed Breakdown")
        st.dataframe(df, use_container_width=True)

    # ── Streamlit: Record-level dashboard ──

    def streamlit_main(self, user=None):
        """Single team's budget detail with expense breakdown."""
        st.title(f"📋 {self.team.name} — {self.quarter}")

        # Key metrics
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Expenses", f"€{self.total_expenses:,.2f}")
        col2.metric("Remaining Budget", f"€{self.remaining_budget:,.2f}")
        col3.metric(
            "Utilization",
            f"{self.utilization_pct:.1f}%",
            delta="Over Budget!" if self.is_over_budget else None,
            delta_color="inverse",
        )

        # Breakdown by category
        expenses = Expense.objects.filter(
            employee__team=self.team,
            quarter=self.quarter,
        )

        if expenses.exists():
            st.subheader("Expenses by Category")
            cat_data = (
                expenses.values("category")
                .annotate(total=models.Sum("amount"))
                .order_by("-total")
            )
            cat_df = pd.DataFrame(cat_data)
            cat_df.columns = ["Category", "Amount"]
            st.bar_chart(cat_df.set_index("Category"))

            st.subheader("All Expenses")
            exp_data = expenses.values(
                "description", "amount", "category", "date",
                "employee__first_name", "employee__last_name",
            ).order_by("-date")
            exp_df = pd.DataFrame(exp_data)
            exp_df.columns = [
                "Description", "Amount", "Category", "Date",
                "First Name", "Last Name",
            ]
            exp_df["Submitted By"] = (
                exp_df["First Name"] + " " + exp_df["Last Name"]
            )
            exp_df = exp_df.drop(columns=["First Name", "Last Name"])
            st.dataframe(exp_df, use_container_width=True)
        else:
            st.info("No expenses recorded for this period.")
