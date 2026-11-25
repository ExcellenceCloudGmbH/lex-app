---
title: "Run a lex-app calculation"
description: "The page of a lex-app calculation model: a form for a run's inputs, the run while it goes, its result, and the past runs."
api:
  - "ui.calculation"
---

A tool that writes to other systems, needs an audit trail and keeps a record people come back to
is a lex-app calculation model: a model whose `calculate()` lex-app runs on its own worker, with
its status, its log and its history. `ui.calculation(Model, ...)` is the page for such a model: a
form for a new run's inputs, the run while it goes, its result, and the past runs. You write no
handler for it.

`ui.calculation` is a page of its own: return it from a `@ui.page`, whose `requires=` its handlers
take. Returned from a `@ui.dashboard`, where no page's `requires=` is known, it is refused when
the page is built, saying to return it from a page function.

```python
from lex import ui
from myproject.models import RegionReport, RegionReportLine


@ui.page("/reports/regions", nav="Reports", requires=ui.read(RegionReport))
def region_reports():
    return ui.calculation(
        RegionReport,
        title="Region report",
        fields=[ui.field("region", options={"north": "North", "south": "South"}, required=True),
                ui.field("year", kind="year", required=True)],
        result=[RegionReport.orders,
                ui.fact("Revenue", RegionReport.revenue, kind="money"),
                RegionReport.report_file,
                ui.section("Lines", ui.table(RegionReportLine, where={"report": RegionReport.id},
                                             columns=["customer", ui.column("amount", kind="money")]))],
        history=ui.table(RegionReport, columns=["region", "year", "created_by"], search=True),
    )
```

`RegionReport` is an ordinary lex-app calculation model:

```python
from django.db import models

from lex.audit_logging.handlers.LexLogger import LexLogger
from lex.core.models.CalculationModel import CalculationModel


class RegionReport(CalculationModel):
    region = models.CharField(max_length=20)
    year = models.IntegerField()
    orders = models.IntegerField(null=True, blank=True)
    revenue = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    report_file = models.FileField(upload_to="region_reports/", null=True, blank=True)
    calculation_error_message = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.region} {self.year}"

    def calculate(self):
        LexLogger().add_text(f"Reading the orders of {self.region}.").log()
        ...
```

```md alert info
The site's sample data has no calculation models, so this page has no live demo.
```

## One page, two views

The page at `/reports/regions` shows the form, and under it the past runs. The same page with
`?run=42` shows run 42: its status, its inputs, and then its progress, its result or its error. A
run's address can be bookmarked and sent while the run still goes. One declaration covers both,
with one permission and one place in the navigation.

## The form: a run's inputs

`fields=` (or `steps=` for a wizard, not both) are written as a [form's](/let-people-act/forms) fields. Each
key names a field of the model, which the value is written to: `ui.field("year")` is
`RegionReport.year`. Nothing is taken from the model, so a field reads the same as on every other
page: give it its `kind=`, `options=` and `required=` yourself.

Two mistakes stop the app from starting, with what to write:

- a key that is not a field a new record is given - not a field of the model, its key,
  `is_calculated`, or one of lex-app's automatic fields such as `created_at`;
- a field the model needs when a record is created (no default, neither `null` nor `blank`) that
  the form leaves out, since lex-app would refuse every run.

The submit button reads **Calculate**, the word lex-app's grid uses. Pressing it does what the grid
does, as the signed-in user:

1. the record is created through lex-app's API, so the model's `permission_create`, its
   validation, `created_by` and the audit entry are the user's;
2. its run is started as lex-app's **Calculate** button starts it, only when nothing has started
   it yet: when the model's `calculate_on_create` started it on its create, the created record
   comes back running or, for a fast run, already done or failed, and it is not started again;
3. the browser goes to the run's address on the page's own route, `?run=<key>`.

When lex-app's API refuses - the user may not create the record, a value it rejects - the form
shows the refusal above the button, in the API's words, and no run starts. The form is shown only
to users who may create the model.

## The run's view

A run's view starts with the page's title, the run's name under it (the record's `__str__`) and a
badge with its status. The inputs follow, as facts labelled as their fields; a foreign key shows
the related record's name, never its bare id. What comes next depends on the status:

| Status | Badge | What is shown |
| --- | --- | --- |
| `IN_PROGRESS` | Running | a progress bar with the newest line the run logged, or "Running"; **Stop** for users who may edit the model |
| `SUCCESS` | Done | `result=` |
| `ERROR` | Failed | the calculation's error in its own words, without the traceback lex-app stored after it |
| `CANCELLED` | Stopped | "The calculation was stopped before it finished." |
| `ABORTED` | Interrupted | "The calculation was interrupted: lex-app found it unfinished after a restart. Start it again." |
| `NOT_CALCULATED` | Not started | |

The error comes from the model's `calculation_error_message` field (or `error_message`). A model
that keeps no message shows "The calculation failed. Its log says what happened." Whatever the
status, a run that has a log gets a closed section **Log**, which shows it as the grid's log panel
does.

A past run reads the same way: the view as the run stands, read now, as the reader. A reader the
model's `permission_read` refuses gets "There is no run 42.", the same as for a key that is no run,
so nothing tells them that the run exists.

## Following a run

While a run goes, the page reads the record through lex-app's API, as the user, once a second,
until its status is no longer `IN_PROGRESS`. It never talks to the process that runs the
calculation, so it follows a run the same way whether lex-app runs it in its own process or on a
Celery worker.

- **Leaving the page stops the watching, never the run.** Coming back to the run's address watches
  again. A lost connection that comes back, or a reload, goes on watching.
- **Progress is the run's own log.** lex-app reports no fraction, so the bar moves without one,
  and its message is the newest line the run has logged, read every third reading of the record. To say which phase it is in, log a line
  from `calculate()`: `LexLogger().add_text("Reading orders").log()`.

## What the result shows

`result=` is written as a form's [result](/long-running-work/tools#laying-out-a-result): what a section holds,
or `ui.section`s. A field of the model, written as the model's field, stands for this run's value
of it:

- **Alone in `result=`**, `RegionReport.orders` is a fact labelled with the field's
  `verbose_name`, shown as its Django field implies: a whole or decimal number, a date, a date and
  time, yes or no, or text.
- **A `FileField` alone**, `RegionReport.report_file`, is a button labelled with the field's
  `verbose_name` that downloads the run's file as the user, through lex-app's download view. It
  shows only when the run has a file.
- **Anywhere a value goes**, it is the run's value: `ui.fact("Revenue", RegionReport.revenue,
  kind="money")`, `ui.text("{n} orders", n=RegionReport.orders)`, or a related table's
  `where={"report": RegionReport.id}`.

A string is a sentence here too, as on every page. A field of another model is refused when the
page is built; for records of another model, give the section a `ui.table` of that model with
`where=`, as `Lines` above.

## The past runs

`history=ui.table(RegionReport, columns=[...])` shows the past runs under the form, titled "Past
runs". lex-app completes the table:

- the run's status is added as the last column, in the badge's words;
- each row opens its run on the same page (`?run=<key>`);
- the newest run comes first until the user sorts;
- a foreign-key column shows the related record's name, as in any model table.

Everything else a [model table](/show-your-data/tables#rows-from-a-model) takes works: `search=`,
`filters=`, `page_size=`, `choose_columns=`. Who sees which runs is the model's `permission_list`
and `permission_read`. A table of another model, `open=`, `where=` and `totals=` are refused, with
what to write.

## Stop

**Stop** shows under a running run's progress to users who may edit the model, and asks "Stop
this run?" before it acts. It is lex-app's cancel:

- a run on a Celery worker is stopped, and the page shows it as Stopped;
- a run in lex-app's own process goes on to its end, and the user reads "This run cannot be
  stopped: lex-app runs it in its own process, where a run goes on to its end. A run on a worker
  can be stopped.";
- after the end, "The run has already ended."

## Who may do what

The page's `requires=` is yours, normally `ui.read(Model)`. Since the page's handlers are lex-app's,
they require the same: a user the page refuses - by a permission or by a
[rule of your own](/who-can-do-what/permissions#a-rule-of-your-own) - cannot send the page's events
by name either. Within the page, the form shows to users who may create the model and **Stop** to
those who may edit it; lex-app's API enforces both, whatever the browser shows, and every read and
write is the user's.

In the [frame](/make-it-your-app/navigation), the page sits in its area like any page. While a run is
open, the breadcrumbs go on: the page becomes a link back to its form, and the run's name is the
last crumb.

## Calculations in your own handlers

Three methods of [`self.api`](/show-your-data/records) work on any calculation model, in any
handler:

| Call | Does |
| --- | --- |
| `self.api.calculate(Model, pk)` | starts the record's run as lex-app's Calculate button does, as the user; returns the record, `IN_PROGRESS`. A run already going is left alone |
| `self.api.log(Model, pk)` | the log of the record's newest run, one Markdown text per entry; `[]` before a log is stored |
| `self.api.file(Model, pk, field)` | a file of the record, downloaded as the user: `{"data": bytes}`, or `{"url": ...}` where the storage serves it at an address |

Start a run with `self.api.calculate`, not `self.api.update(Model, pk, {"calculate": "true"})`:
that starts it too, but under an id with which neither the grid's log button nor this page finds
its log.

## Not built yet

- A step between the inputs and the run that depends on the inputs, such as a decision per row
  found: a wizard's steps have no handler between them.
- The API's refusals at their fields: they are shown together, above the button.
- "Run again with these inputs" on a past run, and a fraction of progress.
