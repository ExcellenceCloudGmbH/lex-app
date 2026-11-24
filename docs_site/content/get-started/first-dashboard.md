---
title: "Your first dashboard"
description: "A page that lists your open funds and lets you close one."
---

A lex-app dashboard is a page written in Python. You describe what is on it - a title, a table,
a button - and lex-app decides how it looks, reads its data as the signed-in user, and checks
their permissions before a handler runs. You write no layout code and no event plumbing.

## The state

A page's data lives on a state: a class with handlers that read and change it. A handler is a
plain function. Inside one, `self.api` reads and writes records as the user who is looking.

```python exec
from lex import ui
from docs_site.sandbox.models import Fund


def every(api, model, **options):
    """Every matching record: self.api.list returns one page of them at a time."""
    ordering = options.pop("ordering", None)
    ordering = f"{ordering},id" if ordering else "id"  # a stable order: no row skipped or repeated
    rows, page = [], 1
    while True:
        found = api.list(model, page=page, per_page=100, ordering=ordering, **options)
        rows.extend(found)
        if not found or len(rows) >= found.count:
            return rows
        page += 1


class Funds(ui.State):
    @ui.rows
    def open_funds(self):
        return every(self.api, Fund, filters={"status": "investing"}, ordering="name")

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        self.api.update(Fund, pk, {"status": "closed"})
        return ui.toast("Fund closed.")
```

```python
from lex import ui
from myproject.models import Fund


def every(api, model, **options):
    """Every matching record: self.api.list returns one page of them at a time."""
    ordering = options.pop("ordering", None)
    ordering = f"{ordering},id" if ordering else "id"  # a stable order: no row skipped or repeated
    rows, page = [], 1
    while True:
        found = api.list(model, page=page, per_page=100, ordering=ordering, **options)
        rows.extend(found)
        if not found or len(rows) >= found.count:
            return rows
        page += 1


class Funds(ui.State):
    @ui.rows
    def open_funds(self):
        return every(self.api, Fund, filters={"status": "investing"}, ordering="name")

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        self.api.update(Fund, pk, {"status": "closed"})
        return ui.toast("Fund closed.")
```

`@ui.rows` returns a table's rows as a list of dicts - all of them, so `every` keeps asking
`self.api.list` for the next page until it has them all: `list` returns one page at a time
(see [Reading every record](/state-and-actions/state#reading-every-record)). `@ui.action` runs when the user clicks, and
`requires=ui.edit(Fund)` means it runs only for users who may edit funds.

## The page

The page says what is on it. `ui.list` is the kind of page for finding things: a title and a table.

```python demo
ui.list(
    title="Open funds",
    table=ui.table(
        Funds.open_funds,
        columns=["name", "vintage", ui.column("size", kind="money", currency="currency")],
        row_actions=lambda row: [ui.button("Close", Funds.close, row["id"])],
    ),
)
```

Click **Close** on a fund: a toast says it is closed, and the table reads its rows again, so the
fund leaves the list. Switch **Viewing as** to Viewer and click **Close** again: the handler does
not run, and a message says you are not permitted to do this, because a viewer may read funds but
not edit them.

The `currency="currency"` on the size column names the row's field that holds each fund's
currency code; a fixed code such as `currency="EUR"` works too.

## Giving it an address

In your project, the same page gets a route with `@ui.page`:

```python
@ui.page("/funds", nav="Dashboards", requires=ui.read(Fund))
def funds():
    return ui.list(
        title="Open funds",
        table=ui.table(
            Funds.open_funds,
            columns=["name", "vintage", ui.column("size", kind="money", currency="currency")],
            row_actions=lambda row: [ui.button("Close", Funds.close, row["id"])],
        ),
    )
```

`nav="Dashboards"` lists the page in the navigation under that heading, with its title as the
label. `requires=` decides who sees the page: a user who may not read funds gets an access-denied
page and no navigation entry. More on that in [`@ui.page`](/pages/ui-page).

Next: [`lex reflex add`](/get-started/lex-reflex-add) writes a starting page for one of your
models.
