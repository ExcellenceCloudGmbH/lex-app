---
title: "Respond to a click"
description: "A handler that runs when the user clicks."
api:
  - "@ui.action"
---

`@ui.action` marks a short handler - under about a second - that runs when the user clicks a
button. You write a plain function; lex-app runs it as the user, checks what it requires, tells
the user when it fails, and refreshes the tables of its state when it succeeds.

## An action that can say no

```python exec
from lex import ui
from docs_site.sandbox.models import Fund


class Sizes(ui.State):
    @ui.rows
    def rows(self):
        return self.api.all(Fund, ordering="name")

    @ui.action(requires=ui.edit(Fund))
    def grow(self, pk: int):
        fund = self.api.get(Fund, pk)
        if fund["status"] == "closed":
            raise ui.Refuse(f"{fund['name']} is closed; its size cannot change.")
        self.api.update(Fund, pk, {"size": fund["size"] + 10_000_000})
        return ui.toast(f"{fund['name']} grew by 10 million.", tone="success")
```

```python
from lex import ui
from myproject.models import Fund


class Sizes(ui.State):
    @ui.rows
    def rows(self):
        return self.api.all(Fund, ordering="name")

    @ui.action(requires=ui.edit(Fund))
    def grow(self, pk: int):
        fund = self.api.get(Fund, pk)
        if fund["status"] == "closed":
            raise ui.Refuse(f"{fund['name']} is closed; its size cannot change.")
        self.api.update(Fund, pk, {"size": fund["size"] + 10_000_000})
        return ui.toast(f"{fund['name']} grew by 10 million.", tone="success")
```

```python demo
ui.table(
    Sizes.rows,
    columns=["name", "status", ui.column("size", kind="money", currency="currency")],
    row_actions=lambda row: [ui.button("Add 10 million", Sizes.grow, row["id"])],
)
```

Click **Add 10 million** on an open fund: a toast confirms it and the size in the table goes up.
On Northbridge Growth I, which is closed, the action raises `ui.Refuse`, and its sentence is shown
to you as written. As a Viewer, the action does not run at all: `requires=ui.edit(Fund)` is not
met, and the message says you are not permitted to do this.

## What `requires=` does

`requires=` names what the user must be granted for the handler to run: `ui.read(Model)`,
`ui.create(Model)`, `ui.edit(Model)`, `ui.delete(Model)`, `ui.permission(...)`, `ui.signed_in`, or
a list of them, all of which must hold. If the user lacks one, the body never runs.

It also decides whether the body may query the database directly. Without `requires=`, a handler
reaches data only through `self.api`, which applies lex-app's field and row rules as the user; a
direct query fails with an error that says so. With `requires=`, direct queries are allowed, and
records written that way name the user as their creator or editor.

## How it runs

- The body runs in a worker thread, not on the server's event loop, on the live state. While it
  runs, the same user's other clicks wait, so keep it short: work that takes longer belongs in a
  [`@ui.job`](/long-running-work/jobs). In development, an action that takes over a second logs a
  warning saying so.
- The values after the handler in `ui.button("Add 10 million", Sizes.grow, row["id"])` are passed
  to it. They come back from the browser, so check them in the handler.
- One handler cannot call another. Calling an action from a body (`self.grow(...)`) fails with
  an error saying so; calling a load or a job does nothing at all, and only `lex reflex check`
  reports it. Put shared work in a method whose name starts with `_` and call it from both.
- When it succeeds, the tables that read this state's `@ui.rows` handlers read again.

## When it fails

If the body raises, the state's fields go back to what the browser last saw, and the user is told:

- `raise ui.Refuse("...")` is an expected refusal. Its sentence is shown as written, and nothing
  is logged. `title=` adds a short heading: `ui.Refuse("No order in this region has an invoice.",
  title="Nothing to export")` is a warning toast whose first line is the title and whose second is
  the sentence. A form's handler shows its refusal in the form instead, above the submit button
  ([Ask for input with a form](/let-people-act/forms#when-the-handler-says-no)); an action that a button's `ask=` dialog runs
  shows it in the dialog, which stays open until the action succeeds.
- If lex-app's API refuses a call because the request is wrong (a 4xx answer), the API's reason
  is shown; if it refuses for lack of permission, the user is told they are not permitted to do
  this. `ui.ApiError` is that refusal, with `.status`, `.denied` and `.detail`, for a handler that
  wants to catch it.
- Anything else is logged, and the user sees "Something went wrong" with a reference to find the
  log entry by.

```md alert warning
Only the state's fields are put back. A record the handler already wrote through `self.api`
before it failed stays written.
```

## What it returns

An action returns nothing, or one or a list of these:

| Return | What happens |
| --- | --- |
| `ui.toast("Saved.", tone="success")` | a short message; tones are info, success, warning, error; `description=` adds a second line; `link=` adds a button ([below](/let-people-act/clicks#a-toast-that-opens-something)) |
| `ui.goto("/funds/7")` | the browser goes to that page; `replace=True` replaces the current entry in its history |
| `ui.download(data=..., filename="x.csv")` | the user gets a file, or `ui.download(url=...)` for one at an address; the file's name tells the browser what it is (`report.pdf` is a PDF) |
| `ui.refresh(Other.rows)` | the tables over another state's `@ui.rows` handler read again ([Keep other tables up to date](/let-people-act/keep-tables-current)) |
| `ui.scroll_to("Errors found")`, `ui.scroll_to(State.rows)` | the section with that title, or the table over that `@ui.rows` handler, comes into view ([Bringing a part into view](/long-running-work/tools#bringing-a-part-into-view)) |
| `Other.reload`, `Other.open(key)` | another state's handler, which the browser runs next |

Results in a list happen in the order written. Anything else - including Reflex's own events
such as `rx.call_script` - fails the action with an error saying what may be returned; a raw
Reflex handler is marked `@ui.custom`. A handler can also
`yield` one of these partway through: what it changed so far reaches the browser at each `yield`.

## A toast that opens something

`ui.toast(..., link=ui.link(...))` puts one button in the toast, for a result the user may want to
open now: an export's page, a record, a report in another system. The link is written as every
[link](/let-people-act/buttons-and-links) is, with the same targets - `to=` a page with its values,
`record=(Model, pk)`, or `href=` an outside address:

```python
@ui.job
def export(self, region):
    export_id = exports.start(self.user.id, region)
    ...
    return ui.toast("Your export is ready.", tone="success",
                    link=ui.link("Open", to=export_page, key=export_id))
```

- The link is checked when the handler makes the toast: a page given a value it does not take,
  or missing one it needs, fails the handler with a sentence saying what to write, not the
  user's click.
- A success or info toast with a button stays open 10 seconds, so there is time to press it.
- The toast's button is always shown: `visible=` on its link is refused. What it opens should
  also be reachable on the page, for a user who misses the toast.

## Seeing it run

In a page, `ui.running(Sizes.grow)` is true while the action runs, and `ui.failed(Sizes.grow)` is
the message of its last failure. A button in a row (`row_actions`) shows its own row's run as loading.
