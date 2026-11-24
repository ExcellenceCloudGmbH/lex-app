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
| `self.api` | lex-app's API as that user: `list`, `get`, `create`, `update`, `delete`; `list` returns one page of records |
| `self.can(Model, scope)` | whether the user is granted `scope` - `"read"`, `"create"`, `"edit"` or `"delete"` - on the model |
| `self.params` | the page's route and query values: `self.params.key`, `self.params["key"]`, or `self.params.get("w")` for an optional one |
| `self.state(Other)` | another state of the same user, to read or change |
| `self.tab_id` | the browser tab the handler runs for, for a per-tab store of your own |

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
carry `.count`, the number of matching records across all pages. A handler that needs every
record - a `@ui.rows` table, a total - asks for the pages in turn until it has `.count` of them.
The examples on this site use this helper:

```python
def every(api, model, **options):
    """Every matching record: self.api.list returns one page of them at a time."""
    rows, page = [], 1
    while True:
        found = api.list(model, page=page, per_page=100, **options)
        rows.extend(found)
        if not found or len(rows) >= found.count:
            return rows
        page += 1
```

`every(self.api, Fund, filters={"status": "investing"}, ordering="name")` takes the same
`filters=`, `search=` and `ordering=` as `list`. For a count alone, `self.api.list(Model).count`
needs no loop.

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

- A field may not be called `user`, `api`, `params`, `can`, `state` or `tab_id`: those names are the
  helpers. A state that declares one is refused when the class is defined, with a message saying
  what to rename.
- lex-app copies a state's fields - to undo a failed action, and as a job's working copy - so a
  field cannot hold something that cannot be copied, such as a lock or a client. Keep those outside
  the state.
- What a field holds reaches the browser. Keep what the user may not see out of fields.

Next: [`@ui.action`](/state-and-actions/action), what runs when the user clicks.
