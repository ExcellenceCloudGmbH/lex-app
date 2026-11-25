---
title: "ui.State"
description: "The state a page's handlers read and change."
---

A page's data lives on a state: a class derived from `ui.State`, with typed fields and the
handlers that fill and change them. A field is what the page shows; a handler is a plain Python
function that runs on the server.

```python
from lex import ui


class FundState(ui.State):
    name: str = ""
    vintage: int = 0

    @ui.load
    def open(self):
        fund = self.api.get(Fund, self.params.key)
        self.name = fund["name"]
        self.vintage = fund["vintage"]
```

The page refers to a field as `FundState.name`: what it shows follows the field as handlers
change it.

## Who is asking

Inside a handler, `self` has helpers for the request it is running for:

| Helper | What it is |
| --- | --- |
| `self.user` | the signed-in user: `.id`, `.name`, `.username`, `.email`, and `.permissions` (their grants as Keycloak lists them) |
| `self.api` | lex-app's API as that user: `list`, `all`, `get`, `create`, `update`, `delete`; `list` returns one page of records, `all` every one |
| `self.can(Model, scope)` | whether the user is granted `scope` - `"read"`, `"create"`, `"edit"` or `"delete"` - on the model |
| `self.params` | the page's route and query values: `self.params.key`, `self.params["key"]`, or `self.params.get("w")` for an optional one |
| `self.state(Other)` | another state of the same user, to read or change |
| `self.tab_id` | the browser tab the handler runs for, for a per-tab store of your own |
| `self.progress(fraction, message)` | in a `@ui.job` only: shows its progress, also when a library calls it ([`@ui.job`](/state-and-actions/job#progress-from-a-library)) |

They work only inside a handler body (`@ui.load`, `@ui.action`, `@ui.job`, `@ui.rows`); anywhere
else they raise an error that says so.

```python exec
from lex import ui
from docs_site.sandbox.models import Fund


class WhoAsks(ui.State):
    name: str = ""
    may_edit: str = ""

    @ui.load
    def open(self):
        self._ask()

    @ui.action
    def ask_again(self):
        self._ask()

    def _ask(self):
        self.name = self.user.name
        self.may_edit = "Yes" if self.can(Fund, "edit") else "No"
```

```python
from lex import ui
from myproject.models import Fund


class WhoAsks(ui.State):
    name: str = ""
    may_edit: str = ""

    @ui.load
    def open(self):
        self._ask()

    @ui.action
    def ask_again(self):
        self._ask()

    def _ask(self):
        self.name = self.user.name
        self.may_edit = "Yes" if self.can(Fund, "edit") else "No"
```

```python demo
ui.section(
    "Who is asking",
    ui.fact("Name", WhoAsks.name),
    ui.fact("May edit funds", WhoAsks.may_edit),
    ui.button("Ask again", WhoAsks.ask_again),
    load=WhoAsks.open,
)
```

Switch **Viewing as** to Viewer and click **Ask again**: the name stays, and "May edit funds"
turns to No. The load and the action share their work through `_ask`, a method whose name starts
with `_`: one handler cannot call another, and a method with a leading `_` is not a handler.

## Reading every record

`self.api.list(Model, ...)` returns **one page** of the matching records: 25 unless you pass
`per_page=`, and the page you ask for with `page=` (the first by default). The rows it returns
carry `.count`, the number of matching records across all pages, so a count needs nothing more.

A handler that needs every record - a `@ui.rows` table, a total - calls `self.api.all` instead:

```python
@ui.rows
def rows(self):
    return self.api.all(Fund, filters={"status": "investing"}, ordering="name")
```

It takes the same `filters=`, `search=`, `ordering=` and `fields=` as `list`, reads page after page
in a stable order, and refuses, in words the user reads, when more than 10,000 records match. See
[Every record](/data-and-permissions/api#every-record).

## Another state

`self.state(Other)` reaches another state of the same user. In an action it is the live state;
in a load or a job it is a working copy, and what the handler changes is written back with its
own changes.

```python
@ui.action
def pick(self, pk: int):
    self.state(Selection).fund_id = pk
```

## What a state may hold

- A field may not be called `user`, `api`, `params`, `can`, `state`, `tab_id` or `progress`: those
  names are the helpers. A state that declares one is refused when the class is defined, with a message saying
  what to rename.
- lex-app copies a state's fields - to undo a failed action, and as a job's working copy - so a
  field cannot hold something that cannot be copied, such as a lock or a client. Keep those outside
  the state.
- What a field holds reaches the browser. Keep what the user may not see in a field whose name
  starts with `_`, which stays on the server, and personal data in a
  [`ui.private`](/state-and-actions/private) field, which is not stored with the state either.

Next: [Keeping results private](/state-and-actions/private), for data that must not be stored.
