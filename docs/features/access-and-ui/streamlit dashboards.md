---
title: Streamlit Dashboards
---

Lex App lets you attach interactive [Streamlit](https://docs.streamlit.io/) dashboards directly to your models. These dashboards appear in the frontend UI and can display charts, tables, forms, or any [Streamlit](https://docs.streamlit.io/) widget.

There are two levels of dashboards:

| Level | Method | When It Shows |
|---|---|---|
| **Table-level** | `streamlit_class_main(cls)` | When viewing the model's table (list view) |
| **Record-level** | `streamlit_main(self)` | When viewing a specific record (detail view) |

## Table-Level Dashboard

A `@classmethod` that receives the model class. Use it for aggregate views — summaries, charts across all records, filtered tables.

```python title="Expense.py"
import streamlit as st
from lex.core.models.LexModel import LexModel
from django.db import models


class Expense(LexModel):
    category = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()

    @classmethod
    def streamlit_class_main(cls):
        st.header("Expense Overview")

        expenses = cls.objects.all().values('category', 'amount')
        import pandas as pd
        df = pd.DataFrame(expenses)

        st.bar_chart(df.groupby('category')['amount'].sum())
        st.dataframe(df)
```

## Record-Level Dashboard

An instance method that receives `self` and the current `user` (optional). Use it for record-specific visualizations — history charts, related data, drill-downs.

```python title="Quarter.py"
class Quarter(LexModel):
    name = models.CharField(max_length=50)
    budget = models.DecimalField(max_digits=12, decimal_places=2)

    def streamlit_main(self, user=None):
        st.header(f"Dashboard: {self.name}")

        expenses = Expense.objects.filter(quarter=self).values('category', 'amount')
        df = pd.DataFrame(expenses)

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Spent", f"€{df['amount'].sum():,.2f}")
        with col2:
            st.metric("Remaining", f"€{self.budget - df['amount'].sum():,.2f}")

        st.bar_chart(df.groupby('category')['amount'].sum())
```

## Running Streamlit

Streamlit dashboards run as a separate process alongside your Lex App application. See [[running your app]] for how to start the Streamlit server.

> [!tip]
> We recommend running Streamlit from your IDE (e.g. PyCharm) using the `lex streamlit` command, which handles environment configuration automatically.

## Tips

- Use `st.cache_data` for expensive queries to keep dashboards responsive
- Use `st.columns()` for side-by-side layouts
- Any Streamlit widget works — `st.plotly_chart()`, `st.map()`, `st.selectbox()`, etc.
- Record-level dashboards have full access to `self` and can query related models

## Federated Authentication

When a dashboard is embedded in the Lex App frontend, the user's access token is passed securely to Streamlit via URL parameters. This enables:

- **No re-authentication** — the user doesn't need to log in again for Streamlit
- **Identity traceability** — actions in the dashboard are linked to the user's Keycloak identity
- **Access control** — the dashboard can use the token to call the Lex App API with the user's permissions

The token exchange is handled automatically by the `StreamlitIframe` component — no developer configuration needed beyond defining the dashboard methods on your models.

## In the Frontend

Dashboards appear in two places:

- **[[interface/record-detail/analytics tab|Analytics Tab]]** — on the record detail page, showing a dashboard scoped to a specific record
- **Table-level toggle** — on the grid toolbar, showing a dashboard for the entire model

If the Streamlit server is unavailable, the UI shows a graceful fallback with a "Retry Connection" button. The rest of the application continues to work normally.

See [[interface/record-detail/analytics tab|Analytics Tab]] for the full user-facing documentation.

## Going the Other Way: Embedding Lex App in Streamlit

The sections above embed Streamlit *inside* Lex App. You can also do the reverse — embed a Lex App page inside a Streamlit script with `lex_view()`, and have Python react to create/update/select/navigation events. See [[features/access-and-ui/lex_view callbacks]].
