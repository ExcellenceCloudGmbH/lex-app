---
title: Streamlit Dashboards
aliases:
  - "features/access-and-ui/streamlit dashboards"
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

Streamlit dashboards run as a separate process alongside your Lex App application. See [[start-here/running your app]] for how to start the Streamlit server.

> [!tip]
> We recommend running Streamlit from your IDE (e.g. PyCharm) using the `lex streamlit` command, which handles environment configuration automatically.

For production-style deployments, give the Streamlit proxy a fixed `SESSION_SECRET`. If you run more than one proxy replica, also use a shared `TOKEN_REDIS_URL` / `REDIS_URL` so users don't lose their dashboard session when a request lands on a different replica.

## Embedding Lex App in a Dashboard

When your dashboard needs Lex App controls, you can embed them directly instead
of rebuilding the UI in Streamlit. Put your dashboard code in
`_streamlit_structure.py` at your project root and expose a `main()` function.

Use one of the flat `lex_*` calls for a single control:

```python
from lex.lex_app.streamlit import lex_calculation

lex_calculation("salesreport", pk=1, title="Sales report")
```

For several controls on one page, group them in one `lex_widgets()` block. They
share one embedded runtime, and keys are derived automatically from the call
site:

```python
from lex.lex_app.streamlit import lex_widgets

with lex_widgets() as page:
    page.calculation("salesreport", pk=1)
    page.calculation_log_tree("salesreport", pk=1)
```

Use [[access-and-dashboards/lex_view callbacks|`lex_view()`]] when you want to
embed a complete Lex App route, such as a table or record form.

## Tips

- Use `st.cache_data` for expensive queries to keep dashboards responsive
- Use `st.columns()` for side-by-side layouts
- Any Streamlit widget works — `st.plotly_chart()`, `st.map()`, `st.selectbox()`, etc.
- Record-level dashboards have full access to `self` and can query related models
- Keep `st.*` calls inside `main()` (or another function it calls); the dashboard module is imported before Streamlit renders the page.

## Federated Authentication

When a dashboard is embedded in the Lex App frontend, the user's access token is handed to the Streamlit proxy on the iframe's first request. The proxy immediately turns it into a session cookie and redirects to the same view without the token, so it doesn't sit in the address bar, browser history, or `Referer` headers. This enables:

- **No re-authentication** — the user doesn't need to log in again for Streamlit
- **Identity traceability** — actions in the dashboard are linked to the user's Keycloak identity
- **Access control** — the dashboard can use the token to call the Lex App API with the user's permissions
- **Longer-lived dashboards** — the proxy refreshes tokens and keeps disconnected Streamlit sessions around long enough for normal re-authentication or network blips

The token exchange is handled automatically — no developer configuration needed beyond defining the dashboard methods on your models.

### Loading and Caching

Streamlit's frontend is a large, code-split bundle. The proxy serves those Streamlit package assets directly, compressed and cacheable, and leaves anything specific to your app authenticated — the dashboard page, WebSocket data, uploads, and `/media/` files.

> [!note]
> If a dashboard ever reports `Failed to fetch dynamically imported module`, it usually means the browser has a stale cached page that points at files from an older Streamlit release. A hard reload resolves it.

### Staying Signed In

Access tokens are short-lived, and dashboards often stay open longer than a token lasts. Lex App renews the token in the background through the proxy, without reloading the dashboard, so widgets and `st.session_state` keep their state.

Sessions still follow Keycloak's maximum lifetime, and revoked Keycloak sessions stop working immediately. When renewal really can't continue, the embedded dashboard asks the surrounding app to re-authenticate and returns the user to the same dashboard view.

Two deployment settings decide whether dashboard sessions survive restarts and load balancing:

- Session cookies are signed with a key derived from `DJANGO_SECRET_KEY` unless you set `SESSION_SECRET` explicitly. Because that key is already stable across restarts and identical on every replica, dashboard sessions survive a redeploy without any extra configuration. Set `SESSION_SECRET` only when you want to control the value yourself.
- `TOKEN_REDIS_URL` (or `REDIS_URL`) is required when you run more than one proxy replica. The in-memory token store is only safe for a single process.

The token exchange is handled automatically by the `StreamlitIframe` component — no developer configuration needed beyond defining the dashboard methods on your models.
The session keeps refreshing while the dashboard is open, including across normal Streamlit script reruns.
See [[reference/Environment Variables]] for the full list, including `SESSION_SAMESITE` for cross-site iframe deployments.

## In the Frontend

Dashboards appear in two places:

- **[[using-the-app/record-detail/analytics tab|Analytics Tab]]** — on the record detail page, showing a dashboard scoped to a specific record
- **Table-level toggle** — on the grid toolbar, showing a dashboard for the entire model

If the Streamlit server is unavailable, the UI shows a graceful fallback with a "Retry Connection" button. The rest of the application continues to work normally.

See [[using-the-app/record-detail/analytics tab|Analytics Tab]] for the full user-facing documentation.

## Going the Other Way: Embedding Lex App in Streamlit

The sections above embed Streamlit *inside* Lex App. You can also do the reverse — embed a Lex App page inside a Streamlit script with `lex_view()`, and have Python react to create/update/select/navigation events. See [[access-and-dashboards/lex_view callbacks]].
