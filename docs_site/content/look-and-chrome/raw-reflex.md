---
title: "Raw Reflex pages"
description: "A page you write in Reflex yourself, with lex.ui parts inside."
---

Most pages are a [page kind](/pages/ui-page) and never need Reflex directly. A raw Reflex page is
for the rare page the kinds cannot express, and for a project that already has Reflex pages and
moves them to lex.ui one table at a time. On such a page you lay out components yourself, and you
can still use lex.ui's parts, links and handlers inside it.

## A raw page with a lex.ui table

```python exec
import reflex as rx
import rfds

from lex import ui
from docs_site.sandbox.models import Fund


class Picker(rx.State):
    """A raw Reflex state: the status the reader picked."""

    status: str = "investing"

    @rx.event
    def pick(self, value: str | list[str]):
        self.status = value[0] if isinstance(value, list) else value  # the picker sends a list or a str
        return ui.refresh(FundsByStatus.rows)


class FundsByStatus(ui.State):
    @ui.rows
    def rows(self):
        status = self.state(Picker).status
        return self.api.all(Fund, filters={"status": status}, ordering="name")
```

```python
import reflex as rx
import rfds

from lex import ui
from myproject.models import Fund


class Picker(rx.State):
    """A raw Reflex state: the status the reader picked."""

    status: str = "investing"

    @rx.event
    def pick(self, value: str | list[str]):
        self.status = value[0] if isinstance(value, list) else value  # the picker sends a list or a str
        return ui.refresh(FundsByStatus.rows)


class FundsByStatus(ui.State):
    @ui.rows
    def rows(self):
        status = self.state(Picker).status
        return self.api.all(Fund, filters={"status": status}, ordering="name")
```

```python demo
rfds.card(
    rfds.segmented_control(
        {"value": "investing", "label": "Investing"},
        {"value": "fundraising", "label": "Fundraising"},
        {"value": "closed", "label": "Closed"},
        value=Picker.status,
        on_change=Picker.pick,
        label="Status",
    ),
    ui.embed(ui.table(FundsByStatus.rows, columns=["name", "vintage", "status"]), name="Funds"),
    title="Funds by status",
)
```

The card and the status picker are RFDS components on a raw Reflex state; the table is a lex.ui
part. Pick a status: the raw handler `Picker.pick` changes its field and returns
`ui.refresh(FundsByStatus.rows)`, so the lex.ui table reads its rows again, as the user, with the
new status.

## The page itself

Write the page as a function that returns components, and register it with `lex_page`, lex-app's
counterpart of Reflex's `@rx.page`. It puts the page in lex-app's frame, requires a sign-in, loads
the user's grants, and can list the page in the navigation:

```python
import rfds

from lex import ui
from lex.lex_app.reflex import lex_page, permission


@lex_page("/funds-by-status", title="Funds by status", nav="Funds", requires=permission(Fund, "read"))
def funds_by_status():
    return rfds.page(
        rfds.page_header(title="Funds by status", path="/funds-by-status"),
        rfds.card(..., ui.embed(ui.table(FundsByStatus.rows, columns=["name", "vintage"]), name="Funds")),
    )
```

`requires=` here is a permission string, which `permission(Model, scope)` spells for you; a user
without it sees an access-denied notice. `on_load=` takes the page's `@ui.load` handlers, which
lex-app starts once the user's grants are known, as for a page kind. A load in `on_load=` declared
with `@ui.load(optional=True)` starts after the others have succeeded and fails only in place.

Choose this over a page kind only when you have to: a raw page has no slots to check, so lex-app
cannot tell you at start-up that something is in the wrong place, and its layout is yours to keep
in line with lex-app's look.

## Parts inside a raw page

`ui.embed(part, name=...)` turns one lex.ui part - a `ui.table` or a `ui.section` - into a Reflex
component you can place anywhere. Its loads and its table's rows start when it appears. `name`
is the table's caption, and names the part in an error. A whole page kind is refused: it belongs
on a [`@ui.page`](/pages/ui-page) or a `@ui.dashboard`.

## Linking to a raw page

lex.ui links name pages, never addresses typed as text. To let a lex.ui link, button or table
lead to a raw page, declare it once with `ui.reflex_page`:

```python
ORDER = ui.reflex_page("/orders/[key]")
SEARCH = ui.reflex_page("/search", params=("q",))

ui.table(Orders.rows, open=ORDER, columns=["number", "customer"])   # every row opens its order
ui.link("Search", to=SEARCH, q="open")
ui.button("Open order 7", to=ORDER, key="7")
ui.address(ORDER, key=row["id"])                                     # in a @ui.rows handler
```

`params=` lists the query values the page accepts besides its route's arguments. When the app
starts, lex-app checks that every declared route is a page of the app; one that is not stops the
start with an error naming the route. `nav=` with `title=` also lists the page in an area of the
navigation: see [The frame](/look-and-chrome/frame#a-raw-reflex-page-in-an-area).

## Raw handlers

- **On a raw `rx.State`**, an `@rx.event` handler is plain Reflex. To make a lex.ui table read
  again, return `ui.refresh(State.rows)`: it is a list of Reflex events, so a raw handler can
  return it as it is, or among its other events.
- **On a `ui.State`**, mark a raw handler `@ui.custom`. It behaves exactly like `@rx.event`
  (`@ui.custom(background=True)` is passed on), and `lex reflex check` lists every one for review.
  An unmarked `@rx.event` on a `ui.State` fails the check. Use it for what lex.ui handlers do not
  return, such as `rx.call_script(...)`.
- **The other way round**, a lex.ui handler can hand on to a raw state: it may return another
  state's handler, `Orders.reload` or `Picker.pick("closed")`, among its results, and the browser
  runs it after the handler. Reflex's own events, such as `rx.redirect` or `rx.call_script`, are
  refused there with an error that points to `@ui.custom`; use `ui.goto` and `ui.toast` instead.

## A raw component inside a lex.ui page

The opposite case - a lex.ui page that needs one component the vocabulary does not have - does
not need a raw page. Put the component in a section with `ui.custom_block`, and say why:

```python
ui.section(
    "Cash flows",
    ui.custom_block(cash_flow_chart(FundView.flows), reason="A waterfall chart, which lex.ui does not have."),
)
```

`reason=` is required: it tells whoever reads the code why the vocabulary did not do. Like every
part, the block takes `visible=`.

Next: [Sign-in](/ship-it/sign-in), how a dashboard knows who is looking.
