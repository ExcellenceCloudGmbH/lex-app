---
title: Standalone Dashboards
---

Some dashboards are not about one model: an overview that spans several, a report, a control panel. In Reflex those are the project's own dashboard — what `/` shows when the URL names no model — and any further pages the project wants.

It takes one file. Put `_reflex_structure.py` at your project root and give it a `main()` that **returns** a component:

```python title="_reflex_structure.py"
import reflex as rx

from lex.lex_app.reflex import LexUser


def main() -> rx.Component:
    return rx.vstack(
        rx.heading("Northwind Analytics"),
        rx.text("Signed in as ", LexUser.display_name, "."),
    )
```

`lex reflex` imports the module when the app compiles and uses what `main()` returns as the page at `/`. Signed in, the page shows the user's name above the dashboard and a **Sign out** button; framed by Lex App, it leaves both out, because Lex App's own header already shows them.

## Two rules that catch people first

> [!warning] `main()` is called once, when the app compiles
> Not per visit and not per user: it builds the component tree, and Reflex compiles that tree into the frontend. Whatever changes at run time — the data, the user — lives in a state and reaches the page as vars. A query written directly in `main()` runs once, at start-up, and its result is baked into the page for everyone.

> [!warning] `main()` is the only name the framework looks for
> A function named `app()` or `render()` is never called, and `/` keeps showing the placeholder that asks for a `_reflex_structure.py`. A `main()` that returns something other than a component stops the app when it compiles, with a message saying so.

A `_reflex_structure.py` that fails to import stops the app too, with the traceback. Only a missing file is quiet: `/` then shows a note saying what to add.

## Data, per visit

The same shape as on a model's dashboard: a state, a handler that reads the models, and `on_mount` to run it when the page appears.

```python title="_reflex_structure.py"
import reflex as rx

from Input.Fund import Fund
from lex.lex_app.reflex import LexUser


class Overview(rx.State):
    funds: int = 0

    @rx.event
    async def load(self):
        self.funds = await Fund.objects.acount()


def main() -> rx.Component:
    return rx.vstack(
        rx.heading("Northwind Analytics"),
        rx.text("Signed in as ", LexUser.display_name, "."),
        rx.text(Overview.funds, " funds"),
        on_mount=Overview.load,
    )
```

The module is imported by the Reflex app alone — Django's model discovery skips names that start with an underscore — so it can import Reflex and define states at the top level. [[access-and-dashboards/reflex/the django orm|The Django ORM]] explains `acount()` and its synchronous alternative.

## More pages

`main()` is the page at `/`. Any other page is an ordinary Reflex page, declared with `@rxe.page` in the same module — or in a module it imports:

```python title="_reflex_structure.py"
import reflex as rx
import reflex_enterprise as rxe


def main() -> rx.Component:
    return rx.vstack(
        rx.heading("Overview"),
        rx.link("Monthly report", href="/report"),
    )


@rxe.page(route="/report", title="Monthly report")
def report() -> rx.Component:
    return rx.vstack(
        rx.heading("Monthly report"),
        rx.link("Back to the overview", href="/"),
    )
```

Every page requires sign-in, whether or not it says so; `rxe.page` is Reflex Enterprise's `rx.page`, which also lets a page narrow who may see it. [[access-and-dashboards/reflex/sessions and authentication|Sessions & Authentication]] covers both.

## Start from one that works

`lex/lex_app/reflex/examples/_reflex_structure.py` in your installed copy of lex-app is a complete, maintained example: an overview of every model in the project and how many rows it holds, read through `run_orm`; the Keycloak resources the viewer holds a permission on; the viewer's name; and a second page with a **Sign out** button. Copy it to `<your_repo>/_reflex_structure.py` and delete what you do not need.

## Related

- [[access-and-dashboards/reflex/dashboards on your models|Dashboards on Your Models]] — when the dashboard *is* about one model
- [[access-and-dashboards/reflex/the django orm|The Django ORM]] — reading and writing models from a handler
- [[access-and-dashboards/streamlit/standalone dashboards|Streamlit's standalone dashboards]] — the same idea, in Streamlit
