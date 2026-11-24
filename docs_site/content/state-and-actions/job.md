---
title: "@ui.job"
description: "A handler for work that takes a while."
---

`@ui.job` marks a handler for work that takes longer than a click should: an export, a
recalculation, a walk over many records. The user keeps working while it runs; the job reports its
progress and can be cancelled.

## A job with progress

This job walks the sample commitments, a third of a second each, and keeps a count and the largest
amount it has seen. Each `yield ui.progress(...)` reports how far it is.

```python exec
import time

from lex import ui
from docs_site.sandbox.models import Commitment


class Tally(ui.State):
    checked: int = 0
    largest: int = 0

    @ui.job
    def walk(self):
        self.checked = 0
        self.largest = 0
        rows = self.api.list(Commitment, ordering="signed_on")[:10]
        for done, row in enumerate(rows, start=1):
            time.sleep(0.3)
            self.checked = done
            self.largest = max(self.largest, row["amount"])
            yield ui.progress(done / len(rows), f"{done} of {len(rows)} commitments")
        return ui.toast("All commitments checked.", tone="success")
```

```python
import time

from lex import ui
from myproject.models import Commitment


class Tally(ui.State):
    checked: int = 0
    largest: int = 0

    @ui.job
    def walk(self):
        self.checked = 0
        self.largest = 0
        rows = self.api.list(Commitment, ordering="signed_on")[:10]
        for done, row in enumerate(rows, start=1):
            time.sleep(0.3)
            self.checked = done
            self.largest = max(self.largest, row["amount"])
            yield ui.progress(done / len(rows), f"{done} of {len(rows)} commitments")
        return ui.toast("All commitments checked.", tone="success")
```

```python demo
ui.section(
    "Commitments",
    ui.fact("Checked", Tally.checked, kind="number"),
    ui.fact("Largest", Tally.largest, kind="number"),
    ui.text(ui.progress_of(Tally.walk)["message"], visible=ui.running(Tally.walk)),
    ui.button("Start", Tally.walk),
    ui.button("Cancel", ui.cancel(Tally.walk), visible=ui.running(Tally.walk)),
)
```

Click **Start**: the count goes up at each step. Click **Start** again while it runs and nothing
happens - the job is already running. Click **Cancel** and it stops at its next `yield`.

## How it runs

- The body works on a **copy** of the state. What it changed is written to the state at each
  `yield` and at the end, so the page shows the job's progress as it goes. After a `yield`, the
  copy also has what the user changed meanwhile.
- **One run at a time** per browser tab and set of arguments: starting the same job again while it
  runs is ignored. The same job with other arguments runs alongside it.
- **`yield ui.progress(fraction, message)`** reports progress: a fraction from 0 to 1 and a
  sentence. A job can also yield or return a `ui.toast`, `ui.goto`, `ui.download` or
  `ui.refresh`, as an [action](/state-and-actions/action#what-it-returns) can.
- **Cancelling** takes effect at the job's next `yield`: what it changed since the last `yield` is
  discarded, and its `finally` blocks run. With `@ui.job(stop_on_leave=True)`, leaving the page
  stops it the same way.
- **When it ends**, the tables over its state's `@ui.rows` handlers read again. If it fails, the
  user is told, as for an action; what it wrote to the state at earlier `yield`s stays.
- **`requires=`** works as on an action: the job runs only if the user is granted it, and it
  allows direct database queries.
- **`self.progress(fraction, message)`** reports progress without a `yield`, from the body or a
  library's callback: see [Progress from a library](/state-and-actions/job#progress-from-a-library).
- **`limit=`** caps how many runs go at once: see [At most a few at once](/state-and-actions/job#at-most-a-few-at-once).

## Progress from a library

Many libraries report their progress through a function you pass them, often from a thread of
their own. Pass them `self.progress`: it takes the same fraction and sentence as
`ui.progress(...)`, and the page shows them as if the job had yielded them.

```python exec
def largest_of(rows, on_progress):
    """Stands in for a library that reports through a callback."""
    largest = 0
    for done, row in enumerate(rows, start=1):
        time.sleep(0.3)
        largest = max(largest, row["amount"])
        on_progress(done / len(rows), f"{done} of {len(rows)} commitments")
    return largest


class Largest(ui.State):
    largest: int = 0

    @ui.job
    def find(self):
        rows = self.api.list(Commitment, ordering="signed_on")[:10]
        self.largest = largest_of(rows, on_progress=self.progress)
```

```python
def largest_of(rows, on_progress):
    """Stands in for a library that reports through a callback."""
    largest = 0
    for done, row in enumerate(rows, start=1):
        time.sleep(0.3)
        largest = max(largest, row["amount"])
        on_progress(done / len(rows), f"{done} of {len(rows)} commitments")
    return largest


class Largest(ui.State):
    largest: int = 0

    @ui.job
    def find(self):
        rows = self.api.list(Commitment, ordering="signed_on")[:10]
        self.largest = largest_of(rows, on_progress=self.progress)
```

```python demo
ui.section(
    "Largest commitment",
    ui.fact("Largest", Largest.largest, kind="number"),
    ui.text(ui.progress_of(Largest.find)["message"], visible=ui.running(Largest.find)),
    ui.button("Find", Largest.find),
    ui.button("Cancel", ui.cancel(Largest.find), visible=ui.running(Largest.find)),
)
```

Click **Find**: the message counts up while the library works, though the job never yields.
Click **Cancel** while it runs and the library stops at its next report.

- `self.progress` writes only the progress. The job's other fields are written at its next
  `yield` or when it ends, never from the library's thread.
- After a cancel, or when the user leaves a page with `stop_on_leave=True`, the next call to
  `self.progress` raises `GeneratorExit` in the thread that made it - what the job's next `yield`
  would raise - so the library stops. A job that ends with it was stopped, not failed.
- Anywhere else - in an action, a load, or after the job has ended - `self.progress` raises an
  error saying it belongs in a `@ui.job`.

## At most a few at once

`@ui.job(limit=2)` lets at most two runs of the job go at once, for all users together: for work
that is expensive for another system.

```python
@ui.job(limit=2)
def export(self, region):
    ...
```

A press beyond the limit is refused at once, not queued: "Export is already running 2 times. Try
again in a moment." (with `limit=1`, "Export is already running. Try again in a moment."), as a
toast and in `ui.failed`. Nothing else on the page changes; for a form's job no field gets a
message and the last result stays. The job is named as its button is: `export` reads "Export".

- The runs are counted in one server process, from the start of a run to its end, however it
  ends. With several backend workers, each allows that many.
- `limit=` is a whole number, 1 or more; anything else is refused when the state is defined.

```md alert info
There is no live demo of `limit=`: on this site it would count the runs of every visitor at
once.
```

## In the page

| In a page | What it is |
| --- | --- |
| `ui.running(Tally.walk)` | true while the job runs |
| `ui.progress_of(Tally.walk)` | `{"fraction": ..., "message": ...}`, as last yielded |
| `ui.cancel(Tally.walk)` | the event a Cancel button sends |
| `ui.failed(Tally.walk)` | the message of its last failure, or empty |

Next: [`@ui.load`](/state-and-actions/load), which fills a page when it opens.
