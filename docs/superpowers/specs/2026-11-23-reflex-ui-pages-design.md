# lex.ui pages: page kinds with named slots, and a closed vocabulary

**Status:** design approved in conversation on 2026-11-23; step 1, tables and forms are implemented (6.13, 6.14).
**Second of four pieces.** The first, the action and state layer, is
`2026-11-23-reflex-ui-action-state-layer-design.md`.

---

## 1. Summary

A dashboard page is declared by choosing one of four page kinds and filling its named slots with a
small, closed set of lex components. lex-app decides everything about how the page looks and where
things sit; the author says only what is on it.

```python
from lex import ui

@ui.page("/funds/[key]", nav="Funds", requires=ui.read(Fund))
def fund():
    return ui.record(
        title=FundState.name,
        load=FundState.open,
        facts=[ui.fact("Vintage", FundState.vintage, kind="year"),
               ui.fact("Net asset value", FundState.nav, kind="money")],
        sections=[ui.section("Holdings", ui.table(FundState.holdings, columns=["name", "share"]),
                             empty="No holdings yet.")],
        actions=[ui.button("Close fund", FundState.close, tone="danger", confirm="Close this fund?")],
    )
```

There is no styling argument anywhere, no layout component, no loading or error branch, no route
string and no translation key in that page. Each of those is something lex-app does.

---

## 2. Why: what a real project showed

The first piece dealt with event handlers. The same reviewed customer project shows what happens to
the views when the component set is open and placement is free.

### 2.1 The design system leaks into project code

| In the project's view code | Count |
| --- | --- |
| `class_name=` arguments | 746 |
| Lines of the project's own stylesheet | 2,201 |
| `style=` arguments | 19 |

The project runs on a token-styled component library (RFDS) with an opinionated theme. Neither
prevented any of this, because every component accepts `class_name`, `style` and arbitrary
properties.

### 2.2 Placement is rewritten on every page

| Call | Uses |
| --- | --- |
| `rfds.stack(` | 676 |
| `rfds.cluster(` | 311 |
| `rfds.section(` | 110 |
| `rfds.card(` | 39 |
| `rfds.grid(` | 32 |
| A ready-made page template | 46, on about 35 routes, and always the generic one |

RFDS ships about twenty page floorplans (object page, list report, overview dashboard, wizard and
others). The project uses none of the specific ones: every page is a hand-built stack of stacks.

### 2.3 Conditions and translation calls drown the content

| Call | Uses |
| --- | --- |
| `rfds.t(` (a translation key, an English default, parameters) | 2,260 |
| `rx.cond(` | 1,502 |
| `rx.fragment(` (mostly the empty branch of a condition) | 850 |
| `rfds.inline_message(` | 408 |
| `rfds.button(` | 382 |
| `rx.foreach(` | 157 |
| `rx.match(` | 137 |
| `rfds.skeleton(` | 133 |
| `rfds.empty_state(` | 74 |
| `rfds.dialog(` | 37 |

Most of the conditions say one of five things: still loading, failed, empty, not allowed, or "this
step is the active one". The translation call is the single most frequent call in the views.

### 2.4 The pages fall into a handful of shapes

About 35 routes, of which roughly 30 are one of: a home or topic page, a searchable list, one
record with its parts, or a form (sometimes in steps, sometimes a tool that takes input and gives
back a result or a file). The remainder are applications of their own (a map, a board).

### 2.5 What follows

The gap is not missing components. It is that composition is open. A consultant, or an AI tool
working for one, is handed a hundred components and free layout primitives and reinvents placement
and look on every page. The result cannot be reviewed at a glance.

**The bar for this piece**, set by the user: it will be used to refactor that project, so it has to
be extremely easy to read and extremely easy to learn.

---

## 3. Where this sits

| Piece | What | State |
| --- | --- | --- |
| 1 | Action and state layer (`ui.State`, `@ui.load`, `@ui.action`, `@ui.job`) | Built; pull request open |
| **2** | **Page kinds and the closed vocabulary (this spec)** | **Designed** |
| 3 | Design rules as checks (content rules: counts, wording, tone) | Later |
| 4 | The calculation-model page | Later; needs a project that has calculations |

Piece 2 builds on piece 1: a page's load is a `@ui.load`, a button runs a `@ui.action` or `@ui.job`,
and the page reads `ui.running` / `ui.failed` so that the author does not.

---

## 4. Decisions

| # | Decision | Chosen | Rejected, and why |
| --- | --- | --- | --- |
| P1 | How fixed a page is | **Page kinds with named slots** | Free composition of opinionated components: placement stays the author's choice, which is half the problem. RFDS as it is plus lint rules: stops leaks but tells nobody where things go. |
| P2 | Which kinds first | **Four: overview, list, record, form** (a tool is a form with a result) | Two (list, record): faster to a proof, but home pages and forms stay hand-built. |
| P3 | What a slot accepts | **A closed vocabulary of about 15 lex components; anything else in a marked `ui.custom_block`** | Any RFDS component with styling arguments refused: a hundred components and free layout primitives still let placement leak in. Both without marking: whatever is unmarked spreads. |
| P4 | Conditions and repetition | **The page owns loading, failed and empty; components take `visible=`; `ui.each` and `ui.switch` for the rest** | Thin renames of Reflex's `cond` / `foreach` / `match`: the three-branch boilerplate stays on every page. Raw Reflex in slots: the situation of section 2.3. |
| P5 | Links | **A link names a page function or a record, never a route string** | Route strings checked against registered pages: still copied between modules. Free strings: broken links show only in a browser. |
| P6 | How a page is declared | **`@ui.page(route, …)` carries routing; the function returns a kind** | One decorator per kind: routing and look mixed, four decorators, dashboards need a second form anyway. A module-level declaration with no function: Reflex builds components lazily for a reason. |
| P7 | Translated text | **The author writes the English sentence; it is the message id** | Translation keys as today: every label twice as long. No translation: lex-app's users work in two languages. |
| P8 | Where a page's load is named | **On the kind (`load=`), the same word as on a section** | On the page decorator: two places to look, and a dashboard has no page decorator. |

---

## 5. What exists already, and what was checked

### 5.1 In RFDS

- **Floorplans.** `object_page`, `list_report`, `overview_dashboard`, `wizard_page` and about sixteen
  more. Each validates itself and raises: a title and a path are required; at most one primary
  action; an object page needs at least one section, unique section ids, a bounded number of header
  facets and of grid tables; an overview dashboard requires an as-of date and three to six KPIs.
- **Components**, about a hundred, in eight groups (actions, display, feedback, inputs, layout,
  navigation, overlays, tables), plus charts, grids and page-level patterns (filter bar, exports,
  record page, wizard).
- **A form root** (`rfds.form`) that submits a dictionary of strings once.
- **Translation.** `rfds.t(msg_id, default, **values)` resolves in the browser for the user's
  language. The message id may be any non-empty string; the default is ICU MessageFormat text, so
  parameters and plurals work. Catalogues are gettext `.po` files in which the id is the context and
  the English default is the message. RFDS's extractor finds only literal `rfds.t(...)` calls.
- **Slot marks.** Components carry a slot attribute, so a built page can be inspected by structure.

### 5.2 In lex-app

- `lex_page` (chrome, navigation entry, permission, the opener that starts a page's loads),
  `lex_chrome`, the LEX theme generated from lex-app's tokens.
- `lex_table` and `lex_grid` over a model through lex-app's API, `lex_view` (one lex-app page inside
  a dashboard), `record_model`.
- The handler layer of piece 1, including the view helpers `ui.running`, `ui.failed`,
  `ui.progress_of`, `ui.cancel` and the test harness.

### 5.3 Checked in Reflex 0.9.13

- **A page function is evaluated in every backend process before it serves.** With compilation on,
  the compiler evaluates every page; with compilation skipped (a backend-only process), it still
  evaluates each page that uses state. So what a kind declares when it is built — its loads, its
  link targets — can be registered at that moment and is in place before the first request.
- **The route registered for the opener is fixed when the page is declared**, and the opener looks
  the page's loads up by route at request time (piece 1). So loads registered when the page is
  built are found.

### 5.4 Consequence

This piece is a curated facade over RFDS, not a new design system. It adds no components of its
own and almost no styling: spacing between slots belongs in lex-app's theme file, never in a
component.

---

## 6. Design

### 6.1 Rules the API is held to

1. **One page of things to learn**: two decorators, four kinds, fifteen components, three structure
   helpers. If the list outgrows a printed page, something comes out.
2. **The same word means the same thing everywhere**: `title`, `actions`, `sections`, `load`,
   `empty`, `visible`, `kind`, `tone`, `to`, `icon`.
3. **A list slot holds the component of the same name in the singular**: `facts=[ui.fact(…)]`,
   `figures=[ui.figure(…)]`, `tiles=[ui.tile(…)]`, `sections=[ui.section(…)]`,
   `fields=[ui.field(…)]`, `filters=[ui.field(…)]`, `steps=[ui.step(…)]`. The one exception is
   `actions=[ui.button(…)]`, because `ui.action` is the handler decorator.
4. **One way to do each thing.** No alternative spellings, no optional styling.
5. **Every error says what to write instead**, naming the page and the slot.

### 6.2 `ui.page` and `ui.dashboard`

```python
@ui.page(route, *, nav=None, icon=None, requires=None, params=())
def fund(): ...
```

- `route` — the path, with its parameters in brackets (`"/funds/[key]"`).
- `nav` — the navigation heading to list the page under; omitted, the page is reachable by link only.
- `icon` — an icon name for the navigation entry.
- `requires` — who may see the page: `ui.read(Model)` and the other values `requires=` takes in
  piece 1. It decides what is shown; handlers still enforce.
- `params` — the names of query parameters the page accepts besides its route parameters.

`ui.page` gives the page lex-app's chrome, its navigation entry and the opener, as `lex_page` does
today, and makes the function a link target. The browser tab's title and the navigation label come
from the kind's `title`. `lex_page` remains as an alias that warns that it is deprecated.

A dashboard that is not a route — the project's `main()` on the home page, a model's record
dashboard — is marked `@ui.dashboard` and returns a kind the same way. It takes no arguments.

### 6.3 The four kinds

Every kind takes `title`, `load` and `actions`.

| Kind | Further slots, in the order they appear on the page | Use it for |
| --- | --- | --- |
| `ui.overview` | `figures`, `tiles`, `sections` | A home or topic page: a few numbers, links to where the work happens |
| `ui.list` | `filters`, `table` | Finding things: search, filter, open one, export |
| `ui.record` | `status`, `facts`, `sections`, `tabs` | One thing: its key facts, then its parts |
| `ui.form` | `fields` or `steps`, `submit`, `result` | Entering something, at once or in steps; a tool is a form with a `result` |

```python
@ui.page("/funds", nav="Funds", requires=ui.read(Fund))
def funds():
    return ui.list(
        title="Funds",
        filters=[ui.field("vintage", kind="year")],
        table=ui.table(Fund, columns=["name", "vintage", "currency"], open=fund),
        actions=[ui.button("New fund", to=new_fund)],
    )

@ui.page("/funds/new", requires=ui.create(Fund))
def new_fund():
    return ui.form(
        title="New fund",
        fields=[ui.field("name", required=True), ui.field("vintage", kind="year")],
        submit=FundState.create,
    )

@ui.dashboard
def main():
    return ui.overview(
        title="Portfolio",
        load=HomeState.open,
        figures=[ui.figure("Funds", HomeState.funds), ui.figure("Net asset value", HomeState.nav, kind="money")],
        tiles=[ui.tile("Funds", "Search and open a fund", to=funds, icon="list")],
    )
```

**What lex-app decides:**

- **Placement.** The order of the slots on the page and the page's width are fixed per kind: a form
  is narrow, a list takes the full width.
- **Actions.** The first action is the primary one, unless it is dangerous. Exactly one further
  action is a button; two or more further actions go into a "More actions" menu. This is RFDS's own
  page-header rule, and the kinds follow it. A button with `tone="danger"` must carry `confirm=`.
- **Sections.** Each has a heading. With more than three, the page shows in-page navigation on its
  own. `tabs=True` on a record shows one section at a time, for pages with large tables.
- **Forms.** A form is submitted once, to one handler, which receives the values as a dictionary
  keyed by each field's `key`. There are no per-keystroke handlers. `steps=[ui.step("Details",
  fields=[…]), …]` makes it a wizard; `result=` is what the page shows after a successful submit.
- **Filters.** A filter's value lives in the page's address as a query parameter, so a filtered
  list can be linked to. A table over a model applies the filters itself; a page with its own load
  is loaded again and reads them from `self.params`.

**Each kind is built on RFDS's floorplan for its shape** and keeps that floorplan's limits, reworded
as lex errors. Where a floorplan requires something the author should not have to supply — the
overview's as-of date — lex-app supplies it (the time of the page's last load).

### 6.4 What the page shows without being told

| State | What is shown | Where it comes from |
| --- | --- | --- |
| The page's load has not finished | A skeleton in the shape of the kind | `ui.loaded(load)`, which the handler layer gains: "not running" is also true before a load has started |
| The page's load failed, on a `ui.page` | The error page of piece 1, with "Try again" | A page-declared required load |
| The page's load failed, on a `ui.dashboard` | The failure in place of the content | A load started on mount reports in place (piece 1) |
| A section's own load has not finished | That section's skeleton; the rest of the page is usable | `ui.loaded` of the section's `load` |
| A section's own load failed | The failure inside that section | `ui.failed` of the section's `load` |
| A table or a section has nothing to show | Its `empty="…"` sentence | The rows it was given |
| The viewer lacks the page's permission | lex-app's access-denied content | `requires=` on `ui.page` |

How the loads are started follows piece 1's two kinds of load:

- On a `ui.page`, the kind's `load` is registered as the page's required load and each section's
  `load` as an optional one, once the project's module is loaded and lex-app registers its pages
  (6.13). The opener starts them after sign-in and grants, with the right address. Where a load is
  registered, not the handler's own `optional=`, decides whether its failure takes over the page.
- On a `ui.dashboard`, there is no opener: each load is started when its part of the page mounts.

The author writes the same thing in both cases.

### 6.5 The vocabulary

| Component | What it is | Arguments |
| --- | --- | --- |
| `ui.text` | A sentence or a paragraph | the sentence, `**values` |
| `ui.fact` | A label and its value | `label`, `value`, `kind=` |
| `ui.figure` | One number that matters | `label`, `value`, `kind=`, `change=`, `to=` |
| `ui.tile` | A link to where work happens | `title`, `text`, `to=`, `icon=` |
| `ui.status` | A small badge | the word, `tone=` |
| `ui.message` | A notice in place | the sentence, `tone=` |
| `ui.table` | Rows, paged on the server | a model or rows, `columns=`, `open=`, `row_actions=`, `empty=`, `export=` |
| `ui.grid` | lex-app's spreadsheet-like grid over a model | a model, `columns=`, `filters=` |
| `ui.chart` | A bar, line, area or pie chart | `kind`, `data`, `x=`, `y=`, `title=` |
| `ui.button` | Runs a handler or opens a page | `label`, a handler or `to=`, `confirm=`, `ask=`, `tone=`, `icon=` |
| `ui.link` | Goes somewhere | `label`, `to=` or `record=` or `href=` |
| `ui.field` | One input | `key`, `label=`, `kind=`, `options=`, `required=`, `help=`, `value=` |
| `ui.section` | A titled part of the page | `title`, content…, `load=`, `empty=` |
| `ui.step` | One step of a wizard | `title`, `fields=` |
| `ui.column` | A table column that needs more than its name | `key`, `label=`, `kind=` |

**Five arguments mean the same thing wherever they appear:**

- `kind=` — what a value is, for display and for input alike: `text`, `number`, `money`, `percent`,
  `date`, `datetime`, `year`, `bool`, `choice`. lex-app formats it for the user's locale.
- `tone=` — one of `neutral`, `info`, `success`, `warning`, `danger`.
- `to=` — a page function, with its parameters by name (`to=fund, key=row.id`).
- `visible=` — a permission, written with the same values `requires=` takes (`visible=ui.edit(Fund)`),
  or a state value (`visible=FundState.phase == "open"`). Display only; the handler still enforces.
- `icon=` — a name from the icon set. There is no icon component.

**Three behaviours are built in:**

- **A button knows its handler.** It disables itself and shows progress while the handler runs
  (`ui.running`). This is the self-disabling button piece 1 promised; the reviewed project keeps
  144 busy flags by hand for it.
- **`confirm=` and `ask=` replace dialogs.** `confirm="Close this fund?"` asks yes or no before the
  handler runs. `ask=[ui.field("reason", required=True)]` opens a small dialog with those fields and
  passes their values to the handler:

  ```python
  ui.button("Decline", TicketState.decline, tone="danger", ask=[ui.field("reason", required=True)])
  ```
- **Content in a section stacks from top to bottom**, and charts and figures that follow one another
  share a row. There is no row, column or spacing component.

**A field's label** defaults to its key with a capital (`ui.field("vintage")` is labelled
"Vintage"); `label=` overrides it. A field with `options=` is a choice.

### 6.6 Structure

- `visible=` on every component, for showing something only to some users or in some states.
- `ui.each(items, render)` repeats `render` for every item.
- `ui.switch(value, {"a": …, "b": …})` shows one of several.
- `ui.custom_block(component, reason="…")` is the marked way out: a raw Reflex or RFDS component,
  with one sentence saying why the vocabulary did not do. `lex reflex check` lists every one for
  review, as it lists `@ui.custom` handlers.

Raw `rx.cond`, `rx.foreach`, `rx.match` and `rx.fragment` are outside the vocabulary.

### 6.7 Links

- **A link names a page.** `ui.link("Open", to=fund, key=row.id)`. The route's parameters are
  required by name; a query parameter must be declared in the page's `params`. A missing or unknown
  parameter is an error naming both pages.
- **A table opens a page the same way.** `ui.table(Fund, open=fund)` makes each row a link to that
  page with the row's key.
- **A record in lex-app.** `ui.link("Open in lex-app", record=(Fund, row.id))`; lex-app builds the
  address.
- **An outside site.** `href="https://…"` only; it always opens in a new tab and carries the mark of
  an external link.
- **Handlers follow the same rule.** `ui.goto(fund, key=7)` replaces `ui.goto("/funds/7")`; a string
  is accepted only as a full outside URL. This changes piece 1's `ui.goto` while its pull request is
  open, so that "go there" is said one way in views and in handlers.
- **How a link looks follows from where it sits**: in text a text link, in `actions=` a button, in
  `tiles=` a tile, in a table a cell link.

### 6.8 Text

- **The sentence is the message.** `ui.button("Close fund", …)`,
  `ui.text("{count} open requests", count=State.n)`. lex-app passes the sentence to RFDS's
  translation as both the id and the English default, so it is translated in the browser exactly as
  RFDS's own text is, with the same parameter and plural forms.
- **`lex ui messages`** collects every sentence from the text arguments of the project's `ui.*`
  calls, by reading the source, into the catalogue files — one per language — and reports
  translations that are no longer used.
- **Data is never translated.** A value from the state is shown as it is.
- **Model labels come from the model.** `ui.table(Fund, columns=["name"])` shows the field's own
  label, as lex-app does.
- **Rewording an English sentence makes it a new message**; the old translation is reported as
  unused. This is the known trade-off of this approach and usually what is wanted.

### 6.9 Enforcement

**When the page is built** — at startup, and in every test that builds the page — each of these is
an exception that names the page and the slot, shows what it got and says what to write:

- a slot given something outside the vocabulary;
- a link with a missing or unknown parameter;
- a page without a title;
- a `tone="danger"` button without `confirm=`;
- a field key used twice in one form;
- one of RFDS's floorplan limits (for example more than one primary action).

**`lex reflex check`** reads source only and gains:

| Finding | Result |
| --- | --- |
| A raw `rx.…` or `rfds.…` component inside a `ui.page` or `ui.dashboard` function, outside `ui.custom_block` | Fails |
| Text built with an f-string or `+` where a sentence is expected (it cannot be collected) | Fails; says to use `{name}` parameters |
| A route written as a string in `ui.link` or `ui.goto` | Fails |
| A `ui.custom_block` | Listed for review, with its reason |
| An outside link | Listed for review |
| A sentence with no translation in a configured language | Warning |
| A page still written with `lex_page` and raw components | Note: "not on a page kind yet" |

### 6.10 Tests

- **Each component and each kind** is built and checked by its structure — which slots are filled,
  which text, which link target — through RFDS's slot marks, not by matching compiled JavaScript.
  Light and dark are both checked.
- **Each rule of 6.9** has a test that breaks it and reads the message: the messages are part of the
  product.
- **For project authors**, one addition to piece 1's harness:

  ```python
  shown = ui.testing.page(fund, user="u-7", grants=["app.Fund:read"], params={"key": "7"})
  assert "Close fund" not in shown.buttons
  ```

  It reports what the page shows: its title, sections, buttons, links with their targets, and what
  is hidden from this user.
- **The look** of the four kinds is checked with screenshots rendered headless, on the CPU.

### 6.11 Migration, page by page

| Today | With page kinds |
| --- | --- |
| `@lex_page(route, title=…, nav=…, requires=…, on_load=…)` | `@ui.page(route, nav=…, requires=…)`; title and load on the kind |
| `rfds.page_template(…)` around `rfds.stack(…)` of sections | `ui.record(…)`, `ui.list(…)`, `ui.overview(…)` or `ui.form(…)` |
| `rfds.key_value(rfds.kv_item(…), …)` | `facts=[ui.fact(…), …]` |
| `rx.cond(loading, rfds.skeleton(…), rx.cond(error, rfds.inline_message(…), content))` | Nothing: the page does it |
| `rx.cond(rows, table, rfds.empty_state(…))` | `empty="…"` on the table or section |
| `rfds.t("some.key", "Close fund")` | `"Close fund"` |
| `rfds.button(…, loading=State.busy, disabled=State.busy)` and the busy flag | `ui.button("Close fund", State.close)` |
| `rfds.dialog(…)` with a confirm handler and an open flag | `confirm="…"` or `ask=[…]` on the button |
| `rfds.link(…, href=some_module.route_for(key))` | `ui.link("Open", to=page, key=key)` |
| `class_name="…"`, `style={…}` | Not accepted |
| Anything that does not fit | `ui.custom_block(component, reason="…")` |

Pages written with `lex_page` and raw components keep working, so a project moves one page at a
time.

### 6.12 What changed between the conversation and this spec

- The page's load is named on the kind (`load=`), not on `ui.page` (decision P8). The first sketch
  in the conversation showed it on the decorator; a dashboard has no page decorator, and a section
  already takes `load=`, so one place and one word is easier to learn.
- `ui.facts(...)` became `facts=[ui.fact(...)]`, and `ui.field` takes the handler's key first, to
  hold rule 3 of 6.1.
- `ui.empty` and `ui.tabs` were dropped in favour of `empty=` and `tabs=True`.
- `ui.dashboard` was added, so a page that is not a route is marked as clearly as one that is.
- A permission in `visible=` is written with the same values `requires=` takes, such as
  `ui.edit(Fund)`; the conversation's sketch used a separate helper.

### 6.13 What a prototype of step 1 changed

Step 1 was prototyped before its plan was written; its code and 22 tests ran green. It changed
five things in this spec:

- **Actions follow RFDS's page-header rule** (6.3), not "more than three collapse into a menu".
- **A page is registered once the project's module is loaded**, not when it is built. `@ui.page`
  only records the function; lex-app then calls it once to learn the kind's title and loads. A page
  function cannot be called when it is decorated, because it may name a page declared below it.
- **The handler layer gains `ui.loaded(handler)`**, and decides whether a load owns the error page
  from where it is registered for the page (6.4).
- **A table over rows held in a state, and filters, move to step 2.** RFDS's paged table needs a
  table state of its own; how such a table stays fresh when a handler changes the rows is decided
  after a real search list has been ported. In step 1 `ui.table` takes a model.
- **`kind=` in step 1** is `text`, `number`, `money`, `percent` or `datetime`; the others arrive
  with the form fields.

### 6.14 What building forms settled

Built on 2026-11-24 after a spike against RFDS 1.0.0 (`ui.form`, `ui.field`, `ui.step`, `confirm=`,
`ask=`; batches 1dh-1dk). These differ from, or add to, the sections above:

- **The handler receives the values as its last argument, called `values`.** `def create(self, values)`;
  a button with values of its own and `ask=` puts them first: `def decline(self, key, values)`. The
  signature is checked when the page is built ("It receives what was entered as its last argument,
  called `values`: `def create(self, values): ...`"). Submit is an `@ui.action` or an `@ui.job`;
  a load or a raw handler is refused.
- **lex-app checks before the body runs, on the server.** Every declared field is read from what the
  browser sent (anything undeclared is dropped), checked for `required=` and for its kind, and turned
  into a Python value. A problem is kept as `ui_invalid["Handler.name|field"]`, shown at the field
  by RFDS's own error slot, and the body does not run (no toast, nothing left running). The values
  the body receives:

  | `kind=` | Input (RFDS 1.0.0) | The handler gets | Nothing entered |
  | --- | --- | --- | --- |
  | `text` | `text_input`; `textarea` with `lines=` | `str`, stripped | `""` |
  | `number` | `number_input` | `int`, or `Decimal` with decimals | `None` |
  | `money` | `money_input` (`currency=` as on `ui.fact`, else the home currency) | `Decimal` | `None` |
  | `percent` | `percent_input` (5.25 % is posted as 0.0525) | `Decimal` ratio | `None` |
  | `date` | `date_input` | `datetime.date` | `None` |
  | `datetime` | the browser's `datetime-local` in RFDS's `field` frame: RFDS has no date-time input | `datetime` in the app's time zone | `None` |
  | `year` | `text_input`, numeric, four digits | `int` | `None` |
  | `bool` | `checkbox` | `bool` (`required=True`: must be ticked) | `False` |
  | `choice` | `select` up to 12 fixed options; else `combobox` | the option's own value (`str`, `int`) | `None` |
  | `choices` | `checkbox_group` up to 12 fixed options; else `multi_select` | a `list` of the options' own values, in their order | `[]` |

- **Nothing reaches the server while the user types.** Text, number, year and date-time inputs are
  uncontrolled (`default_value`); RFDS's controlled inputs (money, percent, date, box, choice) hold
  their value in a browser-side client state until the form is sent. The form is RFDS's `rfds.form`,
  sent once as one dictionary.
- **A handler receives one set of fields.** Building a page tells its handler which fields it
  receives; another page or button giving the same handler other fields is refused, naming where
  the first set was declared. *Cost:* two forms cannot share a handler; each gets its own (a shared
  body goes into a `_method`).
- **The page is RFDS's page header over RFDS's form, at RFDS's `form` width.** RFDS has no form
  floorplan besides the wizard. The submit button is the page's one primary action, so `actions=`
  on a form are all secondary (one as a button, more in the menu). **The submit button is called
  after the handler**: `create` reads "Create", `build_report` "Build report". *Cost:* the handler's
  name is user-facing text; no label argument was added.
- **`result=` shows after a successful run, below the form at full width**, and leaves as soon as
  the form is sent again -- also when the new input is refused at a field -- and on a new visit.
  It holds what a section holds, or `ui.section`s. The handler keeps what it shows in the state,
  large or private data in a backend field (`_name`), read by a `@ui.rows` or a computed value; no
  store was added. A `@ui.job` as submit shows RFDS's progress bar with its message and a Stop button
  while it runs, under the form.
- **A wizard is one form shown in steps, not RFDS's `wizard_page`.** RFDS's wizard needs a
  `WizardState` subclass with a field schema, step ids, save-for-later and knockouts per form: a
  second way to write a form. `steps=` instead keeps all fields in the one `rfds.form`, the other
  steps hidden, with RFDS's `stepper` above. Continue sends the form; the server checks the fields up
  to the current step and moves on (`ui_step`); only the last step's submit runs the body, once,
  with every step's values; a problem in an earlier step returns to it. Back is `ui_step_back`.
  *Cost:* no save-for-later and no "check your answers" page; a wizard of one step is refused.
- **`confirm=` is RFDS's confirmation dialog; `ask=` RFDS's dialog holding an RFDS form.** The
  question follows RFDS's own wording rule, enforced as a lex error: a question naming the action
  ("Close this order?"), never "Are you sure?". The button that agrees repeats the button's label.
  With `ask=` the question, if given, is the dialog's description; the dialog's values are checked
  like a form's, and the dialog stays open with the message at the field until they pass.
- **One dialog per button as written, placed beside the page.** Which dialog is open, and the values
  the button passes (a row's key), are kept in the tab's state (`ui_asking`, `ui_asking_args`), so a
  table's row action has one dialog, not one per row. The values come back from the browser, as a
  row button's always did: the handler checks them.
- **Every `tone="danger"` button must have `confirm=` or `ask=`**, also in a page header's menu and
  among a table's `row_actions` (which the tables design still allowed, 5.9). *Cost:* existing pages
  with a bare dangerous button stop building until they ask.
- **Every button shows its handler running**, not only a row's button: an action's or a job's button
  is loading while it has a run.
- **`kind=` is one list for showing and for entering.** `ui.fact` now takes `date`, `year` (no
  thousands separator), `bool` (the words Yes and No), `choice` and `choices` (joined by commas) too.
- **A choice among many is a type-ahead box over a list the page holds; a server-searched choice is
  not built** (since built without a new handler kind: see 6.15). `options=` takes a list, a dict of value to wording, or a field of the handler's own
  state (filled by the page's load), checked against that field on the server. RFDS's `combobox`
  searches on the server only through an event handler and a results field per field: in lex.ui that
  would be a sixth handler kind or a generated state per field, for one input. Left out rather than
  half built: a choice among thousands loads its options with the page (RFDS's own guidance is server
  search above 200 and a value-help dialog above 1,000).
- **Several of a list is `kind="choices"`, the same field as a choice.** It takes `options=` as a choice does
  and gives the handler a list of the options' own values (texts, or whole numbers) in their order, `[]` when none is chosen; `required=True` means
  at least one ("Choose at least one."), and a value that is no option is refused at the field ("Choose from the
  options."). The input follows RFDS's own rule, at the same line as the dropdown: its checkbox group up to 12
  options, its multi-select above them or for options from the state. *Cost:* none chosen is `[]`, not `None`; no
  `max=`.
- **Several lines of text are `lines=`, not a kind.** `ui.field("notes", lines=6)` is RFDS's `textarea`,
  that many rows high and as wide as the form, uncontrolled like the one-line input; the handler gets a `str`
  with its line breaks, trimmed at the ends like every text (`values["codes"].splitlines()` for a pasted
  list). `lines=` is a whole number of 2 or more, refused when the field is declared otherwise ("A field of
  one line is the default: leave `lines=` out."), and goes with text only: any other kind refuses it when the
  page is built ("`lines=` goes with text, the default kind: leave `kind=` out."). Added 2026-11-24 (batch 1dn).
  *Cost:* no character limit or counter (RFDS's `max_length=`, `show_count=`).
- **A file field is not built** (`kind="file"` says to use `ui.custom_block`).
- **A new visit starts every form over**: the opener clears the messages, the wizard steps, the
  results and an open dialog -- except the result and the refusal of a form declared `keep=True` (6.18).
- **`ui.testing.run(...)` results gain `invalid`** (field -> message) for a project's form tests.

A tool page, whole -- a form, a job with progress as submit, a result with a table and a download:

```python
class InvoiceReportState(ui.State):
    _lines: list[dict] = []                       # stays on the server, per browser tab

    @ui.job
    def build_report(self, values):
        lines = []
        for month in range(1, 13):
            lines += invoices_of(self.user.id, values["customer"], values["year"], month)
            yield ui.progress(month / 12, "Reading invoices")
        self._lines = lines

    @ui.rows
    def lines(self):
        return self._lines

    @ui.action
    def download(self):
        out = io.StringIO()
        csv.DictWriter(out, ["number", "amount"], extrasaction="ignore").writerows(self._lines)
        return ui.download(data=out.getvalue().encode(), filename="invoices.csv")


@ui.page("/invoices/report", nav="Invoices")
def invoice_report():
    return ui.form(
        title="Invoice report",
        fields=[ui.field("customer", required=True), ui.field("year", kind="year", required=True)],
        submit=InvoiceReportState.build_report,
        result=[ui.section("Invoices", ui.table(InvoiceReportState.lines, columns=["number", ui.column("amount", kind="money")],
                                                totals={"amount": "sum"})),
                ui.button("Download", InvoiceReportState.download)],
    )
```


### 6.15 A searched choice, a refusal in place, a toast with a link

Built on 2026-11-24 (batches 1ds-1du), on top of 6.14:

- **A choice searched on the server is `options=` a `@ui.rows` handler -- no new handler kind.** 6.14
  left it out because RFDS's combobox searches only through an event handler and a results field per
  input. The handler the author writes already exists: a rows handler that takes `search` receives the
  typed text (one without `search` returns every row and lex-app matches the words against the labels,
  folded as RFDS's browser filter folds them). The event handler and the results field are lex-app's,
  shared by every searched field: `ui.State.ui_search(field, text)` and `ui_matches[field]`, keyed by
  the field's id (the receiving handler's module-qualified name and the field's key). The browser names
  only that id; the rows handler is looked up in the server's registry and runs as the user, as a
  table's. Each row gives **`id`** (what the handler receives, as the row's own value: an `int` stays an
  `int`, a UUID a UUID) and **`label`** (the words the user reads) -- named keys rather than "the first
  field that is not the id", which depends on a dict's order and is harder to explain.
- **Only a page of matches reaches the browser** (`forms.MATCHES`, 50), each `{value, label}`: none of
  a row's other fields. The first page is asked for when the field appears (`on_mount`), so the list is
  ready when it opens; RFDS debounces typing (250 ms).
- **Offers belong to the user they were made to** (review, 2026-11-24). `_ui_offered` and
  `ui_matches` used to grow for the life of the tab. They are now dropped when another user signs
  in on the tab (checked when a search is asked, a form is sent and a page opens -- a value offered
  to the previous user is refused at the field), and a new visit drops the matches the browser was
  given (each searched field asks again when it appears). The offers themselves stay across the
  same user's visits, so a kept form still accepts a value chosen before the user left (6.18),
  bounded at 1,000 per field as before.
- **A choice's value is the option's own value** (review, 2026-11-24). The first build gave a
  searched choice's `int` id as an `int` and anything else as text, and a fixed choice always as
  text (fixed options had to be texts). Now every kind of option reaches the handler as its own
  value: fixed options may be texts or whole numbers (`options=[2024, 2025]`, `{1: "Q1"}`), options
  from a state field keep that field's values, and a searched row's `id` arrives as the row gave it
  (an `int`, a UUID). The browser still shows and sends text; the server maps it back. The names
  `choice`/`choices` stay.
- **Searches run per field, and never hold the tab** (review, 2026-11-24). `ui_search` was one
  Reflex handler marked `supersedes=True`, and Reflex supersedes by (event name, tab): a search in one
  field cancelled a search running in another field of the same form, and every keystroke held the
  tab's lock while the rows handler ran. Now `ui_search` is an ordinary event that only records the
  field's latest search (a number and the text) -- in order with the tab's other events, since Reflex
  runs a tab's ordinary events one after another -- and returns `ui_search_run(field, number)`, a
  background event: it copies the state under the lock, runs the rows handler without it, and offers
  the matches only if that number is still the field's latest. A slow, older search that ends after a
  newer one is dropped; another field's search is not touched; the user's other clicks go on while a
  search runs. *Considered first:* only dropping `supersedes` (correct, but each keystroke would wait
  for the rows handler while holding the tab), and making `ui_search` itself a background event (its
  order would be the order the lock is taken in, which Reflex's Redis lock does not promise).
- **The server accepts only a value it offered to this tab.** Every value offered is remembered on the
  server (`_ui_offered`, a backend field: never sent), so a value chosen from an earlier search stays
  valid while the user types on; a forged or merely existing value is refused at the field ("Choose one
  of the options."). Past 1,000 remembered values only the latest search's count. A failed search
  (the rows handler raised, or a row without `id` or `label`) is handled as a table's: logged with what
  to write and a reference, the reference shown at the field.
- **RFDS's server-search combobox shows its value as it is**, not an option's label. lex.ui gives the
  combobox the chosen match's label (under a name the server ignores, `<key>__shown`) and sends the id
  beside it in a hidden input under the field's key. *Cost:* the chosen item carries no check mark in
  the open list.
- **A searched choice starts empty and chooses one.** `value=` is refused (the server cannot word, or
  accept, a value it never offered); `kind="choices"` with a searched source is refused: RFDS's
  multi-select has no server search. *Cost:* an edit form cannot show the current customer in the box.
- **It works in a form, a wizard's step and a button's `ask=`**, as every field. A project's unit test
  sends `ui_search` through `ui.testing.Harness` (awaiting `.wait_all()`, which includes the search's
  run) before it sends the form.
- **A form's refusal is shown in the form, above its submit button** -- where the user is looking --
  in RFDS's `inline_message` (warning tone, as the toast it replaces; `role="alert"`: it is new and the
  form did not do what was asked). It is kept per handler (`ui_refused[key] = {title, message}`) and
  leaves when the form is sent again, whatever comes of that send, and on a new visit -- as the result
  of 6.14 does (a form declared `keep=True` keeps both: 6.18). It is not also a toast: RFDS's rule is that a warning is never *only* a toast, and the
  same sentence twice, one of them persistent, is noise. A refusal by lex-app's API (not permitted, a
  4xx) is a refusal too; an unexpected failure stays a toast with its reference, since nothing in the
  form can fix it. One way to refuse, `raise ui.Refuse(...)`, wherever the handler runs: no
  form-error return value was added.
- **`ui.Refuse(message, title=)`: an optional short heading.** In a form it heads the inline message;
  outside a form the refusal is a toast as before, its first line the title and its second the
  sentence (RFDS's toast `description`). *Considered:* a `heading=` word; `title=` is the word the
  page kinds, sections and RFDS's own messages already use.
- **An action's `ask=` dialog stays open until the action succeeds**, so its refusal shows in the
  dialog, above its buttons; before, it closed as soon as the values passed. A job's dialog still
  closes when the job starts -- the user keeps working while it runs -- so a job's refusal from a
  dialog is a toast, headed by its title. `confirm=` dialogs (no form) close first, as before.
- **A toast offers to open something with `link=ui.link(...)`**: `return ui.toast("Your export is
  ready.", link=ui.link("Open", to=history_page, key=report_id))`. No action API was invented: the
  link is written as every link is, with the same targets (`to=` a page or a `ui.reflex_page`,
  `record=(Model, pk)`, `href=` an outside address) and the same checks, run when the handler makes
  the toast, so a wrong link fails the handler, not the user's click ("ui.toast(link=): a link to page
  `export_page` (/exports/[key]) is missing key. ... Write `to=export_page, key=...`."). It becomes
  RFDS's `ToastAction` -- one button, the link's words, opening the address as `ui.link` would
  (`record=` and `href=` as outside addresses) -- and RFDS keeps a success or info toast with an action
  open 10 seconds. `visible=` is refused: a toast's link is always shown. RFDS's rule that a toast's
  action is also reachable on the page is the author's, said in `ui.toast`'s docstring. *Considered:*
  `action=` (RFDS's word, but it takes an event) and a `ui.toast_action`; both are a second way to say
  where something leads.

### 6.16 A section that opens and closes (2026-11-24, batch 1dz)

A need found while rewriting dashboards: a part of a page closed until the user opens it -- the
fields a file was read with, the lines left out -- with a count in its title, "Fields read (12)".

```python
result=[ui.section("Orders", ui.table(ImportState.orders, columns=["name"])),
        ui.section("Fields read", ui.table(ImportState.fields_read, columns=["name"]), collapsed=True)]
```

- **`collapsed=True` is RFDS's section as a native disclosure** (`rfds.section(collapsible=True,
  default_open=False)`: `<details>`/`<summary>`, opened in the browser with no round trip). It works
  wherever a section has its own heading: a form's `result=`, and `ui.embed(ui.section(...))`.
  Name: a section is open by default, so the word says the exception, as `required=` does.
- **The count comes from the section's table.** A title is a sentence, not a state value
  (sections are anchors and table captions by their title), so a count written into the title
  would need a second way to give a title. Instead a closed section that holds one table shows,
  once the table has read its rows, the table's row count through RFDS's translation
  (`"{title} ({count, number})"`) -- the same number the table's toolbar shows; before that, the
  title alone. A closed section without a table has its title alone. The table reads its rows
  when the page shows it, also while the section is closed (the content of `<details>` is
  mounted), so the count is there before the user opens it. *Cost:* a count of anything but a
  table's rows is not offered.
- **A record page's sections are always open.** RFDS's object page lists them under "On this
  page" and has no closed section; `collapsed=True` there is refused, pointing to `tabs=True`.


### 6.17 Tabs inside a page (2026-11-24, batch 1ea)

A need found while rewriting dashboards: two or more views of one result side by side as tabs --
a table per region, or the figures and a chart. **Tabs existed already**: `ui.record(tabs=True)`
shows a record's sections one at a time (RFDS's object page with `navigation="tabs"`). The same
argument now works where sections also are, a form's `result=`; no `ui.tabs` was added.

```python
ui.form(title="Region report", submit=RegionReportState.build_report, fields=[...], tabs=True,
        result=[ui.text(RegionReportState.note),
                ui.section("North", ui.table(RegionReportState.north, columns=["name"])),
                ui.section("South", ui.table(RegionReportState.south, columns=["name"]))])
```

- **Each section's title is its tab** (RFDS's `tabs` with one `TabSpec` per section, switched in
  the browser through a client state, as RFDS's object page does); the title is not repeated as a
  heading inside the tab. What comes before the first section -- a sentence, a button -- is above
  the tabs and shared by all; a part after a section is refused ("Put what every tab shares
  first, then the sections").
- **The same rules as a record's tabs.** Two or more sections; each its own title; `visible=` on a
  section is a permission, RFDS's tab permission (a refused tab is not drawn), never a state value;
  a closed section is refused in a tab ("a tab is opened by its tab"). A section's `load=` stays a
  part load. RFDS mounts only the open tab, so a tab's table reads its rows when the tab is opened.
- *Not added:* a count on each tab (RFDS's tab badge): a table in a tab that is not open has not
  read its rows, so the count would be missing exactly where it is wanted.


### 6.18 A form's result kept across visits: `ui.form(keep=True)` (2026-11-24)

A user starts a long job from a form, leaves the page, comes back, and should find what it found.
Built in batch 1dx.

- **What existed, and what did not.** A state's fields already survive a visit: a page without a
  load never resets them, and a load's `keep=("customer",)` exempts fields from its reset. A job
  keeps running when the user leaves (unless `stop_on_leave=True`) and writes its result. What did
  not survive was the form's result flag: the opener starts every form over (6.14). So `keep=` on
  the state alone shows an empty form; one option was added.
- **`ui.form(..., keep=True)` keeps the result flag across visits**, the same word as the load's
  `keep=` and `ui.private(keep=True)`: "survives a new visit". Back while the job runs, the form
  shows its progress; back after it finished, the result. Its wizard step and field messages still
  start over. *Considered first:* `@ui.load(keep=...)` alone (does not reach the flag), a job
  option (the result is declared on the page, not the handler), keeping every form (an entry form
  should start over).
- **What was typed is not kept by lex-app** (it may be personal): a field shows it again through
  `value=` on a state field the handler sets -- `ui.field("customer", value=ReportState.customer)`,
  `self.customer = values["customer"]` (the kept form keeps that state's fields; see below).
- **A kept result shows only while what it shows is kept.** On a new visit the opener checks the
  submit handler's state: if a `ui.private(keep=True)` field of it is gone (its idle time or age
  passed, another process, another user), the result does not show over nothing; the form says,
  under it, "The last result is no longer available: results are kept only for a while. Send the
  form again." A private field that is not kept leaves with every visit and does not decide: what
  reads it says it is gone where it reads it (action design 6.11).
- **A kept form shows its handler's last failure under it**, in RFDS's inline message (warning
  tone), besides the toast: the user may have been away when it was toasted. Sending the form again
  clears it.
- **A kept form keeps its refusal too, in the one place a refusal shows** (6.15's refusal in place): the
  opener keeps `ui_refused` for a kept form's handler, so a `ui.Refuse` stays above the submit
  button, headed by its title, whether the user stayed or came back; the line under the form then
  stays empty (`ui_failed` holds the same sentence), so a refusal is never shown twice. Under the
  form remain what has no place above the button: an unexpected failure's toasted reference, and
  "The last result is no longer available ...". A form that is not kept drops its refusal on a new
  visit, as 6.15 says.
- **A searched choice (6.15) starts empty on a kept form too**: `value=` is refused for it, and the
  values offered to the tab (`_ui_offered`) are not part of the visit, so a value chosen before the
  visit away is still accepted when the user chooses it again.
- **One `keep=True` is enough** (review, 2026-11-24; this replaces the first build's cost, "the
  result's fields must be kept by the page's load too ... lex-app does not check this", which took
  three declarations nothing checked). A kept form's result lives in the submit handler's state and
  in the states of the `@ui.rows` handlers its result's tables read (found from the result's own
  vars). Their `ui.private` fields stay across visits as if declared `keep=True`, and a load of
  those states keeps all their fields (it still runs and sets what it sets). The one case lex-app
  cannot imply is a result that shows a field of *another* state that the page's load resets: that
  is refused when the page is built -- "Keep it in ReportState, the state the form submits to, or
  write `@ui.load(keep=("note",))` on HeaderState.open." *Considered first:* checking the three
  declarations and refusing what is missing (three things to write for one wish), and implying keep
  for every state the result shows (a load of an unrelated, shared state would stop resetting).
- **A reconnect is not a new visit** (review, 2026-11-24). Reflex sends the page's `on_load` again
  whenever its socket reconnects (`state.js`: `hydrate` and `on_load_internal` on every connect), so
  a laptop waking or a network blip used to drop the tab's private values and start every form
  over. The opener records the address and the socket connection (Reflex's `sid`) it opened on; the
  same address on a new connection is the same visit: nothing is dropped or started over, the
  visit count stays (a `stop_on_leave` job keeps running), and the page's loads run again without
  resetting what the visit holds (a load resets its state once per visit). A link to the same page
  on the same connection is a new visit, as is a reconnect on another address. A reload (F5) of
  the same page also arrives on a new connection, and the server cannot tell it from a reconnect:
  it is the same visit too, which matches the rest of the state, which a reload keeps as well.
  *Considered first:* a visit id kept in the browser (the reloaded `on_load` events are built on
  the server from the route, so nothing of the browser's can ride on them), and not running the
  loads again (a reload would then never refresh the page's data).

### 6.19 lex-app's frame, configured: areas, breadcrumbs, no access, a page opened, a project's rule (2026-11-24)

A need found while rewriting dashboards: a project with several areas of work ("Orders", "Reports",
"Tools", each with several pages) had its own navigation in areas, breadcrumbs, a "you have no access"
page with its own way out, and a "recently used" list. As lex.ui pages they lost all of it, because
lex-app's frame decides how a page looks (goal 4). A per-project wrapper was built and withdrawn
(`2026-11-24-reflex-ui-project-chrome-design.md`): it was a second chrome. Instead lex-app's own frame
takes a few generic parts, each a need of any project with several areas; nothing in it draws.

**One place: `ui.frame(...)`, once, in the dashboard module, beside the `@ui.page`s.**

```python
ui.frame(nav=["Orders", "Reports", "Tools"],
         denied="Ask your administrator for access.", request_access=access_request,
         opened=remember_page, sign_out="https://portal.example.com/sign-out")
```

*Why there, not in Django settings:* the areas are the same words as the pages' `nav=` beside it, and
`request_access=`, `opened=` and `sign_out=` are a page, a function and an address that lex-app
checks as it checks a link (a setting would be a dotted string, checked late). It is called once; a
second call from another module is refused (a reload of the same module replaces it). Every argument
is optional. *Considered:* settings (above); one argument per feature on every `@ui.page` (repeats
itself on every page). *Not added:* the product name stays `LEX_DASHBOARD_TITLE` and the badge
`LEX_ENVIRONMENT` -- values of a deployment, beside each other in its environment; no logo or colours,
since the look is lex-app's (section 8).

- **Areas are `nav=`.** A page's `nav="Orders"` already listed it under a heading; that heading is
  its area. A page with route arguments (`/orders/[key]`) cannot be opened from the navigation, so its
  `nav=` places it in the area -- its breadcrumbs, as section 1's example writes it -- without listing it.
  `ui.frame(nav=[...])` -- the same word -- gives the areas' order; a page whose `nav=` is
  not in that list stops the start with the list to choose from (a typo would make a new area).
  Without it, areas come in the order their first page is declared. Pages in an area keep their
  declaration order, `@ui.page`s and raw pages alike (a `@ui.page` takes its place when declared).
  The current entry is RFDS's rule: its route, or a route below it (`/orders/7` is under `/orders`,
  so a record page under its list's route marks its list); the area holding the current entry has its
  heading marked too (lex-app's stylesheet, `:has()`, no state). A raw page joins with the same words:
  `ui.reflex_page("/legacy", nav="Tools", title="Legacy export", icon="download")` -- `title=` is its
  label, as on `@lex_page`; its `requires=` only hides its entry, since lex-app does not guard a raw page.
- **Breadcrumbs come from the area and the page.** The frame shows RFDS's breadcrumbs above the page:
  the area (text, an area is not a page), then the page. A page that is not listed in the navigation
  has a parent: the first listed page whose table opens it (`open=`), else whose button or link leads
  to it (`to=`), in declaration order -- `Orders / Orders / Order 7`; without a `nav=` of its own it
  takes its parent's area. The parent is a link; the current page's title may be a state value. A
  page with no area has no breadcrumbs. No page writes a crumb. *Cost:* one level of parent only; a page of a record's record shows its area and
  itself.
- **No access: `denied=` and `request_access=`.** RFDS's access-denied notice (`rfds.access_denied`)
  is what every refused page shows, with the project's sentence (`denied=`) instead of RFDS's and a
  "Request access" link (`request_access=`: a page, a `ui.reflex_page`, or an `https://` or `mailto:`
  address). Its "Switch account" link -- sign in with another account -- goes to the sign-out address.
  The page a refused load goes to (`/ui-error`) shows the same notice. *Considered:* a whole page of the
  project's (the withdrawn wrapper again); a `ui.link` (RFDS's notice words its links itself).
- **`sign_out=`** is where the user menu signs out, and where "Switch account" leads: a page or an
  `https://` address; by default lex-app's `/logout`, which ends the dashboard's sign-in and lex-app's.
  A project's address should end there too, or the dashboard's sign-in lasts until it expires.
- **`opened=`: a function lex-app calls when a page opens**, `opened(user, page)`, with the signed-in
  `ui` user and the page: `page.route`, `page.address` (with its values), `page.title` (a state title
  as it stands after the load), `page.area`, `page.params`. For a "recently used" list or an audit
  entry. It runs after the page's required loads succeeded (at once on a page without them), on a
  worker thread, not awaited: the page never waits for it. A failure is logged, never shown. It is not
  called for a refused page or one whose load failed. It is called once a visit: when the socket
  reconnects on the same page (section 6.18's same visit), the page's loads run again but it is not
  called again.
- **A project's rule in `requires=`: `@ui.rule`.** `requires=` took lex-app's permissions; a project
  also has rules of its own ("only the team of the order's region"). A function of the user and the
  page's values, marked `@ui.rule`, is accepted wherever `requires=` is -- `@ui.page` and every
  handler -- alone or in a list with permissions:

  ```python
  @ui.rule
  def region_team(user, params):
      if params.get("region") in teams.regions_of(user.id):
          return True
      return "Only the region's team sees its report."

  @ui.page("/reports/[region]", nav="Reports", requires=[ui.read(Report), region_team])
  ```

  It returns `True`, `False`, or the sentence the user is refused with. It runs on the server, on a
  worker thread (it may query the database), once per event however often it is named, like the
  user's permissions; a rule that raises, or answers anything but `True`, `False` or a sentence,
  refuses -- on a handler as an unexpected failure (logged, a reference for the user), on a page with
  lex-app's no-access sentence (logged). As for any `requires=`, a handler that declares a rule may
  query the database directly. For a page the opener runs it: a
  refused page shows the no-access notice with the rule's sentence and starts no load; its navigation
  entry stays (a rule cannot hide it in the browser). For a handler it is checked with the
  permissions; its sentence is the refusal. A page's `requires=` now takes a list, as a handler's does.
  `visible=` does not take a rule (it is decided in the browser); the message says to set a state field
  in the load instead, where the rule may be called: `region_team(self.user, self.params)`.
  *Considered:* `ui.permission("...")` with a project string (a Keycloak grant, not a rule); a
  `requires=` that takes any function (a typo'd handler would pass silently).

### 6.20 A calculation's page: `ui.calculation(Model, ...)` (2026-11-24, batches 1ef-1ei)

The owner's decision (2026-11-24): a tool that writes to other systems, needs an audit trail and keeps
a record people come back to is a lex-app calculation model, run on lex-app's worker with lex-app's
status, audit and history. lex.ui gets one page kind over such a model: a form for its inputs, the run
while it goes, its result, and the past runs -- with permissions taken from the model. It is written
for any project with calculations; the example is a region report.

**What lex-app already offers, and what the page uses of it** (read in `docs/calculations/`,
`CalculationModel`, `One.py`, `CalculationLogTreeView`, `latest_calculation.py`):

| Need | lex-app's own mechanism | Through |
| --- | --- | --- |
| Create the record | the create endpoint (`permission_create`, validation, audit entry) | `self.api.create` |
| Start the run | the Calculate button's request: `PATCH ... calculate=true`, answered 202 with the record `IN_PROGRESS`; the run goes to the calculation thread pool, and from there to a Celery worker when Celery is on. A `calculate_on_create` model starts on its create | `self.api.calculate` (new) |
| Status | `is_calculated`: `NOT_CALCULATED`, `IN_PROGRESS`, `SUCCESS`, `ERROR`, `CANCELLED`, `ABORTED` -- written by whichever process ran it | `self.api.get` |
| The error | `calculation_error_message` (or `error_message`) when the model declares one: the exception's text, a blank line, the traceback | `self.api.get` |
| Progress | **no fraction**: a run reports by logging (`LexLogger`). Its log lines are stored as `CalculationLog` rows (an atomic run's when it commits) and, while it runs, kept as live text in the calculation cache | `self.api.log` (new) |
| The log of a past run | the newest run's id of a row (`lex_reserved_calculation_id`, resolved by the grid's list request) and its `CalculationLog` rows, read as the user | `self.api.log` (new) |
| Files | a `FileField` of the record, served by `api/<model>/file-download` as the user, audited | the result's download button |
| Stop | `PATCH ... cancel=true` (`CalculationModel.cancel`): revokes a worker's task, or stops a cooperative in-process run | the run's Stop button |

**The API, as a reader writes it:**

```python
from lex import ui
from reports.models import RegionReport, RegionReportLine

@ui.page("/reports/regions", nav="Reports", requires=ui.read(RegionReport))
def region_reports():
    return ui.calculation(
        RegionReport,
        title="Region report",
        fields=[ui.field("region", kind="choice", options={"north": "North", "south": "South"}, required=True),
                ui.field("year", kind="year", required=True)],
        result=[RegionReport.orders,
                ui.fact("Revenue", RegionReport.revenue, kind="money"),
                RegionReport.report_file,
                ui.section("Lines", ui.table(RegionReportLine, where={"report": RegionReport.id},
                                             columns=["customer", ui.column("amount", kind="money")]))],
        history=ui.table(RegionReport, columns=["region", "year", "created_by"], search=True),
    )
```

One new name, `ui.calculation`; everything inside it is form, section and table vocabulary. Three
methods on `self.api` that a project's own handlers may use too: `self.api.calculate(Model, pk)`,
`self.api.log(Model, pk)` and `self.api.file(Model, pk, field)` (a record's file through lex-app's
download view, as the user: `{"data": bytes}`, or `{"url": ...}` where the storage serves it).

**One page, two views.** The page at `/reports/regions` shows the form, and under it the past runs.
The same page with `?run=42` shows that run: the page of a record. One declaration, one permission,
one place in the navigation; a run's address is the page's address with its key, so it can be
bookmarked and sent while the run still goes. *Considered:* two pages (a form page and a run page with
a route argument), which every calculation would declare twice with the same model, title and
result; and `ui.form` with a result that follows the run, which gives a run no address and a past
run no page.

**The kind is bound to the model, and writes no state of the project's.** lex-app makes the page's
state (one per page, as a model table makes one per table) with its handlers: the form's submit, the
run's load, the job that follows a run, Stop and a file's download. A project writes no handler for
the ordinary page. *Considered:* a `ui.State` base class the project subclasses -- a second
place to write the same page, for nothing the page needs.

**A field of the run is written as the model's field: `RegionReport.orders`.** The page is about one
model, so `RegionReport.orders` can only mean this run's `orders`. It works wherever a value goes:
`ui.fact("Revenue", RegionReport.revenue, kind="money")`, `ui.text("{n} orders", n=RegionReport.orders)`,
`ui.table(..., where={"report": RegionReport.id})`, `visible=`. Alone in `result=` it is a fact with the
model's own label (its `verbose_name`, as a table's column header) and the kind its Django field
implies (a whole number, a decimal, a date, an instant, yes or no, text); a `FileField` alone is a
button that downloads the file as the user. A string is still a sentence everywhere, as on every
other page. A field of another model is refused when the page is built ("`Customer.name` is a field
of Customer; this page is about RegionReport"). *Considered:* field names as strings (`"orders"`),
which would make a string mean a sentence on every page but this one; a `ui.run("orders")` helper, a
new name for what the model already names.

**The inputs are the record's fields.** Each field's key names a field of the model, which the form's
values are written to: `ui.field("year")` is `RegionReport.year`. They are written as a form's fields
(kinds, `options=`, `required=`, `steps=` for a wizard) -- nothing is inferred from the model, so a field
reads the same on every page. Built-time checks: a key that is not a field the model lets one set
(not a field, `is_calculated`, the key, an automatic time) is refused, naming the model's settable
fields; a field the model requires on create (no default, not `null`, not `blank`) that the form
leaves out is refused, since lex-app would refuse every create ("RegionReport needs `year` when a
record is created: add `ui.field("year")`").

**Starting is lex-app's create and lex-app's Calculate, as the user.** The submit button reads
**Calculate** -- the word lex-app's grid uses -- and runs `calculate(values)`: the values are made
into the API's shapes (a decimal as text, a date as ISO), the record is created through
`self.api.create` (so `permission_create`, validation, `created_by` and the audit entry are the
user's), and the run is started through `self.api.calculate`, unless the create already started it
(`calculate_on_create`). The browser then goes to the run's address. A refusal by the API -- not
permitted, a field it rejects, a closed record -- is the form's refusal, above the button, in the API's
words (6.15).

**`self.api.calculate(Model, pk)` mints the run's id as the grid does** (`<model>_<pk>_update_<uuid>`).
`self.api.update(Model, pk, {"calculate": "true"})` starts a run too, but under an id of the
`<model>_update_<uuid>` shape, under which neither the grid's log button nor this page finds the run's
log. It answers the record, `IN_PROGRESS`; a run already going is left alone (lex-app answers the
record as it is).

**Following a run is reading the record until it settles: the same with Celery and without.** The
page never talks to the process that runs the calculation; it reads the record through lex-app's API,
as the user, every `calculations.POLL_SECONDS` (1 s), until its status is no longer `IN_PROGRESS`.
Without Celery the run goes on lex-app's calculation thread pool, in the API's process; with Celery
on a worker, which writes the status when it ends (`CallbackTask`). Either way the record's status is
the truth, and the page shows it. The reading is a `@ui.job(stop_on_leave=True)` that starts when the
run's view appears while the run goes (the progress part's `on_mount`): leaving the page stops the
watching, never the run; coming back to the run's address watches again. *Considered:* lex-app's
websocket of status changes, which the Reflex process does not hold a connection to and which carries
no permission check of the reader; a Reflex background task per run without a job's run
bookkeeping.

**Progress is the run's own log.** lex-app reports no fraction, so the bar is RFDS's indeterminate
bar, and its message is the newest line the run has logged -- from the live log while it runs (what the
grid's log panel shows), else "Running". A project that wants a message per phase logs one
(`LexLogger().add_text("Reading orders").log()`): nothing lex.ui-specific goes into a calculation.
*Not built:* a fraction; lex-app would need a progress field written by the run.

**The run's view** is RFDS's page header (the title, the run's name -- its `str()` -- under it, and
"New run" leading back to the form), then:

| Status | What is shown |
| --- | --- |
| any | a status badge (Not started, Running, Done, Failed, Stopped, Interrupted), and the inputs as facts, labelled as their fields |
| `IN_PROGRESS` | the progress bar with the newest log line; **Stop** to users who may edit the model |
| `SUCCESS` | `result=` |
| `ERROR` | the calculation's error in its own words: the first part of `calculation_error_message`, without the traceback, in RFDS's inline message (danger); a model that keeps no message: "The calculation failed. Its log says what happened." |
| `CANCELLED` | "The calculation was stopped before it finished." |
| `ABORTED` | "The calculation was interrupted: lex-app found it unfinished after a restart. Start it again." |
| a log exists | a closed section "Log" with the run's log, rendered as the grid renders it (Markdown, by RFDS's `safe_markdown`), whatever the status |

The run's load reads it through the API, as the user. A run the reader may not read is, to them, no
run: lex-app answers a record outside the reader's `permission_read` as one that does not exist, and
the page says the same as for a key that is no run, on lex-app's error page: "There is no run 42."
(found while building 1eg: the refusal is a 404, not a 403, so nothing tells a reader that the run
exists). The run's log is lex-app's: a failed atomic run's lines are kept from lex-app's log cache.

**The past runs are a `ui.table` over the model: `history=ui.table(RegionReport, columns=[...])`.** All
of a table's words work (`search=`, `filters=`, `page_size=`, `choose_columns=`); lex-app adds the run's
status as the last column (a status column, with the same words and tones as the badge), makes each row
open the run's address, and orders the newest run first. Who sees which runs is the model's
`permission_list`/`permission_read`, as on every model table. A table of another model is refused, so
is `open=` (the page decides where a run leads) and a `where=` (the past runs are the model's). *Considered:*
`history=["region", "year"]` -- a second way to list columns, without search or filters.
*Built (1eh):* the table is written once, under the form, titled "Past runs". What lex-app adds goes
through fields a page kind fills and `ui.table()`/`ui.column()` do not take -- the row's address
template, the order until the user sorts (`"-id"`, new on lex-app's model table state as
`default_ordering`), and the status column's words -- so the table vocabulary gains no argument.

**Permissions come from the model.** The page's `requires=` is the author's (normally
`ui.read(Model)`). The form is shown to users who may create the model (`visible=ui.create(Model)`), Stop
to those who may edit it; lex-app's API enforces both whatever the browser shows, and every read and
write of the page is the user's.

**With the frame (6.19) and the review's fixes (merged 2026-11-24).**
- *The page's `requires=` is its handlers' too* (1.978). A project writes `requires=` on its own
  handlers; a calculation page's handlers are lex-app's, so lex-app gives them the page's -- its
  permissions and its `@ui.rule`s. A user the page refuses cannot send its events by name either
  (Calculate, the run's load, Stop, the download, the following): each is refused with the rule's
  sentence before lex-app's API is called. Before the merge, a rule on the page hid the page and left
  its handlers open. The state is one per page and requirements: the same title and fields under
  another `requires=` is another state.
- *A run's view sits under its page in the breadcrumbs* (1.979). `?run=` is the same route, so the
  frame's trail would not change; while a run is open, the page becomes a link back to its form and the
  run's name (its key until lex-app names it) is the last crumb. A page kind with a view inside it
  gives the crumb with `view_crumb()`; the frame draws it.
- *The past runs after the review's table changes* (1.998): the author's first column stays in place
  and the added status column does not; the pager shows only while the runs do not fit one page;
  `totals=` stays refused, as on every model table.
- *A reconnect while a run is followed is the same visit* (6.18; 1.999): the following goes on, the
  progress part's second `follow` is a repeat, the load reads the run again, and `ui.frame(opened=)`
  is not called again -- it is called once a visit (1.977).

**What a reader of a past run sees** is that run's view, as it stands: the same badge, inputs, result,
error and log as the user who ran it saw at the end -- read now, as the reader, so a field the reader
may not see is missing for them as it is in the grid.

**Stop (built in 1ei).** A button under the progress, to users who may edit the model, asking "Stop this
run?" (RFDS's confirmation: a stopped run cannot be resumed). It is lex-app's cancel
(`PATCH ... cancel=true`). lex-app stops only a run on a worker -- it revokes the worker's task and
writes CANCELLED, which the page then shows; a run in lex-app's own process goes on to its end (the
cancel answers 409, `sync_calculation_not_cancellable`), and the page says so in words: "This run
cannot be stopped: lex-app runs it in its own process, where a run goes on to its end. A run on a
worker can be stopped." After the end: "The run has already ended." The file button is labelled with
the field's `verbose_name` and shown only when the run has a file; the handler accepts only a
`FileField` of the model, since its argument comes from the browser.

**Built in four batches:** 1ef the kind, the form and the start (`self.api.calculate`); 1eg the run's
view, following with and without Celery, the result, the error, the log (`self.api.log`); 1eh the past
runs; 1ei a file of the result, and Stop.

**Not built:**
- *A review between the inputs and the run that depends on the inputs* (rows found for the region,
  a decision per row): a wizard's steps have no handler between them. Checks of the inputs and a
  confirming last step work as in any wizard (`steps=`); a per-row decision is a form of its own
  before the calculation page, or a field of the record the calculation reads.
- *The API's field errors at their fields*: lex-app's validation answers a 400 per field; the page
  shows them together as the form's refusal, above the button.
- *"Run again with these inputs"* on a past run.
- *A fraction of progress*, and a run started elsewhere telling the page it began: the page reads
  only while it is open on the run.
- *Several records per run* (a batch's generated rows): a result's related table shows them.

---

## 7. Order of building

One spec, four steps. Each ends by porting real pages of the reviewed project, in the scratchpad
and under neutral names, so that a wrong slot or a missing component is found early.

| Step | Built | Ported |
| --- | --- | --- |
| 1 | `ui.page`, `ui.dashboard`, `ui.record`, `ui.list`; `text`, `fact`, `section`, `table` over a model, `column`, `button`, `link`, `message`, `status`; links by page; `visible=`, `each`, `switch`, `custom_block`; the build-time errors; `ui.loaded` | A detail page and a search list |
| 2 | A table over rows held in a state, and filters (both shaped by step 1's port of a search list); `ui.form`, `field`, `step`, `confirm=`, `ask=`, the button that knows its handler, `ui.goto(page)`; the kinds `date`, `year`, `bool`, `choice` | A page with actions on one record, and an entry form |
| 3 | `ui.overview`, `figure`, `tile`, `chart`, `grid` | The home page |
| 4 | `lex ui messages`, the new `lex reflex check` rules, `ui.testing.page`, the scaffold (`lex reflex new page --kind record`), lex-app's reference example rewritten on the kinds | — |

For each ported page, before and after: lines of view code; `class_name=` and `style=` (target:
none); lines of the project's own stylesheet the page needed (target: none); `rx.cond`,
`rx.fragment` and `rfds.t` calls; and what went into `ui.custom_block`, with the reason.

The last of these is the finding that matters most: it says whether four kinds and fifteen
components are enough for that project, or which one is missing.

---

## 8. Limits and risks

| Risk | Where it stands |
| --- | --- |
| A page shape nobody foresaw | `ui.custom_block` for a part; a whole page stays on `lex_page` until a kind exists. A kind is added when the same shape appears twice |
| The vocabulary is too small | Expected; each addition is a thin wrapper. The ports of section 7 are how it is found |
| RFDS's floorplans enforce limits of their own | Kept, reworded as lex errors. If one does not suit a kind, that kind is built from RFDS's lower-level parts instead; settled per kind while building |
| Pages are registered once the project's module is loaded | lex-app calls each page function then, to learn its title and its loads; a page may therefore name a page declared below it. Run in a prototype; tests assert the registry |
| RFDS 1.0.0 drops a second header button beside a primary one | Reflex components of one type compare equal and RFDS sorts actions with `not in`. The kinds wrap that button so RFDS can tell it apart; to be reported upstream |
| Filters in the address | Not yet tried: a model table applying query parameters itself, and a page reloading on a filter change. Tried in step 1 before anything depends on it |
| The sentence as message id | Rewording loses the translation (6.8). Two identical sentences that need different translations cannot be told apart; `ui.text(…, context="…")` is added if the case arises |
| Collecting text by reading source | A sentence held in a variable is not found; the check fails text that is not a literal |
| Applications of their own (a map, a board) | Stay custom, and are named as such |
| The look is RFDS's with the LEX theme | No per-project look. That is the point, and a constraint |

---

## 9. Not in this spec

- Rules about content — how many figures an overview may have, wording, when a tone fits (piece 3).
- The calculation-model page (piece 4) -- since designed and built: 6.20.
- A chunked-upload component; until then an upload is a `ui.custom_block`.
- A free dialog or side panel.
- Layout helpers (rows, columns, spacing).
- Moving the reviewed project itself.

---

## 10. Acceptance

1. A page of each kind is written without one raw Reflex or RFDS component and without one styling
   argument.
2. The whole API fits on one printed page (the appendix), and lex-app's reference example uses
   nothing outside it.
3. At least one page of each kind from the reviewed project is ported, with the numbers of section 7
   recorded.
4. Every rule of 6.9 has a test that reads its error message.
5. Pages written with `lex_page`, and the tests of the handler layer, still pass unchanged.

---

## Appendix: the whole API

```text
Declaring        @ui.page(route, nav=, icon=, requires=, params=)      @ui.dashboard
                 ui.frame(nav=, denied=, request_access=, opened=, sign_out=)      @ui.rule

Kinds            ui.overview(title, load=, figures=, tiles=, sections=, actions=)
                 ui.list(title, load=, filters=, table=, actions=)
                 ui.record(title, load=, status=, facts=, sections=, tabs=, actions=)
                 ui.form(title, load=, fields= | steps=, submit=, result=, actions=, keep=)
                 ui.calculation(Model, title=, fields= | steps=, result=, history=, actions=)

Components       ui.text(sentence, **values)             ui.fact(label, value, kind=)
                 ui.figure(label, value, kind=, change=, to=)
                 ui.tile(title, text, to=, icon=)        ui.status(word, tone=)
                 ui.message(sentence, tone=)             ui.chart(kind, data, x=, y=, title=)
                 ui.table(model_or_rows, columns=, open=, row_actions=, empty=, export=)
                 ui.grid(model, columns=, filters=)      ui.column(key, label=, kind=)
                 ui.button(label, handler | to=, confirm=, ask=, tone=, icon=)
                 ui.link(label, to= | record= | href=)
                 ui.field(key, label=, kind=, options=, required=, help=, value=, currency=)
                 ui.section(title, *content, load=, empty=)
                 ui.step(title, fields=)

Structure        visible=          ui.each(items, render)          ui.switch(value, {…})
                 ui.custom_block(component, reason=)

Shared words     kind=   text number money percent date datetime year bool choice
                 tone=   neutral info success warning danger
                 to=     a page function, with its parameters by name

Handlers         ui.State   @ui.load   @ui.action   @ui.job   @ui.custom          (piece 1)
                 ui.goto(page, **params)   ui.toast   ui.download   ui.progress   ui.Refuse

Tools            lex reflex check      lex ui messages      lex reflex new page --kind …
                 ui.testing.run(...)   ui.testing.page(...)
```
