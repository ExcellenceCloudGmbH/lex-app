---
title: Widgets
---

A Streamlit dashboard can host the application's *own* controls. Not a screenshot of the Calculate button, and not a re-implementation of it — the real control, wired to the real record, with the same status pill, the same live log and the same permissions as the grid.

Use this when a dashboard is where the work happens: a report page where the reader should be able to re-run the calculation they are looking at, without leaving for the grid and coming back.

<!-- 📸 TODO: a Streamlit page with a Calculate control and its live log
     beside a chart.

     BLOCKED on authentication, not on a dashboard. The e2e project now has
     one (Fund.streamlit_main), and the capture harness can start
     `lex streamlit` on request — both were added for this. What stops it is
     that the Streamlit proxy authenticates with a Keycloak JWT while the
     harness signs in with a Django admin session, so the embed renders
     "Authentication Failed".

     Wiring JWT issuance into the fixture is real auth work that the security
     specs have a stake in; it is not a screenshot task. The capture exists
     and is skipped (`docshots.spec.ts`, "the Analytics tab") — when someone
     does that work, the test passes and the figure appears. -->

## One widget

Each widget has a flat, one-call form. It takes the model's name and the record's primary key:

```python
from lex.lex_app.streamlit import lex_calculation

lex_calculation("navcalc", pk=1)
```

That renders the full control — the status pill, the Calculate button and the button that opens the live log.

| Call | What it renders |
|---|---|
| `lex_calculation(model, pk)` | The Calculate control: status pill, button, log button |
| `lex_calculation_log(model, pk)` | The calculation log as a **live stream** |
| `lex_calculation_log_tree(model, pk)` | The finished run's **execution tree** |

The stream and the tree answer different questions. The stream shows what is happening now; the tree shows how a completed run was structured. Reach for the stream while you are watching, and the tree when you are navigating something that already finished.

## Several widgets

Every flat call is its own iframe, and therefore its own React runtime. That is the right trade for one or two controls and the wrong one for ten. Once a page has several, open a widget page and declare them together — they then share a single frame:

```python
from lex.lex_app.streamlit import lex_widgets

with lex_widgets(key="report") as page:
    page.calculation("navcalc", pk=1, title="Net asset value")
    page.calculation("feecalc", pk=1, title="Fees")
    page.calculation_log("navcalc", pk=1, height=420)
```

```mermaid
flowchart LR
    subgraph block["one lex_widgets() block"]
        direction TB
        H["host iframe<br/>one React bundle<br/>one auth handshake<br/>one model_info fetch"]
        H --- W1["calculation navcalc"]
        H --- W2["calculation feecalc"]
        H --- W3["calculation_log navcalc"]
    end
    subgraph flat["three flat calls"]
        direction TB
        F1["iframe + React runtime"]
        F2["iframe + React runtime"]
        F3["iframe + React runtime"]
    end
```

Widget count is free; block count is not. Both sides above render the same three
controls, and the right-hand one boots three React applications that contend for
the same network and main thread.

![One block, three widgets: the control at SUCCESS, the live log streaming a table, and the execution tree](images/streamlit/widgets.png)

One block, three widgets, after the run. The control carries the status, the
live log has the calculation's own `LexLogger` output in it, and the execution
tree and consolidated log below are the same run seen two other ways.

`lex_widgets()` and the flat calls are the same code path — the flat form enters and exits the block in one call. There is one manifest builder and one host, so the two cannot drift apart in what they think a widget is.

## Shaping a control

`page.calculation()` is composable, and **absence means hidden**. The default is minimal; you add what a layout needs.

| Argument | Effect |
|---|---|
| `variant` | `"full"` (pill + button), `"status"` (pill alone), `"action"` (button alone) |
| `title` | A heading above the control. Omit it and no heading renders |
| `fields` | Record fields beside the control, drawn by the application's own field renderer — a foreign key shows its display name, a datetime is formatted as the grid formats it |
| `show_log` | Put the log inline under the control |
| `show_log_button` | The control that opens the live log popup. On by default |
| `on_status` | Return the latest status envelope instead of `None` |

When the log is the point, declare it separately rather than with `show_log=True`. A control wants a single line; a two-pane tree wants width and height. Declaring them apart is what lets the control sit in a narrow column and the log run full width beneath it.

## Layout

**Width belongs to Streamlit, not to the widget.** A custom component occupies a full-width block, so even a lone button spans the page. Constrain it the way you would constrain any Streamlit element:

```python
narrow, rest = st.columns([1, 6])
with narrow:
    with lex_widgets(key="run") as page:
        page.calculation("navcalc", pk=1, variant="action", show_log_button=False)
with rest:
    st.write("… your own content, beside the button …")
```

`min_height` is a **floor, not a size** — the height used before the host reports what it actually needs. It defaults to one control row. Raise it only when you know a block is tall and want to avoid the initial reflow.

## Reacting to a result

With `on_status=True`, the call returns the latest status envelope, so the rest of the page can respond to a run finishing:

```python
status = lex_calculation("navcalc", pk=1, on_status=True)
if status and status["payload"]["status"] == "SUCCESS":
    st.success("Recalculated — the figures below are current.")
```

The envelope has the same shape as the one
[[access-and-dashboards/lex_view callbacks#The event envelope|`lex_view` returns]]:

| Key | Meaning |
|---|---|
| `type` | `"calculation_status"`. Worth checking — every envelope type shares one component value, so a click on the log button arrives here too, and its payload has no `status` key |
| `id` | A unique event id, used to de-duplicate across re-runs |
| `payload.widget_id` | Which widget this is about, when a block has several |
| `payload.status` | The calculation's state — `SUCCESS`, `ERROR`, `IN_PROGRESS`, and the rest of the [[calculations/calculation models#The State Machine|state machine]] |

It arrives on the **next** rerun, not during the one that started the run.

## Two hosts on one page

`key` distinguishes them:

```python
with lex_widgets(key="top") as page: ...
with lex_widgets(key="bottom") as page: ...
```

You rarely need to set widget ids yourself. An id is derived from what the widget is *about* — its kind, model and primary key — not from its position, so putting a widget behind an `if` does not renumber the ones after it. Ids used to be positional, and on the rerun where such a condition flipped, a status envelope could be routed to the wrong widget.

> [!warning] A malformed widget raises rather than rendering blank
> `WidgetSpecError` is raised when the *spec* cannot be built — a misspelled
> option, a `variant` that is not `"full"` or `"action"`, `fields` given as a
> bare string instead of a list, a non-positive `log_height`, or two widgets
> claiming the same id. It surfaces as an exception rather than an empty frame,
> because an empty frame looks like a loading state and gets waited on.
>
> A model or primary key that does not *exist* is a different case and does not
> raise: that widget renders an error card and its siblings keep working. The
> spec was well-formed; the record was not there.

## Related

- [[access-and-dashboards/streamlit dashboards|Streamlit dashboards]] — writing and serving the dashboards themselves
- [[access-and-dashboards/lex_view callbacks|lex_view callbacks]] — embedding a whole lex-app page in a dashboard, and reacting to what happens in it
- [[calculations/logging|Logging]] — what appears in the log these widgets display
