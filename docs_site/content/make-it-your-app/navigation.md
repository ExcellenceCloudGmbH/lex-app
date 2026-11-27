---
title: "Navigation, header and menu"
description: "The navigation and its sections, counts, the header's search and actions, the user menu, a banner and the logo."
api:
  - "ui.frame"
---

Every page opened on its own sits in lex-app's frame: a header with the dashboard's name and the
signed-in user, a side navigation, and breadcrumbs above the page. lex-app draws all of it, and
shows each user only what they may open. A project tells the frame how to behave with one call,
`ui.frame(...)`, in the dashboard module beside its `@ui.page`s.

What a user sees on a page they may not open, and where **Sign out** leads, is on
[Pages a user can't open](/make-it-your-app/blocked-pages).

```python
from lex import ui
from myproject.models import Order
from myproject.recent import remember_page

ui.frame(
    nav={"Orders": [], "Reports": [], "Tools": []},
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
    nav={"Orders": [ui.link("Search orders", to=search, icon="search")],
         "Reports": [],
         "Requests": [ui.link("Waiting for you", to=approvals, badge=waiting)],
         "Favourites": favourites,
         "Help": [ui.link("All workflows", href="https://wiki.example.com/workflows")]},
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

- **`ui.frame(nav={...})`** gives the order of the areas, in the same words the pages use: a dict,
  in the order written, where `[]` means "the area's pages". Areas it does not name - say, those of
  raw pages you have not moved yet - follow the named ones, in the order their first page is
  declared. Without `nav=`, every area comes in that order.
- **A typo stops the app.** With `nav=` given, a `@ui.page` whose area neither `nav=` nor a raw
  page has stops the app from starting, naming the areas to choose from, so a typo cannot make a
  new area. A raw page is never refused for that, and a `@ui.page` may join a raw page's area.
- **`nav=` is a dict.** A list, `nav=["Orders", "Reports"]`, is refused with the dict to write:
  `nav={"Orders": [], "Reports": []}`.
- **A page with route arguments**, such as `/orders/[key]`, cannot be opened from the
  navigation, so it is never listed. Its `nav=` only places it in an area, for its breadcrumbs.
- **The current entry is marked**: the entry whose route the browser is on, or a route below it,
  so `/orders/7` marks `/orders`. The heading of its area is marked too.

## Only what the user may open

An entry the user may not open is not drawn. lex-app decides it on the server when a page opens,
for every entry that is guarded: a page's `requires=`, a raw page's `visible=`, a link's
`requires=` and the `requires=` of the page a link leads to. The header's search box and buttons
are decided the same way.

- **Permissions** are read from Keycloak with the user's own sign-in, not taken from what the
  browser was told, and only when a guard, a rule or a function of the user needs them. The page
  itself is decided from the same grants.
- **[Rules of your own](/who-can-do-what/permissions#a-rule-of-your-own)** are asked on a worker
  thread, as they are for a handler: a page's rule with no page values, a link's rule with the
  link's values. A rule that fails hides its entry, and the failure is logged.
- Only the answers reach the browser. A guarded entry is drawn once the answer has come, so the
  first page of a new tab is right too.
- An area whose entries the user may open none of is not drawn at all, heading included.

This hides entries; it does not guard pages. A page's own `requires=` still decides whether it
opens, and each handler's `requires=` what it may do.

## Links in an area

Instead of `[]`, give an area a list of `ui.link`s, for entries that are not pages of an area: a
search page, a wiki, a record.

```python
ui.frame(nav={
    "Orders": [ui.link("Search orders", to=search, icon="search")],
    "Reports": [],
    "Help": [ui.link("All workflows", href="https://wiki.example.com/workflows"),
             ui.link("Legacy export", to=legacy),
             ui.link("Order 7", to=order, key=7)],
})
```

- **The links come after the area's pages**: "Orders" lists the Orders page, then **Search
  orders**. An area without pages, such as "Help", holds its links alone.
- **A link leads** `to=` a page or a raw page (with fixed values for its address),
  `record=(Model, pk)` to a record in lex-app, or `href=` an `https://` address.
- **`icon=`** is the entry's icon, an RFDS icon name.
- **`badge=`** is a count beside the entry: see [Counts](#counts).
- **`requires=`** takes what `requires=` takes everywhere - a permission, a rule, or a list - and
  the entry is drawn only for users who meet it. A link to a page that has a `requires=` of its
  own is guarded by that too.

A link's values are fixed: the frame is the same on every page. A link with a state value, an
area given anything but `[]`, `ui.link`s or a function of the user, or a `ui.section` in `nav=`
is refused, with what to write. `ui.section` is a titled part of a page, never of the navigation.

### A section for each user

Give a name one function of the user instead of a list, and the navigation has a section that
lists what the function returns: the user's favourites, the topics they may see.

```python
def favourites(user):
    return [ui.link(f.title, to=report, key=f.report_id) for f in Favourite.objects.filter(user=user.id)]


ui.frame(nav={"Reports": [], "Favourites": favourites})
```

- It is called on the server each time a page opens: see [Values the frame shows](#values-the-frame-shows).
- The links are shown as they are: their words are your data's, not translated.
- They take no `icon=`, `badge=` or `requires=`: the function decides who sees what. A link
  that has one is left out, and logged.
- **Each link leads only to a page the user may open.** A link to a page whose `requires=` the
  user does not meet - its permission, or its rule asked with the link's values - is left out.
- While it returns no links, the section is not drawn.
- **Its name is its own.** A section of the user's links may not share its name with an area
  that has pages: `nav={"Orders": favourites}` with a page in "Orders" is refused, wherever the
  second of the two is declared (the frame, a `@ui.page` or a raw page), asking you to give one
  of them another name, such as `"Orders (mine)"`.

## Counts

**`badge=`** puts a count beside an entry: "Waiting for you 3". It is a function of the user, and
works on a frame link, on a page's entry and on a raw page's entry:

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
is logged. A state value is refused, asking for a function of the user: no page loads a state for
the frame, so it would show what the last page left there.

`badge=` belongs on a listed entry. On a page without `nav=` it is refused: it has no entry to
stand beside. So is `badge=` or `icon=` on a page with route arguments, such as `/orders/[key]`,
which is never listed: put it on the page that leads there, `@ui.page("/orders", nav="Orders", badge=...)`.

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
opens the page without it. The page reads it as `self.params.get("q")`. What is typed stays in the
browser until Enter: no event is sent per key. A page with no query parameter, with two, or with
route arguments is refused.

The box is drawn only for users who may open the search page, decided on the server as a
navigation entry is: a user its `requires=` refuses sees no search box.

### Actions

**`actions=`** are the parts at the top right of the header, before the user menu: `ui.link`
(with `icon=`, `badge=` and `requires=`, as in the navigation), `ui.button`, `ui.status` and
`ui.custom_block`.

```python
def sync_state(user):
    return "Sync delayed" if sync_is_late() else None


ui.frame(actions=[
    ui.status(sync_state, tone="warning"),
    ui.link("Notifications", to=inbox, icon="notification", badge=unread),
])
```

- **A status's word** is fixed or a function of the user; while the function answers nothing,
  the status is not shown.
- **A button** is drawn only for users its handler's `requires=` grants, as a link is drawn by
  its own `requires=`.
- **Free text is not one of them**: `ui.text` is refused. A state in a word is a `ui.status`,
  such as `ui.status("Read-only")`; anything else, such as a notification centre of your own, is a
  `ui.custom_block(..., reason=...)`.

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
[Inside lex-app](/make-it-your-app/inside-lex-app).

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

Its sentence is fixed or a function of the user, and the banner shows while the sentence is not
empty. There is one banner at most; it shows inside lex-app too.

## Values the frame shows

Where a part of the frame shows a value - a count, a status's word, the banner's sentence, a
section's links - it is fixed, or **a function of the user**: a plain function with one argument,
`def waiting(user): ...`. A state value is refused there: no page loads a state for the frame, so
it would show whatever the last page left in it.

- lex-app calls it on the server each time a page opens, on a worker thread, so it may query the
  database. The page does not wait for it, and the browser gets only the answer.
- It is called once per opening, however many entries name it. A page opened within a second of
  the last decision for the same tab and user keeps the answers just decided.
- If it raises, the failure is logged with its name, and its part shows nothing - no badge, no
  banner - never an error.
- `None` or `""` hides a word or a sentence; 0 hides a count.
- `user` is the signed-in user: `.id`, `.name`, `.username`, `.email`, and `.permissions`, the
  grants Keycloak lists for them. A [rule](/who-can-do-what/permissions#a-rule-of-your-own) and
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

On a [calculation page](/long-running-work/calculations), while one run is open, the page becomes a link back to
its form and the run's name is the last crumb.

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

`page.address` and `page.params` come from the browser: they are what it asked for, not something
lex-app decided. Use them to remember where the user was, never to decide what they may do.

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
ui.reflex_page("/audit-export", nav="Tools", title="Audit export", visible=ui.read(Order))
```

- **`title=`** is the entry's label, and is required with `nav=`; `icon=` is its icon and
  `badge=` its [count](#counts).
- **`visible=`** takes a permission, a rule, or a list of them, and shows the entry only to users
  who meet it, decided on the server. It only hides the entry: lex-app neither frames nor guards
  a raw page, so the page checks access itself.
- **`requires=` is refused** on a raw page, saying to write `visible=`: everywhere else
  `requires=` means access control, which lex-app cannot give a raw page. A fixed or state value
  for `visible=` is refused too: the entry is decided for each user.
- `title=`, `icon=`, `badge=` or `visible=` without `nav=`, and a route with arguments, are
  refused, with what to write.

The page is still somewhere a lex.ui link may lead; see
[Linking to a raw page](/make-it-your-app/your-own-reflex#linking-to-a-raw-page).

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
| `nav=` | a dict, in order: each area's name -> `[]`, a list of `ui.link`s, or a function of the user | the order their first page is declared |
| `nav_footer=` | one `ui.link` or `ui.text` | nothing |
| `search=` | a page that takes one query parameter | no search box |
| `actions=` | `ui.link`, `ui.button`, `ui.status`, `ui.custom_block` | nothing |
| `user_menu=` | `ui.link`s | Sign out alone |
| `banner=` | one `ui.message` | no banner |
| `logo=`, `logo_label=` | a mark's address, and what it says | the name alone |
| `denied=` | the sentence a refused user reads | RFDS's sentence |
| `request_access=` | a page, an `https://` or a `mailto:` address | no Request access link |
| `denied_as_missing=` | `True` or `False` | `False`: the no-access notice |
| `sign_out=` | a page or an `https://` address | lex-app's `/logout` |
| `opened=` | a function of `(user, page)` | nothing is called |
