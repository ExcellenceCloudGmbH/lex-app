---
title: "Tables"
description: "Tables for a page, from a model or from rows you provide."
---

`ui.table` shows rows on a page: from a `@ui.rows` handler on your state, or straight from a model.
lex-app pages them (25 rows a page), sorts them by column, shows an error in place if reading
fails and "No rows match" with a way to clear the search. Only the fields the table uses reach
the browser.

## Rows from a handler

A `@ui.rows` handler returns every row, as a list of dicts, each with an `id` (or give the table
`key=` to name another field that tells rows apart). lex-app calls it
as the user when the table appears, and again on refresh and after an action of the same state;
it then searches, filters, sorts and pages the rows itself. The handler only reads: setting a
field of the state there fails.

Return every row, not one page: `self.api.list` returns one page of records at a time (25 by
default), so a handler that returns it directly shows, searches and totals only that page. The
`every` helper below asks for the pages in turn; [Reading every
record](/state-and-actions/state#reading-every-record) explains it.

```python exec
from lex import ui
from docs_site.sandbox.models import Commitment, Fund, Investor


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


class Commitments(ui.State):
    @ui.rows
    def rows(self):
        funds = {row["id"]: row for row in every(self.api, Fund)}
        investors = {row["id"]: row["name"] for row in every(self.api, Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "fund": funds[row["fund"]]["name"],
             "amount": row["amount"], "currency": funds[row["fund"]]["currency"]}
            for row in every(self.api, Commitment, ordering="-amount")
        ]
```

```python
from lex import ui
from myproject.models import Commitment, Fund, Investor


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


class Commitments(ui.State):
    @ui.rows
    def rows(self):
        funds = {row["id"]: row for row in every(self.api, Fund)}
        investors = {row["id"]: row["name"] for row in every(self.api, Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "fund": funds[row["fund"]]["name"],
             "amount": row["amount"], "currency": funds[row["fund"]]["currency"]}
            for row in every(self.api, Commitment, ordering="-amount")
        ]
```

```python demo
ui.table(
    Commitments.rows,
    columns=["investor", "fund", ui.column("amount", kind="money", currency="currency")],
    search=True,
    totals={"amount": "sum"},
)
```

`search=True` adds a search box over the table's text columns. `totals={"amount": "sum"}` adds a
totals row over every matching row, not only the visible page - one per currency, since these
commitments are in euros and dollars. Search for "Rhine" and the totals follow.

A handler that takes a `search` argument receives the search text and does the search itself, for
a ranked search or a search in another system:

```python
@ui.rows
def rows(self, search):
    return my_search(self.user, search)
```

## Columns

`columns=` lists field names, or `ui.column(...)` for a column that needs more than its name.
The first column names the row, so it is shown as plain text.

```python
ui.column("amount", kind="money", currency="EUR")       # a fixed currency code
ui.column("amount", kind="money", currency="currency")  # the row's field that holds the code
ui.column("status", kind="status", tones={"open": "info", "late": "warning"})
ui.column("signed_on", label="Signed", kind="date")
```

- **`kind=`** says what the values are: `text`, `number`, `money`, `percent`, `date`, `datetime`,
  `status` (a word with a tone), `tag` (one label) or `tags` (a list of labels).
- **`label=`** is the column's heading; by default it is the field name with spaces for
  underscores, capitalized (`signed_on` becomes "Signed on").
- **`tones=`** goes with `kind="status"`: which tone each word gets.
- **`show=`** draws a cell no kind describes: a function of the row that returns `ui.text`,
  `ui.status` or `ui.link`.

## Filters

`filters=` names columns the user can filter on. lex-app picks the control from the column's kind:
a dropdown of the values in the column, or, for a `date` or `datetime` column, a range of days.

```python exec
class AllFunds(ui.State):
    @ui.rows
    def rows(self):
        return every(self.api, Fund, ordering="name")
```

```python
class AllFunds(ui.State):
    @ui.rows
    def rows(self):
        return every(self.api, Fund, ordering="name")
```

```python demo
ui.table(
    AllFunds.rows,
    columns=["name", "vintage", ui.column("status", kind="status")],
    filters=["status"],
)
```

A dropdown offers at most 50 values. If a filtered column has more, the table cannot read: the
user sees "Something went wrong" with a reference, and the log says the column has too many
values to filter. Search such a column instead, with `search=True`.

## Filtering by date

A `date` or `datetime` column in `filters=` gets a date-range picker instead of a dropdown. The
user picks a first and a last day, and the table keeps the rows whose date falls on one of those
days, the last day included.

```python exec
class Signings(ui.State):
    @ui.rows
    def rows(self):
        funds = {row["id"]: row for row in every(self.api, Fund)}
        investors = {row["id"]: row["name"] for row in every(self.api, Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "fund": funds[row["fund"]]["name"],
             "signed_on": row["signed_on"], "amount": row["amount"],
             "currency": funds[row["fund"]]["currency"]}
            for row in every(self.api, Commitment, ordering="signed_on")
        ]
```

```python
class Signings(ui.State):
    @ui.rows
    def rows(self):
        funds = {row["id"]: row for row in every(self.api, Fund)}
        investors = {row["id"]: row["name"] for row in every(self.api, Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "fund": funds[row["fund"]]["name"],
             "signed_on": row["signed_on"], "amount": row["amount"],
             "currency": funds[row["fund"]]["currency"]}
            for row in every(self.api, Commitment, ordering="signed_on")
        ]
```

```python demo
ui.table(
    Signings.rows,
    columns=["investor", "fund", ui.column("signed_on", label="Signed", kind="date"),
             ui.column("amount", kind="money", currency="currency")],
    filters=["fund", "signed_on"],
)
```

Pick 1 January 2020 to 31 December 2021: three commitments are left. Choose a fund as well, and
the table keeps the rows both filters allow.

- A row's date may be a `date`, a `datetime` or ISO text such as `"2020-06-30"`. A `datetime`
  counts on its day: one at 23:59 on the last day is kept.
- A first day alone keeps everything from then on; a last day alone, everything until then.
- A row without a date never matches a range.
- **Clear filters** removes the range, and the picker is empty again.

## Opening rows, row actions and export

```python
ui.table(
    Funds.rows,
    columns=["name", "vintage"],
    open=fund,                                    # a @ui.page with one route argument: the row's id goes there
    row_actions=lambda row: [ui.button("Close", Funds.close, row["id"])],
    export=True,                                  # CSV and Excel of every matching row
    empty="No funds yet.",
)
```

- **`open=`** makes each row a link to a page declared with `@ui.page` and one route argument,
  such as `"/funds/[key]"`. For rows that lead to different pages, put a `ui.address(...)` in a
  field of each row and give `open=` that field's name; `ui.column("fund", open="fund_page")`
  makes one cell a link the same way. See [Buttons and links](/pages/buttons-and-links#addresses-in-rows).
- **`row_actions=`** is a function of the row returning `ui.button`s or `ui.link`s. A value after
  the handler, such as `row["id"]`, is passed to it; it comes back from the browser, so check it in
  the handler. [Your first dashboard](/get-started/first-dashboard#the-page) has one.
- **`export=True`** adds an export menu for CSV and Excel files of every matching row.
- **`empty=`** is the sentence shown when there are no rows.

## Rows from a model

`ui.table(Model, ...)` reads a model through lex-app's API, as the user. `where=` chooses which
rows to show; who may see them is decided by the model's own permissions, never by `where=`.

```python
ui.table(Commitment, columns=["investor", "amount"], where={"fund": FundState.id})
```

```md alert info
A model table reads lex-app's API directly, with the signed-in user's token, so it is not live
on this site. The demos here use `@ui.rows` tables over sample data.
```

A model table is shown only to users who may read the model. Its `filters=` work on fields with
choices or yes/no fields, so a date range is not offered there, and it cannot export or total
yet; for those, read the rows in a `@ui.rows` handler.

Next: [Buttons and links](/pages/buttons-and-links), what a user clicks.
