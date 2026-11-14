---
title: Dashboards on Your Models
---

The quickest dashboard to write is one that belongs to a model. You add a method, and the application decides when to show it — no routing, no separate app, no extra process to configure beyond the Streamlit server itself.

**[Northwind Analytics](https://github.com/ExcellenceCloudGmbH/DemoNorthwindAnalytics) has one:** `Fund.streamlit_main` in `Input/Fund.py`, which is what the Analytics tab renders when you open a fund. Run the project and open any fund's Analytics tab to see it.

There are two of them, and the difference is what the method receives:

| Level | Method | Receives | Where it appears |
|---|---|---|---|
| **Table-level** | `streamlit_class_main(cls)` | the model class | the chart icon on the grid toolbar |
| **Record-level** | `streamlit_main(self, user)` | one record, and the viewer | the [[using-the-app/record-detail/analytics tab\|Analytics tab]] on the record page |

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

`streamlit_main` is called with one argument, `user`: the viewer's claims from Keycloak, a dict with `sub`, `email` and `preferred_username` — not a Django user. Keep the parameter even when you do not use it: a `streamlit_main(self)` without it fails with a `TypeError`, shown in the tab. `streamlit_class_main` is a `@classmethod` and is called with nothing but the class. Either can read the viewer, their Keycloak permissions and their access token from `st.session_state` — see [[access-and-dashboards/streamlit/sessions and authentication#Who is signed in|Who is signed in]]. Their ORM queries are not narrowed to what the viewer may see: the ORM reads as the dashboard.

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

## What the reader sees

| URL | Shows |
|---|---|
| `?model=fund&pk=42` | fund 42's `streamlit_main(user)` |
| `?model=fund` | `Fund.streamlit_class_main()` |
| `?model=fund&pk=42`, and `Fund` defines no `streamlit_main` | *No instance-level visualization available for this model.* |
| `?model=fund`, and `Fund` defines no `streamlit_class_main` | *No class-level visualization available for this model.* |
| `?model=nope` | *❌ Model 'nope' not found* |
| `?model=fund&pk=999`, with no such fund | *❌ Object with ID 999 not found* |
| `?model=…&pk=…` for a plain Django model, not a `LexModel` | *❌ This model doesn't support visualization* |
| `?model=…` for a plain Django model | *❌ This model doesn't support class-level visualization* |
| `streamlit_main` or `streamlit_class_main` raises | *❌ Error:* and the exception's message, on one line — no traceback |

`model` is matched case-insensitively against the names of the project's own models. The Reflex dashboards show similar messages for a missing model, record or dashboard method, without the ❌ and without an error row of this kind — see [[access-and-dashboards/reflex/dashboards on your models#What the reader sees|What the reader sees]].

The project's own dashboard — `main()` in `_streamlit_structure.py` — is not wrapped like this. An exception from it is raised to Streamlit, which shows it on the page, unless `DEPLOYMENT_ENVIRONMENT` is exactly `PROD`: then the page shows *An error occurred while trying to load the app.*, which opens to the traceback. A `_streamlit_structure.py` that fails to import shows nothing at all: your part of the page stays blank, and nothing is logged.

## Tips

- `st.cache_data` on expensive queries keeps a dashboard responsive; a record-level dashboard is re-run on every interaction.
- `st.columns()` for side-by-side layouts.
- Any Streamlit widget works — `st.plotly_chart()`, `st.map()`, `st.selectbox()`, and the rest.
- A record-level dashboard has the whole record in `self`, so related models are one `related_name` away.
- Keep the queries narrow. `values(...)` of the two columns you are charting beats loading every field of every row.

## Related

- [[access-and-dashboards/streamlit/standalone dashboards|Standalone Dashboards]] — a dashboard that is not about a single model
- [[access-and-dashboards/streamlit/embedding app controls|Embedding App Controls]] — putting the real Calculate button on one of these
