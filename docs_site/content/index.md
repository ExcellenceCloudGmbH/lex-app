---
title: "lex-app dashboards"
description: "Build dashboards on lex-app in plain Python, with live examples on every page."
---

**lex.ui** is how you write dashboards for a lex-app project: pages in plain Python, on
[Reflex](https://reflex.dev). You say what is on a page and write ordinary functions for what
happens on a click; lex-app lays the page out, reads the data as the signed-in user, and checks
their permissions before your code runs.

## A complete example

A state holds the page's data, and its handlers fill and change it:

```python exec
from lex import ui
from docs_site.sandbox.models import Fund


class Welcome(ui.State):
    funds: int = 0

    @ui.load
    def open(self):
        self.funds = self.api.list(Fund).count

    @ui.action(requires=ui.create(Fund))
    def add(self):
        self.api.create(Fund, {"name": "New fund", "vintage": 2026, "status": "fundraising",
                               "size": 0, "currency": "EUR"})
        self.funds = self.api.list(Fund).count
        return ui.toast("Fund added.", tone="success")
```

```python
from lex import ui
from myproject.models import Fund


class Welcome(ui.State):
    funds: int = 0

    @ui.load
    def open(self):
        self.funds = self.api.list(Fund).count

    @ui.action(requires=ui.create(Fund))
    def add(self):
        self.api.create(Fund, {"name": "New fund", "vintage": 2026, "status": "fundraising",
                               "size": 0, "currency": "EUR"})
        self.funds = self.api.list(Fund).count
        return ui.toast("Fund added.", tone="success")
```

and the page says what is on it:

```python demo
ui.section(
    "Funds",
    ui.fact("Funds you can see", Welcome.funds, kind="number"),
    ui.button("Add a fund", Welcome.add),
    load=Welcome.open,
)
```

Click **Add a fund**: the count goes up and a toast says so. Switch **Viewing as** to Viewer and
click again: a viewer may read funds but not create them, so the handler does not run.

Every example on this site runs like this one. It works on sample data that belongs to your
browser tab: a reload keeps your changes, a new tab starts fresh, and **Reset sample data** puts
the data back as it started.
