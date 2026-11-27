---
title: "Read and write records"
description: "Read and write records from a handler, as the signed-in user."
api:
  - "self.api"
---

`self.api` is how a handler reads and writes your models. Every call goes to lex-app's own API as
the user who is looking at the page, so a dashboard sees exactly what that user sees in lex-app's
grid, and a write goes through the same permissions, validation, lifecycle hooks and audit log.

## Reading

```python exec
from lex import ui
from docs_site.sandbox.models import Fund


class Portfolio(ui.State):
    investing: int = 0
    largest: str = ""

    @ui.load
    def open(self):
        self.investing = self.api.list(Fund, filters={"status": "investing"}).count
        top = self.api.list(Fund, ordering="-size", per_page=1)
        self.largest = top[0]["name"] if top else ""

    @ui.rows
    def recent(self):
        return self.api.all(Fund, filters={"vintage__gte": 2020, "status__in": ["investing", "fundraising"]},
                            ordering="-size")
```

```python
from lex import ui
from myproject.models import Fund


class Portfolio(ui.State):
    investing: int = 0
    largest: str = ""

    @ui.load
    def open(self):
        self.investing = self.api.list(Fund, filters={"status": "investing"}).count
        top = self.api.list(Fund, ordering="-size", per_page=1)
        self.largest = top[0]["name"] if top else ""

    @ui.rows
    def recent(self):
        return self.api.all(Fund, filters={"vintage__gte": 2020, "status__in": ["investing", "fundraising"]},
                            ordering="-size")
```

```python demo
ui.section(
    "Portfolio",
    ui.fact("Funds investing", Portfolio.investing, kind="number"),
    ui.fact("Largest fund", Portfolio.largest),
    ui.table(Portfolio.recent, columns=["name", "vintage", "status",
                                        ui.column("size", kind="money", currency="currency")]),
    load=Portfolio.open,
)
```

`list` returns one page of records, and its `.count` is the number of matching records across all
pages, so the count needs nothing more. The table needs every matching record, so its handler
returns `self.api.all(...)`, which reads them all; see [Every record](#every-record).

## The methods

| Call | Returns |
| --- | --- |
| `self.api.list(Model, filters=, search=, ordering=, page=1, per_page=25, fields=)` | one page of records, a list of dicts with `.count`, the number of matches across all pages |
| `self.api.all(Model, filters=, search=, ordering=, fields=)` | every matching record, a list of dicts, for a `@ui.rows` table; at most 10,000 |
| `self.api.get(Model, pk)` | one record, a dict |
| `self.api.create(Model, {...})` | the new record, as lex-app saved it |
| `self.api.update(Model, pk, {...})` | the record after the change; only the fields you pass change |
| `self.api.delete(Model, pk)` | nothing |
| `self.api.calculate(Model, pk)` | starts a calculation record's run, as lex-app's Calculate button does; the record, `IN_PROGRESS` |
| `self.api.log(Model, pk)` | the log of a calculation record's newest run, one Markdown text per entry |
| `self.api.file(Model, pk, field)` | a record's file, downloaded as the user: `{"data": bytes}` or `{"url": ...}` |

A record is a plain dict of the fields lex-app's API returns to this user. Each call is a request
to lex-app's API: in a handler that runs often, ask for what you need in one `list` rather than
one `get` per record.

## Filters, search and order

`filters=` takes a field name and the value it must have, as lex-app's grid filters:

| Filter | Matches |
| --- | --- |
| `{"status": "investing"}` | exactly that value |
| `{"status": ["investing", "fundraising"]}` | any of the values |
| `{"status__in": ["investing", "fundraising"]}` | the same, written as a lookup |
| `{"vintage__gte": 2020}`, `__gt`, `__lte`, `__lt` | greater than or equal, and so on |
| `{"name__icontains": "growth"}`, `__istartswith` | text, ignoring case |
| `{"fund__name": "Northbridge Growth I"}` | a field of a related record |
| `{"status!": "closed"}` | every value but this one |

A filter whose value is `None` or `""` is left out, so an empty choice means "all". Several
filters must all match.

```md alert warning
lex-app ignores a filter it does not recognise: a misspelled field or an unknown lookup filters
nothing, and you get every record. Filtering or ordering by a field the user may not read is
refused, and the user sees lex-app's reason.
```

- `search="growth"` matches, ignoring case, the first plain text field (one without choices) that
  your model declares itself. The fields lex-app adds to every model, such as `created_by` (who
  created the record), are never searched. A model without a text field of its own raises an
  error that says to filter with `"<field>__icontains"` instead.
- `ordering="-size"` sorts by a field, `-` first for descending; `"status,-size"` sorts by more
  than one. Two records that tie can come back in any order, so end with a field that is
  different on every record, such as `id`, when you read page after page yourself.
- `fields=["id", "name"]` keeps only those fields in each record.

## Related records

In the records `list` and `all` return, a foreign key comes as the related record's id, and
beside it, under `<field>__short_description`, the related record's name: the text your model's
`__str__` gives, which lex-app's grid shows too (`None` when the key is empty). A
commitment's row holds `"fund": 2` and `"fund__short_description": "Northbridge Growth II"`.
`fields=["fund"]` keeps both.

```python
@ui.rows
def commitments(self):
    return [{"id": row["id"], "fund": row["fund__short_description"], "amount": row["amount"]}
            for row in self.api.all(Commitment, filters={"investor": self.params.key})]
```

A [model table](/show-your-data/tables#rows-from-a-model) shows the name in a foreign-key column by itself.

```md alert info
The sample data on this site is not lex-app's API, and its rows carry no
`__short_description`: the demos look the names up themselves.
```

## Every record

`self.api.all(Model, ...)` returns every record the user may read that matches, as one list: what
a [`@ui.rows` handler](/show-your-data/tables#rows-from-a-handler) returns for a table. It takes the same
`filters=`, `search=`, `ordering=` and `fields=` as `list`.

```python
@ui.rows
def orders(self):
    return self.api.all(Order, filters={"region": self.params.key}, ordering="-placed_on")
```

- **It reads page after page**, 1,000 records at a time, in a stable order: by `ordering=` and
  then by the record's key. The pages are read one after another, not at one moment, so a record
  created or deleted meanwhile can shift a page, and a record may then be read twice or missed;
  the table shows it right on its next read. Its `.count` is the number of records read.
- **At most 10,000.** When more match, it reads no further than the first page and refuses: the
  table shows "There are 12,345 orders here, more than can be shown at once (10,000). Narrow them
  down first." The words for the records are your model's plural name. A `@ui.rows` handler holds
  every row in the server's memory, so read fewer with `filters=`: the user's region, this year.
- **A table over it does the rest**: it searches, filters (by date too), sorts, totals and exports
  every row, which a model table cannot. It reads them once and does all of that in the server's
  memory, until it reads again: see [When the handler runs again](/show-your-data/tables#when-the-handler-runs-again).

## Writing

```python exec
from lex import ui
from docs_site.sandbox.models import Investor


class Investors(ui.State):
    @ui.rows
    def rows(self):
        return self.api.all(Investor, ordering="name")

    @ui.action(requires=ui.create(Investor))
    def add(self):
        made = self.api.create(Investor, {"name": "Harbour Pension Trust", "country": "NL", "kind": "pension"})
        return ui.toast(f"Added {made['name']} as investor {made['id']}.", tone="success")

    @ui.action(requires=ui.delete(Investor))
    def remove(self, pk: int):
        try:
            investor = self.api.get(Investor, pk)
        except ui.ApiError as error:
            if error.status == 404:
                raise ui.Refuse("This investor has already been removed.")
            raise
        self.api.delete(Investor, pk)
        return ui.toast(f"Removed {investor['name']}.")
```

```python
from lex import ui
from myproject.models import Investor


class Investors(ui.State):
    @ui.rows
    def rows(self):
        return self.api.all(Investor, ordering="name")

    @ui.action(requires=ui.create(Investor))
    def add(self):
        made = self.api.create(Investor, {"name": "Harbour Pension Trust", "country": "NL", "kind": "pension"})
        return ui.toast(f"Added {made['name']} as investor {made['id']}.", tone="success")

    @ui.action(requires=ui.delete(Investor))
    def remove(self, pk: int):
        try:
            investor = self.api.get(Investor, pk)
        except ui.ApiError as error:
            if error.status == 404:
                raise ui.Refuse("This investor has already been removed.")
            raise
        self.api.delete(Investor, pk)
        return ui.toast(f"Removed {investor['name']}.")
```

```python demo
ui.section(
    "Investors",
    ui.button("Add an investor", Investors.add),
    ui.table(Investors.rows, columns=["name", "country", "kind"],
             row_actions=lambda row: [ui.button("Remove", Investors.remove, row["id"], tone="danger",
                                                       confirm="Remove this investor?")]),
)
```

Click **Add an investor**, then **Remove** on a row: each action writes through `self.api`, and the
table reads again because an action of its own state succeeded. **Reset sample data** puts the
investors back.

## When lex-app says no

When lex-app's API refuses a call, the call raises `ui.ApiError`:

| Attribute | Is |
| --- | --- |
| `.status` | the HTTP status of the answer; `0` when lex-app could not be reached |
| `.denied` | true for 401, 403 and 404: the user may not do this, or the record is not theirs to see |
| `.detail` | the body of the answer, usually with lex-app's reason in it |

You rarely catch it. Left alone, it fails the handler, and the user is told: "You are not
permitted to do this." when `.denied` is true, lex-app's own reason for any other refusal (a
validation error, a field the user may not change), and "Something went wrong" with a reference
when lex-app could not answer. Catch it, as `remove` does above, when you can say something more
useful. A record the user may not see answers 404, exactly like one that does not exist.

## What the user may see

`self.api` applies the rules your models already have, because it is lex-app's API answering:

- **Rows.** `list` returns only the records the user may read, and `.count` counts only those.
  `get` on any other record raises `ui.ApiError` with `.denied`.
- **Fields.** A field the user may not read is not in the records. Changing a field they may not
  edit is refused with lex-app's reason.
- **Actions.** Creating or deleting without the permission is refused as "not permitted".

These rules come from your models' `permission_*` methods and the user's Keycloak grants: see
[Where permissions come from](/who-can-do-what/grants).

## Querying the database directly

A handler may also use the Django ORM directly, but only when it says what it requires:

```python
from django.db.models import Sum


class Totals(ui.State):
    invested: str = ""

    @ui.load(requires=ui.read(Fund))
    def open(self):
        total = Fund.objects.filter(status="investing").aggregate(total=Sum("size"))["total"]
        self.invested = f"{total or 0:,}"
```

Without `requires=`, the query fails with an error saying that it bypasses lex-app's field and row
rules, and to read through `self.api` or declare what the handler requires. With it, the body
runs only for users who hold that permission, and a record it saves names the user as its creator
or editor.

```md alert warning
A direct query sees every record and every field: none of the row and field rules apply. Use it
for what the API cannot give you - an aggregate over many records, a model method, a calculation's
inputs - and keep anything the user may not see out of the state, because every field reaches the
browser.
```
