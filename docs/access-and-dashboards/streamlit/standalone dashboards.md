---
title: Standalone Dashboards
---

Some dashboards are not about one model. A monthly report spans several; a control panel drives a workflow; an overview page is the first thing a user should see. Those get a **standalone dashboard** — an app of its own, with no host page around it.

**[Northwind Analytics](https://github.com/ExcellenceCloudGmbH/DemoNorthwindAnalytics) is one**, end to end: its `_streamlit_structure.py` is a ten-page dashboard where each page demonstrates one API on this list.

It takes one file. Put `_streamlit_structure.py` at your project root, give it a `main()`, and the Streamlit process serves it when no `model` is in the query string.

```python title="_streamlit_structure.py"
import streamlit as st


def main():
    st.title("Northwind Analytics")
    st.write("Everything on this page is the project's own.")
```

![A standalone Streamlit app: the project's own pages, lex-app's theme and sign-in, and the project's data](images/streamlit/overview.png)

Nothing about the frame is the project's own work — the navigation rail, the theme, the signed-in user and the way out all come from lex-app. What the project wrote is the column of metrics, the chart, and the Calculate control sitting beside them.

## Two rules that catch people first

> [!warning] `main()` is the only name the framework looks for
> The module is imported and `main()` is called. A function named `app()` or
> `render()` is never reached, and the page renders blank rather than raising.

> [!warning] Every `st.*` call must be **inside** a function
> The module is imported at startup, before there is a script run to draw into.
> Anything at module level renders nothing and logs "missing ScriptRunContext" —
> which reads like a Streamlit bug and is not one.

```python
import streamlit as st

st.title("Never renders")      # ← module level: too early, draws nothing

def main():
    st.title("Renders")        # ← inside a function: correct
```

## Start from one that works

You do not have to write the file from scratch. Two starting points ship with the framework and the organisation:

**The reference dashboard in the package.** `lex/lex_app/streamlit/examples/_streamlit_structure.py` in your installed copy of lex-app is a complete, maintained example — one control, several controls, a log with room, a row composed with Streamlit columns, reading a result back, a whole page, and a multi-step flow. Copy it to `<your_repo>/_streamlit_structure.py` and delete what you do not need.

**A whole project you can run.** [DemoNorthwindAnalytics](https://github.com/ExcellenceCloudGmbH/DemoNorthwindAnalytics) is a public Lex App project built around its Streamlit surface: models, data, a calculation, and a five-page `_streamlit_structure.py` where each page demonstrates one API. Use the package example to copy a snippet; use this one when you want to see the parts working together against real data.

## Several pages

`main()` is a normal Streamlit entry point, so pagination is whatever you would do in Streamlit — a radio in the sidebar, `st.tabs`, or a dict of callables:

```python
import streamlit as st


def overview():
    st.header("Overview")


def calculations():
    st.header("Calculations")


PAGES = {"Overview": overview, "Calculations": calculations}


def main():
    choice = st.sidebar.radio("Page", list(PAGES))
    PAGES[choice]()
```

Each page is a function, so every `st.*` call is inside one — which is the second rule above, satisfied for free.

## Related

- [[access-and-dashboards/streamlit/dashboards on your models|Dashboards on Your Models]] — when the dashboard *is* about one model
- [[access-and-dashboards/streamlit/embedding app pages|Embedding App Pages]] — putting a real lex-app table or form on the page
- [[access-and-dashboards/streamlit/embedding app controls|Embedding App Controls]] — putting a real Calculate button on the page
