---
title: "ui.list"
description: "A page that lists things, with a title, a table and actions."
---

`ui.list` is the page for finding things: a title, one table to search, sort and open rows from,
and the page's actions in its header.

## Open funds, and a new one

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


class OpenFunds(ui.State):
    @ui.rows
    def rows(self):
        return every(self.api, Fund, filters={"status__in": ["investing", "fundraising"]}, ordering="-vintage")

    @ui.action(requires=ui.create(Fund))
    def new_fund(self):
        number = self.api.list(Fund).count + 1
        fund = self.api.create(Fund, {"name": f"New fund {number}", "vintage": 2026, "status": "fundraising",
                                      "size": 0, "currency": "EUR"})
        return ui.toast(f"{fund['name']} created.", tone="success")
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


class OpenFunds(ui.State):
    @ui.rows
    def rows(self):
        return every(self.api, Fund, filters={"status__in": ["investing", "fundraising"]}, ordering="-vintage")

    @ui.action(requires=ui.create(Fund))
    def new_fund(self):
        number = self.api.list(Fund).count + 1
        fund = self.api.create(Fund, {"name": f"New fund {number}", "vintage": 2026, "status": "fundraising",
                                      "size": 0, "currency": "EUR"})
        return ui.toast(f"{fund['name']} created.", tone="success")
```

```python demo
ui.list(
    title="Open funds",
    table=ui.table(
        OpenFunds.rows,
        columns=["name", "vintage",
                 ui.column("status", kind="status", tones={"investing": "info", "fundraising": "warning"})],
        search=True,
    ),
    actions=[ui.button("New fund", OpenFunds.new_fund)],
)
```

Click **New fund**: the fund is created through `self.api`, a toast says so, and the table reads
its rows again, as a table does after an action of its own state succeeds. As a Viewer, the
action is refused: `requires=ui.create(Fund)` is not met.

## The slots

- **`title=`** is a sentence or a state value.
- **`table=`** is one `ui.table(...)`, over a model or a `@ui.rows` handler. Everything a table
  can do is on [Tables](/pages/tables).
- **`actions=`** are buttons in the page's header, placed as on a [record page](/pages/record#the-slots):
  the first is the primary one, one more is a button, and two or more further actions go into a
  menu.
- **`load=`** is a `@ui.load` to run before the page shows. Until it has finished, the whole page
  shows a skeleton, so no action can be clicked before the data it acts on has arrived.

On a `@ui.page`, the search box of a list page's table reads the address's `q` parameter when the
page opens, so a link such as `/funds?q=growth` opens the list already searched.

```python
@ui.page("/funds", nav="Funds", requires=ui.read(Fund))
def funds():
    return ui.list(
        title="Open funds",
        table=ui.table(OpenFunds.rows, columns=["name", "vintage"], search=True),
        actions=[ui.button("New fund", OpenFunds.new_fund)],
    )
```

Next: [Tables](/pages/tables), and what a table can do.
