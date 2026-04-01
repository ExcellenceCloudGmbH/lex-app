# Streamlit Dashboards

Search keywords: streamlit, streamlit_main, streamlit_class_main, dashboards, analytics tab, table toggle, streamlit token

## Scope

- Model-attached Streamlit dashboards in Lex App
- Table-level vs record-level dashboard method contracts
- Runtime behavior and frontend embedding points
- Federated token handoff behavior and fallback UX

## Key Points

- Lex supports embedded Streamlit dashboards attached directly to model classes.
- Two method entry points are supported:
  - Table-level: `streamlit_class_main(cls)`
  - Record-level: `streamlit_main(self)`
- Table-level dashboards are shown from the model table view; record-level dashboards are shown in record detail analytics.
- Streamlit runs as a separate process and should be started with `lex streamlit`.
- Embedded mode passes user access context automatically; no second login flow is required.

## Method Contracts

### Table-Level Dashboard

- Define a `@classmethod` named `streamlit_class_main(cls)` on a `LexModel` descendant.
- Use for aggregate views across model records (summaries, grouped charts, global filters).

```python
@classmethod
def streamlit_class_main(cls):
    import streamlit as st
    import pandas as pd

    st.header("Overview")
    rows = cls.objects.all().values("category", "amount")
    df = pd.DataFrame(rows)
    st.bar_chart(df.groupby("category")["amount"].sum())
    st.dataframe(df)
```

### Record-Level Dashboard

- Define an instance method named `streamlit_main(self)` on a `LexModel` descendant.
- Use for record-scoped metrics, related objects, and drill-down analysis.

```python
def streamlit_main(self):
    import streamlit as st

    st.header(f"Dashboard: {self}")
    st.write("Record ID:", self.id)
```

## Runtime

- Start backend normally (`lex start`) and run Streamlit as its own process (`lex streamlit`).
- If Streamlit process is not available, Lex frontend shows a graceful fallback with retry.
- Streamlit is an integration surface, not a replacement for model/serializer/API contracts.

## Frontend Embedding Points

- Record detail page: Analytics tab for record-level dashboards.
- Model grid/table page: table-level dashboard toggle.

## Authentication & Access Context

- Embedded dashboards receive user token context through Lex frontend integration.
- Expected behavior:
  - no re-authentication prompt inside embedded dashboard
  - user identity traceability for dashboard-triggered actions
  - permission-aligned API calls when dashboard requests backend data

## Practical Guidance

- Keep first render light; avoid expensive full-table scans in record-level views.
- Use `st.cache_data` for repeat-heavy queries.
- Prefer ORM aggregation (`values`, `annotate`) before converting to DataFrames.

## Where to Expand

- `content/features/access-and-ui/streamlit dashboards.md`
- `content/running your app.md`

## LLM Prompt Starters

- "Add a table-level Streamlit dashboard (`streamlit_class_main`) for this model with grouped metrics."
- "Add a record-level Streamlit dashboard (`streamlit_main`) showing record KPIs and related data."
- "Troubleshoot why the analytics tab dashboard is not rendering for this model."
