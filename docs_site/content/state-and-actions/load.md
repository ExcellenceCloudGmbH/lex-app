---
title: "@ui.load"
description: "A handler that fills a page when it opens."
---

`@ui.load` marks the handler that fills a page's fields when the page opens: it reads the record
the page is about and puts what the page shows into the state. You write a plain function; lex-app
starts it at the right moment, shows the page's skeleton until it has finished, and shows its
failure when it fails.

## A page that loads

```python exec
import time

from lex import ui
from docs_site.sandbox.models import Commitment, Fund


class FundView(ui.State):
    name: str = ""
    vintage: str = ""
    status: str = ""
    investors: int = 0

    @ui.load
    def open(self):
        time.sleep(1)  # a slow source, so that you can see the page wait
        pk = self.params.get("key", 3)  # the page's [key]; this site has none, so fund 3
        fund = self.api.get(Fund, pk)
        self.name = fund["name"]
        self.vintage = str(fund["vintage"])
        self.status = fund["status"]
        self.investors = self.api.list(Commitment, filters={"fund": pk}).count


class Valuation(ui.State):
    net_asset_value: int = 0

    @ui.load
    def open(self):
        raise ui.Refuse("No valuation has been published for this fund yet.")
```

```python
import time

from lex import ui
from myproject.models import Commitment, Fund


class FundView(ui.State):
    name: str = ""
    vintage: str = ""
    status: str = ""
    investors: int = 0

    @ui.load
    def open(self):
        time.sleep(1)  # a slow source, so that you can see the page wait
        pk = self.params.get("key", 3)  # the page's [key]; this site has none, so fund 3
        fund = self.api.get(Fund, pk)
        self.name = fund["name"]
        self.vintage = str(fund["vintage"])
        self.status = fund["status"]
        self.investors = self.api.list(Commitment, filters={"fund": pk}).count


class Valuation(ui.State):
    net_asset_value: int = 0

    @ui.load
    def open(self):
        raise ui.Refuse("No valuation has been published for this fund yet.")
```

```python demo
ui.record(
    title=FundView.name,
    load=FundView.open,
    status=ui.status(FundView.status, tone="info"),
    facts=[ui.fact("Vintage", FundView.vintage), ui.fact("Investors", FundView.investors, kind="number")],
    sections=[
        ui.section("Strategy", ui.text("Core infrastructure in Europe and North America.")),
        ui.section("Valuation", ui.fact("Net asset value", Valuation.net_asset_value, kind="money"),
                   load=Valuation.open),
    ],
)
```

For a second the page shows a skeleton: `FundView.open` is still reading. Then the fund appears,
and the Valuation section shows its own failure in its place, because its load refused. The rest
of the page is not affected.

## How a load is attached

A load is not called by a button. You hand it to the page, and lex-app starts it:

- **`load=` on a page kind** - `ui.record(load=...)` or `ui.list(load=...)` - is the page's load.
  Until it has finished, the whole page shows a skeleton, header included, so that nobody clicks
  an action before the data it acts on has arrived.
- **`load=` on a `ui.section`** is that section's load. The section shows its own skeleton and,
  if the load fails, its own message. Section loads start once the page's load has succeeded (at
  once, on a page without one).

```python
@ui.page("/funds/[key]", requires=ui.read(Fund))
def fund():
    return ui.record(
        title=FundView.name,
        load=FundView.open,
        sections=[ui.section("Valuation", ..., load=Valuation.open)],
    )
```

Pass the handler itself, `load=FundView.open`, not a call: a load given to a page takes no
arguments. It reads the page's address from `self.params` - `self.params.key` for the route's
`[key]`, `self.params.get("w")` for an optional query value. Giving `load=` something that is not
an `@ui.load`, or one load to two places on the same page, stops the app from starting with an
error that names the page.

```md alert info
`@ui.page` has no `load=` of its own: the load belongs to the page kind or the section the page
function returns. The page's `requires=` only decides whether the loads start at all; what a load
may read is decided by its own `requires=` and by lex-app's API.
```

## When it runs

- **Every time the page opens**, after the user's permissions are known. Opening the same page
  again - another fund, or the same one - starts its loads again and shows the skeleton again.
- **The state starts clean.** The first load of a state in a visit puts the state's own fields
  back to their defaults before it runs, so a fund never shows the previous fund's values. List
  fields that should survive in `keep=`: `@ui.load(keep=("filter_text",))`.
- **A result that arrives too late is dropped.** If the user has left the page by the time a load
  finishes, what it read is discarded, and so is its failure.
- **On a page without an address** - a [`@ui.dashboard`](/pages/ui-page), or a part placed with
  [`ui.embed`](/look-and-chrome/raw-reflex) - each load starts when its part of the page appears,
  and clears its state every time it starts. Give each of those loads a state of its own.

The body runs in a worker thread, on a copy of the state: what it changed is written to the state
when it ends, or at each `yield` if it yields partway through.

## When it fails

A load fails the way an [action](/state-and-actions/action) does: `raise ui.Refuse("...")` shows
its sentence as written; a refusal from lex-app's API shows the API's reason, or "You are not
permitted to do this." for a permission; anything else is logged and shows "Something went wrong"
with a reference. Where the message appears depends on the load:

| The load is | When it fails |
| --- | --- |
| a page kind's `load=` | the browser goes to an error page, "This page could not be opened", with the message and a **Try again** link back to the page; for a missing permission it says so instead |
| a section's `load=` | the section shows the message in its place; the rest of the page stays |
| started when its part appears (a dashboard, `ui.embed`) | the message in its place |

A load with `requires=` that the user does not meet does not run its body: it fails as "not
permitted", like any handler.

## Showing its progress yourself

Page kinds show the skeleton and the failure for you. In a part you build yourself, three values
tell you where a load is:

| In a page | Is |
| --- | --- |
| `ui.loaded(FundView.open)` | true once the load has finished in this visit; false again while it runs on the next visit |
| `ui.running(FundView.open)` | true while it runs |
| `ui.failed(FundView.open)` | the message of its last failure, or `""` |

```python
ui.message(ui.failed(Valuation.open), tone="danger", visible=ui.failed(Valuation.open) != "")
```

Next: [Refreshing other tables](/state-and-actions/refresh), when a handler changes what another
state shows.
