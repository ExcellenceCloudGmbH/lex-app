---
title: "Keeping results private"
description: "ui.private keeps a result on the server, for its user and for a while; ui.form(keep=True) shows it again when the user comes back."
---

A tool's result often names people and amounts. `ui.private()` declares a state field whose value
stays in the server's memory only, for the user who wrote it, and leaves after a while.
`ui.form(keep=True)` lets a form show its result again when the user leaves the page and comes
back. Use them together for a long job whose result is personal.

```python
from lex import ui
from myproject.reports import salaries_of


class PayrollState(ui.State):
    _report: list[dict] = ui.private()

    @ui.job
    def build(self, values):
        self._report = salaries_of(self.user.id, values["month"])

    @ui.rows
    def report(self):
        return self._report
```

`_report` is read and written like any field. A `@ui.rows` handler reads it for a table, as on
[Forms](/pages/forms#a-tool-a-form-with-a-long-run-and-a-result).

```md alert info
There is no live demo on this page: what these two do shows over time and across visits - an hour
without use, a second visit to a page with its own address - and the demos on this site are drawn
without an address.
```

## Why not a field whose name starts with `_`

A field whose name starts with `_` never reaches the browser, but it is still part of the state,
and Reflex stores the whole state: in Redis in production, on disk in development. Personal data
would land there and outlive the page. A `ui.private()` field is kept in the memory
of the server process instead, for this browser tab and the signed-in user who wrote it. The state
holds only a random mark that something was kept.

A table that shows a private value's rows keeps them out of the store too: no lex.ui table stores
the rows it shows ([What reaches the browser, and what is
kept](/pages/tables#what-reaches-the-browser-and-what-is-kept)).

## How to declare one

```python
_report: dict = ui.private()                                   # 60 minutes idle, 8 hours at most
_draft: dict = ui.private(idle_minutes=30, max_hours=4, keep=True)
```

- The name starts with one `_`, and the field has a type, like every field. Until something is
  kept, it holds the empty value of its type: `{}`, `[]`, `""`, `0` or `None`. Writing that empty
  value keeps nothing.
- It is a field of a `ui.State`. A public name, a missing type, a plain Reflex state, a limit that
  is not a number above 0 or a `keep=` that is not `True` or `False` is refused when the class is
  defined, with a sentence saying what to write.
- A computed var (`@rx.var`) may not read it, since Reflex works a computed var out whenever it
  sends the page, when the value may be gone. A state whose computed var reads a private field is
  refused when the class is defined. Read the value in a `@ui.rows` handler, or copy what the page
  shows into an ordinary field where you write the private one.

## When the value is gone

The value is gone:

- after **`idle_minutes`** (60 by default) without a read or a write;
- **`max_hours`** (8 by default) after it was written, even if it was read meanwhile;
- when the tab **opens a page again**, unless the field is declared `keep=True`. A reconnect is
  not opening it again: when the browser loses its connection and gets it back, or the user
  reloads the page, the values stay;
- when **the user signs out** of the dashboard: every value they hold in that process leaves at
  once. Signing out of lex-app itself does not reach the dashboard's server, so those values leave
  after their idle time;
- when **another user** signs in on the tab, or the tab's events reach **another server
  process** - after a restart, or when the app runs several backend workers;
- when the user holds more than the setting `LEX_UI_PRIVATE_MB_PER_USER` allows (100 megabytes by
  default, in each server process): that user's own value used longest ago goes first, never
  another user's. A single value larger than the whole allowance is not kept: writing it fails with
  a sentence naming the setting, logged for you, and the user gets an error with a reference;
- when the process keeps more values than the setting `LEX_UI_PRIVATE_VALUES` allows (5000 by
  default): the one used longest ago goes first.

Reading a value that is gone is refused with a plain sentence: "This is no longer available: it is
kept only for a while. Start again." It is shown as every [refusal](/state-and-actions/action#when-it-fails)
is - a toast for an action or a job, above the submit button in a form, in place of a table's rows,
at the field for a searched choice - and the page keeps what it showed. A reader never gets old
data, an empty value in its place, or another user's.

A page's `load=` resets a private field like every other field. One declared `keep=True` (or listed
in the load's `keep=`) is left alone while its value is kept; once it is gone, the load resets it,
so the page opens clean.

## A result that waits for the user

A new visit starts a form over: its result leaves. `ui.form(..., keep=True)` keeps it. The user
starts a long job, opens another page and comes back: while the job still runs the form shows its
progress, and once it has finished, its result.

```python
from lex import ui
from myproject.invoices import customer_names, invoices_of


class ReportState(ui.State):
    customers: list[str] = []
    customer: str = ""
    _lines: list[dict] = ui.private()

    @ui.load
    def open(self):
        self.customers = customer_names(self.user.id)

    @ui.job
    def build_report(self, values):
        self.customer = values["customer"]
        lines = []
        for month in range(1, 13):
            lines += invoices_of(self.user.id, values["customer"], month)
            yield ui.progress(month / 12, "Reading invoices")
        self._lines = lines

    @ui.rows
    def lines(self):
        return self._lines


@ui.page("/invoices/report", nav="Invoices")
def invoice_report():
    return ui.form(
        title="Invoice report",
        load=ReportState.open,
        fields=[ui.field("customer", options=ReportState.customers, value=ReportState.customer,
                         required=True)],
        submit=ReportState.build_report,
        keep=True,
        result=[ui.table(ReportState.lines, columns=["number", ui.column("amount", kind="money")])],
    )
```

- **One `keep=True` is enough.** The result lives in the state the form submits to, here
  `ReportState`, and in the states whose `@ui.rows` handlers the result's tables read. lex-app keeps
  their private fields across visits as if they were declared `keep=True`, and a load of those
  states, such as `ReportState.open`, no longer resets their fields: it still runs, and sets what it
  sets.
- **A field of another state** that the page's `load=` resets cannot be kept that way. A result that
  shows one is refused when the page is built, with what to write: move the field into the state
  the form submits to, or list it in that load's `keep=`, as in `@ui.load(keep=("note",))`.
- **When a kept private value is gone**, the result does not show over nothing. The form says, under
  it: "The last result is no longer available: results are kept only for a while. Send the form
  again."
- **What was typed is not kept by lex-app**, since it may be personal. To show it again, the
  handler sets a field and the input starts from it, as `customer` above. A
  [searched choice](/pages/forms#a-choice-searched-on-the-server) always starts empty.
- **A refusal stays** above the submit button across visits, and **the handler's last other
  failure** is shown under the form, since the user may have been away when its toast appeared.
  Sending the form again clears both.
- A wizard's step and the messages at the fields still start over.

## In tests

`ui.testing` treats a private field like any other: `fields={"_report": [...]}` seeds it, and
`result.fields["_report"]` is what the handler left in it. To test what a handler does once the
value is gone, seed it with `ui.testing.GONE`; a value that is gone reads as `ui.testing.GONE` in
`result.fields` too. See [Testing](/ship-it/testing).

Next: [`@ui.action`](/state-and-actions/action), what runs when the user clicks.
