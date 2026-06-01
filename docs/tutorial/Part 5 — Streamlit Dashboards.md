---
title: "Part 5 — Streamlit Dashboards"
---

In this part, you'll add two interactive dashboards to `BudgetSummary` — a company-wide overview and a per-team deep-dive. Both are added directly to your model file in `Reports/`, using [Streamlit](https://docs.streamlit.io/) — see [[features/access-and-ui/streamlit dashboards]] for the full guide.

| Dashboard | Level | What It Shows |
|---|---|---|
| **Company Overview** | Table-level (`streamlit_class_main`) | All teams' budgets side-by-side |
| **Team Deep-Dive** | Record-level (`streamlit_main`) | One team's expenses in detail |

## Add the Table-Level Dashboard

Add this class method to your `BudgetSummary` class (after `calculate()`):

> [!important]
> Import `streamlit` **inside** the Streamlit methods, not at the top of the file. Your model file is loaded by Django at startup — when Streamlit isn't running. A top-level `import streamlit as st` would crash.

> [!note]
> The snippets below append to `BudgetSummary.py`. They assume the imports you already added in [[tutorial/Part 3 — Calculations & Logging|Part 3]] are still at the top of the file:
> ```python
> import pandas as pd
> from django.db import models
> from Input.Expense import Expense
> ```
> If any of these are missing, add them now so the new code doesn't raise `NameError`.

```python title="Reports/BudgetSummary.py"
    @classmethod
    def streamlit_class_main(cls):
        """Company-wide budget overview — all teams at a glance."""
        import streamlit as st

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
```

> [!tip]
> `streamlit_class_main` is a `@classmethod` — it shows when users open the [Streamlit](https://docs.streamlit.io/) page for the BudgetSummary table (no specific record selected). We recommend using it for aggregate overviews and `streamlit_main` for per-record detail views.

## Add the Record-Level Dashboard

Add this instance method below `streamlit_class_main`:

```python title="Reports/BudgetSummary.py"
    def streamlit_main(self, user=None):
        """Single team's budget detail with expense breakdown."""
        import streamlit as st

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
```

## Try It Out

Select **"Streamlit"** from the run configuration dropdown in PyCharm → click ▶️.

> [!note]- Terminal alternative
> **Linux / macOS:**
> ```bash
> set -a; source .env; set +a
> lex streamlit
> ```
> **Windows PowerShell:**
> ```powershell
> lex streamlit
> ```

In the frontend, navigate to **Reports → BudgetSummary** and click the [Streamlit](https://docs.streamlit.io/) icon in the toolbar (not on a specific record) to see the company-wide overview. Click the icon on a specific record for the team deep-dive.


## Checkpoint

At this point you have:
- Company-wide budget dashboard with charts
- Per-team deep-dive with expense breakdowns
- Everything interactive — [Streamlit](https://docs.streamlit.io/) updates in real time

Next up: [[tutorial/Part 6 — History in Action|Part 6 — History in Action]] — the final part, where you'll explore the bitemporal audit trail.
