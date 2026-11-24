---
title: "The frame"
description: "ui.frame: the navigation's areas, breadcrumbs, the no-access notice, a page opened, where Sign out leads."
---

Every page opened on its own sits in lex-app's frame: a header with the dashboard's name and the
signed-in user, a side navigation, and breadcrumbs above the page. lex-app draws all of it. A
project with several areas of work - orders, reports, tools - tells the frame how to behave with
one call, `ui.frame(...)`, in the dashboard module beside its `@ui.page`s.

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

Every argument of `ui.frame` is optional; without the call, lex-app's defaults apply.

## Areas in the navigation

A page's `nav=` names its area. `@ui.page("/orders", nav="Orders")` lists the page under the
heading "Orders", by its title. Within an area, pages come in the order they are declared,
`@ui.page`s and raw pages alike. lex-app's **Overview** entry, which opens `/`, comes first.

- **`ui.frame(nav=[...])`** gives the order of the areas, in the same words the pages use. A page
  whose `nav=` is not in that list stops the app from starting, with the list to choose from, so a
  typo cannot make a new area. Without it, the areas come in the order their first page is
  declared.
- **A page with route arguments**, such as `/orders/[key]`, cannot be opened from the
  navigation, so it is never listed. Its `nav=` only places it in an area, for its breadcrumbs.
- **The current entry is marked**: the entry whose route the browser is on, or a route below it,
  so `/orders/7` marks `/orders`. The heading of its area is marked too.
- **A permission in a page's `requires=`** leaves the page out of the navigation of users who
  lack it. A [rule of your own](/data-and-permissions/can#a-rule-of-your-own) does not hide the
  entry: it is asked on the server when the page opens.

## A raw Reflex page in an area

A page written in raw Reflex joins an area with the same words, through `ui.reflex_page`:

```python
ui.reflex_page("/legacy", nav="Tools", title="Legacy export", icon="download")
ui.reflex_page("/audit-export", nav="Tools", title="Audit export", requires=ui.read(Order))
```

- **`title=`** is the entry's label, and is required with `nav=`; `icon=` is its icon.
- **`requires=`** takes one permission and only hides the entry from users without it. lex-app
  neither frames nor guards a raw page: the page checks access itself.
- `title=`, `icon=` or `requires=` without `nav=`, a route with arguments, and a rule as
  `requires=` are refused, with what to write.

The page is still somewhere a lex.ui link may lead; see
[Linking to a raw page](/look-and-chrome/raw-reflex#linking-to-a-raw-page).

## Breadcrumbs

The frame shows breadcrumbs above every page that is in an area. No page writes them:

- **The area** comes first, as text: an area is not a page.
- **The parent** comes next, as a link, for a page that is not listed in the navigation itself.
  The parent is the first listed page whose table opens it (`open=`); failing that, the first whose
  button or link leads to it (`to=`), in the order the pages are declared.
- **The page** comes last, by its title; on a record page that may be a state value, such as the
  order's number.

In the example, the list's trail is `Orders / Orders`, an order's is `Orders / Orders / A-1007`,
and the form's is `Orders / Orders / New order`. A page without a `nav=` of its own takes its
parent's area. A page in no area, such as a help page no listed page leads to, has no breadcrumbs.
There is one level of parent only: a page opened from a record page shows its area and itself.

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

`user` is the signed-in user: `.id`, `.name`, `.username`, `.email`.

- It runs once the page's loads have succeeded, at once on a page without loads, on a worker
  thread. The page never waits for it.
- It is not called for a page the user may not see, or one whose load failed.
- It is called once a visit. When the browser loses its connection and gets it back, or the user
  reloads the page, the loads run again but `opened=` is not called again. Following a link to the
  same page is a new visit.
- If it raises, the failure is logged with the page's route, and the user sees nothing of it.
- It must be a plain function of `(user, page)`; an `async` function, or one with another number of
  arguments, is refused when `ui.frame` is called.

## The header

Two settings of the deployment change the header, not `ui.frame`:

| Setting | Does |
| --- | --- |
| `LEX_DASHBOARD_TITLE` | the name in the header; without it, the project's name |
| `LEX_ENVIRONMENT` | a badge for `prod`, `uat` or `dev`; `none` (the default) shows none |

Inside lex-app the header and the navigation are hidden, because lex-app shows its own; see
[Inside lex-app](/look-and-chrome/embedding).

## Once, in the dashboard module

`ui.frame` is called once, in the module that declares the pages. A second call from another
module is refused, naming the module that configured the frame; calling it again from the same
module, as a reload does, replaces the configuration.

| Argument | Takes | Default |
| --- | --- | --- |
| `nav=` | the areas, in their order: `["Orders", "Reports"]` | the order their first page is declared |
| `denied=` | the sentence a refused user reads | RFDS's sentence |
| `request_access=` | a page, an `https://` or a `mailto:` address | no Request access link |
| `sign_out=` | a page or an `https://` address | lex-app's `/logout` |
| `opened=` | a function of `(user, page)` | nothing is called |

Next: [Inside lex-app](/look-and-chrome/embedding), a dashboard in lex-app and lex-app in a
dashboard.
