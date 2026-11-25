---
title: "Show one record"
description: "One thing on a page - its title, facts, sections and actions."
api:
  - "ui.record"
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
        investors = {row["id"]: row["name"] for row in self.api.all(Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "amount": row["amount"],
             "currency": self.currency}
            for row in self.api.all(Commitment, filters={"fund": FUND}, ordering="signed_on")
        ]


class FundInvestors(ui.State):
    count: int = 0

    @ui.load
    def open(self):
        self.count = len({row["investor"] for row in self.api.all(Commitment, filters={"fund": FUND})})
```

```python
from lex import ui
from myproject.models import Commitment, Fund, Investor


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
        investors = {row["id"]: row["name"] for row in self.api.all(Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "amount": row["amount"],
             "currency": self.currency}
            for row in self.api.all(Commitment, filters={"fund": FUND}, ordering="signed_on")
        ]


class FundInvestors(ui.State):
    count: int = 0

    @ui.load
    def open(self):
        self.count = len({row["investor"] for row in self.api.all(Commitment, filters={"fund": FUND})})
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
  each section needs its own title, in fixed words: the page lists them under "On this page".
  Sections do not nest.
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

A record page's sections are always open: `collapsed=True`, which closes a section in a
[form's result](/long-running-work/tools#laying-out-a-result), stops a record page with an error. To show one
section at a time, give the record `tabs=True`.

A record page names its sections itself, so two things a [form's
result](/long-running-work/tools#laying-out-a-result) allows stop the page here, with what to write instead:
a title from the state (put the value inside the section, as a `ui.fact` or a `ui.text`), and a
section's `actions=` (put the buttons inside the section, or in the page's `actions=`). A handler
can still bring a section into view by its title, with
[`ui.scroll_to("Commitments")`](/long-running-work/tools#bringing-a-part-into-view).

On a record page, a section's `visible=` takes a permission, such as `visible=ui.read(Investor)`:
a user without it still sees the section's heading, with a note that they may not see it. To show
different content depending on the record, put a `ui.switch(...)` inside the section. With
`tabs=True`, `visible=` may also be a state value: the tab shows while it is true. The page opens
on its first tab, so that one cannot depend on the state.

```python
ui.record(title=FundPage.name, load=FundPage.open, tabs=True, sections=[
    ui.section("Commitments", ui.table(FundPage.commitments, columns=["investor", "amount"])),
    ui.section("Distributions", ui.table(FundPage.distributions, columns=["paid_on", "amount"]),
               visible=FundPage.has_distributions),
])
```

## At most four tables

A record page shows at most four tables, counted over all its sections, with `tabs=True` too.
This is a rule of RFDS, the design system lex-app draws with, for one page. A fifth stops the
page with a sentence that names how many tables there are and in which sections.

The way out is a page of their own for the further tables, led to from a section of the record:

```python
class OrderInvoices(ui.State):
    order: str = ""

    @ui.load
    def open(self):
        self.order = str(self.params.key)


@ui.page("/orders/[key]/invoices", nav="Orders", requires=ui.read(Invoice))
def order_invoices():
    return ui.list(title="Invoices", load=OrderInvoices.open,
                   table=ui.table(Invoice, columns=["number", "amount"], where={"order": OrderInvoices.order}))


# a section of the order's record page
ui.section("Invoices", ui.link("All invoices", to=order_invoices, key=OrderState.key))
```

The record page keeps its four most important tables; the invoices page lists the order's
invoices, its table's `where=` naming the order.
