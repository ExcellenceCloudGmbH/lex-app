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

## In the page

| In a page | What it is |
| --- | --- |
| `ui.running(Tally.walk)` | true while the job runs |
| `ui.progress_of(Tally.walk)` | `{"fraction": ..., "message": ...}`, as last yielded |
| `ui.cancel(Tally.walk)` | the event a Cancel button sends |
| `ui.failed(Tally.walk)` | the message of its last failure, or empty |

Next: [`@ui.load`](/state-and-actions/load), which fills a page when it opens.
