---
title: "Testing"
description: "Test a dashboard's handlers and tables without a browser."
---

`ui.testing` runs your handlers in an ordinary test, the way a browser would run them: through
Reflex's own event processing, for a signed-in user you name, with the grants you give them. No
browser, no running server, no Keycloak. You hand it a stand-in for `self.api`, so a test decides
exactly what lex-app "answers".

There are two functions: `ui.testing.run` runs one handler, and `ui.testing.rows` reads a table's
rows.

## A first test

Take a state with a table of open funds, a load and an action:

```python
# myproject/_dashboards/funds.py
from lex import ui
from myproject.models import Fund


class Funds(ui.State):
    selected: str = ""

    @ui.rows
    def open_funds(self):
        return self.api.list(Fund, filters={"status": "investing"})

    @ui.load
    def open(self):
        self.selected = str(self.params.get("fund") or "")

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        fund = self.api.get(Fund, pk)
        if fund["status"] == "closed":
            raise ui.Refuse(f"{fund['name']} is already closed.")
        self.api.update(Fund, pk, {"status": "closed"})
        self.selected = ""
        return ui.toast(f"{fund['name']} closed.", tone="success")
```

The tests give it a stand-in for `self.api` with the methods the handlers call, and check what
happened:

```python
# tests/test_funds.py
from lex import ui
from myproject._dashboards.funds import Funds
from myproject.models import Fund


class FakeApi:
    """Stands in for self.api: answers from a dict and records what was written."""

    def __init__(self, funds):
        self.funds = {fund["id"]: dict(fund) for fund in funds}
        self.updates = []

    def list(self, model, *, filters=None, **options):
        wanted = (filters or {}).items()
        return [f for f in self.funds.values() if all(f.get(k) == v for k, v in wanted)]

    def get(self, model, pk):
        return self.funds[pk]

    def update(self, model, pk, data):
        self.updates.append((pk, data))
        self.funds[pk].update(data)
        return self.funds[pk]


FUNDS = [
    {"id": 1, "name": "Alpine Growth I", "status": "investing"},
    {"id": 2, "name": "Harbor Credit II", "status": "closed"},
]


def test_an_editor_closes_a_fund():
    api = FakeApi(FUNDS)
    result = ui.testing.run(Funds, "close", pk=1, grants=[(Fund, "edit")], api=api,
                            fields={"selected": "1"})
    assert api.updates == [(1, {"status": "closed"})]
    assert result.fields["selected"] == ""
    assert [toast.message for toast in result.toasts] == ["Alpine Growth I closed."]
    assert result.error == ""


def test_a_closed_fund_is_refused():
    api = FakeApi(FUNDS)
    result = ui.testing.run(Funds, "close", pk=2, grants=[(Fund, "edit")], api=api)
    assert result.refused == "Harbor Credit II is already closed."
    assert api.updates == []


def test_a_viewer_cannot_close_a_fund():
    api = FakeApi(FUNDS)
    result = ui.testing.run(Funds, "close", pk=1, grants=[(Fund, "read")], api=api)
    assert result.denied
    assert api.updates == []


def test_the_load_reads_the_page_parameter():
    result = ui.testing.run(Funds, "open", params={"fund": "7"})
    assert result.fields["selected"] == "7"


def test_the_table_lists_the_open_funds():
    rows = ui.testing.rows(Funds.open_funds, api=FakeApi(FUNDS))
    assert [row["name"] for row in rows] == ["Alpine Growth I"]
```

Run them with `lex pytest`, like the project's other tests: it sets Django up before the tests
import `lex.ui`.

## Running a handler

```python
result = ui.testing.run(State, "handler", user=..., grants=..., params=..., fields=..., api=...,
                        **arguments)
```

It runs the handler to the end - an action, a load or a job - and returns what it did. The
handler's own arguments are passed as keywords: `pk=1` above.

| Argument | What it does |
| --- | --- |
| `user="tester"` | the signed-in user's username; `self.user.name` is the same, and the e-mail is `<user>@example.test` |
| `grants=()` | what the user is granted: `(Model, "scope")` pairs, or strings such as `"myproject.Fund:edit"`; without them the user is granted nothing |
| `params=None` | the page's parameters, as `self.params` reads them: `{"fund": "7"}` |
| `fields=None` | values for the state's fields before the handler runs: what the page already shows |
| `api=None` | the object `self.api` returns. A handler that uses `self.api` needs one: without it, the call fails and the run ends as an unexpected error |

`requires=` is checked as it is in the app: without the grant, the body never runs and
`result.denied` is true.

## What a run returns

| `result.` | Holds |
| --- | --- |
| `fields` | the state's fields after the run, as a dict |
| `toasts` | the toasts shown, each with `.message`, `.tone` and `.description` |
| `goto` | the address of the last `ui.goto`, or `None` |
| `downloads` | the files handed to the user, each with `.filename` and `.data` (or `.url`) |
| `refused` | the message shown for an expected refusal - a `ui.Refuse`, or lex-app's API turning a request down as invalid - else `""` |
| `denied` | whether the user was told they are not permitted to do this |
| `error` | the "Something went wrong" message of an unexpected error, with its reference, else `""` |
| `running`, `failed` | what `ui.running` and `ui.failed` read after the run |
| `updates` | each change the browser was sent, in order, as a dict of field values |

A failure does not raise in the test: it is reported, as the user would see it. When a handler
breaks unexpectedly, `result.error` is set and the traceback is logged.

## Reading a table's rows

```python
rows = ui.testing.rows(Funds.open_funds, user=..., grants=..., params=..., api=..., fields=...,
                       search="", filters=None, table=None)
```

It returns the rows a table over that `@ui.rows` handler would get, after its search and filters.
Here a mistake raises, so the test fails on it, instead of the table showing an error:

- a handler with `requires=` that the user is not granted raises `PermissionError`;
- a handler that changes the state raises an error naming the field;
- a handler that returns something other than a list of dicts raises an error that says what to
  return.

Pass the table itself to see exactly what its browser receives - only the table's columns and the
row's `id`, through the table's own search:

```python
def test_the_search_box_finds_a_fund():
    table = ui.table(Funds.open_funds, columns=["name"], search=True)
    rows = ui.testing.rows(Funds.open_funds, api=FakeApi(FUNDS), table=table, search="alpine")
    assert rows == [{"id": "1", "name": "Alpine Growth I"}]
```

`filters=` maps a field to the one value its rows must have: `filters={"status": "investing"}`.
With `table=`, it may only name the table's filters that offer a list of choices; any other name
- a date filter too - raises an error that lists the ones it takes.

## Handlers that query the database

A handler with `requires=` may query the database directly. Its body runs on a worker thread with
its own database connection, so it does not see rows a test created inside an open transaction.
Put such tests in Django's `TransactionTestCase`, whose data every connection sees:

```python
from django.test import TransactionTestCase

from lex import ui
from myproject.models import Fund


class FundCount(ui.State):
    count: int = 0

    @ui.action(requires=ui.read(Fund))
    def recount(self):
        self.count = Fund.objects.count()


class FundCountTest(TransactionTestCase):
    def test_counts_the_funds(self):
        Fund.objects.create(name="Alpine Growth I", status="investing")
        result = ui.testing.run(FundCount, "recount", grants=[(Fund, "read")])
        self.assertEqual(result.fields["count"], 1)
```

Without `requires=`, the same query fails: a handler without it reaches data only through
`self.api`.

## Several steps in one test

`ui.testing.run` starts a fresh browser tab for each call. To run several handlers in one tab - a
load, then an action on what it loaded - use `ui.testing.Harness`, which `run` is built on:

```python
import asyncio


def test_open_then_close():
    api = FakeApi(FUNDS)

    async def scenario():
        async with ui.testing.Harness(grants=[(Fund, "edit")], params={"fund": "1"}, api=api) as h:
            await h.open_tab()
            await (await h.send(Funds, "open"))
            opened = await h.fields(Funds)
            await (await h.send(Funds, "close", pk=1))
            return opened, await h.fields(Funds)

    opened, closed = asyncio.run(scenario())
    assert opened["selected"] == "1"
    assert closed["selected"] == ""
```

`Harness` takes the same `user`, `grants`, `params` and `api` as `run`. `send` sends one event and
returns something to `await` until it has run; `fields` reads a state; `h.updates` and `h.results`
hold what the browser was sent and what the handlers returned.

Some events start others that run in the background: a [searched
choice](/pages/forms#a-choice-searched-on-the-server)'s search, the `ui_search` event, runs its rows
handler that way. Awaiting what `send` returns waits for the event itself only; await its
`.wait_all()` to wait for those too: `await (await h.send(...)).wait_all()`. To test which
matches a searched choice offers, call its rows handler directly:
`ui.testing.rows(Orders.customers, search="acme")`.

`ui.testing.run` and `ui.testing.rows` start their own event loop, so call them from ordinary
test functions, not from `async def` tests.

Next: [`lex reflex check`](/ship-it/check), which finds mistakes without running anything.
