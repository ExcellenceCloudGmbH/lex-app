---
title: Dashboards on Your Models
---

The quickest dashboard to write is one that belongs to a model. You add a method, and the application decides when to show it — no routing, no separate app, no extra process to configure beyond the Streamlit server itself.

**[Northwind Analytics](https://github.com/ExcellenceCloudGmbH/DemoNorthwindAnalytics) has one:** `Fund.streamlit_main` in `Input/Fund.py`, which is what the Analytics tab renders when you open a fund. Its dashboard's *Record and table dashboards* page walks through it.

There are two of them, and the difference is what the method receives:

| Level | Method | Receives | Where it appears |
|---|---|---|---|
| **Table-level** | `streamlit_class_main(cls)` | the model class | the chart icon on the grid toolbar |
| **Record-level** | `streamlit_main(self)` | one record | the [[using-the-app/record-detail/analytics tab\|Analytics tab]] on the record page |

Reach for the table-level one for aggregates — summaries, charts across all records, a filtered table. Reach for the record-level one for detail: this record's history, its related data, a drill-down.

## Both, in one file

Both are ordinary methods on an ordinary [[model-your-data/index|LexModel]]. Here is one file with one of each:

```python title="Expense.py"
import pandas as pd
import streamlit as st
from django.db import models

from lex.core.models.LexModel import LexModel


class Quarter(LexModel):
    name = models.CharField(max_length=50)
    budget = models.DecimalField(max_digits=12, decimal_places=2)

    def streamlit_main(self, user=None):
        """Record-level: the Analytics tab on one quarter."""
        st.header(f"Dashboard: {self.name}")

        spend = pd.DataFrame(self.expenses.values("category", "amount"))

        left, right = st.columns(2)
        with left:
            st.metric("Total spent", f"€{spend['amount'].sum():,.2f}")
        with right:
            st.metric("Remaining", f"€{self.budget - spend['amount'].sum():,.2f}")

        st.bar_chart(spend.groupby("category")["amount"].sum())


class Expense(LexModel):
    quarter = models.ForeignKey(Quarter, on_delete=models.CASCADE, related_name="expenses")
    category = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()

    @classmethod
    def streamlit_class_main(cls):
        """Table-level: the chart icon above the Expense grid."""
        st.header("Expense overview")

        spend = pd.DataFrame(cls.objects.values("category", "amount"))

        st.bar_chart(spend.groupby("category")["amount"].sum())
        st.dataframe(spend)
```

`streamlit_main` takes an optional `user` — the signed-in user, if you need to vary what the dashboard shows by who is looking at it. `streamlit_class_main` is a `@classmethod` and takes nothing but the class.

> [!warning] Import what you use
> A dashboard that builds a `DataFrame` without importing `pandas` fails at the
> moment someone opens it — `name 'pd' is not defined`, rendered inside the tab
> — not when the application starts, which is where you would be looking.
>
> Top of the file or inside the method both work. Importing inside the method
> keeps Django's start-up from loading Streamlit for an application that may
> never serve a dashboard; at the top of the file is easier to keep track of.
> Neither placement crashes anything.

## Where they show up

- **Record-level** — the [[using-the-app/record-detail/analytics tab|Analytics tab]] on the record detail page, scoped to that record.
- **Table-level** — the toggle on the grid toolbar, for the whole model.

Neither appears unless the Streamlit server is running and `IS_STREAMLIT_ENABLED` is set; see [[reference/Environment Variables]]. If the server is unavailable, the UI shows a fallback with a "Retry Connection" button and the rest of the application keeps working.

See [[using-the-app/record-detail/analytics tab|Analytics Tab]] for what the reader of a dashboard sees.

## Tips

- `st.cache_data` on expensive queries keeps a dashboard responsive; a record-level dashboard is re-run on every interaction.
- `st.columns()` for side-by-side layouts.
- Any Streamlit widget works — `st.plotly_chart()`, `st.map()`, `st.selectbox()`, and the rest.
- A record-level dashboard has the whole record in `self`, so related models are one `related_name` away.
- Keep the queries narrow. `values(...)` of the two columns you are charting beats loading every field of every row.

## Related

- [[access-and-dashboards/streamlit/standalone dashboards|Standalone Dashboards]] — a dashboard that is not about a single model
- [[access-and-dashboards/streamlit/embedding app controls|Embedding App Controls]] — putting the real Calculate button on one of these
