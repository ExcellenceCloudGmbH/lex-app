---
title: Streamlit Dashboards
aliases:
  - "access-and-dashboards/streamlit dashboards"
  - "features/access-and-ui/streamlit dashboards"
---

A Lex App project can carry interactive [Streamlit](https://docs.streamlit.io/) dashboards, and the two embed into **each other**. Hold on to that before reading anything else here: "a Streamlit dashboard" means two different jobs depending on which way round you are, and most of the confusion on this subject comes from mixing them.

```mermaid
flowchart LR
    subgraph app["Lex App"]
        G["A table or a record"]
        A["Analytics tab"]
    end
    subgraph st["Your Streamlit dashboard"]
        M["streamlit_main /<br/>streamlit_class_main"]
        V["lex_view(…)"]
        W["lex_widgets(…)"]
    end
    G --> A
    A -- "opens it with model and pk" --> M
    V -- "embeds a route" --> G
    W -- "embeds a control" --> G
```

**Downward**, lex-app opens your dashboard. The Analytics tab on a record, and the chart icon on a table, both point at the Streamlit process with the model and primary key in the query string; the framework calls your `streamlit_main` or `streamlit_class_main` with them. You write Streamlit; the framework decides when it is shown.

**Upward**, your dashboard opens lex-app. `lex_view` embeds one of the application's routes — a table, a form, a record — and `lex_widgets` embeds one of its controls. These are the same components the application renders, not copies, so a filter applied in an embedded table is the same filter, saved to the same view.

## What to read, in order

| Page | Answers |
|---|---|
| [[access-and-dashboards/streamlit/dashboards on your models\|Dashboards on Your Models]] | How do I attach a dashboard to a model, so it shows up on the record page or the grid? |
| [[access-and-dashboards/streamlit/standalone dashboards\|Standalone Dashboards]] | How do I write a dashboard that is not about one model — a whole application of its own? |
| [[access-and-dashboards/streamlit/embedding app pages\|Embedding App Pages]] | How do I put a real lex-app table or form on my dashboard, and react in Python when the user acts on it? |
| [[access-and-dashboards/streamlit/embedding app controls\|Embedding App Controls]] | How do I put the real Calculate button and its live log on my dashboard? |
| [[access-and-dashboards/streamlit/sessions and authentication\|Sessions & Authentication]] | Who is the dashboard running as, and what do I configure before deploying it? |

The first two are downward, the next two are upward, and the last applies to all of them.

## A project you can run

**[ExcellenceCloudGmbH/DemoNorthwindAnalytics](https://github.com/ExcellenceCloudGmbH/DemoNorthwindAnalytics)** is a complete, public Lex App project written for exactly this section. Five funds, their positions and one calculation that values a fund — enough data for every page to show something real, and small enough to read in a sitting.

Its `_streamlit_structure.py` is a dashboard of five pages, picked from its sidebar. Each shows the code it runs beside the live result:

| Its page | What it calls | Documented in |
|---|---|---|
| Overview | `st.metric`, `st.bar_chart` and a `lex_widgets` block with one **Revalue** control, in one layout | [[access-and-dashboards/streamlit/embedding app controls\|Embedding App Controls]] |
| The table | `lex_view("fund")` — the application's own table | [[access-and-dashboards/streamlit/embedding app pages\|Embedding App Pages]] |
| A narrower view | `lex_view("fund", serializer="summary", hide_toolbar=True, hide_actions=True)` | [[model-your-data/serializers\|Serializers]], [[access-and-dashboards/streamlit/embedding app pages\|Embedding App Pages]] |
| Reacting to the table | `lex_view(…, on_select=True, on_update=True)`, with the event it returns | [[access-and-dashboards/streamlit/embedding app pages\|Embedding App Pages]] |
| Calculations | `page.calculation`, `page.calculation_log` and `page.calculation_log_tree` in one `lex_widgets()` block | [[access-and-dashboards/streamlit/embedding app controls\|Embedding App Controls]] |

It also carries a real `Fund.streamlit_main`, which is what the [[using-the-app/record-detail/analytics tab|Analytics tab]] renders when you open a fund — see [[access-and-dashboards/streamlit/dashboards on your models|Dashboards on Your Models]] — and the serializer that makes `serializer="summary"` resolve. Flows, the other `on_*` flags and the options of a single control have no page there; the examples on this site's pages show them. Clone it and run it; the pages here explain what you are looking at.

## Which one runs

One Streamlit process serves all three kinds of dashboard, and the query string decides which you get:

```mermaid
flowchart TB
    A["lex streamlit"] --> B["lex-app's streamlit_app.py"]
    B --> C{"Identity in the<br/>proxy's headers?"}
    C -- "no" --> D["Authentication error"]
    C -- "yes" --> E{"Query parameters"}
    E -- "model and pk" --> F["Fund.objects.get(pk=42)<br/>.streamlit_main(user)"]
    E -- "model only" --> G["Fund.streamlit_class_main()"]
    E -- "neither" --> H["yourproject/_streamlit_structure.py<br/>main()"]
```

The frontend links to the first two branches with `?model=fund&pk=42` and `?model=fund` respectively — the record- and table-level dashboards of [[access-and-dashboards/streamlit/dashboards on your models|Dashboards on Your Models]]. The third is [[access-and-dashboards/streamlit/standalone dashboards|a standalone dashboard]].

Streamlit runs as a separate process alongside your application. See [[start-here/running your app]] for how to start it.

> [!tip]
> We recommend running Streamlit from your IDE (e.g. PyCharm) using the `lex streamlit` command, which handles environment configuration automatically.

Dashboards can also be written in Reflex, which Lex App supports alongside Streamlit, with the same `?model=&pk=` links and sign-in against the same Keycloak — see [[access-and-dashboards/reflex/index|Reflex Dashboards]].
