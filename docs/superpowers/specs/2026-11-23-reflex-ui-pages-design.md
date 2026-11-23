# lex.ui pages: page kinds with named slots, and a closed vocabulary

**Status:** design approved in conversation on 2026-11-23; nothing is implemented.
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
- **Actions.** The first action is the primary one. More than three collapse into a menu. A button
  with `tone="danger"` must carry `confirm=`.
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
| The page's load is running | A skeleton in the shape of the kind | `ui.running(load)` |
| The page's load failed, on a `ui.page` | The error page of piece 1, with "Try again" | A page-declared required load |
| The page's load failed, on a `ui.dashboard` | The failure in place of the content | A load started on mount reports in place (piece 1) |
| A section's own load is running | That section's skeleton; the rest of the page is usable | `ui.running` of the section's `load` |
| A section's own load failed | The failure inside that section | `ui.failed` of the section's `load` |
| A table or a section has nothing to show | Its `empty="…"` sentence | The rows it was given |
| The viewer lacks the page's permission | lex-app's access-denied content | `requires=` on `ui.page` |

How the loads are started follows piece 1's two kinds of load:

- On a `ui.page`, the kind's `load` is registered as the page's required load and each section's
  `load` as an optional one, when the page is built (5.3). The opener starts them after sign-in and
  grants, with the right address.
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

---

## 7. Order of building

One spec, four steps. Each ends by porting real pages of the reviewed project, in the scratchpad
and under neutral names, so that a wrong slot or a missing component is found early.

| Step | Built | Ported |
| --- | --- | --- |
| 1 | `ui.page`, `ui.dashboard`, `ui.record`, `ui.list`; `text`, `fact`, `section`, `table`, `column`, `button`, `link`, `message`, `status`; links by page; `visible=`, `each`, `switch`, `custom_block`; the build-time errors | A detail page and a search list |
| 2 | `ui.form`, `field`, `step`, `confirm=`, `ask=`, the button that knows its handler, `ui.goto(page)` | A page with actions on one record, and an entry form |
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
| Section loads are registered when the page is built | Verified in Reflex 0.9.13 (5.3); a test asserts the registry after a dry-run compile and with compilation skipped |
| Filters in the address | Not yet tried: a model table applying query parameters itself, and a page reloading on a filter change. Tried in step 1 before anything depends on it |
| The sentence as message id | Rewording loses the translation (6.8). Two identical sentences that need different translations cannot be told apart; `ui.text(…, context="…")` is added if the case arises |
| Collecting text by reading source | A sentence held in a variable is not found; the check fails text that is not a literal |
| Applications of their own (a map, a board) | Stay custom, and are named as such |
| The look is RFDS's with the LEX theme | No per-project look. That is the point, and a constraint |

---

## 9. Not in this spec

- Rules about content — how many figures an overview may have, wording, when a tone fits (piece 3).
- The calculation-model page (piece 4).
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

Kinds            ui.overview(title, load=, figures=, tiles=, sections=, actions=)
                 ui.list(title, load=, filters=, table=, actions=)
                 ui.record(title, load=, status=, facts=, sections=, tabs=, actions=)
                 ui.form(title, load=, fields= | steps=, submit=, result=, actions=)

Components       ui.text(sentence, **values)             ui.fact(label, value, kind=)
                 ui.figure(label, value, kind=, change=, to=)
                 ui.tile(title, text, to=, icon=)        ui.status(word, tone=)
                 ui.message(sentence, tone=)             ui.chart(kind, data, x=, y=, title=)
                 ui.table(model_or_rows, columns=, open=, row_actions=, empty=, export=)
                 ui.grid(model, columns=, filters=)      ui.column(key, label=, kind=)
                 ui.button(label, handler | to=, confirm=, ask=, tone=, icon=)
                 ui.link(label, to= | record= | href=)
                 ui.field(key, label=, kind=, options=, required=, help=, value=)
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
