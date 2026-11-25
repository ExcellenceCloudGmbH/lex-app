---
title: "Tool pages: a form, a run, a result"
description: "A form whose submit runs for a while, shows its progress, and lays out what it produced."
api:
  - "ui.form"
---

A form that starts work which takes a while is a tool page. It puts together a
[form](/let-people-act/forms) and a [job](/long-running-work/jobs).

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
  [Keep a result private](/long-running-work/private-results#a-result-that-waits-for-the-user).
- Keep what the result shows in the state. Large data goes in a field whose name starts with `_`:
  it stays on the server and never reaches the browser. A `@ui.rows` handler reads it. Personal
  data goes in a [`ui.private`](/long-running-work/private-results) field, which also stays out of the
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
skip - the lines that matched nothing in [Several lines of text](/let-people-act/forms#several-lines-of-text). It opens
and closes in the browser, without asking the server.

- What it holds appears when the user first opens it, and stays, so a chart in it fits the space
  it is shown in.
- A table is the exception: it reads its rows at once, so that the closed section's title can say
  how many there are, "Not found (1)". A closed section without a table shows its title alone.
- It works in a form's `result=` and in a section shown on its own with
  [`ui.embed`](/make-it-your-app/your-own-reflex). A record page's sections are always open, and
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
`tabs=True` does on a [record page](/show-your-data/one-record). Each section's title is its tab:

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
