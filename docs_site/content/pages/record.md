---
title: "ui.record"
description: "One thing on a page - its title, facts, sections and actions."
---

`ui.record` is the page for one thing: a fund, an invoice, a request. It shows a title, an
optional status badge and key facts at the top, then the record's sections, with the page's
actions in its header. You fill the slots; lex-app decides where they sit.

## A fund's record

The state reads the fund when the page opens. A `@ui.rows` handler gives the commitments table
its rows, and an action exports the same rows as a CSV file; both call one helper whose name starts
with `_`, because one handler cannot call another.

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

FUND = 2


class FundPage(ui.State):
    name: str = ""
    vintage: str = ""
    status: str = ""
    size: int = 0
    currency: str = "EUR"

    @ui.load
    def open(self):
        fund = self.api.get(Fund, FUND)
        self.name = fund["name"]
        self.vintage = str(fund["vintage"])
        self.status = fund["status"]
        self.size = fund["size"]
        self.currency = fund["currency"]

    @ui.rows
    def commitments(self):
        return self._commitments()

    @ui.action
    def export(self):
        lines = ["investor,amount", *(f"{row['investor']},{row['amount']}" for row in self._commitments())]
        return ui.download(data="\n".join(lines), filename="commitments.csv")

    def _commitments(self):
        investors = {row["id"]: row["name"] for row in every(self.api, Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "amount": row["amount"],
             "currency": self.currency}
            for row in every(self.api, Commitment, filters={"fund": FUND}, ordering="signed_on")
        ]


class FundInvestors(ui.State):
    count: int = 0

    @ui.load
    def open(self):
        self.count = len({row["investor"] for row in every(self.api, Commitment, filters={"fund": FUND})})
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

FUND = 2


class FundPage(ui.State):
    name: str = ""
    vintage: str = ""
    status: str = ""
    size: int = 0
    currency: str = "EUR"

    @ui.load
    def open(self):
        fund = self.api.get(Fund, FUND)
        self.name = fund["name"]
        self.vintage = str(fund["vintage"])
        self.status = fund["status"]
        self.size = fund["size"]
        self.currency = fund["currency"]

    @ui.rows
    def commitments(self):
        return self._commitments()

    @ui.action
    def export(self):
        lines = ["investor,amount", *(f"{row['investor']},{row['amount']}" for row in self._commitments())]
        return ui.download(data="\n".join(lines), filename="commitments.csv")

    def _commitments(self):
        investors = {row["id"]: row["name"] for row in every(self.api, Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "amount": row["amount"],
             "currency": self.currency}
            for row in every(self.api, Commitment, filters={"fund": FUND}, ordering="signed_on")
        ]


class FundInvestors(ui.State):
    count: int = 0

    @ui.load
    def open(self):
        self.count = len({row["investor"] for row in every(self.api, Commitment, filters={"fund": FUND})})
```

```python demo
ui.record(
    title=FundPage.name,
    load=FundPage.open,
    status=ui.status(FundPage.status, tone="info"),
    facts=[
        ui.fact("Vintage", FundPage.vintage),
        ui.fact("Size", FundPage.size, kind="money", currency=FundPage.currency),
    ],
    sections=[
        ui.section(
            "Commitments",
            ui.table(FundPage.commitments, columns=[
                "investor", ui.column("amount", kind="money", currency="currency")]),
        ),
        ui.section("Investors", ui.fact("Investors committed", FundInvestors.count, kind="number"),
                   load=FundInvestors.open),
    ],
    actions=[ui.button("Export commitments", FundPage.export)],
)
```

In your project the record comes from the page's address rather than a fixed number: on a page
declared as `@ui.page("/funds/[key]")`, the load reads `self.params.key`.

## The slots

- **`title=`** is a sentence or a state value, as here.
- **`load=`** is the `@ui.load` that fills the page. Until it has finished the whole page shows a
  skeleton. If it fails on a `@ui.page`, the user is taken to an error page that says why; on a
  dashboard, like the demo above, the failure shows in place.
- **`status=`** is one `ui.status(word, tone=)`, a small badge at the top of the page. The tones are
  neutral, info, success, warning and danger.
- **`facts=`** is a list of `ui.fact(label, value)`. `kind=` formats the value for the user's
  locale: `"text"` (the default), `"number"`, `"money"` (with `currency=`, a code such as `"EUR"`
  or a state value), `"percent"` (a ratio: 0.6 shows 60%) or `"datetime"`.
- **`sections=`** is a list of `ui.section(title, ...)`. A record page needs at least one, and
  each section needs its own title. Sections do not nest.
- **`actions=`** are buttons in the page's header. The first is the primary one, unless it is
  dangerous (`tone="danger"`) or a link; one more is shown as a button, and two or more further
  actions go into a "More actions" menu.
- **`tabs=True`** shows one section at a time, as tabs, when there are two or more; otherwise the
  page shows every section, one below the other, with links to each.

```md alert info
An action whose label has the word "Save" in it is refused: a page header cannot show the
last-saved time that goes with one. Name the action for what it does.
```

## Sections

A section holds the components that fill a page: `ui.text`, `ui.fact`, `ui.table`, `ui.message`,
`ui.status`, `ui.link`, `ui.button`, and `ui.each` and `ui.switch` for repetition and choice. Facts
next to each other are shown as one list of labels and values.

With `load=`, a section loads on its own - after the page's `load=` when the page has one,
at once when it does not - and shows its own skeleton and its own
failure - the "Investors" section above. A load belongs to the page or to one section, not both.

```python
ui.section("Investors", ui.fact("Investors committed", FundInvestors.count, kind="number"),
           load=FundInvestors.open)
```

`empty=` is the sentence a section's table shows when it has no rows.

On a record page, a section's `visible=` takes a permission, such as `visible=ui.read(Investor)`:
a user without it still sees the section's heading, with a note that they may not see it. To show
different content depending on the record, put a `ui.switch(...)` inside the section.

Next: [`ui.list`](/pages/list), the page for finding things.
