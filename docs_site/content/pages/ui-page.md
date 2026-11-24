---
title: "@ui.page"
description: "Declare a page at a route."
---

`@ui.page` says where a page lives: its address, its place in the navigation, and who may see it.
The function under it returns what is on the page - a page kind such as `ui.record` or `ui.list` -
and does nothing else.

## Declaring a page

```python
from lex import ui
from myproject.models import Fund


@ui.page("/funds", nav="Funds", icon="dashboard", requires=ui.read(Fund))
def funds():
    return ui.list(title="Funds", table=ui.table(FundList.rows, columns=["name", "vintage"], open=fund))


@ui.page("/funds/[key]", requires=ui.read(Fund))
def fund():
    return ui.record(
        title=FundState.name,
        load=FundState.open,
        facts=[ui.fact("Vintage", FundState.vintage)],
        sections=[ui.section("Strategy", ui.text(FundState.strategy))],
    )
```

The list is in the navigation; each of its rows opens the record page, putting the row's `id` into
the address's `[key]`.

What each argument does:

- **The route** starts with `/`. A part in brackets, such as `[key]`, is an argument of the address;
  the page's loads read it from `self.params`, so the page function itself takes no arguments.
  Only plain arguments are supported, not optional or catch-all ones. An argument may not be
  named like a state helper (`user`, `api`, `params`, `can`, `state`, `tab_id`, `progress`), and
  no argument or parameter may be named `to`, `record`, `href`, `visible`, `label`, `handler`,
  `tone` or `icon`, the words a link or button uses itself; the error says what to rename. Nor
  may it share its name with a field of any state: see
  [A route argument and a state field](/pages/ui-page#a-route-argument-and-a-state-field).
- **`nav=`** names the page's area in the navigation: the page is listed under that heading, with
  its title as its label, after the pages declared before it. A page with route arguments, such as
  `fund` above, cannot be opened from the navigation: `nav=` puts it in the area, for its
  breadcrumbs, without listing it. Leave `nav=` out and the page is reachable by link only. A page
  listed in the navigation needs a fixed title, a string rather than a state value. How areas are
  ordered and how breadcrumbs are made is on [The frame](/look-and-chrome/frame).
- **`icon=`** is the icon of its navigation entry, one of RFDS's icon names such as `"dashboard"`.
- **`requires=`** takes what a handler's `requires=` takes: a permission such as
  `ui.read(Model)`, `ui.signed_in`, a rule of your own marked [`@ui.rule`](/data-and-permissions/can#a-rule-of-your-own),
  or a list of them. A user who does not meet it sees an access-denied notice instead of the page,
  and the page's loads do not start. A permission also leaves the page out of that user's
  navigation. Leave it out for a page any signed-in user may see.
- **`params=`** lists the query parameters the page accepts besides its route's arguments, so
  that a link to it may pass them: `params=["tab"]`. How to link to a page is on
  [Buttons and links](/pages/buttons-and-links).

```md alert warning
`requires=` decides what is shown, never what is allowed. What a user may read or change is
enforced by each handler's own `requires=` and by lex-app's API, which acts as the user.
```

## One route, one page

Each route has one page. Declaring a second function at a route that already has a page fails
with an error that names both. lex-app calls every page function when it registers the pages,
after your whole module is loaded, so a page may link to a page declared further down. A page
declared wrongly - an empty slot, a section where a fact belongs - stops the app from starting,
with an error that names the page and the slot.

## A route argument and a state field

Reflex makes each route argument a value of every state, and does not build the app's pages
while any state - yours or a plain Reflex one - has a field or a computed value of the same name.
Reflex finds this out only when it builds the pages for the browser, long after your tests
passed. lex-app checks every route when the app starts, and stops it with a sentence that names
the field, its state and the route:

```text
route '/regions/[region_code]': its dynamic argument [region_code] has the name of the field RegionsBoard.region_code (in myproject.regions). Reflex makes a route argument a value of every state, and does not build the app's pages while a state has a field of that name. Rename the route argument, e.g. /regions/[region_code_key], or the field, e.g. region_code_value.
```

Renaming the route argument always works. When the field belongs to a state lex-app, RFDS or
Reflex made - a table's own `page` or `sort`, say - the sentence says whose state it is instead of
suggesting a new field name, since you cannot rename that field.

A field whose name starts with `_`, or a handler, may share an argument's name: Reflex allows
those, and so does the check.

## What a page body is

The function returns a page kind, and lex-app builds it. Here is a `ui.record`, built the same way
a page is:

```python exec
from lex import ui
```

```python demo
ui.record(
    title="Harbour Infrastructure",
    status=ui.status("Investing", tone="info"),
    facts=[ui.fact("Vintage", "2020"), ui.fact("Size", 600_000_000, kind="money", currency="USD")],
    sections=[ui.section("Strategy", ui.text("Core infrastructure in Europe and North America."))],
)
```

## A page without a route

`@ui.dashboard` marks a page that is not a route: the project's `main()`, or a model's dashboard
hook. It returns a page kind just like `@ui.page`, but with no address there is nothing to open
it, so each load starts when its part of the page appears. That is also how the demos on this site
are drawn.

```python
@ui.dashboard
def main():
    return ui.list(title="Open funds", table=ui.table(Funds.open_funds, columns=["name", "vintage"]))
```

On a model, the hook is a class method; `@classmethod` goes above `@ui.dashboard`. On a dashboard
each load clears its state's fields when it starts, so give each load a state of its own.

Next: [`ui.record`](/pages/record), the page for one thing.
