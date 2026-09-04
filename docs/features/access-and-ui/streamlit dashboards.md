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

When a dashboard is embedded in the Lex App frontend, the user's access token is handed to the auth proxy on the iframe's first request and immediately exchanged for a session cookie — the proxy then redirects to the same view without it, so the token does not linger in the address bar, in browser history, or in the `Referer` of anything the page loads. This enables:

- **No re-authentication** — the user doesn't need to log in again for Streamlit
- **Identity traceability** — actions in the dashboard are linked to the user's Keycloak identity
- **Access control** — the dashboard can use the token to call the Lex App API with the user's permissions

The token exchange is handled automatically by the `StreamlitIframe` component — no developer configuration needed beyond defining the dashboard methods on your models.

### Loading and caching

A dashboard's frontend is a large, code-split bundle: Streamlit ships several hundred JavaScript chunks and eagerly preloads over a hundred of them on first paint. The auth proxy serves that bundle itself, compressed and marked immutable, and does **not** put it behind authentication — it is package content from the installed Streamlit release, identical for every deployment, with no application data in it. First load is a few hundred kilobytes; afterwards the browser cache serves it.

Everything that *is* specific to your deployment stays authenticated: the dashboard page, the WebSocket carrying its data, uploads, and anything served under `/media/`.

> [!note]
> If a dashboard ever reports `Failed to fetch dynamically imported module`, it means a lazily-loaded chunk could not be fetched — usually a stale cached page asking for a previous release's files. A hard reload resolves it.

### Staying signed in

Access tokens are short-lived, and a dashboard is often left open far longer than one lasts. The framework renews ahead of every expiry for as long as the page is open, so a dashboard someone comes back to after lunch keeps working — nothing to configure, and nothing for the user to click.

Renewal always goes through the auth proxy, which is the only component that holds the refresh token. Your dashboard code never sees or manages tokens; read the current user from `st.session_state["user_info"]` and their permissions from `st.session_state["permissions"]` as usual.

Sessions do not live forever: Keycloak's SSO maximum lifetime still applies, and a session revoked in Keycloak stops working immediately. When renewal genuinely can't succeed, the embedded dashboard asks the surrounding app to re-authenticate, and a standalone one offers a sign-in link. Either way you are returned to the view you were on, not to the application's first page.

Two deployment settings decide whether a session survives at all, and both are refused at startup rather than degrading quietly, because a session that dies for an invisible reason looks exactly like one that expired:

- `SESSION_SECRET` must be set and identical on every replica. Left unset, the proxy signs cookies with a value unique to each process, so any restart or second replica logs everyone out.
- `TOKEN_REDIS_URL` (or `REDIS_URL`) is required beyond a single replica, and that replica needs session affinity — Streamlit's own session state is held in the process the browser is connected to.

See [[reference/Environment Variables]] for both, along with `SESSION_SAMESITE`, which has to be `none` whenever the frontend and the dashboard are not on the same registrable domain.

## In the Frontend

Dashboards appear in two places:

- **[[interface/record-detail/analytics tab|Analytics Tab]]** — on the record detail page, showing a dashboard scoped to a specific record
- **Table-level toggle** — on the grid toolbar, showing a dashboard for the entire model

If the Streamlit server is unavailable, the UI shows a graceful fallback with a "Retry Connection" button. The rest of the application continues to work normally.

See [[interface/record-detail/analytics tab|Analytics Tab]] for the full user-facing documentation.
