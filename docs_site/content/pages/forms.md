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
| `text` (the default) | a line of text, or several with `lines=` | `str`, trimmed; `""` when empty |
| `number` | a number | `int`, or `Decimal` when it has decimals |
| `money` | an amount in `currency=` | `Decimal` |
| `percent` | a percentage | `Decimal` as a ratio: 5.25 % arrives as `0.0525` |
| `date` | a day | `datetime.date` |
| `datetime` | a day and a time | `datetime.datetime`, in the app's time zone |
| `year` | four digits | `int` |
| `bool` | a box to tick | `bool`; `False` when not ticked |
| `choice` | one of `options=` | the option itself, checked against the options: `str` or `int` |
| `choices` | several of `options=` | a `list` of the options in their order, `[]` when none |

Other kinds arrive as `None` when nothing was entered. A field with `options=` is a choice even
without `kind="choice"`. Values for fields the page did not declare never reach the handler.

## Fields

```python
ui.field("name")                                     # a line of text, labelled "Name"
ui.field("signed_on", label="Signed", kind="date", required=True)
ui.field("currency", options=["EUR", "USD"])         # a dropdown
ui.field("year", options=[2024, 2025, 2026])         # the handler gets an int
ui.field("country", options={"DE": "Germany", "FR": "France"})  # value -> what the user reads
ui.field("investor", options=Lookup.investor_names)  # options held in a field of the state
ui.field("regions", kind="choices", options=["North", "South", "East", "West"])  # several
ui.field("rate", kind="percent", help="Per year.")
ui.field("notes", lines=6)                           # several lines of text
ui.field("customer", options=Orders.customers)       # a @ui.rows handler: searched on the server
```

- **`label=`** is what the user reads; by default it is the key with a capital letter.
- **`required=True`** refuses an empty value, at the field.
- **`help=`** is a sentence under the field.
- **`value=`** is what the field starts with: a value, or a field of the state your page's `load=`
  filled. A value that changes with each visit, such as today's date, belongs in the state: a
  value written in the page is fixed when the page is built.
- **`options=`** is a list of texts or whole numbers, each once, or a dict from each value to the
  words the user reads. Up to 12 fixed options make a dropdown; with more, or with options held in
  a state field, it is a box that filters the options as the user types. Either way the chosen
  value is checked on the server, and the handler receives the option itself, of its own type:
  `options=[2024, 2025]` gives an `int`, as does `{1: "Q1", 2: "Q2"}`.
- **`kind="choices"`** lets the user choose several: boxes to tick for up to 12 fixed options, a
  list to pick from above that. `required=True` then means at least one. A **Clear** button beside
  the field empties it at once, in the browser; it shows while something is chosen.
- **`lines=`** makes a text field that many lines high, 2 or more: see
  [Several lines of text](/pages/forms#several-lines-of-text).
- **`options=` a `@ui.rows` handler** searches the options on the server as the user types: see
  [A choice searched on the server](/pages/forms#a-choice-searched-on-the-server).

Apart from that search, nothing reaches the server while the user types: the one event a form
sends is its submit.

```md alert info
There is no file field yet: a page that takes an upload draws it with
`ui.custom_block(..., reason="...")`.
```

## Several lines of text

`ui.field("names", lines=5)` is a text box five lines high, as wide as the form: for notes, a
reason, or a list pasted from somewhere else. The handler still receives a `str`, with its line
breaks, trimmed at the ends; `values["names"].splitlines()` gives the lines.

```python exec
from lex import ui
from docs_site.sandbox.models import Fund


class LookUp(ui.State):
    _found: list[dict] = []
    _missing: list[dict] = []

    @ui.action
    def look_up(self, values):
        self._found, self._missing = [], []
        funds = {row["name"].casefold(): row for row in self.api.all(Fund)}
        for number, line in enumerate(values["names"].splitlines(), start=1):
            name = line.strip()
            fund = funds.get(name.casefold())
            if fund and all(row["id"] != fund["id"] for row in self._found):
                self._found.append({"id": fund["id"], "name": fund["name"], "vintage": fund["vintage"],
                                    "status": fund["status"]})
            elif name and not fund:
                self._missing.append({"id": number, "line": name})
        if not self._found:
            raise ui.Refuse("None of these lines is the name of a fund.", title="No fund found")

    @ui.rows
    def found(self):
        return self._found

    @ui.rows
    def missing(self):
        return self._missing
```

```python
from lex import ui
from myproject.models import Fund


class LookUp(ui.State):
    _found: list[dict] = []
    _missing: list[dict] = []

    @ui.action
    def look_up(self, values):
        self._found, self._missing = [], []
        funds = {row["name"].casefold(): row for row in self.api.all(Fund)}
        for number, line in enumerate(values["names"].splitlines(), start=1):
            name = line.strip()
            fund = funds.get(name.casefold())
            if fund and all(row["id"] != fund["id"] for row in self._found):
                self._found.append({"id": fund["id"], "name": fund["name"], "vintage": fund["vintage"],
                                    "status": fund["status"]})
            elif name and not fund:
                self._missing.append({"id": number, "line": name})
        if not self._found:
            raise ui.Refuse("None of these lines is the name of a fund.", title="No fund found")

    @ui.rows
    def found(self):
        return self._found

    @ui.rows
    def missing(self):
        return self._missing
```

```python demo
ui.form(
    title="Look up funds",
    fields=[ui.field("names", lines=5, required=True, help="One fund name a line.")],
    submit=LookUp.look_up,
    result=[
        ui.section("Found", ui.table(LookUp.found, columns=["name", "vintage", ui.column("status", kind="status")])),
        ui.section("Not found", ui.table(LookUp.missing, columns=["line"]), collapsed=True),
    ],
)
```

Paste these four lines and press **Look up**:

```text
Harbour Infrastructure
Cedar Venture III
Oak Partners
birch secondaries
```

Three funds are found. The third line is in **Not found (1)**, a section that stays closed
until you open it ([Laying out a result](/pages/forms#laying-out-a-result)). Now enter only
`Oak Partners`: the handler refuses, and its message appears in the form, above the button.

- `lines=` goes with text only. On any other kind the page stops with an error saying so, and a
  number below 2 is refused where the field is written: one line is the default.
- `required=True` refuses a text of only spaces and empty lines, at the field.
- There is no limit on the length and no character count.

## When the handler says no

A handler that cannot do what was asked raises `ui.Refuse("...")`, as any handler does. In a
form, its sentence is shown where the user is looking: above the submit button, under the
`title=` when the refusal has one, as the demo above shows. It stays there until the form is
sent again, and it is not also a toast.

```python
raise ui.Refuse("No order in this region has an invoice.", title="Nothing to export")
```

When the user lacks what the handler's `requires=` names, or lex-app's API refuses a call, the
form shows that refusal the same way. An unexpected failure is still a toast with a
reference, since nothing in the form can fix it. Outside a form, a refusal is a toast with the
title as its first line ([`@ui.action`](/state-and-actions/action#when-it-fails)).

## A choice searched on the server

A choice among thousands, or among names the user must not all see, cannot travel with the page.
Give `options=` a `@ui.rows` handler instead: as the user types, lex-app runs it on the server, as
the user, and sends the browser only a page of matches. Each row gives `id`, the value the
handler receives, and `label`, the words the user reads.

```python exec
from docs_site.sandbox.models import Commitment, Investor


class ByInvestor(ui.State):
    _rows: list[dict] = []

    @ui.rows
    def investors(self, search):
        found = self.api.list(Investor, search=search, ordering="name", per_page=50)
        return [{"id": row["id"], "label": row["name"]} for row in found]

    @ui.action
    def list_commitments(self, values):
        funds = {row["id"]: row for row in self.api.all(Fund)}
        found = self.api.all(Commitment, filters={"investor": values["investor"]}, ordering="signed_on")
        self._rows = [{"id": row["id"], "fund": funds[row["fund"]]["name"], "signed_on": row["signed_on"],
                       "amount": row["amount"], "currency": funds[row["fund"]]["currency"]} for row in found]

    @ui.rows
    def commitments(self):
        return self._rows
```

```python
from myproject.models import Commitment, Investor


class ByInvestor(ui.State):
    _rows: list[dict] = []

    @ui.rows
    def investors(self, search):
        found = self.api.list(Investor, search=search, ordering="name", per_page=50)
        return [{"id": row["id"], "label": row["name"]} for row in found]

    @ui.action
    def list_commitments(self, values):
        funds = {row["id"]: row for row in self.api.all(Fund)}
        found = self.api.all(Commitment, filters={"investor": values["investor"]}, ordering="signed_on")
        self._rows = [{"id": row["id"], "fund": funds[row["fund"]]["name"], "signed_on": row["signed_on"],
                       "amount": row["amount"], "currency": funds[row["fund"]]["currency"]} for row in found]

    @ui.rows
    def commitments(self):
        return self._rows
```

```python demo
ui.form(
    title="Commitments of an investor",
    fields=[ui.field("investor", options=ByInvestor.investors, required=True)],
    submit=ByInvestor.list_commitments,
    result=[ui.table(ByInvestor.commitments,
                     columns=["fund", ui.column("signed_on", label="Signed", kind="date"),
                              ui.column("amount", kind="money", currency="currency")],
                     totals={"amount": "sum"})],
)
```

Open the box: the first matches are already there. Type `fund` and the server's search leaves
the two investors whose names contain it. Choose one and press **List commitments**: the handler
receives the investor's `id`, a number, and lists their commitments.

- A rows handler that takes `search` receives what the user typed and searches itself. One
  without `search` returns every row, and lex-app matches the typed words against the labels.
- The browser gets at most 50 matches at a time, each its `id` and `label`: nothing else of the
  row. The first page is asked for when the field appears.
- The server accepts only a value it offered to this browser tab, so a value made up in the
  browser is refused at the field ("Choose one of the options."). A value offered by an earlier
  search stays valid while the user types on.
- The handler receives the row's `id` itself, of its own type: a number stays a number, a UUID
  stays a UUID.
- A searched choice starts empty and chooses one: `value=` and `kind="choices"` are refused when
  the page is built.
- It works in a form, in a wizard's step and in a button's `ask=`, like every field.

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
        funds = {row["id"]: row for row in self.api.all(Fund)}
        found = self.api.all(Commitment, ordering="signed_on")
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
        funds = {row["id"]: row for row in self.api.all(Fund)}
        found = self.api.all(Commitment, ordering="signed_on")
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
- The result stays until the form is sent again or the user opens the page again. A reload, or a
  lost connection that comes back, is not opening it again: the result stays. With
  `ui.form(..., keep=True)` it is still there when the user comes back:
  [Keeping results private](/state-and-actions/private#a-result-that-waits-for-the-user).
- Keep what the result shows in the state. Large data goes in a field whose name starts with `_`:
  it stays on the server and never reaches the browser. A `@ui.rows` handler reads it. Personal
  data goes in a [`ui.private`](/state-and-actions/private) field, which also stays out of the
  stored state.
- A job's changes before a `yield` are kept even if the job is refused later. Clear the old result
  before the first `yield`, as above.

## Laying out a result

A result can hold what a section holds, or `ui.section`s. A section in a result has a heading of
its own, so it can be closed, take its title from the state, and carry buttons.

```python exec
class Vintages(ui.State):
    summary: str = "Summary"
    count: int = 0
    largest: str = ""
    _funds: list[dict] = []

    @ui.action
    def look(self, values):
        self._funds = self.api.all(Fund, filters={"vintage__gte": values["since"]}, ordering="vintage")
        self.count = len(self._funds)
        self.largest = max(self._funds, key=lambda row: row["size"])["name"] if self._funds else ""
        self.summary = f"Summary: {self.count} funds since {values['since']}"

    @ui.rows
    def funds(self):
        return self._funds

    @ui.action
    def show_list(self):
        return ui.scroll_to(Vintages.funds)
```

```python
class Vintages(ui.State):
    summary: str = "Summary"
    count: int = 0
    largest: str = ""
    _funds: list[dict] = []

    @ui.action
    def look(self, values):
        self._funds = self.api.all(Fund, filters={"vintage__gte": values["since"]}, ordering="vintage")
        self.count = len(self._funds)
        self.largest = max(self._funds, key=lambda row: row["size"])["name"] if self._funds else ""
        self.summary = f"Summary: {self.count} funds since {values['since']}"

    @ui.rows
    def funds(self):
        return self._funds

    @ui.action
    def show_list(self):
        return ui.scroll_to(Vintages.funds)
```

```python demo
ui.form(
    title="Funds by vintage",
    fields=[ui.field("since", options=[2018, 2020, 2022], required=True)],
    submit=Vintages.look,
    result=[
        ui.section(Vintages.summary,
                   ui.fact("Funds", Vintages.count, kind="number"),
                   ui.fact("Largest", Vintages.largest),
                   collapsed=True,
                   actions=[ui.button("Show the list", Vintages.show_list)]),
        ui.section("Funds", ui.table(Vintages.funds, columns=["name", "vintage", ui.column("status", kind="status")])),
    ],
)
```

Choose 2020 and press **Look**: the closed section is titled "Summary: 4 funds since 2020". Open
it: its facts appear, and its **Show the list** button brings the table into view.

**`collapsed=True`** keeps a section closed until the user opens it, for a detail most users
skip - the lines that matched nothing in [Several lines of text](#several-lines-of-text). It opens
and closes in the browser, without asking the server.

- What it holds appears when the user first opens it, and stays, so a chart in it fits the space
  it is shown in.
- A table is the exception: it reads its rows at once, so that the closed section's title can say
  how many there are, "Not found (1)". A closed section without a table shows its title alone.
- It works in a form's `result=` and in a section shown on its own with
  [`ui.embed`](/look-and-chrome/raw-reflex). A record page's sections are always open, and
  `collapsed=True` there stops the page with an error that points to `tabs=True`.

**A title from the state**, `ui.section(Vintages.summary, ...)`, is a field your handler fills: a
title with a count, "Lines (12)". It works wherever the section has a heading of its own - a
form's result, `ui.embed`. A record page lists its sections under "On this page" and a tab names
its section, both with fixed words, so a state title there stops the page with an error.

**`actions=[ui.button(...)]`** puts buttons, or links, in the section's heading. In a closed
section they appear when it is opened, never on the closed heading itself, which is the one place
a user clicks to open it. A record page's section and a tab have no heading of their own: there
`actions=` stops the page, saying to put the buttons inside the section or in the page's
`actions=`.

### Bringing a part into view

A handler returns **`ui.scroll_to(...)`** to bring a part of the page into view, as the demo's
**Show the list** does:

```python
return ui.scroll_to("Errors found")          # the section with that title
return ui.scroll_to(Vintages.funds)          # the table over that @ui.rows handler
```

- A section is named by its title, as the page names it; two sections of one title: the first.
- A table is named by its `@ui.rows` handler. A model table cannot be named: name the section
  that holds it.
- A section whose title is a state value is reached through a table it holds.
- A part that is not on the page is not scrolled to, and nothing fails.
- It happens in order with the handler's other results: a toast, then the scroll.

### Tabs

**`ui.form(..., tabs=True)`** shows the result's sections one at a time, as tabs, the way
`tabs=True` does on a [record page](/pages/record). Each section's title is its tab:

```python exec
class ByStatus(ui.State):
    has_closed: bool = False
    _open: list[dict] = []
    _closed: list[dict] = []

    @ui.action
    def sort_funds(self, values):
        funds = self.api.all(Fund, filters={"vintage__gte": values["since"]}, ordering="name")
        self._open = [row for row in funds if row["status"] != "closed"]
        self._closed = [row for row in funds if row["status"] == "closed"]
        self.has_closed = bool(self._closed)

    @ui.rows
    def open_funds(self):
        return self._open

    @ui.rows
    def closed_funds(self):
        return self._closed
```

```python
class ByStatus(ui.State):
    has_closed: bool = False
    _open: list[dict] = []
    _closed: list[dict] = []

    @ui.action
    def sort_funds(self, values):
        funds = self.api.all(Fund, filters={"vintage__gte": values["since"]}, ordering="name")
        self._open = [row for row in funds if row["status"] != "closed"]
        self._closed = [row for row in funds if row["status"] == "closed"]
        self.has_closed = bool(self._closed)

    @ui.rows
    def open_funds(self):
        return self._open

    @ui.rows
    def closed_funds(self):
        return self._closed
```

```python demo
ui.form(
    title="Funds by status",
    fields=[ui.field("since", options=[2018, 2020, 2022], required=True)],
    submit=ByStatus.sort_funds,
    tabs=True,
    result=[
        ui.section("Not closed", ui.table(ByStatus.open_funds, columns=["name", "vintage", "status"])),
        ui.section("Closed", ui.table(ByStatus.closed_funds, columns=["name", "vintage"]),
                   visible=ByStatus.has_closed),
    ],
)
```

Choose 2018 and press **Sort funds**: two tabs, and **Closed** lists the one closed fund. Choose
2020 and send it again: no fund since 2020 is closed, and the **Closed** tab is gone.

- What comes before the first section is shown above the tabs; anything after a section is
  refused.
- It needs two or more sections, each with its own fixed title, and none of them `collapsed=True`.
- `visible=` on a section takes a permission, such as `visible=ui.read(Investor)`: a user without
  it does not get that tab. Or a state value, as **Closed** above: the tab shows while it is true.
  The page opens on its first tab, so a state value there is refused: put a tab that is always
  there first.
- A table in a tab reads its rows when its tab is opened.

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
named `values`, two fields with the same key, a `kind=` lex-app does not know, `lines=` on a field
that is not text, a searched choice with `value=` or `kind="choices"`, `tabs=True` without two
sections, or one handler used by two forms with different fields (a handler receives one set of
fields).

Next: [`ui.calculation`](/pages/calculations), the page of a lex-app calculation model.
