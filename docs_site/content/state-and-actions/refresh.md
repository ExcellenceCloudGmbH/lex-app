---
title: "Refreshing other tables"
description: "After a handler, read another state's tables again."
---

A table reads its rows again by itself after an action or a job of its own state. When a handler
changes data that a table of **another** state shows, return `ui.refresh(Other.rows)`: the
tables over that `@ui.rows` handler read again too.

## Two tables, two states

The commitments table and the per-fund totals belong to different states. Raising a commitment
changes both.

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


class FundTotals(ui.State):
    @ui.rows
    def rows(self):
        commitments = every(self.api, Commitment)
        return [
            {"id": fund["id"], "name": fund["name"], "currency": fund["currency"],
             "committed": sum(row["amount"] for row in commitments if row["fund"] == fund["id"])}
            for fund in every(self.api, Fund, ordering="name")
        ]


class CommitmentDesk(ui.State):
    @ui.rows
    def rows(self):
        funds = {row["id"]: row for row in every(self.api, Fund)}
        investors = {row["id"]: row["name"] for row in every(self.api, Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "fund": funds[row["fund"]]["name"],
             "amount": row["amount"], "currency": funds[row["fund"]]["currency"]}
            for row in every(self.api, Commitment)
        ]

    @ui.action(requires=ui.edit(Commitment))
    def raise_amount(self, pk: int):
        commitment = self.api.get(Commitment, pk)
        self.api.update(Commitment, pk, {"amount": commitment["amount"] + 5_000_000})
        return [ui.toast("Commitment raised by 5 million.", tone="success"), ui.refresh(FundTotals.rows)]
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


class FundTotals(ui.State):
    @ui.rows
    def rows(self):
        commitments = every(self.api, Commitment)
        return [
            {"id": fund["id"], "name": fund["name"], "currency": fund["currency"],
             "committed": sum(row["amount"] for row in commitments if row["fund"] == fund["id"])}
            for fund in every(self.api, Fund, ordering="name")
        ]


class CommitmentDesk(ui.State):
    @ui.rows
    def rows(self):
        funds = {row["id"]: row for row in every(self.api, Fund)}
        investors = {row["id"]: row["name"] for row in every(self.api, Investor)}
        return [
            {"id": row["id"], "investor": investors[row["investor"]], "fund": funds[row["fund"]]["name"],
             "amount": row["amount"], "currency": funds[row["fund"]]["currency"]}
            for row in every(self.api, Commitment)
        ]

    @ui.action(requires=ui.edit(Commitment))
    def raise_amount(self, pk: int):
        commitment = self.api.get(Commitment, pk)
        self.api.update(Commitment, pk, {"amount": commitment["amount"] + 5_000_000})
        return [ui.toast("Commitment raised by 5 million.", tone="success"), ui.refresh(FundTotals.rows)]
```

```python demo
ui.record(
    title="Commitments",
    sections=[
        ui.section("Commitments", ui.table(
            CommitmentDesk.rows,
            columns=["investor", "fund", ui.column("amount", kind="money", currency="currency")],
            row_actions=lambda row: [ui.button("Add 5 million", CommitmentDesk.raise_amount, row["id"])],
        )),
        ui.section("Committed per fund", ui.table(
            FundTotals.rows,
            columns=["name", ui.column("committed", kind="money", currency="currency")],
        )),
    ],
)
```

Click **Add 5 million** on a commitment. The commitments table reads again because the action
belongs to its state; the per-fund totals read again because the action returned
`ui.refresh(FundTotals.rows)`. Without it, the totals would stay as they were until the page is
opened again.

## What `ui.refresh` takes

`ui.refresh` takes one or more `@ui.rows` handlers, such as
`ui.refresh(FundTotals.rows, Investors.rows)`. Every table that reads one of them, anywhere on the
page, reads again; a table that is not on screen ignores it. Anything that is not a `@ui.rows`
handler fails with an error saying so.

You do not need it for the tables of the handler's own state: they read again after every
`@ui.action` that succeeds, and when a `@ui.job` ends.

## Returning it

An `@ui.action` or a `@ui.job` returns `ui.refresh(...)` like any other result, alone or in a list
with a toast. A job can also `yield` it partway through, to show what it has done so far:

```python
@ui.job
def import_all(self):
    for done, batch in enumerate(batches(self.user), start=1):
        import_batch(batch)
        yield ui.refresh(FundTotals.rows)
        yield ui.progress(done / 10, f"Batch {done} imported")
```

## From a raw Reflex handler

`ui.refresh` returns a list of Reflex events, so a handler written in raw Reflex - an `@rx.event`,
or an `@ui.custom` handler on a `ui.State` - returns it as it is:

```python
import reflex as rx
from lex import ui


class Uploads(rx.State):
    @rx.event
    def finished(self):
        ...
        return ui.refresh(FundTotals.rows)
```

## Handing on to another state's handler

Besides `ui.refresh`, a lex.ui handler may hand on to another state's handler: return it, alone
or in a list, and the browser runs it after the handler, in the order written.

```python
@ui.action(requires=ui.create(Fund))
def create(self):
    fund = self.api.create(Fund, {"name": "New fund", "status": "fundraising"})
    return [ui.toast("Fund created.", tone="success"), FundEditor.open_fund(fund["id"])]
```

That is the only kind of Reflex event a lex.ui handler returns. Reflex's own events -
`rx.call_script`, `rx.redirect`, `rx.download` - are refused, because lex.ui has results of its
own for those (`ui.goto`, `ui.download`, `ui.toast`) and a raw event belongs in a handler marked
`@ui.custom`, where a review sees it. A handler that returns one fails: the user sees "Something
went wrong" with a reference, and the log says what was returned and what to write instead:

```text
CommitmentDesk.raise_amount returned ... A lex.ui handler returns ui.goto(), ui.toast(),
ui.download(), ui.refresh() or another state's handler (`Other.reload`, `Other.open(key)`),
and a job may yield ui.progress(). Use @ui.custom for raw Reflex events such as rx.call_script().
```

```md alert warning
To make a table read again, return `ui.refresh(Other.rows)`, not `Other.rows` itself. A
`@ui.rows` handler is read by tables and is not an event: sent on its own, it fails with an error
saying so.
```

Next: [Data and permissions](/data-and-permissions/api), reading and writing records as the user.
