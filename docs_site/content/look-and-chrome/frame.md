---
title: "The frame"
description: "ui.frame: the navigation and its sections, counts, the header's search and actions, the user menu, a banner, the logo, the no-access notice."
---

Every page opened on its own sits in lex-app's frame: a header with the dashboard's name and the
signed-in user, a side navigation, and breadcrumbs above the page. lex-app draws all of it, and
shows each user only what they may open. A project tells the frame how to behave with one call,
`ui.frame(...)`, in the dashboard module beside its `@ui.page`s.

```python
from lex import ui
from myproject.models import Order
from myproject.recent import remember_page

ui.frame(
    nav=["Orders", "Reports", "Tools"],
    denied="Ask your administrator for access.",
    request_access="mailto:it@example.com",
    opened=remember_page,
)


@ui.page("/orders", nav="Orders", requires=ui.read(Order))
def orders():
    return ui.list(title="Orders", table=ui.table(Order, columns=["number", "customer"], open=order),
                   actions=[ui.button("New order", to=new_order)])


@ui.page("/orders/[key]", requires=ui.read(Order))
def order():
    return ui.record(title=OrderState.number, load=OrderState.open,
                     facts=[ui.fact("Customer", OrderState.customer)])


@ui.page("/orders/new", requires=ui.create(Order))
def new_order():
    return ui.form(title="New order", fields=[ui.field("customer", required=True)],
                   submit=OrderState.create)


@ui.page("/reports/monthly", nav="Reports", requires=ui.read(Order))
def monthly_report():
    return ui.list(title="Monthly report",
                   table=ui.table(Reports.months, columns=["month", ui.column("total", kind="money")]))


ui.reflex_page("/legacy", nav="Tools", title="Legacy export", icon="download")
```

The navigation then reads:

```text
Overview
Orders
  Orders
Reports
  Monthly report
Tools
  Legacy export
```

Opening order 7 shows the breadcrumbs `Orders / Orders / A-1007`: the area, the list that opens the
order, and the order's own title.

```md alert info
The demos on this site are drawn without lex-app's frame, so this page has no live demo.
```

Every argument of `ui.frame` is optional; without the call, lex-app's defaults apply. A bigger
frame, with every part this page explains:

```python
def waiting(user):                      # a function of the user: lex-app calls it on the server
    return Request.objects.filter(approver=user.id, state="open").count()


def favourites(user):
    return [ui.link(f.title, to=report, key=f.report_id) for f in Favourite.objects.filter(user=user.id)]


def config_problem(user):
    return "Some settings could not be loaded." if not settings_loaded() else None


ui.frame(
    nav=[ui.section("Orders", ui.link("Search orders", to=search, icon="search")),
         "Reports",
         ui.section("Requests", ui.link("Waiting for you", to=approvals, badge=waiting)),
         ui.section("Favourites", favourites),
         ui.section("Help", ui.link("All workflows", href="https://wiki.example.com/workflows"))],
    nav_footer=ui.link("Version 2.4.1", href="https://example.com/changes"),
    search=search,
    actions=[ui.link("Notifications", to=inbox, icon="notification", badge=unread)],
    user_menu=[ui.link("Account", to=account),
               ui.link("Settings", to=settings, requires=ui.edit(Setting))],
    banner=ui.message(config_problem, tone="warning"),
    logo="/acme.svg",
)
```

## Areas in the navigation

A page's `nav=` names its area. `@ui.page("/orders", nav="Orders")` lists the page under the
heading "Orders", by its title. Within an area, pages come in the order they are declared,
`@ui.page`s and raw pages alike. lex-app's **Overview** entry, which opens `/`, comes first.

- **`ui.frame(nav=[...])`** gives the order of the areas, in the same words the pages use. Areas
  it does not list - say, those of raw pages you have not moved yet - follow the listed ones, in
  the order their first page is declared. Without the list, every area comes in that order.
- **A typo stops the app.** With `nav=[...]` given, a `@ui.page` whose area neither the list nor a
  raw page has stops the app from starting, naming the areas to choose from, so a typo cannot make
  a new area. A raw page is never refused, and a `@ui.page` may join a raw page's area.
- **A page with route arguments**, such as `/orders/[key]`, cannot be opened from the
  navigation, so it is never listed. Its `nav=` only places it in an area, for its breadcrumbs.
- **The current entry is marked**: the entry whose route the browser is on, or a route below it,
  so `/orders/7` marks `/orders`. The heading of its area is marked too.

## Only what the user may open

An entry the user may not open is not drawn. lex-app decides it on the server when a page opens,
for every entry that has a `requires=`: a page's, a raw page's, a link's, and the `requires=` of
the page a link leads to.

- **Permissions** are read from Keycloak with the user's own sign-in, not taken from what the
  browser was told.
- **[Rules of your own](/data-and-permissions/can#a-rule-of-your-own)** are asked on a worker
  thread, as they are for a handler: a page's rule with no page values, a link's rule with the
  link's values. A rule that fails hides its entry, and the failure is logged.
- Only the answers reach the browser. A guarded entry is drawn once the answer has come, so the
  first page of a new tab is right too.
- An area whose entries the user may open none of is not drawn at all, heading included.

This hides entries; it does not guard pages. A page's own `requires=` still decides whether it
opens, and each handler's `requires=` what it may do.

## Sections of links

An item of `nav=` may also be `ui.section("Title", ...)` with `ui.link`s in it, for entries that
are not pages of an area: a search page, a wiki, a record.

```python
ui.frame(nav=[
    ui.section("Orders", ui.link("Search orders", to=search, icon="search")),
    "Reports",
    ui.section("Help", ui.link("All workflows", href="https://wiki.example.com/workflows"),
               ui.link("Legacy export", to=legacy),
               ui.link("Order 7", to=order, key=7)),
])
```

- **A section named like an area** holds the area's pages, then its links: "Orders" lists the
  Orders page, then **Search orders**. Any other title makes a section of links.
- **A link leads** `to=` a page or a raw page (with fixed values for its address),
  `record=(Model, pk)` to a record in lex-app, or `href=` an `https://` address.
- **`icon=`** is the entry's icon, an RFDS icon name.
- **`badge=`** is a count beside the entry: see [Counts](#counts).
- **`requires=`** takes what `requires=` takes everywhere - a permission, a rule, or a list - and
  the entry is drawn only for users who meet it. A link to a page that has a `requires=` of its
  own is guarded by that too.

A link's values are fixed: the frame is the same on every page. A link with a state value, an
empty section, an area listed twice, or a section with `collapsed=`, `load=`, `visible=` or
`actions=` is refused, with what to write.

### A section for each user

Give a section one function of the user instead of links, and it lists what the function returns:
the user's favourites, the topics they may see.

```python
def favourites(user):
    return [ui.link(f.title, to=report, key=f.report_id) for f in Favourite.objects.filter(user=user.id)]


ui.frame(nav=["Reports", ui.section("Favourites", favourites)])
```

- It is called on the server each time a page opens: see [Values the frame shows](#values-the-frame-shows).
- The links are shown as they are: their words are your data's, not translated.
- They take no `icon=`, `badge=` or `requires=`: the function decides who sees what. A link
  that has one is left out, and logged.
- While it returns no links, the section is not drawn.

## Counts

**`badge=`** puts a count beside an entry: "Waiting for you 3". It is a function of the user, or a
whole-number state value, and works on a frame link, on a page's entry and on a raw page's entry:

```python
def waiting(user):
    return Request.objects.filter(approver=user.id, state="open").count()


@ui.page("/approvals", nav="Requests", badge=waiting)
def approvals():
    ...


ui.reflex_page("/inbox", nav="Requests", title="Inbox", badge=unread)
ui.link("Waiting for you", to=approvals, badge=waiting)
```

A count of 0 shows no badge, and an answer that is not a whole number, 0 or more, shows none and
is logged. `badge=` on a page without `nav=` is refused: it has no entry to stand beside.

## The foot of the navigation

**`nav_footer=`** is one `ui.link` or `ui.text` at the foot of the navigation: a version label, a
link to help.

```python
ui.frame(nav_footer=ui.link("Version 2.4.1", href="https://example.com/changes"))
```

## The header

### A search box

**`search=`** puts a search box in the header. It names your search page: a `@ui.page` (or a
`ui.reflex_page`) that takes exactly one query parameter and no route arguments.

```python
@ui.page("/search", params=["q"])
def search():
    return ui.list(title="Search", load=SearchState.find, table=ui.table(SearchState.found, columns=["name"]))


ui.frame(search=search)
```

The user types and presses Enter: the browser opens `/search?q=` with what was typed; an empty box
opens the page without it. The page reads it as `self.params.get("q")`. A page with no query
parameter, with two, or with route arguments is refused.

### Actions

**`actions=`** are a few parts in the header, before the user menu: `ui.link` (with `icon=`,
`badge=` and `requires=`, as in the navigation), `ui.button`, `ui.status`, `ui.text` and
`ui.custom_block`.

```python
def sync_state(user):
    return "Sync delayed" if sync_is_late() else None


ui.frame(actions=[
    ui.status(sync_state, tone="warning"),
    ui.link("Notifications", to=inbox, icon="notification", badge=unread),
])
```

A status's word and a text's sentence may be a function of the user; while it answers nothing,
the part is not shown. A notification centre of your own goes in a `ui.custom_block`.

### The logo

**`logo=`** is the address of your project's mark: a file of the app's assets, `"/acme.svg"`, or
an `https://` address. It sits beside the dashboard's name, and both lead to the overview. Without
a logo, the name alone leads there.

```python
ui.frame(logo="/acme.svg")
ui.frame(logo="/acme.svg", logo_label="Acme")
```

Beside the name, the mark is decoration, and a screen reader reads the name once. When the mark
says something the name does not, such as a company's name, **`logo_label=`** says it. The logo
changes nothing else: the colours, type and layout stay lex-app's.

### The name and the environment

Two settings of the deployment change the header, not `ui.frame`:

| Setting | Does |
| --- | --- |
| `LEX_DASHBOARD_TITLE` | the name in the header; without it, the project's name |
| `LEX_ENVIRONMENT` | a badge for `prod`, `uat` or `dev`; `none` (the default) shows none |

Inside lex-app the header and the navigation are hidden, because lex-app shows its own; see
[Inside lex-app](/look-and-chrome/embedding).

## The user menu

**`user_menu=`** adds `ui.link`s to the user menu, above **Sign out**: the user's own pages.

```python
ui.frame(user_menu=[ui.link("Account", to=account),
                    ui.link("Settings", to=settings, requires=ui.edit(Setting))])
```

Each entry is drawn only for users who meet its `requires=` and may open the page it leads to.
An entry takes no `badge=`: a count belongs in the header or the navigation.

## A banner

**`banner=`** is one `ui.message` above the header on every page, in the message's tone, for
something every user should know: a failed configuration, planned maintenance.

```python
def config_problem(user):
    return "Some settings could not be loaded." if not settings_loaded() else None


ui.frame(banner=ui.message(config_problem, tone="warning"))
```

Its sentence is fixed, a state value, or a function of the user, and the banner shows while the
sentence is not empty. There is one banner at most; it shows inside lex-app too.

## Values the frame shows

Where a part of the frame shows a value - a count, a status's word, the banner's sentence, a
section's links - it takes a fixed value, a state value, or **a function of the user**: a plain
function with one argument, `def waiting(user): ...`.

- lex-app calls it on the server each time a page opens, on a worker thread, so it may query the
  database. The page does not wait for it, and the browser gets only the answer.
- It is called once per opening, however many entries name it.
- If it raises, the failure is logged with its name, and its part shows nothing - no badge, no
  banner - never an error.
- `None` or `""` hides a word or a sentence; 0 hides a count.
- `user` is the signed-in user: `.id`, `.name`, `.username`, `.email`, and `.permissions`, the
  grants Keycloak lists for them. A [rule](/data-and-permissions/can#a-rule-of-your-own) and
  `opened=` may read `.permissions` too.

A function that takes another number of arguments, or is `async`, is refused when `ui.frame` is
called.

## Breadcrumbs

The frame shows breadcrumbs above every page that is in an area. No page writes them:

- **The area** comes first, as text: an area is not a page.
- **The parent** comes next, as a link, for a page that is not listed in the navigation itself.
  The parent is the first listed page whose table opens it (`open=`); failing that, the first whose
  button or link leads to it (`to=`), in the order the pages are declared.
- **The page** comes last, by its title; on a record page that may be a state value, such as the
  order's number.

In the first example, the list's trail is `Orders / Orders`, an order's is
`Orders / Orders / A-1007`, and the form's is `Orders / Orders / New order`. A page without a
`nav=` of its own takes its parent's area. A page in no area, such as a help page no listed page
leads to, has no breadcrumbs. There is one level of parent only: a page opened from a record page
shows its area and itself.

On a [calculation page](/pages/calculations), while one run is open, the page becomes a link back to
its form and the run's name is the last crumb.

## When a user may not see a page

A page whose `requires=` the user does not meet shows RFDS's access-denied notice instead of its
content, inside the frame. Two arguments word it for your project:

- **`denied=`** is the sentence the user reads, instead of RFDS's. A rule that refuses with a
  sentence of its own shows that sentence instead.
- **`request_access=`** adds a **Request access** link: a page (a `@ui.page` or a
  `ui.reflex_page`, without route arguments), or an `https://` or `mailto:` address.

The notice's **Switch account** link, to sign in as someone else, goes to the sign-out address
(below). The page a refused load goes to shows the same notice, with the refusal's own sentence.

### Refused pages that look missing

Some applications must not even say that a page exists. With **`refused_as_missing=True`**, a
page the user may not open - by a permission or by a rule - looks exactly like a page that does
not exist: lex-app's "Page not found", with a link to the overview. The browser tab's title, the
breadcrumbs and the navigation entry give nothing away either.

```python
ui.frame(refused_as_missing=True)
```

- The page's opener decides on the server, and tells the browser only yes or no, never a rule's
  sentence.
- A refused load's error page is the same "Page not found".
- lex-app adds its own not-found page for addresses that match no page, so a missing page and a
  refused one look the same. A project that adds its own `404` page keeps it.
- `denied=` and `request_access=` word a notice this never shows, so they are refused beside it.

```md alert warning
This hides a page from the screen, not from someone who reads the app's code: every page's code is
in the app the browser loads. What a page shows is still guarded by its loads' and handlers'
`requires=`.
```

## Where Sign out leads

**`sign_out=`** is where the user menu's **Sign out** leads, and where **Switch account** goes: a
page, or an `https://` address. By default it is lex-app's `/logout`, which ends the dashboard's
sign-in and lex-app's. An address of your own should end at lex-app's sign-out too, or the
dashboard's sign-in lasts until it expires.

A route is never written as text in `ui.frame`: `request_access="/help"` is refused, with a sentence
asking for the page itself.

## When a page has opened

**`opened=`** is a function of yours that lex-app calls when a page opens, for a "recently used"
list or an audit entry. It takes the signed-in user and the page:

```python
from myproject.models import RecentPage


def remember_page(user, page):
    RecentPage.objects.update_or_create(user_id=user.id, address=page.address,
                                        defaults={"title": page.title, "area": page.area})
```

| `page.` | Is |
| --- | --- |
| `route` | the route as declared: `/orders/[key]` |
| `address` | the address the browser has, with its values: `/orders/7` |
| `title` | the page's title; a state value as the page's load left it |
| `area` | its area in the navigation, `""` outside every area |
| `params` | the page's route and query values |

`user` is the signed-in user: `.id`, `.name`, `.username`, `.email` and `.permissions`.

- It runs once the page's loads have succeeded, at once on a page without loads, on a worker
  thread. The page never waits for it.
- It is not called for a page the user may not see, or one whose load failed.
- It is called once a visit. When the browser loses its connection and gets it back, or the user
  reloads the page, the loads run again but `opened=` is not called again. Following a link to the
  same page is a new visit.
- If it raises, the failure is logged with the page's route, and the user sees nothing of it.
- It must be a plain function of `(user, page)`; an `async` function, or one with another number of
  arguments, is refused when `ui.frame` is called.

## A raw Reflex page in an area

A page written in raw Reflex joins an area with the same words, through `ui.reflex_page`:

```python
ui.reflex_page("/legacy", nav="Tools", title="Legacy export", icon="download")
ui.reflex_page("/audit-export", nav="Tools", title="Audit export", requires=ui.read(Order))
```

- **`title=`** is the entry's label, and is required with `nav=`; `icon=` is its icon and
  `badge=` its [count](#counts).
- **`requires=`** takes what a page's does - a permission, a rule, or a list - and only hides the
  entry from users who may not open the page. lex-app neither frames nor guards a raw page: the
  page checks access itself.
- `title=`, `icon=`, `badge=` or `requires=` without `nav=`, and a route with arguments, are
  refused, with what to write.

The page is still somewhere a lex.ui link may lead; see
[Linking to a raw page](/look-and-chrome/raw-reflex#linking-to-a-raw-page).

## The frame's words in a page

`icon=`, `badge=` and `requires=` on a `ui.link` belong to the frame, where the server decides
them. In a page they stop the page with a sentence saying what to write instead: there `visible=`
decides what is shown, a button has the icon, and a count is a state value in a `ui.text`.

## Once, in the dashboard module

`ui.frame` is called once, in the module that declares the pages. A second call from another
module is refused, naming the module that configured the frame; calling it again from the same
module, as a reload does, replaces the configuration.

| Argument | Takes | Default |
| --- | --- | --- |
| `nav=` | the areas in their order, and `ui.section`s of links | the order their first page is declared |
| `nav_footer=` | one `ui.link` or `ui.text` | nothing |
| `search=` | a page that takes one query parameter | no search box |
| `actions=` | `ui.link`, `ui.button`, `ui.status`, `ui.text`, `ui.custom_block` | nothing |
| `user_menu=` | `ui.link`s | Sign out alone |
| `banner=` | one `ui.message` | no banner |
| `logo=`, `logo_label=` | a mark's address, and what it says | the name alone |
| `denied=` | the sentence a refused user reads | RFDS's sentence |
| `request_access=` | a page, an `https://` or a `mailto:` address | no Request access link |
| `refused_as_missing=` | `True` or `False` | `False`: the no-access notice |
| `sign_out=` | a page or an `https://` address | lex-app's `/logout` |
| `opened=` | a function of `(user, page)` | nothing is called |

Next: [Inside lex-app](/look-and-chrome/embedding), a dashboard in lex-app and lex-app in a
dashboard.
