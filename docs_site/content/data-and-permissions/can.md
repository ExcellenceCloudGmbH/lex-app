---
title: "What the user may do"
description: "self.can, requires=, a rule of your own, visible= and a button that says why it cannot be pressed."
---

A dashboard asks what the signed-in user may do in three places: a handler's `requires=` decides
whether it runs, `self.can` lets a handler's body ask, and `visible=` or `disabled=` decide what
the page shows. Only the first two protect anything; the last two keep the page honest.

## A button that says why

```python exec
from lex import ui
from docs_site.sandbox.models import Fund


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


class Desk(ui.State):
    who: str = ""

    @ui.load
    def open(self):
        self._ask()

    @ui.action
    def check_again(self):
        self._ask()

    def _ask(self):
        if self.can(Fund, "edit"):
            self.who = f"{self.user.name} may edit funds."
        else:
            self.who = f"{self.user.name} may read funds, not change them."

    @ui.rows
    def funds(self):
        may_edit = self.can(Fund, "edit")
        rows = every(self.api, Fund, ordering="name")
        for row in rows:
            if not may_edit:
                row["why_not"] = "Only an editor may close a fund."
            elif row["status"] == "closed":
                row["why_not"] = "This fund is already closed."
            else:
                row["why_not"] = ""
        return rows

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        self.api.update(Fund, pk, {"status": "closed"})
        return ui.toast("Fund closed.")
```

```python
from lex import ui
from myproject.models import Fund


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


class Desk(ui.State):
    who: str = ""

    @ui.load
    def open(self):
        self._ask()

    @ui.action
    def check_again(self):
        self._ask()

    def _ask(self):
        if self.can(Fund, "edit"):
            self.who = f"{self.user.name} may edit funds."
        else:
            self.who = f"{self.user.name} may read funds, not change them."

    @ui.rows
    def funds(self):
        may_edit = self.can(Fund, "edit")
        rows = every(self.api, Fund, ordering="name")
        for row in rows:
            if not may_edit:
                row["why_not"] = "Only an editor may close a fund."
            elif row["status"] == "closed":
                row["why_not"] = "This fund is already closed."
            else:
                row["why_not"] = ""
        return rows

    @ui.action(requires=ui.edit(Fund))
    def close(self, pk: int):
        self.api.update(Fund, pk, {"status": "closed"})
        return ui.toast("Fund closed.")
```

```python demo
ui.section(
    "Funds",
    ui.message(Desk.who),
    ui.button("Check again", Desk.check_again),
    ui.table(Desk.funds, columns=["name", "status"],
             row_actions=lambda row: [ui.button("Close", Desk.close, row["id"], disabled=row["why_not"])]),
    load=Desk.open,
)
```

As an Editor, **Close** is greyed out on Northbridge Growth I only; point at it and it says the
fund is already closed. Switch **Viewing as** to Viewer and click **Check again**: the table reads
again, and every **Close** is greyed out with the reason "Only an editor may close a fund." The
buttons stay where they are, so the user learns why rather than wondering where they went.

The greyed button is a courtesy. What stops a viewer is `requires=ui.edit(Fund)` on `close`:
even if the browser sent the click, the handler would not run.

## Asking in a handler

Inside a handler body, `self.user` is the signed-in user and `self.can` asks about their grants:

| Helper | Is |
| --- | --- |
| `self.can(Fund, "edit")` | whether the user is granted `edit` on the model as a whole; the scope defaults to `"read"` |
| `self.user.id`, `.name`, `.username`, `.email` | who is signed in |
| `self.user.permissions` | every grant they hold, as Keycloak lists them: `{"rsname": "myproject.Fund", "scopes": ["read", "edit"]}` |

`self.can` takes a model, or a Keycloak resource name as text. It answers only for grants on the
whole model; a grant for single records is not enough (see
[Grants from Keycloak](/data-and-permissions/grants)). Use it to choose what to show or to explain
a refusal; to stop a handler from running, use `requires=`.

## `requires=` on a handler

`requires=` names what the user must be granted for the handler to run. Every handler takes it:
`@ui.load`, `@ui.action`, `@ui.job` and `@ui.rows`.

| Value | The user must hold |
| --- | --- |
| `ui.read(Fund)`, `ui.create(Fund)`, `ui.edit(Fund)`, `ui.delete(Fund)` | that scope on the model |
| `ui.permission(Fund, "export")` | any Keycloak scope on a model, or on a resource named as text |
| `ui.signed_in` | nothing beyond being signed in |
| `fund_team`, a function marked `@ui.rule` | what your rule decides: see [A rule of your own](/data-and-permissions/can#a-rule-of-your-own) |
| `[ui.read(Fund), ui.read(Investor)]` | all of them |

When the user does not hold it, the body never runs and the user is told "You are not permitted
to do this.", or a rule's own sentence. When it is a page kind's `load=`, they go to the error page
instead. `requires=` also lets the
body query the database directly; see [`self.api`](/data-and-permissions/api#querying-the-database-directly).

## `requires=` on a page

`@ui.page(requires=...)` takes what a handler's takes: a permission, `ui.signed_in`, a rule, or a
list of them. A user who does not meet it sees an access-denied notice instead of the page, and
its loads do not start. A permission also leaves the page out of their navigation. How the notice
is worded, and the way out it offers, is set on [The frame](/look-and-chrome/frame#when-a-user-may-not-see-a-page).

```python
@ui.page("/funds", nav="Funds", requires=ui.read(Fund))
def funds():
    ...
```

```md alert warning
A page's `requires=` decides what is shown, not what is allowed: a value that reaches the page
reaches the browser. Give each handler that reads or changes data its own `requires=`, or let
`self.api` apply lex-app's rules.
```

## A rule of your own

lex-app's permissions say what a user may do with a model. A project often has rules of its own:
only the team of a fund sees its page, only the desk that owns an order may close it. Write such a
rule as a function of the signed-in user and the page's values, mark it `@ui.rule`, and name it in
`requires=` wherever a permission goes - on a page and on any handler, alone or in a list:

```python
from lex import ui
from myproject.models import Fund
from myproject.teams import funds_of


@ui.rule
def fund_team(user, params):
    if str(params.get("key")) in funds_of(user.id):
        return True
    return "Only the fund's own team sees this fund."


@ui.page("/funds/[key]", requires=[ui.read(Fund), fund_team])
def fund():
    return ui.record(title=FundView.name, load=FundView.open,
                     actions=[ui.button("Close fund", FundView.close)])


class FundView(ui.State):
    name: str = ""

    @ui.load(requires=fund_team)
    def open(self):
        self.name = self.api.get(Fund, self.params.key)["name"]

    @ui.action(requires=[ui.edit(Fund), fund_team])
    def close(self):
        self.api.update(Fund, self.params.key, {"status": "closed"})
        return ui.toast("Fund closed.")
```

- **What it returns**: `True` grants. A sentence refuses, and is what the user reads. `False` (or
  nothing) refuses with lex-app's own words: "You are not permitted to do this." on a handler, the
  no-access notice on a page.
- **When it is asked**: on the server, on a worker thread, so it may query the database. lex-app's
  permissions are checked first; the rule is asked only if they are met. It is asked at most once
  per event, however often the handler's `requires=` names it.
- **It never opens by accident.** A rule that raises, or answers anything but `True`, `False` or a
  sentence, refuses. On a handler that is an unexpected failure: it is logged and the user gets a
  reference. On a page the user reads lex-app's no-access sentence, and the failure is logged.
- **On a page** the rule is asked when the page opens. Refused, the page shows the no-access
  notice with the rule's sentence and starts no load. A rule does not hide the page's entry in the
  navigation; a permission in the same `requires=` still does.
- **On a handler** it is checked with the permissions, before the body runs. As with any
  `requires=`, a handler that names a rule may query the database directly.
- **It stays a plain function.** `visible=` cannot take a rule, since `visible=` is decided in the
  browser. Call the rule in a load instead and keep the answer in a field:
  `self.may_close = fund_team(self.user, self.params) is True`, then `visible=FundView.may_close`.
- A rule must take exactly the two arguments `(user, params)`, and may not be `async`. A plain
  function given to `requires=` without `@ui.rule` is refused, with a sentence saying to mark it.

The page's values are `self.params` as a handler sees it: the route's arguments and the query.
`user` is the signed-in user, with `.id`, `.name`, `.username` and `.email`.

```md alert info
There is no live demo of a rule: a rule sees the user and the page's values, and on this site the
user is the same in both roles and the demos have no address. Test a rule with
[`ui.testing`](/ship-it/testing), as below.
```

```python
def test_only_the_team_closes_its_fund(monkeypatch):
    monkeypatch.setattr("myproject._dashboards.funds.funds_of", lambda user_id: {"1"})
    api = FakeApi(FUNDS)
    ours = ui.testing.run(FundView, "close", grants=[(Fund, "edit")], params={"key": "1"}, api=api)
    theirs = ui.testing.run(FundView, "close", grants=[(Fund, "edit")], params={"key": "2"}, api=api)
    assert ours.toasts and not ours.denied
    assert theirs.denied               # the user read "Only the fund's own team sees this fund."
```

## Showing and hiding

Every `ui.*` component takes `visible=`:

| `visible=` | Shows the component |
| --- | --- |
| `ui.edit(Fund)`, or any permission `requires=` takes | when the user holds it |
| `[ui.edit(Fund), ui.delete(Fund)]` | when the user holds all of them |
| `State.is_open`, a field of the state | when the field is true |
| `True` or `False` | always, or never |

```python
ui.button("Close fund", FundView.close, visible=ui.edit(Fund))
ui.message("This fund is closed.", tone="warning", visible=FundView.is_closed)
```

On a section of a `ui.record` page, `visible=` takes a permission only: the section's heading
stays, and the page says why its content is not shown.

Prefer `disabled=` to `visible=` for an action the user can see but not use right now. It takes
the reason as a sentence, or as a field that holds one: `""` means the button can be pressed. A
true/false field is refused when the page is built, with a message asking for the reason as text.

```python
ui.button("Close fund", FundView.close, disabled=FundView.why_not)       # a field of the state
ui.button("Close fund", FundView.close, disabled="Funds close at year end.")
```

Next: [Grants from Keycloak](/data-and-permissions/grants), where these permissions come from.
