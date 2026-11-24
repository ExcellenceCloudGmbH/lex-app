# lex.ui tables: rows from a handler, a filtered model, and what a table does by itself

**Status:** design approved in conversation on 2026-11-23; implemented on `feat/reflex-ui-tables` (section 5.13 records what building it settled).
**Step 2 of the pages piece.** Step 1 (`ui.record`, `ui.list`, the vocabulary) is
`2026-11-23-reflex-ui-pages-design.md` and PR #911. The handler layer it builds on is
`2026-11-23-reflex-ui-action-state-layer-design.md` and PR #909.

---

## 1. Summary

A table shows rows from one of two sources: a lex model, narrowed with `where=`, or a `@ui.rows`
handler, a plain function on the page's state that returns the rows as a list of dicts. lex-app
reads the rows as the user, searches, filters, sorts and pages them on the server, and sends the
browser only the visible page and only the fields the table uses.

```python
class PersonState(ui.State):
    name: str = ""

    @ui.load
    def open(self):
        self.name = logic.person(self.user.id, self.params.key)["name"]

    @ui.rows
    def roles(self):
        return [{**row, "goes_to": ui.address(company, key=row["company_id"])}
                for row in logic.roles(self.user.id, self.params.key)]

    @ui.action
    def end_role(self, key: str):
        logic.end_role(self.user.id, key)          # the roles table reads again by itself


@ui.page("/people/[key]")
def person():
    return ui.record(
        title=PersonState.name, load=PersonState.open,
        sections=[ui.section("Companies and roles", ui.table(
            PersonState.roles, open="goes_to", search=True, filters=["kind"], export=True,
            columns=["name", ui.column("roles", kind="tags"), ui.column("since", kind="date")],
            row_actions=lambda row: [ui.button("End role", PersonState.end_role, row["id"])]))])
```

A table over a model reads through lex-app's API, so the model's permissions decide which rows a
user sees:

```python
ui.table(Holding, where={"fund": FundState.id}, columns=["name", ui.column("amount", kind="money")])
```

---

## 2. Why: what the reviewed project showed

The reviewed customer project (about 119,000 lines of Reflex) has 36 tables in 16 files. Every
one is an RFDS `data_table` over a table state the project wrote by hand:

| Hand-written per table | Uses |
| --- | --- |
| A `_fetch_page` method | 34, about 775 lines in total |
| `query_rows(...)` (RFDS's in-memory search, sort and page) | 33 |
| `row_fields` (which fields the browser receives) | 46 |
| `page_size_options` | 34 |
| Hand-drawn cells (`render=`) | 21 |
| Money in the row's own currency (`ccy_field`) | 16 |
| Address templates, `row_href`, click handlers for row links | 24 |
| A separate export state per table | every detail table |

The fetches have one shape: check who is signed in; read the full list off the event loop
(`run_orm(logic_function, ...)`), from a server-only field, or from another state; turn any failure
into a message with a reference and log it; hand the list to `query_rows`. What repeats is reading
rows and paging them. That is the part this step wraps, as the handler layer wrapped handlers.

Two things in the project are not copied, on purpose:

- **A per-tab server cache of rows.** Fourteen tables keep the list they read so that a slow
  source (the other system) is not read on every click. lex.ui does not cache (decision T2): how
  stale data may be is a data decision, and a slow source caches where it is read, with Django's
  cache that lex-app already configures.
- **Access checks in front of the models.** The project's history tables read its own lex models,
  but the models refuse lex-app's API (`permission_list` returns False, `permission_read` denies)
  and a project-made gate runs before every read in the dashboard code. In lex-app the rule
  belongs in the model's `permission_list` / `permission_read`; then `ui.table(Model, where=...)`
  shows the right rows with no gate code in the page (decision T3).

---

## 3. Decisions

| # | Decision | Chosen | Rejected |
| --- | --- | --- | --- |
| T1 | Where rows that are not a lex model come from | A `@ui.rows` handler that returns the whole list; lex-app searches, filters, sorts and pages it on the server | The load fills a server-only field (rows kept in the state store for every tab, no refresh of its own); the load fills a normal field and RFDS pages in the browser (every row reaches the browser) |
| T2 | When a table reads again | Every time the table asks; no cache in lex.ui; a slow source caches with Django's cache | lex.ui keeps the list per tab (a cache with its own rules inside the UI layer); the author chooses per table |
| T3 | Narrowing a model table to the record on the page | `where={"fund": FundState.id}` on `ui.table`, sent to lex-app's API as list filters | A `@ui.rows` that queries the ORM (permissions by hand, every row loaded); naming it `filters=` (the word is taken by what the user picks) |
| T4 | Cells | A closed set of column kinds, plus `show=`, a function of the row that returns one `ui.*` part | Kinds only (one odd cell costs the whole table); `show=` only (money and status written differently on every page) |
| T5 | Where a row or a cell leads | `open=` takes a page, or the name of a field the rows carry, filled in Python with `ui.address(page, ...)` | A mapping in the table (`by="kind"`, a second small language); links only through `show=` (no row link) |
| T6 | Search and filters | The table's own search box and filters; lex-app applies them; a `@ui.rows` with a `search` parameter does its own search; on `ui.list` the search is at the top and kept in the address (`?q=`) | Filters as state fields bound to choice components (forms territory); search only |
| T7 | Extras in this step | A button on each row, export, totals | Selection and actions on several rows (later, with confirmations); expandable rows (not used) |

---

## 4. What exists already

**RFDS 1.0.0.** `DataTableMixin` (search `query`, `sort` up to three levels, `filters`, `page`,
`page_size`, `page_rows`, `total_rows`, `refresh`, an overridable `_fetch_page`, `row_fields`
projection); `query_rows(rows, sort=, filters=, query=, columns=, offset=, limit=)`;
`data_table(state=, columns=, caption=, row_key=, row_href=, row_actions=, totals=, toolbar=, empty=,
error=)`; `table_toolbar(search=, filters=, export=, result_count=)`; `filter_chips`;
`ExportMixin` (an `export(kind)` event over an overridable `_export_rows`, CSV and XLSX, an audit
hook); `export_menu`; `compute_totals` and the totals row.

**lex-app.** `lex_table(model, columns=, filters=, search_field=)` already builds one table state
per model and configuration, accepts state values in `filters=`, and turns RFDS filters into API
list parameters (`filter_params`); step 1's `ui.table(Model)` uses it. The handler layer runs a
plain function as the user, off the event loop, with `self.user`, `self.params`, `self.api`, the
ORM blocked unless `requires=` is declared, and one way to report failures.

**Consequence.** Nothing here needs a component of its own. Each table is a small RFDS table state
that lex-app generates; the new work is the generated state's `_fetch_page` and `_export_rows`, the
column kinds, and the checks.

---

## 5. Design

### 5.1 `@ui.rows`

```python
@ui.rows
def roles(self):
    return logic.roles(self.user.id, self.params.key)

@ui.rows(requires=ui.read(Role))
def roles(self):
    return list(Role.objects.filter(person=self.params.key).values("id", "name", "since"))

@ui.rows
def results(self, search: str):            # it searches; lex-app does not filter by the text
    return directory.search(self.user.id, search)
```

- A fifth handler kind beside `load`, `action`, `job` and `custom`. Its body is a plain function
  that returns a list of dicts (a generator is refused when it is declared).
- It runs off the event loop, as the user, through the handler layer: `self.user`, `self.params`,
  `self.api`; the database only with `requires=`, as for every handler.
- **It reads, it does not change.** It runs on a read-only copy of its state: reading a field
  works, writing one fails the read with "`@ui.rows` reads; change the state in a `@ui.load` or an
  `@ui.action`".
- It takes no arguments except an optional `search: str`. A `@ui.rows` with `search` gets the
  table's search text (`""` when there is none) and lex-app does not filter by the text itself.
- It is called by tables only. Calling it from a handler's body is refused like any sibling call.
- Values: text, numbers, `Decimal`, `date`, `datetime`, `bool`, `None`, lists of text (for `tags`),
  and addresses from `ui.address`. lex-app turns them into what RFDS expects (decimals as strings,
  dates as ISO text).

### 5.2 `ui.table`

```python
ui.table(source, *, columns=(), key=None, where=None, open=None, search=False, filters=(),
         export=False, totals=None, row_actions=None, empty=None, visible=None)
```

| Argument | Meaning |
| --- | --- |
| `source` | A lex model, or a `@ui.rows` handler (`PersonState.roles`) |
| `columns` | Field names or `ui.column(...)`s, in order |
| `key` | The field that names a row. A model: its primary key. Rows: `"id"` unless given |
| `where` | Models only. `{field or lookup: value}`; a value is fixed or a state value |
| `open` | A page (the row's key goes into its one argument), or the name of a field holding a `ui.address` |
| `search` | `True`: a search box over the text columns |
| `filters` | Column names the user can filter on; the choices are the column's values |
| `export` | `True`: CSV and Excel of all matching rows, as shown |
| `totals` | `{column: "sum" or "count"}`, over all matching rows |
| `row_actions` | A function of the row returning `ui.button`s / `ui.link`s |
| `empty` | The sentence when there are no rows at all |
| `visible` | As in step 1. A model table defaults to "users who may read the model"; a rows table to everyone who sees the page |

No page size and no paging argument: a table shows 25 rows a page, with RFDS's own page-size
choice. No caption argument: a table inside a section is named by the section's title; elsewhere by
the page's title.

### 5.3 `ui.column`

```python
ui.column(key, *, label=None, kind=None, currency=None, tones=None, open=None, show=None)
```

- **`kind`**: `text`, `number`, `money`, `percent`, `date`, `datetime`, `status`, `tag`, `tags`.
  Step 1's five plus four. Each is drawn by RFDS the same way on every page.
- **`currency`**: for `money`. A three-letter code (`"EUR"`), or the name of a field of the row
  that holds the code (`currency="currency"`). Without it: the home currency.
- **`tones`**: for `status`. `{word: tone}`; a word not listed is neutral.
- **`open`**: the name of a field holding a `ui.address`; the cell becomes a link.
- **`show`**: a function of the row returning one of `ui.text`, `ui.status`, `ui.link`. For a cell
  no kind describes. `show=` and `kind=` are not given together.
- A model table's columns are checked against the model, as in step 1; a rows table's when its
  rows are read (5.8).

### 5.4 `ui.address`

```python
ui.address(page, **values) -> str
```

The address of a page declared with `@ui.page`, with its route arguments and query values encoded.
Built in Python, on the server. A value in a row's `open=` field is followed only if it is an
address made this way: lex-app marks addresses it made and drops any other string, so text from
data can never become a link. A row whose `open=` field is empty or missing is not a link.

### 5.5 What reaches the browser

Only the visible page. Each row carries only:

- the columns' fields, and a `money` column's currency field;
- the row's key;
- the `open=` fields of the table and of its columns;
- the fields `show=` and `row_actions=` read. lex-app calls those functions once with a recording
  row while the page is built and collects the fields they read.

Everything else a `@ui.rows` handler returns stays on the server.

### 5.6 When a table reads its rows

- When it appears (it appears only after its page's or section's load has finished, as in step 1).
- On each page, sort, search and filter change, and on its refresh button.
- After an `@ui.action` or `@ui.job` of the same state finishes: every table on the current page
  whose rows handler belongs to that state reads again. (A model table reads again after a handler
  of the state its `where=` values come from.)
- When a handler of another state returns `ui.refresh(PersonState.roles)` (or a list of them).

No cache (T2). A slow source caches where it is read:

```python
@ui.rows
def holdings(self):
    return cache.get_or_set(f"holdings:{self.params.key}:{self.user.id}",
                            lambda: logic.holdings(self.user.id, self.params.key), 300)
```

### 5.7 Search, filters, sort

- **Rows tables:** lex-app applies the search (every word must occur in one of the text columns),
  the filters (equality on the filtered column; for `tags`, the tag is among the row's tags) and
  the sort (by column kind: numbers and money numerically, dates by date) with RFDS's `query_rows`.
- **Model tables:** search, filters and sort become API list parameters (lex-app's `filter_params`
  and `ordering`); the database does the work.
- **A filter's choices** are the column's distinct values among all rows (rows tables) or the
  field's choices / distinct values (model tables), at most 50; a column with more is refused as a
  filter when the rows are read, with the advice to use search.
- **On `ui.list`** a table with `search=True` puts its search box at the top of the page, and the
  page keeps the text in the address as `?q=`; opening `/people?q=ada` searches at once. Filters are
  not kept in the address.

### 5.8 Export and totals

- **Export:** an "Export" menu (CSV, Excel) in the table's toolbar. lex-app calls the same source
  for all rows with the current search, filters and sort, and writes the columns' labels and
  formatted values (RFDS's `ExportMixin`; large files as a background job with RFDS's tray). A
  model table's export needs the model's `permission_export`. The export is recorded with RFDS's
  audit hook (who, which table, how many rows).
- **Totals:** a totals row under the table, over all matching rows. `sum` for number, money and
  percent columns; `count` for any. A money column whose rows are in several currencies gets one
  totals row per currency.

### 5.9 Row actions

`row_actions=lambda row: [ui.button("End role", PersonState.end_role, row["id"])]`. A button may
take arguments after its handler; they are values of the row. The handler receives them as plain
values. The button shows its handler running for that row (`ui.running(handler, key)` from the
handler layer). At most two buttons are shown in the row; more go into the row's menu (RFDS's
rule). A dangerous action has no confirmation yet (that comes with forms); it is allowed, and
`lex reflex check` lists it.

### 5.10 What the page shows without being told

Loading rows while the table reads; the table's error in its own place, with a reference; `empty=`
when there are no rows at all; "No rows match …" with a "Clear search and filters" link when the
search or the filters leave nothing; the number of matching rows in the toolbar.

### 5.11 Mistakes and what is said

**When the pages are registered** (every page is built once, as in step 1), a `PageError` names the
page and the slot:

- a source that is neither a model nor a `@ui.rows` handler: "Return the rows from a `@ui.rows`
  handler on the state: `ui.table(State.rows, ...)`.";
- `where=` on a rows table: "Filter inside the handler; `where=` narrows a model.";
- `filters=` or `totals=` naming something that is not a column; a total other than `sum` / `count`,
  or `sum` on a column that is not a number, money or percent;
- `row_actions=` returning anything but `ui.button` / `ui.link`; `show=` returning anything but
  `ui.text`, `ui.status`, `ui.link`; `show=` together with `kind=`;
- `search=True` on a table with no text column;
- on a model table, a column or a `where=` field the model does not have.

**When the rows are read** a broken source shows the table's error with a reference, and the exact
reason goes to the log: not a list of dicts; a row without a column's field or without its key;
two rows with one key; a filter column with more than 50 values. `ui.testing` raises these as
failures instead.

### 5.12 Tests

Cluster 1 batches, next free letters, through `ui.testing` and Reflex's own event processor:

- rows are read as the user, off the event loop, on a read-only copy; a write is refused;
- search, filters and sort in memory; as API parameters for a model table with `where=` (a state
  value included); a `search` parameter takes over the search;
- the browser receives only the visible page and only the fields used;
- a same-state action makes the table read again; `ui.refresh` from another state;
- `open=` follows only `ui.address` values;
- export contents (CSV), totals per currency, the list page's `?q=`;
- every refusal in 5.11, with its exact text;
- **first:** a table on a page reached by in-app navigation reads the new page's address.

`ui.testing.rows(PersonState.roles, user=..., params=..., search="", filters={})` returns the rows
a table would get, so a project tests its rows in one line.

### 5.13 What building it settled

Built and tested the same day; these differ from, or add to, the sections above:

- **A row that leads to a page of its own is a button, not a link.** RFDS encodes every value it
  puts into a row-link template, so a whole address held in a row cannot be the identity cell's
  link. With `open="field"`, the first cell is a button whose click sends the row's key to the
  table's state, which looks up that row's `ui.address` on the server and opens it. A table whose
  `open=` is one page keeps a real link. A cell's `open=` is a real link.
- **The first column names the row and is drawn as text.** RFDS draws the identity cell itself and
  ignores a custom cell there, so a first column with `show=`, `open=`, `status`, `tag` or `tags`
  is refused with "Put a plain column first, such as the name".
- **A filter's "All" has the value `__all__`**: RFDS's dropdown refuses an empty value.
- **A model table's export and totals wait.** In this version `export=` and `totals=` on a model
  table are refused ("a model table cannot export or total yet"); a model table's filters take
  fields with choices or yes/no fields, and its search covers its first plain text field. A model
  table is keyed by its `where=` values: when one changes (another record of the same page), it
  appears again and reads with the new value. It does not read again after a handler of the state
  its `where=` values come from. A column's `open=` is refused on a model table: a model's field is
  data and never becomes a link.
- **Only the fields the table uses ever leave the handler.** Rows are cut down to those fields
  before search, filters and sort, the browser can filter only on the table's `filters=`, and a
  table without `search=True` ignores a search it is sent. Without this, the count of matching rows
  would let the browser learn a hidden value.
- **A total is a sum.** `count` is not offered: RFDS draws totals only on number columns, so a
  count of text would show nothing and a count of money would show as money. `totals=` takes
  `"sum"` on number, money and percent columns.
- **An export carries each amount's currency** (a "Currency" column when the currency is a field
  that is not shown) and the cells' types, and records the chosen filters in RFDS's audit hook.
- **A table that appears only after a load is not re-read by the page opener**: it appears again
  after the load and reads then. Only a table outside any load is told to `reopen`.
- **`ui.refresh(...)` is itself a list of Reflex events**, so a handler written in raw Reflex can
  return it as it is (needed while a project moves one page at a time).
- **`ui.testing.rows(handler, table=ui.table(...))`** runs the rows through that table's own
  rules and returns them as its browser gets them; a missing permission raises `PermissionError`.
- **On a list page the search box stays in the table's toolbar**, not above the table, and only a
  rows table keeps it in the address as `?q=`.
- **`currency=` tells a code from a field by its letters**: three capitals is a code (`"EUR"`),
  anything else a field name (`"ccy"`).
- **Every page open tells its tables to read again.** A table already on screen is not mounted
  again when the user moves to another record of the same page, so `ui_open` sends each of the
  page's tables a `reopen`. This also covers a mount event that carries the previous page's
  address (section 6).
- **`lex reflex check` does not yet list dangerous row actions** (5.9); it accepts `@ui.rows`.
- **Two additions for moving a project one table at a time** (from starting the reviewed
  project's refactor): `ui.embed(part, name=)` builds one lex.ui part into a Reflex component for
  a page not yet written with `ui.page`, and `self.user.permissions` gives a handler body the
  user's Keycloak permission list for a project's own logic.

---

- **A raw Reflex page can be where a link leads** (added 2026-11-24, from moving the reviewed customer
  project's tables): `ui.reflex_page("/orders/[key]")` declares a page registered with
  `app.add_page` as a link target, so `ui.address`, a table's `open=`, `ui.link(to=)` and
  `ui.button(to=)` take it like a `@ui.page` function. A link still names a page, never a route
  string typed into it. When the app starts, each declared route is checked against the app's
  pages.
- **A table that leaves the screen keeps none of its rows** (added 2026-11-24): `close` drops the
  page, the count, the totals and the filter choices from the tab's state.

### 5.14 Columns and rows a reader relies on (2026-11-24, batch 1dy)

Needs found while rewriting dashboards: a name that stays in place in a wide table, columns of
different widths, columns the user sizes, a file's rows in one scroll, a totals row that says what
it sums. Each is RFDS's own feature, exposed with the fewest new words:

```python
ui.table(CustomersState.customers, page_size=None, choose_columns=True,
         totals={"amount": "sum of all rows"},
         columns=[ui.column("name", kind="name", pin=True), ui.column("code", kind="code"),
                  "region", ui.column("amount", kind="money", currency="currency")])
```

- **One pinned column: the first.** `ui.column(..., pin=True)` is RFDS's `sticky="start"`. RFDS's
  data table puts every start-sticky cell at the left edge and reserves scroll padding for one such
  column, the identity cell, so only the first column -- the one that names the row -- may be
  pinned ("RFDS keeps one column in place while the table scrolls sideways: the first, which names
  the row. Pin it: ..."). Pinning several would need hand-made offsets; not added.
- **No width argument; two kinds instead.** Rule 6.1(4) of the pages design forbids optional
  styling, and test 1.806 refuses a `width` argument on every component. The need is about the
  values, so two kinds say what they are: `kind="name"` (a person's or a company's name) is RFDS's
  `entity` kind at RFDS's wide field width (`size.field.md`); `kind="code"` (an order number, an
  ISO code) is RFDS's identifier (`id`) at its narrow field width (`size.field.xs`). A plain column
  takes the room its values need, as before. Considered: `width="wide"|"narrow"` (refused by the
  rule), `min_width=`.
- **Resizing is RFDS's "Columns" menu.** RFDS's data table has no drag handle (WCAG 2.5.7: dragging
  is never required); its `column_chooser` shows and hides columns, moves them up and down, takes a
  width typed in pixels and "Fit to content". Drag resizing exists only in RFDS's AG Grid
  (`data_grid`), which lex.ui tables do not use. `choose_columns=True` adds the menu to the
  toolbar, on rows and model tables. The choice is kept in the table's state (`ChosenColumns`,
  `data.py`) and cleaned on the server: only the table's own columns, widths from 40 to 1,200 px
  (RFDS's range), and the first column always shown and first, because it names the row and is
  its link. It lasts until the tab is closed; saving it needs RFDS's saved views and a store (not
  added). Name: like `search=`, `filters=` and `export=`, it names what the user gets.
- **Every row: `page_size=None`, up to 200.** RFDS's rule DA-09: at most 200 rows reach the
  browser in a data table (`DataTableMixin.page_size_options` must be 1-200). `page_size=None`
  opens the table at 200 rows in one scroll, without the rows-per-page picker; past 200, RFDS's
  `pagination` appears below the table, so no row is out of reach. More than 200 rows in one
  scroll is RFDS's AG Grid (virtualised), a different engine; not added. `page_size=50` sets the
  rows a page (1-200), offered with 25, 50, 100 and 200. Name: `page_size`, as `lex_table()` and
  RFDS's state call it; `rows_per_page=` and `paged=False` were considered.
- **A totals row says what it sums.** `totals={"amount": "sum"}` totals the rows the search and
  filters leave, as before; `"sum of all rows"` totals every row the handler returns, whatever the
  user narrows (a handler that searches for itself is read again without the search). While a
  search or a filter is on, the label says which: "Total of matching rows" or "Total of all rows";
  otherwise "Total". All totals of one table cover the same rows ("write "sum" for each column,
  or "sum of all rows" for each"). Name: a second word for `totals=` rather than a new argument
  (`totals_of="all"` considered). A model table still cannot total (5.13).

## 6. Limits and risks

- **The address on mount.** Step 1 found that Reflex sends a component's mount event with the
  previous page's address after in-app navigation. A table appears only after its page's load has
  finished, so it should read the right address; the prototype proves it before the plan is
  written. If it does not hold, the table takes the page's values from the page state the opener
  recorded, not from the event.
- **Large rows tables.** A rows table holds all rows in memory for each read. The project's largest
  table is a few thousand rows; at about 10,000 a `@ui.rows` that pages for itself becomes worth
  adding (section 7).
- **One generated state per table.** Reflex states are classes; two tables with the same source and
  configuration share one, as `lex_table` already does. The configuration includes the columns,
  `where=` keys, search and filters.
- **Field recording.** `show=` and `row_actions=` are called once with a recording row. A function
  that reads a field only in some branch is still recorded, because Reflex evaluates both branches
  of `ui.switch` and `visible=` at build time.

## 7. Not in this step

- Selecting rows and acting on several at once (needs confirmations, with forms).
- A `@ui.rows` that receives page, sort and search and pages for itself.
- Facts grouped under titles with their kind given by data, a count on each tab, the tab kept in
  the address, "Try again" on a failed section, a second header tag, breadcrumbs, a notice with a
  title and a list: the step-1 port leftovers that are not tables.
- Forms, confirmations and the button that shows its handler running on a page header: the spec's
  former step 2, now step 3.

## 8. Order of building

1. Prototype: the address on mount after navigation; `@ui.rows` on a read-only copy through the
   handler layer; a generated table state over it with `query_rows`; field recording; RFDS's totals
   and export on a generated state.
2. The plan, from the prototype that ran green.
3. Implementation with reviews, then the ports: the reviewed project's detail page and search page
   again, and one history table as `ui.table(Model, where=...)` with its access check moved into
   the model. Numbers into the review document.

## 9. Acceptance

1. The detail page and the search page of the reviewed project port without a `ui.custom_block`
   for a table.
2. A history table ports as a model table with `where=`, its access check in the model.
3. A project test of a rows handler is one line with `ui.testing.rows`.
4. Every Reflex batch passes together.
