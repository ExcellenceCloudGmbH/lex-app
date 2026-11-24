---
title: "Buttons and links"
description: "Buttons that run handlers, links to pages and records."
---

`ui.button` runs a handler when the user clicks it. `ui.link` goes somewhere: a page of your
dashboards, a record in lex-app, or another site. Both work in a section, in a page's `actions=`
and in a table's `row_actions=`.

## A button per row

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


class Closing(ui.State):
    @ui.rows
    def rows(self):
        return [
            {**fund, "why_not": "This fund is already closed." if fund["status"] == "closed" else ""}
            for fund in every(self.api, Fund, ordering="name")
        ]

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        fund = self.api.get(Fund, pk)
        if fund["status"] == "closed":
            raise ui.Refuse(f"{fund['name']} is already closed.")
        self.api.update(Fund, pk, {"status": "closed"})
        return ui.toast(f"{fund['name']} closed.", tone="success")
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


class Closing(ui.State):
    @ui.rows
    def rows(self):
        return [
            {**fund, "why_not": "This fund is already closed." if fund["status"] == "closed" else ""}
            for fund in every(self.api, Fund, ordering="name")
        ]

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        fund = self.api.get(Fund, pk)
        if fund["status"] == "closed":
            raise ui.Refuse(f"{fund['name']} is already closed.")
        self.api.update(Fund, pk, {"status": "closed"})
        return ui.toast(f"{fund['name']} closed.", tone="success")
```

```python demo
ui.table(
    Closing.rows,
    columns=["name", ui.column("status", kind="status")],
    row_actions=lambda row: [
        ui.button("Close", Closing.close, row["id"], tone="danger",
                  confirm="Close this fund?", disabled=row["why_not"]),
    ],
)
```

Click **Close** on a fund: it asks "Close this fund?" first. While the action runs, that row's button shows it is busy, then a toast
confirms it and the fund's button turns grey. Point at a grey button, or reach it with the
keyboard, and it says why it cannot be pressed.

## `ui.button`

```python
ui.button(label, handler=None, *values, to=None, confirm=None, ask=(), tone="neutral", icon=None, visible=None, disabled=None,
          **page_values)
```

- **The handler** is what runs: an `@ui.action`, an `@ui.job`, or `ui.cancel(State.job)` for a
  Cancel button. Values after it are passed to it - `row["id"]` above. They come back from the
  browser, so check them in the handler.
- **`to=`** opens a page instead of running a handler, with the page's values by name:
  `ui.button("New fund", to=new_fund)`. A button takes a handler or `to=`, not both.
- **`confirm=`** asks a question before the handler runs, in a dialog with the button's label and
  Cancel. Write the question so it names the action - `"Close this fund?"`; a question that does
  not end in `?`, or "Are you sure?", stops the page with an error saying so.
- **`ask=`** asks for values first: a list of [`ui.field`](/pages/forms)s in a dialog. They are
  checked like a form's and passed to the handler as its last argument, `values`.
- **`tone="danger"`** marks an action that destroys or cannot be undone. A dangerous button must
  have `confirm=` or `ask=`; without one, the page stops with an error. In a page's `actions=`, a
  dangerous button is never the primary one. `tone=` takes the same tones as `ui.status`, but only
  `"danger"` changes how a button looks.
- **`icon=`** puts an RFDS icon on the button, by its name.
- **`visible=`** decides whether the button is shown: a state value, or a permission such as
  `ui.edit(Fund)`.

```md alert warning
`visible=` and `disabled=` decide what is shown, never what is allowed. The handler still checks:
`requires=` for the user's permission, and its own code for the record's state - the demo's
`close` refuses a closed fund even if a button for it were pressed.
```

## Saying why a button is off

`disabled=` takes the reason, as a sentence. The button stays in its place, greyed, and shows the
reason when it is pointed at or focused, so the user does not have to guess why the action is
missing.

```python
ui.button("Post", Invoices.post, disabled="Nothing to post yet.")      # always off, for this reason
ui.button("Post", Invoices.post, row["id"], disabled=row["blocked"])   # off for the rows with a reason
```

A field holding the reason works per row: a row whose field is `""` gets a button that can be
pressed. `disabled=True`, or a yes/no field of the state, is refused with an error asking for the
reason as text.

## `ui.link`

A link takes exactly one of `to=`, `record=` or `href=`.

```python demo
TABLES = ui.reflex_page("/pages/tables")

ui.section(
    "Read more",
    ui.link("How tables work", to=TABLES),
    ui.link("The Reflex documentation", href="https://reflex.dev/docs/"),
)
```

This site's pages are raw Reflex pages, so the demo declares one of them with `ui.reflex_page` and
links to it; in your project `to=` is usually a page function.

| Link | Where it goes |
| --- | --- |
| `ui.link("Open", to=fund, key=7)` | a page declared with `@ui.page`, with its values by name |
| `ui.link("Orders", to=ORDERS)` | a raw Reflex page declared with `ui.reflex_page("/orders")` |
| `ui.link("In lex-app", record=(Fund, 7))` | lex-app's own page for that record |
| `ui.link("Reflex", href="https://reflex.dev")` | another site; the address starts with `https://` or `http://` |

`to=` names a page, never a route string: when the route changes, every link follows. A link in a
page's `actions=` is drawn as a button. Mail and phone links are not supported yet.

## Linking to a page

A link to a page gives every argument of its route by name. Other values must be listed in the
page's `params=`; they become the query part of the address.

```python
@ui.page("/funds/[key]", params=["tab"], requires=ui.read(Fund))
def fund():
    ...


ui.link("Open fund", to=fund, key=FundList.selected)              # /funds/7
ui.link("Its commitments", to=fund, key=7, tab="commitments")     # /funds/7?tab=commitments
```

The page's loads read the values from `self.params`: `self.params.key`, or
`self.params.get("tab")` for one that may be missing. A value is a fixed value or a state value;
each is encoded as one piece of the address, so a value from data cannot change which page opens.
A link that leaves out an argument, or passes a value the page does not take, stops the app from
starting, with an error that names the page and what it takes.

## Raw Reflex pages

`ui.reflex_page(route)` declares a page written in raw Reflex, registered with `app.add_page`, as
somewhere a lex.ui link may lead. It is for a project that moves to lex.ui one table at a time:

```python
ORDER = ui.reflex_page("/orders/[key]")

ui.table(Orders.rows, columns=["number", "customer"], open=ORDER)   # every row opens its order
ui.link("All orders", to=ui.reflex_page("/orders"))
```

`params=` lists the query values the page reads, as on `@ui.page`. When the app starts, lex-app
checks that each declared route is a page of the app, and stops with an error if it is not.

## Going somewhere from a handler

`ui.goto(path)` sends the browser to a page after a handler has done its work - for example to
the record it just created:

```python
@ui.action(requires=ui.create(Fund))
def new_fund(self):
    fund = self.api.create(Fund, {"name": "New fund", "status": "fundraising"})
    return [ui.toast("Fund created.", tone="success"), ui.goto(ui.address(fund_page, key=fund["id"]))]
```

The path is a page of your dashboards, or a full address such as `https://example.com`, which
loads that site in place of the dashboard. For a page of your dashboards, `replace=True` replaces
the current page in the browser's history, so **Back** skips it. Use `ui.link` when the destination is known before the
click, and `ui.goto` when the handler decides it.

## Addresses in rows

`ui.address(page, **values)` builds the address of a page in Python, with the same values a link
takes. A `@ui.rows` handler puts one in a field of each row when rows lead to different pages, and
the table's `open=` - or a column's - names that field:

```python
class Activity(ui.State):
    @ui.rows
    def rows(self):
        return [
            {"id": item["id"], "title": item["title"],
             "goes_to": ui.address(fund_page, key=item["fund"]) if item["kind"] == "fund"
                        else ui.address(investor_page, key=item["investor"])}
            for item in recent_activity(self.user)
        ]


ui.table(Activity.rows, columns=["title"], open="goes_to")
```

- A value that is `None` or empty leaves that row without a link: `ui.address` returns `""`.
- A table follows only addresses made by `ui.address`. A plain string in the field, such as an
  address read from data, never becomes a link.
- `ui.address` takes a `@ui.page` function or a `ui.reflex_page`; a missing or unknown value
  raises an error that lists what the page takes.

Next: [`ui.State`](/state-and-actions/state), where a page's data lives.
