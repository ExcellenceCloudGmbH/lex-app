---
title: "Forms"
description: "A page where the user enters something and a handler receives it, checked and typed."
---

`ui.form` is a page kind for entering something: a list of fields, a submit button, and a handler
that receives what was entered. lex-app checks every field before your handler runs, hands it the
values already typed (a date as a `date`, an amount as a `Decimal`), shows the handler running, and
shows a problem at the field it belongs to. You write no setters, no busy flags and no checks of
your own.

## A form that creates a record

```python exec
from lex import ui
from docs_site.sandbox.models import Fund


class NewFund(ui.State):
    made: str = ""

    @ui.action(requires=ui.create(Fund))
    def create(self, values):
        fund = self.api.create(Fund, {**values, "status": "open"})
        self.made = f"{fund['name']} ({fund['vintage']}, {fund['currency']})"
        return ui.toast(f"{fund['name']} was created.", tone="success")
```

```python
from lex import ui
from myproject.models import Fund


class NewFund(ui.State):
    made: str = ""

    @ui.action(requires=ui.create(Fund))
    def create(self, values):
        fund = self.api.create(Fund, {**values, "status": "open"})
        self.made = f"{fund['name']} ({fund['vintage']}, {fund['currency']})"
        return ui.toast(f"{fund['name']} was created.", tone="success")
```

```python demo
ui.form(
    title="New fund",
    fields=[
        ui.field("name", required=True),
        ui.field("vintage", kind="year", required=True),
        ui.field("currency", options=["EUR", "USD"], required=True),
        ui.field("size", kind="money", currency="EUR", help="The target size, in euros."),
    ],
    submit=NewFund.create,
    result=[ui.section("Created", ui.fact("Fund", NewFund.made))],
)
```

Press **Create** with the name empty: the message appears at the field and nothing is sent to your
handler. Type a year like `20x5` and the year field says so. Fill it in properly and the handler
runs once, a toast confirms it, and the **Created** section appears under the form. As a Viewer,
`requires=ui.create(Fund)` is not met and you are told you are not permitted to do this.

## What the handler receives

The submit handler is an `@ui.action` (or an `@ui.job`, below) whose last argument is named
`values`. It is a dict from each field's key to what was entered, already checked and converted:

| `kind=` | Entered as | Your handler gets |
| --- | --- | --- |
| `text` (the default) | a line of text | `str`, trimmed; `""` when empty |
| `number` | a number | `int`, or `Decimal` when it has decimals |
| `money` | an amount in `currency=` | `Decimal` |
| `percent` | a percentage | `Decimal` as a ratio: 5.25 % arrives as `0.0525` |
| `date` | a day | `datetime.date` |
| `datetime` | a day and a time | `datetime.datetime`, in the app's time zone |
| `year` | four digits | `int` |
| `bool` | a box to tick | `bool`; `False` when not ticked |
| `choice` | one of `options=` | `str`, checked against the options |

Other kinds arrive as `None` when nothing was entered. A field with `options=` is a choice even
without `kind="choice"`. Values for fields the page did not declare never reach the handler.

## Fields

```python
ui.field("name")                                     # a line of text, labelled "Name"
ui.field("signed_on", label="Signed", kind="date", required=True)
ui.field("currency", options=["EUR", "USD"])         # a dropdown
ui.field("country", options={"DE": "Germany", "FR": "France"})  # value -> what the user reads
ui.field("investor", options=Lookup.investor_names)  # options held in a field of the state
ui.field("rate", kind="percent", help="Per year.")
```

- **`label=`** is what the user reads; by default it is the key with a capital letter.
- **`required=True`** refuses an empty value, at the field.
- **`help=`** is a sentence under the field.
- **`value=`** is what the field starts with: a value, or a field of the state your page's `load=`
  filled. A value that changes with each visit, such as today's date, belongs in the state: a
  value written in the page is fixed when the page is built.
- **`options=`** with up to 12 fixed options is a dropdown; with more, or with options held in a
  state field, it is a box that filters the options as the user types. Either way the chosen value
  is checked on the server.

Nothing reaches the server while the user types: the one event a form sends is its submit.

```md alert info
There is no file field yet, and no choice searched on the server: every option of a choice is
sent to the browser with the page, so a choice among thousands, or among names the user may not
all see, is not a field. Search for it in the handler instead, from a text field.
```

## A tool: a form with a long run and a result

A tool is a form whose submit is an `@ui.job` and whose `result=` shows what the run produced. The
button turns into a progress bar with **Stop** while the job runs, and the result appears when it
succeeds.

```python exec
from lex import ui
from docs_site.sandbox.models import Commitment, Fund


class Signed(ui.State):
    _rows: list[dict] = []

    @ui.job
    def build_list(self, values):
        self._rows = []                     # before the first yield: an old result never shows
        funds = {row["id"]: row for row in self.api.list(Fund, per_page=100)}
        found = self.api.list(Commitment, per_page=100, ordering="signed_on")
        rows = []
        for i, row in enumerate(found, start=1):
            yield ui.progress(i / len(found), "Reading commitments")
            day = row["signed_on"]          # the API answers with the ISO text of the day
            if day and values["start"].isoformat() <= day <= values["end"].isoformat():
                fund = funds[row["fund"]]
                rows.append({"id": row["id"], "fund": fund["name"], "signed_on": day,
                             "amount": row["amount"], "currency": fund["currency"]})
        self._rows = rows

    @ui.rows
    def rows(self):
        return self._rows
```

```python
from lex import ui
from myproject.models import Commitment, Fund


class Signed(ui.State):
    _rows: list[dict] = []

    @ui.job
    def build_list(self, values):
        self._rows = []                     # before the first yield: an old result never shows
        funds = {row["id"]: row for row in self.api.list(Fund, per_page=100)}
        found = self.api.list(Commitment, per_page=100, ordering="signed_on")
        rows = []
        for i, row in enumerate(found, start=1):
            yield ui.progress(i / len(found), "Reading commitments")
            day = row["signed_on"]          # the API answers with the ISO text of the day
            if day and values["start"].isoformat() <= day <= values["end"].isoformat():
                fund = funds[row["fund"]]
                rows.append({"id": row["id"], "fund": fund["name"], "signed_on": day,
                             "amount": row["amount"], "currency": fund["currency"]})
        self._rows = rows

    @ui.rows
    def rows(self):
        return self._rows
```

```python demo
ui.form(
    title="Commitments signed",
    fields=[ui.field("start", kind="date", required=True), ui.field("end", kind="date", required=True)],
    submit=Signed.build_list,
    result=[ui.section("Signed in that period", ui.table(
        Signed.rows,
        columns=["fund", ui.column("signed_on", label="Signed", kind="date"),
                 ui.column("amount", kind="money", currency="currency")],
        totals={"amount": "sum"},
        export=True,
    ))],
)
```

Enter 2020-01-01 to 2021-12-31 and press **Build list**: the progress bar moves, then the table
appears with its totals. Send the form again and the old table goes until the new run succeeds.

- The submit button is named after the handler: `build_list` reads **Build list**.
- The result stays until the form is sent again or the user comes back to the page.
- Keep what the result shows in the state. Large or private data goes in a field whose name starts
  with `_`: it stays on the server and never reaches the browser. A `@ui.rows` handler reads it.
- A job's changes before a `yield` are kept even if the job is refused later. Clear the old result
  before the first `yield`, as above.

## A form in steps

`steps=` instead of `fields=` makes a wizard: one form shown a step at a time. **Next** checks the
step's fields on the server; the handler runs once, on the last step, with the values of every step.

```python
@ui.page("/orders/new", nav="New order")
def new_order():
    return ui.form(
        title="New order",
        steps=[
            ui.step("Customer", fields=[ui.field("customer", required=True)]),
            ui.step("Lines", fields=[ui.field("amount", kind="money", required=True),
                                     ui.field("due", kind="date")]),
        ],
        submit=OrderState.create,
    )
```

## Buttons that confirm or ask

The same checks work on a single button. `confirm=` asks a question first; `ask=` asks for values
in a dialog and passes them to the handler as `values`, after the row's own values:

```python
ui.button("Close", Orders.close, row["id"], tone="danger", confirm="Close this order?")
ui.button("Decline", Orders.decline, row["id"], ask=[ui.field("reason", required=True)])


class Orders(ui.State):
    @ui.action
    def decline(self, key, values):
        orders.decline(key, values["reason"])
```

A `tone="danger"` button must have `confirm=` or `ask=`. A question is written so it names the
action and ends in `?`: "Are you sure?" stops the page with an error. More in
[Buttons and links](/pages/buttons-and-links).

## When something is wrong in the page

A form is checked when the page is built, and a mistake stops the app with a sentence that says
what to write instead: a submit handler that is a `@ui.load`, a handler whose last argument is not
named `values`, two fields with the same key, a `kind=` lex-app does not know, or one handler used
by two forms with different fields (a handler receives one set of fields).

Next: [Tables](/pages/tables), for what a result shows.
