---
title: "Inside lex-app"
description: "Show a dashboard inside lex-app, and lex-app's pages inside a dashboard."
---

Dashboards and lex-app work both ways round. lex-app shows a model's dashboard on the model's own
pages, in its Analytics tab; and a dashboard can show lex-app's own pages and controls - its grid,
its forms, a calculation's button and log - inside one of its sections. Both directions use the
same contract as lex-app's Streamlit dashboards, so a frontend that can show one can show the
other.

These pages need a running lex-app, so this page has no live demos.

## A dashboard on a model

A model gets a dashboard by defining a class method that returns one: `reflex_main` for one
record, `reflex_class_main` for the whole table. Keep the dashboard in a module of its own, and
import it inside the method: `lex.ui` defines Reflex states, so only the Reflex process may import
it.

```python
# myproject/Fund.py
class Fund(LexModel):
    name = models.CharField(max_length=200)

    @classmethod
    def reflex_main(cls):
        """One fund's dashboard: lex-app opens it with ?model=fund&pk=<id>."""
        from myproject.fund_dashboard import fund_record

        return fund_record()
```

```python
# myproject/fund_dashboard.py
from lex import ui
from myproject.Fund import Fund


class FundPanel(ui.State):
    name: str = ""
    vintage: str = ""

    @ui.load
    def open(self):
        fund = self.api.get(Fund, self.params.get("pk"))
        self.name = fund["name"]
        self.vintage = str(fund["vintage"])


@ui.dashboard
def fund_record():
    return ui.record(
        title=FundPanel.name,
        load=FundPanel.open,
        facts=[ui.fact("Vintage", FundPanel.vintage)],
        sections=[ui.section("Strategy", ui.text("Growth equity in Northern Europe."))],
    )
```

The dashboard has no route of its own: it is opened at the dashboards' home page, and the query
string says which one.

| The address carries | lex-app shows |
| --- | --- |
| `?model=fund&pk=42` | `Fund.reflex_main()`, on the record's Analytics tab |
| `?model=fund` | `Fund.reflex_class_main()`, from the table's Analytics toggle |
| neither | the project's own `main()` |

A load reads those values from `self.params`: `self.params.get("pk")`. A dashboard's loads start
when their part of the page appears; see [`@ui.load`](/state-and-actions/load#when-it-runs).

lex-app shows the Reflex dashboard when `IS_REFLEX_ENABLED` is `true` and the model defines the
method itself; otherwise it falls back to the model's Streamlit dashboard, if it has one.
`LEX_DASHBOARD_SURFACE=streamlit` keeps every model on Streamlit, for a project that moves its
dashboards over one at a time.

## What changes inside lex-app's frame

lex-app opens the dashboard in a frame, and the dashboard adapts:

- **No second frame.** lex-app adds `?lex_embed=1`, and the dashboard hides its own header and
  navigation: lex-app's are already on screen.
- **The same mode.** The dashboard opens in lex-app's light or dark mode and follows it when the
  user switches.
- **The same sign-in.** The dashboard is signed in as the user of lex-app. When the frame needs a
  click to sign in again, it tells lex-app, which offers the way back instead of showing an empty
  frame. See [Sign-in](/ship-it/sign-in).
- **Only lex-app may frame it.** The dashboards tell the browser that lex-app's own addresses, and
  no other site, may show them in a frame.

`?is_logout_enabled=false` in the address hides the sign-out control in the header.

## lex-app inside a dashboard

Two components from `lex.lex_app.reflex` frame lex-app's React app inside a dashboard:

| Component | Shows |
| --- | --- |
| `lex_view(path, ...)` | one lex-app page: a table (`"investor"`), a form (`"investor/create"`), a record (`"investor/42/show"`) |
| `lex_widgets(...)` | lex-app's controls in one frame: `calculation(model, pk)` is a calculation's status and button, `calculation_log(model, pk)` its log |
| `lex_calculation(model, pk)` | one calculation, the short form of `lex_widgets(calculation(...))` |

What they frame is lex-app itself, so its validation, permissions, lifecycle hooks and
calculations apply exactly as in lex-app. They are Reflex components rather than `ui.*` parts:
in a page kind, put one in a section with `ui.custom_block`, which asks for a sentence saying why.

```python
from lex import ui
from lex.lex_app.reflex import lex_calculation, lex_view


class FundDesk(ui.State):
    pk: str = ""
    selected: list[str] = []
    status: str = ""

    @ui.load
    def open(self):
        self.pk = str(self.params.get("pk") or "")

    @ui.action
    def picked(self, payload: dict):
        """lex-app's grid: the selection changed, {resource, ids}."""
        self.selected = [str(i) for i in payload.get("ids") or []]

    @ui.action
    def calculated(self, payload: dict):
        """The calculation's status changed: {widget_id, model, pk, status}."""
        self.status = str(payload.get("status") or "")


@ui.dashboard
def fund_desk():
    return ui.record(
        title="Fund desk",
        load=FundDesk.open,
        sections=[
            ui.section("Commitments", ui.custom_block(
                lex_view("commitment", height=420, hide_toolbar=True,
                         filters={"fund": FundDesk.pk}, on_select=FundDesk.picked),
                reason="lex-app's own grid, with its inline editing.")),
            ui.section("Valuation", ui.custom_block(
                lex_calculation("fund", FundDesk.pk, show_log=True, on_status=FundDesk.calculated),
                reason="lex-app's calculation button and its live log.")),
        ],
    )
```

`path`, `filters` and a calculation's `pk` may be fields of the state: the frame follows them as
they change, and new filters reach lex-app's grid without reloading it. A handler named in
`on_select=`, `on_create=`, `on_update=`, `on_delete=`, `on_navigate=`, `on_filter=` or
`on_flow_step=` receives that event's payload as a dict; naming it is what makes lex-app send
that kind of event. `on_event=` receives every event, wrapped with its type.

Other options of `lex_view`:

| Option | Does |
| --- | --- |
| `height=` | the frame's height in pixels (800 by default) |
| `hide_toolbar=`, `hide_actions=` | hide the grid's toolbar, or its actions column |
| `redirect_after=`, `redirect_after_create=`, `redirect_after_update=` | where a form goes after saving; `{id}` is the saved record's id |
| `flow=Flow()...` | chained forms: what opens after each save, from `lex.lex_app.reflex` |
| `filters=` | the grid's filters, `{field: value or [values]}` |

Several calculations on one page belong in one `lex_widgets(...)` frame, not one frame each: each
frame starts lex-app's widget host anew. A calculation's log opens in a dialog over the page.

Next: [Raw Reflex pages](/look-and-chrome/raw-reflex), for a page you write in Reflex yourself.
